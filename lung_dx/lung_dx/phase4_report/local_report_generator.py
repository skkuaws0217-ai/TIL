"""로컬 템플릿 기반 임상소견서 생성.

Bedrock 미사용 시(로컬 개발) 구조화된 데이터를 Markdown 형식의
임상소견서로 변환한다.
settings.report_backend="template"일 때 사용.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


class LocalReportGenerator:
    """템플릿 기반 Markdown 임상소견서 생성."""

    def generate_report(
        self, report_data: dict[str, Any], language: str = "ko"
    ) -> str:
        sections = []

        # 헤더
        sections.append(self._header(report_data))
        sections.append(self._patient_info(report_data))
        sections.append(self._chief_complaint(report_data))
        sections.append(self._imaging_findings(report_data))
        sections.append(self._lab_findings(report_data))
        sections.append(self._vrh_findings(report_data))
        sections.append(self._micro_findings(report_data))
        sections.append(self._scoring_systems(report_data))
        sections.append(self._differential_diagnosis(report_data))
        sections.append(self._rare_disease_assessment(report_data))
        sections.append(self._recommendations(report_data))
        sections.append(self._summary(report_data))

        return "\n\n".join(s for s in sections if s)

    @staticmethod
    def _header(data: dict) -> str:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        return (
            f"# 임상 진단 소견서 (Clinical Diagnostic Report)\n"
            f"생성일시: {ts} | 생성방식: template | "
            f"Case ID: {data.get('case_id', 'N/A')}"
        )

    @staticmethod
    def _patient_info(data: dict) -> str:
        p = data.get("patient", {})
        return (
            f"## 1. 환자 정보 (Patient Information)\n"
            f"- 나이: {p.get('age', 'N/A')}세\n"
            f"- 성별: {p.get('sex', 'N/A')}"
        )

    @staticmethod
    def _chief_complaint(data: dict) -> str:
        p = data.get("patient", {})
        symptoms = p.get("symptoms", [])
        cc = p.get("chief_complaint", "")
        lines = ["## 2. 주소 및 증상 (Chief Complaint & Symptoms)"]
        if cc:
            lines.append(f"- 주소: {cc}")
        if symptoms:
            lines.append(f"- 증상: {', '.join(symptoms)}")
        return "\n".join(lines)

    @staticmethod
    def _imaging_findings(data: dict) -> str:
        phase1 = data.get("phase1")
        if not phase1:
            return "## 3. 영상검사 소견 (Imaging Findings)\n- X-ray 미시행"

        lines = ["## 3. 영상검사 소견 (Imaging Findings)"]
        detected = phase1.get("detected", [])
        possible = phase1.get("possible", [])

        if detected:
            lines.append("### 확인 소견 (Detected)")
            for f in detected:
                lines.append(f"- **{f['finding']}** (prob: {f['probability']:.2f})")
        if possible:
            lines.append("### 의심 소견 (Possible)")
            for f in possible:
                lines.append(f"- {f['finding']} (prob: {f['probability']:.2f})")

        keywords = phase1.get("ai_keywords", [])
        if keywords:
            lines.append(f"\nAI 키워드: {', '.join(keywords)}")
        return "\n".join(lines)

    @staticmethod
    def _lab_findings(data: dict) -> str:
        labs = data.get("lab_findings", [])
        if not labs:
            return "## 4. 검사실 소견 (Laboratory Findings)\n- Lab 미시행"

        lines = ["## 4. 검사실 소견 (Laboratory Findings)"]
        abnormal = [l for l in labs if l.get("severity") != "normal"]
        if abnormal:
            lines.append(f"| 검사 | 값 | 해석 | Medical Term | 심각도 |")
            lines.append(f"|------|-----|------|-------------|--------|")
            for l in abnormal:
                lines.append(
                    f"| {l['name']} | {l['value']} {l.get('unit','')} | "
                    f"{l['interpretation']} | {l.get('medical_term','')} | "
                    f"{l['severity']} |"
                )
        return "\n".join(lines)

    @staticmethod
    def _vrh_findings(data: dict) -> str:
        vrh = data.get("vrh_findings", [])
        if not vrh:
            return "## 5. Vitals/Respiratory/Hemodynamic 소견\n- 미측정"

        lines = ["## 5. Vitals/Respiratory/Hemodynamic 소견"]
        abnormal = [v for v in vrh if v.get("severity") != "normal"]
        if abnormal:
            lines.append(f"| 파라미터 | 값 | 해석 | Medical Term |")
            lines.append(f"|----------|-----|------|-------------|")
            for v in abnormal:
                lines.append(
                    f"| {v['name']} | {v['value']} {v.get('unit','')} | "
                    f"{v['interpretation']} | {v.get('medical_term','')} |"
                )

        scoring = data.get("scoring_systems", [])
        if scoring:
            lines.append("\n### 임상 스코어링")
            for s in scoring:
                lines.append(f"- **{s['name']}**: {s['score']}점 — {s.get('interpretation','')}")
        return "\n".join(lines)

    @staticmethod
    def _micro_findings(data: dict) -> str:
        micro = data.get("micro_findings", [])
        if not micro:
            return "## 6. 미생물학적 소견\n- 미시행"

        lines = ["## 6. 미생물학적 소견 (Microbiological Findings)"]
        for m in micro:
            diseases = ", ".join(m.get("matched_diseases", [])[:5])
            lines.append(f"- **{m['organism']}** → {diseases}")
        return "\n".join(lines)

    @staticmethod
    def _scoring_systems(data: dict) -> str:
        return ""  # VRH 섹션에 통합

    @staticmethod
    def _differential_diagnosis(data: dict) -> str:
        ranked = data.get("ranked_diseases", [])
        if not ranked:
            return "## 7. 추정 진단 (Differential Diagnosis)\n- 데이터 불충분"

        lines = ["## 7. 추정 진단 (Differential Diagnosis)"]
        lines.append("| 순위 | Score | Confidence | 질환명 | ICD-10 |")
        lines.append("|------|-------|------------|--------|--------|")
        for i, d in enumerate(ranked[:10], 1):
            icd = ",".join(d.get("icd10_codes", [])[:3])
            lines.append(
                f"| {i} | {d['total_score']:.3f} | {d['confidence']} | "
                f"{d['name_kr'] or d['name_en']} | {icd} |"
            )
        return "\n".join(lines)

    @staticmethod
    def _rare_disease_assessment(data: dict) -> str:
        phase3 = data.get("phase3")
        if not phase3 or not phase3.get("triggered"):
            return ""

        lines = ["## 8. 희귀질환 평가 (Rare Disease Assessment)"]
        lines.append(f"트리거 사유: {', '.join(phase3.get('trigger_reasons', []))}")

        candidates = phase3.get("rare_candidates", [])
        if candidates:
            lines.append("\n| 순위 | Score | 질환명 | OrphaCode | 유전자 |")
            lines.append("|------|-------|--------|-----------|--------|")
            for i, c in enumerate(candidates[:10], 1):
                genes = ",".join(c.get("major_genes", [])[:3]) or "—"
                lines.append(
                    f"| {i} | {c['hpo_score']:.3f} | {c['name_en'][:40]} | "
                    f"{c.get('orpha_code','')} | {genes} |"
                )

        gene_recs = phase3.get("genetic_tests", [])
        if gene_recs:
            lines.append("\n### 유전자 검사 추천")
            for r in gene_recs[:5]:
                lines.append(
                    f"- [{r['priority']}] **{r['gene']}** ({r['test_type']}) "
                    f"— {r.get('rationale','')[:80]}"
                )
        return "\n".join(lines)

    @staticmethod
    def _recommendations(data: dict) -> str:
        lines = ["## 9. 추가 검사 권고 (Recommended Further Workup)"]

        confirmatory = data.get("confirmatory_tests", [])
        if confirmatory:
            for t in confirmatory[:10]:
                lines.append(f"- [{t['test_type']}] {t['test_name'][:80]} — {t['for_disease']}")
        else:
            lines.append("- 현재 데이터로 추가 검사 권고 없음")
        return "\n".join(lines)

    @staticmethod
    def _summary(data: dict) -> str:
        ranked = data.get("ranked_diseases", [])
        top = ranked[0] if ranked else {}
        return (
            f"## 10. 종합 소견 (Summary & Impression)\n"
            f"가장 유력한 진단: **{top.get('name_kr', 'N/A')}** "
            f"(score: {top.get('total_score', 0):.3f}, "
            f"confidence: {top.get('confidence', 'N/A')})\n\n"
            f"---\n*본 소견서는 AI 진단 보조 프로그램에 의해 자동 생성되었으며, "
            f"최종 진단은 담당 의사의 임상 판단에 따릅니다.*"
        )
