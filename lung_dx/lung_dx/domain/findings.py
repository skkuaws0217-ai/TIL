"""개별 검사 소견 데이터 모델."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────
# Phase 1: 영상학적 소견
# ─────────────────────────────────────────────────────────────
@dataclass
class XrayPrediction:
    """CheXNet 모델 출력 — CheXpert label 하나의 확률."""
    label: str              # CheXpert label (e.g., "Consolidation")
    probability: float      # 0.0 ~ 1.0


@dataclass
class RadiologyFinding:
    """X-ray에서 발견된 영상학적 소견 하나."""
    finding: str                    # e.g., "consolidation"
    present: bool = True            # True=확인, False=의심(possible)
    probability: float = 0.0       # CheXNet 확률
    ai_keywords: list[str] = field(default_factory=list)  # 매칭된 AI 키워드
    location: Optional[str] = None  # e.g., "left lower lobe"
    icd10_codes: list[str] = field(default_factory=list)  # 연관 ICD-10


@dataclass
class Phase1Result:
    """Phase 1 X-ray 분석 결과."""
    detected_findings: list[RadiologyFinding] = field(default_factory=list)   # prob >= threshold
    possible_findings: list[RadiologyFinding] = field(default_factory=list)   # possible 범위
    all_predictions: list[XrayPrediction] = field(default_factory=list)       # 전체 14개 label
    candidate_icd_codes: list[str] = field(default_factory=list)
    ai_keywords_matched: list[str] = field(default_factory=list)
    gradcam_paths: dict[str, str] = field(default_factory=dict)  # label → 히트맵 경로


# ─────────────────────────────────────────────────────────────
# Phase 2: Lab / Vitals·Respiratory·Hemodynamic / Micro / Symptom 소견
# ─────────────────────────────────────────────────────────────
@dataclass
class LabFinding:
    """Lab 검사값 해석 결과."""
    itemid: int | str               # MIMIC ItemID 또는 EXT_XX
    name: str                       # 검사명 (e.g., "pO2")
    value: float | str              # 실측값
    unit: str = ""
    ref_lower: Optional[float] = None
    ref_upper: Optional[float] = None
    interpretation: str = ""        # "Low", "High", "Normal", "Critical"
    medical_term: str = ""          # "Hypoxemia", "Leukocytosis" 등
    severity: str = "normal"        # normal / abnormal / critical
    disease_associations: list[dict] = field(default_factory=list)
    ref_source: str = ""


@dataclass
class VitalsRespiratoryHemodynamicFinding:
    """Vitals / Respiratory / Hemodynamic 파라미터 해석 결과."""
    itemid: int
    name: str                       # e.g., "SpO2"
    name_kr: str = ""               # e.g., "산소포화도"
    value: float = 0.0
    unit: str = ""
    interpretation: str = ""
    medical_term: str = ""
    severity: str = "normal"
    thresholds_triggered: list[str] = field(default_factory=list)
    scoring_contributions: dict[str, int] = field(default_factory=dict)  # {"NEWS2": 3}
    disease_associations: list[dict] = field(default_factory=list)


@dataclass
class MicroFinding:
    """미생물 소견 (Excel DB 매칭 기반, CSV 미사용)."""
    organism: str                   # 균종명
    matched_diseases: list[str] = field(default_factory=list)  # 매칭된 질환 키


@dataclass
class SymptomMatch:
    """환자 증상과 질환 프로필 간 매칭 결과."""
    symptom: str                    # 증상명
    hpo_id: str = ""                # HPO ID
    hpo_kr: str = ""                # 한국어 증상명
    frequency: str = ""             # "common", "frequent", "occasional" 또는 HPO 빈도코드
    matched_diseases: list[str] = field(default_factory=list)


@dataclass
class ScoringSystemResult:
    """임상 스코어링 시스템 계산 결과."""
    name: str                       # "NEWS2", "qSOFA", "CURB-65", "PESI"
    score: int | float = 0
    interpretation: str = ""        # e.g., "High risk"
    components: dict[str, int | float] = field(default_factory=dict)


@dataclass
class DerivedIndicator:
    """파생 지표 (S/F ratio, P/F ratio 등)."""
    name: str
    value: float = 0.0
    interpretation: str = ""
    category: str = ""              # e.g., "mild_ards", "moderate_ards"


@dataclass
class Phase2Result:
    """Phase 2 다중모달 매칭 결과."""
    lab_findings: list[LabFinding] = field(default_factory=list)
    vrh_findings: list[VitalsRespiratoryHemodynamicFinding] = field(default_factory=list)
    micro_findings: list[MicroFinding] = field(default_factory=list)
    symptom_matches: list[SymptomMatch] = field(default_factory=list)
    scoring_systems: list[ScoringSystemResult] = field(default_factory=list)
    derived_indicators: list[DerivedIndicator] = field(default_factory=list)
    ranked_diseases: list = field(default_factory=list)  # List[DiseaseScore]
    top_candidates: list = field(default_factory=list)   # List[DiseaseScore]
