"""Diagnostic pipeline orchestrator -- assemble patient data and run scoring.

Takes a (subject_id, hadm_id) pair, loads all relevant data from parquet
files, runs all processors, and produces a complete diagnostic result.

This module ties together:
    - data_loaders.base_loader (parquet I/O)
    - processors.lab_interpreter (lab value interpretation)
    - processors.radiology_nlp (radiology report NLP)
    - processors.micro_interpreter (microbiology classification)
    - processors.vitals_processor (ICU vitals summarisation)
    - knowledge.icd_disease_matcher (disease scoring)
    - knowledge.rare_disease_matcher (rare disease assessment)

Reference Sources:
    - Clinical workflow modeled after Harrison's 21st Ed, Ch.1
      "Approach to the Patient: History and Physical Examination"
    - Diagnostic reasoning approach: Stern et al. "Symptom to Diagnosis"
      (McGraw-Hill, 4th Ed 2020)
"""

import os
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any

import pandas as pd

from config.paths import (
    LUNG_LABS_PARQUET,
    CHEST_RADIOLOGY_PARQUET,
    DISCHARGE_CC_PARQUET,
    RESPIRATORY_MICRO_PARQUET,
    RESPIRATORY_VITALS_PARQUET,
    LUNG_COHORT_PARQUET,
    DIAGNOSES_ICD_GZ,
    OUTPUT_DIR,
)
from data_loaders.base_loader import load_parquet, load_small_csv
from processors.lab_interpreter import interpret_lab_dataframe
from processors.radiology_nlp import parse_radiology_report, get_finding_summary
from processors.micro_interpreter import interpret_micro_dataframe
from processors.vitals_processor import summarize_patient_vitals
from knowledge.icd_disease_matcher import (
    DiagnosticScorer, PatientRecord, DiseaseScore,
)
from knowledge.rare_disease_matcher import (
    RareDiseaseMatcher, RareDiseaseResult,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Diagnostic Result Data Class
# ═══════════════════════════════════════════════════════════════

@dataclass
class DiagnosticResult:
    """Complete diagnostic output for a patient admission.

    Contains all processor outputs and scoring results.
    """
    # Patient identifiers
    subject_id: int = 0
    hadm_id: int = 0

    # Demographics (from admissions/patients if available)
    demographics: Dict[str, Any] = field(default_factory=dict)

    # Assembled patient record
    patient_record: Optional[PatientRecord] = None

    # Processor outputs (raw)
    lab_data: Optional[pd.DataFrame] = None
    radiology_data: Optional[pd.DataFrame] = None
    radiology_parsed: List[Dict[str, Any]] = field(default_factory=list)
    micro_data: Optional[pd.DataFrame] = None
    vitals_summary: Dict[str, Any] = field(default_factory=dict)
    chief_complaint: str = ""
    symptoms_present: List[str] = field(default_factory=list)
    symptoms_denied: List[str] = field(default_factory=list)

    # Disease scoring
    ranked_diseases: List[DiseaseScore] = field(default_factory=list)
    validation: Dict[str, Any] = field(default_factory=dict)

    # Rare disease assessment
    rare_disease_result: Optional[RareDiseaseResult] = None
    rare_disease_triggered: bool = False

    # Actual diagnoses
    actual_icd_codes: List[str] = field(default_factory=list)

    # Status
    status: str = "pending"
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# Diagnostic Engine
# ═══════════════════════════════════════════════════════════════

class DiagnosticEngine:
    """Full diagnostic pipeline orchestrator.

    Usage:
        engine = DiagnosticEngine()
        result = engine.run(subject_id=12345, hadm_id=67890)
    """

    def __init__(self,
                 scorer: Optional[DiagnosticScorer] = None,
                 rare_matcher: Optional[RareDiseaseMatcher] = None,
                 rare_disease_threshold: float = 0.5):
        """Initialize the diagnostic engine.

        Args:
            scorer: Pre-initialized DiagnosticScorer. Created if None.
            rare_matcher: Pre-initialized RareDiseaseMatcher. Created if None.
            rare_disease_threshold: If max disease score < this threshold,
                trigger rare disease workup. Ref: clinical decision support
                cutoff for atypical presentations.
        """
        self.scorer = scorer or DiagnosticScorer()
        self.rare_matcher = rare_matcher or RareDiseaseMatcher()
        self.rare_disease_threshold = rare_disease_threshold

        # Cached dataframes (lazy loaded)
        self._labs_df: Optional[pd.DataFrame] = None
        self._radiology_df: Optional[pd.DataFrame] = None
        self._cc_df: Optional[pd.DataFrame] = None
        self._micro_df: Optional[pd.DataFrame] = None
        self._vitals_df: Optional[pd.DataFrame] = None
        self._cohort_df: Optional[pd.DataFrame] = None
        self._diagnoses_df: Optional[pd.DataFrame] = None

    # ─── Public API ──────────────────────────────────────────────

    def run(self, subject_id: int, hadm_id: int, top_n: int = 5) -> DiagnosticResult:
        """Run the full diagnostic pipeline for a patient admission.

        Args:
            subject_id: MIMIC subject_id.
            hadm_id: MIMIC hadm_id (hospital admission).
            top_n: Number of top disease candidates to return.

        Returns:
            DiagnosticResult with all findings and scored diseases.
        """
        result = DiagnosticResult(
            subject_id=subject_id,
            hadm_id=hadm_id,
        )

        logger.info("Running diagnostic pipeline for subject=%d, hadm=%d",
                     subject_id, hadm_id)

        # Step 1: Load and filter all data sources
        self._load_patient_data(result)

        # Step 2: Run processors
        self._process_labs(result)
        self._process_radiology(result)
        self._process_chief_complaint(result)
        self._process_microbiology(result)
        self._process_vitals(result)

        # Step 3: Assemble PatientRecord
        self._assemble_patient_record(result)

        # Step 4: Run disease scoring
        self._score_diseases(result, top_n=top_n)

        # Step 5: Validate against actual diagnoses
        self._validate_diagnoses(result)

        # Step 6: Rare disease assessment (if triggered)
        self._assess_rare_diseases(result)

        result.status = "complete"
        logger.info("Diagnostic pipeline complete for subject=%d, hadm=%d. "
                     "Top disease: %s (score=%.3f)",
                     subject_id, hadm_id,
                     result.ranked_diseases[0].disease if result.ranked_diseases else "none",
                     result.ranked_diseases[0].score if result.ranked_diseases else 0.0)

        return result

    # ─── Data Loading ────────────────────────────────────────────

    def _load_dataframe(self, path: str, name: str) -> Optional[pd.DataFrame]:
        """Safely load a parquet file."""
        if not os.path.exists(path):
            logger.warning("%s not found: %s", name, path)
            return None
        try:
            return load_parquet(path)
        except Exception as exc:
            logger.error("Failed to load %s: %s", name, exc)
            return None

    def _load_patient_data(self, result: DiagnosticResult) -> None:
        """Load all data sources, filtering to the patient."""
        sid = result.subject_id
        hid = result.hadm_id

        # Labs
        if self._labs_df is None:
            self._labs_df = self._load_dataframe(LUNG_LABS_PARQUET, "lung_labs")
        if self._labs_df is not None:
            mask = (self._labs_df["subject_id"] == sid) & (self._labs_df["hadm_id"] == hid)
            result.lab_data = self._labs_df[mask].copy()
        else:
            result.warnings.append("Lab data not available")

        # Radiology
        if self._radiology_df is None:
            self._radiology_df = self._load_dataframe(
                CHEST_RADIOLOGY_PARQUET, "chest_radiology"
            )
        if self._radiology_df is not None:
            mask = self._radiology_df["subject_id"] == sid
            if "hadm_id" in self._radiology_df.columns:
                mask = mask & (self._radiology_df["hadm_id"] == hid)
            result.radiology_data = self._radiology_df[mask].copy()
        else:
            result.warnings.append("Radiology data not available")

        # Chief complaint / discharge
        if self._cc_df is None:
            self._cc_df = self._load_dataframe(
                DISCHARGE_CC_PARQUET, "discharge_chief_complaints"
            )
        if self._cc_df is not None:
            mask = (self._cc_df["subject_id"] == sid) & (self._cc_df["hadm_id"] == hid)
            cc_rows = self._cc_df[mask]
            if not cc_rows.empty:
                row = cc_rows.iloc[0]
                result.chief_complaint = str(row.get("chief_complaint", ""))
                sp = str(row.get("symptoms_present", ""))
                sd = str(row.get("symptoms_denied", ""))
                result.symptoms_present = [s for s in sp.split("|") if s]
                result.symptoms_denied = [s for s in sd.split("|") if s]
        else:
            result.warnings.append("Discharge CC data not available")

        # Microbiology
        if self._micro_df is None:
            self._micro_df = self._load_dataframe(
                RESPIRATORY_MICRO_PARQUET, "respiratory_micro"
            )
        if self._micro_df is not None:
            mask = (self._micro_df["subject_id"] == sid) & (self._micro_df["hadm_id"] == hid)
            result.micro_data = self._micro_df[mask].copy()
        else:
            result.warnings.append("Microbiology data not available")

        # Vitals
        if self._vitals_df is None:
            self._vitals_df = self._load_dataframe(
                RESPIRATORY_VITALS_PARQUET, "respiratory_vitals"
            )
        if self._vitals_df is not None:
            mask = (self._vitals_df["subject_id"] == sid) & (self._vitals_df["hadm_id"] == hid)
            vitals_rows = self._vitals_df[mask]
            if not vitals_rows.empty:
                result.vitals_summary = summarize_patient_vitals(vitals_rows)
        else:
            result.warnings.append("Vitals data not available")

        # Actual ICD diagnoses for validation
        self._load_actual_diagnoses(result)

    def _load_actual_diagnoses(self, result: DiagnosticResult) -> None:
        """Load actual ICD diagnoses for the admission."""
        if self._diagnoses_df is None:
            try:
                if os.path.exists(DIAGNOSES_ICD_GZ):
                    self._diagnoses_df = load_small_csv(
                        DIAGNOSES_ICD_GZ,
                        usecols=["subject_id", "hadm_id", "icd_code", "icd_version"],
                    )
                else:
                    logger.warning("Diagnoses ICD file not found: %s", DIAGNOSES_ICD_GZ)
                    return
            except Exception as exc:
                logger.error("Failed to load diagnoses: %s", exc)
                return

        if self._diagnoses_df is not None:
            mask = (
                (self._diagnoses_df["subject_id"] == result.subject_id) &
                (self._diagnoses_df["hadm_id"] == result.hadm_id)
            )
            patient_diag = self._diagnoses_df[mask]
            result.actual_icd_codes = patient_diag["icd_code"].dropna().tolist()

    # ─── Processor Steps ─────────────────────────────────────────

    def _process_labs(self, result: DiagnosticResult) -> None:
        """Run lab interpreter on patient lab data."""
        if result.lab_data is None or result.lab_data.empty:
            return
        try:
            interpreted = interpret_lab_dataframe(result.lab_data)
            result.lab_data = interpreted
        except Exception as exc:
            result.errors.append(f"Lab interpretation error: {exc}")
            logger.error("Lab interpretation failed: %s", exc)

    def _process_radiology(self, result: DiagnosticResult) -> None:
        """Run radiology NLP on patient radiology reports."""
        if result.radiology_data is None or result.radiology_data.empty:
            return
        try:
            text_col = "text" if "text" in result.radiology_data.columns else None
            if text_col is None:
                for col in result.radiology_data.columns:
                    if "text" in col.lower() or "note" in col.lower():
                        text_col = col
                        break
            if text_col is None:
                result.warnings.append("No text column found in radiology data")
                return

            parsed_reports = []
            for _, row in result.radiology_data.iterrows():
                text = str(row.get(text_col, ""))
                if text and text.lower() not in ("nan", "none", ""):
                    parsed = parse_radiology_report(text)
                    parsed_reports.append(parsed)

            result.radiology_parsed = parsed_reports
        except Exception as exc:
            result.errors.append(f"Radiology NLP error: {exc}")
            logger.error("Radiology NLP failed: %s", exc)

    def _process_chief_complaint(self, result: DiagnosticResult) -> None:
        """Chief complaint already extracted during data loading."""
        # Already handled in _load_patient_data
        pass

    def _process_microbiology(self, result: DiagnosticResult) -> None:
        """Run microbiology interpreter on patient culture data."""
        if result.micro_data is None or result.micro_data.empty:
            return
        try:
            interpreted = interpret_micro_dataframe(result.micro_data)
            result.micro_data = interpreted
        except Exception as exc:
            result.errors.append(f"Microbiology interpretation error: {exc}")
            logger.error("Microbiology interpretation failed: %s", exc)

    def _process_vitals(self, result: DiagnosticResult) -> None:
        """Vitals already summarised during data loading."""
        # Already handled in _load_patient_data
        pass

    # ─── Patient Record Assembly ─────────────────────────────────

    def _assemble_patient_record(self, result: DiagnosticResult) -> None:
        """Assemble a PatientRecord from all processor outputs."""
        record = PatientRecord(
            subject_id=result.subject_id,
            hadm_id=result.hadm_id,
            chief_complaint=result.chief_complaint,
            symptoms_present=list(result.symptoms_present),
            symptoms_denied=list(result.symptoms_denied),
            vitals_summary=result.vitals_summary,
            icd_diagnoses=result.actual_icd_codes,
        )

        # Lab interpretations
        if result.lab_data is not None and "medical_term" in result.lab_data.columns:
            terms = result.lab_data["medical_term"].dropna().unique().tolist()
            record.lab_interpretations = [
                t for t in terms
                if t and t.lower() not in ("", "within reference range", "normal")
            ]
            record.lab_details = result.lab_data.to_dict("records")

        # Radiology findings
        for parsed in result.radiology_parsed:
            for f in parsed.get("positive_findings", []):
                finding_name = f.get("finding", "")
                if finding_name:
                    record.radiology_findings.append(finding_name)
            record.radiology_details.append(parsed)

        # Microbiology organisms
        if result.micro_data is not None and "organism" in result.micro_data.columns:
            organisms = result.micro_data["organism"].dropna().unique().tolist()
            record.micro_findings = [o for o in organisms if o]
            record.micro_details = result.micro_data.to_dict("records")

        result.patient_record = record

    # ─── Disease Scoring ─────────────────────────────────────────

    def _score_diseases(self, result: DiagnosticResult, top_n: int = 5) -> None:
        """Run disease scoring on the assembled patient record."""
        if result.patient_record is None:
            result.errors.append("No patient record assembled -- cannot score")
            return
        try:
            result.ranked_diseases = self.scorer.score_patient(
                result.patient_record, top_n=top_n
            )
        except Exception as exc:
            result.errors.append(f"Disease scoring error: {exc}")
            logger.error("Disease scoring failed: %s", exc)

    def _validate_diagnoses(self, result: DiagnosticResult) -> None:
        """Cross-reference predicted diseases with actual ICD codes."""
        if not result.ranked_diseases or not result.actual_icd_codes:
            return
        try:
            result.validation = self.scorer.validate_against_icd(
                result.ranked_diseases, result.actual_icd_codes
            )
        except Exception as exc:
            result.warnings.append(f"Validation error: {exc}")
            logger.error("Diagnosis validation failed: %s", exc)

    # ─── Rare Disease Assessment ─────────────────────────────────

    def _assess_rare_diseases(self, result: DiagnosticResult) -> None:
        """Run rare disease matcher if triggered.

        Triggers:
            1. Max disease score < rare_disease_threshold (low confidence)
            2. Rare disease trigger findings are present
        """
        if result.patient_record is None:
            return

        pr = result.patient_record

        # Check trigger conditions
        max_score = 0.0
        if result.ranked_diseases:
            max_score = result.ranked_diseases[0].score

        low_confidence = max_score < self.rare_disease_threshold

        # Check for rare disease trigger findings
        triggered, trigger_reasons = self.rare_matcher.should_trigger_rare_workup(
            pr.symptoms_present, pr.lab_interpretations, pr.radiology_findings
        )

        if low_confidence or triggered:
            result.rare_disease_triggered = True
            logger.info("Rare disease workup triggered. Low confidence=%s, "
                        "trigger findings=%s", low_confidence, triggered)

            try:
                result.rare_disease_result = self.rare_matcher.assess_patient(
                    symptoms=pr.symptoms_present,
                    lab_terms=pr.lab_interpretations,
                    radiology_findings=pr.radiology_findings,
                    micro_findings=pr.micro_findings,
                )
            except Exception as exc:
                result.errors.append(f"Rare disease assessment error: {exc}")
                logger.error("Rare disease assessment failed: %s", exc)
