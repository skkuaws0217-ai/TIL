# PaperHub — Kiro CLI 배포 가이드

## 개요

Kiro CLI를 사용하여 PaperHub 전체 AWS 인프라를 자연어 명령으로 배포합니다.
커스텀 에이전트, MCP 서버, 스킬이 미리 구성되어 있어 `kiro-cli`만 실행하면 됩니다.

## 1단계: 사전 준비

### Kiro CLI 설치
```bash
curl -fsSL https://cli.kiro.dev/install | bash
```

### 로그인
```bash
kiro-cli  # GitHub, Google, 또는 AWS Builder ID로 로그인
```

### AWS CLI 설정
```bash
aws configure
# Region: ap-northeast-2
# Output format: json
```

### 필수 도구 설치
```bash
# uv (MCP 서버 실행용)
curl -LsSf https://astral.sh/uv/install.sh | sh

# CDK CLI
npm install -g aws-cdk

# Python 3.12+
# (OS에 맞게 설치)
```

## 2단계: 프로젝트 열기

```bash
cd paperhub-kiro/
kiro-cli
```

Kiro CLI가 시작되면 자동으로:
- `.kiro/settings/mcp.json` — AWS CDK, Documentation MCP 서버 로드
- `.kiro/steering/` — 프로젝트 컨텍스트 및 배포 워크플로우 로드
- `AGENTS.md` — 프로젝트 개요 로드

## 3단계: 에이전트 선택 및 배포

### 전체 인프라 배포
```
> /agent swap paperhub-deployer
> PaperHub 전체 인프라를 배포해줘
```

에이전트가 자동으로:
1. AWS 자격증명 확인
2. CDK Bootstrap
3. 4개 스택 순서대로 배포 (Storage → SageMaker → Pipeline → API)
4. SES 이메일 인증
5. 배포 결과 검증

### SageMaker 엔드포인트만 배포
```
> /agent swap paperhub-sagemaker
> Llama 3 8B 모델로 SageMaker 엔드포인트 배포해줘
```

### 스크립트로 직접 배포
```
> 배포 스크립트 실행해줘
> ! ./scripts/deploy.sh --all
```

## 4단계: 대화형 배포 예시

### 예시 1: CDK 배포
```
사용자> CDK로 Storage 스택부터 배포해줘
Kiro> 먼저 AWS 자격증명을 확인합니다...
      ✅ Account: 123456789012, Region: ap-northeast-2
      CDK Bootstrap 상태를 확인하겠습니다...
      ✅ Bootstrap 완료
      PaperHubStorage 스택을 배포합니다...
      [deploy output]
      ✅ DynamoDB 테이블 2개, S3 버킷 2개 생성 완료
```

### 예시 2: SageMaker 배포
```
사용자> SageMaker 엔드포인트 상태 확인해줘
Kiro> paperhub-summarizer 엔드포인트를 확인합니다...
      ❌ 엔드포인트가 없습니다. 배포하시겠습니까?
      ⚠️ ml.g5.2xlarge 기준 월 ~$1,400 비용이 발생합니다.
      Bedrock 대안(월 ~$50-200)도 가능합니다.

사용자> Bedrock으로 전환해줘
Kiro> infra/app.py의 USE_BEDROCK 환경 변수를 "true"로 변경합니다...
      PaperHubPipeline 스택을 재배포합니다...
      ✅ Bedrock Claude 모드로 전환 완료
```

### 예시 3: 비용 확인
```
사용자> PaperHub 현재 비용 확인해줘
Kiro> [Cost Explorer MCP 사용]
      이번 달 PaperHub 관련 비용:
      - SageMaker: $0 (엔드포인트 미배포)
      - DynamoDB: $0.25
      - S3: $0.02
      - Lambda: $0.00 (프리티어)
      총: $0.27
```

## 프로젝트 구조

```
paperhub-kiro/
├── .kiro/
│   ├── agents/
│   │   ├── paperhub-deployer.json     ← 전체 인프라 배포 에이전트
│   │   └── paperhub-sagemaker.json    ← SageMaker 전문 에이전트
│   ├── settings/
│   │   └── mcp.json                   ← AWS MCP 서버 설정
│   ├── steering/
│   │   ├── project-context.md         ← 아키텍처 컨텍스트
│   │   └── deployment-workflow.md     ← 배포 절차
│   └── skills/
│       ├── cdk-deploy/SKILL.md        ← CDK 배포 스킬
│       └── sagemaker-deploy/SKILL.md  ← SageMaker 스킬
├── AGENTS.md                          ← 프로젝트 요약 (항상 로드)
├── scripts/
│   └── deploy.sh                      ← 원클릭 배포 스크립트
├── infra/                             ← CDK 코드
├── lambda/                            ← Lambda 핸들러
├── sagemaker/                         ← SageMaker 배포 스크립트
└── frontend/                          ← React 프론트엔드
```

## MCP 서버 구성

| MCP 서버 | 용도 |
|---------|------|
| `aws-mcp` | AWS API 직접 호출 (SageMaker, Lambda 등) |
| `awslabs.cdk-mcp-server` | CDK 문서 검색, 베스트 프랙티스, 코드 샘플 |
| `awslabs.aws-documentation-mcp-server` | AWS 공식 문서 검색 |
| `awslabs.cost-explorer-mcp-server` | 비용 분석 및 최적화 추천 |

## 커스텀 에이전트

### paperhub-deployer
전체 인프라 배포 전문. CDK 스택 배포, Lambda 업데이트, Step Functions 워크플로우 관리.
- 허용 서비스: S3, Lambda, CloudFormation, DynamoDB, SageMaker, StepFunctions 등 15개
- 스폰 시 자동으로 AWS 자격증명 확인
- 배포 순서 자동 관리

### paperhub-sagemaker
SageMaker 엔드포인트 관리 전문. 모델 배포, 추론 테스트, 비용 최적화.
- 허용 서비스: SageMaker, Bedrock, S3, IAM, ECR
- 엔드포인트 수명 주기 관리
- Bedrock 전환 지원

## 유용한 Kiro CLI 명령어

```bash
# 에이전트 목록 확인
> /agent list

# 에이전트 전환
> /agent swap paperhub-deployer

# MCP 서버 상태 확인
> kiro-cli mcp

# 컨텍스트 확인
> /context show

# 대화 저장/불러오기
> /save deployment-session.json
> /load deployment-session.json

# 사용량 확인
> /usage
```

## 트러블슈팅

| 문제 | 해결 |
|------|------|
| MCP 서버 로드 안 됨 | `uv` 설치 확인, `kiro-cli mcp` 실행 |
| CDK 배포 실패 | `cdk diff`로 변경 사항 확인, CloudFormation 이벤트 확인 |
| 에이전트 전환 안 됨 | `.kiro/agents/` 경로 및 JSON 문법 확인 |
| AWS 자격증명 오류 | `aws sts get-caller-identity` 실행, 프로필 확인 |
| SageMaker 할당량 | Service Quotas에서 g5 인스턴스 할당량 요청 |
