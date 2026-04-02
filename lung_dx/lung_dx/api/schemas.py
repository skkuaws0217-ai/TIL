"""API 요청/응답 Pydantic 모델."""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Any, Optional


# ── 요청 ──────────────────────────────────────────────────────
class LabResultInput(BaseModel):
    itemid: int | str
    value: float | str
    unit: str = ""
    ref_range_lower: Optional[float] = None
    ref_range_upper: Optional[float] = None


class VRHInput(BaseModel):
    itemid: int
    value: float | str
    unit: str = ""
    timestamp: str = ""


class DiagnosticRequest(BaseModel):
    """진단 파이프라인 요청."""
    case_id: str = Field(default="", description="환자 케이스 ID")
    age: Optional[int] = Field(default=None, description="나이")
    sex: Optional[str] = Field(default=None, description="성별 (M/F)")
    chief_complaint: str = Field(default="", description="주소")
    symptoms: list[str] = Field(default_factory=list, description="증상 목록 (영문)")
    hpo_symptoms: list[str] = Field(default_factory=list, description="HPO ID 목록")
    lab_results: list[LabResultInput] = Field(default_factory=list)
    vitals_respiratory_hemodynamic: list[VRHInput] = Field(default_factory=list)
    micro_findings: list[str] = Field(default_factory=list, description="미생물 소견")
    lab_pdf_path: Optional[str] = Field(default=None, description="Lab 결과지 PDF 경로 (자동 파싱)")
    xray_image_path: Optional[str] = Field(default=None, description="X-ray 이미지 경로")
    include_rare_screening: bool = Field(default=False, description="희귀질환 스크리닝 강제 실행")


# ── 응답 ──────────────────────────────────────────────────────
class LabFindingResponse(BaseModel):
    name: str
    value: Any
    unit: str
    interpretation: str
    medical_term: str
    severity: str


class VRHFindingResponse(BaseModel):
    name: str
    value: Any
    unit: str
    interpretation: str
    medical_term: str
    severity: str
    thresholds_triggered: list[str] = []


class ScoringResponse(BaseModel):
    name: str
    score: float
    interpretation: str
    components: dict[str, Any] = {}


class DiseaseRankResponse(BaseModel):
    rank: int
    disease_key: str
    name_en: str
    name_kr: str
    icd10_codes: list[str]
    total_score: float
    confidence: str
    modality_scores: dict[str, float]


class RareDiseaseResponse(BaseModel):
    name_en: str
    name_kr: str
    orpha_code: str
    hpo_score: float
    matched_hpo_count: int
    total_hpo: int
    major_genes: list[str]
    genetic_type: str


class GeneticTestResponse(BaseModel):
    gene: str
    test_type: str
    priority: str
    rationale: str


class DiagnosticResponse(BaseModel):
    """진단 파이프라인 응답."""
    case_id: str
    lab_findings: list[LabFindingResponse] = []
    vrh_findings: list[VRHFindingResponse] = []
    scoring_systems: list[ScoringResponse] = []
    ranked_diseases: list[DiseaseRankResponse] = []
    rare_screening_triggered: bool = False
    rare_candidates: list[RareDiseaseResponse] = []
    genetic_tests_recommended: list[GeneticTestResponse] = []
    report_text: str = ""
    errors: list[str] = []
    warnings: list[str] = []
