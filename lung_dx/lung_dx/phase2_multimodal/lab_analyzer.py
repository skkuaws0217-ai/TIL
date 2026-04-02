"""Lab 값 해석 모듈.

환자의 Lab 결과를 lab_reference_ranges_v3.yaml(89개)과 비교하여
각 값의 interpretation, medical_term, severity, disease_associations를
생성한다.
"""

from __future__ import annotations

from typing import Any

from ..domain.findings import LabFinding
from ..knowledge.lab_reference import LabReferenceManager


class LabAnalyzer:
    """환자 Lab 결과를 reference range 기반으로 해석."""

    def __init__(self, lab_ref: LabReferenceManager):
        self._lab_ref = lab_ref
        self._lab_ref._ensure_loaded()

    def analyze(self, lab_results: list[dict[str, Any]]) -> list[LabFinding]:
        """환자의 Lab 결과 목록을 일괄 해석.

        Args:
            lab_results: [{itemid, name, value, unit, ref_range_lower,
                           ref_range_upper, ...}]

        Returns:
            해석된 LabFinding 목록 (비정상 + 정상 모두 포함).
        """
        findings = []
        for result in lab_results:
            itemid = result.get("itemid")
            value = result.get("value")
            if itemid is None or value is None:
                continue

            # 숫자값만 해석 (정성검사는 별도 처리)
            if isinstance(value, (int, float)):
                finding = self._lab_ref.interpret_value(
                    itemid=itemid,
                    value=float(value),
                    ref_lower=result.get("ref_range_lower"),
                    ref_upper=result.get("ref_range_upper"),
                )
            else:
                finding = self._interpret_qualitative(itemid, str(value))

            findings.append(finding)
        return findings

    def get_abnormal_findings(
        self, findings: list[LabFinding]
    ) -> list[LabFinding]:
        """비정상 소견만 필터."""
        return [f for f in findings if f.severity != "normal"]

    def get_critical_findings(
        self, findings: list[LabFinding]
    ) -> list[LabFinding]:
        """위험(critical) 소견만 필터."""
        return [f for f in findings if f.severity == "critical"]

    def extract_medical_terms(
        self, findings: list[LabFinding]
    ) -> set[str]:
        """비정상 소견의 medical_term 집합 추출.

        diagnostic_scorer에서 질환 프로필의 lab_patterns와 매칭할 때 사용.
        예: {"Leukocytosis", "Hypoxemia", "Elevated CRP"}
        """
        terms = set()
        for f in findings:
            if f.severity != "normal" and f.medical_term:
                terms.add(f.medical_term)
        return terms

    def extract_disease_associations(
        self, findings: list[LabFinding]
    ) -> dict[str, list[str]]:
        """비정상 소견에서 disease_key → [근거 pattern] 매핑 추출.

        Returns:
            {"community_acquired_pneumonia": ["↓ pO2 (Hypoxemia)", ...]}
        """
        assoc: dict[str, list[str]] = {}
        for f in findings:
            if f.severity == "normal":
                continue
            for da in f.disease_associations:
                dk = da.get("disease_key", "")
                pattern = da.get("pattern", "")
                if dk:
                    evidence = f"{f.medical_term or f.interpretation} — {f.name}"
                    if pattern:
                        evidence += f" [{pattern[:60]}]"
                    assoc.setdefault(dk, []).append(evidence)
        return assoc

    # ── 정성검사 해석 ─────────────────────────────────────────
    def _interpret_qualitative(
        self, itemid: int | str, value: str
    ) -> LabFinding:
        """Positive/Negative, Detected/Not detected 등 정성 결과 해석."""
        item = self._lab_ref.get_item(itemid) or {}
        name = item.get("name", str(itemid))
        unit = item.get("unit", "")
        medical_terms = item.get("medical_terms", {})
        disease_assoc = item.get("disease_associations", [])

        value_lower = value.strip().lower()
        positive_keywords = {"positive", "detected", "reactive", "present"}
        negative_keywords = {"negative", "not detected", "nonreactive", "absent"}

        if any(kw in value_lower for kw in positive_keywords):
            interpretation = "Positive"
            medical_term = medical_terms.get("high", "Positive")
            severity = "abnormal"
        elif any(kw in value_lower for kw in negative_keywords):
            interpretation = "Negative"
            medical_term = ""
            severity = "normal"
        else:
            interpretation = value
            medical_term = ""
            severity = "normal"

        return LabFinding(
            itemid=itemid,
            name=name,
            value=value,
            unit=unit,
            interpretation=interpretation,
            medical_term=medical_term,
            severity=severity,
            disease_associations=disease_assoc,
            ref_source=item.get("ref_source", ""),
        )
