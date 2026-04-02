"""리포트 빌더 — Phase 1~3 결과를 종합하여 임상소견서 생성.

settings.report_backend에 따라 Bedrock Claude 또는 로컬 템플릿을 사용.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Optional

from ..config.settings import Settings, get_settings
from ..domain.patient import PatientCase
from ..domain.findings import Phase1Result, Phase2Result
from ..domain.disease import Phase3Result
from .bedrock_client import BedrockReportClient
from .local_report_generator import LocalReportGenerator


class ReportBuilder:
    """Phase 1~3 결과 → 임상소견서."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

        if self._settings.report_backend == "bedrock":
            self._generator = BedrockReportClient(
                model_id=self._settings.bedrock_model_id,
                region=self._settings.bedrock_region,
            )
        else:
            self._generator = LocalReportGenerator()

    def build(
        self,
        patient: PatientCase,
        phase1: Optional[Phase1Result] = None,
        phase2: Optional[Phase2Result] = None,
        phase3: Optional[Phase3Result] = None,
    ) -> str:
        """전체 결과를 종합하여 임상소견서 텍스트 생성.

        Returns:
            Markdown 형식 임상소견서 문자열.
        """
        report_data = self._assemble_data(patient, phase1, phase2, phase3)
        return self._generator.generate_report(report_data)

    def _assemble_data(
        self,
        patient: PatientCase,
        phase1: Optional[Phase1Result],
        phase2: Optional[Phase2Result],
        phase3: Optional[Phase3Result],
    ) -> dict[str, Any]:
        """모든 Phase 결과를 단일 딕셔너리로 조립."""
        data: dict[str, Any] = {
            "case_id": patient.case_id,
            "patient": {
                "age": patient.age,
                "sex": patient.sex,
                "chief_complaint": patient.chief_complaint,
                "symptoms": patient.symptoms,
                "hpo_symptoms": patient.hpo_symptoms,
            },
        }

        # Phase 1
        if phase1:
            data["phase1"] = {
                "detected": [
                    {"finding": f.finding, "probability": f.probability,
                     "ai_keywords": f.ai_keywords}
                    for f in phase1.detected_findings
                ],
                "possible": [
                    {"finding": f.finding, "probability": f.probability}
                    for f in phase1.possible_findings
                ],
                "ai_keywords": phase1.ai_keywords_matched,
                "candidate_icd_codes": phase1.candidate_icd_codes,
            }

        # Phase 2
        if phase2:
            data["lab_findings"] = [
                {"name": f.name, "value": f.value, "unit": f.unit,
                 "interpretation": f.interpretation,
                 "medical_term": f.medical_term, "severity": f.severity}
                for f in phase2.lab_findings
            ]
            data["vrh_findings"] = [
                {"name": f.name, "value": f.value, "unit": f.unit,
                 "interpretation": f.interpretation,
                 "medical_term": f.medical_term, "severity": f.severity}
                for f in phase2.vrh_findings
            ]
            data["micro_findings"] = [
                {"organism": f.organism, "matched_diseases": f.matched_diseases}
                for f in phase2.micro_findings
            ]
            data["scoring_systems"] = [
                {"name": s.name, "score": s.score,
                 "interpretation": s.interpretation,
                 "components": s.components}
                for s in phase2.scoring_systems
            ]
            data["ranked_diseases"] = [
                {"disease_key": d.disease_key, "name_en": d.name_en,
                 "name_kr": d.name_kr, "icd10_codes": d.icd10_codes,
                 "total_score": d.total_score, "confidence": d.confidence.value,
                 "modality_scores": d.modality_scores}
                for d in phase2.top_candidates
            ]

        # Phase 3
        if phase3 and phase3.triggered:
            data["phase3"] = {
                "triggered": True,
                "trigger_reasons": phase3.trigger_reasons,
                "rare_candidates": [
                    {"name_en": c.name_en, "name_kr": c.name_kr,
                     "orpha_code": c.orpha_code, "hpo_score": c.hpo_score,
                     "matched_hpo": len(c.matched_hpo),
                     "total_hpo": c.total_hpo,
                     "major_genes": c.major_genes,
                     "genetic_type": c.genetic_type}
                    for c in phase3.rare_candidates
                ],
                "genetic_tests": [
                    {"gene": g.gene, "test_type": g.test_type,
                     "priority": g.priority, "rationale": g.rationale}
                    for g in phase3.genetic_tests_recommended
                ],
            }
            data["confirmatory_tests"] = [
                {"test_name": t.test_name, "test_type": t.test_type,
                 "for_disease": t.for_disease}
                for t in phase3.confirmatory_tests
            ]

        return data
