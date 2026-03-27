# PaperHub

개인 논문 접근 서비스. AWS CDK로 인프라를 배포하고, Bedrock Claude로 AI 요약을 제공합니다.

## 프로젝트 구조
- `infra/` — CDK 인프라 코드 (Python, 3개 스택: Storage / Pipeline / API)
- `lambda/paper_collector/` — PubMed 검색 + PDF 수집
- `lambda/paper_summarizer/` — Bedrock Claude 요약
- `lambda/alert_sender/` — SES 메일 발송
- `scripts/` — 배포/검증 스크립트

## 아키텍처
```
EventBridge → Lambda(수집) → PubMed API → S3(PDF) → Step Functions
                                                        ↓
                                                   Bedrock Claude (요약)
                                                        ↓
                                                   SES (메일 발송)

사용자 → CloudFront → API Gateway → Lambda(API) → DynamoDB
```

## 배포 명령어
```bash
# 전체 배포
./scripts/deploy.sh --all

# 또는 수동
cd infra && cdk deploy --all
```

## 리전
ap-northeast-2 (서울)
