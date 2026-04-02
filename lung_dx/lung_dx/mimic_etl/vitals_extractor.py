"""Vitals/Respiratory/Hemodynamic 환자 측정값 추출기.

chartevents.csv에서 환자의 VRH 측정값을 추출한다.

추출 대상 ItemID:
  vitals_respiratory_hemodynamic_reference_range_v1.yaml에 정의된 37개.
  Core Vital Signs(6) + Respiratory(14) + Hemodynamic(5) +
  Ventilator/Categorical(6) + Lung Sounds(4) + Blood Pressure(2)

항목 정의·reference range·thresholds·scoring_systems·
disease_associations는 모두 위 YAML에 완비되어 있다.
d_items.csv 등 MIMIC lookup 테이블은 사용하지 않는다.

출력: respiratory_vitals.parquet (환자 측정값만 포함)
"""

from __future__ import annotations

from pathlib import Path

from ..config import paths
from ..knowledge.vitals_reference import VitalsRespiratoryHemodynamicManager
from .chunked_reader import ChunkedCSVReader


# chartevents.csv에서 추출할 컬럼
# (환자 측정값 — 숫자값 + 텍스트값 모두 포함)
CHART_USECOLS = [
    "subject_id", "hadm_id", "stay_id", "charttime",
    "itemid", "value", "valuenum", "valueuom",
]


class VitalsExtractor:
    """chartevents.csv → respiratory_vitals.parquet 추출.

    ItemID 출처: vitals_respiratory_hemodynamic_reference_range_v1.yaml (유일한 기준)
    """

    def __init__(
        self,
        vrh_ref: VitalsRespiratoryHemodynamicManager | None = None,
        chunk_size: int = 500_000,
    ):
        self._vrh_ref = vrh_ref or VitalsRespiratoryHemodynamicManager()
        self._vrh_ref._ensure_loaded()
        self._reader = ChunkedCSVReader(chunk_size=chunk_size)

    def extract(
        self,
        csv_path: str | Path | None = None,
        output_path: str | Path | None = None,
        subject_ids: set[int] | None = None,
    ) -> Path:
        """chartevents.csv에서 YAML 정의 VRH 항목의 환자 측정값 추출.

        Args:
            csv_path: chartevents.csv 경로 (기본: paths.CHARTEVENTS_CSV)
            output_path: 출력 parquet 경로
            subject_ids: 특정 환자만 추출 (None이면 전체)

        Returns:
            출력 parquet 경로.
        """
        csv_path = csv_path or paths.CHARTEVENTS_CSV
        output_path = output_path or (paths.PARQUET_DIR / "respiratory_vitals.parquet")

        # vitals_respiratory_hemodynamic_reference_range_v1.yaml에서 ItemID 추출
        target_itemids = set(self._vrh_ref.get_all_itemids())

        return self._reader.extract_by_itemids(
            csv_path=csv_path,
            target_itemids=target_itemids,
            output_path=output_path,
            usecols=CHART_USECOLS,
            subject_ids=subject_ids,
        )
