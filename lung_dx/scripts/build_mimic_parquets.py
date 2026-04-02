#!/usr/bin/env python3
"""MIMIC-IV CSV → Parquet 변환 스크립트.

실행: python scripts/build_mimic_parquets.py [--lab-only | --vitals-only]

출력:
  cache/parquets/lung_labs.parquet         (labevents.csv 18GB → ~1-5GB)
  cache/parquets/respiratory_vitals.parquet (chartevents.csv 42GB → ~2-8GB)

예상 소요: 합계 30-60분 (SSD 기준)
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lung_dx.config import paths
from lung_dx.mimic_etl import LabExtractor, VitalsExtractor

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


def main():
    parser = argparse.ArgumentParser(description="MIMIC-IV CSV → Parquet 추출")
    parser.add_argument("--lab-only", action="store_true", help="Lab만 추출")
    parser.add_argument("--vitals-only", action="store_true", help="VRH만 추출")
    parser.add_argument("--chunk-size", type=int, default=500_000)
    args = parser.parse_args()

    do_lab = not args.vitals_only
    do_vitals = not args.lab_only

    paths.PARQUET_DIR.mkdir(parents=True, exist_ok=True)

    if do_lab:
        print(f"\n{'='*60}")
        print(f" Lab 추출: {paths.LABEVENTS_CSV.name}")
        print(f"{'='*60}")
        if not paths.LABEVENTS_CSV.exists():
            print(f"  ⚠ 파일 없음: {paths.LABEVENTS_CSV}")
        else:
            t0 = time.time()
            extractor = LabExtractor(chunk_size=args.chunk_size)
            out = extractor.extract()
            elapsed = time.time() - t0
            size_mb = out.stat().st_size / 1024 / 1024
            print(f"  ✓ 완료: {out} ({size_mb:.1f} MB, {elapsed:.0f}초)")

    if do_vitals:
        print(f"\n{'='*60}")
        print(f" VRH 추출: {paths.CHARTEVENTS_CSV.name}")
        print(f"{'='*60}")
        if not paths.CHARTEVENTS_CSV.exists():
            print(f"  ⚠ 파일 없음: {paths.CHARTEVENTS_CSV}")
        else:
            t0 = time.time()
            extractor = VitalsExtractor(chunk_size=args.chunk_size)
            out = extractor.extract()
            elapsed = time.time() - t0
            size_mb = out.stat().st_size / 1024 / 1024
            print(f"  ✓ 완료: {out} ({size_mb:.1f} MB, {elapsed:.0f}초)")

    print("\n완료!")


if __name__ == "__main__":
    main()
