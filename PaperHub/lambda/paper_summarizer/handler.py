"""
PaperHub — 논문 요약 Lambda (Bedrock Claude 호출)
Step Functions 워크플로우에서 호출됩니다.

기능:
1. S3에서 PDF 텍스트 추출
2. Bedrock Claude로 한줄 요약 생성
3. Bedrock Claude로 전문 1페이지 요약 생성
4. 요약 결과 DynamoDB 업데이트
5. 요약 PDF 생성 → S3 저장
"""

import os
import json
import io
import boto3
from datetime import datetime

# ─── 환경 변수 ───
TABLE_NAME = os.environ.get("PAPERS_TABLE", "paperhub-papers")
BUCKET_NAME = os.environ.get("PDF_BUCKET", "paperhub-pdfs")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-3-5-haiku-20241022-v1:0")

# ─── AWS 클라이언트 ───
dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
bedrock_runtime = boto3.client("bedrock-runtime")
table = dynamodb.Table(TABLE_NAME)


# ══════════════════════════════════════════
# PDF 텍스트 추출
# ══════════════════════════════════════════

def extract_text_from_pdf_s3(s3_key: str) -> str:
    """S3의 PDF에서 텍스트 추출 (Amazon Textract 또는 pypdf)"""

    try:
        # S3에서 PDF 다운로드
        response = s3.get_object(Bucket=BUCKET_NAME, Key=s3_key)
        pdf_bytes = response["Body"].read()

        # pypdf로 텍스트 추출 (Lambda Layer에 포함)
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(pdf_bytes))
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
            return text.strip()
        except ImportError:
            pass

        # Textract 대안
        try:
            textract = boto3.client("textract")
            response = textract.detect_document_text(
                Document={"S3Object": {"Bucket": BUCKET_NAME, "Name": s3_key}}
            )
            text = " ".join(
                block["Text"]
                for block in response["Blocks"]
                if block["BlockType"] == "LINE"
            )
            return text
        except Exception as e:
            print(f"[WARN] Textract 실패: {e}")

        return ""

    except Exception as e:
        print(f"[ERROR] PDF 텍스트 추출 실패: {e}")
        return ""


# ══════════════════════════════════════════
# Bedrock Claude 호출
# ══════════════════════════════════════════

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


def invoke_bedrock_summary(text: str, mode: str = "oneline") -> str:
    """Bedrock Claude로 요약 생성"""

    max_chars = 3000 if mode == "oneline" else 12000
    truncated = text[:max_chars]

    if mode == "oneline":
        system = ONELINE_SYSTEM_PROMPT
        user_msg = f"논문 내용:\n{truncated}\n\n위 논문을 한국어 한 문장으로 요약해주세요."
    else:
        system = FULL_SUMMARY_SYSTEM_PROMPT
        user_msg = f"논문 내용:\n{truncated}\n\n위 논문을 한국어로 구조화하여 요약해주세요. 주요 통계 지표와 p-value 분석을 반드시 포함해주세요."

    response = bedrock_runtime.invoke_model(
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


def generate_summary(text: str, mode: str = "oneline") -> str:
    """Bedrock Claude로 요약 생성"""
    return invoke_bedrock_summary(text, mode)


# ══════════════════════════════════════════
# 요약 PDF 생성
# ══════════════════════════════════════════

def create_summary_pdf(paper_info: dict, oneline: str, full_summary: str) -> bytes:
    """요약 내용을 PDF로 생성 (reportlab 사용)"""

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.enums import TA_LEFT
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=20*mm, bottomMargin=20*mm)
        styles = getSampleStyleSheet()

        # 커스텀 스타일
        title_style = ParagraphStyle(
            "CustomTitle", parent=styles["Heading1"],
            fontSize=16, spaceAfter=12,
        )
        subtitle_style = ParagraphStyle(
            "SubTitle", parent=styles["Normal"],
            fontSize=10, textColor="grey",
        )
        section_style = ParagraphStyle(
            "Section", parent=styles["Heading2"],
            fontSize=12, spaceBefore=16, spaceAfter=8,
        )
        body_style = ParagraphStyle(
            "Body", parent=styles["Normal"],
            fontSize=10, leading=16,
        )

        elements = []

        # 헤더
        elements.append(Paragraph("PaperHub AI Summary", title_style))
        elements.append(Paragraph(
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            subtitle_style,
        ))
        elements.append(Spacer(1, 10))

        # 논문 정보
        elements.append(Paragraph("Paper Information", section_style))
        elements.append(Paragraph(f"<b>Title:</b> {paper_info.get('title', '')}", body_style))
        elements.append(Paragraph(f"<b>Authors:</b> {', '.join(paper_info.get('authors', []))}", body_style))
        elements.append(Paragraph(f"<b>Journal:</b> {paper_info.get('journal', '')} ({paper_info.get('year', '')})", body_style))
        elements.append(Paragraph(f"<b>DOI:</b> {paper_info.get('doi', '')}", body_style))
        elements.append(Spacer(1, 10))

        # 한줄 요약
        elements.append(Paragraph("One-Line Summary", section_style))
        elements.append(Paragraph(oneline, body_style))
        elements.append(Spacer(1, 10))

        # 전문 요약
        elements.append(Paragraph("Full Summary (1-Page)", section_style))
        for line in full_summary.split("\n"):
            if line.strip():
                elements.append(Paragraph(line, body_style))

        doc.build(elements)
        return buffer.getvalue()

    except ImportError:
        # reportlab 없으면 텍스트 PDF 생략
        print("[WARN] reportlab 미설치, PDF 생성 건너뜀")
        return b""


# ══════════════════════════════════════════
# Lambda 핸들러
# ══════════════════════════════════════════

def handler(event, context):
    """
    Step Functions에서 호출되는 요약 Lambda

    Input:
    {
        "pmid": "12345678",
        "title": "...",
        "abstract": "...",
        "pdf_s3_key": "papers/12345678/12345678.pdf",
        "alert_keyword": "CRISPR"
    }
    """

    print(f"[START] 요약 처리: {event.get('pmid')}")

    pmid = event["pmid"]
    title = event.get("title", "")
    abstract = event.get("abstract", "")
    pdf_s3_key = event.get("pdf_s3_key", "")

    # ─── 1. 텍스트 준비 ───
    # PDF 전문이 있으면 전문 사용, 없으면 abstract
    full_text = abstract
    if pdf_s3_key:
        extracted = extract_text_from_pdf_s3(pdf_s3_key)
        if extracted and len(extracted) > len(abstract):
            full_text = extracted
            print(f"[TEXT] PDF 전문 사용 ({len(full_text)} chars)")
        else:
            print(f"[TEXT] Abstract 사용 ({len(abstract)} chars)")

    # ─── 2. 한줄 요약 생성 ───
    print("[SUMMARY] 한줄 요약 생성 중...")
    oneline = generate_summary(
        f"Title: {title}\n\n{abstract}",
        mode="oneline",
    )
    print(f"[SUMMARY] 한줄: {oneline[:100]}...")

    # ─── 3. 전문 요약 생성 ───
    print("[SUMMARY] 전문 요약 생성 중...")
    full_summary = generate_summary(
        f"Title: {title}\n\n{full_text}",
        mode="full",
    )
    print(f"[SUMMARY] 전문: {full_summary[:100]}...")

    # ─── 4. DynamoDB 업데이트 ───
    table.update_item(
        Key={"pmid": pmid},
        UpdateExpression="SET summary_oneline = :ol, summary_full = :fs, summarized_at = :now",
        ExpressionAttributeValues={
            ":ol": oneline,
            ":fs": full_summary,
            ":now": datetime.now().isoformat(),
        },
    )

    # ─── 5. 요약 PDF 생성 & S3 저장 ───
    summary_pdf = create_summary_pdf(event, oneline, full_summary)
    summary_s3_key = ""

    if summary_pdf:
        summary_s3_key = f"papers/{pmid}/{pmid}_summary.pdf"
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=summary_s3_key,
            Body=summary_pdf,
            ContentType="application/pdf",
        )
        print(f"[PDF] 요약 PDF 저장: {summary_s3_key}")

    # ─── 6. 결과 반환 (Step Functions 다음 단계로) ───
    return {
        "pmid": pmid,
        "title": title,
        "authors": event.get("authors", []),
        "journal": event.get("journal", ""),
        "year": event.get("year", ""),
        "doi": event.get("doi", ""),
        "abstract": abstract[:500],
        "summary_oneline": oneline,
        "summary_full": full_summary,
        "pdf_s3_key": pdf_s3_key,
        "summary_pdf_s3_key": summary_s3_key,
        "alert_keyword": event.get("alert_keyword", ""),
    }
