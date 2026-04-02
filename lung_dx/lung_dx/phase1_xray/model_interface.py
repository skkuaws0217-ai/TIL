"""X-ray 분류 모델 추상 인터페이스.

local CheXNet과 SageMaker 엔드포인트를 동일한 인터페이스로 교체 가능하게 한다.
settings.py의 xray_backend 값으로 전환: "local" | "sagemaker"
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
import torch

from ..domain.findings import XrayPrediction


class XrayModelInterface(ABC):
    """X-ray 분류 모델 추상 인터페이스."""

    @abstractmethod
    def predict(self, image_tensor: torch.Tensor) -> list[XrayPrediction]:
        """전처리된 이미지 텐서 → CheXpert 14개 label 확률.

        Args:
            image_tensor: (1, 3, 224, 224) 전처리된 텐서

        Returns:
            14개 XrayPrediction(label, probability) 목록.
        """

    @abstractmethod
    def get_model(self) -> torch.nn.Module:
        """GradCAM 등 후처리를 위해 내부 모델 객체 반환."""
