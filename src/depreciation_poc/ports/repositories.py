from __future__ import annotations

from typing import Protocol

from depreciation_poc.domain.models import (
    AssetCategory,
    AssetEvent,
    DepreciationCode,
    DepreciationPolicy,
    FixedAsset,
    PlannedAsset,
)


class DepreciationRepository(Protocol):
    def load_asset_categories(self) -> list[AssetCategory]:
        ...

    def load_depreciation_policies(self) -> list[DepreciationPolicy]:
        ...

    def load_depreciation_codes(self) -> list[DepreciationCode]:
        ...

    def load_fixed_assets(self) -> list[FixedAsset]:
        ...

    def load_planned_assets(self) -> list[PlannedAsset]:
        ...

    def load_asset_events(self) -> list[AssetEvent]:
        ...
