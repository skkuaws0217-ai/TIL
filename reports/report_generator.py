"""Diagnostic report generator -- structured and markdown output.

Takes a DiagnosticResult from the pipeline and generates comprehensive
clinical reports in both dict (JSON-serializable) and markdown formats.

Report sections modeled after standard medical documentation:
    Ref: Bates' Guide to Physical Examination (13th Ed, 2022)
    Ref: Joint Commission requirements for medical record documentation

Output follows clinical presentation order:
    1. Patient Demographics
    2. Chief Complaint
    3. Clinical Findings (symptoms present / denied)
    4. Lab Interpretation
    5. Radiology Findings
    6. Microbiology Results
    7. ICU Respiratory Status
    8. Suspected Diagnoses
    9. Rare Disease Assessment
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


def generate_report_dict(diagnostic_result) -> Dict[str, Any]:
    """Generate a JSON-serializable dict report from DiagnosticResult.

    Args:
        diagnostic_result: A DiagnosticResult instance from DiagnosticEngine.

    Returns:
        Dict with all report sections, suitable for JSON serialization.
    """
    dr = diagnostic_result
    pr = dr.patient_record

    report: Dict[str, Any] = {
        "report_metadata": {
            "generated_at": datetime.now().isoformat(),
            "report_version": "1.0",
            "status": dr.status,
            "warnings": dr.warnings,
            "errors": dr.errors,
        },
    }

    # ─── 1. Patient Demographics ───
    report["patient_demographics"] = {
        "subject_id": dr.subject_id,
        "hadm_id": dr.hadm_id,
        **dr.demographics,
    }

    # ─── 2. Chief Complaint ───
    report["chief_complaint"] = {
        "text": dr.chief_complaint if dr.chief_complaint else "Not available",
    }

    # ─── 3. Clinical Findings ───
    report["clinical_findings"] = {
        "symptoms_present": pr.symptoms_present if pr else [],
        "symptoms_denied": pr.symptoms_denied if pr else [],
        "symptom_count": len(pr.symptoms_present) if pr else 0,
    }

    # ─── 4. Lab Interpretation ───
    report["lab_interpretation"] = _build_lab_section(dr)

    # ─── 5. Radiology Findings ───
    report["radiology_findings"] = _build_radiology_section(dr)

    # ─── 6. Microbiology Results ───
    report["microbiology_results"] = _build_micro_section(dr)

    # ─── 7. ICU Respiratory Status ───
    report["icu_respiratory_status"] = _build_vitals_section(dr)

    # ─── 8. Suspected Diagnoses ───
    report["suspected_diagnoses"] = _build_diagnosis_section(dr)

    # ─── 9. Rare Disease Assessment ───
    report["rare_disease_assessment"] = _build_rare_disease_section(dr)

    # ─── 10. Validation (if available) ───
    if dr.validation:
        report["diagnosis_validation"] = dr.validation

    return report


def generate_report_markdown(diagnostic_result) -> str:
    """Generate a markdown text report from DiagnosticResult.

    Args:
        diagnostic_result: A DiagnosticResult instance from DiagnosticEngine.

    Returns:
        String containing the full report in markdown format.
    """
    dr = diagnostic_result
    pr = dr.patient_record
    lines: List[str] = []

    # Title
    lines.append(f"# Diagnostic Report")
    lines.append(f"**Subject ID:** {dr.subject_id}  ")
    lines.append(f"**Admission ID:** {dr.hadm_id}  ")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  ")
    lines.append(f"**Status:** {dr.status}")
    lines.append("")

    if dr.warnings:
        lines.append("**Warnings:**")
        for w in dr.warnings:
            lines.append(f"- {w}")
        lines.append("")

    # ─── 1. Chief Complaint ───
    lines.append("## Chief Complaint")
    lines.append(dr.chief_complaint if dr.chief_complaint else "_Not available_")
    lines.append("")

    # ─── 2. Clinical Findings ───
    lines.append("## Clinical Findings")
    if pr and pr.symptoms_present:
        lines.append("**Symptoms Present:**")
        for s in pr.symptoms_present:
            lines.append(f"- {s}")
    else:
        lines.append("_No symptoms extracted_")
    lines.append("")

    if pr and pr.symptoms_denied:
        lines.append("**Symptoms Denied:**")
        for s in pr.symptoms_denied:
            lines.append(f"- {s}")
        lines.append("")

    # ─── 3. Lab Interpretation ───
    lines.append("## Lab Interpretation")
    lab_section = _build_lab_section(dr)
    if lab_section.get("abnormal_results"):
        lines.append("| Lab Test | Value | Interpretation | Medical Term | Severity | Memo |")
        lines.append("|----------|-------|---------------|--------------|----------|------|")
        for lab in lab_section["abnormal_results"]:
            lines.append(
                f"| {lab.get('name', '')} "
                f"| {lab.get('value', '')} "
                f"| {lab.get('interpretation', '')} "
                f"| {lab.get('medical_term', '')} "
                f"| {lab.get('severity', '')} "
                f"| {lab.get('memo', '')} |"
            )
    else:
        lines.append("_No abnormal lab results_")
    lines.append("")

    if lab_section.get("reference_source_summary"):
        lines.append(f"**Reference sources:** {lab_section['reference_source_summary']}")
        lines.append("")

    # ─── 4. Radiology Findings ───
    lines.append("## Radiology Findings")
    rad_section = _build_radiology_section(dr)
    if rad_section.get("positive_findings"):
        lines.append("**Positive Findings:**")
        for f in rad_section["positive_findings"]:
            loc = f" ({f['location']})" if f.get("location") else ""
            lines.append(f"- **{f['finding']}** [{f['category']}]{loc}")
    else:
        lines.append("_No positive findings_")
    lines.append("")

    if rad_section.get("negative_findings"):
        lines.append("**Excluded Findings:**")
        for f in rad_section["negative_findings"]:
            lines.append(f"- ~~{f['finding']}~~ (negated)")
        lines.append("")

    if rad_section.get("impression"):
        lines.append(f"**Impression:** {rad_section['impression']}")
        lines.append("")

    # ─── 5. Microbiology Results ───
    lines.append("## Microbiology Results")
    micro_section = _build_micro_section(dr)
    if micro_section.get("organisms"):
        for org in micro_section["organisms"]:
            mdr_flag = " **[MDR]**" if org.get("is_mdr") else ""
            lines.append(
                f"- **{org['organism']}** ({org.get('category', 'unknown')}){mdr_flag}"
            )
            if org.get("clinical_significance"):
                lines.append(f"  - Significance: {org['clinical_significance']}")
            if org.get("resistant_antibiotics"):
                lines.append(f"  - Resistant to: {org['resistant_antibiotics']}")
            if org.get("susceptible_antibiotics"):
                lines.append(f"  - Susceptible to: {org['susceptible_antibiotics']}")
    else:
        lines.append("_No microbiology results_")
    lines.append("")

    # ─── 6. ICU Respiratory Status ───
    lines.append("## ICU Respiratory Status")
    vitals_section = _build_vitals_section(dr)
    if vitals_section.get("has_data"):
        rr = vitals_section.get("respiratory_rate", {})
        spo2 = vitals_section.get("spo2", {})
        derived = vitals_section.get("derived", {})

        lines.append(f"| Parameter | Latest | Range | Abnormal |")
        lines.append(f"|-----------|--------|-------|----------|")

        if rr:
            lines.append(
                f"| Respiratory Rate | {rr.get('latest', 'N/A')} "
                f"| {rr.get('min', '')}-{rr.get('max', '')} "
                f"| {'Yes' if rr.get('abnormal_flag') else 'No'} |"
            )
        if spo2:
            lines.append(
                f"| SpO2 | {spo2.get('latest', 'N/A')}% "
                f"| {spo2.get('min', '')}-{spo2.get('max', '')}% "
                f"| {'Yes' if spo2.get('abnormal_flag') else 'No'} |"
            )

        fio2_max = vitals_section.get("fio2_max")
        if fio2_max is not None:
            lines.append(f"| FiO2 (max) | {fio2_max} | - | - |")
        peep_max = vitals_section.get("peep_max")
        if peep_max is not None:
            lines.append(f"| PEEP (max) | {peep_max} | - | - |")

        lines.append("")

        if derived:
            sf = derived.get("spo2_fio2_ratio")
            if sf:
                lines.append(f"**SpO2/FiO2 Ratio:** {sf} (ARDS category: {derived.get('ards_category', 'N/A')})")
            if derived.get("ventilator_dependence"):
                lines.append("**Ventilator Dependent:** Yes")
            if derived.get("respiratory_distress"):
                lines.append(f"**Respiratory Distress:** {derived.get('respiratory_distress_detail', 'Yes')}")
    else:
        lines.append("_No ICU vitals data available_")
    lines.append("")

    # ─── 7. Suspected Diagnoses ───
    lines.append("## Suspected Diagnoses")
    diag_section = _build_diagnosis_section(dr)
    if diag_section.get("diseases"):
        lines.append("| Rank | Disease | ICD-10 | Score | Confidence | Evidence |")
        lines.append("|------|---------|--------|-------|------------|----------|")
        for i, d in enumerate(diag_section["diseases"], 1):
            icd_str = ", ".join(d.get("icd10_codes", [])[:3])
            evidence_str = f"{d.get('matched_count', 0)}/{d.get('total_criteria', 0)} criteria"
            lines.append(
                f"| {i} | {d['disease']} | {icd_str} "
                f"| {d['score']:.3f} | {d['confidence']} "
                f"| {evidence_str} |"
            )
    else:
        lines.append("_No diseases scored_")
    lines.append("")

    # ─── 8. Rare Disease Assessment ───
    if dr.rare_disease_triggered:
        lines.append("## Rare Disease Assessment")
        rare_section = _build_rare_disease_section(dr)
        if rare_section.get("triggered"):
            lines.append("**Rare disease workup triggered.**")
            if rare_section.get("trigger_reasons"):
                lines.append("**Trigger reasons:**")
                for r in rare_section["trigger_reasons"]:
                    lines.append(f"- {r}")
            lines.append("")

            if rare_section.get("matches"):
                lines.append("| Disease | ORPHA | Score | Matched HPO | Genes |")
                lines.append("|---------|-------|-------|-------------|-------|")
                for m in rare_section["matches"][:5]:
                    genes_str = ", ".join(m.get("gene_panel", [])[:3])
                    lines.append(
                        f"| {m['disease_name']} | {m['orpha_code']} "
                        f"| {m['score']:.3f} "
                        f"| {len(m.get('matched_hpo', []))}/{m.get('total_hpo', 0)} "
                        f"| {genes_str} |"
                    )
                lines.append("")

            if rare_section.get("recommended_gene_panel"):
                lines.append(
                    f"**Recommended Gene Panel:** "
                    f"{', '.join(rare_section['recommended_gene_panel'][:10])}"
                )
        lines.append("")

    # ─── Validation ───
    if dr.validation:
        lines.append("## Diagnosis Validation")
        v = dr.validation
        lines.append(f"**Actual ICD Codes:** {', '.join(v.get('actual_icd_codes', []))}")
        lines.append(f"**Hits (correctly predicted):** {', '.join(v.get('hits', []))}")
        lines.append(f"**Misses:** {', '.join(v.get('misses', []))}")
        lines.append(f"**Sensitivity:** {v.get('sensitivity', 0):.2f}")
        lines.append(f"**Precision:** {v.get('precision', 0):.2f}")
        lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Section Builders (shared by dict and markdown generators)
# ═══════════════════════════════════════════════════════════════

def _build_lab_section(dr) -> Dict[str, Any]:
    """Build the lab interpretation section."""
    section: Dict[str, Any] = {
        "abnormal_results": [],
        "normal_count": 0,
        "abnormal_count": 0,
        "critical_count": 0,
        "reference_source_summary": "",
    }

    if dr.lab_data is None or (hasattr(dr.lab_data, 'empty') and dr.lab_data.empty):
        return section

    df = dr.lab_data
    ref_sources = set()

    if "severity" not in df.columns:
        return section

    for _, row in df.iterrows():
        severity = str(row.get("severity", ""))
        ref_src = str(row.get("reference_source", ""))
        if ref_src and ref_src not in ("", "nan"):
            ref_sources.add(ref_src)

        if severity == "normal":
            section["normal_count"] += 1
            continue

        if "critical" in severity:
            section["critical_count"] += 1

        section["abnormal_count"] += 1

        # Get lab name
        lab_name = ""
        from processors.lab_interpreter import LAB_MEDICAL_TERMS
        itemid = int(row.get("itemid", 0))
        if itemid in LAB_MEDICAL_TERMS:
            lab_name = LAB_MEDICAL_TERMS[itemid].get("name", "")

        section["abnormal_results"].append({
            "name": lab_name or str(row.get("label", f"itemid={itemid}")),
            "value": str(row.get("valuenum", row.get("value", ""))),
            "interpretation": str(row.get("interpretation", "")),
            "medical_term": str(row.get("medical_term", "")),
            "severity": severity,
            "memo": str(row.get("memo", "")),
            "reference_source": ref_src,
        })

    section["reference_source_summary"] = ", ".join(sorted(ref_sources))
    return section


def _build_radiology_section(dr) -> Dict[str, Any]:
    """Build the radiology findings section."""
    section: Dict[str, Any] = {
        "report_count": 0,
        "positive_findings": [],
        "negative_findings": [],
        "impression": "",
        "finding_summary": "",
    }

    if not dr.radiology_parsed:
        return section

    section["report_count"] = len(dr.radiology_parsed)

    all_positive = []
    all_negative = []
    impressions = []

    for parsed in dr.radiology_parsed:
        for f in parsed.get("positive_findings", []):
            all_positive.append({
                "finding": f.get("finding", ""),
                "category": f.get("category", ""),
                "location": f.get("location", ""),
                "weight": f.get("weight", 0),
            })
        for f in parsed.get("negative_findings", []):
            all_negative.append({
                "finding": f.get("finding", ""),
                "category": f.get("category", ""),
            })
        imp = parsed.get("impression", "")
        if imp:
            impressions.append(imp)

    # Deduplicate positive findings by name
    seen = set()
    for f in all_positive:
        key = f["finding"]
        if key not in seen:
            seen.add(key)
            section["positive_findings"].append(f)

    seen = set()
    for f in all_negative:
        key = f["finding"]
        if key not in seen:
            seen.add(key)
            section["negative_findings"].append(f)

    section["impression"] = " | ".join(impressions) if impressions else ""
    section["finding_summary"] = "; ".join(
        f["finding"] for f in section["positive_findings"]
    ) or "No significant findings"

    return section


def _build_micro_section(dr) -> Dict[str, Any]:
    """Build the microbiology results section."""
    section: Dict[str, Any] = {
        "organisms": [],
        "mdr_count": 0,
        "has_data": False,
    }

    if dr.micro_data is None or (hasattr(dr.micro_data, 'empty') and dr.micro_data.empty):
        return section

    section["has_data"] = True

    for _, row in dr.micro_data.iterrows():
        org_name = str(row.get("organism", ""))
        if not org_name or org_name.lower() in ("", "nan", "none"):
            continue

        is_mdr = bool(row.get("is_mdr", False))
        if is_mdr:
            section["mdr_count"] += 1

        section["organisms"].append({
            "organism": org_name,
            "category": str(row.get("category", "unknown")),
            "clinical_significance": str(row.get("clinical_significance", "")),
            "specimen_type": str(row.get("specimen_type", "")),
            "is_mdr": is_mdr,
            "resistant_antibiotics": str(row.get("resistant_antibiotics", "")),
            "susceptible_antibiotics": str(row.get("susceptible_antibiotics", "")),
            "reference": str(row.get("reference", "")),
        })

    return section


def _build_vitals_section(dr) -> Dict[str, Any]:
    """Build the ICU respiratory status section."""
    section: Dict[str, Any] = {
        "has_data": False,
    }

    if not dr.vitals_summary or dr.vitals_summary.get("status") == "no_data":
        return section

    section["has_data"] = True
    vs = dr.vitals_summary

    section["respiratory_rate"] = vs.get("respiratory_rate", {})
    section["spo2"] = vs.get("spo2", {})
    section["fio2_max"] = vs.get("fio2", {}).get("max")
    section["peep_max"] = vs.get("peep", {}).get("max")
    section["tidal_volume_mean"] = vs.get("tidal_volume", {}).get("mean")
    section["lung_sounds"] = vs.get("lung_sounds", {})
    section["ventilator_mode"] = vs.get("ventilator_mode", {})
    section["derived"] = vs.get("derived", {})

    return section


def _build_diagnosis_section(dr) -> Dict[str, Any]:
    """Build the suspected diagnoses section."""
    section: Dict[str, Any] = {
        "diseases": [],
        "top_disease": "",
        "top_score": 0.0,
        "top_confidence": "",
    }

    if not dr.ranked_diseases:
        return section

    for ds in dr.ranked_diseases:
        # Collect supporting evidence summary
        supporting = [
            e.finding for e in ds.evidence
            if e.matched
        ]

        section["diseases"].append({
            "disease": ds.disease,
            "icd10_codes": ds.icd10_codes,
            "score": ds.score,
            "confidence": ds.confidence,
            "matched_count": ds.matched_count,
            "total_criteria": ds.total_criteria,
            "supporting_evidence": supporting[:10],  # Top 10
        })

    top = dr.ranked_diseases[0]
    section["top_disease"] = top.disease
    section["top_score"] = top.score
    section["top_confidence"] = top.confidence

    return section


def _build_rare_disease_section(dr) -> Dict[str, Any]:
    """Build the rare disease assessment section."""
    section: Dict[str, Any] = {
        "triggered": dr.rare_disease_triggered,
        "trigger_reasons": [],
        "matches": [],
        "recommended_gene_panel": [],
        "summary": "",
    }

    if dr.rare_disease_result is None:
        return section

    rdr = dr.rare_disease_result
    section["trigger_reasons"] = rdr.trigger_reasons
    section["summary"] = rdr.summary
    section["recommended_gene_panel"] = rdr.recommended_gene_panel

    for match in rdr.matches:
        section["matches"].append({
            "orpha_code": match.orpha_code,
            "disease_name": match.disease_name,
            "score": match.score,
            "confidence": match.confidence,
            "matched_hpo": match.matched_hpo,
            "total_hpo": match.total_hpo,
            "gene_panel": match.gene_panel,
        })

    return section
