"""Vitals / Respiratory / Hemodynamic 분석 모듈.

환자의 VRH 데이터를 vitals_respiratory_hemodynamic_reference_range_v1.yaml
(37개 파라미터)과 비교하여 해석하고, 임상 스코어링 시스템(NEWS2, qSOFA,
CURB-65, PESI)을 계산하며, 파생 지표(S/F ratio, Driving Pressure 등)를
산출한다.
"""

from __future__ import annotations

from typing import Any

from ..domain.findings import (
    VitalsRespiratoryHemodynamicFinding,
    ScoringSystemResult,
    DerivedIndicator,
)
from ..knowledge.vitals_reference import VitalsRespiratoryHemodynamicManager


class VitalsRespiratoryHemodynamicAnalyzer:
    """VRH 파라미터 해석 + 스코어링 + 파생지표 계산."""

    def __init__(self, vrh_ref: VitalsRespiratoryHemodynamicManager):
        self._vrh_ref = vrh_ref
        self._vrh_ref._ensure_loaded()

    def analyze(
        self, vrh_data: list[dict[str, Any]]
    ) -> list[VitalsRespiratoryHemodynamicFinding]:
        """환자의 VRH 데이터 일괄 해석.

        Args:
            vrh_data: [{itemid, name, value, unit, timestamp, ...}]

        Returns:
            해석된 VitalsRespiratoryHemodynamicFinding 목록.
        """
        findings = []
        for record in vrh_data:
            itemid = record.get("itemid")
            value = record.get("value")
            if itemid is None or value is None:
                continue

            # 숫자값만 해석 (Lung Sounds 등 categorical은 별도)
            if isinstance(value, (int, float)):
                finding = self._vrh_ref.interpret_value(int(itemid), float(value))
                findings.append(finding)
            else:
                findings.append(self._interpret_categorical(
                    int(itemid), str(value)
                ))
        return findings

    def compute_scoring_systems(
        self,
        vrh_data: list[dict[str, Any]],
        patient_age: int | None = None,
        patient_confusion: bool = False,
        patient_bun: float | None = None,
    ) -> list[ScoringSystemResult]:
        """임상 스코어링 시스템 계산.

        NEWS2, qSOFA는 VRH 데이터만으로 계산.
        CURB-65는 age, confusion, BUN 추가 필요.
        PESI는 age + 임상 정보 추가 필요.

        Args:
            vrh_data: VRH 측정값 목록
            patient_age: 환자 나이 (CURB-65, PESI용)
            patient_confusion: 의식 혼란 여부 (CURB-65, qSOFA용)
            patient_bun: BUN mg/dL (CURB-65용)
        """
        # itemid → 최신값 매핑 (동일 항목 여러 측정 시 최신값 사용)
        vitals_map: dict[int, float] = {}
        for record in vrh_data:
            itemid = record.get("itemid")
            value = record.get("value")
            if itemid is not None and isinstance(value, (int, float)):
                vitals_map[int(itemid)] = float(value)

        # VRH 매니저의 스코어링 계산
        results = self._vrh_ref.compute_scoring_systems(vitals_map)

        # CURB-65 보충 (VRH 외 항목)
        self._supplement_curb65(
            results, vitals_map, patient_age, patient_confusion, patient_bun
        )

        # qSOFA 보충 (의식수준)
        self._supplement_qsofa(results, patient_confusion)

        return results

    def compute_derived_indicators(
        self, vrh_data: list[dict[str, Any]]
    ) -> list[DerivedIndicator]:
        """파생 지표 계산 (S/F ratio, Driving Pressure 등)."""
        vitals_map: dict[int, float] = {}
        for record in vrh_data:
            itemid = record.get("itemid")
            value = record.get("value")
            if itemid is not None and isinstance(value, (int, float)):
                vitals_map[int(itemid)] = float(value)

        indicators = self._vrh_ref.compute_derived_indicators(vitals_map)

        # Driving Pressure (Pplat - PEEP)
        pplat = vitals_map.get(224696)   # Plateau Pressure
        peep = vitals_map.get(220339)    # PEEP set
        if pplat is not None and peep is not None:
            dp = pplat - peep
            cat = "target" if dp < 15 else "concern"
            indicators.append(DerivedIndicator(
                name="Driving Pressure",
                value=round(dp, 1),
                interpretation=f"Pplat({pplat}) - PEEP({peep})",
                category=cat,
            ))

        # Ventilator Dependence
        fio2 = vitals_map.get(223835, 0.21)
        peep_val = vitals_map.get(220339, 0)
        if fio2 > 0.21 or peep_val > 0:
            indicators.append(DerivedIndicator(
                name="Ventilator Dependence",
                value=1.0,
                interpretation=f"FiO2={fio2}, PEEP={peep_val}",
                category="ventilator_dependent",
            ))

        return indicators

    def get_abnormal_findings(
        self, findings: list[VitalsRespiratoryHemodynamicFinding]
    ) -> list[VitalsRespiratoryHemodynamicFinding]:
        return [f for f in findings if f.severity != "normal"]

    def extract_disease_associations(
        self, findings: list[VitalsRespiratoryHemodynamicFinding]
    ) -> dict[str, list[str]]:
        """비정상 소견에서 disease_key → [근거] 매핑 추출."""
        assoc: dict[str, list[str]] = {}
        for f in findings:
            if f.severity == "normal":
                continue
            for da in f.disease_associations:
                dk = da.get("disease_key", "")
                pattern = da.get("pattern", "")
                if dk:
                    evidence = f"{f.medical_term or f.interpretation} — {f.name}"
                    if pattern:
                        evidence += f" [{pattern[:60]}]"
                    assoc.setdefault(dk, []).append(evidence)
        return assoc

    # ── CURB-65 보충 ──────────────────────────────────────────
    # [Lim et al. Thorax 2003;58(5):377-382]
    def _supplement_curb65(
        self,
        results: list[ScoringSystemResult],
        vitals_map: dict[int, float],
        age: int | None,
        confusion: bool,
        bun: float | None,
    ) -> None:
        curb = None
        for r in results:
            if r.name == "CURB65":
                curb = r
                break
        if curb is None:
            curb = ScoringSystemResult(name="CURB65", score=0, components={})
            results.append(curb)

        # C: Confusion
        if confusion:
            curb.components["Confusion"] = 1
            curb.score += 1

        # U: BUN ≥ 20 mg/dL (≈ Urea ≥ 7 mmol/L)
        if bun is not None and bun >= 20:
            curb.components["BUN≥20"] = 1
            curb.score += 1

        # R: RR ≥ 30 — VRH 매니저에서 이미 계산됨

        # B: SBP < 90 또는 DBP ≤ 60
        sbp = vitals_map.get(220050) or vitals_map.get(220179)
        dbp = vitals_map.get(220051) or vitals_map.get(220180)
        if sbp is not None and sbp < 90:
            curb.components["SBP<90"] = 1
            curb.score += 1
        elif dbp is not None and dbp <= 60:
            curb.components["DBP≤60"] = 1
            curb.score += 1

        # 65: Age ≥ 65
        if age is not None and age >= 65:
            curb.components["Age≥65"] = 1
            curb.score += 1

        # 해석 [Lim et al. Thorax 2003]
        if curb.score <= 1:
            curb.interpretation = "Low severity (outpatient)"
        elif curb.score == 2:
            curb.interpretation = "Moderate severity (consider admission)"
        else:
            curb.interpretation = "High severity (ICU consideration if 4-5)"

    # ── qSOFA 보충 ────────────────────────────────────────────
    # [Seymour et al. JAMA 2016;315(8):801-810]
    def _supplement_qsofa(
        self,
        results: list[ScoringSystemResult],
        confusion: bool,
    ) -> None:
        qsofa = None
        for r in results:
            if r.name == "qSOFA":
                qsofa = r
                break
        if qsofa is None:
            qsofa = ScoringSystemResult(name="qSOFA", score=0, components={})
            results.append(qsofa)

        # GCS < 15 (의식 변화)
        if confusion:
            qsofa.components["Altered mental status"] = 1
            qsofa.score += 1

        # 해석
        qsofa.interpretation = (
            "Sepsis suspected" if qsofa.score >= 2 else "Low risk"
        )

    # ── Categorical 값 해석 ───────────────────────────────────
    def _interpret_categorical(
        self, itemid: int, value: str
    ) -> VitalsRespiratoryHemodynamicFinding:
        """Lung Sounds, Ventilator Mode 등 텍스트값 해석."""
        item = self._vrh_ref.get_item(itemid) or {}
        name = item.get("name", str(itemid))
        name_kr = item.get("name_kr", "")

        # 폐 청진: Clear=정상, 나머지=비정상
        severity = "normal"
        medical_term = ""
        value_lower = value.strip().lower()

        if itemid in (223986, 223987, 223988, 223989):  # Lung Sounds
            if value_lower != "clear":
                severity = "abnormal"
                medical_term = f"{value} lung sounds"
        elif itemid == 223849:  # Ventilator Mode
            medical_term = f"Ventilator mode: {value}"

        return VitalsRespiratoryHemodynamicFinding(
            itemid=itemid,
            name=name,
            name_kr=name_kr,
            value=0.0,
            unit=item.get("unit", "text"),
            interpretation=value,
            medical_term=medical_term,
            severity=severity,
            disease_associations=item.get("disease_associations", []),
        )
