"""
PaperHub — 논문 수집 Lambda
EventBridge 스케줄 또는 API Gateway에서 트리거됩니다.

기능:
1. PubMed E-utilities API로 논문 메타데이터 검색
2. DOI 기반 PDF 접근 (오픈액세스 / Unpaywall)
3. S3에 PDF 캐시 저장
4. DynamoDB에 메타데이터 저장
5. Step Functions 워크플로우 시작 (요약 파이프라인)
"""

import os
import json
import time
import hashlib
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Optional

import boto3
from botocore.exceptions import ClientError


# ─── 환경 변수 ───
TABLE_NAME = os.environ.get("PAPERS_TABLE", "paperhub-papers")
ALERTS_TABLE = os.environ.get("ALERTS_TABLE", "paperhub-alerts")
BUCKET_NAME = os.environ.get("PDF_BUCKET", "paperhub-pdfs")
STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")
PUBMED_API_KEY = os.environ.get("PUBMED_API_KEY", "")  # 선택: 속도 향상
USE_BEDROCK = os.environ.get("USE_BEDROCK", "false").lower() == "true"
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-3-5-haiku-20241022-v1:0")

# ─── AWS 클라이언트 ───
dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
sfn = boto3.client("stepfunctions")
papers_table = dynamodb.Table(TABLE_NAME)
alerts_table = dynamodb.Table(ALERTS_TABLE)


# ══════════════════════════════════════════
# PubMed E-utilities API 래퍼
# ══════════════════════════════════════════

# ─── AI 요약 프롬프트 ───
ONELINE_SYSTEM_PROMPT = """You are an expert academic paper summarizer.
Summarize the following paper in exactly ONE sentence in Korean.
Focus on the key finding, methodology, and significance.
If statistical values (p-value, effect size, CI) are mentioned, include the most important one in the sentence."""

FULL_SUMMARY_SYSTEM_PROMPT = """You are an expert academic paper analyst and summarizer.
Create a structured summary in Korean with the following sections. Use bullet points.

## 연구 배경
- 연구 동기와 기존 연구의 한계

## 핵심 방법론
- 연구 설계 (RCT, cohort, meta-analysis 등)
- 표본 크기 (n=)
- 주요 실험/분석 방법

## 주요 결과 및 통계 지표
- 핵심 결과를 수치와 함께 기술
- 보고된 모든 통계 지표를 추출하여 정리:
  • p-value, 신뢰구간 (CI), 효과크기 (Cohen's d, OR, HR, RR 등)
  • 감도/특이도, AUC 등 (해당 시)

## p-value 심층 분석
- 보고된 p-value 값을 나열하고 각각에 대해:
  • p < 0.001: 매우 강한 통계적 유의성
  • p < 0.01: 강한 통계적 유의성
  • p < 0.05: 통계적 유의성 있음
  • p ≥ 0.05: 통계적 유의성 없음 (귀무가설 기각 불가)
- p-value만으로 판단하지 말고, 표본 크기와 효과크기를 함께 고려한 해석 제공
- 다중비교 보정 여부 (Bonferroni, FDR 등) 언급
- 임상적/실질적 유의성 vs 통계적 유의성 구분

## 의의 및 한계
- 연구의 기여점
- 방법론적 한계
- 후속 연구 방향

Keep under 700 words. 통계 지표가 논문에 명시되지 않은 경우 "본 논문에서 해당 지표 미보고"로 표기."""


PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


import re


def pubmed_search(keyword: str, max_results: int = 20, days_back: int = 7,
                  sci_only: bool = False) -> list[str]:
    """PubMed에서 키워드로 논문 ID(PMID) 검색

    Args:
        keyword: 검색 키워드
        max_results: 최대 결과 수
        days_back: 최근 N일 이내 논문만
        sci_only: True면 MEDLINE 색인 저널(SCI급)만 필터

    Returns:
        PMID 리스트
    """
    min_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y/%m/%d")
    max_date = datetime.now().strftime("%Y/%m/%d")

    # SCI 필터: MEDLINE 색인 + journal article 타입만
    search_term = keyword
    if sci_only:
        search_term = f'({keyword}) AND "journal article"[pt] AND medline[sb] AND hasabstract'

    params = {
        "db": "pubmed",
        "term": search_term,
        "retmax": str(max_results),
        "sort": "date",
        "datetype": "pdat",
        "mindate": min_date,
        "maxdate": max_date,
        "retmode": "json",
    }
    if PUBMED_API_KEY:
        params["api_key"] = PUBMED_API_KEY

    url = f"{PUBMED_BASE}/esearch.fcgi?{urllib.parse.urlencode(params)}"

    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.loads(resp.read())

    return data.get("esearchresult", {}).get("idlist", [])


def extract_pvalues(abstract: str) -> list[dict]:
    """Abstract에서 p-value 추출 및 유의성 판정"""
    if not abstract:
        return []

    patterns = [
        r'[Pp]\s*[<>=≤≥]\s*[\d.]+(?:\s*[×x]\s*10\s*[-−]\s*\d+)?',
        r'[Pp]-?\s*value\s*[<>=≤≥:]\s*[\d.]+(?:\s*[×x]\s*10\s*[-−]\s*\d+)?',
        r'[Pp]\s*=\s*[\d.]+(?:\s*[×x]\s*10\s*[-−]\s*\d+)?',
    ]

    found = set()
    for pat in patterns:
        for m in re.finditer(pat, abstract):
            found.add(m.group(0).strip())

    results = []
    for raw in found:
        numeric = _parse_pvalue_numeric(raw)
        significance = _judge_significance(numeric)
        results.append({
            "raw": raw,
            "numeric": numeric,
            "significance": significance,
        })

    # 가장 유의한 것 먼저
    results.sort(key=lambda x: x["numeric"] if x["numeric"] is not None else 1.0)
    return results


def _parse_pvalue_numeric(raw: str) -> Optional[float]:
    """p-value 문자열에서 숫자값 추출"""
    try:
        # "p < 0.001", "P = 0.03", "p-value < 0.05" 등
        nums = re.findall(r'[\d.]+(?:\s*[×x]\s*10\s*[-−]\s*\d+)?', raw)
        if not nums:
            return None
        num_str = nums[-1]
        # 과학적 표기법 처리: 3.2 × 10-5
        if re.search(r'[×x]', num_str):
            parts = re.split(r'\s*[×x]\s*10\s*[-−]\s*', num_str)
            if len(parts) == 2:
                return float(parts[0]) * (10 ** -int(parts[1]))
        return float(num_str)
    except (ValueError, IndexError):
        return None


def _judge_significance(value: Optional[float]) -> str:
    """p-value 유의성 판정"""
    if value is None:
        return "판정 불가"
    if value < 0.001:
        return "매우 강한 유의성 (p < 0.001)"
    elif value < 0.01:
        return "강한 유의성 (p < 0.01)"
    elif value < 0.05:
        return "유의함 (p < 0.05)"
    else:
        return "유의하지 않음 (p ≥ 0.05)"


def get_min_pvalue(pvalues: list[dict]) -> Optional[float]:
    """가장 작은 p-value 반환 (정렬 기준)"""
    nums = [p["numeric"] for p in pvalues if p["numeric"] is not None]
    return min(nums) if nums else None


def enrich_with_citations(papers: list[dict]) -> list[dict]:
    """Semantic Scholar Batch API로 인용수 보강 (한 번에 최대 500건)"""

    # DOI/PMID 목록 준비
    ids = []
    id_map = {}
    for paper in papers:
        paper["citation_count"] = 0
        paper["influential_citations"] = 0
        doi = paper.get("doi", "")
        pmid = paper.get("pmid", "")
        if doi:
            identifier = f"DOI:{doi}"
        elif pmid:
            identifier = f"PMID:{pmid}"
        else:
            continue
        ids.append(identifier)
        id_map[identifier] = paper

    if not ids:
        return papers

    # Batch API 호출 (최대 500건 한 번에)
    try:
        batch_url = "https://api.semanticscholar.org/graph/v1/paper/batch"
        req_data = json.dumps({"ids": ids[:100]}).encode("utf-8")
        req = urllib.request.Request(
            f"{batch_url}?fields=citationCount,influentialCitationCount,externalIds",
            data=req_data,
            headers={
                "User-Agent": "PaperHub/1.0",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            results = json.loads(resp.read())

        for i, result in enumerate(results):
            if result and i < len(ids):
                identifier = ids[i]
                if identifier in id_map:
                    id_map[identifier]["citation_count"] = result.get("citationCount", 0) or 0
                    id_map[identifier]["influential_citations"] = result.get("influentialCitationCount", 0) or 0
    except Exception as e:
        print(f"[WARN] Semantic Scholar batch failed: {e}")

    return papers


# ══════════════════════════════════════════
# Scopus CiteScore (OpenAlex API)
# ══════════════════════════════════════════

# CiteScore 캐시 (ISSN → score)
_citescore_cache = {}


def get_citescore(issn: str, journal_name: str = "") -> dict:
    """OpenAlex API로 Scopus CiteScore 조회

    OpenAlex는 Scopus 소스 데이터를 기반으로 저널 임팩트 지표를 제공합니다.
    https://www.scopus.com/sources 의 CiteScore에 대응합니다.
    """
    cache_key = issn or journal_name
    if cache_key in _citescore_cache:
        return _citescore_cache[cache_key]

    result = {
        "citescore": None,
        "impact_factor": None,
        "h_index": None,
        "scopus_url": f"https://www.scopus.com/sources",
    }

    if not issn and not journal_name:
        return result

    try:
        # OpenAlex: ISSN으로 저널(source) 조회
        if issn:
            # ISSN 포맷 맞추기 (1234-5678)
            clean_issn = issn.strip().replace(" ", "")
            if len(clean_issn) == 8 and "-" not in clean_issn:
                clean_issn = clean_issn[:4] + "-" + clean_issn[4:]

            api_url = f"https://api.openalex.org/sources/issn:{clean_issn}"
        else:
            encoded = urllib.parse.quote(journal_name)
            api_url = f"https://api.openalex.org/sources?search={encoded}&per_page=1"

        req = urllib.request.Request(api_url, headers={
            "User-Agent": "PaperHub/1.0 (mailto:paperhub@example.com)"
        })
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())

        # 단일 소스 또는 검색 결과
        source = data
        if "results" in data and data["results"]:
            source = data["results"][0]

        # CiteScore 대응: OpenAlex의 cited_by_count / works_count ≈ CiteScore
        # 또는 summary_stats의 2yr_mean_citedness (≈ Impact Factor)
        summary = source.get("summary_stats", {})
        two_yr_citedness = summary.get("2yr_mean_citedness")
        h_index = summary.get("h_index")

        # OpenAlex의 2yr_mean_citedness ≈ JCR Impact Factor ≈ Scopus CiteScore와 유사
        if two_yr_citedness is not None:
            result["citescore"] = round(two_yr_citedness, 2)
            result["impact_factor"] = round(two_yr_citedness, 2)

        if h_index is not None:
            result["h_index"] = h_index

        # Scopus source ID 가져오기 (있으면)
        ids = source.get("ids", {})
        if ids.get("issn"):
            issn_val = ids["issn"][0] if isinstance(ids["issn"], list) else ids["issn"]
            result["scopus_url"] = f"https://www.scopus.com/sources?query={issn_val}"

    except Exception as e:
        print(f"[WARN] CiteScore lookup failed for {cache_key}: {e}")

    _citescore_cache[cache_key] = result
    return result


def enrich_with_citescore(papers: list[dict]) -> list[dict]:
    """논문 목록에 CiteScore/Impact Factor 추가"""
    checked = {}

    for paper in papers:
        issn = paper.get("issn", "")
        journal = paper.get("journal", "")
        key = issn or journal

        if key and key not in checked:
            checked[key] = get_citescore(issn, journal)

        cs = checked.get(key, {})
        paper["citescore"] = cs.get("citescore")
        paper["impact_factor"] = cs.get("impact_factor")
        paper["h_index"] = cs.get("h_index")
        paper["scopus_url"] = cs.get("scopus_url", "")

    return papers


# ══════════════════════════════════════════
# Clarivate MJL — SCIE 등재 확인
# ══════════════════════════════════════════

# 저널 SCIE 상태 캐시 (Lambda warm start 간 유지)
_scie_cache = {}


def check_scie_status(journal_name: str, issn: str = "") -> dict:
    """Clarivate Master Journal List에서 SCIE 등재 여부 확인

    https://mjl.clarivate.com/home 에서
    Science Citation Index Expanded (Web of Science Core Collection)
    등재 여부를 확인합니다.
    """

    cache_key = issn or journal_name
    if cache_key in _scie_cache:
        return _scie_cache[cache_key]

    result = {
        "is_scie": False,
        "collections": [],
        "mjl_url": "",
    }

    # ISSN 기반 검색 우선, 없으면 저널명
    search_value = issn if issn else journal_name
    if not search_value:
        return result

    try:
        # Clarivate MJL 검색 페이지 조회
        encoded = urllib.parse.quote(search_value)
        mjl_url = f"https://mjl.clarivate.com/search-results?issn={encoded}" if issn else f"https://mjl.clarivate.com/search-results?search={encoded}"
        result["mjl_url"] = mjl_url

        req = urllib.request.Request(mjl_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
        })
        with urllib.request.urlopen(req, timeout=3) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        # "Science Citation Index Expanded" 텍스트 확인
        if "Science Citation Index Expanded" in html:
            result["is_scie"] = True
            result["collections"].append("SCIE")

        # 추가 컬렉션 확인
        if "Social Sciences Citation Index" in html:
            result["collections"].append("SSCI")
        if "Arts &amp; Humanities Citation Index" in html or "Arts & Humanities Citation Index" in html:
            result["collections"].append("AHCI")
        if "Emerging Sources Citation Index" in html:
            result["collections"].append("ESCI")

    except Exception as e:
        print(f"[WARN] MJL check failed for {search_value}: {e}")
        # MJL 접근 실패 시 — Medline 색인 여부로 대체 추정
        result["is_scie"] = None  # 확인 불가

    _scie_cache[cache_key] = result
    return result


def enrich_with_scie(papers: list[dict]) -> list[dict]:
    """논문 목록에 SCIE 등재 정보 추가"""
    # 저널별로 그룹화하여 중복 조회 방지
    checked_journals = {}

    for paper in papers:
        journal = paper.get("journal", "")
        issn = paper.get("issn", "")
        key = issn or journal

        if key not in checked_journals:
            checked_journals[key] = check_scie_status(journal, issn)

        scie_info = checked_journals[key]
        paper["is_scie"] = scie_info.get("is_scie", False)
        paper["wos_collections"] = scie_info.get("collections", [])
        paper["mjl_url"] = scie_info.get("mjl_url", "")

    return papers


def pubmed_fetch_details(pmids: list[str]) -> list[dict]:
    """PMID 리스트로 논문 상세 정보 조회

    Returns:
        논문 메타데이터 딕셔너리 리스트
    """
    if not pmids:
        return []

    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "rettype": "xml",
        "retmode": "xml",
    }
    if PUBMED_API_KEY:
        params["api_key"] = PUBMED_API_KEY

    url = f"{PUBMED_BASE}/efetch.fcgi?{urllib.parse.urlencode(params)}"

    with urllib.request.urlopen(url, timeout=30) as resp:
        xml_text = resp.read().decode("utf-8")

    return parse_pubmed_xml(xml_text)


def parse_pubmed_xml(xml_text: str) -> list[dict]:
    """PubMed XML 응답 파싱"""
    root = ET.fromstring(xml_text)
    papers = []

    for article in root.findall(".//PubmedArticle"):
        try:
            medline = article.find(".//MedlineCitation")
            pmid = medline.find("PMID").text
            art = medline.find("Article")

            # 제목
            title_el = art.find("ArticleTitle")
            title = "".join(title_el.itertext()) if title_el is not None else ""

            # 저자
            authors = []
            for author in art.findall(".//Author"):
                last = author.find("LastName")
                init = author.find("Initials")
                if last is not None:
                    name = last.text
                    if init is not None:
                        name += f" {init.text}"
                    authors.append(name)

            # 저널
            journal_el = art.find(".//Journal/Title")
            journal = journal_el.text if journal_el is not None else ""

            # ISSN
            issn = ""
            issn_el = art.find(".//Journal/ISSN")
            if issn_el is not None:
                issn = issn_el.text or ""

            # 연도
            pub_date = art.find(".//PubDate")
            year = ""
            if pub_date is not None:
                year_el = pub_date.find("Year")
                year = year_el.text if year_el is not None else ""

            # 초록
            abstract_el = art.find(".//Abstract")
            abstract = ""
            if abstract_el is not None:
                abstract = " ".join(
                    "".join(at.itertext())
                    for at in abstract_el.findall("AbstractText")
                )

            # DOI
            doi = ""
            for id_el in article.findall(".//ArticleId"):
                if id_el.get("IdType") == "doi":
                    doi = id_el.text
                    break

            # 키워드
            keywords = []
            for kw in medline.findall(".//KeywordList/Keyword"):
                if kw.text:
                    keywords.append(kw.text)

            # PMC ID (오픈액세스 확인용)
            pmc_id = ""
            for id_el in article.findall(".//ArticleId"):
                if id_el.get("IdType") == "pmc":
                    pmc_id = id_el.text
                    break

            papers.append({
                "pmid": pmid,
                "pmc_id": pmc_id,
                "title": title,
                "authors": authors,
                "journal": journal,
                "issn": issn,
                "year": year,
                "abstract": abstract,
                "doi": doi,
                "keywords": keywords,
                "is_open_access": bool(pmc_id),
            })

        except Exception as e:
            print(f"[WARN] 논문 파싱 실패: {e}")
            continue

    return papers


# ══════════════════════════════════════════
# PDF 다운로드 & S3 캐시
# ══════════════════════════════════════════

def get_sci_hub_url(doi: str) -> str:
    """DOI로 Sci-Hub URL 생성"""
    if not doi:
        return ""
    return f"https://www.sci-hub.in/{doi}"


def get_pdf_url(paper: dict) -> Optional[str]:
    """논문 PDF URL 탐색

    탐색 순서:
    1. PMC 오픈액세스 PDF
    2. Unpaywall API (합법적 오픈액세스 버전)
    3. Sci-Hub (DOI 기반)
    """

    # 1) PMC 오픈액세스
    if paper.get("pmc_id"):
        pmc = paper["pmc_id"]
        return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc}/pdf/"

    # 2) Unpaywall — 합법적 오픈액세스 탐색
    doi = paper.get("doi")
    if doi:
        try:
            unpaywall_url = (
                f"https://api.unpaywall.org/v2/{doi}"
                f"?email=paperhub@example.com"
            )
            with urllib.request.urlopen(unpaywall_url, timeout=10) as resp:
                data = json.loads(resp.read())

            best = data.get("best_oa_location")
            if best and best.get("url_for_pdf"):
                return best["url_for_pdf"]
        except Exception:
            pass

    # 3) Sci-Hub
    if doi:
        return get_sci_hub_url(doi)

    return None


def download_and_cache_pdf(paper: dict) -> Optional[str]:
    """PDF 다운로드 → S3 캐시 저장

    Returns:
        S3 key (성공 시) 또는 None
    """
    s3_key = f"papers/{paper['pmid']}/{paper['pmid']}.pdf"

    # 이미 캐시에 있는지 확인
    try:
        s3.head_object(Bucket=BUCKET_NAME, Key=s3_key)
        print(f"[CACHE HIT] {s3_key}")
        return s3_key
    except ClientError:
        pass

    # PDF URL 탐색
    pdf_url = get_pdf_url(paper)
    if not pdf_url:
        print(f"[WARN] PDF URL 없음: {paper['pmid']} - {paper['title'][:50]}")
        return None

    # 다운로드
    try:
        req = urllib.request.Request(pdf_url, headers={
            "User-Agent": "PaperHub/1.0 (academic research tool)"
        })
        with urllib.request.urlopen(req, timeout=60) as resp:
            pdf_bytes = resp.read()

        # S3 업로드
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_key,
            Body=pdf_bytes,
            ContentType="application/pdf",
            Metadata={
                "pmid": paper["pmid"],
                "doi": paper.get("doi", ""),
                "title": paper["title"][:256],
            },
        )
        print(f"[CACHED] {s3_key} ({len(pdf_bytes)} bytes)")
        return s3_key

    except Exception as e:
        print(f"[ERROR] PDF 다운로드 실패: {paper['pmid']} - {e}")
        return None


# ══════════════════════════════════════════
# DynamoDB 저장
# ══════════════════════════════════════════

def save_paper_metadata(paper: dict, s3_key: Optional[str] = None):
    """논문 메타데이터 DynamoDB 저장"""
    item = {
        "pmid": paper["pmid"],
        "title": paper["title"],
        "authors": paper["authors"],
        "journal": paper["journal"],
        "year": paper["year"],
        "abstract": paper["abstract"],
        "doi": paper.get("doi", ""),
        "pmc_id": paper.get("pmc_id", ""),
        "keywords": paper.get("keywords", []),
        "is_open_access": paper.get("is_open_access", False),
        "pdf_s3_key": s3_key or "",
        "has_pdf": bool(s3_key),
        "collected_at": datetime.now().isoformat(),
        "summary_oneline": "",
        "summary_full": "",
    }

    papers_table.put_item(Item=item)
    return item


def is_paper_new(pmid: str) -> bool:
    """이미 수집된 논문인지 확인"""
    try:
        resp = papers_table.get_item(Key={"pmid": pmid})
        return "Item" not in resp
    except Exception:
        return True


# ══════════════════════════════════════════
# Step Functions 워크플로우 시작
# ══════════════════════════════════════════

def start_summarize_workflow(paper: dict, s3_key: Optional[str], alert_keyword: str = ""):
    """요약 + 메일 발송 Step Functions 워크플로우 시작"""
    if not STATE_MACHINE_ARN:
        print("[WARN] STATE_MACHINE_ARN 미설정, 워크플로우 건너뜀")
        return

    input_payload = {
        "pmid": paper["pmid"],
        "title": paper["title"],
        "abstract": paper["abstract"],
        "doi": paper.get("doi", ""),
        "journal": paper.get("journal", ""),
        "authors": paper.get("authors", []),
        "year": paper.get("year", ""),
        "pdf_s3_key": s3_key or "",
        "alert_keyword": alert_keyword,
    }

    execution_name = f"paper-{paper['pmid']}-{int(time.time())}"

    sfn.start_execution(
        stateMachineArn=STATE_MACHINE_ARN,
        name=execution_name,
        input=json.dumps(input_payload),
    )
    print(f"[SFN] 워크플로우 시작: {execution_name}")


# ══════════════════════════════════════════
# Lambda 핸들러
# ══════════════════════════════════════════

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
}


def api_response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(body),
    }


def handler(event, context):
    """
    Lambda 핸들러 — 세 가지 트리거 모드:

    1. EventBridge 스케줄 → 모든 활성 알림 키워드 수집
    2. API Gateway → 논문 검색/상세, 알림 CRUD
    3. 직접 호출 → 키워드 검색
    """

    print(f"[START] Event: {json.dumps(event)[:500]}")

    # ─── EventBridge 스케줄 ───
    if event.get("source") == "aws.events":
        return handle_scheduled_collection()

    # ─── API Gateway 라우팅 ───
    http_method = event.get("httpMethod", "")
    resource = event.get("resource", "")
    path = event.get("path", "")

    if http_method:
        body = {}
        if event.get("body"):
            try:
                body = json.loads(event["body"])
            except (json.JSONDecodeError, TypeError):
                body = {}
        path_params = event.get("pathParameters") or {}

        # /alerts
        if "/alerts" in resource or "/alerts" in path:
            if http_method == "GET" and not path_params.get("alert_id"):
                return handle_list_alerts()
            elif http_method == "POST" and not path_params.get("alert_id"):
                return handle_create_alert(body)
            elif http_method == "PUT" and path_params.get("alert_id"):
                return handle_update_alert(path_params["alert_id"], body)
            elif http_method == "DELETE" and path_params.get("alert_id"):
                return handle_delete_alert(path_params["alert_id"])

        # /papers
        if "/papers" in resource or "/papers" in path:
            if http_method == "POST" and not path_params.get("pmid"):
                return handle_search_request(body)
            elif http_method == "GET" and not path_params.get("pmid"):
                return handle_list_papers()
            elif http_method == "GET" and path_params.get("pmid") and "/pdf" not in path and "/summary" not in path and "/related" not in path:
                return handle_get_paper(path_params["pmid"])
            elif http_method == "POST" and path_params.get("pmid") and "/summary" in (resource + path):
                return handle_summary_request(path_params["pmid"])
            elif http_method == "GET" and path_params.get("pmid") and "/related" in (resource + path):
                return handle_related_papers(path_params["pmid"])

        return api_response(404, {"error": "Not found"})

    # ─── 직접 호출 (body에 keyword) ───
    body = event
    if isinstance(event.get("body"), str):
        body = json.loads(event["body"])
    return handle_search_request(body)


def handle_scheduled_collection():
    """스케줄 기반 수집: 모든 활성 알림 키워드 처리"""

    # DynamoDB에서 활성 알림 조회
    response = alerts_table.scan(
        FilterExpression="active = :val",
        ExpressionAttributeValues={":val": True},
    )
    alerts = response.get("Items", [])
    print(f"[ALERTS] 활성 알림 {len(alerts)}건")

    total_new = 0

    for alert in alerts:
        keyword = alert["keyword"]
        frequency = alert.get("frequency", "daily")

        # 주기에 따른 검색 범위
        days_back = 1 if frequency == "daily" else 7

        print(f"\n[KEYWORD] '{keyword}' (최근 {days_back}일)")

        # PubMed 검색
        pmids = pubmed_search(keyword, max_results=20, days_back=days_back)
        print(f"  검색 결과: {len(pmids)}건")

        # 새 논문만 필터
        new_pmids = [p for p in pmids if is_paper_new(p)]
        print(f"  신규 논문: {len(new_pmids)}건")

        if not new_pmids:
            continue

        # 상세 정보 조회
        papers = pubmed_fetch_details(new_pmids)

        for paper in papers:
            # PDF 다운로드 & 캐시
            s3_key = download_and_cache_pdf(paper)

            # DynamoDB 저장
            save_paper_metadata(paper, s3_key)

            # 요약 워크플로우 시작
            start_summarize_workflow(paper, s3_key, alert_keyword=keyword)

            total_new += 1

        # 알림 통계 업데이트
        alerts_table.update_item(
            Key={"alert_id": alert["alert_id"]},
            UpdateExpression="SET paperCount = paperCount + :cnt, lastTriggered = :now",
            ExpressionAttributeValues={
                ":cnt": len(new_pmids),
                ":now": datetime.now().isoformat(),
            },
        )

    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": f"수집 완료: 신규 논문 {total_new}건",
            "alerts_processed": len(alerts),
        }),
    }


def handle_search_request(body: dict):
    """API 요청 기반 검색

    Params:
        keyword: 검색어
        max_results: 최대 결과 수 (기본 50, 최대 100)
        sci_only: SCI급 저널만 (기본 false)
        sort_by: 정렬 기준 — "date" | "citations" | "pvalue" | "impact" (기본 date)
    """

    keyword = body.get("keyword", "")
    max_results = min(body.get("max_results", 50), 100)
    sci_only = body.get("sci_only", False)
    sort_by = body.get("sort_by", "date")

    if not keyword:
        return api_response(400, {"error": "keyword 필수"})

    # PubMed 검색
    pmids = pubmed_search(keyword, max_results=max_results, days_back=365,
                          sci_only=sci_only)
    papers = pubmed_fetch_details(pmids)

    # 인용수 보강 (Semantic Scholar)
    papers = enrich_with_citations(papers)

    # SCIE 등재 확인 (Clarivate MJL)
    papers = enrich_with_scie(papers)

    # CiteScore / Impact Factor (Scopus via OpenAlex)
    papers = enrich_with_citescore(papers)

    # 각 논문 처리
    results = []
    for paper in papers:
        s3_key = download_and_cache_pdf(paper)
        save_paper_metadata(paper, s3_key)

        # p-value 추출
        pvalues = extract_pvalues(paper.get("abstract", ""))
        min_p = get_min_pvalue(pvalues)

        results.append({
            "pmid": paper["pmid"],
            "title": paper["title"],
            "authors": paper["authors"],
            "journal": paper["journal"],
            "issn": paper.get("issn", ""),
            "year": paper["year"],
            "abstract": paper["abstract"][:500],
            "doi": paper["doi"],
            "keywords": paper["keywords"],
            "has_pdf": bool(s3_key),
            "is_open_access": paper.get("is_open_access", False),
            "sci_hub_url": get_sci_hub_url(paper.get("doi", "")),
            "citation_count": paper.get("citation_count", 0),
            "influential_citations": paper.get("influential_citations", 0),
            "pvalues": pvalues,
            "min_pvalue": min_p,
            "is_scie": paper.get("is_scie", False),
            "wos_collections": paper.get("wos_collections", []),
            "mjl_url": paper.get("mjl_url", ""),
            "citescore": paper.get("citescore"),
            "impact_factor": paper.get("impact_factor"),
            "h_index": paper.get("h_index"),
            "scopus_url": paper.get("scopus_url", ""),
        })

    # sci_only 필터: MJL에서 SCIE 확인된 것만
    if sci_only:
        results = [r for r in results if r["is_scie"] is True]

    # 정렬
    if sort_by == "citations":
        results.sort(key=lambda x: x["citation_count"], reverse=True)
    elif sort_by == "pvalue":
        # p-value가 작을수록 상위, 없는 건 맨 뒤
        results.sort(key=lambda x: x["min_pvalue"] if x["min_pvalue"] is not None else 999)
    elif sort_by == "impact":
        # 영향력 = CiteScore 우선 → influential_citations → citation_count
        results.sort(key=lambda x: (
            x["citescore"] or 0,
            x["influential_citations"],
            x["citation_count"],
        ), reverse=True)
    else:
        # date: 기본 PubMed 날짜순 유지
        pass

    return api_response(200, {
        "papers": results,
        "total": len(results),
        "sci_only": sci_only,
        "sort_by": sort_by,
    })


# ══════════════════════════════════════════
# Papers API
# ══════════════════════════════════════════

def handle_list_papers():
    """최근 논문 목록"""
    response = papers_table.scan(Limit=50)
    items = response.get("Items", [])
    results = []
    for p in items:
        results.append({
            "pmid": p.get("pmid"),
            "title": p.get("title", ""),
            "authors": p.get("authors", []),
            "journal": p.get("journal", ""),
            "year": p.get("year", ""),
            "abstract": p.get("abstract", "")[:500],
            "doi": p.get("doi", ""),
            "keywords": p.get("keywords", []),
            "has_pdf": p.get("has_pdf", False),
            "summary_oneline": p.get("summary_oneline", ""),
        })
    return api_response(200, {"papers": results, "total": len(results)})


def handle_get_paper(pmid: str):
    """논문 상세 조회"""
    response = papers_table.get_item(Key={"pmid": pmid})
    item = response.get("Item")
    if not item:
        return api_response(404, {"error": "Paper not found"})

    # DynamoDB Decimal 타입 처리
    result = {}
    for k, v in item.items():
        if isinstance(v, list):
            result[k] = [str(i) if not isinstance(i, (str, bool)) else i for i in v]
        elif isinstance(v, (int, float)):
            result[k] = str(v)
        else:
            result[k] = v

    return api_response(200, result)


# ══════════════════════════════════════════
# Alerts CRUD
# ══════════════════════════════════════════

def handle_list_alerts():
    """알림 목록 조회"""
    response = alerts_table.scan()
    items = response.get("Items", [])
    results = []
    for a in items:
        results.append({
            "alert_id": a.get("alert_id", ""),
            "keyword": a.get("keyword", ""),
            "email": a.get("email", ""),
            "frequency": a.get("frequency", "daily"),
            "active": a.get("active", True),
            "created_at": a.get("created_at", ""),
        })
    return api_response(200, {"alerts": results, "total": len(results)})


def handle_create_alert(body: dict):
    """알림 생성"""
    keyword = body.get("keyword", "").strip()
    email = body.get("email", "").strip()
    frequency = body.get("frequency", "daily")

    if not keyword or not email:
        return api_response(400, {"error": "keyword and email are required"})

    alert_id = hashlib.md5(f"{keyword}:{email}".encode()).hexdigest()[:12]

    item = {
        "alert_id": alert_id,
        "keyword": keyword,
        "email": email,
        "frequency": frequency,
        "active": True,
        "paperCount": 0,
        "created_at": datetime.now().isoformat(),
    }
    alerts_table.put_item(Item=item)

    return api_response(201, {"message": "Alert created", "alert": item})


def handle_update_alert(alert_id: str, body: dict):
    """알림 수정"""
    update_parts = []
    values = {}

    if "active" in body:
        update_parts.append("active = :active")
        values[":active"] = body["active"]
    if "frequency" in body:
        update_parts.append("frequency = :freq")
        values[":freq"] = body["frequency"]
    if "email" in body:
        update_parts.append("email = :email")
        values[":email"] = body["email"]

    if not update_parts:
        return api_response(400, {"error": "No fields to update"})

    alerts_table.update_item(
        Key={"alert_id": alert_id},
        UpdateExpression="SET " + ", ".join(update_parts),
        ExpressionAttributeValues=values,
    )
    return api_response(200, {"message": "Alert updated"})


def handle_delete_alert(alert_id: str):
    """알림 삭제"""
    alerts_table.delete_item(Key={"alert_id": alert_id})
    return api_response(200, {"message": "Alert deleted"})


# ══════════════════════════════════════════
# Related Papers (키워드 기반 추천)
# ══════════════════════════════════════════

def handle_related_papers(pmid: str):
    """논문의 키워드를 분석하여 관련 논문 추천"""

    # 1. 원본 논문 조회
    response = papers_table.get_item(Key={"pmid": pmid})
    item = response.get("Item")

    if not item:
        return api_response(404, {"error": "Paper not found"})

    title = item.get("title", "")
    abstract = item.get("abstract", "")
    existing_keywords = item.get("keywords", [])

    # 2. Bedrock으로 핵심 키워드 추출
    extracted_keywords = []
    if USE_BEDROCK and abstract:
        try:
            bedrock = boto3.client("bedrock-runtime")
            extracted_keywords = _extract_keywords_bedrock(
                bedrock, title, abstract, existing_keywords
            )
        except Exception as e:
            print(f"[WARN] Keyword extraction failed: {e}")

    # 3. 키워드 합치기 (중복 제거)
    all_keywords = existing_keywords + extracted_keywords
    seen = set()
    unique_keywords = []
    for kw in all_keywords:
        kw_lower = kw.lower().strip()
        if kw_lower and kw_lower not in seen:
            seen.add(kw_lower)
            unique_keywords.append(kw)

    if not unique_keywords:
        return api_response(200, {
            "source_pmid": pmid,
            "keywords": [],
            "related_papers": [],
            "message": "No keywords found",
        })

    # 4. 상위 키워드로 PubMed 검색 (최대 5개 조합)
    search_keywords = unique_keywords[:5]
    search_query = " OR ".join(f'"{kw}"' for kw in search_keywords)

    pmids = pubmed_search(search_query, max_results=20, days_back=365 * 3)

    # 원본 논문 제외
    pmids = [p for p in pmids if p != pmid][:15]

    if not pmids:
        return api_response(200, {
            "source_pmid": pmid,
            "keywords": unique_keywords,
            "related_papers": [],
            "message": "No related papers found",
        })

    # 5. 상세 정보 조회
    papers = pubmed_fetch_details(pmids)

    # 6. 관련도 점수 계산
    results = []
    for paper in papers:
        paper_kws = set(kw.lower() for kw in paper.get("keywords", []))
        paper_text = (paper.get("title", "") + " " + paper.get("abstract", "")).lower()

        # 관련도: 키워드 매칭 + 텍스트 매칭
        keyword_matches = []
        relevance_score = 0
        for kw in unique_keywords:
            if kw.lower() in paper_kws:
                relevance_score += 3  # 키워드 직접 매칭
                keyword_matches.append(kw)
            elif kw.lower() in paper_text:
                relevance_score += 1  # 텍스트 내 언급
                keyword_matches.append(kw)

        results.append({
            "pmid": paper["pmid"],
            "title": paper["title"],
            "authors": paper["authors"],
            "journal": paper["journal"],
            "year": paper["year"],
            "abstract": paper["abstract"][:300],
            "doi": paper.get("doi", ""),
            "keywords": paper.get("keywords", []),
            "is_open_access": paper.get("is_open_access", False),
            "sci_hub_url": get_sci_hub_url(paper.get("doi", "")),
            "relevance_score": relevance_score,
            "matched_keywords": keyword_matches,
        })

    # 관련도 높은 순 정렬
    results.sort(key=lambda x: x["relevance_score"], reverse=True)

    return api_response(200, {
        "source_pmid": pmid,
        "source_title": title,
        "keywords": unique_keywords,
        "extracted_keywords": extracted_keywords,
        "existing_keywords": existing_keywords,
        "related_papers": results,
        "total": len(results),
    })


def _extract_keywords_bedrock(client, title: str, abstract: str,
                               existing_keywords: list) -> list[str]:
    """Bedrock Claude로 논문에서 핵심 키워드 추출"""

    existing_str = ", ".join(existing_keywords) if existing_keywords else "없음"
    text = f"Title: {title}\n\nAbstract: {abstract[:4000]}"

    response = client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 512,
            "system": """You are a biomedical keyword extraction expert.
Extract the most important research keywords from the paper that would help find related research.
Focus on: specific techniques, diseases, genes, proteins, pathways, and methodologies.
Return ONLY a JSON array of strings, nothing else. Example: ["CRISPR-Cas9", "gene therapy", "p53"]
Do NOT include generic terms like "research", "study", "analysis".
Extract 5-10 specific keywords.""",
            "messages": [{"role": "user", "content":
                f"논문 기존 키워드: {existing_str}\n\n{text}\n\n위 논문에서 관련 연구를 찾기 위한 핵심 키워드를 추출해주세요. JSON 배열만 반환하세요."}],
        }),
    )

    result = json.loads(response["body"].read())
    text_result = result["content"][0]["text"].strip()

    # JSON 배열 파싱
    try:
        # 마크다운 코드블록 제거
        cleaned = text_result.strip("`").strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
        keywords = json.loads(cleaned)
        if isinstance(keywords, list):
            return [str(kw) for kw in keywords if isinstance(kw, str)]
    except (json.JSONDecodeError, ValueError):
        pass

    return []


# ══════════════════════════════════════════
# On-Demand Summary (Bedrock)
# ══════════════════════════════════════════

def handle_summary_request(pmid: str):
    """온디맨드 AI 요약 — DynamoDB 캐시 확인 후 Bedrock 호출"""

    # 1. DynamoDB에서 기존 요약 확인
    response = papers_table.get_item(Key={"pmid": pmid})
    item = response.get("Item")

    if not item:
        return api_response(404, {"error": "Paper not found"})

    # 이미 요약이 있으면 바로 반환
    if item.get("summary_oneline") and item.get("summary_full"):
        return api_response(200, {
            "pmid": pmid,
            "summary_oneline": item["summary_oneline"],
            "summary_full": item["summary_full"],
        })

    # 2. Bedrock으로 요약 생성
    if not USE_BEDROCK:
        return api_response(503, {"error": "Summarization not available"})

    title = item.get("title", "")
    abstract = item.get("abstract", "")

    if not abstract:
        return api_response(400, {"error": "No abstract available for summarization"})

    try:
        bedrock = boto3.client("bedrock-runtime")
        text_input = f"Title: {title}\n\nAbstract: {abstract}"

        # 한줄 요약
        oneline = _call_bedrock(bedrock, text_input,
            ONELINE_SYSTEM_PROMPT,
            "위 논문을 한국어 한 문장으로 요약해주세요.")

        # 전문 요약
        full_summary = _call_bedrock(bedrock, text_input,
            FULL_SUMMARY_SYSTEM_PROMPT,
            "위 논문을 한국어로 구조화하여 요약해주세요. 주요 통계 지표와 p-value 분석을 반드시 포함해주세요.")

        # DynamoDB에 캐시 저장
        papers_table.update_item(
            Key={"pmid": pmid},
            UpdateExpression="SET summary_oneline = :ol, summary_full = :fs, summarized_at = :now",
            ExpressionAttributeValues={
                ":ol": oneline,
                ":fs": full_summary,
                ":now": datetime.now().isoformat(),
            },
        )

        return api_response(200, {
            "pmid": pmid,
            "summary_oneline": oneline,
            "summary_full": full_summary,
        })

    except Exception as e:
        print(f"[ERROR] Summary generation failed: {e}")
        return api_response(500, {"error": f"Summary generation failed: {str(e)}"})


def _call_bedrock(client, text: str, system: str, instruction: str) -> str:
    """Bedrock Claude API 호출"""
    user_msg = f"논문 내용:\n{text[:8000]}\n\n{instruction}"

    response = client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 2048,
            "system": system,
            "messages": [{"role": "user", "content": user_msg}],
        }),
    )

    result = json.loads(response["body"].read())
    return result["content"][0]["text"]
