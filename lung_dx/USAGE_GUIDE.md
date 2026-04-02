# 폐질환 진단 보조 프로그램 사용 가이드

## 1. 프로그램 개요

AI 기반 폐질환 및 희귀질환 진단 보조 프로그램.

**4단계 파이프라인:**
- Phase 1: X-ray 이미지 AI 분석 → 영상학적 소견 추출 → 1차 질환 후보
- Phase 2: Lab + Vitals/Respiratory/Hemodynamic + 미생물 + 증상(HPO) → 가중 스코어링 → 2차 진단 추정
- Phase 3: 376개 희귀 폐질환 HPO 스크리닝 → 유전자/확진검사 제안
- Phase 4: 임상소견서 자동 생성

**데이터 기준 (유일한 기준 출처):**
- `lab_reference_ranges_v3.yaml` — 89개 Lab 검사항목 (정상범위, critical, threshold, disease_associations)
- `vitals_respiratory_hemodynamic_reference_range_v1.yaml` — 37개 VRH 파라미터 (정상범위, thresholds, 스코어링 시스템)
- `lung_disease_profiles_v2.yaml` — 17개 질환 상세 프로필
- `lung_disease_symptoms_v2.yaml` — 17개 질환 증상 상세
- `일반_폐질환_데이터베이스_v4.xlsx` — 82개 일반 폐질환
- `기타_폐관련_질환_데이터베이스_v4.xlsx` — 70개 기타 폐관련 질환
- `희귀_폐질환_데이터베이스_v4.xlsx` — 376개 희귀 폐질환

---

## 2. 설치

```bash
cd /Users/skku_aws2_05/Documents/pre_project/lung_disease_lab_data
pip install -r requirements.txt
```

추가 패키지 (Lab PDF 자동 파싱용):
```bash
pip install pdfplumber rapidfuzz
```

---

## 3. 사용 방법

### 3-1. JSON 파일로 실행

환자 데이터를 JSON 파일로 작성한 후 실행한다.

```bash
python -m lung_dx.cli --json scripts/sample_patient.json
```

**JSON 형식:**

```json
{
  "case_id": "PATIENT-001",
  "age": 65,
  "sex": "M",
  "chief_complaint": "발열, 기침, 호흡곤란 3일",
  "symptoms": ["cough", "fever", "dyspnea", "sputum production"],
  "hpo_symptoms": ["HP:0012735", "HP:0001945", "HP:0002094"],
  "lab_results": [
    {"itemid": 51301, "value": 18.5},
    {"itemid": 50889, "value": 150.0},
    {"itemid": 50821, "value": 58.0}
  ],
  "vitals_respiratory_hemodynamic": [
    {"itemid": 220277, "value": 88.0},
    {"itemid": 220210, "value": 32.0},
    {"itemid": 220045, "value": 115.0},
    {"itemid": 223762, "value": 39.2}
  ],
  "micro_findings": ["Streptococcus pneumoniae"],
  "xray_image_path": null,
  "include_rare_screening": false
}
```

**출력:**
- 터미널에 Markdown 형식 임상소견서 출력
- `scripts/sample_patient.result.json`에 진단 순위 저장

### 3-2. Lab 결과지 PDF 자동 파싱

Lab 결과값을 직접 입력하지 않고, 결과지 PDF를 자동으로 인식한다.

```bash
python -m lung_dx.cli --json patient.json --lab-pdf lab_results.pdf
```

PDF에서 자동 추출하는 정보:
- 검사명 (한국어/영문 모두 인식)
- 결과값
- 단위
- 참고범위

인식 예시:
- "백혈구", "WBC", "White Blood Cell Count" → 모두 ItemID 51301로 매칭
- "CRP", "C반응단백", "C-Reactive Protein" → 모두 ItemID 50889로 매칭
- "산소분압", "pO2" → 모두 ItemID 50821로 매칭

매칭에 실패한 항목은 터미널에 미매칭 목록으로 출력된다.

### 3-3. 대화형 모드

터미널에서 항목별로 직접 입력한다.

```bash
python -m lung_dx.cli --interactive
```

안내에 따라 입력:
```
Case ID: TEST-001
나이: 65
성별 (M/F): M
주소 (chief complaint): 발열, 기침
증상 (쉼표 구분, 영문): cough, fever, dyspnea
미생물 소견 (쉼표 구분): Streptococcus pneumoniae

Lab 결과 입력 (빈 줄 입력 시 종료):
  형식: itemid,value
  > 51301,18.5
  > 50889,150
  > 50821,58
  >

VRH 데이터 입력 (빈 줄 입력 시 종료):
  형식: itemid,value
  > 220277,88
  > 220210,32
  >
```

### 3-4. FastAPI 웹 API

```bash
uvicorn lung_dx.main:app --reload --port 8000
```

- Swagger UI: http://localhost:8000/docs
- 상태 확인: `GET /api/v1/health`
- 진단 실행: `POST /api/v1/diagnose`

API 요청 예시:
```bash
curl -X POST http://localhost:8000/api/v1/diagnose \
  -H "Content-Type: application/json" \
  -d '{
    "case_id": "API-001",
    "age": 55,
    "symptoms": ["cough", "fever", "dyspnea"],
    "lab_results": [
      {"itemid": 51301, "value": 15.0},
      {"itemid": 50821, "value": 65.0}
    ],
    "vitals_respiratory_hemodynamic": [
      {"itemid": 220277, "value": 91.0}
    ]
  }'
```

Lab PDF 경로를 API로 전달할 수도 있다:
```json
{
  "lab_pdf_path": "/path/to/lab_results.pdf",
  "symptoms": ["cough", "fever"]
}
```

---

## 4. ItemID 참조표

### 4-1. 자주 사용하는 Lab ItemID

| ItemID | 검사명 | 단위 | 정상범위 | 카테고리 |
|--------|--------|------|----------|----------|
| 50821 | pO2 | mmHg | 80-100 | Blood Gas |
| 50818 | pCO2 | mmHg | 35-45 | Blood Gas |
| 50820 | pH | — | 7.35-7.45 | Blood Gas |
| 50813 | Lactate | mmol/L | 0.5-2.0 | Blood Gas |
| 51301 | WBC | K/uL | 4.5-11.0 | CBC |
| 51222 | Hemoglobin | g/dL | 12.0-17.5 | CBC |
| 51265 | Platelet | K/uL | 150-400 | CBC |
| 50862 | Albumin | g/dL | 3.5-5.5 | Chemistry |
| 50912 | Creatinine | mg/dL | 0.6-1.2 | Chemistry |
| 51006 | BUN | mg/dL | 6-20 | Chemistry |
| 50931 | Glucose | mg/dL | 70-100 | Chemistry |
| 50971 | Potassium | mEq/L | 3.5-5.0 | Electrolytes |
| 50983 | Sodium | mEq/L | 136-145 | Electrolytes |
| 50889 | CRP | mg/L | 0-10 | Inflammatory |
| 50963 | BNP | pg/mL | 0-100 | Cardiac |
| 50911 | NT-proBNP | pg/mL | 0-125 | Cardiac |
| 51003 | Troponin T | ng/mL | 0-0.014 | Cardiac |
| 50915 | D-Dimer | ng/mL | 0-500 | Coagulation |

전체 89개 항목은 `lab_reference_ranges_v3.yaml`에서 확인.

### 4-2. 자주 사용하는 VRH ItemID

| ItemID | 파라미터 | 단위 | 정상범위 | 카테고리 |
|--------|----------|------|----------|----------|
| 220277 | SpO2 | % | 95-100 | Vital Signs |
| 220210 | Respiratory Rate | insp/min | 12-20 | Vital Signs |
| 220045 | Heart Rate | bpm | 60-100 | Vital Signs |
| 223762 | Temperature (°C) | °C | 36.1-37.2 | Vital Signs |
| 220050 | Arterial BP systolic | mmHg | 90-140 | Vital Signs |
| 220051 | Arterial BP diastolic | mmHg | 60-90 | Vital Signs |
| 223835 | FiO2 | fraction | 0.21-1.0 | Respiratory |
| 220339 | PEEP | cmH2O | 0-24 | Respiratory |
| 224684 | Tidal Volume (set) | mL | 300-700 | Respiratory |
| 224696 | Plateau Pressure | cmH2O | 10-25 | Respiratory |
| 224695 | Peak Insp. Pressure | cmH2O | 10-30 | Respiratory |
| 220074 | CVP | mmHg | 2-8 | Hemodynamic |
| 220059 | PA Pressure systolic | mmHg | 15-30 | Hemodynamic |

전체 37개 항목은 `vitals_respiratory_hemodynamic_reference_range_v1.yaml`에서 확인.

---

## 5. 출력 소견서 섹션

| 섹션 | 내용 |
|------|------|
| 1. 환자 정보 | 나이, 성별 |
| 2. 주소 및 증상 | 주소(chief complaint), 증상 목록 |
| 3. 영상검사 소견 | X-ray AI 분석 결과 (Phase 1, X-ray 제공 시) |
| 4. 검사실 소견 | Lab 비정상 항목 표 (해석, medical term, 심각도) |
| 5. VRH 소견 | Vitals/Respiratory/Hemodynamic 비정상 항목 + 스코어링(NEWS2, qSOFA, CURB-65, PESI, Wells PE) |
| 6. 미생물학적 소견 | 검출 균종 및 매칭 질환 |
| 7. 추정 진단 | 질환 순위 (Score, Confidence, ICD-10) |
| 8. 희귀질환 평가 | HPO 매칭 결과 + 유전자 검사 추천 (Phase 3 트리거 시) |
| 9. 추가 검사 권고 | 미수행 확진검사 목록 |
| 10. 종합 소견 | 가장 유력한 진단 요약 |

---

## 6. 희귀질환 스크리닝

Phase 3는 다음 조건에서 자동 트리거된다:
- Phase 2 최고 점수 < 0.5 (일반 질환의 설명력 부족)
- 환자 나이 < 40 + ILD/섬유화 패턴
- 희귀질환 시사 소견 (honeycombing, 반복 기흉, clubbing 등)

강제 실행하려면:
- JSON: `"include_rare_screening": true`
- API: `"include_rare_screening": true`

---

## 7. AWS 모드 전환

로컬 모드에서 AWS 모드로 전환할 때 환경변수만 설정하면 된다.
코드 수정 없이 동일한 파이프라인이 AWS 서비스를 사용한다.

```bash
# X-ray 모델: 로컬 CheXNet → SageMaker 엔드포인트
export LUNG_DX_XRAY_BACKEND=sagemaker
export LUNG_DX_SAGEMAKER_ENDPOINT=chexnet-endpoint

# 소견서 생성: 로컬 템플릿 → Bedrock Claude
export LUNG_DX_REPORT_BACKEND=bedrock
export LUNG_DX_BEDROCK_MODEL_ID=anthropic.claude-sonnet-4-20250514
export LUNG_DX_BEDROCK_REGION=us-east-1

# 데이터 저장: 로컬 → S3
export LUNG_DX_DATA_BACKEND=s3
export LUNG_DX_S3_BUCKET=my-lung-dx-bucket
```

| 설정 | 로컬 (기본) | AWS |
|------|------------|-----|
| `LUNG_DX_XRAY_BACKEND` | local | sagemaker |
| `LUNG_DX_REPORT_BACKEND` | template | bedrock |
| `LUNG_DX_DATA_BACKEND` | local | s3 |

AWS 자격증명이 필요하다:
```bash
aws configure
```

---

## 8. MIMIC-IV 대량 환자 검증 (선택)

MIMIC-IV CSV에서 환자 측정값을 일괄 추출하려면:

```bash
python scripts/build_mimic_parquets.py
```

이 스크립트는 chartevents.csv(42GB)와 labevents.csv(18GB)에서
YAML에 정의된 ItemID에 해당하는 환자 측정값만 추출하여
parquet 파일로 저장한다. 추출 기준은 위 2개 YAML 파일이다.

---

## 9. 프로젝트 파일 구조

```
lung_disease_lab_data/
├── lung_dx/                          # 메인 패키지 (52개 .py)
│   ├── config/                       # 설정 (paths, settings)
│   ├── domain/                       # 데이터 모델
│   ├── knowledge/                    # 536개 질환 레지스트리 + YAML 매니저
│   ├── phase1_xray/                  # X-ray AI (CheXNet + GradCAM)
│   ├── phase2_multimodal/            # 다중모달 스코어링
│   ├── phase3_rare/                  # 희귀질환 HPO 스크리닝
│   ├── phase4_report/                # 임상소견서 생성
│   ├── pipeline/                     # 4단계 오케스트레이터
│   ├── mimic_etl/                    # MIMIC-IV CSV → parquet
│   ├── parsers/                      # Lab PDF 자동 파싱
│   ├── aws/                          # S3 + SageMaker
│   ├── api/                          # FastAPI
│   ├── main.py                       # FastAPI 엔트리포인트
│   └── cli.py                        # CLI 도구
├── scripts/
│   ├── sample_patient.json           # 샘플 환자 데이터
│   ├── build_disease_registry.py     # 질환 레지스트리 검증
│   ├── build_mimic_parquets.py       # MIMIC-IV ETL
│   └── test_phase2_scoring.py        # Phase 2 스코어링 검증
├── MIMIC-IV data_origin/
│   ├── data/                         # 7개 핵심 파일 (YAML + Excel)
│   ├── chartevents.csv               # 환자 VRH 측정값 (42GB)
│   └── labevents.csv                 # 환자 Lab 측정값 (18GB)
├── requirements.txt
└── USAGE_GUIDE.md                    # 이 파일
```
