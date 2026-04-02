"""CheXNet 로컬 추론 — DenseNet121 on Apple MPS / CPU.

CheXNet 아키텍처:
  - Backbone: DenseNet-121 (Huang et al. CVPR 2017)
  - Classifier: Linear(1024, 14) + Sigmoid (multi-label)
  - 14개 CheXpert labels 동시 예측 (multi-label classification)

사전학습 가중치:
  - CheXpert 또는 ChestX-ray14 데이터셋에서 학습된 가중치
  - 가중치 파일이 없으면 ImageNet pretrained DenseNet121을 사용하고
    classifier head만 14-class로 교체 (fine-tuning 전 상태)

[Rajpurkar et al. CheXNet. arXiv:1711.05225 (2017);
 Irvin et al. CheXpert. AAAI 2019]
"""

from __future__ import annotations

import logging
from pathlib import Path

import torch
import torch.nn as nn
import torchvision.models as models

from ..config import paths
from ..domain.findings import XrayPrediction
from .model_interface import XrayModelInterface

logger = logging.getLogger(__name__)


class CheXNetLocal(XrayModelInterface):
    """DenseNet121 기반 CheXNet — 로컬 추론."""

    def __init__(
        self,
        weights_path: str | None = None,
        device: str | None = None,
    ):
        """
        Args:
            weights_path: CheXNet 가중치 파일 경로 (.pth).
                None이면 ImageNet pretrained + random classifier head.
            device: "mps" | "cpu" | "cuda". None이면 자동 선택.
        """
        if device is None:
            if torch.backends.mps.is_available():
                device = "mps"
            elif torch.cuda.is_available():
                device = "cuda"
            else:
                device = "cpu"
        self.device = torch.device(device)

        self._model = self._build_model(weights_path)
        logger.info("CheXNet 로드 완료 (device=%s, weights=%s)", device,
                     "custom" if weights_path else "ImageNet pretrained")

    def _build_model(self, weights_path: str | None) -> nn.Module:
        """DenseNet121 + 14-class sigmoid head 구축."""
        # DenseNet121 backbone
        model = models.densenet121(weights=models.DenseNet121_Weights.DEFAULT)

        # Classifier head 교체: 1024 → 14 (CheXpert labels)
        num_features = model.classifier.in_features
        model.classifier = nn.Sequential(
            nn.Linear(num_features, 14),
            nn.Sigmoid(),
        )

        # 커스텀 가중치 로드
        if weights_path and Path(weights_path).exists():
            state_dict = torch.load(weights_path, map_location=self.device)
            # 다양한 가중치 포맷 호환
            if "state_dict" in state_dict:
                state_dict = state_dict["state_dict"]
            # "module." 접두어 제거 (DataParallel 학습 시)
            state_dict = {
                k.replace("module.", ""): v for k, v in state_dict.items()
            }
            model.load_state_dict(state_dict, strict=False)
            logger.info("커스텀 가중치 로드 완료: %s", weights_path)

        model = model.to(self.device)
        model.eval()
        return model

    def predict(self, image_tensor: torch.Tensor) -> list[XrayPrediction]:
        """전처리된 이미지 → 14개 CheXpert label 확률."""
        image_tensor = image_tensor.to(self.device)

        with torch.no_grad():
            output = self._model(image_tensor)  # (1, 14)

        probs = output.cpu().numpy().flatten()

        return [
            XrayPrediction(label=label, probability=float(prob))
            for label, prob in zip(paths.CHEXPERT_LABELS, probs)
        ]

    def get_model(self) -> nn.Module:
        return self._model
