from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from decimal import Decimal

from depreciation_poc.domain.models import ForecastLine, SummaryLine, money


ZERO = Decimal("0")


class DepreciationAggregator:
    def summarize(self, lines: list[ForecastLine]) -> list[SummaryLine]:
        groups: dict[tuple, dict[str, Decimal | ForecastLine | str]] = {}
        for line in lines:
            event_type = self._event_type(line)
            key = (
                line.scenario_id,
                line.budget_version,
                str(line.period),
                line.company,
                line.department,
                line.cost_center,
                line.profit_center,
                line.asset_category,
                line.asset_source_type,
                event_type,
                line.depreciation_policy,
            )
            if key not in groups:
                groups[key] = {
                    "line": line,
                    "event_type": event_type,
                    "monthly": ZERO,
                    "addition": ZERO,
                    "disposal": ZERO,
                    "impairment": ZERO,
                }
            groups[key]["monthly"] += line.monthly_depreciation
            if line.addition_amount:
                groups[key]["addition"] += line.monthly_depreciation
            if line.disposal_amount:
                groups[key]["disposal"] += line.monthly_depreciation
            if line.impairment_amount:
                groups[key]["impairment"] += line.monthly_depreciation

        summaries: list[SummaryLine] = []
        for group in groups.values():
            line = group["line"]
            assert isinstance(line, ForecastLine)
            summaries.append(
                SummaryLine(
                    scenario_id=line.scenario_id,
                    budget_version=line.budget_version,
                    period=line.period,
                    year=line.period.year,
                    company=line.company,
                    department=line.department,
                    cost_center=line.cost_center,
                    profit_center=line.profit_center,
                    asset_category=line.asset_category,
                    asset_source_type=line.asset_source_type,
                    event_type=str(group["event_type"]),
                    depreciation_policy=line.depreciation_policy,
                    monthly_depreciation_sum=money(group["monthly"]),
                    addition_depreciation_impact=money(group["addition"]),
                    disposal_depreciation_impact=money(group["disposal"]),
                    impairment_depreciation_impact=money(group["impairment"]),
                )
            )
        return sorted(summaries, key=lambda item: (str(item.period), item.company, item.department, item.asset_category))

    def compare_monthly_totals(
        self,
        baseline: list[ForecastLine],
        scenario: list[ForecastLine],
    ) -> dict[str, Decimal]:
        base_totals = self._monthly_totals(baseline)
        scenario_totals = self._monthly_totals(scenario)
        periods = set(base_totals) | set(scenario_totals)
        return {
            period: money(scenario_totals.get(period, ZERO) - base_totals.get(period, ZERO))
            for period in sorted(periods)
        }

    @staticmethod
    def _event_type(line: ForecastLine) -> str:
        if line.disposal_amount:
            return "DISPOSAL"
        if line.impairment_amount:
            return "IMPAIRMENT"
        if line.addition_amount:
            return "ADDITION"
        return "BASE"

    @staticmethod
    def _monthly_totals(lines: list[ForecastLine]) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for line in lines:
            totals[str(line.period)] += line.monthly_depreciation
        return totals
