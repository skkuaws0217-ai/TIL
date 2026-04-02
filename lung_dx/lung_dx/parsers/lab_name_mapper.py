"""Lab 검사명 → YAML ItemID 매퍼.

PDF에서 추출된 검사명(한글/영문)을 lab_reference_ranges_v3.yaml의
89개 항목과 매칭하여 ItemID를 반환한다.

매칭 전략:
  1순위: 정확 매칭 (대소문자 무시)
  2순위: 핵심 키워드 매칭 (괄호 제거 후)
  3순위: 퍼지 매칭 (rapidfuzz, score ≥ 80)
  4순위: 한국어 별칭 매칭 (일반적 한국어 검사명 → 영문명 변환)
"""

from __future__ import annotations

import re
from typing import Optional

from rapidfuzz import fuzz, process

from ..knowledge.lab_reference import LabReferenceManager


# ── 한국어 검사명 → 영문 매핑 테이블 ─────────────────────────
# 국내 병원 Lab 결과지에서 흔히 사용되는 한국어 검사명.
# 매핑 근거: 대한진단검사의학회 검사 용어집, 국내 병원 공통 표기
KOREAN_TO_ENGLISH = {
    # A. 혈액가스
    "산소분압": "pO2",
    "이산화탄소분압": "pCO2",
    "수소이온농도": "pH",
    "중탄산": "Bicarbonate",
    "중탄산염": "Bicarbonate",
    "염기과잉": "Base Excess",
    "젖산": "Lactate",
    "유산": "Lactate",
    "폐포동맥산소분압차": "Alveolar-arterial Gradient",
    "카르복시헤모글로빈": "Carboxyhemoglobin",
    "메트헤모글로빈": "Methemoglobin",
    "젖산탈수소효소": "Lactate Dehydrogenase",
    "LDH": "Lactate Dehydrogenase",
    # B. CBC
    "백혈구": "White Blood Cell Count",
    "백혈구수": "White Blood Cell Count",
    "WBC": "White Blood Cell Count",
    "적혈구": "Red Blood Cell Count",
    "적혈구수": "Red Blood Cell Count",
    "RBC": "Red Blood Cell Count",
    "혈색소": "Hemoglobin",
    "헤모글로빈": "Hemoglobin",
    "Hb": "Hemoglobin",
    "적혈구용적률": "Hematocrit",
    "헤마토크릿": "Hematocrit",
    "Hct": "Hematocrit",
    "혈소판": "Platelet Count",
    "혈소판수": "Platelet Count",
    "PLT": "Platelet Count",
    "호중구": "Neutrophils",
    "호중구비율": "Neutrophils",
    "림프구": "Lymphocytes",
    "림프구비율": "Lymphocytes",
    "호산구": "Eosinophils",
    "호산구비율": "Eosinophils",
    "호염기구": "Basophils",
    "단구": "Monocytes",
    "적혈구분포폭": "RDW",
    "적혈구침강속도": "ESR",
    "ESR": "ESR",
    "호중구림프구비": "NLR",
    "NLR": "NLR",
    # C. 생화학/전해질
    "알부민": "Albumin",
    "크레아티닌": "Creatinine",
    "혈중요소질소": "Blood Urea Nitrogen",
    "BUN": "Blood Urea Nitrogen",
    "혈당": "Glucose",
    "포도당": "Glucose",
    "칼슘": "Calcium Total",
    "인": "Phosphate",
    "칼륨": "Potassium",
    "나트륨": "Sodium",
    "마그네슘": "Magnesium",
    # D. 염증표지자
    "C반응단백": "C-Reactive Protein",
    "CRP": "C-Reactive Protein",
    "고감도CRP": "High-Sensitivity CRP",
    "hs-CRP": "High-Sensitivity CRP",
    "프로칼시토닌": "Procalcitonin",
    "PCT": "Procalcitonin",
    "페리틴": "Ferritin",
    # E. 응고
    "D-다이머": "D-Dimer",
    "D-이합체": "D-Dimer",
    "피브리노겐": "Fibrinogen",
    "프로트롬빈시간": "PT",
    "INR": "INR",
    # F. 심장표지자
    "BNP": "BNP",
    "NT-proBNP": "NT-proBNP",
    "트로포닌": "Troponin T",
    "트로포닌T": "Troponin T",
    "트로포닌I": "Troponin I",
    # G. 면역
    "항핵항체": "ANA",
    "ANA": "ANA",
    "류마티스인자": "Rheumatoid Factor",
    "RF": "Rheumatoid Factor",
    "총IgE": "Total IgE",
    "IgE": "Total IgE",
    # J. 감염/미생물
    "혈액배양": "Blood Culture",
    "객담배양": "Sputum Culture",
    "항산균도말": "AFB Smear",
    "AFB": "AFB Smear",
    "결핵PCR": "TB-PCR",
    "IGRA": "IGRA",
    "코로나PCR": "SARS-CoV-2 RT-PCR",
    "코로나신속항원": "SARS-CoV-2 Rapid Antigen Test",
}


class LabNameMapper:
    """검사명 → YAML ItemID 매퍼."""

    def __init__(self, lab_ref: LabReferenceManager):
        self._lab_ref = lab_ref
        self._lab_ref._ensure_loaded()
        self._build_lookup()

    def _build_lookup(self) -> None:
        """YAML 항목에서 매칭용 lookup 테이블 구축."""
        self._name_to_id: dict[str, int | str] = {}    # 정확 매칭
        self._core_to_id: dict[str, int | str] = {}    # 핵심어 매칭
        self._fuzzy_choices: list[str] = []              # 퍼지 매칭 대상
        self._fuzzy_id_map: dict[str, int | str] = {}

        for itemid in self._lab_ref.get_all_itemids():
            item = self._lab_ref.get_item(itemid)
            if not item:
                continue
            name = item.get("name", "")
            name_lower = name.lower().strip()

            # 정확 매칭용
            self._name_to_id[name_lower] = itemid

            # 핵심어 매칭용 (괄호 제거)
            core = re.sub(r"\s*\(.*?\)", "", name_lower).strip()
            self._core_to_id[core] = itemid

            # 퍼지 매칭용
            self._fuzzy_choices.append(name_lower)
            self._fuzzy_id_map[name_lower] = itemid

    def match(self, test_name: str) -> Optional[int | str]:
        """검사명을 YAML ItemID로 변환.

        Args:
            test_name: PDF에서 추출된 검사명 (한글 또는 영문)

        Returns:
            매칭된 ItemID, 실패 시 None.
        """
        name = test_name.strip()
        if not name:
            return None

        # 한국어 → 영문 변환 시도
        english = KOREAN_TO_ENGLISH.get(name)
        if english:
            name = english

        name_lower = name.lower().strip()

        # 1순위: 정확 매칭
        if name_lower in self._name_to_id:
            return self._name_to_id[name_lower]

        # 2순위: 핵심어 매칭
        core = re.sub(r"\s*\(.*?\)", "", name_lower).strip()
        if core in self._core_to_id:
            return self._core_to_id[core]

        # 정확 매칭 - 부분 문자열
        for yaml_name, itemid in self._name_to_id.items():
            if name_lower in yaml_name or yaml_name in name_lower:
                return itemid

        # 3순위: 퍼지 매칭 (score ≥ 80)
        result = process.extractOne(
            name_lower, self._fuzzy_choices,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=80,
        )
        if result:
            matched_name, score, _ = result
            return self._fuzzy_id_map[matched_name]

        return None
