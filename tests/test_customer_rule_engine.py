from datetime import date
from decimal import Decimal
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from depreciation_poc.calculation.engine import DepreciationCalculationEngine
from depreciation_poc.domain.models import DepreciationPolicy, FixedAsset, Month, MonthlyDriver


class StubResolver:
    def __init__(self, policy):
        self.policy = policy

    def resolve_for_asset(self, **_kwargs):
        return self.policy


def asset(*, asset_id="A-1", code="Z802", block="B-1", in_service_date=date(2026, 9, 1), accumulated=Decimal("0")):
    return FixedAsset(
        asset_id=asset_id, name=asset_id, company="9800", department="测试单位", cost_center="CC",
        profit_center="PC", asset_category="C", depreciation_code=code, original_cost=Decimal("1000"),
        in_service_date=in_service_date, accumulated_depreciation=accumulated,
        accumulated_impairment=Decimal("0"), status="在账", block_id=block, useful_life_months=120,
        residual_rate=Decimal("0"),
    )


class CustomerRuleEngineTest(unittest.TestCase):
    def test_production_rule_handles_no_production_and_no_reserves(self):
        policy = DepreciationPolicy("P", "产量法", "9800", "BUDGET", "C", "PRODUCTION", 120, Decimal("0"), "CURRENT_MONTH")
        engine = DepreciationCalculationEngine(StubResolver(policy))
        lines = engine.forecast(
            scenario_id="S", budget_version="B", start_period=Month.parse("2026-09"), months=2,
            fixed_assets=[asset()], planned_assets=[], events=[], monthly_drivers=[
                MonthlyDriver("PRODUCTION", Month.parse("2026-09"), "9800", "B-1", production=Decimal("0"), reserves=Decimal("100")),
                MonthlyDriver("PRODUCTION", Month.parse("2026-10"), "9800", "B-1", production=Decimal("10"), reserves=Decimal("0")),
            ],
        )
        self.assertEqual(str(lines[0].monthly_depreciation), "0.00")
        self.assertEqual(str(lines[1].monthly_depreciation), "1000.00")
        self.assertEqual([item.branch_id for item in engine.executions], ["NO_PRODUCTION", "NO_RESERVES"])

    def test_workload_rule_allocates_pool_by_opening_net_value(self):
        policy = DepreciationPolicy("P", "工作量法", "9800", "BUDGET", "C", "WORKLOAD", 120, Decimal("0"), "CURRENT_MONTH")
        engine = DepreciationCalculationEngine(StubResolver(policy))
        lines = engine.forecast(
            scenario_id="S", budget_version="B", start_period=Month.parse("2026-09"), months=1,
            fixed_assets=[asset(asset_id="A-1", code="Z901", block=None), asset(asset_id="A-2", code="Z901", block=None)],
            planned_assets=[], events=[],
            monthly_drivers=[MonthlyDriver("WORKLOAD", Month.parse("2026-09"), "9800", "9800", workload=Decimal("10"), unit_fee=Decimal("6"))],
        )
        self.assertEqual([str(item.monthly_depreciation) for item in lines], ["30.00", "30.00"])
        self.assertTrue(all(item.branch_id == "WORKLOAD_ALLOCATION" for item in engine.executions))

    def test_production_uses_ledger_opening_net_and_recalculates_rate(self):
        policy = DepreciationPolicy("P", "产量法", "9800", "BUDGET", "C", "PRODUCTION", 120, Decimal("0"), "CURRENT_MONTH")
        engine = DepreciationCalculationEngine(StubResolver(policy))
        lines = engine.forecast(
            scenario_id="S", budget_version="B", start_period=Month.parse("2026-09"), months=1,
            fixed_assets=[asset(in_service_date=date(2020, 1, 1), accumulated=Decimal("100"))],
            planned_assets=[], events=[], monthly_drivers=[
                MonthlyDriver(
                    "PRODUCTION", Month.parse("2026-09"), "9800", "B-1",
                    production=Decimal("10"), reserves=Decimal("100"), depletion_rate=Decimal("0.5"),
                )
            ],
        )
        self.assertEqual(str(lines[0].opening_net_value), "900.00")
        self.assertEqual(str(lines[0].monthly_depreciation), "90.00")
        self.assertEqual(engine.executions[0].inputs["当期折耗率"], "0.1")

    def test_workload_uses_configured_pool_and_caps_at_asset_net(self):
        policy = DepreciationPolicy("P", "工作量法", "9800", "BUDGET", "C", "WORKLOAD", 120, Decimal("0"), "CURRENT_MONTH")
        engine = DepreciationCalculationEngine(StubResolver(policy))
        lines = engine.forecast(
            scenario_id="S", budget_version="B", start_period=Month.parse("2026-09"), months=1,
            fixed_assets=[asset(asset_id="A-1", code="Z901", block=None)], planned_assets=[], events=[],
            monthly_drivers=[MonthlyDriver(
                "WORKLOAD", Month.parse("2026-09"), "9800", "9800",
                total_amortization=Decimal("3000"), pool_opening_net_value=Decimal("1000"),
            )],
        )
        self.assertEqual(str(lines[0].monthly_depreciation), "1000.00")
        self.assertEqual(engine.executions[0].branch_id, "WORKLOAD_FULL_AMORTIZATION")


if __name__ == "__main__":
    unittest.main()
