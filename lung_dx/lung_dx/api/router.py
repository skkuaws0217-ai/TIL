"""FastAPI 라우터 — 진단 파이프라인 엔드포인트."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..domain.patient import PatientCase
from ..pipeline import DiagnosticPipeline
from .schemas import (
    DiagnosticRequest,
    DiagnosticResponse,
    LabFindingResponse,
    VRHFindingResponse,
    ScoringResponse,
    DiseaseRankResponse,
    RareDiseaseResponse,
    GeneticTestResponse,
)

router = APIRouter(prefix="/api/v1", tags=["diagnostic"])

# 파이프라인은 앱 시작 시 1회 초기화 (싱글톤)
_pipeline: DiagnosticPipeline | None = None


def get_pipeline() -> DiagnosticPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = DiagnosticPipeline()
    return _pipeline


@router.post("/diagnose", response_model=DiagnosticResponse)
def diagnose(req: DiagnosticRequest) -> DiagnosticResponse:
    """환자 데이터 → 4단계 진단 파이프라인 실행.

    Lab 데이터 입력 방법:
      1) lab_results 직접 입력 (itemid + value)
      2) lab_pdf_path로 PDF 경로 지정 → 자동 파싱하여 lab_results에 추가
      두 방법을 동시에 사용하면 병합된다.
    """
    # Lab 결과 수집
    lab_results = [lr.model_dump() for lr in req.lab_results]

    # PDF 자동 파싱
    if req.lab_pdf_path:
        from ..parsers import PDFLabParser
        parser = PDFLabParser()
        pdf_results = parser.parse(req.lab_pdf_path)
        # 매칭 성공한 항목만 추가
        for r in pdf_results:
            if r.get("itemid") is not None:
                lab_results.append({
                    "itemid": r["itemid"],
                    "value": r["value"],
                    "unit": r.get("unit", ""),
                    "ref_range_lower": r.get("ref_range_lower"),
                    "ref_range_upper": r.get("ref_range_upper"),
                })

    patient = PatientCase(
        case_id=req.case_id,
        age=req.age,
        sex=req.sex,
        chief_complaint=req.chief_complaint,
        symptoms=req.symptoms,
        hpo_symptoms=req.hpo_symptoms,
        lab_results=lab_results,
        vitals_respiratory_hemodynamic=[v.model_dump() for v in req.vitals_respiratory_hemodynamic],
        micro_findings=req.micro_findings,
        xray_image_path=req.xray_image_path,
    )

    pipeline = get_pipeline()

    # 희귀질환 스크리닝 강제 여부 반영
    if req.include_rare_screening:
        pipeline._settings.always_screen_rare = True

    result = pipeline.run(patient)

    # 응답 변환
    response = DiagnosticResponse(case_id=req.case_id)

    if result.phase2:
        p2 = result.phase2
        response.lab_findings = [
            LabFindingResponse(
                name=f.name, value=f.value, unit=f.unit,
                interpretation=f.interpretation,
                medical_term=f.medical_term, severity=f.severity,
            )
            for f in p2.lab_findings if f.severity != "normal"
        ]
        response.vrh_findings = [
            VRHFindingResponse(
                name=f.name, value=f.value, unit=f.unit,
                interpretation=f.interpretation,
                medical_term=f.medical_term, severity=f.severity,
                thresholds_triggered=f.thresholds_triggered,
            )
            for f in p2.vrh_findings if f.severity != "normal"
        ]
        response.scoring_systems = [
            ScoringResponse(
                name=s.name, score=s.score,
                interpretation=s.interpretation, components=s.components,
            )
            for s in p2.scoring_systems
        ]
        response.ranked_diseases = [
            DiseaseRankResponse(
                rank=i + 1,
                disease_key=d.disease_key, name_en=d.name_en,
                name_kr=d.name_kr, icd10_codes=d.icd10_codes,
                total_score=d.total_score, confidence=d.confidence.value,
                modality_scores=d.modality_scores,
            )
            for i, d in enumerate(p2.top_candidates)
        ]

    if result.phase3 and result.phase3.triggered:
        p3 = result.phase3
        response.rare_screening_triggered = True
        response.rare_candidates = [
            RareDiseaseResponse(
                name_en=c.name_en, name_kr=c.name_kr,
                orpha_code=c.orpha_code, hpo_score=c.hpo_score,
                matched_hpo_count=len(c.matched_hpo),
                total_hpo=c.total_hpo,
                major_genes=c.major_genes,
                genetic_type=c.genetic_type,
            )
            for c in p3.rare_candidates
        ]
        response.genetic_tests_recommended = [
            GeneticTestResponse(
                gene=g.gene, test_type=g.test_type,
                priority=g.priority, rationale=g.rationale,
            )
            for g in p3.genetic_tests_recommended
        ]

    response.report_text = result.report_text
    response.errors = result.errors
    response.warnings = result.warnings

    return response


@router.get("/health")
def health_check():
    """서버 상태 확인."""
    return {"status": "ok", "diseases_loaded": get_pipeline()._registry.count}
