"""Lab 결과지 PDF → 구조화된 Lab 데이터 자동 추출.

PDF에서 검사명·결과값·단위·참고범위를 자동 인식하고,
lab_reference_ranges_v3.yaml의 ItemID에 매칭하여
PatientCase.lab_results 형식으로 변환한다.

지원 PDF 유형:
  - 텍스트 기반 PDF (디지털 생성) → pdfplumber로 텍스트/테이블 추출
  - 스캔 PDF (이미지) → AWS Textract 또는 로컬 OCR (향후 확장)

일반적인 Lab 결과지 형식:
  한국어: 검사명 | 결과 | 단위 | 참고치
  영문:   Test Name | Result | Unit | Reference Range
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pdfplumber

from .lab_name_mapper import LabNameMapper
from ..knowledge.lab_reference import LabReferenceManager

logger = logging.getLogger(__name__)


class PDFLabParser:
    """Lab 결과지 PDF → 구조화된 Lab 데이터."""

    def __init__(self, lab_ref: LabReferenceManager | None = None):
        self._lab_ref = lab_ref or LabReferenceManager()
        self._lab_ref._ensure_loaded()
        self._mapper = LabNameMapper(self._lab_ref)

    def parse(self, pdf_path: str | Path) -> list[dict[str, Any]]:
        """PDF에서 Lab 결과 추출 → PatientCase.lab_results 형식.

        Args:
            pdf_path: Lab 결과지 PDF 경로

        Returns:
            [{"itemid": 51301, "value": 18.5, "unit": "K/uL",
              "ref_range_lower": 4.5, "ref_range_upper": 11.0,
              "original_name": "WBC"}, ...]
            매칭 실패한 항목도 itemid=None으로 포함 (수동 확인용).
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF 파일 없음: {pdf_path}")

        logger.info("PDF 파싱 시작: %s", pdf_path.name)

        # 1) 테이블 추출 시도
        results = self._extract_from_tables(pdf_path)

        # 2) 테이블 실패 시 텍스트 기반 추출
        if not results:
            results = self._extract_from_text(pdf_path)

        # 3) 검사명 → ItemID 매칭
        matched = self._match_to_itemids(results)

        matched_count = sum(1 for r in matched if r.get("itemid") is not None)
        logger.info("PDF 파싱 완료: %d개 추출, %d개 ItemID 매칭 성공",
                     len(matched), matched_count)

        return matched

    def _extract_from_tables(
        self, pdf_path: Path
    ) -> list[dict[str, str]]:
        """pdfplumber 테이블 추출."""
        results = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    results.extend(self._parse_table(table))

        return results

    def _parse_table(self, table: list[list]) -> list[dict[str, str]]:
        """추출된 테이블을 파싱.

        헤더 행에서 검사명·결과·단위·참고치 컬럼을 식별하고,
        데이터 행에서 값을 추출한다.
        """
        if not table or not table[0]:
            return []

        # 헤더 식별
        header = [str(c).strip().lower() if c else "" for c in table[0]]
        col_map = self._identify_columns(header)

        if col_map.get("name") is None or col_map.get("result") is None:
            # 첫 번째 행이 헤더가 아닐 수 있음 → 두 번째 행 시도
            if len(table) > 1:
                header = [str(c).strip().lower() if c else "" for c in table[1]]
                col_map = self._identify_columns(header)
                if col_map.get("name") is not None:
                    table = table[1:]

        if col_map.get("name") is None:
            return []

        results = []
        for row in table[1:]:  # 헤더 제외
            if not row or len(row) <= max(v for v in col_map.values() if v is not None):
                continue

            name_idx = col_map["name"]
            result_idx = col_map["result"]
            name = str(row[name_idx]).strip() if name_idx is not None and row[name_idx] else ""
            result_val = str(row[result_idx]).strip() if result_idx is not None and row[result_idx] else ""

            if not name or not result_val:
                continue

            entry = {"name": name, "result": result_val}

            if col_map.get("unit") is not None and len(row) > col_map["unit"]:
                entry["unit"] = str(row[col_map["unit"]] or "").strip()

            if col_map.get("reference") is not None and len(row) > col_map["reference"]:
                entry["reference"] = str(row[col_map["reference"]] or "").strip()

            results.append(entry)

        return results

    def _identify_columns(self, header: list[str]) -> dict[str, int | None]:
        """헤더에서 컬럼 역할 식별."""
        col_map = {"name": None, "result": None, "unit": None, "reference": None}

        name_keywords = {"검사명", "검사항목", "항목", "test", "item", "검사"}
        result_keywords = {"결과", "result", "value", "결과값"}
        unit_keywords = {"단위", "unit", "units"}
        ref_keywords = {"참고치", "참고범위", "reference", "ref", "정상범위", "normal"}

        for i, col in enumerate(header):
            col_clean = col.replace(" ", "")
            if any(kw in col_clean for kw in name_keywords):
                col_map["name"] = i
            elif any(kw in col_clean for kw in result_keywords):
                col_map["result"] = i
            elif any(kw in col_clean for kw in unit_keywords):
                col_map["unit"] = i
            elif any(kw in col_clean for kw in ref_keywords):
                col_map["reference"] = i

        # 헤더 키워드로 식별 실패 시 위치 기반 추론
        # (일반적 패턴: 검사명 | 결과 | 단위 | 참고치)
        if col_map["name"] is None and len(header) >= 2:
            col_map["name"] = 0
            col_map["result"] = 1
            if len(header) >= 3:
                col_map["unit"] = 2
            if len(header) >= 4:
                col_map["reference"] = 3

        return col_map

    def _extract_from_text(
        self, pdf_path: Path
    ) -> list[dict[str, str]]:
        """테이블 추출 실패 시 텍스트 기반 줄 단위 파싱."""
        results = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                for line in text.split("\n"):
                    parsed = self._parse_text_line(line)
                    if parsed:
                        results.append(parsed)

        return results

    @staticmethod
    def _parse_text_line(line: str) -> dict[str, str] | None:
        """텍스트 줄에서 검사명·결과값 추출.

        일반적 패턴:
          "WBC        18.5    K/uL    4.5-11.0"
          "백혈구      18.5    10^3/uL 4.5~11.0"
          "CRP (정량)  150.0   mg/L    0-5"
        """
        line = line.strip()
        if not line or len(line) < 5:
            return None

        # 숫자값이 포함된 줄만 처리
        numbers = re.findall(r"[\d]+\.?[\d]*", line)
        if not numbers:
            return None

        # 패턴: 텍스트 + 공백 + 숫자 + 나머지
        match = re.match(
            r"^(.+?)\s{2,}([\d.]+)\s*(.*?)$", line
        )
        if not match:
            # 탭 구분
            match = re.match(
                r"^(.+?)\t+([\d.]+)\s*(.*?)$", line
            )

        if not match:
            return None

        name = match.group(1).strip()
        result_val = match.group(2).strip()
        rest = match.group(3).strip()

        # 나머지에서 단위와 참고범위 분리
        unit = ""
        reference = ""
        if rest:
            parts = re.split(r"\s{2,}|\t+", rest, maxsplit=1)
            unit = parts[0].strip() if parts else ""
            reference = parts[1].strip() if len(parts) > 1 else ""

        return {"name": name, "result": result_val, "unit": unit, "reference": reference}

    def _match_to_itemids(
        self, raw_results: list[dict[str, str]]
    ) -> list[dict[str, Any]]:
        """추출된 검사 결과를 YAML ItemID에 매칭."""
        matched = []

        for entry in raw_results:
            name = entry.get("name", "")
            result_str = entry.get("result", "")
            unit = entry.get("unit", "")
            reference = entry.get("reference", "")

            # 결과값 파싱
            value = self._parse_value(result_str)

            # ItemID 매칭
            itemid = self._mapper.match(name)

            # 참고범위 파싱
            ref_lower, ref_upper = self._parse_reference_range(reference)

            matched.append({
                "itemid": itemid,
                "value": value,
                "unit": unit,
                "ref_range_lower": ref_lower,
                "ref_range_upper": ref_upper,
                "original_name": name,
                "original_result": result_str,
                "matched": itemid is not None,
            })

        return matched

    @staticmethod
    def _parse_value(val_str: str) -> float | str:
        """결과값 문자열 → 숫자 또는 텍스트."""
        val_str = val_str.strip()
        # 부등호 제거
        cleaned = re.sub(r"^[<>≤≥]=?\s*", "", val_str)
        try:
            return float(cleaned)
        except ValueError:
            return val_str

    @staticmethod
    def _parse_reference_range(ref_str: str) -> tuple[float | None, float | None]:
        """참고범위 문자열 파싱.

        "4.5-11.0", "4.5~11.0", "80 - 100", "< 5", ">= 60" 등.
        """
        if not ref_str:
            return None, None

        # 범위 패턴: 4.5-11.0, 4.5~11.0, 80 - 100
        range_match = re.match(
            r"([\d.]+)\s*[-~]\s*([\d.]+)", ref_str
        )
        if range_match:
            return float(range_match.group(1)), float(range_match.group(2))

        # 상한만: < 5, <= 10
        upper_match = re.match(r"[<≤]\s*([\d.]+)", ref_str)
        if upper_match:
            return None, float(upper_match.group(1))

        # 하한만: > 60, >= 80
        lower_match = re.match(r"[>≥]\s*([\d.]+)", ref_str)
        if lower_match:
            return float(lower_match.group(1)), None

        return None, None
