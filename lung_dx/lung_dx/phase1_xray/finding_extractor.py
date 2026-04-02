"""X-ray 모델 출력 → 영상학적 소견 + 질환 후보 추출.

CheXNet 14개 label 확률을 임상적 소견으로 변환하고,
Excel DB의 "영상 키워드 (AI 매칭)" 51개 키워드와 매칭하여
1차 질환 후보 목록을 생성한다.

CheXpert Label → AI 키워드 매핑:
  각 CheXpert label이 시사하는 영상 키워드를 fact 기반으로 매핑.
  [Irvin et al. CheXpert: A Large Chest Radiograph Dataset.
   AAAI 2019 — Table 1 label definitions]
"""

from __future__ import annotations

from ..config.paths import CHEXPERT_LABELS
from ..config.settings import Settings, get_settings
from ..domain.findings import XrayPrediction, RadiologyFinding, Phase1Result
from ..knowledge.disease_registry import DiseaseRegistry


# ═══════════════════════════════════════════════════════════════
# CheXpert Label → AI 영상 키워드 매핑
# ═══════════════════════════════════════════════════════════════
#
# 각 CheXpert label이 X-ray에서 시사하는 영상 키워드.
# Excel DB의 "영상 키워드 (AI 매칭)" 51개 키워드 체계와 일치시켰다.
#
# 매핑 근거:
#   Irvin et al. CheXpert (AAAI 2019) — label 정의
#   Hansell et al. Fleischner Society Glossary (Radiology 2008)
#   Webb et al. Fundamentals of Body CT, 4th Ed
# ═══════════════════════════════════════════════════════════════
CHEXPERT_TO_KEYWORDS: dict[str, list[str]] = {
    "Atelectasis": ["atelectasis", "volume loss", "opacity"],
    "Cardiomegaly": ["cardiomegaly"],
    "Consolidation": ["consolidation", "airspace disease", "air bronchogram"],
    "Edema": ["pulmonary edema", "interstitial", "septal thickening",
              "pleural effusion"],
    "Enlarged Cardiomediastinum": ["mediastinal widening", "lymphadenopathy"],
    "Fracture": ["fracture"],
    "Lung Lesion": ["nodule", "mass", "lesion"],
    "Lung Opacity": ["opacity", "infiltrate", "ground glass", "consolidation"],
    "No Finding": [],
    "Pleural Effusion": ["pleural effusion"],
    "Pleural Other": ["pleural thickening", "calcification"],
    "Pneumonia": ["consolidation", "infiltrate", "opacity",
                  "airspace disease", "air bronchogram"],
    "Pneumothorax": ["pneumothorax"],
    "Support Devices": [],
}


class FindingExtractor:
    """CheXNet 출력 → RadiologyFinding + 질환 후보."""

    def __init__(
        self,
        disease_registry: DiseaseRegistry,
        settings: Settings | None = None,
    ):
        self._registry = disease_registry
        self._settings = settings or get_settings()

    def extract(self, predictions: list[XrayPrediction]) -> Phase1Result:
        """CheXNet 14개 label 확률 → Phase1Result.

        1. 확률 threshold로 detected/possible 분류
        2. CheXpert label → AI 키워드 변환
        3. 키워드로 질환 DB 매칭 → 1차 후보 목록
        """
        detect_th = self._settings.xray_detection_threshold
        possible_th = self._settings.xray_possible_threshold

        detected: list[RadiologyFinding] = []
        possible: list[RadiologyFinding] = []
        all_keywords: set[str] = set()
        all_icd_codes: set[str] = set()

        for pred in predictions:
            if pred.label in ("No Finding", "Support Devices"):
                continue

            keywords = CHEXPERT_TO_KEYWORDS.get(pred.label, [])

            if pred.probability >= detect_th:
                finding = RadiologyFinding(
                    finding=pred.label,
                    present=True,
                    probability=pred.probability,
                    ai_keywords=keywords,
                )
                detected.append(finding)
                all_keywords.update(kw.lower() for kw in keywords)

            elif pred.probability >= possible_th:
                finding = RadiologyFinding(
                    finding=pred.label,
                    present=False,  # possible, not confirmed
                    probability=pred.probability,
                    ai_keywords=keywords,
                )
                possible.append(finding)
                all_keywords.update(kw.lower() for kw in keywords)

        # 키워드로 질환 DB 매칭
        matched_diseases = self._registry.search_by_keywords(list(all_keywords))
        for profile in matched_diseases:
            all_icd_codes.update(profile.icd10_codes)

        return Phase1Result(
            detected_findings=detected,
            possible_findings=possible,
            all_predictions=predictions,
            candidate_icd_codes=sorted(all_icd_codes),
            ai_keywords_matched=sorted(all_keywords),
        )
