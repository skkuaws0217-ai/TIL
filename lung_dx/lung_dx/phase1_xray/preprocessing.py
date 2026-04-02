"""X-ray 이미지 전처리 파이프라인.

CheXNet(DenseNet121)에 입력하기 위한 표준 전처리:
  1. 그레이스케일 → RGB 3채널 복제
  2. 224×224 리사이즈
  3. [0,1] 정규화
  4. ImageNet 평균/표준편차 정규화

[Rajpurkar et al. CheXNet: Radiologist-Level Pneumonia Detection
 on Chest X-Rays with Deep Learning. arXiv:1711.05225 (2017)]
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
import torch
from torchvision import transforms


# CheXNet/ImageNet 표준 전처리 파라미터
IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]
INPUT_SIZE = 224


def get_transform() -> transforms.Compose:
    """CheXNet 추론용 torchvision 전처리 파이프라인."""
    return transforms.Compose([
        transforms.Resize((INPUT_SIZE, INPUT_SIZE)),
        transforms.ToTensor(),              # [0,255] → [0,1], HWC → CHW
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ])


def load_and_preprocess(image_path: str | Path) -> torch.Tensor:
    """이미지 파일 → 모델 입력 텐서 (1, 3, 224, 224).

    DICOM, PNG, JPG 등 PIL이 읽을 수 있는 모든 포맷 지원.
    그레이스케일 이미지는 자동으로 RGB 3채널로 변환.
    """
    img = Image.open(image_path)

    # 그레이스케일(L) → RGB 3채널 복제
    if img.mode != "RGB":
        img = img.convert("RGB")

    tensor = get_transform()(img)       # (3, 224, 224)
    return tensor.unsqueeze(0)          # (1, 3, 224, 224)


def preprocess_numpy(image_array: np.ndarray) -> torch.Tensor:
    """numpy 배열 → 모델 입력 텐서.

    Args:
        image_array: (H, W) 그레이스케일 또는 (H, W, 3) RGB
    """
    if image_array.ndim == 2:
        # 그레이스케일 → RGB
        image_array = np.stack([image_array] * 3, axis=-1)

    img = Image.fromarray(image_array.astype(np.uint8))
    if img.mode != "RGB":
        img = img.convert("RGB")

    tensor = get_transform()(img)
    return tensor.unsqueeze(0)
