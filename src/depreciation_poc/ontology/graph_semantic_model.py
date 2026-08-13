from __future__ import annotations

from depreciation_poc.domain.models import (
    AssetCategory,
    DepreciationCode,
    DepreciationPolicy,
)
from depreciation_poc.infrastructure.graph_store import SQLiteGraphStore


class GraphBackedSemanticModel:
    def __init__(
        self,
        *,
        graph_store: SQLiteGraphStore,
        categories: list[AssetCategory],
        policies: list[DepreciationPolicy],
        codes: list[DepreciationCode],
        load_graph: bool = True,
    ) -> None:
        self.graph_store = graph_store
        self.categories = {item.category_id: item for item in categories}
        self.policies = {item.policy_id: item for item in policies}
        self.codes = {item.code_id: item for item in codes}
        if load_graph:
            self.graph_store.load_ontology(
                categories=categories,
                policies=policies,
                codes=codes,
            )

    def ancestors_including_self(self, category_id: str) -> list[str]:
        return self.graph_store.ancestors_including_self(category_id)

    def is_category_or_descendant(self, category_id: str, ancestor_id: str) -> bool:
        return self.graph_store.is_category_or_descendant(category_id, ancestor_id)

    def match_policy(
        self,
        *,
        company: str,
        perspective: str,
        asset_category: str,
    ) -> DepreciationPolicy | None:
        explanation = self.graph_store.explain_policy_match(
            company=company,
            perspective=perspective,
            asset_category=asset_category,
        )
        if explanation is None:
            return None
        return self.policies.get(str(explanation["policy_id"]))

    def code_is_compatible(self, *, asset_category: str, code_id: str) -> bool:
        code = self.codes.get(code_id)
        if code is None:
            return False
        return self.graph_store.is_category_or_descendant(asset_category, code.asset_category)

    def policy_for_code(self, code_id: str) -> DepreciationPolicy | None:
        code = self.codes.get(code_id)
        if code is None:
            return None
        return self.policies.get(code.policy_id)
