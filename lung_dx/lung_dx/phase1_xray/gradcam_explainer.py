"""GradCAM 히트맵 생성 — X-ray 판독 근거 시각화.

모델이 어떤 영역을 보고 특정 label을 예측했는지 시각화한다.
임상의에게 AI 판단의 근거를 설명하는 데 사용.

[Selvaraju et al. Grad-CAM: Visual Explanations from Deep Networks
 via Gradient-based Localization. ICCV 2017]
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import torch
from PIL import Image

logger = logging.getLogger(__name__)


class GradCAMExplainer:
    """DenseNet121 기반 GradCAM 히트맵 생성."""

    def __init__(self, model: torch.nn.Module, device: str = "cpu"):
        """
        Args:
            model: CheXNet DenseNet121 모델
            device: 추론 디바이스
        """
        self._model = model
        self._device = torch.device(device)

        # DenseNet121의 마지막 conv block (features.denseblock4)
        self._target_layer = self._get_target_layer()
        self._gradients = None
        self._activations = None

        # Hook 등록
        self._target_layer.register_forward_hook(self._forward_hook)
        self._target_layer.register_full_backward_hook(self._backward_hook)

    def _get_target_layer(self):
        """DenseNet121의 마지막 dense block 반환."""
        return self._model.features.denseblock4

    def _forward_hook(self, module, input, output):
        self._activations = output.detach()

    def _backward_hook(self, module, grad_input, grad_output):
        self._gradients = grad_output[0].detach()

    def generate(
        self,
        image_tensor: torch.Tensor,
        target_label_index: int,
    ) -> np.ndarray:
        """특정 label에 대한 GradCAM 히트맵 생성.

        Args:
            image_tensor: (1, 3, 224, 224) 전처리된 텐서
            target_label_index: CheXpert label 인덱스 (0-13)

        Returns:
            (224, 224) float32 히트맵 (0.0~1.0)
        """
        image_tensor = image_tensor.to(self._device)
        image_tensor.requires_grad_(True)

        # Forward
        self._model.eval()
        output = self._model(image_tensor)  # (1, 14)

        # Backward — target label에 대해
        self._model.zero_grad()
        target_score = output[0, target_label_index]
        target_score.backward()

        if self._gradients is None or self._activations is None:
            logger.warning("GradCAM: gradient/activation 없음")
            return np.zeros((224, 224), dtype=np.float32)

        # GAP over spatial dims → channel weights
        weights = self._gradients.mean(dim=[2, 3], keepdim=True)  # (1, C, 1, 1)

        # Weighted combination
        cam = (weights * self._activations).sum(dim=1, keepdim=True)  # (1, 1, H, W)
        cam = torch.relu(cam)
        cam = cam.squeeze().cpu().numpy()

        # Resize to 224×224 and normalize
        cam = np.maximum(cam, 0)
        if cam.max() > 0:
            cam = cam / cam.max()

        # Bilinear resize
        cam_pil = Image.fromarray((cam * 255).astype(np.uint8))
        cam_pil = cam_pil.resize((224, 224), Image.BILINEAR)
        cam = np.array(cam_pil).astype(np.float32) / 255.0

        return cam

    def generate_overlay(
        self,
        original_image_path: str | Path,
        cam: np.ndarray,
        alpha: float = 0.4,
    ) -> Image.Image:
        """원본 이미지 위에 GradCAM 히트맵 오버레이.

        Args:
            original_image_path: 원본 X-ray 이미지 경로
            cam: (224, 224) 히트맵
            alpha: 히트맵 투명도

        Returns:
            오버레이된 PIL Image.
        """
        import matplotlib.cm as cm

        # 원본 이미지 로드 및 리사이즈
        orig = Image.open(original_image_path).convert("RGB")
        orig = orig.resize((224, 224))
        orig_arr = np.array(orig).astype(np.float32) / 255.0

        # Jet colormap 적용
        heatmap = cm.jet(cam)[:, :, :3]  # (224, 224, 3)

        # 오버레이
        overlay = (1 - alpha) * orig_arr + alpha * heatmap
        overlay = np.clip(overlay * 255, 0, 255).astype(np.uint8)

        return Image.fromarray(overlay)
