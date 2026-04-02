"""Lab 환자 측정값 추출기.

labevents.csv에서 환자의 Lab 측정값을 추출한다.

추출 대상 ItemID:
  lab_reference_ranges_v3.yaml에 정의된 MIMIC-IV ItemID 53개.
  (전체 89개 중 EXT_A~EXT_AJ 36개는 외부검사로 MIMIC에 없으므로 제외)

항목 정의·reference range·medical_terms·disease_associations는
모두 lab_reference_ranges_v3.yaml에 완비되어 있다.
d_labitems.csv 등 MIMIC lookup 테이블은 사용하지 않는다.

출력: lung_labs.parquet (환자 측정값만 포함)
"""

from __future__ import annotations

from pathlib import Path

from ..config import paths
from ..knowledge.lab_reference import LabReferenceManager
from .chunked_reader import ChunkedCSVReader


# labevents.csv에서 추출할 컬럼
# (환자 측정값 + 참고범위 + 이상 플래그)
LAB_USECOLS = [
    "labevent_id", "subject_id", "hadm_id", "itemid",
    "charttime", "value", "valuenum", "valueuom",
    "ref_range_lower", "ref_range_upper", "flag",
]


class LabExtractor:
    """labevents.csv → lung_labs.parquet 추출.

    ItemID 출처: lab_reference_ranges_v3.yaml (유일한 기준)
    """

    def __init__(
        self,
        lab_ref: LabReferenceManager | None = None,
        chunk_size: int = 500_000,
    ):
        self._lab_ref = lab_ref or LabReferenceManager()
        self._lab_ref._ensure_loaded()
        self._reader = ChunkedCSVReader(chunk_size=chunk_size)

    def extract(
        self,
        csv_path: str | Path | None = None,
        output_path: str | Path | None = None,
        subject_ids: set[int] | None = None,
    ) -> Path:
        """labevents.csv에서 YAML 정의 Lab 항목의 환자 측정값 추출.

        Args:
            csv_path: labevents.csv 경로 (기본: paths.LABEVENTS_CSV)
            output_path: 출력 parquet 경로
            subject_ids: 특정 환자만 추출 (None이면 전체)

        Returns:
            출력 parquet 경로.
        """
        csv_path = csv_path or paths.LABEVENTS_CSV
        output_path = output_path or (paths.PARQUET_DIR / "lung_labs.parquet")

        # lab_reference_ranges_v3.yaml에서 MIMIC-IV ItemID만 추출
        target_itemids = set(self._lab_ref.get_mimic_itemids())

        return self._reader.extract_by_itemids(
            csv_path=csv_path,
            target_itemids=target_itemids,
            output_path=output_path,
            usecols=LAB_USECOLS,
            subject_ids=subject_ids,
        )
