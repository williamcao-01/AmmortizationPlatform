import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from depreciation_poc.app.demo_server import DemoState
from depreciation_poc.domain.models import Month
from depreciation_poc.infrastructure.customer_excel_repository import CustomerExcelRepository


DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "customer_snapshot"


class CustomerSingleSourceTest(unittest.TestCase):
    def make_state(self, root: Path) -> DemoState:
        repository = CustomerExcelRepository(DATA_DIR)
        start_period = Month.parse(repository.source_summary()["snapshot_period"]).add(1)
        return DemoState(
            customer_data_dir=DATA_DIR,
            graph_db_path=root / "graph.sqlite",
            business_db_path=root / "business.sqlite",
            start_period=start_period,
            months=repository.verified_forecast_months(start_period, maximum=6),
        )

    def test_only_current_workbooks_and_covered_periods_are_used(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                snapshot = state.snapshot_status()
                self.assertEqual(snapshot["source_mode"], "customer_excel_only")
                self.assertEqual(snapshot["asset_count"], 279)
                self.assertEqual(snapshot["excluded_asset_count"], 149)
                self.assertEqual(snapshot["source_files"], [
                    "资产明细表_资产台账明细_20260812.xlsx",
                    "资产相关配置表_20260812.xlsx",
                ])
                self.assertEqual(snapshot["forecast_periods"], ["2026-07", "2026-08"])
                self.assertEqual(
                    state.wide_table({"scenario_id": ["BASELINE"]})["periods"],
                    ["2026-06", "2026-07", "2026-08"],
                )
                self.assertEqual([item["scenario_id"] for item in state.scenarios()], ["BASELINE"])
                cards = state.assets_cards({"scenario_id": ["BASELINE"]})
                self.assertFalse(any(item["asset_ref"].startswith(("PA-", "FA-")) for item in cards))
                with self.assertRaisesRegex(ValueError, "超出当前源数据覆盖范围"):
                    state.forecast_lines({"scenario_id": ["BASELINE"], "period_to": ["2026-09"]})
            finally:
                state.close()

    def test_rejects_missing_or_extra_workbooks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "只能包含当前两份受控 Excel"):
                CustomerExcelRepository(root)
            for source in DATA_DIR.iterdir():
                (root / source.name).write_bytes(source.read_bytes())
            (root / "旧版本资产台账.xlsx").write_bytes(b"not-a-workbook")
            with self.assertRaisesRegex(ValueError, "只能包含当前两份受控 Excel"):
                CustomerExcelRepository(root)


if __name__ == "__main__":
    unittest.main()
