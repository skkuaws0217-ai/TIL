#!/usr/bin/env python3
"""검증 스크립트: 7개 데이터 파일에서 전체 질환 레지스트리를 구축하고 요약 출력.

실행:
    python scripts/build_disease_registry.py
"""

import sys
from pathlib import Path

# 프로젝트 루트를 PYTHONPATH에 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lung_dx.knowledge import DiseaseRegistry, LabReferenceManager, VitalsRespiratoryHemodynamicManager


def main():
    print("=" * 60)
    print(" Disease Registry 구축 및 검증")
    print("=" * 60)

    # ── 1) DiseaseRegistry ────────────────────────────────────
    print("\n[1/3] DiseaseRegistry 로드 중...")
    registry = DiseaseRegistry()
    registry.load()

    summary = registry.summary()
    print(f"\n  총 질환 수: {summary['total']}")
    print(f"    - 일반 (common):       {summary['common']}")
    print(f"    - 기타 (other):        {summary['other']}")
    print(f"    - 희귀 (rare):         {summary['rare']}")
    print(f"    - YAML 보강 (enriched):{summary['yaml_enriched']}")
    print(f"  AI 영상 키워드 보유:      {summary['with_ai_keywords']}")
    print(f"  Lab 패턴 보유:            {summary['with_lab_patterns']}")
    print(f"  유전자 정보 보유:         {summary['with_genes']}")
    print(f"  고유 HPO ID 수:          {summary['unique_hpo_ids']}")
    print(f"  고유 AI 키워드 수:        {summary['unique_keywords']}")

    # 샘플 출력
    print("\n  --- 샘플 질환 (상위 5개) ---")
    for profile in list(registry.get_all())[:5]:
        print(f"    {profile.disease_key} | {profile.name_kr} | ICD-10: {profile.icd10_codes[:3]} | category: {profile.category.value}")

    # 희귀질환 유전자 보유 샘플
    gene_diseases = registry.get_diseases_with_genes()
    print(f"\n  --- 유전자 정보 보유 질환 (상위 5개 / 전체 {len(gene_diseases)}) ---")
    for p in gene_diseases[:5]:
        print(f"    {p.name_en[:50]} | genes: {p.major_genes[:3]} | type: {p.genetic_type}")

    # ── 2) LabReferenceManager ────────────────────────────────
    print(f"\n[2/3] LabReferenceManager 로드 중...")
    lab_ref = LabReferenceManager()
    lab_ref.load()
    print(f"  총 검사항목 수: {lab_ref.item_count}")
    print(f"  MIMIC-IV ItemID 수: {len(lab_ref.get_mimic_itemids())}")

    # 샘플 해석
    finding = lab_ref.interpret_value(50821, 65.0)  # pO2 = 65 (low)
    print(f"\n  샘플 해석: pO2=65 → {finding.interpretation} ({finding.medical_term}), severity={finding.severity}")

    # ── 3) VitalsRespiratoryHemodynamicManager ────────────────
    print(f"\n[3/3] VitalsRespiratoryHemodynamicManager 로드 중...")
    vrh_ref = VitalsRespiratoryHemodynamicManager()
    vrh_ref.load()
    print(f"  총 파라미터 수: {vrh_ref.item_count}")

    # 샘플 해석
    finding = vrh_ref.interpret_value(220277, 88.0)  # SpO2 = 88%
    print(f"\n  샘플 해석: SpO2=88% → {finding.interpretation} ({finding.medical_term}), severity={finding.severity}")
    print(f"  트리거된 thresholds: {finding.thresholds_triggered}")

    print("\n" + "=" * 60)
    print(" 검증 완료!")
    print("=" * 60)


if __name__ == "__main__":
    main()
