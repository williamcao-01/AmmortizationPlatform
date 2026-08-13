from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from depreciation_poc.domain.models import AttributionLine, ForecastLine, WhatIfChange, money


ZERO = Decimal("0")


class AttributionService:
    def attribute_what_if_difference(
        self,
        *,
        baseline_lines: list[ForecastLine],
        scenario_lines: list[ForecastLine],
        changes: list[WhatIfChange],
    ) -> list[AttributionLine]:
        change_by_target = {change.target_id: change for change in changes}
        policy_change = next((change for change in changes if change.target_type == "DepreciationPolicy"), None)
        baseline_totals = self._object_period_totals(baseline_lines)
        scenario_totals = self._object_period_totals(scenario_lines)
        keys = sorted(set(baseline_totals) | set(scenario_totals), key=lambda item: (str(item[1]), item[0], item[2]))
        scenario_id = scenario_lines[0].scenario_id if scenario_lines else ""
        baseline_id = baseline_lines[0].scenario_id if baseline_lines else ""

        attributions: list[AttributionLine] = []
        for object_type, period, object_id in keys:
            baseline_amount = baseline_totals.get((object_type, period, object_id), ZERO)
            scenario_amount = scenario_totals.get((object_type, period, object_id), ZERO)
            difference = money(scenario_amount - baseline_amount)
            if difference == ZERO:
                continue
            change = change_by_target.get(object_id) or policy_change
            driver_type = self._driver_type(change) if change else "UNCLASSIFIED"
            driver_id = change.change_id if change else object_id
            reason = change.reason if change else "Difference not linked to an explicit what-if change."
            attributions.append(
                AttributionLine(
                    scenario_id=scenario_id,
                    compared_to_scenario_id=baseline_id,
                    period=period,
                    object_type=object_type,
                    object_id=object_id,
                    driver_type=driver_type,
                    driver_id=driver_id,
                    baseline_depreciation=money(baseline_amount),
                    scenario_depreciation=money(scenario_amount),
                    difference=difference,
                    explanation=reason,
                )
            )
        return attributions

    @staticmethod
    def _driver_type(change: WhatIfChange | None) -> str:
        if change is None:
            return "UNCLASSIFIED"
        if change.target_type == "DepreciationPolicy":
            return "POLICY_PARAMETER_CHANGE"
        return {
            "planned_amount": "ASSET_AMOUNT_CHANGE",
            "expected_in_service_date": "IN_SERVICE_DATE_CHANGE",
            "disposal_date": "DISPOSAL_EVENT",
            "impairment_amount": "IMPAIRMENT_EVENT",
        }.get(change.field_name, "WHAT_IF_CHANGE")

    @staticmethod
    def _object_period_totals(lines: list[ForecastLine]) -> dict[tuple[str, object, str], Decimal]:
        totals: dict[tuple[str, object, str], Decimal] = defaultdict(lambda: ZERO)
        for line in lines:
            object_type = "PlannedAsset" if line.planned_asset_id else "FixedAsset"
            object_id = line.planned_asset_id or line.asset_id or "UNKNOWN"
            totals[(object_type, line.period, object_id)] += line.monthly_depreciation
        return totals
