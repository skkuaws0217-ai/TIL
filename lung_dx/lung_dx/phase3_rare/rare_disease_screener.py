"""HPO 빈도가중 희귀질환 스크리닝 엔진.

376개 희귀 폐질환(Orphadata 기반)에 대해 환자의 HPO 표현형을
빈도가중 매칭하여 가능성이 높은 희귀질환을 순위대로 산출한다.

# ═══════════════════════════════════════════════════════════════
# 스크리닝 트리거 조건 및 스코어링 근거
# ═══════════════════════════════════════════════════════════════
#
# [R1] 트리거 조건 (OR 로직)
#   (a) Phase 2 최고 점수 < 0.5 (일반/기타 질환의 설명력 부족)
#   (b) 환자 나이 < 40 + ILD/섬유화 패턴
#   (c) 반복성 기흉, 클러빙, 호산구증가증 등 희귀질환 시사 소견
#   (d) 다장기 침범 패턴
#   (e) 임상의 요청 (always_screen_rare=True)
#
# [R2] HPO 빈도가중 스코어링
#   Orphanet HPO 빈도 코드 → 가중치:
#     HP:0040280 (Obligate, 100%)      → 1.0
#     HP:0040281 (Very frequent, 80-99%) → 0.8
#     HP:0040282 (Frequent, 30-79%)    → 0.6
#     HP:0040283 (Occasional, 5-29%)   → 0.3
#     HP:0040284 (Very rare, 1-4%)     → 0.1
#     빈도 없음                         → 0.5 (중간값)
#   [Orphanet. Orphadata: Free access products.
#    http://www.orphadata.org/cgi-bin/index.php]
#
# [R3] Information Content (IC) 보정
#   희귀한 HPO가 매칭되면 진단적 가치가 높다.
#   IC(hpo) = -log2(해당 HPO 보유 질환 수 / 전체 질환 수)
#   → IC가 높을수록 보너스 크게.
#   [Köhler et al. Clinical diagnostics in human genetics with
#    semantic similarity searches in ontologies. AJHG 2009;84(4):457]
# ═══════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import math
import logging
from typing import Optional

from ..domain.disease import (
    DiseaseProfile,
    RareDiseaseScore,
    Phase3Result,
)
from ..domain.enums import DiseaseCategory, HPOFrequency
from ..domain.findings import Phase2Result
from ..domain.patient import PatientCase
from ..knowledge.disease_registry import DiseaseRegistry

logger = logging.getLogger(__name__)

# 희귀질환 시사 소견 (트리거)
RARE_TRIGGER_FINDINGS = {
    "honeycombing", "eosinophilia", "clubbing", "hemoptysis",
    "recurrent pneumothorax", "pulmonary fibrosis",
    "ground glass", "interstitial",
}

IC_BONUS_SCALE = 0.01  # [R3] IC 보너스 스케일링


class RareDiseaseScreener:
    """376개 희귀 폐질환 HPO 빈도가중 스크리닝."""

    def __init__(self, disease_registry: DiseaseRegistry):
        self._registry = disease_registry
        self._registry._ensure_loaded()

    def should_trigger(
        self,
        phase2_result: Phase2Result,
        patient: PatientCase,
        threshold: float = 0.5,
        force: bool = False,
    ) -> tuple[bool, list[str]]:
        """희귀질환 스크리닝 트리거 여부 판단 [R1]."""
        if force:
            return True, ["임상의 요청 (force=True)"]

        reasons = []

        # (a) Phase 2 최고 점수 < threshold
        if phase2_result.top_candidates:
            top_score = phase2_result.top_candidates[0].total_score
            if top_score < threshold:
                reasons.append(
                    f"Phase 2 최고 점수 {top_score:.3f} < {threshold}"
                )

        # (b) 나이 < 40 + ILD/섬유화 패턴
        if patient.age and patient.age < 40:
            symptoms_lower = {s.lower() for s in patient.symptoms}
            if symptoms_lower & {"fibrosis", "interstitial", "ild"}:
                reasons.append(f"나이 {patient.age} < 40 + ILD 패턴")

        # (c) 희귀질환 시사 소견
        all_symptoms = {s.lower() for s in patient.symptoms}
        all_symptoms.update(s.lower() for s in patient.micro_findings)
        trigger_matches = all_symptoms & RARE_TRIGGER_FINDINGS
        if trigger_matches:
            reasons.append(f"희귀질환 시사 소견: {trigger_matches}")

        return bool(reasons), reasons

    def screen(
        self,
        patient: PatientCase,
        patient_hpo_ids: set[str],
        top_n: int = 20,
    ) -> list[RareDiseaseScore]:
        """376개 희귀질환 HPO 빈도가중 매칭.

        Args:
            patient: 환자 케이스
            patient_hpo_ids: 환자의 HPO ID 집합
                (symptom_matcher.get_patient_hpo_ids()로 변환)
            top_n: 상위 N개 반환

        Returns:
            RareDiseaseScore 목록 (score 내림차순).
        """
        rare_profiles = self._registry.get_by_category(DiseaseCategory.RARE)
        total_rare = len(rare_profiles)

        scores = []
        for profile in rare_profiles:
            score = self._score_rare_disease(
                profile, patient_hpo_ids, total_rare
            )
            if score.hpo_score > 0:
                scores.append(score)

        scores.sort(key=lambda s: s.hpo_score, reverse=True)
        return scores[:top_n]

    def _score_rare_disease(
        self,
        profile: DiseaseProfile,
        patient_hpo_ids: set[str],
        total_diseases: int,
    ) -> RareDiseaseScore:
        """단일 희귀질환의 HPO 빈도가중 스코어 계산 [R2][R3]."""
        matched_hpo = []
        matched_weight = 0.0
        total_weight = 0.0

        for pheno in profile.hpo_phenotypes:
            hpo_id = pheno.get("hpo_id", "")
            freq_code = pheno.get("frequency", "")
            if not hpo_id:
                continue

            weight = HPOFrequency.weight_for_code(freq_code, default=0.5)
            total_weight += weight

            if hpo_id in patient_hpo_ids:
                matched_weight += weight
                matched_hpo.append(hpo_id)

        # 기본 스코어
        base_score = matched_weight / total_weight if total_weight > 0 else 0.0

        # [R3] Information Content 보정
        ic_bonus = 0.0
        for hpo_id in matched_hpo:
            disease_count = self._registry.count_diseases_with_hpo(hpo_id)
            if disease_count > 0 and total_diseases > 0:
                ic = -math.log2(disease_count / total_diseases)
                ic_bonus += ic * IC_BONUS_SCALE

        final_score = min(base_score + ic_bonus, 1.0)

        return RareDiseaseScore(
            disease_key=profile.disease_key,
            name_en=profile.name_en,
            name_kr=profile.name_kr,
            orpha_code=profile.orpha_code or "",
            icd10_codes=profile.icd10_codes,
            hpo_score=round(final_score, 4),
            matched_hpo=matched_hpo,
            total_hpo=len(profile.hpo_phenotypes),
            genetic_type=profile.genetic_type or "",
            major_genes=profile.major_genes,
        )
