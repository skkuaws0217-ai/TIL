#!/usr/bin/env python3
"""Sprint 2 검증: 샘플 환자 케이스로 Phase 2 전체 파이프라인 테스트.

시나리오: 65세 남성, 발열+기침+호흡곤란 → CAP(지역사회획득 폐렴) 의심 환자
  Lab:  WBC↑, CRP↑, pO2↓, Lactate↑, Procalcitonin↑
  VRH:  SpO2↓, RR↑, HR↑, Temp↑
  Micro: Streptococcus pneumoniae
  Symptoms: cough, fever, dyspnea, sputum production, pleuritic chest pain
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lung_dx.knowledge import (
    DiseaseRegistry,
    LabReferenceManager,
    VitalsRespiratoryHemodynamicManager,
)
from lung_dx.phase2_multimodal import (
    LabAnalyzer,
    VitalsRespiratoryHemodynamicAnalyzer,
    MicroAnalyzer,
    SymptomMatcher,
    DiagnosticScorer,
)


def main():
    print("=" * 70)
    print(" Phase 2 Scoring 검증 — CAP 의심 환자 시나리오")
    print("=" * 70)

    # ── 1) Knowledge Base 로드 ────────────────────────────────
    print("\n[1] Knowledge Base 로드...")
    registry = DiseaseRegistry()
    registry.load()
    lab_ref = LabReferenceManager()
    lab_ref.load()
    vrh_ref = VitalsRespiratoryHemodynamicManager()
    vrh_ref.load()
    print(f"  질환: {registry.count}개, Lab: {lab_ref.item_count}개, VRH: {vrh_ref.item_count}개")

    # ── 2) 샘플 환자 데이터 ───────────────────────────────────
    print("\n[2] 샘플 환자 데이터 (65세 남성, CAP 의심)")

    # Lab 결과
    lab_results = [
        {"itemid": 51301, "value": 18.5, "name": "WBC"},           # ↑ (정상 4.5-11.0)
        {"itemid": 50889, "value": 150.0, "name": "CRP"},          # ↑↑ (정상 0-5)
        {"itemid": 50821, "value": 58.0, "name": "pO2"},           # ↓↓ Critical
        {"itemid": 50813, "value": 3.2, "name": "Lactate"},        # ↑ (정상 0.5-2.0)
        {"itemid": 50818, "value": 32.0, "name": "pCO2"},          # ↓ (정상 35-45)
        {"itemid": 50820, "value": 7.48, "name": "pH"},            # ↑ slight alkalemia
        {"itemid": 50931, "value": 95.0, "name": "Glucose"},       # 정상
        {"itemid": 51222, "value": 12.5, "name": "Hemoglobin"},    # 정상
    ]

    # VRH 데이터
    vrh_data = [
        {"itemid": 220277, "value": 88.0, "name": "SpO2"},         # ↓ (정상 95-100)
        {"itemid": 220210, "value": 32.0, "name": "RR"},           # ↑ (정상 12-20)
        {"itemid": 220045, "value": 115.0, "name": "HR"},          # ↑ (정상 60-100)
        {"itemid": 223762, "value": 39.2, "name": "Temperature"},  # ↑ fever
        {"itemid": 220050, "value": 95.0, "name": "SBP"},          # ↓ borderline
    ]

    # 미생물 소견
    patient_micro = ["Streptococcus pneumoniae"]

    # 증상
    patient_symptoms = ["cough", "fever", "dyspnea", "sputum production",
                        "pleuritic chest pain"]
    patient_hpo = ["HP:0012735", "HP:0001945", "HP:0002094"]  # cough, fever, dyspnea

    # ── 3) 분석 실행 ──────────────────────────────────────────
    print("\n[3] 분석 실행...")

    # Lab
    lab_analyzer = LabAnalyzer(lab_ref)
    lab_findings = lab_analyzer.analyze(lab_results)
    abnormal_labs = lab_analyzer.get_abnormal_findings(lab_findings)
    critical_labs = lab_analyzer.get_critical_findings(lab_findings)
    lab_terms = lab_analyzer.extract_medical_terms(lab_findings)

    print(f"\n  Lab 결과: {len(lab_findings)}개 분석, {len(abnormal_labs)}개 비정상, {len(critical_labs)}개 critical")
    for f in abnormal_labs:
        print(f"    {f.name}: {f.value} → {f.interpretation} ({f.medical_term}) [{f.severity}]")
    print(f"  Medical terms: {lab_terms}")

    # VRH
    vrh_analyzer = VitalsRespiratoryHemodynamicAnalyzer(vrh_ref)
    vrh_findings = vrh_analyzer.analyze(vrh_data)
    abnormal_vrh = vrh_analyzer.get_abnormal_findings(vrh_findings)

    print(f"\n  VRH 결과: {len(vrh_findings)}개 분석, {len(abnormal_vrh)}개 비정상")
    for f in abnormal_vrh:
        triggered = f", triggers: {f.thresholds_triggered}" if f.thresholds_triggered else ""
        print(f"    {f.name}: {f.value} → {f.interpretation} ({f.medical_term}) [{f.severity}]{triggered}")

    # 스코어링 시스템
    scoring = vrh_analyzer.compute_scoring_systems(vrh_data, patient_age=65, patient_confusion=False, patient_bun=25.0)
    print(f"\n  스코어링 시스템:")
    for s in scoring:
        print(f"    {s.name}: {s.score}점 — {s.interpretation}")
        if s.components:
            print(f"      components: {s.components}")

    # 파생 지표
    indicators = vrh_analyzer.compute_derived_indicators(vrh_data)
    if indicators:
        print(f"\n  파생 지표:")
        for ind in indicators:
            print(f"    {ind.name}: {ind.value} ({ind.category})")

    # Micro
    micro_analyzer = MicroAnalyzer()
    micro_findings = micro_analyzer.analyze(patient_micro, registry.get_all())
    print(f"\n  미생물 매칭:")
    for f in micro_findings:
        print(f"    {f.organism} → {len(f.matched_diseases)}개 질환 매칭")
        for dk in f.matched_diseases[:5]:
            p = registry.get_by_key(dk)
            if p:
                print(f"      - {p.name_kr} ({dk})")

    # Symptom
    symptom_matcher = SymptomMatcher()
    symptom_matches = symptom_matcher.match(
        patient_symptoms, patient_hpo, registry.get_all()
    )
    print(f"\n  증상 매칭: {len(symptom_matches)}개 증상, 총 매칭 질환:")
    for m in symptom_matches[:5]:
        print(f"    {m.symptom} (HPO:{m.hpo_id}) → {len(m.matched_diseases)}개 질환")

    # ── 4) 진단 스코어링 ──────────────────────────────────────
    print("\n[4] 진단 스코어링 (상위 10개)...")
    scorer = DiagnosticScorer(registry)
    ranked = scorer.score_all(
        patient_lab_findings=lab_findings,
        patient_vrh_findings=vrh_findings,
        patient_micro_findings=micro_findings,
        patient_symptom_matches=symptom_matches,
        scoring_results=scoring,
        top_n=10,
        include_rare=False,
    )

    print(f"\n  {'순위':<4} {'Score':<7} {'Confidence':<10} {'질환명':<45} {'ICD-10'}")
    print("  " + "-" * 90)
    for i, ds in enumerate(ranked, 1):
        icd = ",".join(ds.icd10_codes[:3])
        print(f"  {i:<4} {ds.total_score:<7.4f} {ds.confidence.value:<10} {ds.name_kr[:40]:<45} {icd}")
        print(f"       modality: S={ds.modality_scores.get('symptoms',0):.2f} "
              f"L={ds.modality_scores.get('lab',0):.2f} "
              f"R={ds.modality_scores.get('radiology',0):.2f} "
              f"M={ds.modality_scores.get('micro',0):.2f}")

    # ── 5) 검증: CAP가 1위인가? ──────────────────────────────
    print("\n[5] 검증:")
    if ranked and "pneumonia" in ranked[0].disease_key.lower():
        print("  ✓ 폐렴 관련 질환이 1위 — 기대한 결과!")
    else:
        top_key = ranked[0].disease_key if ranked else "none"
        print(f"  ⚠ 1위: {top_key} — CAP가 아닌 다른 질환이 1위")

    print("\n" + "=" * 70)
    print(" Phase 2 검증 완료!")
    print("=" * 70)


if __name__ == "__main__":
    main()
