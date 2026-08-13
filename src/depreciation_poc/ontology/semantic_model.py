from __future__ import annotations

from depreciation_poc.domain.models import (
    AssetCategory,
    DepreciationCode,
    DepreciationPolicy,
)


class SemanticModel:
    def __init__(
        self,
        categories: list[AssetCategory],
        policies: list[DepreciationPolicy],
        codes: list[DepreciationCode],
    ) -> None:
        self.categories = {item.category_id: item for item in categories}
        self.policies = {item.policy_id: item for item in policies}
        self.codes = {item.code_id: item for item in codes}

    def ancestors_including_self(self, category_id: str) -> list[str]:
        result: list[str] = []
        current = category_id
        seen: set[str] = set()
        while current:
            if current in seen:
                raise ValueError(f"Cycle detected in category hierarchy at {current}")
            seen.add(current)
            result.append(current)
            category = self.categories.get(current)
            if category is None:
                break
            current = category.parent_id or ""
        return result

    def is_category_or_descendant(self, category_id: str, ancestor_id: str) -> bool:
        return ancestor_id in self.ancestors_including_self(category_id)

    def match_policy(
        self,
        *,
        company: str,
        perspective: str,
        asset_category: str,
    ) -> DepreciationPolicy | None:
        candidates = [
            policy
            for policy in self.policies.values()
            if policy.company == company
            and policy.perspective == perspective
            and self.is_category_or_descendant(asset_category, policy.asset_category)
        ]
        if not candidates:
            return None
        candidates.sort(
            key=lambda policy: self.ancestors_including_self(asset_category).index(
                policy.asset_category
            )
        )
        return candidates[0]

    def code_is_compatible(self, *, asset_category: str, code_id: str) -> bool:
        code = self.codes.get(code_id)
        if code is None:
            return False
        return self.is_category_or_descendant(asset_category, code.asset_category)

    def policy_for_code(self, code_id: str) -> DepreciationPolicy | None:
        code = self.codes.get(code_id)
        if code is None:
            return None
        return self.policies.get(code.policy_id)
