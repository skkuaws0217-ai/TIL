"""4단계 진단 파이프라인 오케스트레이터.

Phase 1: X-ray 이미지 AI 분석 → 1차 질환 후보
Phase 2: 다중모달 매칭 (Lab + VRH + Micro + Symptoms) → 2차 진단 추정
Phase 3: 희귀질환 스크리닝 → 유전자/확진검사 제안
Phase 4: 임상소견서 생성 (Bedrock Claude 또는 로컬 템플릿)
"""

from __future__ import annotations

import logging
from typing import Optional

from ..config.settings import Settings, get_settings
from ..domain.patient import PatientCase
from ..domain.findings import Phase1Result, Phase2Result
from ..domain.disease import Phase3Result, FullDiagnosticResult
from ..knowledge import (
    DiseaseRegistry,
    LabReferenceManager,
    VitalsRespiratoryHemodynamicManager,
)
from ..phase1_xray import CheXNetLocal, SageMakerXrayModel, FindingExtractor
from ..phase1_xray.preprocessing import load_and_preprocess
from ..phase2_multimodal import (
    LabAnalyzer,
    VitalsRespiratoryHemodynamicAnalyzer,
    MicroAnalyzer,
    SymptomMatcher,
    DiagnosticScorer,
)
from ..phase3_rare import (
    RareDiseaseScreener,
    GeneticRecommender,
    ConfirmatoryPlanner,
)
from ..phase4_report.report_builder import ReportBuilder

logger = logging.getLogger(__name__)


class DiagnosticPipeline:
    """4단계 전체 진단 파이프라인."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

        # Knowledge Base
        self._registry = DiseaseRegistry()
        self._lab_ref = LabReferenceManager()
        self._vrh_ref = VitalsRespiratoryHemodynamicManager()

        # 로드
        self._registry.load()
        self._lab_ref.load()
        self._vrh_ref.load()

        # Phase 1
        self._xray_model = self._init_xray_model()
        self._finding_extractor = FindingExtractor(self._registry, self._settings)

        # Phase 2
        self._lab_analyzer = LabAnalyzer(self._lab_ref)
        self._vrh_analyzer = VitalsRespiratoryHemodynamicAnalyzer(self._vrh_ref)
        self._micro_analyzer = MicroAnalyzer()
        self._symptom_matcher = SymptomMatcher()
        self._scorer = DiagnosticScorer(self._registry)

        # Phase 3
        self._rare_screener = RareDiseaseScreener(self._registry)
        self._genetic_recommender = GeneticRecommender()
        self._confirmatory_planner = ConfirmatoryPlanner(self._registry)

        # Phase 4
        self._report_builder = ReportBuilder(self._settings)

        logger.info("DiagnosticPipeline 초기화 완료")

    def _init_xray_model(self):
        if self._settings.xray_backend == "sagemaker":
            return SageMakerXrayModel(
                endpoint_name=self._settings.sagemaker_endpoint,
                region=self._settings.bedrock_region,
            )
        return CheXNetLocal(
            weights_path=self._settings.chexnet_weights_path,
        )

    def run(self, patient: PatientCase) -> FullDiagnosticResult:
        """4단계 전체 파이프라인 실행.

        Args:
            patient: 환자 케이스 (필수: symptoms 또는 lab_results 중 하나 이상)

        Returns:
            FullDiagnosticResult (phase1~4 + report_text)
        """
        result = FullDiagnosticResult(patient_case_id=patient.case_id)

        # ── Phase 1: X-ray ────────────────────────────────────
        phase1 = None
        if patient.xray_image_path:
            try:
                phase1 = self._run_phase1(patient)
                result.phase1 = phase1
                logger.info("Phase 1 완료: %d detected, %d possible",
                            len(phase1.detected_findings),
                            len(phase1.possible_findings))
            except Exception as e:
                result.warnings.append(f"Phase 1 오류: {e}")
                logger.warning("Phase 1 실패: %s", e)

        # ── Phase 2: Multi-modal ──────────────────────────────
        try:
            phase2 = self._run_phase2(patient, phase1)
            result.phase2 = phase2
            top = phase2.top_candidates[0] if phase2.top_candidates else None
            logger.info("Phase 2 완료: 1위 %s (%.3f)",
                        top.name_kr if top else "N/A",
                        top.total_score if top else 0)
        except Exception as e:
            result.errors.append(f"Phase 2 오류: {e}")
            logger.error("Phase 2 실패: %s", e)
            phase2 = Phase2Result()

        # ── Phase 3: Rare Disease ─────────────────────────────
        phase3 = None
        triggered, reasons = self._rare_screener.should_trigger(
            phase2, patient,
            threshold=self._settings.rare_trigger_threshold,
            force=self._settings.always_screen_rare,
        )
        if triggered:
            try:
                phase3 = self._run_phase3(patient, phase2, reasons)
                result.phase3 = phase3
                logger.info("Phase 3 완료: %d 희귀질환 후보",
                            len(phase3.rare_candidates))
            except Exception as e:
                result.warnings.append(f"Phase 3 오류: {e}")
                logger.warning("Phase 3 실패: %s", e)

        # ── Phase 4: Report ───────────────────────────────────
        try:
            report_text = self._report_builder.build(
                patient, phase1, phase2, phase3
            )
            result.report_text = report_text
            logger.info("Phase 4 완료: 소견서 생성 (%d자)", len(report_text))
        except Exception as e:
            result.errors.append(f"Phase 4 오류: {e}")
            logger.error("Phase 4 실패: %s", e)

        return result

    # ── Phase 구현 ────────────────────────────────────────────
    def _run_phase1(self, patient: PatientCase) -> Phase1Result:
        """X-ray 이미지 분석."""
        image_tensor = load_and_preprocess(patient.xray_image_path)
        predictions = self._xray_model.predict(image_tensor)
        return self._finding_extractor.extract(predictions)

    def _run_phase2(
        self, patient: PatientCase, phase1: Optional[Phase1Result]
    ) -> Phase2Result:
        """다중모달 매칭 + 스코어링."""
        # 분석
        lab_findings = self._lab_analyzer.analyze(patient.lab_results)
        vrh_findings = self._vrh_analyzer.analyze(patient.vitals_respiratory_hemodynamic)
        micro_findings = self._micro_analyzer.analyze(
            patient.micro_findings, self._registry.get_all()
        )
        symptom_matches = self._symptom_matcher.match(
            patient.symptoms, patient.hpo_symptoms, self._registry.get_all()
        )

        # 스코어링 시스템
        scoring = self._vrh_analyzer.compute_scoring_systems(
            patient.vitals_respiratory_hemodynamic,
            patient_age=patient.age,
        )

        # 파생 지표
        derived = self._vrh_analyzer.compute_derived_indicators(
            patient.vitals_respiratory_hemodynamic
        )

        # 질환 스코어링
        ranked = self._scorer.score_all(
            patient_lab_findings=lab_findings,
            patient_vrh_findings=vrh_findings,
            patient_micro_findings=micro_findings,
            patient_symptom_matches=symptom_matches,
            phase1_result=phase1,
            scoring_results=scoring,
            top_n=self._settings.top_n_diseases,
        )

        return Phase2Result(
            lab_findings=lab_findings,
            vrh_findings=vrh_findings,
            micro_findings=micro_findings,
            symptom_matches=symptom_matches,
            scoring_systems=scoring,
            derived_indicators=derived,
            ranked_diseases=ranked,
            top_candidates=ranked[:self._settings.top_n_diseases],
        )

    def _run_phase3(
        self,
        patient: PatientCase,
        phase2: Phase2Result,
        trigger_reasons: list[str],
    ) -> Phase3Result:
        """희귀질환 스크리닝."""
        # 환자 증상 → HPO ID 변환
        patient_hpo = set(patient.hpo_symptoms)
        patient_hpo.update(
            self._symptom_matcher.get_patient_hpo_ids(
                patient.symptoms, self._registry.get_all()
            )
        )

        # 스크리닝
        rare_candidates = self._rare_screener.screen(
            patient, patient_hpo, top_n=self._settings.rare_top_n
        )

        # 유전자 검사 추천
        genetic_recs = self._genetic_recommender.recommend(rare_candidates, patient)

        # 확진검사 계획
        confirmatory = self._confirmatory_planner.plan(rare_candidates, patient)

        return Phase3Result(
            triggered=True,
            trigger_reasons=trigger_reasons,
            rare_candidates=rare_candidates,
            genetic_tests_recommended=genetic_recs,
            confirmatory_tests=confirmatory,
        )
