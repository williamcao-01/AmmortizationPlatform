from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional


MONEY_QUANT = Decimal("0.01")


def money(value: Decimal | int | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def parse_date(value: str | None) -> Optional[date]:
    if value is None or value == "":
        return None
    return date.fromisoformat(value)


def parse_decimal(value: str | None, default: str = "0") -> Decimal:
    if value is None or value == "":
        value = default
    return Decimal(str(value))


@dataclass(frozen=True)
class Month:
    year: int
    month: int

    @classmethod
    def parse(cls, value: str) -> "Month":
        year_text, month_text = value.split("-", maxsplit=1)
        return cls(int(year_text), int(month_text))

    @classmethod
    def from_date(cls, value: date) -> "Month":
        return cls(value.year, value.month)

    def add(self, months: int) -> "Month":
        absolute = self.year * 12 + (self.month - 1) + months
        return Month(absolute // 12, absolute % 12 + 1)

    def months_until(self, other: "Month") -> int:
        return (other.year - self.year) * 12 + (other.month - self.month)

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    def __lt__(self, other: "Month") -> bool:
        return (self.year, self.month) < (other.year, other.month)

    def __le__(self, other: "Month") -> bool:
        return (self.year, self.month) <= (other.year, other.month)


def first_depreciation_month(in_service_date: date, start_rule: str) -> Month:
    base = Month.from_date(in_service_date)
    if start_rule == "NEXT_MONTH":
        return base.add(1)
    if start_rule == "CURRENT_MONTH":
        return base
    raise ValueError(f"Unsupported start rule: {start_rule}")


@dataclass(frozen=True)
class AssetCategory:
    category_id: str
    name: str
    parent_id: Optional[str]


@dataclass(frozen=True)
class DepreciationPolicy:
    policy_id: str
    name: str
    company: str
    perspective: str
    asset_category: str
    method: str
    useful_life_months: int
    residual_rate: Decimal
    start_rule: str


@dataclass(frozen=True)
class DepreciationCode:
    code_id: str
    name: str
    asset_category: str
    policy_id: str


@dataclass(frozen=True)
class FixedAsset:
    asset_id: str
    name: str
    company: str
    department: str
    cost_center: str
    profit_center: str
    asset_category: str
    depreciation_code: str
    original_cost: Decimal
    in_service_date: Optional[date]
    accumulated_depreciation: Decimal
    accumulated_impairment: Decimal
    status: str
    # Source-specific fields are optional so the synthetic sample adapter remains compatible.
    block_id: Optional[str] = None
    useful_life_months: Optional[int] = None
    residual_rate: Optional[Decimal] = None
    start_rule: Optional[str] = None
    asset_type: str = ""
    source_row: Optional[int] = None
    asset_category_name: str = ""
    asset_major_category: str = ""
    asset_major_category_name: str = ""
    organization_id: str = ""
    snapshot_monthly_depreciation: Decimal = Decimal("0")
    snapshot_net_value: Decimal = Decimal("0")


@dataclass(frozen=True)
class PlannedAsset:
    planned_asset_id: str
    name: str
    company: str
    department: str
    cost_center: str
    profit_center: str
    asset_category: str
    depreciation_code: str
    planned_amount: Decimal
    expected_in_service_date: Optional[date]
    budget_version: str
    status: str


@dataclass(frozen=True)
class AssetEvent:
    event_id: str
    event_type: str
    target_asset_id: Optional[str]
    target_planned_asset_id: Optional[str]
    company: str
    department: str
    cost_center: str
    profit_center: str
    amount: Decimal
    effective_date: date
    budget_version: str
    description: str


@dataclass(frozen=True)
class ForecastLine:
    scenario_id: str
    budget_version: str
    asset_id: Optional[str]
    planned_asset_id: Optional[str]
    asset_source_type: str
    company: str
    department: str
    cost_center: str
    profit_center: str
    asset_category: str
    depreciation_code: str
    depreciation_policy: str
    depreciation_method: str
    period: Month
    opening_original_cost: Decimal
    opening_accumulated_depreciation: Decimal
    opening_accumulated_impairment: Decimal
    opening_net_value: Decimal
    addition_amount: Decimal
    disposal_amount: Decimal
    impairment_amount: Decimal
    depreciable_base: Decimal
    monthly_depreciation: Decimal
    accumulated_depreciation: Decimal
    closing_net_value: Decimal
    source_event_id: Optional[str]
    calculation_rule_id: str
    validation_status: str


@dataclass(frozen=True)
class MonthlyDriver:
    """A normalized monthly input consumed by non-straight-line rules."""

    driver_type: str
    period: Month
    company: str
    target_id: str
    production: Decimal = Decimal("0")
    reserves: Decimal = Decimal("0")
    workload: Decimal = Decimal("0")
    unit_fee: Decimal = Decimal("0")
    total_amortization: Optional[Decimal] = None
    depletion_rate: Optional[Decimal] = None
    source_refs: tuple[str, ...] = ()
    assumption_note: str = ""


@dataclass(frozen=True)
class RuleExecution:
    scenario_id: str
    asset_ref: str
    period: Month
    rule_id: str
    branch_id: str
    formula_cn: str
    inputs: dict[str, str]
    conclusion_cn: str


@dataclass(frozen=True)
class Anomaly:
    anomaly_id: str
    severity: str
    object_type: str
    object_id: str
    rule_id: str
    message: str


@dataclass(frozen=True)
class SummaryLine:
    scenario_id: str
    budget_version: str
    period: Month
    year: int
    company: str
    department: str
    cost_center: str
    profit_center: str
    asset_category: str
    asset_source_type: str
    event_type: str
    depreciation_policy: str
    monthly_depreciation_sum: Decimal
    addition_depreciation_impact: Decimal
    disposal_depreciation_impact: Decimal
    impairment_depreciation_impact: Decimal


@dataclass(frozen=True)
class WhatIfChange:
    change_id: str
    target_type: str
    target_id: str
    field_name: str
    old_value: str
    new_value: str
    reason: str


@dataclass(frozen=True)
class AttributionLine:
    scenario_id: str
    compared_to_scenario_id: str
    period: Month
    object_type: str
    object_id: str
    driver_type: str
    driver_id: str
    baseline_depreciation: Decimal
    scenario_depreciation: Decimal
    difference: Decimal
    explanation: str
