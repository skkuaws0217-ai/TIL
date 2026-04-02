"""메모리 안전 대용량 CSV 청크 리더.

MIMIC-IV의 chartevents.csv(42GB), labevents.csv(18GB)에서
환자 측정값(patient measurements)을 추출한다.

추출 대상 ItemID는 다음 2개 YAML 파일에서 동적 로드한다:
  - lab_reference_ranges_v3.yaml       → 89개 (MIMIC 53개 + 외부 36개)
  - vitals_respiratory_hemodynamic_reference_range_v1.yaml → 37개

이 2개 YAML이 항목 정의 및 reference range의 유일한 기준 출처이다.
d_items.csv, d_labitems.csv 등 MIMIC lookup 테이블은 사용하지 않는다.
CSV는 순수하게 환자 측정값 데이터 소스로만 활용한다.

청크 처리:
  500K row 단위로 읽어 target itemid만 필터 → parquet 저장.
  메모리 예산: 청크당 ~2GB (500K rows × ~20 cols × ~200 bytes)
"""

from __future__ import annotations

import gc
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


class ChunkedCSVReader:
    """대용량 CSV에서 YAML 정의 ItemID에 해당하는 환자 측정값만 추출."""

    def __init__(self, chunk_size: int = 500_000):
        self.chunk_size = chunk_size

    def extract_by_itemids(
        self,
        csv_path: str | Path,
        target_itemids: set[int],
        output_path: str | Path,
        usecols: list[str] | None = None,
        subject_ids: set[int] | None = None,
        dtype_overrides: dict | None = None,
    ) -> Path:
        """YAML 정의 ItemID에 해당하는 행만 추출하여 parquet 저장.

        Args:
            csv_path: MIMIC-IV 원본 CSV 경로 (chartevents 또는 labevents)
            target_itemids: YAML에서 로드한 추출 대상 ItemID 집합
            output_path: 출력 parquet 경로
            usecols: 읽을 컬럼 (None이면 전체)
            subject_ids: 환자 필터 (None이면 전체)
            dtype_overrides: 컬럼별 dtype 지정

        Returns:
            출력 parquet 파일 경로.
        """
        csv_path = Path(csv_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info("ETL 시작: %s → %s", csv_path.name, output_path.name)
        logger.info("  YAML 기반 target itemids: %d개, chunk_size: %d",
                     len(target_itemids), self.chunk_size)

        results: list[pd.DataFrame] = []
        total_rows = 0
        matched_rows = 0
        chunk_count = 0

        reader = pd.read_csv(
            csv_path,
            chunksize=self.chunk_size,
            usecols=usecols,
            dtype=dtype_overrides,
            low_memory=False,
        )

        for chunk in reader:
            chunk_count += 1
            total_rows += len(chunk)

            # YAML 정의 ItemID로 필터
            filtered = chunk[chunk["itemid"].isin(target_itemids)]

            # subject_id 필터 (선택)
            if subject_ids is not None and "subject_id" in filtered.columns:
                filtered = filtered[filtered["subject_id"].isin(subject_ids)]

            if not filtered.empty:
                results.append(filtered)
                matched_rows += len(filtered)

            # 주기적 flush — 메모리 누적 방지 (20 청크마다)
            if len(results) >= 20:
                self._flush_to_parquet(results, output_path)
                results = []
                gc.collect()

            # 진행 로그 (50 청크마다)
            if chunk_count % 50 == 0:
                logger.info(
                    "  chunk %d: %dM rows 처리, %d rows 매칭",
                    chunk_count, total_rows // 1_000_000, matched_rows,
                )

        # 마지막 flush
        if results:
            self._flush_to_parquet(results, output_path)

        logger.info(
            "ETL 완료: %dM rows 중 %d rows 추출 (%.2f%%) → %s",
            total_rows // 1_000_000, matched_rows,
            (matched_rows / total_rows * 100) if total_rows else 0,
            output_path.name,
        )
        return output_path

    def _flush_to_parquet(
        self, dfs: list[pd.DataFrame], output_path: Path
    ) -> None:
        """DataFrame 목록을 parquet에 추가 저장."""
        combined = pd.concat(dfs, ignore_index=True)

        if output_path.exists():
            existing = pd.read_parquet(output_path)
            combined = pd.concat([existing, combined], ignore_index=True)

        combined.to_parquet(output_path, index=False, engine="pyarrow")
