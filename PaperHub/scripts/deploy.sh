#!/bin/bash
# PaperHub — 배포 스크립트 (Bedrock 모드)
# 사용법: ./scripts/deploy.sh [--all|--storage|--pipeline|--api|--frontend]

set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-2}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "UNKNOWN")

echo "╔══════════════════════════════════════════╗"
echo "║     PaperHub 배포 스크립트               ║"
echo "║     Region: $REGION                      ║"
echo "║     Account: $ACCOUNT_ID                 ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# 색상 정의
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_ok()   { echo -e "${GREEN}✅ $1${NC}"; }
log_err()  { echo -e "${RED}❌ $1${NC}"; }
log_warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }

# ─── 사전 요구사항 확인 ───
check_prerequisites() {
    echo "📋 사전 요구사항 확인..."

    if ! command -v aws &> /dev/null; then
        log_err "AWS CLI가 설치되지 않았습니다"
        exit 1
    fi
    log_ok "AWS CLI: $(aws --version | head -c 30)"

    if [ "$ACCOUNT_ID" = "UNKNOWN" ]; then
        log_err "AWS 자격증명이 설정되지 않았습니다. 'aws configure' 실행 필요"
        exit 1
    fi
    log_ok "AWS Account: $ACCOUNT_ID"

    if ! command -v cdk &> /dev/null; then
        log_warn "CDK CLI 미설치 — 설치 중..."
        npm install -g aws-cdk
    fi
    log_ok "CDK: $(cdk --version | head -c 20)"

    if ! command -v python3 &> /dev/null; then
        log_err "Python 3 필요"
        exit 1
    fi
    log_ok "Python: $(python3 --version)"

    echo ""
}

# ─── CDK Bootstrap ───
bootstrap() {
    echo "🔧 CDK Bootstrap 확인..."
    cdk bootstrap aws://$ACCOUNT_ID/$REGION 2>/dev/null || true
    log_ok "CDK Bootstrap 완료"
    echo ""
}

# ─── CDK 의존성 설치 ───
install_deps() {
    echo "📦 의존성 설치..."
    cd infra/
    if [ ! -d ".venv" ]; then
        python3 -m venv .venv
    fi
    source .venv/bin/activate
    pip install -q -r requirements.txt
    cd ..
    log_ok "CDK 의존성 설치 완료"
    echo ""
}

# ─── 스택 배포 함수 ───
deploy_stack() {
    local stack_name=$1
    echo "🚀 $stack_name 배포 중..."
    cd infra/
    source .venv/bin/activate
    cdk deploy $stack_name --require-approval never 2>&1 | tail -5
    cd ..
    log_ok "$stack_name 배포 완료"
    echo ""
}

# ─── SES 이메일 인증 ───
setup_ses() {
    echo "📧 SES 이메일 인증..."
    local email="${SENDER_EMAIL:-alert@paperhub.io}"
    aws ses verify-email-identity \
        --email-address "$email" \
        --region $REGION 2>/dev/null || true
    log_ok "SES 인증 이메일 전송: $email"
    log_warn "메일함에서 인증 링크를 클릭해주세요"
    echo ""
}

# ─── 프론트엔드 배포 ───
deploy_frontend() {
    echo "🌐 프론트엔드 배포..."
    local bucket="paperhub-frontend-$ACCOUNT_ID"

    if [ -d "frontend/build" ]; then
        aws s3 sync frontend/build/ s3://$bucket/ --delete
        log_ok "프론트엔드 S3 업로드 완료"
    else
        log_warn "frontend/build 디렉토리 없음. 'cd frontend && npm run build' 먼저 실행하세요"
    fi
    echo ""
}

# ─── 배포 후 검증 ───
verify() {
    echo "🔍 배포 검증..."

    # CloudFormation 스택 상태
    for stack in PaperHubStorage PaperHubPipeline PaperHubApi; do
        local status=$(aws cloudformation describe-stacks \
            --stack-name $stack \
            --query 'Stacks[0].StackStatus' \
            --output text 2>/dev/null || echo "NOT_FOUND")
        if [ "$status" = "CREATE_COMPLETE" ] || [ "$status" = "UPDATE_COMPLETE" ]; then
            log_ok "$stack: $status"
        elif [ "$status" = "NOT_FOUND" ]; then
            log_warn "$stack: 미배포"
        else
            log_err "$stack: $status"
        fi
    done

    # API Gateway URL
    local api_url=$(aws cloudformation describe-stacks \
        --stack-name PaperHubApi \
        --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' \
        --output text 2>/dev/null || echo "")
    if [ -n "$api_url" ]; then
        log_ok "API URL: $api_url"
    fi

    echo ""
    echo "╔══════════════════════════════════════════╗"
    echo "║     PaperHub 배포 완료!                  ║"
    echo "╚══════════════════════════════════════════╝"
}

# ─── 메인 실행 ───
MODE="${1:---all}"

check_prerequisites

case $MODE in
    --all)
        bootstrap
        install_deps
        deploy_stack "PaperHubStorage"
        deploy_stack "PaperHubPipeline"
        deploy_stack "PaperHubApi"
        setup_ses
        verify
        ;;
    --storage)
        install_deps
        deploy_stack "PaperHubStorage"
        ;;
    --pipeline)
        install_deps
        deploy_stack "PaperHubPipeline"
        ;;
    --api)
        install_deps
        deploy_stack "PaperHubApi"
        ;;
    --frontend)
        deploy_frontend
        ;;
    --verify)
        verify
        ;;
    *)
        echo "사용법: $0 [--all|--storage|--pipeline|--api|--frontend|--verify]"
        exit 1
        ;;
esac
