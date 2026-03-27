# PaperHub 배포 가이드 (Quick Start)

## 1단계: SageMaker 엔드포인트 배포

### Option A: SageMaker + HuggingFace LLM (추천)

```bash
# SageMaker 노트북 또는 로컬에서 실행
cd sagemaker/
pip install -r requirements.txt

# Llama 3 8B 배포 (ml.g5.2xlarge 기준 월 ~$1,400)
python deploy_endpoint.py \
    --model-id meta-llama/Meta-Llama-3-8B-Instruct \
    --instance-type ml.g5.2xlarge

# 또는 더 큰 모델 (ml.g5.12xlarge 기준 월 ~$5,600)
python deploy_endpoint.py \
    --model-id meta-llama/Meta-Llama-3-70B-Instruct \
    --instance-type ml.g5.12xlarge

# 상태 확인
python deploy_endpoint.py --status

# 사용 안 할 때 삭제 (비용 절약!)
python deploy_endpoint.py --delete
```

### Option B: Amazon Bedrock (SageMaker 없이)

CDK 배포 시 환경 변수만 변경:
```python
# infra/app.py에서
"USE_BEDROCK": "true",
"BEDROCK_MODEL_ID": "anthropic.claude-3-5-sonnet-20241022-v2:0",
```

### 비용 비교

| 방식 | 월간 예상 비용 | 장점 |
|------|-------------|------|
| SageMaker g5.2xlarge | ~$1,400 | 전용 엔드포인트, 낮은 지연 |
| SageMaker (Serverless) | 사용량 기반 | 비용 효율적 |
| Bedrock Claude | ~$50-200 | 관리 불필요, 종량제 |
| Bedrock Haiku | ~$10-50 | 가장 저렴, 빠른 응답 |

## 2단계: AWS 인프라 배포

```bash
cd infra/

# 가상환경 생성
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# CDK 부트스트랩 (최초 1회)
cdk bootstrap aws://ACCOUNT_ID/REGION

# 스택 배포
cdk deploy PaperHubStorage      # DynamoDB + S3
cdk deploy PaperHubSageMaker    # SageMaker 역할
cdk deploy PaperHubPipeline     # Lambda + Step Functions + EventBridge
cdk deploy PaperHubApi          # API Gateway + CloudFront

# 또는 한번에
cdk deploy --all --require-approval never
```

## 3단계: SES 이메일 설정

```bash
# 발신 이메일 인증 (sandbox 모드)
aws ses verify-email-identity --email-address alert@paperhub.io

# 프로덕션: SES sandbox 해제 요청
# https://console.aws.amazon.com/ses/home#/account
```

## 4단계: 프론트엔드 배포

```bash
cd frontend/

# API URL 설정 (.env)
echo "REACT_APP_API_URL=https://xxxx.execute-api.ap-northeast-2.amazonaws.com/prod" > .env

npm install
npm run build

# S3 업로드
aws s3 sync build/ s3://paperhub-frontend-ACCOUNT_ID/ --delete

# CloudFront 캐시 무효화
aws cloudfront create-invalidation \
    --distribution-id DIST_ID \
    --paths "/*"
```

## 5단계: 테스트

```bash
# 논문 검색 API 테스트
curl -X POST https://API_URL/papers \
  -H "Content-Type: application/json" \
  -d '{"keyword": "CRISPR", "max_results": 5}'

# 알림 등록 테스트
curl -X POST https://API_URL/alerts \
  -H "Content-Type: application/json" \
  -d '{
    "keyword": "CRISPR",
    "email": "your@email.com",
    "frequency": "daily"
  }'

# Step Functions 수동 실행
aws stepfunctions start-execution \
  --state-machine-arn STATE_MACHINE_ARN \
  --input '{"pmid":"test123","title":"Test","abstract":"Test abstract","alert_keyword":"CRISPR"}'
```

## SageMaker 비용 최적화 팁

1. **Serverless Inference**: 트래픽이 적을 때
   ```python
   predictor = model.deploy(
       serverless_inference_config=ServerlessInferenceConfig(
           memory_size_in_mb=6144,
           max_concurrency=5,
       )
   )
   ```

2. **Auto Scaling**: 트래픽에 따라 인스턴스 수 조정
   ```python
   client.register_scalable_target(
       ServiceNamespace="sagemaker",
       ResourceId=f"endpoint/{ENDPOINT_NAME}/variant/AllTraffic",
       ScalableDimension="sagemaker:variant:DesiredInstanceCount",
       MinCapacity=0, MaxCapacity=3,
   )
   ```

3. **스케줄 기반 중단**: 야간/주말 엔드포인트 중지
   ```bash
   # 퇴근 시 (비용 절약)
   python deploy_endpoint.py --delete
   # 출근 시
   python deploy_endpoint.py
   ```

## 트러블슈팅

| 문제 | 해결 |
|------|------|
| SageMaker 배포 실패 | 인스턴스 할당량 확인: Service Quotas → SageMaker |
| PDF 다운로드 실패 | Unpaywall API 응답 확인, PMC 오픈액세스 여부 확인 |
| SES 메일 미발송 | Sandbox 모드에서는 인증된 이메일만 수신 가능 |
| Lambda 타임아웃 | SageMaker 콜드스타트 시 최대 2분 소요, 타임아웃 5분 권장 |
| Step Functions 실패 | CloudWatch Logs에서 각 Lambda 로그 확인 |
