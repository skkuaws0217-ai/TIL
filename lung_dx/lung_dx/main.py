"""FastAPI 엔트리포인트.

실행: uvicorn lung_dx.main:app --reload --port 8000
문서: http://localhost:8000/docs (Swagger UI)
"""

from fastapi import FastAPI
from .api.router import router

app = FastAPI(
    title="폐질환 진단 보조 API",
    description=(
        "AI 기반 폐질환 및 희귀질환 진단 보조 프로그램.\n\n"
        "Phase 1: X-ray 이미지 AI 분석\n"
        "Phase 2: Lab + Vitals/Respiratory/Hemodynamic + Micro + Symptoms 다중모달 매칭\n"
        "Phase 3: 376개 희귀질환 HPO 스크리닝 + 유전자 검사 제안\n"
        "Phase 4: 임상소견서 생성"
    ),
    version="1.0.0",
)

app.include_router(router)
