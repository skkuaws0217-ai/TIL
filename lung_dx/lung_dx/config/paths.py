"""파일 경로 상수 및 MIMIC-IV ItemID 정의."""

from pathlib import Path

# ── 프로젝트 루트 ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # lung_disease_lab_data/
DATA_ORIGIN = PROJECT_ROOT / "MIMIC-IV data_origin"
DATA_DIR = DATA_ORIGIN / "data"

# ── YAML Reference 파일 ───────────────────────────────────────
LAB_REFERENCE_YAML = DATA_DIR / "lab_reference_ranges_v3.yaml"
VITALS_REFERENCE_YAML = DATA_DIR / "vitals_respiratory_hemodynamic_reference_range_v1.yaml"
DISEASE_PROFILES_YAML = DATA_DIR / "lung_disease_profiles_v2.yaml"
DISEASE_SYMPTOMS_YAML = DATA_DIR / "lung_disease_symptoms_v2.yaml"

# ── Excel Disease Databases ───────────────────────────────────
COMMON_DISEASE_XLSX = DATA_DIR / "일반_폐질환_데이터베이스_v4.xlsx"
OTHER_DISEASE_XLSX = DATA_DIR / "기타_폐관련_질환_데이터베이스_v4.xlsx"
RARE_DISEASE_XLSX = DATA_DIR / "희귀_폐질환_데이터베이스_v4.xlsx"

# ── MIMIC-IV 환자 측정값 CSV ──────────────────────────────────
# 항목 정의·reference range는 위 2개 YAML에 이미 완비되어 있음.
# d_items.csv, d_labitems.csv 등 MIMIC lookup 테이블은 사용하지 않음.
# CSV는 순수하게 환자 측정값 데이터 소스로만 활용.
CHARTEVENTS_CSV = DATA_ORIGIN / "chartevents.csv"
LABEVENTS_CSV = DATA_ORIGIN / "labevents.csv"

# ── 캐시 / 출력 디렉토리 ──────────────────────────────────────
CACHE_DIR = PROJECT_ROOT / "lung_dx" / "cache"
PARQUET_DIR = CACHE_DIR / "parquets"
MODEL_DIR = CACHE_DIR / "models"
REPORT_DIR = PROJECT_ROOT / "reports"

# ── CheXpert 14 Labels (Stanford CheXpert) ────────────────────
CHEXPERT_LABELS = [
    "Atelectasis",
    "Cardiomegaly",
    "Consolidation",
    "Edema",
    "Enlarged Cardiomediastinum",
    "Fracture",
    "Lung Lesion",
    "Lung Opacity",
    "No Finding",
    "Pleural Effusion",
    "Pleural Other",
    "Pneumonia",
    "Pneumothorax",
    "Support Devices",
]

# ── ItemID 목록 ──────────────────────────────────────────────
# Lab ItemID:   lab_reference_ranges_v3.yaml에서 동적 로드
#               89개 (MIMIC-IV 53개 + 외부 EXT_A~EXT_AJ 36개)
#               미생물(J카테고리) 10개 항목 포함
# Vitals/Respiratory/Hemodynamic ItemID:
#               vitals_respiratory_hemodynamic_reference_range_v1.yaml
#               에서 동적 로드 (37개)

# ── 미생물 참조 ──────────────────────────────────────────────
# micro 관련 reference range 및 threshold는
# lab_reference_ranges_v3.yaml의 J_Infection_Microbiology 카테고리
# (10개 항목: Blood Culture, Sputum Culture, AFB, TB-PCR, IGRA,
#  Aspergillus GM, Beta-D-Glucan, COVID-19 RT-PCR/Rapid PCR/Ag)
# + Excel DB "미생물 소견" 컬럼 + YAML micro_findings에서 참조
# (microbiologyevents.csv는 사용하지 않음)
