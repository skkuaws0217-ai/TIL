# PaperHub — AWS + SageMaker 기반 논문 접근 서비스

## 아키텍처 개요

```
사용자 → CloudFront → API Gateway → Lambda (서빙)
                                        ↓
                                   DynamoDB (메타)
                                   S3 (PDF 캐시)
                                   ElastiCache (Redis)

EventBridge (주기 트리거) → Lambda (수집) → PubMed API
                                          → Sci-Hub fetch
                                          → S3 저장
                                          → Step Functions
                                              ↓
                                         SageMaker Endpoint
                                         (한줄 요약 / 전문 요약 / 추천)
                                              ↓
                                         SES 메일 발송
```

## 프로젝트 구조

```
paperhub/
├── infra/                    # CDK 인프라 코드
│   ├── app.py
│   ├── stacks/
│   │   ├── storage_stack.py      # DynamoDB, S3, ElastiCache
│   │   ├── sagemaker_stack.py    # SageMaker Endpoint
│   │   ├── api_stack.py          # API Gateway + Lambda
│   │   └── pipeline_stack.py     # EventBridge + Step Functions
│   └── requirements.txt
├── lambda/
│   ├── paper_collector/      # 논문 수집 Lambda
│   ├── paper_summarizer/     # SageMaker 호출 Lambda
│   └── alert_sender/         # SES 메일 발송 Lambda
├── sagemaker/
│   ├── deploy_endpoint.py    # SageMaker 엔드포인트 배포
│   ├── model/
│   │   ├── inference.py      # 추론 코드
│   │   └── requirements.txt
│   └── notebook/
│       └── fine_tune.ipynb   # (선택) 파인튜닝 노트북
└── frontend/                 # React 프론트엔드
```

## 빠른 시작

### 1. 사전 요구사항
- AWS CLI 설정 완료
- Python 3.11+
- Node.js 18+
- AWS CDK v2

### 2. SageMaker 엔드포인트 배포
```bash
cd sagemaker/
pip install -r requirements.txt
python deploy_endpoint.py
```

### 3. 인프라 배포
```bash
cd infra/
pip install -r requirements.txt
cdk bootstrap
cdk deploy --all
```

### 4. 프론트엔드 배포
```bash
cd frontend/
npm install && npm run build
aws s3 sync build/ s3://paperhub-frontend-bucket/
```
