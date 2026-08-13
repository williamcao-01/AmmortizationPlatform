from __future__ import annotations

from depreciation_poc.domain.models import DepreciationPolicy
from depreciation_poc.ontology.semantic_model import SemanticModel


class PolicyResolver:
    def __init__(self, semantic_model: SemanticModel, perspective: str) -> None:
        self.semantic_model = semantic_model
        self.perspective = perspective

    def resolve(self, *, company: str, asset_category: str) -> DepreciationPolicy | None:
        return self.semantic_model.match_policy(
            company=company,
            perspective=self.perspective,
            asset_category=asset_category,
        )

    def resolve_for_asset(
        self,
        *,
        company: str,
        asset_category: str,
        depreciation_code: str,
    ) -> DepreciationPolicy | None:
        """Prefer the ledger depreciation code, then retain category inheritance as fallback."""
        code_policy = self.semantic_model.policy_for_code(depreciation_code)
        if code_policy is not None and code_policy.company == company:
            return code_policy
        return self.resolve(company=company, asset_category=asset_category)
