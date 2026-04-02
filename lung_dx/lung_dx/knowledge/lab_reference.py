"""Lab Reference Range 매니저 — lab_reference_ranges_v3.yaml 기반.

89개 검사항목(MIMIC-IV 50 + 외부 39)의 정상범위, critical 값,
medical_terms, disease_associations를 관리한다.
"""

from __future__ import annotations

from typing import Any, Optional

import yaml

from ..config import paths
from ..domain.findings import LabFinding


class LabReferenceManager:
    """lab_reference_ranges_v3.yaml 로드 및 검사값 해석."""

    def __init__(self, yaml_path: str | None = None):
        self._yaml_path = yaml_path or str(paths.LAB_REFERENCE_YAML)
        self._items: dict[str | int, dict[str, Any]] = {}
        self._loaded = False

    # ── 로드 ──────────────────────────────────────────────────
    def load(self) -> None:
        with open(self._yaml_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        for key, item in raw.items():
            self._items[key] = item
        self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # ── 조회 ──────────────────────────────────────────────────
    @property
    def item_count(self) -> int:
        self._ensure_loaded()
        return len(self._items)

    def get_all_itemids(self) -> list[str | int]:
        self._ensure_loaded()
        return list(self._items.keys())

    def get_mimic_itemids(self) -> list[int]:
        """MIMIC-IV 내장 ItemID만 반환 (숫자)."""
        self._ensure_loaded()
        return [k for k in self._items if isinstance(k, int)]

    def get_item(self, itemid: int | str) -> dict[str, Any] | None:
        self._ensure_loaded()
        return self._items.get(itemid)

    def get_items_by_category(self, category: str) -> list[tuple[str | int, dict]]:
        """카테고리별 항목 조회. e.g., 'A_Blood_Gas_Analysis'."""
        self._ensure_loaded()
        return [
            (k, v) for k, v in self._items.items()
            if v.get("category", "") == category
        ]

    # ── 검사값 해석 ───────────────────────────────────────────
    def interpret_value(
        self,
        itemid: int | str,
        value: float,
        ref_lower: Optional[float] = None,
        ref_upper: Optional[float] = None,
    ) -> LabFinding:
        """검사값을 reference range와 비교하여 해석.

        우선순위:
        1. 외부 제공 ref_lower/ref_upper (MIMIC labevents)
        2. YAML ranges
        """
        self._ensure_loaded()
        item = self._items.get(itemid, {})
        name = item.get("name", str(itemid))
        unit = item.get("unit", "")
        ref_source = item.get("ref_source", "")
        medical_terms = item.get("medical_terms", {})
        disease_assoc = item.get("disease_associations", [])

        # Reference range 결정
        yaml_ranges = item.get("ranges", {})
        lower = ref_lower if ref_lower is not None else yaml_ranges.get("lower")
        upper = ref_upper if ref_upper is not None else yaml_ranges.get("upper")

        # Critical thresholds
        critical = item.get("critical", {})
        crit_low = critical.get("low")
        crit_high = critical.get("high")

        # 해석
        interpretation = "Normal"
        medical_term = ""
        severity = "normal"

        if crit_low is not None and value < crit_low:
            interpretation = "Critical Low"
            medical_term = medical_terms.get("critical_low", medical_terms.get("low", ""))
            severity = "critical"
        elif crit_high is not None and value > crit_high:
            interpretation = "Critical High"
            medical_term = medical_terms.get("critical_high", medical_terms.get("high", ""))
            severity = "critical"
        elif lower is not None and value < lower:
            interpretation = "Low"
            medical_term = medical_terms.get("low", "")
            severity = "abnormal"
        elif upper is not None and value > upper:
            interpretation = "High"
            medical_term = medical_terms.get("high", "")
            severity = "abnormal"

        return LabFinding(
            itemid=itemid,
            name=name,
            value=value,
            unit=unit,
            ref_lower=lower,
            ref_upper=upper,
            interpretation=interpretation,
            medical_term=medical_term,
            severity=severity,
            disease_associations=disease_assoc,
            ref_source=ref_source,
        )

    def get_disease_associations(self, itemid: int | str) -> list[dict]:
        self._ensure_loaded()
        item = self._items.get(itemid, {})
        return item.get("disease_associations", [])
