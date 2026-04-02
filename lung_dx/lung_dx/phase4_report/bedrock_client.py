"""AWS Bedrock Claude 기반 임상소견서 생성.

Phase 1~3의 구조화된 결과를 Bedrock Claude에 전달하여
한국어 자연어 임상소견서를 생성한다.

settings.report_backend="bedrock"일 때 사용.
AWS 자격증명 필요: aws configure 또는 환경변수.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
당신은 호흡기내과 전문의 보조 AI입니다.
아래 제공된 구조화된 진단 데이터를 기반으로 한국어 임상소견서를 작성하세요.

작성 원칙:
1. 의학 표준 용어를 사용하되, 각 소견에 영문 표기를 병기
2. 사실(fact)에 기반하여 작성 — 추론은 "~시사함", "~가능성" 등으로 명확히 구분
3. 진단 순위에 대한 근거를 구체적으로 기술
4. 희귀질환 평가가 있으면 추가 검사 제안을 포함
5. 참고문헌이 있으면 인용 표기

소견서 섹션:
1. 환자 정보 (Patient Information)
2. 주소 및 현병력 (Chief Complaint & HPI)
3. 영상검사 소견 (Imaging Findings) — Phase 1
4. 검사실 소견 (Laboratory Findings)
5. Vitals/Respiratory/Hemodynamic 소견
6. 미생물학적 소견 (Microbiological Findings)
7. 임상 스코어링 (Clinical Scoring Systems)
8. 추정 진단 (Differential Diagnosis — ranked)
9. 희귀질환 평가 (Rare Disease Assessment) — Phase 3 (해당 시)
10. 추가 검사 권고 (Recommended Further Workup)
11. 종합 소견 (Summary & Impression)
"""


class BedrockReportClient:
    """Bedrock Claude로 임상소견서 생성."""

    def __init__(
        self,
        model_id: str = "anthropic.claude-sonnet-4-20250514",
        region: str = "us-east-1",
    ):
        self.model_id = model_id
        self.region = region
        self._client = None

    def _get_client(self):
        if self._client is None:
            import boto3
            self._client = boto3.client(
                "bedrock-runtime", region_name=self.region
            )
        return self._client

    def generate_report(
        self, report_data: dict[str, Any], language: str = "ko"
    ) -> str:
        """구조화된 데이터 → Bedrock Claude 임상소견서.

        Args:
            report_data: Phase 1~3 결과를 담은 딕셔너리
            language: "ko" (한국어) | "en" (영문)

        Returns:
            생성된 임상소견서 텍스트.
        """
        user_message = self._build_user_message(report_data, language)

        try:
            client = self._get_client()
            response = client.invoke_model(
                modelId=self.model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 4096,
                    "system": SYSTEM_PROMPT,
                    "messages": [
                        {"role": "user", "content": user_message}
                    ],
                }),
            )
            result = json.loads(response["body"].read())
            return result["content"][0]["text"]

        except Exception as e:
            logger.error("Bedrock 호출 실패: %s", e)
            return f"[Bedrock 오류] {e}\n\n원본 데이터:\n{json.dumps(report_data, ensure_ascii=False, indent=2)[:3000]}"

    @staticmethod
    def _build_user_message(data: dict, language: str) -> str:
        lang_instruction = "한국어로" if language == "ko" else "in English"
        return (
            f"다음 진단 데이터를 기반으로 {lang_instruction} 임상소견서를 작성하세요.\n\n"
            f"```json\n{json.dumps(data, ensure_ascii=False, indent=2, default=str)}\n```"
        )
