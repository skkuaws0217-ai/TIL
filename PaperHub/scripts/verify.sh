#!/bin/bash
# PaperHub — 배포 후 검증 스크립트
# Kiro CLI에서: ! ./scripts/verify.sh

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

REGION="${AWS_REGION:-ap-northeast-2}"
PASS=0
FAIL=0
WARN=0

check() {
    local desc="$1"
    local cmd="$2"
    local result
    result=$(eval "$cmd" 2>/dev/null || echo "FAIL")
    if [ "$result" = "FAIL" ] || [ -z "$result" ]; then
        echo -e "${RED}  FAIL${NC}  $desc"
        ((FAIL++))
    else
        echo -e "${GREEN}  PASS${NC}  $desc → $result"
        ((PASS++))
    fi
}

warn_check() {
    local desc="$1"
    local cmd="$2"
    local result
    result=$(eval "$cmd" 2>/dev/null || echo "WARN")
    if [ "$result" = "WARN" ] || [ -z "$result" ]; then
        echo -e "${YELLOW}  WARN${NC}  $desc"
        ((WARN++))
    else
        echo -e "${GREEN}  PASS${NC}  $desc → $result"
        ((PASS++))
    fi
}

echo ""
echo "═══════════════════════════════════════════"
echo "  PaperHub 배포 검증"
echo "═══════════════════════════════════════════"
echo ""

echo "1. AWS 자격증명"
check "Account ID" "aws sts get-caller-identity --query Account --output text"
echo ""

echo "2. CloudFormation 스택"
check "PaperHubStorage" "aws cloudformation describe-stacks --stack-name PaperHubStorage --query 'Stacks[0].StackStatus' --output text"
check "PaperHubSageMaker" "aws cloudformation describe-stacks --stack-name PaperHubSageMaker --query 'Stacks[0].StackStatus' --output text"
check "PaperHubPipeline" "aws cloudformation describe-stacks --stack-name PaperHubPipeline --query 'Stacks[0].StackStatus' --output text"
check "PaperHubApi" "aws cloudformation describe-stacks --stack-name PaperHubApi --query 'Stacks[0].StackStatus' --output text"
echo ""

echo "3. DynamoDB 테이블"
check "paperhub-papers" "aws dynamodb describe-table --table-name paperhub-papers --query 'Table.TableStatus' --output text"
check "paperhub-alerts" "aws dynamodb describe-table --table-name paperhub-alerts --query 'Table.TableStatus' --output text"
echo ""

echo "4. Lambda 함수"
check "paperhub-collector" "aws lambda get-function --function-name paperhub-collector --query 'Configuration.State' --output text"
check "paperhub-summarizer-fn" "aws lambda get-function --function-name paperhub-summarizer-fn --query 'Configuration.State' --output text"
check "paperhub-alert-sender" "aws lambda get-function --function-name paperhub-alert-sender --query 'Configuration.State' --output text"
echo ""

echo "5. Step Functions"
check "paperhub-summarize-pipeline" "aws stepfunctions describe-state-machine --state-machine-arn \$(aws stepfunctions list-state-machines --query 'stateMachines[?name==\`paperhub-summarize-pipeline\`].stateMachineArn' --output text) --query 'status' --output text"
echo ""

echo "6. SageMaker 엔드포인트 (선택)"
warn_check "paperhub-summarizer" "aws sagemaker describe-endpoint --endpoint-name paperhub-summarizer --query 'EndpointStatus' --output text"
echo ""

echo "7. API Gateway"
warn_check "API URL" "aws cloudformation describe-stacks --stack-name PaperHubApi --query 'Stacks[0].Outputs[?OutputKey==\`ApiUrl\`].OutputValue' --output text"
echo ""

echo "8. EventBridge 규칙"
check "daily-collection" "aws events describe-rule --name paperhub-daily-collection --query 'State' --output text"
check "weekly-collection" "aws events describe-rule --name paperhub-weekly-collection --query 'State' --output text"
echo ""

echo "═══════════════════════════════════════════"
echo -e "  결과: ${GREEN}PASS=$PASS${NC}  ${RED}FAIL=$FAIL${NC}  ${YELLOW}WARN=$WARN${NC}"
echo "═══════════════════════════════════════════"

if [ $FAIL -gt 0 ]; then
    echo -e "${RED}  일부 검증 실패. 위 항목을 확인하세요.${NC}"
    exit 1
else
    echo -e "${GREEN}  모든 필수 항목 통과!${NC}"
fi
