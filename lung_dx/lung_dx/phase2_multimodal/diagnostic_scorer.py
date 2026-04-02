"""가중 다중모달 질환 스코어링 엔진.

528개 질환에 대해 환자의 Lab, Vitals/Respiratory/Hemodynamic, Microbiology,
Symptoms(HPO) 소견을 질환별 S/L/R/M 가중치로 종합 평가하여
가능성이 높은 질환을 순위대로 산출한다.

# ═══════════════════════════════════════════════════════════════
# 가중치 체계 및 부여 근거 (Fact-Based)
# ═══════════════════════════════════════════════════════════════
#
# [W1] 가중치의 의미
#
#   S (Symptoms):   임상 증상·병력·신체 소견의 진단 기여도
#   L (Lab):        검사실 검사(혈액가스, CBC, 생화학 등)의 진단 기여도
#   R (Radiology):  영상검사(X-ray, CT 등)의 진단 기여도
#   M (Micro):      미생물학적 검사(배양, PCR, 항원 등)의 진단 기여도
#
#   모든 가중치의 합은 1.0이다: S + L + R + M = 1.0
#   높은 가중치 = 해당 모달리티가 진단에 더 결정적인 역할을 함.
#
# [W2] 명시적 가중치가 있는 질환 (58개)
#
#   YAML 17개 질환: lung_disease_profiles_v2.yaml에 기재된 weights.
#     근거: Harrison's 21st Ed, Mandell's 9th Ed, UpToDate 2025,
#     GOLD 2024, GINA 2024, ESC PE/PH 2022, ATS/ERS IPF 2022.
#     각 질환의 진단 알고리즘에서 모달리티의 상대적 중요도를 반영.
#
#   Excel 일반 21개 + 기타 20개: "진단 가중치 (S/L/R/M)" 컬럼.
#     근거: 동일 가이드라인 기반으로 구축.
#
#   이 질환들은 해당 값을 그대로 사용한다.
#
# [W3] 기본 가중치 — 명시적 가중치가 없는 질환 (478개)
#
#   명시적 가중치가 없는 질환은 질환 유형(category)에 따라
#   임상적으로 적절한 기본 가중치를 적용한다.
#
#   기본 가중치 도출 근거:
#
#   (a) 전체 기본값: S:0.25  L:0.20  R:0.35  M:0.20
#       → YAML 17개 질환의 가중 평균에서 도출:
#         S=0.244, L=0.228, R=0.364, M=0.165
#       → 폐질환에서 영상검사(CXR/CT)가 진단의 핵심 수단이라는
#         임상 현실을 반영. ATS/IDSA CAP 2019, GOLD 2024, ESC PE 2022
#         등 주요 가이드라인 모두 영상검사를 진단 알고리즘의 초기 단계에 배치.
#       [Harrison's 21st Ch.33 "Approach to Chest Imaging";
#        ATS/IDSA CAP 2019 Fig.1 "Diagnostic Algorithm"]
#
#   (b) 희귀질환 기본값: S:0.45  L:0.20  R:0.20  M:0.15
#       → 희귀질환은 Excel Sheet 3(영상·Lab·Micro) 데이터가 대부분 부재하고,
#         Sheet 2(HPO 표현형)에 3,468개 레코드가 수록되어 있어
#         표현형(증상) 매칭이 1차 스크리닝의 핵심 수단이다.
#       → Orphanet/HPO 기반 희귀질환 진단 접근법:
#         "표현형 유사도(phenotypic similarity) 기반 후보 질환 도출 →
#          확진검사(유전자·생검 등)로 확정"
#         [Köhler et al. Am J Hum Genet 2009;84(4):457-467
#          — HPO-based phenotype matching for rare disease diagnosis;
#          Orphanet — "phenotype-driven approach to rare diseases"]
#       → Lab·Radiology·Micro 가중치는 데이터 가용성에 비례하여 낮춤.
#
# [W4] 보정 계수(Adjustments) 및 근거
#
#   (a) Critical Lab Value 보너스: +0.05
#       근거: Critical value("panic value")는 즉각적 의료 개입이 필요한
#       수치로, 해당 질환과의 연관성이 매우 높다.
#       [CAP Critical Values Checklist; Tietz 7th Ch.5]
#
#   (b) Clinical Scoring 보너스: NEWS2 ≥7이고 감염성 질환이면 +0.03
#       근거: NEWS2 ≥7은 "High clinical risk"로 응급 대응이 필요한 수준.
#       급성 감염 질환(폐렴, 패혈증)에서 NEWS2 고점수는 진단 확신도를
#       높이는 보조 근거가 된다.
#       [Royal College of Physicians. NEWS2, 2017]
#
#   (c) 음성 소견 감점: 병리특이소견이 명시적으로 음성이면 -0.10/건
#       근거: 해당 질환에서 반드시 나타나야 하는 소견(pathognomonic
#       finding)이 없으면 해당 질환의 가능성을 유의하게 낮춘다.
#       예: 기흉에서 X-ray 기흉 소견 음성 → 기흉 가능성 대폭 감소.
#       [Harrison's 21st — 각 질환의 "Diagnostic Criteria" 섹션]
#
#   (d) 가용 모달리티 재분배
#       특정 모달리티의 데이터가 전혀 없거나 해당 질환 프로필에
#       해당 모달리티 criteria가 없으면, 그 가중치를 나머지 모달리티에
#       비례 배분한다.
#       이유: 데이터 부재로 인한 불공정한 점수 하락을 방지.
#       예: 미생물 검사 미시행 환자 → M 가중치를 S/L/R에 재분배.
#
# [W5] 참고문헌
#
#   [1] Harrison's Principles of Internal Medicine, 21st Ed (2022)
#   [2] Mandell's Principles and Practice of Infectious Diseases, 9th Ed
#   [3] ATS/IDSA. Diagnosis and Treatment of CAP. AJRCCM 2019
#   [4] GOLD 2024 — Global Strategy for COPD
#   [5] GINA 2024 — Global Initiative for Asthma
#   [6] ESC Guidelines for PE (2022) / PH (2022)
#   [7] ATS/ERS IPF Guidelines (2022)
#   [8] Berlin Definition for ARDS. JAMA 2012
#   [9] Royal College of Physicians. NEWS2, 2017
#   [10] Köhler et al. Clinical diagnostics in HPO. AJHG 2009
#   [11] Orphanet — phenotype-driven rare disease diagnosis approach
#   [12] CAP (College of American Pathologists) Critical Values Checklist
#   [13] Tietz Textbook of Clinical Chemistry, 7th Ed (2024)
#   [14] UpToDate 2025 — disease-specific diagnostic algorithms
# ═══════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import re
from typing import Optional

from ..domain.disease import (
    DiseaseProfile,
    DiseaseScore,
    DiagnosticEvidence,
)
from ..domain.enums import Confidence, DiseaseCategory
from ..domain.findings import (
    LabFinding,
    VitalsRespiratoryHemodynamicFinding,
    MicroFinding,
    SymptomMatch,
    ScoringSystemResult,
    Phase1Result,
)
from ..knowledge.disease_registry import DiseaseRegistry


# ── 기본 가중치 [W3] ─────────────────────────────────────────
# 명시적 가중치가 없는 질환에 적용하는 카테고리별 기본값.

DEFAULT_WEIGHTS = {
    # 전체 기본값 — YAML 17개 질환 가중 평균 기반 [W3(a)]
    "default": {"symptoms": 0.25, "lab": 0.20, "radiology": 0.35, "micro": 0.20},
    # 희귀질환 — HPO 표현형 중심 [W3(b)]
    "rare":    {"symptoms": 0.45, "lab": 0.20, "radiology": 0.20, "micro": 0.15},
}

# ── 보정 상수 [W4] ────────────────────────────────────────────
CRITICAL_LAB_BONUS = 0.05     # [W4(a)]
NEWS2_HIGH_BONUS = 0.03       # [W4(b)]
NEGATIVE_PATHOGNOMONIC = -0.10 # [W4(c)]

# ── 최소 기준 수 정규화 [W4(e)] ───────────────────────────────
# 기준 항목이 1~2개뿐인 질환이 우연 매칭으로 ratio=1.0을 받아
# 과대평가되는 것을 방지한다.
# 각 모달리티에서 최소 이 수 이상의 기준이 있어야 ratio=1.0이 가능.
# 예: Lab 기준 1개 질환은 1/max(1,3)=0.33, 4개 매칭/7개 기준은 4/7=0.57
# → 구체적 프로필(CAP)이 모호한 프로필보다 높게 평가된다.
# [Harrison's 21st — 진단 알고리즘은 다중 소견의 종합을 요구;
#  Bayesian diagnostic reasoning: more evidence = higher confidence]
MINIMUM_CRITERIA_PER_MODALITY = 3

# ── 다중모달 근거 커버리지 [W4(f)] ────────────────────────────
# 하나의 모달리티에서만 근거가 있는 질환은 진단 확신도가 낮다.
# 여러 모달리티(증상+Lab+영상+미생물)에서 동시에 근거가 있을수록
# 진단의 신뢰도가 높아진다 (Bayesian diagnostic convergence).
# coverage_factor = sqrt(active_modalities / patient_available_modalities)
# [Harrison's 21st Ch.1 "The Practice of Medicine" — 진단은 다중
#  소견의 수렴(convergence of evidence)으로 확립됨;
#  Sox et al. Medical Decision Making, 2nd Ed — 독립 소견의
#  조합은 우도비(likelihood ratio)를 기하급수적으로 증가시킴]

# ── 감염 관련 질환 YAML 키 (NEWS2 보너스 적용 대상) ──────────
# 런타임에 registry.yaml_key_map으로 실제 키를 resolve하여 사용.
_INFECTIOUS_YAML_KEYS = {
    "community_acquired_pneumonia", "hospital_acquired_pneumonia",
    "aspiration_pneumonia", "lung_abscess", "tuberculosis",
    "viral_pneumonia", "influenza", "empyema",
    "acute_bronchitis", "acute_bronchiolitis",
}


class DiagnosticScorer:
    """528개 질환 가중 다중모달 스코어링 엔진."""

    def __init__(self, disease_registry: DiseaseRegistry):
        self._registry = disease_registry
        self._registry._ensure_loaded()

    def score_all(
        self,
        patient_lab_findings: list[LabFinding],
        patient_vrh_findings: list[VitalsRespiratoryHemodynamicFinding],
        patient_micro_findings: list[MicroFinding],
        patient_symptom_matches: list[SymptomMatch],
        phase1_result: Optional[Phase1Result] = None,
        scoring_results: Optional[list[ScoringSystemResult]] = None,
        top_n: int = 10,
        include_rare: bool = False,
    ) -> list[DiseaseScore]:
        """전체 질환에 대해 스코어링 수행.

        Args:
            patient_lab_findings: Lab 분석 결과
            patient_vrh_findings: VRH 분석 결과
            patient_micro_findings: 미생물 매칭 결과
            patient_symptom_matches: 증상 매칭 결과
            phase1_result: Phase 1 X-ray 분석 결과 (선택)
            scoring_results: NEWS2/qSOFA 등 스코어링 결과 (선택)
            top_n: 상위 N개 반환
            include_rare: 희귀질환 포함 여부 (False이면 Phase 3로 위임)

        Returns:
            DiseaseScore 목록 (score 내림차순).
        """
        # 환자 소견 전처리
        evidence_bundle = self._build_evidence_bundle(
            patient_lab_findings, patient_vrh_findings,
            patient_micro_findings, patient_symptom_matches,
            phase1_result,
        )

        # NEWS2 점수 확인
        news2_score = 0
        if scoring_results:
            for sr in scoring_results:
                if sr.name.startswith("NEWS2") and not sr.name.endswith("COPD"):
                    news2_score = sr.score

        # 감염성 질환 키 세트 (YAML key → 실제 profile key resolve)
        key_map = self._registry.yaml_key_map
        infectious_keys = set()
        for yk in _INFECTIOUS_YAML_KEYS:
            infectious_keys.add(key_map.get(yk, yk))

        # 전체 질환 스코어링
        scores = []
        for profile in self._registry.get_all():
            if not include_rare and profile.category == DiseaseCategory.RARE:
                continue

            score = self._score_single_disease(
                profile, evidence_bundle, news2_score, infectious_keys
            )
            scores.append(score)

        # 정렬 및 반환
        scores.sort(key=lambda s: s.total_score, reverse=True)
        return scores[:top_n]

    def _build_evidence_bundle(
        self,
        lab_findings: list[LabFinding],
        vrh_findings: list[VitalsRespiratoryHemodynamicFinding],
        micro_findings: list[MicroFinding],
        symptom_matches: list[SymptomMatch],
        phase1: Optional[Phase1Result],
    ) -> dict:
        """환자 소견을 스코어링에 적합한 형태로 정리."""
        # Lab medical terms 집합
        lab_terms = set()
        has_critical_lab = False
        for f in lab_findings:
            if f.severity != "normal" and f.medical_term:
                lab_terms.add(f.medical_term.lower())
            if f.severity == "critical":
                has_critical_lab = True

        # YAML key → profile key 매핑 (disease_associations의 키 변환)
        key_map = self._registry.yaml_key_map

        # Lab disease associations (YAML key를 실제 profile key로 변환)
        lab_disease_map: dict[str, int] = {}
        for f in lab_findings:
            if f.severity == "normal":
                continue
            for da in f.disease_associations:
                dk = da.get("disease_key", "")
                if dk:
                    resolved = key_map.get(dk, dk)
                    lab_disease_map[resolved] = lab_disease_map.get(resolved, 0) + 1

        # VRH disease associations
        vrh_disease_map: dict[str, int] = {}
        for f in vrh_findings:
            if f.severity == "normal":
                continue
            for da in f.disease_associations:
                dk = da.get("disease_key", "")
                if dk:
                    resolved = key_map.get(dk, dk)
                    vrh_disease_map[resolved] = vrh_disease_map.get(resolved, 0) + 1

        # Micro disease → [organisms]
        micro_disease_map: dict[str, list[str]] = {}
        for f in micro_findings:
            for dk in f.matched_diseases:
                micro_disease_map.setdefault(dk, []).append(f.organism)

        # Symptom disease → [symptoms]
        symptom_disease_map: dict[str, list[str]] = {}
        for m in symptom_matches:
            for dk in m.matched_diseases:
                symptom_disease_map.setdefault(dk, []).append(m.symptom)

        # Phase 1 AI keywords
        ai_keywords = set()
        if phase1:
            ai_keywords = {kw.lower() for kw in phase1.ai_keywords_matched}

        return {
            "lab_terms": lab_terms,
            "has_critical_lab": has_critical_lab,
            "lab_disease_map": lab_disease_map,
            "vrh_disease_map": vrh_disease_map,
            "micro_disease_map": micro_disease_map,
            "symptom_disease_map": symptom_disease_map,
            "ai_keywords": ai_keywords,
            # 환자 데이터 가용성 (모달리티별 재분배 판단용) [W4(d)]
            "has_lab_data": len(lab_findings) > 0,
            "has_radiology_data": bool(ai_keywords),
            "has_micro_data": len(micro_findings) > 0,
            "has_symptom_data": len(symptom_matches) > 0,
        }

    def _score_single_disease(
        self,
        profile: DiseaseProfile,
        evidence: dict,
        news2_score: int,
        infectious_keys: set[str] | None = None,
    ) -> DiseaseScore:
        """단일 질환의 스코어 계산.

        알고리즘:
        1. 4개 모달리티별 match_ratio 계산 (0.0~1.0)
        2. 질환별 S/L/R/M 가중치로 가중 합산
        3. 보정 적용 [W4]
        4. Confidence 분류
        """
        # ── 1) 가중치 결정 ────────────────────────────────────
        weights = self._get_weights(profile)

        # ── 2) 모달리티별 매칭 비율 ──────────────────────────
        evidences = []

        # (S) Symptoms
        ratio_s, evid_s = self._calc_symptom_ratio(profile, evidence)

        # (L) Lab
        ratio_l, evid_l = self._calc_lab_ratio(profile, evidence)

        # (R) Radiology
        ratio_r, evid_r = self._calc_radiology_ratio(profile, evidence)

        # (M) Micro
        ratio_m, evid_m = self._calc_micro_ratio(profile, evidence)

        evidences = evid_s + evid_l + evid_r + evid_m

        # ── 3) 가중 합산 (데이터 없는 모달리티 재분배) [W4(d)] ─
        #
        # 재분배 조건: 환자에게 해당 모달리티의 데이터가 전혀 없으면
        # 그 모달리티의 가중치를 제외한다.
        # 예: X-ray 미촬영 시 R 가중치를 S/L/M에 비례 재분배.
        patient_has_data = {
            "symptoms": evidence["has_symptom_data"],
            "lab": evidence["has_lab_data"],
            "radiology": evidence["has_radiology_data"],
            "micro": evidence["has_micro_data"],
        }

        symptom_criteria = len(profile.symptoms) or len(profile.hpo_phenotypes)
        modality_data = [
            ("symptoms", ratio_s, weights["symptoms"], symptom_criteria),
            ("lab", ratio_l, weights["lab"], len(profile.lab_patterns)),
            ("radiology", ratio_r, weights["radiology"],
             len(profile.ai_imaging_keywords) + len(profile.radiology_findings)),
            ("micro", ratio_m, weights["micro"], len(profile.micro_findings)),
        ]

        numerator = 0.0
        denominator = 0.0
        modality_scores = {}

        for mod_name, ratio, weight, criteria_count in modality_data:
            modality_scores[mod_name] = round(ratio, 3)
            # 환자에게 데이터가 있고, 프로필에 기준이 있는 경우만 포함
            if criteria_count > 0 and patient_has_data.get(mod_name, True):
                numerator += weight * ratio
                denominator += weight

        base_score = numerator / denominator if denominator > 0 else 0.0

        # ── 다중모달 근거 커버리지 보정 [W4(f)] ──────────────
        # 프로필에 기준이 있는 모달리티 수 / 환자에게 데이터가 있는 모달리티 수
        import math
        active_modalities = sum(
            1 for mod_name, ratio, weight, cc in modality_data
            if cc > 0 and patient_has_data.get(mod_name, True) and ratio > 0
        )
        patient_modalities = sum(
            1 for mod_name in patient_has_data
            if patient_has_data.get(mod_name, False)
        )
        if patient_modalities > 0 and active_modalities > 0:
            coverage_factor = math.sqrt(active_modalities / patient_modalities)
        else:
            coverage_factor = 0.0
        base_score *= coverage_factor

        # ── 4) 보정 [W4] ─────────────────────────────────────
        adjustments = 0.0

        # [W4(a)] Critical Lab
        if evidence["has_critical_lab"]:
            dk = profile.disease_key
            if dk in evidence["lab_disease_map"]:
                adjustments += CRITICAL_LAB_BONUS

        # [W4(b)] NEWS2 High + 감염성 질환
        if news2_score >= 7 and infectious_keys and profile.disease_key in infectious_keys:
            adjustments += NEWS2_HIGH_BONUS

        final_score = max(0.0, min(1.0, base_score + adjustments))

        # ── 5) Confidence ─────────────────────────────────────
        if final_score > 0.7:
            confidence = Confidence.STRONG
        elif final_score >= 0.4:
            confidence = Confidence.MODERATE
        else:
            confidence = Confidence.WEAK

        matched_count = sum(
            1 for e in evidences if e.matched
        )
        total_criteria = sum(
            c for _, _, _, c in modality_data
        )

        return DiseaseScore(
            disease_key=profile.disease_key,
            name_en=profile.name_en,
            name_kr=profile.name_kr,
            category=profile.category.value,
            icd10_codes=profile.icd10_codes,
            total_score=round(final_score, 4),
            confidence=confidence,
            modality_scores=modality_scores,
            evidence=evidences,
            matched_count=matched_count,
            total_criteria=total_criteria,
        )

    # ── 가중치 결정 ───────────────────────────────────────────
    def _get_weights(self, profile: DiseaseProfile) -> dict[str, float]:
        """질환 프로필에서 가중치 추출. 없으면 기본값 적용 [W2][W3]."""
        s = profile.weight_symptoms
        l = profile.weight_lab
        r = profile.weight_radiology
        m = profile.weight_micro

        total = s + l + r + m

        # 가중치가 명시적으로 설정된 경우 (합 ~1.0)
        if 0.95 <= total <= 1.05:
            return {"symptoms": s, "lab": l, "radiology": r, "micro": m}

        # 기본값 적용
        if profile.category == DiseaseCategory.RARE:
            return DEFAULT_WEIGHTS["rare"]
        return DEFAULT_WEIGHTS["default"]

    # ── 모달리티별 매칭 비율 계산 ─────────────────────────────
    def _calc_symptom_ratio(
        self, profile: DiseaseProfile, evidence: dict
    ) -> tuple[float, list[DiagnosticEvidence]]:
        """증상 매칭 비율.

        분모는 profile.symptoms(핵심 임상 증상 목록)만 사용한다.
        hpo_phenotypes는 "가능한 모든 표현형"의 전수 목록이므로
        Phase 2 진단 스코어링의 분모에는 포함하지 않는다.
        HPO 전수 매칭은 Phase 3 희귀질환 스크리닝에서 수행한다.
        [Harrison's 21st — 진단 알고리즘은 핵심 증상(cardinal symptoms)
         기반이며, 가능한 모든 표현형의 완전 충족을 요구하지 않음]
        """
        disease_symptoms = evidence["symptom_disease_map"].get(
            profile.disease_key, []
        )
        # 핵심 증상만 분모로 사용
        total = len(profile.symptoms)
        if total == 0:
            # 증상 리스트 없으면 HPO 수로 fallback (희귀질환 등)
            total = len(profile.hpo_phenotypes)
        if total == 0:
            return 0.0, []

        matched = len(disease_symptoms)
        # 최소 기준 수 정규화 [W4(e)]
        effective_total = max(total, MINIMUM_CRITERIA_PER_MODALITY)
        ratio = min(matched / effective_total, 1.0)

        evidences = [
            DiagnosticEvidence(
                modality="symptoms",
                finding=s,
                matched=True,
                profile_criterion="symptom match",
                weight=0.0,
            )
            for s in disease_symptoms
        ]
        return ratio, evidences

    def _calc_lab_ratio(
        self, profile: DiseaseProfile, evidence: dict
    ) -> tuple[float, list[DiagnosticEvidence]]:
        """Lab 매칭 비율.

        두 가지 소스에서 매칭:
        1. lab_terms vs profile.lab_patterns (텍스트 매칭)
        2. lab_disease_map에서 해당 질환의 직접 association 수
        """
        patient_terms = evidence["lab_terms"]
        profile_patterns = profile.lab_patterns
        direct_hits = evidence["lab_disease_map"].get(profile.disease_key, 0)

        if not profile_patterns and direct_hits == 0:
            return 0.0, []

        # 텍스트 패턴 매칭 — 핵심 용어(core term) 기반
        # "Elevated CRP (inflammation)"의 핵심: "elevated crp"
        # "Markedly Elevated CRP (severe inflammation/infection)"의 핵심: "markedly elevated crp"
        # → "elevated crp" ⊂ "markedly elevated crp" → 매칭 성공
        text_matched = 0
        evidences = []
        for pattern in profile_patterns:
            pattern_lower = pattern.strip().lower()
            pattern_core = re.sub(r"\s*\(.*?\)", "", pattern_lower).strip()

            matched_flag = False
            for pt in patient_terms:
                pt_core = re.sub(r"\s*\(.*?\)", "", pt).strip()
                # 전체 매칭 또는 핵심 용어 매칭
                if (pattern_lower in pt or pt in pattern_lower
                        or pattern_core in pt_core or pt_core in pattern_core):
                    matched_flag = True
                    break

            if matched_flag:
                text_matched += 1
                evidences.append(DiagnosticEvidence(
                    modality="lab",
                    finding=pattern,
                    matched=True,
                    profile_criterion=pattern,
                ))

        # 직접 association 매칭 (YAML disease_associations)
        # — 텍스트 매칭과 중복 방지 위해 max 사용
        total_criteria = max(len(profile_patterns), 1)
        total_matched = max(text_matched, direct_hits)

        # 최소 기준 수 정규화 [W4(e)]
        effective_total = max(total_criteria, MINIMUM_CRITERIA_PER_MODALITY)
        ratio = min(total_matched / effective_total, 1.0)
        return ratio, evidences

    def _calc_radiology_ratio(
        self, profile: DiseaseProfile, evidence: dict
    ) -> tuple[float, list[DiagnosticEvidence]]:
        """영상 키워드 매칭 비율.

        Phase 1 X-ray AI keywords + VRH disease associations 활용.
        """
        ai_keywords = evidence["ai_keywords"]

        # 프로필의 AI 키워드
        profile_keywords = {kw.lower() for kw in profile.ai_imaging_keywords}
        # YAML radiology_findings도 포함
        profile_keywords.update(kw.lower() for kw in profile.radiology_findings)

        if not profile_keywords:
            # VRH disease_map에서의 매칭으로 대체
            vrh_hits = evidence["vrh_disease_map"].get(profile.disease_key, 0)
            if vrh_hits > 0:
                return min(vrh_hits / 3, 1.0), []
            return 0.0, []

        matched = profile_keywords & ai_keywords
        # 최소 기준 수 정규화 [W4(e)]
        effective_total = max(len(profile_keywords), MINIMUM_CRITERIA_PER_MODALITY)
        ratio = len(matched) / effective_total if effective_total else 0.0

        evidences = [
            DiagnosticEvidence(
                modality="radiology",
                finding=kw,
                matched=True,
                profile_criterion="AI keyword match",
            )
            for kw in matched
        ]
        return min(ratio, 1.0), evidences

    def _calc_micro_ratio(
        self, profile: DiseaseProfile, evidence: dict
    ) -> tuple[float, list[DiagnosticEvidence]]:
        """미생물 매칭 비율."""
        matched_organisms = evidence["micro_disease_map"].get(
            profile.disease_key, []
        )
        total = len(profile.micro_findings)
        if total == 0:
            return 0.0, []

        # 최소 기준 수 정규화 [W4(e)]
        effective_total = max(total, MINIMUM_CRITERIA_PER_MODALITY)
        ratio = min(len(matched_organisms) / effective_total, 1.0)
        evidences = [
            DiagnosticEvidence(
                modality="micro",
                finding=org,
                matched=True,
                profile_criterion="organism match",
            )
            for org in matched_organisms
        ]
        return ratio, evidences
