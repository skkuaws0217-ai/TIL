"""Vitals · Respiratory · Hemodynamic Reference 매니저.

vitals_respiratory_hemodynamic_reference_range_v1.yaml 기반.
37개 파라미터의 정상범위, thresholds, scoring_systems,
derived_indicators, disease_associations를 관리한다.
"""

from __future__ import annotations

import re
from typing import Any, Optional

import yaml

from ..config import paths
from ..domain.findings import (
    VitalsRespiratoryHemodynamicFinding,
    ScoringSystemResult,
    DerivedIndicator,
)


class VitalsRespiratoryHemodynamicManager:
    """vitals_respiratory_hemodynamic_reference_range_v1.yaml 로드 및 해석."""

    def __init__(self, yaml_path: str | None = None):
        self._yaml_path = yaml_path or str(paths.VITALS_REFERENCE_YAML)
        self._items: dict[int, dict[str, Any]] = {}
        self._loaded = False

    # ── 로드 ──────────────────────────────────────────────────
    def load(self) -> None:
        with open(self._yaml_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        for key, item in raw.items():
            self._items[int(key)] = item
        self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # ── 조회 ──────────────────────────────────────────────────
    @property
    def item_count(self) -> int:
        self._ensure_loaded()
        return len(self._items)

    def get_all_itemids(self) -> list[int]:
        self._ensure_loaded()
        return list(self._items.keys())

    def get_item(self, itemid: int) -> dict[str, Any] | None:
        self._ensure_loaded()
        return self._items.get(itemid)

    def get_items_by_category(self, category: str) -> list[tuple[int, dict]]:
        self._ensure_loaded()
        return [
            (k, v) for k, v in self._items.items()
            if v.get("category", "") == category
        ]

    # ── 개별 파라미터 해석 ────────────────────────────────────
    def interpret_value(
        self, itemid: int, value: float
    ) -> VitalsRespiratoryHemodynamicFinding:
        """파라미터 값을 reference range 및 thresholds와 비교."""
        self._ensure_loaded()
        item = self._items.get(itemid, {})
        name = item.get("name", str(itemid))
        name_kr = item.get("name_kr", "")
        unit = item.get("unit", "")
        medical_terms = item.get("medical_terms", {})
        disease_assoc = item.get("disease_associations", [])

        ranges = item.get("ranges", {})
        lower = ranges.get("lower")
        upper = ranges.get("upper")

        # 기본 해석
        interpretation = "Normal"
        medical_term = ""
        severity = "normal"

        if lower is not None and value < lower:
            interpretation = "Low"
            medical_term = medical_terms.get("low", "")
            severity = "abnormal"
        elif upper is not None and value > upper:
            interpretation = "High"
            medical_term = medical_terms.get("high", "")
            severity = "abnormal"

        # Critical 판정 — 방향(Low/High)을 구분하여 판정
        # Low 방향: value < lower이고 severe 하한 threshold 이하
        if interpretation == "Low" and "critical_low" in medical_terms:
            for th in item.get("thresholds", []):
                th_name = th.get("name", "").lower()
                if "severe" in th_name and ("low" in th_name or "brady" in th_name
                                            or "hypo" in th_name or "depression" in th_name):
                    th_val = self._parse_threshold_value(th.get("criterion", ""))
                    if th_val is not None and value <= th_val:
                        severity = "critical"
                        medical_term = medical_terms.get("critical_low", medical_term)
                        break

        # High 방향: value > upper이고 severe 상한 threshold 이상
        if interpretation == "High" and "critical_high" in medical_terms:
            for th in item.get("thresholds", []):
                th_name = th.get("name", "").lower()
                if "severe" in th_name and ("high" in th_name or "tachy" in th_name
                                            or "hyper" in th_name or "crisis" in th_name):
                    th_val = self._parse_threshold_value(th.get("criterion", ""))
                    if th_val is not None and value >= th_val:
                        severity = "critical"
                        medical_term = medical_terms.get("critical_high", medical_term)
                        break

        # Threshold 트리거 목록
        triggered = []
        for th in item.get("thresholds", []):
            if self._check_threshold(value, th.get("criterion", "")):
                triggered.append(th.get("name", ""))

        # 스코어링 기여도
        scoring = {}
        for system_name, rules in item.get("scoring_systems", {}).items():
            score = self._evaluate_scoring(value, rules)
            if score is not None:
                scoring[system_name] = score

        return VitalsRespiratoryHemodynamicFinding(
            itemid=itemid,
            name=name,
            name_kr=name_kr,
            value=value,
            unit=unit,
            interpretation=interpretation,
            medical_term=medical_term,
            severity=severity,
            thresholds_triggered=triggered,
            scoring_contributions=scoring,
            disease_associations=disease_assoc,
        )

    # ── 스코어링 시스템 ───────────────────────────────────────
    def compute_scoring_systems(
        self, vitals: dict[int, float]
    ) -> list[ScoringSystemResult]:
        """이용 가능한 vitals로 NEWS2, qSOFA 등 계산."""
        self._ensure_loaded()
        system_totals: dict[str, dict] = {}

        for itemid, value in vitals.items():
            item = self._items.get(itemid, {})
            for sys_name, rules in item.get("scoring_systems", {}).items():
                score = self._evaluate_scoring(value, rules)
                if score is not None:
                    if sys_name not in system_totals:
                        system_totals[sys_name] = {"score": 0, "components": {}}
                    system_totals[sys_name]["score"] += score
                    system_totals[sys_name]["components"][item.get("name", str(itemid))] = score

        results = []
        for sys_name, data in system_totals.items():
            results.append(ScoringSystemResult(
                name=sys_name,
                score=data["score"],
                interpretation=self._interpret_system_score(sys_name, data["score"]),
                components=data["components"],
            ))
        return results

    # ── 파생 지표 ─────────────────────────────────────────────
    def compute_derived_indicators(
        self, vitals: dict[int, float]
    ) -> list[DerivedIndicator]:
        """S/F ratio 등 파생 지표 계산."""
        self._ensure_loaded()
        results = []

        # derived_indicators가 있는 항목에서 추출
        for itemid, item in self._items.items():
            for di in item.get("derived_indicators", []):
                name = di.get("name", "")
                if name == "S/F ratio" and 220277 in vitals and 223835 in vitals:
                    spo2 = vitals[220277]
                    fio2 = vitals[223835]
                    if fio2 > 0:
                        sf_ratio = spo2 / fio2
                        cat = self._classify_sf_ratio(sf_ratio)
                        results.append(DerivedIndicator(
                            name="S/F ratio",
                            value=round(sf_ratio, 1),
                            interpretation=f"SpO2({spo2})/FiO2({fio2})",
                            category=cat,
                        ))
        return results

    # ── 내부 유틸 ─────────────────────────────────────────────
    @staticmethod
    def _parse_threshold_value(criterion: str) -> float | None:
        """'≤94', '<90', '≥96' 등에서 숫자 추출."""
        m = re.search(r"[<≤>≥]?\s*([\d.]+)", criterion)
        return float(m.group(1)) if m else None

    @staticmethod
    def _check_threshold(value: float, criterion: str) -> bool:
        """가이드라인 연산자 기반 threshold 체크."""
        criterion = criterion.strip()
        # 범위 (e.g., "94–98", "88–92")
        range_match = re.match(r"([\d.]+)[–-]([\d.]+)", criterion)
        if range_match:
            lo, hi = float(range_match.group(1)), float(range_match.group(2))
            return lo <= value <= hi

        # 단일 비교
        for op, fn in [
            ("≤", lambda v, t: v <= t),
            ("≥", lambda v, t: v >= t),
            ("<", lambda v, t: v < t),
            (">", lambda v, t: v > t),
        ]:
            if criterion.startswith(op):
                num = re.search(r"[\d.]+", criterion[len(op):])
                if num:
                    return fn(value, float(num.group()))
        return False

    def _evaluate_scoring(self, value: float, rules: dict) -> float | None:
        """스코어링 규칙 딕셔너리에서 value에 해당하는 점수 반환."""
        for criterion_str, score in rules.items():
            if self._check_threshold(value, str(criterion_str)):
                return self._parse_score_value(score)
        return None

    @staticmethod
    def _parse_score_value(score: Any) -> float:
        """점수값 파싱. '+1.5점', '+20점', '3', 20 등 다양한 형태 처리."""
        if isinstance(score, (int, float)):
            return float(score)
        s = str(score).strip()
        m = re.search(r"[+-]?\d+\.?\d*", s)
        return float(m.group()) if m else 0.0

    @staticmethod
    def _interpret_system_score(system: str, score: int | float) -> str:
        if system.startswith("NEWS2"):
            if score >= 7:
                return "High clinical risk"
            elif score >= 5:
                return "Medium clinical risk"
            elif score >= 1:
                return "Low clinical risk"
            return "No risk"
        if system == "qSOFA":
            return "Sepsis suspected" if score >= 2 else "Low risk"
        return ""

    @staticmethod
    def _classify_sf_ratio(sf: float) -> str:
        if sf <= 148:
            return "severe_ards"
        elif sf <= 235:
            return "moderate_ards"
        elif sf <= 315:
            return "mild_ards"
        return "normal"
