"""누락 유전자 검사 추천 모듈.

희귀질환 후보의 주요 유전자(major_genes)와 환자의 기존 유전자
검사 결과를 비교하여, 미수행 유전자 검사를 추천한다.

101/376 희귀질환이 유전자 정보를 보유.
[ACMG Secondary Findings v3.2 (2023);
 Orphanet — gene-disease associations]
"""

from __future__ import annotations

from ..domain.disease import (
    RareDiseaseScore,
    GeneticTestRecommendation,
)
from ..domain.patient import PatientCase


class GeneticRecommender:
    """희귀질환 후보 기반 유전자 검사 추천."""

    def recommend(
        self,
        candidates: list[RareDiseaseScore],
        patient: PatientCase,
        max_recommendations: int = 10,
    ) -> list[GeneticTestRecommendation]:
        """후보 질환의 주요 유전자 중 미검사 항목 추천.

        Args:
            candidates: Phase 3 희귀질환 후보 (score 내림차순)
            patient: 환자 케이스 (genetic_tests 포함)
            max_recommendations: 최대 추천 수

        Returns:
            GeneticTestRecommendation 목록 (priority 순).
        """
        # 환자의 기존 유전자 검사 목록
        tested_genes = {
            t.get("gene", "").upper()
            for t in patient.genetic_tests
            if t.get("gene")
        }

        recommendations: list[GeneticTestRecommendation] = []
        seen_genes: set[str] = set()

        for candidate in candidates:
            if not candidate.major_genes:
                continue

            for gene in candidate.major_genes:
                gene_upper = gene.upper()
                if gene_upper in tested_genes or gene_upper in seen_genes:
                    continue

                seen_genes.add(gene_upper)

                # 우선순위: 후보 점수 + 유전형 기반
                priority = self._determine_priority(candidate)

                # 검사 유형 결정
                test_type = self._determine_test_type(
                    gene, candidate, len(candidate.major_genes)
                )

                recommendations.append(GeneticTestRecommendation(
                    gene=gene,
                    test_type=test_type,
                    priority=priority,
                    associated_diseases=[
                        f"{candidate.name_en} (OrphaCode:{candidate.orpha_code})"
                    ],
                    rationale=self._build_rationale(gene, candidate),
                ))

        # 우선순위 정렬
        priority_order = {"high": 0, "medium": 1, "low": 2}
        recommendations.sort(key=lambda r: priority_order.get(r.priority, 9))

        return recommendations[:max_recommendations]

    @staticmethod
    def _determine_priority(candidate: RareDiseaseScore) -> str:
        """후보 점수 + 유전형 기반 우선순위."""
        if candidate.hpo_score >= 0.3:
            return "high"
        elif candidate.hpo_score >= 0.15:
            return "medium"
        return "low"

    @staticmethod
    def _determine_test_type(
        gene: str, candidate: RareDiseaseScore, gene_count: int
    ) -> str:
        """유전자 검사 유형 결정.

        [ACMG v3.2 (2023) — secondary findings reporting;
         CAP/IASLC/AMP Molecular Testing 2018]
        """
        genetic_type = (candidate.genetic_type or "").lower()

        # 다중 유전자 질환 → 유전자 패널 또는 WES
        if gene_count >= 3:
            return "gene_panel"

        # 단일 유전자 + Mendelian 유전 → 단일 유전자 검사
        if gene_count == 1 and ("autosomal" in genetic_type or "x-linked" in genetic_type):
            return "single_gene"

        # 다인자성 → WES/WGS 고려
        if "multigenic" in genetic_type or "multifactorial" in genetic_type:
            return "WES"

        return "single_gene"

    @staticmethod
    def _build_rationale(gene: str, candidate: RareDiseaseScore) -> str:
        """추천 사유 문자열 생성."""
        parts = [
            f"{candidate.name_en} 의심",
            f"(HPO match score: {candidate.hpo_score:.3f},",
            f"matched HPO: {len(candidate.matched_hpo)}/{candidate.total_hpo})",
        ]
        if candidate.genetic_type:
            parts.append(f"유전형: {candidate.genetic_type}")
        return " ".join(parts)
