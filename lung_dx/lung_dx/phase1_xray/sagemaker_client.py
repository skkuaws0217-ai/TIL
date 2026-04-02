"""SageMaker 엔드포인트 X-ray 추론 클라이언트.

로컬 CheXNet과 동일한 XrayModelInterface를 구현하여
settings.py의 xray_backend="sagemaker"로 전환만 하면 사용 가능.

AWS 자격증명 설정 필요: aws configure 또는 환경변수.
"""

from __future__ import annotations

import json
import logging
from io import BytesIO

import numpy as np
import torch

from ..config import paths
from ..domain.findings import XrayPrediction
from .model_interface import XrayModelInterface

logger = logging.getLogger(__name__)


class SageMakerXrayModel(XrayModelInterface):
    """AWS SageMaker 엔드포인트를 통한 X-ray 추론."""

    def __init__(self, endpoint_name: str, region: str = "ap-northeast-2"):
        self.endpoint_name = endpoint_name
        self.region = region
        self._client = None

    def _get_client(self):
        if self._client is None:
            import boto3
            self._client = boto3.client(
                "sagemaker-runtime", region_name=self.region
            )
        return self._client

    def predict(self, image_tensor: torch.Tensor) -> list[XrayPrediction]:
        """SageMaker 엔드포인트에 이미지 전송 → 14개 label 확률."""
        # 텐서 → numpy → bytes
        arr = image_tensor.cpu().numpy()
        buf = BytesIO()
        np.save(buf, arr)
        payload = buf.getvalue()

        client = self._get_client()
        response = client.invoke_endpoint(
            EndpointName=self.endpoint_name,
            ContentType="application/x-npy",
            Body=payload,
        )

        result = json.loads(response["Body"].read().decode("utf-8"))
        probs = result.get("predictions", result.get("probabilities", []))

        return [
            XrayPrediction(label=label, probability=float(prob))
            for label, prob in zip(paths.CHEXPERT_LABELS, probs)
        ]

    def get_model(self) -> torch.nn.Module:
        raise NotImplementedError(
            "SageMaker 엔드포인트에서는 내부 모델 객체에 접근할 수 없음. "
            "GradCAM은 로컬 모델에서만 가능."
        )
