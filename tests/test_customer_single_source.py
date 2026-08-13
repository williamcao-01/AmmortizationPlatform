import sys
import tempfile
import unittest
from unittest.mock import patch
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
        with patch.dict("os.environ", {"NEO4J_ENABLED": "false"}):
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

    def test_asset_detail_exposes_read_only_rule_context_for_what_if(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                production_asset = next(
                    item for item in state.assets_cards({"scenario_id": ["BASELINE"]})
                    if item["depreciation_method"] == "PRODUCTION"
                )
                detail = state.asset_detail({
                    "scenario_id": ["BASELINE"],
                    "asset_ref": [production_asset["asset_ref"]],
                })
                self.assertEqual(detail["asset"]["asset_ref"], production_asset["asset_ref"])
                self.assertEqual(detail["driver_context"]["driver_type"], "PRODUCTION")
                self.assertTrue(detail["driver_context"]["target_id"])
                self.assertIn("2026-07", detail["driver_context"]["by_period"])
                self.assertIn("useful_life_months", detail["source_context"])
            finally:
                state.close()

    def test_knowledge_graph_contains_only_customer_business_objects_and_exposes_node_details(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                graph = state.knowledge_graph({"scenario_id": ["BASELINE"], "focus": ["full"]})
                assets = [item for item in graph["nodes"] if item["object_type"] == "FixedAsset"]
                self.assertEqual(len(assets), 279)
                self.assertFalse(any(str(item["technical_ref"]).startswith(("PA-", "FA-")) for item in assets))
                self.assertTrue(all("客户" in str(item["source_system"]) for item in assets))
                self.assertGreater(graph["summary"]["edge_count"], 0)

                detail = state.knowledge_graph_node({
                    "scenario_id": ["BASELINE"],
                    "id": [str(assets[0]["id"])],
                })
                self.assertEqual(detail["node"]["object_id"], assets[0]["id"])
                self.assertTrue(detail["related_nodes"])
                self.assertIn("asset_ref", detail["node"]["properties"])
            finally:
                state.close()

    def test_scenario_compare_supports_overview_and_two_level_drilldown(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                asset = next(item for item in state.repository.load_fixed_assets() if item.depreciation_code == "Z112")
                state.create_customer_scenario({
                    "base_scenario_id": "BASELINE",
                    "scenario_name": "场景对比下钻测试",
                    "assumptions": [{
                        "template_id": "straight_impairment",
                        "asset_id": asset.asset_id,
                        "amount": "100",
                        "effective_date": "2026-07-01",
                    }],
                })
                overview = state.wide_table_compare({
                    "scenario_ids": ["BASELINE", "SCN-001"],
                    "period_from": "2026-06",
                    "period_to": "2026-08",
                })
                self.assertEqual(overview["dimensions"], [])
                self.assertEqual(overview["tree"][0]["dimension"], "scope_label")
                self.assertIn("SCN-001", overview["tree"][0]["months"]["2026-07"])

                drilldown = state.wide_table_compare({
                    "scenario_ids": ["BASELINE", "SCN-001"],
                    "dimensions": ["department", "depreciation_code"],
                    "period_from": "2026-06",
                    "period_to": "2026-08",
                })
                self.assertEqual(drilldown["dimensions"], ["department", "depreciation_code"])
                self.assertEqual(drilldown["tree"][0]["dimension"], "department")
                self.assertTrue(drilldown["tree"][0]["children"])
                self.assertEqual(drilldown["tree"][0]["children"][0]["dimension"], "depreciation_code")
            finally:
                state.close()

    def test_saved_what_if_scenario_survives_state_restart_and_can_be_deleted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.make_state(root)
            asset = next(item for item in first.repository.load_fixed_assets() if item.depreciation_code == "Z112")
            try:
                created = first.create_customer_scenario({
                    "base_scenario_id": "BASELINE",
                    "scenario_name": "持久化测试场景",
                    "assumptions": [{
                        "template_id": "straight_impairment",
                        "asset_id": asset.asset_id,
                        "amount": "100",
                        "effective_date": "2026-07-01",
                    }],
                })
                self.assertEqual(created["scenario"]["scenario_id"], "SCN-001")
            finally:
                first.close()

            second = self.make_state(root)
            try:
                persisted = second.scenario_detail("SCN-001")
                self.assertEqual(persisted["scenario"]["scenario_name"], "持久化测试场景")
                self.assertEqual(len(persisted["assumptions"]), 1)
                self.assertTrue(persisted["dashboard"]["kpis"]["forecast_line_count"])
                second.delete_scenario("SCN-001")
                self.assertIsNone(second.business_store.scenario("SCN-001"))
                self.assertEqual([item["scenario_id"] for item in second.scenarios()], ["BASELINE"])
            finally:
                second.close()


if __name__ == "__main__":
    unittest.main()
