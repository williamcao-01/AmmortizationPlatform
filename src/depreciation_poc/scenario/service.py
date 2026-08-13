from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from depreciation_poc.domain.models import PlannedAsset, WhatIfChange


class ScenarioService:
    def change_planned_asset_amount(
        self,
        *,
        planned_assets: list[PlannedAsset],
        target_planned_asset_id: str,
        new_amount: Decimal,
        change_id: str,
        reason: str,
    ) -> tuple[list[PlannedAsset], WhatIfChange]:
        changed_assets: list[PlannedAsset] = []
        matched = False
        old_value = ""
        for asset in planned_assets:
            if asset.planned_asset_id == target_planned_asset_id:
                matched = True
                old_value = str(asset.planned_amount)
                changed_assets.append(replace(asset, planned_amount=new_amount))
            else:
                changed_assets.append(asset)
        if not matched:
            raise ValueError(f"Planned asset not found: {target_planned_asset_id}")
        return changed_assets, WhatIfChange(
            change_id=change_id,
            target_type="PlannedAsset",
            target_id=target_planned_asset_id,
            field_name="planned_amount",
            old_value=old_value,
            new_value=str(new_amount),
            reason=reason,
        )

    def change_planned_asset(
        self,
        *,
        planned_assets: list[PlannedAsset],
        target_planned_asset_id: str,
        new_amount: Decimal | None,
        new_expected_in_service_date: date | None,
        change_id_prefix: str,
    ) -> tuple[list[PlannedAsset], list[WhatIfChange]]:
        changed_assets: list[PlannedAsset] = []
        changes: list[WhatIfChange] = []
        matched = False
        for asset in planned_assets:
            if asset.planned_asset_id != target_planned_asset_id:
                changed_assets.append(asset)
                continue
            matched = True
            updated = asset
            if new_amount is not None and new_amount != asset.planned_amount:
                updated = replace(updated, planned_amount=new_amount)
                changes.append(
                    WhatIfChange(
                        change_id=f"{change_id_prefix}-AMOUNT",
                        target_type="PlannedAsset",
                        target_id=target_planned_asset_id,
                        field_name="planned_amount",
                        old_value=str(asset.planned_amount),
                        new_value=str(new_amount),
                        reason="调整计划资产金额并重算未来折旧。",
                    )
                )
            if (
                new_expected_in_service_date is not None
                and new_expected_in_service_date != asset.expected_in_service_date
            ):
                updated = replace(
                    updated,
                    expected_in_service_date=new_expected_in_service_date,
                )
                changes.append(
                    WhatIfChange(
                        change_id=f"{change_id_prefix}-IN-SERVICE-DATE",
                        target_type="PlannedAsset",
                        target_id=target_planned_asset_id,
                        field_name="expected_in_service_date",
                        old_value=str(asset.expected_in_service_date),
                        new_value=str(new_expected_in_service_date),
                        reason="调整预计投产日期并重算折旧开始期间。",
                    )
                )
            changed_assets.append(updated)
        if not matched:
            raise ValueError(f"Planned asset not found: {target_planned_asset_id}")
        if not changes:
            changes.append(
                WhatIfChange(
                    change_id=f"{change_id_prefix}-NOOP",
                    target_type="PlannedAsset",
                    target_id=target_planned_asset_id,
                    field_name="none",
                    old_value="unchanged",
                    new_value="unchanged",
                    reason="输入与基准场景一致，未产生业务假设变化。",
                )
            )
        return changed_assets, changes
