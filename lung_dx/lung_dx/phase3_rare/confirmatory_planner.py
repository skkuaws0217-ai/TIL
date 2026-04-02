"""확진검사 계획 모듈.

희귀질환 후보의 진단 포인트(diagnostic_points)에서
환자에게 아직 수행되지 않은 확진검사를 추출하여 제안한다.

[Harrison's 21st — 각 질환의 Diagnostic Criteria;
 Orphanet — diagnostic workflow per disease]
"""

from __future__ import annotations

import re

from ..domain.disease import (
    RareDiseaseScore,
    ConfirmatoryTest,
)
from ..domain.patient import PatientCase
from ..knowledge.disease_registry import DiseaseRegistry


# 확진검사 키워드 → 검사 유형 매핑
TEST_TYPE_KEYWORDS = {
    "genetic": ["genetic", "gene", "mutation", "sequencing", "WES", "WGS",
                "karyotype", "FISH", "PCR", "NGS"],
    "biopsy": ["biopsy", "histology", "pathology", "cytology", "BAL"],
    "imaging": ["CT", "HRCT", "MRI", "PET", "echocardiography",
                "angiography", "V/Q scan"],
    "lab": ["serology", "antibody", "antigen", "culture", "IGRA",
            "biomarker", "enzyme", "complement"],
    "pulmonary_function": ["PFT", "spirometry", "DLCO", "plethysmography",
                           "6MWT", "FeNO"],
}


class ConfirmatoryPlanner:
    """희귀질환 후보 기반 확진검사 계획."""

    def __init__(self, disease_registry: DiseaseRegistry):
        self._registry = disease_registry

    def plan(
        self,
        candidates: list[RareDiseaseScore],
        patient: PatientCase,
        max_tests: int = 15,
    ) -> list[ConfirmatoryTest]:
        """후보 질환의 diagnostic_points에서 미수행 검사 추출.

        Args:
            candidates: Phase 3 희귀질환 후보
            patient: 환자 케이스
            max_tests: 최대 추천 검사 수

        Returns:
            ConfirmatoryTest 목록.
        """
        tests: list[ConfirmatoryTest] = []
        seen_tests: set[str] = set()

        for candidate in candidates:
            profile = self._registry.get_by_key(candidate.disease_key)
            if not profile:
                continue

            diag_points = profile.diagnostic_points
            special_clinical = profile.special_clinical_findings
            if not diag_points and not special_clinical:
                continue

            # 진단 포인트에서 검사 항목 추출
            extracted = self._extract_tests(
                diag_points + " " + special_clinical,
                candidate.disease_key,
                candidate.name_en,
            )

            for test in extracted:
                key = test.test_name.lower()
                if key not in seen_tests:
                    seen_tests.add(key)
                    tests.append(test)

        # 우선순위 정렬: genetic > biopsy > lab > imaging > PFT
        type_order = {"genetic": 0, "biopsy": 1, "lab": 2,
                      "imaging": 3, "pulmonary_function": 4, "": 5}
        tests.sort(key=lambda t: type_order.get(t.test_type, 9))

        return tests[:max_tests]

    @staticmethod
    def _extract_tests(
        text: str, disease_key: str, disease_name: str
    ) -> list[ConfirmatoryTest]:
        """텍스트에서 검사 항목 추출."""
        if not text or text.strip() in ("—", "-", ""):
            return []

        tests = []

        # 문장 단위로 분리
        sentences = re.split(r"[.;]\s*", text)

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence or sentence == "—":
                continue

            # 검사 유형 결정
            test_type = ""
            for ttype, keywords in TEST_TYPE_KEYWORDS.items():
                if any(kw.lower() in sentence.lower() for kw in keywords):
                    test_type = ttype
                    break

            if test_type:
                tests.append(ConfirmatoryTest(
                    test_name=sentence[:100],
                    test_type=test_type,
                    priority="medium",
                    for_disease=disease_name,
                    rationale=f"{disease_key} 확진 검사",
                ))

        return tests
