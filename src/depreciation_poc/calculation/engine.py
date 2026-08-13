from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from decimal import Decimal

from depreciation_poc.domain.models import (
    AssetEvent,
    DepreciationPolicy,
    FixedAsset,
    ForecastLine,
    Month,
    MonthlyDriver,
    PlannedAsset,
    RuleExecution,
    first_depreciation_month,
    money,
)
from depreciation_poc.policy.resolver import PolicyResolver


ZERO = Decimal("0")


class DepreciationCalculationEngine:
    """Routes each asset to a deterministic rule and keeps an auditable execution trace."""

    def __init__(self, policy_resolver: PolicyResolver) -> None:
        self.policy_resolver = policy_resolver
        self.executions: list[RuleExecution] = []

    def forecast(
        self,
        *,
        scenario_id: str,
        budget_version: str,
        start_period: Month,
        months: int,
        fixed_assets: list[FixedAsset],
        planned_assets: list[PlannedAsset],
        events: list[AssetEvent],
        monthly_drivers: list[MonthlyDriver] | None = None,
        invalid_object_ids: set[str] | None = None,
    ) -> list[ForecastLine]:
        self.executions = []
        invalid_object_ids = invalid_object_ids or set()
        events_by_asset: dict[str, list[AssetEvent]] = defaultdict(list)
        for event in events:
            if event.target_asset_id:
                events_by_asset[event.target_asset_id].append(event)
        drivers = self._driver_index(monthly_drivers or [])

        lines: list[ForecastLine] = []
        workload_assets: list[tuple[FixedAsset, DepreciationPolicy]] = []
        for asset in fixed_assets:
            if asset.asset_id in invalid_object_ids or asset.in_service_date is None:
                continue
            policy = self._policy(asset)
            if policy is None:
                continue
            if policy.method == "WORKLOAD":
                workload_assets.append((asset, policy))
                continue
            lines.extend(
                self._forecast_existing_asset(
                    scenario_id=scenario_id,
                    budget_version=budget_version,
                    start_period=start_period,
                    months=months,
                    asset=asset,
                    policy=policy,
                    events=events_by_asset.get(asset.asset_id, []),
                    drivers=drivers,
                    validation_status="ERROR" if asset.asset_id in invalid_object_ids else "OK",
                )
            )
        lines.extend(
            self._forecast_workload_assets(
                scenario_id=scenario_id,
                budget_version=budget_version,
                start_period=start_period,
                months=months,
                assets=workload_assets,
                drivers=drivers,
            )
        )
        for asset in planned_assets:
            if asset.planned_asset_id in invalid_object_ids or asset.expected_in_service_date is None:
                continue
            policy = self._policy(asset)
            if policy is None:
                continue
            lines.extend(
                self._forecast_planned_asset(
                    scenario_id=scenario_id,
                    budget_version=asset.budget_version or budget_version,
                    start_period=start_period,
                    months=months,
                    asset=asset,
                    policy=policy,
                    validation_status="ERROR" if asset.planned_asset_id in invalid_object_ids else "OK",
                )
            )
        return lines

    @staticmethod
    def _driver_index(drivers: list[MonthlyDriver]) -> dict[tuple[str, str, str], MonthlyDriver]:
        return {(item.driver_type, item.target_id, str(item.period)): item for item in drivers}

    def _policy(self, asset: FixedAsset | PlannedAsset) -> DepreciationPolicy | None:
        policy = self.policy_resolver.resolve_for_asset(
            company=asset.company,
            asset_category=asset.asset_category,
            depreciation_code=asset.depreciation_code,
        )
        if policy is None:
            return None
        overrides: dict[str, object] = {}
        if isinstance(asset, FixedAsset) and asset.useful_life_months:
            overrides["useful_life_months"] = asset.useful_life_months
        if isinstance(asset, FixedAsset) and asset.residual_rate is not None:
            overrides["residual_rate"] = asset.residual_rate
        if isinstance(asset, FixedAsset) and asset.start_rule:
            overrides["start_rule"] = asset.start_rule
        return replace(policy, **overrides) if overrides else policy

    def _forecast_existing_asset(
        self,
        *,
        scenario_id: str,
        budget_version: str,
        start_period: Month,
        months: int,
        asset: FixedAsset,
        policy: DepreciationPolicy,
        events: list[AssetEvent],
        drivers: dict[tuple[str, str, str], MonthlyDriver],
        validation_status: str,
    ) -> list[ForecastLine]:
        first_month = first_depreciation_month(asset.in_service_date, policy.start_rule)
        total_depreciable = asset.original_cost * (Decimal("1") - policy.residual_rate)
        elapsed_months = max(0, min(policy.useful_life_months, first_month.months_until(start_period)))
        original_monthly_amount = total_depreciable / Decimal(policy.useful_life_months)
        accumulated_depreciation = max(asset.accumulated_depreciation, money(original_monthly_amount * elapsed_months))
        accumulated_impairment = asset.accumulated_impairment
        disposed = False
        lines: list[ForecastLine] = []
        events_by_period: dict[Month, list[AssetEvent]] = defaultdict(list)
        for event in events:
            events_by_period[Month.from_date(event.effective_date)].append(event)

        for offset in range(months):
            period = start_period.add(offset)
            event_type = "BASE"
            source_event_id = None
            impairment_amount = ZERO
            disposal_amount = ZERO
            opening_depreciation = accumulated_depreciation
            opening_impairment = accumulated_impairment
            opening_net_value = money(asset.original_cost - opening_depreciation - opening_impairment)
            for event in events_by_period.get(period, []):
                source_event_id = event.event_id
                event_type = event.event_type
                if event.event_type == "IMPAIRMENT":
                    impairment_amount += event.amount
                    accumulated_impairment += event.amount
                elif event.event_type == "DISPOSAL":
                    disposal_amount += event.amount
                    disposed = True

            depreciation, branch_id, formula_cn, inputs, conclusion = self._monthly_amount(
                asset=asset,
                policy=policy,
                period=period,
                first_month=first_month,
                total_depreciable=total_depreciable,
                accumulated_depreciation=accumulated_depreciation,
                accumulated_impairment=accumulated_impairment,
                opening_net_value=opening_net_value,
                disposed=disposed,
                drivers=drivers,
            )
            depreciation = money(depreciation)
            accumulated_depreciation = money(accumulated_depreciation + depreciation)
            closing_net_value = money(asset.original_cost - accumulated_depreciation - accumulated_impairment - disposal_amount)
            line = ForecastLine(
                scenario_id=scenario_id, budget_version=budget_version, asset_id=asset.asset_id,
                planned_asset_id=None, asset_source_type="CURRENT", company=asset.company,
                department=asset.department, cost_center=asset.cost_center, profit_center=asset.profit_center,
                asset_category=asset.asset_category, depreciation_code=asset.depreciation_code,
                depreciation_policy=policy.policy_id, depreciation_method=policy.method, period=period,
                opening_original_cost=money(asset.original_cost),
                opening_accumulated_depreciation=money(opening_depreciation),
                opening_accumulated_impairment=money(opening_impairment), opening_net_value=opening_net_value,
                addition_amount=ZERO, disposal_amount=money(disposal_amount), impairment_amount=money(impairment_amount),
                depreciable_base=money(total_depreciable - accumulated_impairment), monthly_depreciation=depreciation,
                accumulated_depreciation=accumulated_depreciation, closing_net_value=closing_net_value,
                source_event_id=source_event_id, calculation_rule_id=f"{policy.policy_id}:{branch_id}",
                validation_status=validation_status,
            )
            lines.append(line)
            self._record(scenario_id, asset.asset_id, period, policy.policy_id, branch_id, formula_cn, inputs, conclusion)
        return lines

    def _monthly_amount(self, *, asset: FixedAsset, policy: DepreciationPolicy, period: Month,
                        first_month: Month, total_depreciable: Decimal, accumulated_depreciation: Decimal,
                        accumulated_impairment: Decimal, opening_net_value: Decimal, disposed: bool,
                        drivers: dict[tuple[str, str, str], MonthlyDriver]) -> tuple[Decimal, str, str, dict[str, str], str]:
        if disposed:
            return ZERO, "DISPOSAL_STOP", "资产已减少，停止计提。", {}, "资产减少后本月不计提折旧。"
        if period < first_month:
            return ZERO, "BEFORE_START", "尚未达到开始计提月份。", {"首次计提月份": str(first_month)}, "本月尚未开始计提。"
        if policy.method == "PRODUCTION":
            driver = drivers.get(("PRODUCTION", asset.block_id or "", str(period)))
            production = driver.production if driver else ZERO
            reserves = driver.reserves if driver else ZERO
            configured_rate = driver.depletion_rate if driver else None
            if configured_rate is not None:
                rate = min(max(configured_rate, ZERO), Decimal("1"))
                branch = "CONFIGURED_DEPLETION_RATE"
                conclusion = "按区块配置表提供的折耗率计算折耗。"
            elif reserves <= ZERO:
                rate, branch = Decimal("1"), "NO_RESERVES"
                conclusion = "剩余储量为零，按规则一次性折耗剩余净值。"
            elif production <= ZERO:
                rate, branch = ZERO, "NO_PRODUCTION"
                conclusion = "有剩余储量但当月无产量，按规则不计提折耗。"
            elif production >= reserves:
                rate, branch = Decimal("1"), "PRODUCTION_EXCEEDS_RESERVES"
                conclusion = "当月产量不小于剩余储量，折耗率封顶为 100%。"
            else:
                rate, branch = production / reserves, "NORMAL_PRODUCTION"
                conclusion = "按当月产量与剩余储量的比例计算折耗。"
            amount = max(money(opening_net_value * rate), ZERO)
            return amount, branch, "月折耗 = 期初净值 × 折耗率", {
                "区块": asset.block_id or "-", "当月产量": str(production), "剩余储量": str(reserves),
                "折耗率": str(rate), "期初净值": str(opening_net_value),
                "折耗率来源": "区块配置表" if configured_rate is not None else "由产量/储量计算",
            }, conclusion
        month_index = first_month.months_until(period)
        if not 0 <= month_index < policy.useful_life_months:
            return ZERO, "LIFE_EXPIRED", "已达到折旧月数，停止计提。", {"使用年限(月)": str(policy.useful_life_months)}, "资产已折旧到期。"
        remaining_months = policy.useful_life_months - month_index
        remaining_base = total_depreciable - accumulated_depreciation - accumulated_impairment
        branch = "IMPAIRMENT_RECALC" if accumulated_impairment > ZERO else "STRAIGHT_LINE"
        formula = "月折旧 = 剩余可折旧金额 ÷ 剩余折旧月数"
        return max(remaining_base / Decimal(remaining_months), ZERO), branch, formula, {
            # Keep the pre-rounding amount for row-level audit replay. The forecast amount is
            # rounded to cents afterwards, so a cents-rounded input would not always reproduce it.
            "剩余可折旧金额": str(remaining_base), "剩余折旧月数": str(remaining_months),
            "残值率": str(policy.residual_rate),
        }, "减值后按剩余可折旧金额和剩余期间重算。" if branch == "IMPAIRMENT_RECALC" else "按年限平均法计提。"

    def _forecast_workload_assets(self, *, scenario_id: str, budget_version: str, start_period: Month,
                                  months: int, assets: list[tuple[FixedAsset, DepreciationPolicy]],
                                  drivers: dict[tuple[str, str, str], MonthlyDriver]) -> list[ForecastLine]:
        if not assets:
            return []
        balances = {asset.asset_id: money(asset.original_cost - asset.accumulated_depreciation - asset.accumulated_impairment) for asset, _ in assets}
        accumulated = {asset.asset_id: asset.accumulated_depreciation for asset, _ in assets}
        lines: list[ForecastLine] = []
        for offset in range(months):
            period = start_period.add(offset)
            grouped: dict[str, list[tuple[FixedAsset, DepreciationPolicy]]] = defaultdict(list)
            for asset, policy in assets:
                grouped[asset.organization_id or asset.company].append((asset, policy))
            for driver_target, members in grouped.items():
                driver = drivers.get(("WORKLOAD", driver_target, str(period)))
                workload = driver.workload if driver else ZERO
                unit_fee = driver.unit_fee if driver else ZERO
                total_amortization = money(
                    driver.total_amortization if driver and driver.total_amortization is not None else workload * unit_fee
                )
                eligible = [(asset, policy) for asset, policy in members if period >= first_depreciation_month(asset.in_service_date, policy.start_rule) and balances[asset.asset_id] > ZERO]
                total_net = sum((balances[asset.asset_id] for asset, _ in eligible), ZERO)
                for asset, policy in members:
                    opening_net = balances[asset.asset_id]
                    if asset not in [item[0] for item in eligible] or total_net <= ZERO or total_amortization <= ZERO:
                        amount, branch = ZERO, "NO_WORKLOAD"
                        conclusion = "当月工作量或单位费用为零，未形成工作量法摊销。"
                    else:
                        amount = min(money(total_amortization * opening_net / total_net), opening_net)
                        branch, conclusion = "WORKLOAD_ALLOCATION", "按资产期初净值占工作量法资产净额的比例分摊。"
                    balances[asset.asset_id] = money(opening_net - amount)
                    accumulated[asset.asset_id] = money(accumulated[asset.asset_id] + amount)
                    line = ForecastLine(
                        scenario_id=scenario_id, budget_version=budget_version, asset_id=asset.asset_id,
                        planned_asset_id=None, asset_source_type="CURRENT", company=asset.company,
                        department=asset.department, cost_center=asset.cost_center, profit_center=asset.profit_center,
                        asset_category=asset.asset_category, depreciation_code=asset.depreciation_code,
                        depreciation_policy=policy.policy_id, depreciation_method=policy.method, period=period,
                        opening_original_cost=money(asset.original_cost),
                        opening_accumulated_depreciation=money(accumulated[asset.asset_id] - amount),
                        opening_accumulated_impairment=money(asset.accumulated_impairment), opening_net_value=opening_net,
                        addition_amount=ZERO, disposal_amount=ZERO, impairment_amount=ZERO, depreciable_base=opening_net,
                        monthly_depreciation=amount, accumulated_depreciation=accumulated[asset.asset_id],
                        closing_net_value=balances[asset.asset_id], source_event_id=None,
                        calculation_rule_id=f"{policy.policy_id}:{branch}", validation_status="OK",
                    )
                    lines.append(line)
                    self._record(scenario_id, asset.asset_id, period, policy.policy_id, branch,
                                 "月摊销 = 当月总摊销额 × 资产期初净值 ÷ 工作量法资产期初净额", {
                                     "工作量": str(workload), "单位费用": str(unit_fee), "当月总摊销额": str(total_amortization),
                                     "总摊销额来源": "工作量法配置表" if driver and driver.total_amortization is not None else "工作量 × 单位费用",
                                     "资产期初净值": str(opening_net), "资产池期初净额": str(total_net),
                                 }, conclusion)
        return lines

    def _forecast_planned_asset(self, *, scenario_id: str, budget_version: str, start_period: Month, months: int,
                                asset: PlannedAsset, policy: DepreciationPolicy, validation_status: str) -> list[ForecastLine]:
        first_month = first_depreciation_month(asset.expected_in_service_date, policy.start_rule)
        total_depreciable = asset.planned_amount * (Decimal("1") - policy.residual_rate)
        monthly_amount = total_depreciable / Decimal(policy.useful_life_months)
        accumulated_depreciation = ZERO
        lines: list[ForecastLine] = []
        for offset in range(months):
            period = start_period.add(offset)
            month_index = first_month.months_until(period)
            depreciation = monthly_amount if 0 <= month_index < policy.useful_life_months else ZERO
            depreciation = money(depreciation)
            accumulated_depreciation = money(accumulated_depreciation + depreciation)
            addition_amount = asset.planned_amount if period == first_month else ZERO
            lines.append(ForecastLine(
                scenario_id=scenario_id, budget_version=budget_version, asset_id=None, planned_asset_id=asset.planned_asset_id,
                asset_source_type="PLANNED", company=asset.company, department=asset.department, cost_center=asset.cost_center,
                profit_center=asset.profit_center, asset_category=asset.asset_category, depreciation_code=asset.depreciation_code,
                depreciation_policy=policy.policy_id, depreciation_method=policy.method, period=period,
                opening_original_cost=money(asset.planned_amount), opening_accumulated_depreciation=money(accumulated_depreciation - depreciation),
                opening_accumulated_impairment=ZERO, opening_net_value=money(asset.planned_amount - accumulated_depreciation + depreciation),
                addition_amount=money(addition_amount), disposal_amount=ZERO, impairment_amount=ZERO,
                depreciable_base=money(total_depreciable), monthly_depreciation=depreciation,
                accumulated_depreciation=accumulated_depreciation, closing_net_value=money(asset.planned_amount - accumulated_depreciation),
                source_event_id="SCENARIO_NEW_ASSET" if addition_amount else None,
                calculation_rule_id=f"{policy.policy_id}:NEW_ASSET", validation_status=validation_status,
            ))
            self._record(scenario_id, asset.planned_asset_id, period, policy.policy_id, "NEW_ASSET",
                         "月折旧 = (资产原值 × (1 - 残值率)) ÷ 使用月数", {
                             "资产原值": str(asset.planned_amount), "残值率": str(policy.residual_rate), "使用月数": str(policy.useful_life_months),
                         }, "新增资产按适用开始计提规则进入折旧。")
        return lines

    def _record(self, scenario_id: str, asset_ref: str, period: Month, rule_id: str, branch_id: str,
                formula_cn: str, inputs: dict[str, str], conclusion_cn: str) -> None:
        self.executions.append(RuleExecution(scenario_id, asset_ref, period, rule_id, branch_id, formula_cn, inputs, conclusion_cn))
