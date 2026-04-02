"""528개 폐질환 통합 레지스트리.

데이터 소스 7개 파일:
  1. 일반_폐질환_데이터베이스_v4.xlsx  (82개)
  2. 기타_폐관련_질환_데이터베이스_v4.xlsx  (70개)
  3. 희귀_폐질환_데이터베이스_v4.xlsx  (376개)
  4. lung_disease_profiles_v2.yaml  (17개 상세 프로필)
  5. lung_disease_symptoms_v2.yaml  (17개 증상 상세)
  6. lab_reference_ranges_v3.yaml  (89개 검사항목 — disease_associations 활용)
  7. vitals_respiratory_hemodynamic_reference_range_v1.yaml
     (37개 파라미터 + 미생물 검사 reference — disease_associations 활용)

YAML 17개 질환은 Excel DB와 중복될 수 있으며,
YAML 데이터가 더 상세하므로 YAML을 우선 적용(merge)한다.
"""

from __future__ import annotations

import logging
from typing import Optional

import yaml

from ..config import paths
from ..domain.disease import DiseaseProfile
from ..domain.enums import DiseaseCategory
from .excel_loader import (
    load_common_or_other_diseases,
    load_rare_diseases,
)

logger = logging.getLogger(__name__)


class DiseaseRegistry:
    """528개 질환 통합 레지스트리 + 역인덱스."""

    def __init__(self):
        self._profiles: dict[str, DiseaseProfile] = {}
        # YAML key → 실제 profile key 매핑
        # (YAML 병합 시 Excel 키로 저장되므로 역참조 필요)
        self._yaml_key_map: dict[str, str] = {}
        # 역인덱스
        self._icd10_index: dict[str, list[str]] = {}     # ICD-10 → [disease_key]
        self._keyword_index: dict[str, list[str]] = {}    # AI keyword → [disease_key]
        self._hpo_index: dict[str, list[str]] = {}        # HPO ID → [disease_key]
        self._loaded = False

    # ── 로드 ──────────────────────────────────────────────────
    def load(self) -> None:
        """7개 데이터 소스에서 전체 질환 레지스트리를 구축한다."""
        # 1) Excel DB 로드
        common = load_common_or_other_diseases(
            str(paths.COMMON_DISEASE_XLSX), DiseaseCategory.COMMON
        )
        other = load_common_or_other_diseases(
            str(paths.OTHER_DISEASE_XLSX), DiseaseCategory.OTHER
        )
        rare = load_rare_diseases(str(paths.RARE_DISEASE_XLSX))

        for profile in common + other + rare:
            self._add_profile(profile)

        # 2) YAML 상세 프로필 병합 (17개)
        self._merge_yaml_profiles()

        # 3) 역인덱스 구축
        self._build_indexes()
        self._loaded = True

        logger.info(
            "DiseaseRegistry loaded: %d diseases "
            "(common=%d, other=%d, rare=%d, yaml_enriched=%d)",
            len(self._profiles),
            sum(1 for p in self._profiles.values() if p.category == DiseaseCategory.COMMON),
            sum(1 for p in self._profiles.values() if p.category == DiseaseCategory.OTHER),
            sum(1 for p in self._profiles.values() if p.category == DiseaseCategory.RARE),
            sum(1 for p in self._profiles.values() if p.category == DiseaseCategory.YAML_PROFILE),
        )

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def _add_profile(self, profile: DiseaseProfile) -> None:
        """프로필 추가. key 충돌 시 기존 것과 merge."""
        key = profile.disease_key
        if key in self._profiles:
            # 동일 키 충돌 → ICD-10 추가해서 유니크하게
            suffix = 2
            while f"{key}_{suffix}" in self._profiles:
                suffix += 1
            key = f"{key}_{suffix}"
            profile.disease_key = key
        self._profiles[key] = profile

    def _merge_yaml_profiles(self) -> None:
        """YAML 17개 상세 프로필을 기존 Excel 프로필에 병합.

        YAML은 weights, symptoms, lab_patterns, radiology_findings,
        micro_findings 등이 더 상세하다.
        """
        # lung_disease_profiles_v2.yaml
        profiles_data = self._load_yaml(str(paths.DISEASE_PROFILES_YAML))
        # lung_disease_symptoms_v2.yaml
        symptoms_data = self._load_yaml(str(paths.DISEASE_SYMPTOMS_YAML))

        for yaml_key, data in profiles_data.items():
            # Excel에서 동일 질환 찾기 (disease_key 또는 ICD-10 매칭)
            existing = self._find_matching_profile(yaml_key, data)

            if existing:
                # 기존 프로필에 YAML 데이터 merge
                self._enrich_profile(existing, data, symptoms_data.get(yaml_key, {}))
                existing.category = DiseaseCategory.YAML_PROFILE
                # YAML key → 실제 profile key 매핑 기록
                self._yaml_key_map[yaml_key] = existing.disease_key
            else:
                # Excel에 없는 질환 → 새로 추가
                profile = self._yaml_to_profile(yaml_key, data, symptoms_data.get(yaml_key, {}))
                self._profiles[yaml_key] = profile
                self._yaml_key_map[yaml_key] = yaml_key

    def _find_matching_profile(
        self, yaml_key: str, data: dict
    ) -> Optional[DiseaseProfile]:
        """YAML 키 또는 ICD-10으로 기존 프로필 검색."""
        # 1) disease_key 직접 매칭
        if yaml_key in self._profiles:
            return self._profiles[yaml_key]

        # 2) ICD-10 코드로 매칭
        yaml_icd = set(data.get("icd10", []))
        for profile in self._profiles.values():
            if yaml_icd & set(profile.icd10_codes):
                return profile
        return None

    def _enrich_profile(
        self, profile: DiseaseProfile, yaml_data: dict, symptom_data: dict
    ) -> None:
        """기존 프로필에 YAML 상세 데이터를 보강."""
        w = yaml_data.get("weights", {})
        if w:
            profile.weight_symptoms = w.get("symptoms", profile.weight_symptoms)
            profile.weight_lab = w.get("lab", profile.weight_lab)
            profile.weight_radiology = w.get("radiology", profile.weight_radiology)
            profile.weight_micro = w.get("micro", profile.weight_micro)

        if yaml_data.get("lab_patterns"):
            profile.lab_patterns = yaml_data["lab_patterns"]
        if yaml_data.get("radiology_findings"):
            profile.radiology_findings = yaml_data["radiology_findings"]
        if yaml_data.get("micro_findings"):
            profile.micro_findings = yaml_data["micro_findings"]
        if yaml_data.get("symptoms"):
            # YAML 증상이 더 상세하므로 교체
            profile.symptoms = yaml_data["symptoms"]
        if yaml_data.get("icd11"):
            profile.icd11_code = yaml_data["icd11"]
        if yaml_data.get("icd9"):
            profile.icd9_code = str(yaml_data["icd9"])
        if yaml_data.get("disease_kr"):
            profile.name_kr = yaml_data["disease_kr"]

        # hpo_symptom_map 보강
        hpo_map = yaml_data.get("hpo_symptom_map", {})
        if hpo_map:
            existing_hpo_ids = {h["hpo_id"] for h in profile.hpo_phenotypes}
            for symptom_name, hpo_id in hpo_map.items():
                if hpo_id not in existing_hpo_ids:
                    profile.hpo_phenotypes.append({
                        "hpo_id": hpo_id,
                        "hpo_term": symptom_name,
                        "hpo_kr": "",
                        "frequency": "",
                    })

        # symptom_data (lung_disease_symptoms_v2.yaml) 보강
        if symptom_data.get("symptoms"):
            for s in symptom_data["symptoms"]:
                hpo_id = s.get("hpo_id", "")
                if hpo_id:
                    existing_ids = {h["hpo_id"] for h in profile.hpo_phenotypes}
                    if hpo_id not in existing_ids:
                        profile.hpo_phenotypes.append({
                            "hpo_id": hpo_id,
                            "hpo_term": s.get("name", ""),
                            "hpo_kr": s.get("hpo_kr", ""),
                            "frequency": s.get("frequency", ""),
                        })

    def _yaml_to_profile(
        self, yaml_key: str, data: dict, symptom_data: dict
    ) -> DiseaseProfile:
        """YAML 데이터로 새 DiseaseProfile 생성."""
        w = data.get("weights", {})
        hpo_map = data.get("hpo_symptom_map", {})
        hpo_list = [
            {"hpo_id": hpo_id, "hpo_term": name, "hpo_kr": "", "frequency": ""}
            for name, hpo_id in hpo_map.items()
        ]
        # symptom_data 보충
        if symptom_data.get("symptoms"):
            existing_ids = {h["hpo_id"] for h in hpo_list}
            for s in symptom_data["symptoms"]:
                hpo_id = s.get("hpo_id", "")
                if hpo_id and hpo_id not in existing_ids:
                    hpo_list.append({
                        "hpo_id": hpo_id,
                        "hpo_term": s.get("name", ""),
                        "hpo_kr": s.get("hpo_kr", ""),
                        "frequency": s.get("frequency", ""),
                    })

        return DiseaseProfile(
            disease_key=yaml_key,
            name_en=yaml_key.replace("_", " ").title(),
            name_kr=data.get("disease_kr", ""),
            category=DiseaseCategory.YAML_PROFILE,
            icd10_codes=data.get("icd10", []),
            icd11_code=data.get("icd11", ""),
            icd9_code=str(data.get("icd9", "")),
            weight_symptoms=w.get("symptoms", 0.25),
            weight_lab=w.get("lab", 0.20),
            weight_radiology=w.get("radiology", 0.35),
            weight_micro=w.get("micro", 0.20),
            symptoms=data.get("symptoms", []),
            hpo_phenotypes=hpo_list,
            lab_patterns=data.get("lab_patterns", []),
            radiology_findings=data.get("radiology_findings", []),
            micro_findings=data.get("micro_findings", []),
        )

    # ── 역인덱스 ──────────────────────────────────────────────
    def _build_indexes(self) -> None:
        self._icd10_index.clear()
        self._keyword_index.clear()
        self._hpo_index.clear()

        for key, profile in self._profiles.items():
            # ICD-10 인덱스
            for code in profile.icd10_codes:
                self._icd10_index.setdefault(code, []).append(key)

            # AI 키워드 인덱스
            for kw in profile.ai_imaging_keywords:
                kw_lower = kw.lower().strip()
                if kw_lower:
                    self._keyword_index.setdefault(kw_lower, []).append(key)

            # HPO 인덱스
            for hpo in profile.hpo_phenotypes:
                hpo_id = hpo.get("hpo_id", "")
                if hpo_id:
                    self._hpo_index.setdefault(hpo_id, []).append(key)

    # ── 조회 API ──────────────────────────────────────────────
    @property
    def count(self) -> int:
        self._ensure_loaded()
        return len(self._profiles)

    def get_by_key(self, disease_key: str) -> Optional[DiseaseProfile]:
        self._ensure_loaded()
        return self._profiles.get(disease_key)

    def get_all(self) -> list[DiseaseProfile]:
        self._ensure_loaded()
        return list(self._profiles.values())

    def get_by_category(self, category: DiseaseCategory) -> list[DiseaseProfile]:
        self._ensure_loaded()
        return [p for p in self._profiles.values() if p.category == category]

    def search_by_icd10(self, code: str) -> list[DiseaseProfile]:
        self._ensure_loaded()
        keys = self._icd10_index.get(code, [])
        return [self._profiles[k] for k in keys if k in self._profiles]

    def search_by_keyword(self, keyword: str) -> list[DiseaseProfile]:
        """AI 영상 키워드로 질환 검색."""
        self._ensure_loaded()
        keys = self._keyword_index.get(keyword.lower().strip(), [])
        return [self._profiles[k] for k in keys if k in self._profiles]

    def search_by_keywords(self, keywords: list[str]) -> list[DiseaseProfile]:
        """여러 키워드 중 하나라도 매칭되는 질환 검색."""
        self._ensure_loaded()
        matched_keys = set()
        for kw in keywords:
            matched_keys.update(self._keyword_index.get(kw.lower().strip(), []))
        return [self._profiles[k] for k in matched_keys if k in self._profiles]

    def search_by_hpo(self, hpo_id: str) -> list[DiseaseProfile]:
        self._ensure_loaded()
        keys = self._hpo_index.get(hpo_id, [])
        return [self._profiles[k] for k in keys if k in self._profiles]

    def count_diseases_with_hpo(self, hpo_id: str) -> int:
        """특정 HPO를 가진 질환 수 (Information Content 계산용)."""
        self._ensure_loaded()
        return len(self._hpo_index.get(hpo_id, []))

    def get_diseases_with_genes(self) -> list[DiseaseProfile]:
        """유전자 정보가 있는 질환만 반환."""
        self._ensure_loaded()
        return [p for p in self._profiles.values() if p.major_genes]

    def get_all_unique_hpo_ids(self) -> set[str]:
        self._ensure_loaded()
        return set(self._hpo_index.keys())

    def get_all_unique_keywords(self) -> set[str]:
        self._ensure_loaded()
        return set(self._keyword_index.keys())

    def resolve_yaml_key(self, yaml_key: str) -> str:
        """YAML disease_key를 실제 profile key로 변환.

        YAML disease_associations의 disease_key(예: 'community_acquired_pneumonia')
        가 Excel 병합으로 다른 키(예: 'bacterial_pneumonia_nec')로 저장된 경우
        실제 키를 반환한다. 매핑이 없으면 원본 키를 그대로 반환.
        """
        self._ensure_loaded()
        return self._yaml_key_map.get(yaml_key, yaml_key)

    @property
    def yaml_key_map(self) -> dict[str, str]:
        self._ensure_loaded()
        return dict(self._yaml_key_map)

    # ── 유틸 ──────────────────────────────────────────────────
    @staticmethod
    def _load_yaml(path: str) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def summary(self) -> dict[str, int]:
        """레지스트리 요약 통계."""
        self._ensure_loaded()
        return {
            "total": len(self._profiles),
            "common": sum(1 for p in self._profiles.values() if p.category == DiseaseCategory.COMMON),
            "other": sum(1 for p in self._profiles.values() if p.category == DiseaseCategory.OTHER),
            "rare": sum(1 for p in self._profiles.values() if p.category == DiseaseCategory.RARE),
            "yaml_enriched": sum(1 for p in self._profiles.values() if p.category == DiseaseCategory.YAML_PROFILE),
            "with_ai_keywords": sum(1 for p in self._profiles.values() if p.ai_imaging_keywords),
            "with_lab_patterns": sum(1 for p in self._profiles.values() if p.lab_patterns),
            "with_genes": sum(1 for p in self._profiles.values() if p.major_genes),
            "unique_hpo_ids": len(self._hpo_index),
            "unique_keywords": len(self._keyword_index),
        }
