#!/bin/bash
# PaperHub — 리소스 정리 스크립트
# 비용 절약을 위해 SageMaker 엔드포인트만 삭제하거나, 전체 인프라를 삭제합니다.
# 사용법: ./scripts/teardown.sh [--sagemaker|--all]

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

MODE="${1:---sagemaker}"

case $MODE in
    --sagemaker)
        echo -e "${YELLOW}SageMaker 엔드포인트만 삭제합니다 (비용 절약)${NC}"
        echo ""
        
        STATUS=$(aws sagemaker describe-endpoint \
            --endpoint-name paperhub-summarizer \
            --query 'EndpointStatus' \
            --output text 2>/dev/null || echo "NOT_FOUND")
        
        if [ "$STATUS" = "NOT_FOUND" ]; then
            echo -e "${GREEN}엔드포인트가 이미 없습니다.${NC}"
        else
            echo "현재 상태: $STATUS"
            echo -n "삭제하시겠습니까? (y/N): "
            read -r confirm
            if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
                aws sagemaker delete-endpoint --endpoint-name paperhub-summarizer
                echo -e "${GREEN}엔드포인트 삭제 요청 완료${NC}"
                echo -e "${GREEN}월 ~\$1,400 절약됩니다${NC}"
            fi
        fi
        ;;
    
    --all)
        echo -e "${RED}══════════════════════════════════════════${NC}"
        echo -e "${RED}  경고: 모든 PaperHub 리소스를 삭제합니다!${NC}"
        echo -e "${RED}  DynamoDB 데이터가 유실될 수 있습니다.${NC}"
        echo -e "${RED}══════════════════════════════════════════${NC}"
        echo ""
        echo -n "정말 삭제하시겠습니까? 'DELETE'를 입력하세요: "
        read -r confirm
        
        if [ "$confirm" != "DELETE" ]; then
            echo "취소되었습니다."
            exit 0
        fi
        
        # SageMaker 엔드포인트 먼저
        aws sagemaker delete-endpoint --endpoint-name paperhub-summarizer 2>/dev/null || true
        echo -e "${GREEN}SageMaker 엔드포인트 삭제${NC}"
        
        # CDK 스택 역순 삭제
        cd infra/
        source .venv/bin/activate 2>/dev/null || true
        
        for stack in PaperHubApi PaperHubPipeline PaperHubSageMaker PaperHubStorage; do
            echo "Destroying $stack..."
            cdk destroy $stack --force 2>&1 | tail -2
            echo -e "${GREEN}$stack 삭제 완료${NC}"
        done
        
        echo ""
        echo -e "${GREEN}모든 PaperHub 리소스가 삭제되었습니다.${NC}"
        ;;
    
    *)
        echo "사용법: $0 [--sagemaker|--all]"
        echo "  --sagemaker  SageMaker 엔드포인트만 삭제 (비용 절약)"
        echo "  --all        전체 인프라 삭제 (주의!)"
        exit 1
        ;;
esac
