#!/usr/bin/env python3
"""CLI — 로컬에서 환자 데이터를 입력하여 진단 파이프라인 실행.

사용법:
  python -m lung_dx.cli --json patient_data.json
  python -m lung_dx.cli --json patient_data.json --lab-pdf lab_results.pdf
  python -m lung_dx.cli --interactive
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .domain.patient import PatientCase
from .pipeline import DiagnosticPipeline


def run_from_json(json_path: str, lab_pdf_path: str | None = None) -> None:
    """JSON 파일에서 환자 데이터를 읽어 파이프라인 실행."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    lab_results = data.get("lab_results", [])

    # Lab PDF 자동 파싱
    if lab_pdf_path:
        from .parsers import PDFLabParser
        print(f"Lab PDF 파싱 중: {lab_pdf_path}")
        parser = PDFLabParser()
        pdf_results = parser.parse(lab_pdf_path)
        matched = [r for r in pdf_results if r.get("itemid") is not None]
        unmatched = [r for r in pdf_results if r.get("itemid") is None]
        print(f"  추출: {len(pdf_results)}개, 매칭 성공: {len(matched)}개, 미매칭: {len(unmatched)}개")
        if unmatched:
            print("  미매칭 항목:")
            for u in unmatched[:10]:
                print(f"    - {u['original_name']}: {u['original_result']}")
        for r in matched:
            lab_results.append({
                "itemid": r["itemid"],
                "value": r["value"],
                "unit": r.get("unit", ""),
                "ref_range_lower": r.get("ref_range_lower"),
                "ref_range_upper": r.get("ref_range_upper"),
            })

    patient = PatientCase(
        case_id=data.get("case_id", Path(json_path).stem),
        age=data.get("age"),
        sex=data.get("sex"),
        chief_complaint=data.get("chief_complaint", ""),
        symptoms=data.get("symptoms", []),
        hpo_symptoms=data.get("hpo_symptoms", []),
        lab_results=lab_results,
        vitals_respiratory_hemodynamic=data.get("vitals_respiratory_hemodynamic", []),
        micro_findings=data.get("micro_findings", []),
        xray_image_path=data.get("xray_image_path"),
    )

    print("파이프라인 초기화 중...")
    pipeline = DiagnosticPipeline()

    if data.get("include_rare_screening"):
        pipeline._settings.always_screen_rare = True

    print("진단 실행 중...")
    result = pipeline.run(patient)

    # 결과 출력
    if result.errors:
        print(f"\n오류: {result.errors}")
    if result.warnings:
        print(f"경고: {result.warnings}")

    print("\n" + result.report_text)

    # JSON 결과도 저장
    output_path = Path(json_path).with_suffix(".result.json")
    output_data = {
        "case_id": patient.case_id,
        "ranked_diseases": [
            {
                "rank": i + 1,
                "name_kr": d.name_kr,
                "name_en": d.name_en,
                "icd10": d.icd10_codes,
                "score": d.total_score,
                "confidence": d.confidence.value,
            }
            for i, d in enumerate(result.phase2.top_candidates)
        ] if result.phase2 else [],
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장: {output_path}")


def run_interactive() -> None:
    """대화형으로 환자 데이터 입력."""
    print("=" * 60)
    print(" 폐질환 진단 보조 프로그램 (대화형 모드)")
    print("=" * 60)

    case_id = input("\nCase ID: ").strip() or "interactive"
    age = input("나이: ").strip()
    sex = input("성별 (M/F): ").strip()
    chief_complaint = input("주소 (chief complaint): ").strip()
    symptoms_raw = input("증상 (쉼표 구분, 영문): ").strip()
    micro_raw = input("미생물 소견 (쉼표 구분, 없으면 Enter): ").strip()

    symptoms = [s.strip() for s in symptoms_raw.split(",") if s.strip()]
    micro = [m.strip() for m in micro_raw.split(",") if m.strip()]

    print("\nLab 결과 입력 (빈 줄 입력 시 종료):")
    print("  형식: itemid,value (예: 51301,18.5)")
    lab_results = []
    while True:
        line = input("  > ").strip()
        if not line:
            break
        parts = line.split(",")
        if len(parts) >= 2:
            try:
                lab_results.append({
                    "itemid": int(parts[0]),
                    "value": float(parts[1]),
                })
            except ValueError:
                print("    형식 오류, 다시 입력하세요.")

    print("\nVRH 데이터 입력 (빈 줄 입력 시 종료):")
    print("  형식: itemid,value (예: 220277,92)")
    vrh_data = []
    while True:
        line = input("  > ").strip()
        if not line:
            break
        parts = line.split(",")
        if len(parts) >= 2:
            try:
                vrh_data.append({
                    "itemid": int(parts[0]),
                    "value": float(parts[1]),
                })
            except ValueError:
                print("    형식 오류, 다시 입력하세요.")

    patient = PatientCase(
        case_id=case_id,
        age=int(age) if age else None,
        sex=sex or None,
        chief_complaint=chief_complaint,
        symptoms=symptoms,
        vitals_respiratory_hemodynamic=vrh_data,
        lab_results=lab_results,
        micro_findings=micro,
    )

    print("\n파이프라인 초기화 중...")
    pipeline = DiagnosticPipeline()
    print("진단 실행 중...")
    result = pipeline.run(patient)

    print("\n" + result.report_text)


def main():
    parser = argparse.ArgumentParser(
        description="폐질환 진단 보조 프로그램 CLI"
    )
    parser.add_argument("--json", help="환자 데이터 JSON 파일 경로")
    parser.add_argument("--lab-pdf", help="Lab 결과지 PDF 경로 (자동 파싱)")
    parser.add_argument("--interactive", action="store_true",
                        help="대화형 모드")
    args = parser.parse_args()

    if args.json:
        run_from_json(args.json, lab_pdf_path=args.lab_pdf)
    elif args.interactive:
        run_interactive()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
