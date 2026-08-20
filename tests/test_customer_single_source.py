import sys
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from depreciation_poc.app.demo_server import DemoState
from depreciation_poc.domain.models import Month
from depreciation_poc.infrastructure.customer_excel_repository import CustomerExcelRepository
from depreciation_poc.infrastructure.in_memory_ontology_store import InMemoryOntologyStore


DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "customer_snapshot"


class CustomerSingleSourceTest(unittest.TestCase):
    def make_state(self, root: Path) -> DemoState:
        repository = CustomerExcelRepository(DATA_DIR)
        start_period, end_period = repository.baseline_period_range()
        with patch.dict("os.environ", {"NEO4J_ENABLED": "false"}):
            return DemoState(
                customer_data_dir=DATA_DIR,
                business_db_path=root / "business.sqlite",
                start_period=start_period,
                months=start_period.months_until(end_period) + 1,
                ontology_store=InMemoryOntologyStore(),
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
                    "资产相关配置表_20260819.xlsx",
                    "组织机构表_所属单位表_20260810.xlsx",
                ])
                self.assertEqual(snapshot["organization_count"], 60)
                self.assertEqual(snapshot["forecast_periods"][0], "2025-01")
                self.assertEqual(snapshot["forecast_periods"][-1], "2027-12")
                self.assertEqual(len(snapshot["forecast_periods"]), 36)
                periods = state.wide_table({"scenario_id": ["BASELINE"]})["periods"]
                self.assertEqual((periods[0], periods[-1], len(periods)), ("2025-01", "2027-12", 36))
                self.assertEqual([item["scenario_id"] for item in state.scenarios()], ["BASELINE"])
                cards = state.assets_cards({"scenario_id": ["BASELINE"]})
                self.assertFalse(any(item["asset_ref"].startswith(("PA-", "FA-")) for item in cards))
                with self.assertRaisesRegex(ValueError, "超出当前源数据覆盖范围"):
                    state.forecast_lines({"scenario_id": ["BASELINE"], "period_to": ["2028-01"]})
            finally:
                state.close()

    def test_rejects_missing_or_extra_workbooks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "只能包含当前三份受控 Excel"):
                CustomerExcelRepository(root)
            for source in DATA_DIR.iterdir():
                (root / source.name).write_bytes(source.read_bytes())
            (root / "旧版本资产台账.xlsx").write_bytes(b"not-a-workbook")
            with self.assertRaisesRegex(ValueError, "只能包含当前三份受控 Excel"):
                CustomerExcelRepository(root)

    def test_rebuilt_rules_use_document_formulas_for_production_and_workload(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                all_lines = state.business_store.forecast_lines(
                    scenario_id="BASELINE", period_from="2026-07", period_to="2026-08", limit=10000,
                )
                production = [item for item in all_lines if item["asset_id"] == "201000121705-0"]
                self.assertEqual([item["monthly_depreciation"] for item in production], ["774.68", "602.25"])

                workload = [item for item in all_lines if item["asset_id"] == "401000003180-0"]
                self.assertEqual([item["monthly_depreciation"] for item in workload], ["1625713.27", "0.00"])
                execution = state.business_store.rule_executions(
                    scenario_id="BASELINE", asset_refs=["401000003180-0"], period="2026-07",
                )[0]
                self.assertEqual(execution["inputs"]["当月总摊销额"], "55270800.00")
                self.assertEqual(execution["inputs"]["配置资产池期初净额"], "1081902.00")
            finally:
                state.close()

    def test_company_reverse_plan_prefers_exact_accounting_adjustment(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                result = state._optimize_reverse_plan({
                    "scenario_id": "BASELINE",
                    "target_period": "2026-08",
                    "scope_type": "company",
                    "scope_value": "9800",
                    "target_amount": Decimal("512731.50"),
                    "required_delta": Decimal("-10000.00"),
                }, Decimal("522731.50"))
                recommendations = result["recommendations"] + result["operational_fallback_recommendations"]
                self.assertTrue(result["optimization"]["is_exact"])
                self.assertTrue(result["optimization"]["accounting_is_exact"])
                self.assertEqual(recommendations[0]["target_amount"], "512731.50")
                self.assertEqual(recommendations[0]["gap"], "0.00")
                self.assertEqual(recommendations[0]["assumptions"][0]["template_id"], "straight_impairment")
                self.assertEqual(recommendations[0]["assumptions"][0]["asset_id"], "401000023122-0")
                self.assertEqual(result["operational_fallback_recommendations"], [])
            finally:
                state.close()

    def test_full_horizon_is_snapshot_anchored_and_missing_drivers_are_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                lines = state.business_store.forecast_lines(scenario_id="BASELINE", limit=20000)
                self.assertEqual(len(lines), 279 * 36)
                target = [item for item in lines if item["asset_id"] == "201000121705-0"]
                self.assertEqual(len(target), 36)
                by_period = {item["period"]: item for item in target}
                self.assertEqual(by_period["2026-05"]["closing_net_value"], by_period["2026-06"]["opening_net_value"])
                self.assertEqual(by_period["2026-06"]["closing_net_value"], by_period["2026-07"]["opening_net_value"])
                self.assertEqual(by_period["2026-06"]["validation_status"], "SOURCE_SNAPSHOT")
                self.assertEqual(by_period["2025-01"]["validation_status"], "HISTORICAL_RECONSTRUCTED")

                drivers = {
                    (item.driver_type, item.target_id, str(item.period)): item
                    for item in state.repository.baseline_drivers(start_period=Month(2025, 1), months=36)
                }
                missing_block = drivers[("PRODUCTION", "98000009", "2026-09")]
                self.assertEqual((missing_block.production, missing_block.reserves), (0, 0))
                self.assertIn("按零值处理", missing_block.assumption_note)
                missing_workload = drivers[("WORKLOAD", "9800000100090018", "2027-09")]
                self.assertEqual(missing_workload.total_amortization, None)
            finally:
                state.close()

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
                departments = [item for item in graph["nodes"] if item["object_type"] == "Department"]
                self.assertEqual(len(assets), 428)
                self.assertEqual(sum(bool(item["properties"].get("calculation_included")) for item in assets), 279)
                self.assertEqual(sum(not bool(item["properties"].get("calculation_included")) for item in assets), 149)
                self.assertFalse(any(str(item["technical_ref"]).startswith(("PA-", "FA-")) for item in assets))
                self.assertTrue(all("客户" in str(item["source_system"]) for item in assets))
                self.assertGreater(graph["summary"]["edge_count"], 0)
                target_department = next(item for item in departments if item["properties"].get("code") == "9800000100090018")
                self.assertEqual(target_department["properties"]["parent_code"], "980000010009")

                detail = state.knowledge_graph_node({
                    "scenario_id": ["BASELINE"],
                    "id": [str(assets[0]["id"])],
                })
                self.assertEqual(detail["node"]["object_id"], assets[0]["id"])
                self.assertTrue(detail["related_nodes"])
                self.assertIn("asset_ref", detail["node"]["properties"])
            finally:
                state.close()

    def test_knowledge_chat_impairment_impact_uses_rule_engine_and_ontology_gateway(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                plan = state.knowledge_chat._mandatory_impairment_impact_plan(
                    "如果资产101000146848-0在2026年7月计提减值1000元，和当前的7月折旧比差多少？"
                )
                self.assertEqual(plan["tool_calls"][0]["name"], "simulate_asset_impairment_impact")
                self.assertEqual(plan["tool_calls"][0]["arguments"], {
                    "asset_ref": "101000146848-0", "period": "2026-07", "amount": "1000",
                })
                evidence = state._execute_knowledge_chat_tool(
                    "simulate_asset_impairment_impact", plan["tool_calls"][0]["arguments"], "BASELINE",
                )
                item = evidence["items"][0]
                self.assertEqual(item["baseline_monthly_depreciation"], "61.48")
                self.assertEqual(item["simulated_monthly_depreciation"], "53.01")
                self.assertEqual(item["difference"], "-8.47")
                self.assertEqual(evidence["summary"]["ontology_gateway"]["status"], "verified")
                self.assertFalse(item["scenario_written"])
            finally:
                state.close()

    def test_knowledge_chat_asset_detail_includes_production_rule_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                evidence = state._execute_knowledge_chat_tool(
                    "get_asset_detail",
                    {"asset_ref": "201000120127", "periods": ["2026-07", "2026-08"]},
                    "BASELINE",
                )
                lines = [item["text"] for item in evidence["items"] if item["type"] == "forecast_line"]
                self.assertEqual(len(lines), 2)
                self.assertTrue(all("规则输入" in item for item in lines))
                self.assertTrue(all("当期产量" in item and "当期总储量" in item for item in lines))
                self.assertIn("480281.6072", lines[0])
                self.assertIn("381768.6072", lines[1])
                gateway = evidence["summary"]["ontology_gateway"]
                self.assertEqual(gateway["status"], "verified")
                self.assertTrue(gateway["query_executed"])
                self.assertEqual(gateway["access"], "python_neo4j_driver_controlled_cypher")
                self.assertTrue(any(item["object_id"] == "FixedAsset:201000120127-0" for item in gateway["checked_anchors"]))
            finally:
                state.close()

    def test_knowledge_chat_no_match_is_only_reported_after_ontology_gateway_query(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                evidence = state._execute_knowledge_chat_tool(
                    "resolve_business_term", {"term": "不存在的测试资产类别"}, "BASELINE",
                )
                gateway = evidence["summary"]["ontology_gateway"]
                self.assertEqual(evidence["summary"]["matched_count"], 0)
                self.assertEqual(gateway["status"], "missing_after_query")
                self.assertTrue(gateway["query_executed"])
                self.assertTrue(any(item["object_id"] == "Scenario:BASELINE" for item in gateway["checked_anchors"]))
            finally:
                state.close()

    def test_chat_what_if_draft_requires_confirmation_and_cannot_execute_twice(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                asset = next(item for item in state.repository.load_fixed_assets() if item.depreciation_code == "Z112")
                draft = state.create_chat_what_if_draft({
                    "conversation_id": "CHAT-TEST", "base_scenario_id": "BASELINE", "scenario_name": "聊天草稿测试",
                    "assumptions": [{"template_id": "straight_impairment", "asset_id": asset.asset_id, "amount": "100", "effective_date": "2026-07-01"}],
                })
                self.assertEqual(draft["status"], "PENDING")
                self.assertEqual(len(state.scenarios()), 1)
                self.assertEqual(draft["ontology_gateway"]["status"], "verified")
                confirmed = state.confirm_chat_action_draft(draft["draft_id"])
                self.assertEqual(confirmed["status"], "CONFIRMED")
                self.assertEqual(len(state.scenarios()), 2)
                self.assertEqual(confirmed["ontology_gateway"]["status"], "verified")
                with self.assertRaisesRegex(ValueError, "不能重复执行"):
                    state.confirm_chat_action_draft(draft["draft_id"])
            finally:
                state.close()

    def test_chat_scenario_comparison_returns_ontology_grounded_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                asset = next(item for item in state.repository.load_fixed_assets() if item.depreciation_code == "Z112")
                created = state.create_customer_scenario({
                    "base_scenario_id": "BASELINE", "scenario_name": "对比测试",
                    "assumptions": [{"template_id": "straight_impairment", "asset_id": asset.asset_id, "amount": "100", "effective_date": "2026-07-01"}],
                })
                scenario_id = created["scenario"]["scenario_id"]
                evidence = state._execute_knowledge_chat_tool(
                    "compare_scenarios", {"baseline_scenario_id": "BASELINE", "scenario_ids": [scenario_id]}, "BASELINE",
                )
                self.assertEqual(evidence["items"][0]["type"], "comparison_result")
                self.assertEqual(evidence["summary"]["ontology_gateway"]["status"], "verified")
            finally:
                state.close()

    def test_knowledge_chat_rule_execution_follows_ontology_to_monthly_driver(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                evidence = state._execute_knowledge_chat_tool(
                    "get_rule_execution",
                    {"asset_ref": "201000139202", "period": "2027-05"},
                    "BASELINE",
                )
                item = evidence["items"][0]
                self.assertIn("Ontology路径", item["text"])
                self.assertIn("区块 98000031", item["text"])
                self.assertIn("693.2298", item["text"])
                trace = item["ontology_driver_evidence"]
                self.assertEqual(trace["driver"]["properties"]["production"], "693.2298")
                self.assertEqual(
                    [link["link_type"] for link in trace["path"]],
                    ["assetBelongsToBlock", "blockHasMonthlyDriver", "driverAffectsMethod"],
                )
            finally:
                state.close()

    def test_knowledge_chat_auto_expands_material_asset_rankings_to_rule_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                ranking = state._execute_knowledge_chat_tool(
                    "get_monthly_summary", {"period": "2026-07", "group_by": "asset", "top_n": 2}, "BASELINE",
                )
                asset_refs = ranking["summary"]["trace_asset_refs"]
                self.assertTrue(asset_refs)
                calls = state.knowledge_chat._required_asset_trace_calls(
                    tool_name="get_monthly_summary",
                    arguments={"period": "2026-07", "_conversation_id": "CHAT-TEST"}, result=ranking,
                )
                self.assertEqual(len(calls), len(asset_refs) * 2)
                self.assertEqual(calls[0][0], "get_asset_detail")
                self.assertEqual(calls[1][0], "get_rule_execution")
                self.assertEqual(calls[0][1]["periods"], ["2026-06", "2026-07"])
            finally:
                state.close()

    def test_knowledge_chat_forces_monthly_variance_evidence_for_chinese_year_month(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                plan = state.knowledge_chat._mandatory_variance_plan("26年7月折旧大幅上升是因为什么？")
                self.assertEqual(plan["tool_calls"][0]["name"], "explain_monthly_change")
                self.assertEqual(plan["tool_calls"][0]["arguments"]["period"], "2026-07")
                evidence = state._execute_knowledge_chat_tool(
                    "explain_monthly_change", {"period": "2026-07", "top_n": 5}, "BASELINE",
                )
                self.assertEqual(evidence["summary"]["previous_total"], "557674.48")
                self.assertEqual(evidence["summary"]["current_total"], "5419196.28")
                self.assertIn("401000003280-0", evidence["summary"]["trace_asset_refs"])
                follow_ups = state.knowledge_chat._required_asset_trace_calls(
                    tool_name="explain_monthly_change", arguments={"period": "2026-07"}, result=evidence,
                )
                self.assertTrue(any(name == "get_rule_execution" for name, _args in follow_ups))
            finally:
                state.close()

    def test_ontology_covers_all_nonempty_source_fields_and_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                objects = state.neo4j_store.objects()
                meta = state.neo4j_store.ontology_meta()
                properties_by_type = {
                    item["type_id"]: {prop["property_id"] for prop in item["properties"]}
                    for item in meta["object_types"]
                }

                asset_records = state.repository.ontology_asset_records()
                nonempty_asset_fields = set().union(*(set(item["properties"]) for item in asset_records))
                self.assertTrue(nonempty_asset_fields.issubset(properties_by_type["FixedAsset"]))

                organization_units = state.repository.organization_units()
                nonempty_org_fields = set().union(*(set(item["source_properties"]) for item in organization_units))
                self.assertTrue(nonempty_org_fields.issubset(properties_by_type["Department"]))

                counts = {}
                for item in objects:
                    counts[item["object_type"]] = counts.get(item["object_type"], 0) + 1
                self.assertEqual(counts["FixedAsset"], 428)
                self.assertEqual(counts["Department"], 60)
                self.assertEqual(counts["AssetCategoryPolicyConfig"], 118)
                self.assertGreaterEqual(counts["MonthlyDriver"], 738)

                cost_center = next(item for item in objects if item["object_id"] == "CostCenter:980005005R")
                self.assertIn("C4772", cost_center["properties"]["description"])
                self.assertNotEqual(cost_center["properties"]["name"], "980005005R")

                source_types = {"FixedAsset", "Department", "DepreciationCode", "AssetCategoryPolicyConfig", "MonthlyDriver"}
                source_objects = [item for item in objects if item["object_type"] in source_types]
                for object_type in source_types:
                    values = [item["properties"] for item in source_objects if item["object_type"] == object_type]
                    for property_id in properties_by_type.get(object_type, set()):
                        self.assertTrue(
                            any(props.get(property_id) not in (None, "") for props in values),
                            f"{object_type}.{property_id} should not be an all-empty ontology property",
                        )
            finally:
                state.close()

    def test_sqlite_contains_no_ontology_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                ontology_count = state.neo4j_store.counts()["node_count"]
                state.business_store.reset_business_data()
                self.assertEqual(state.neo4j_store.counts()["node_count"], ontology_count)
                self.assertIsNone(state.business_store.scenario("BASELINE"))
                self.assertEqual(state.business_store._one("select count(*) as count from ontology_objects")["count"], 0)
                self.assertEqual(state.business_store._one("select count(*) as count from object_types")["count"], 0)
            finally:
                state.close()

    def test_knowledge_chat_does_not_replace_deepseek_with_template_answer(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                state.knowledge_chat.available_api_key = ""
                events = list(state.stream_knowledge_chat({
                    "scenario_id": "BASELINE",
                    "messages": [
                        {"role": "user", "content": "资产201000121705-0在7月折旧是多少？"},
                        {"role": "assistant", "content": "已查询7月折旧。"},
                        {"role": "user", "content": "它8月呢？"},
                    ],
                    "question": "它8月呢？",
                }))
                self.assertEqual(events[0]["type"], "meta")
                self.assertEqual(events[0]["protocol_version"], "knowledge-agent-v2")
                self.assertEqual(events[-1]["type"], "error")
                self.assertEqual(events[-1]["code"], "DEEPSEEK_NOT_AVAILABLE")
                self.assertFalse(any(item["type"] == "delta" for item in events))
            finally:
                state.close()

    def test_knowledge_chat_uses_model_plan_to_call_controlled_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                service = state.knowledge_chat
                service.available_api_key = "test-key"
                calls = []

                def fake_plan_tools(*, observations, **_kwargs):
                    if observations:
                        return {"action": "ready", "tool_calls": []}
                    return {
                        "action": "tool_calls",
                        "tool_calls": [{
                            "name": "get_asset_detail",
                            "arguments": {"asset_ref": "201000121705-0", "periods": ["2026-07"]},
                        }],
                    }

                def fake_executor(name, arguments, scenario_id):
                    calls.append((name, arguments, scenario_id))
                    return state._execute_knowledge_chat_tool(name, arguments, scenario_id)

                service._plan_tools = fake_plan_tools
                service.tool_executor = fake_executor
                service._stream_model = lambda **_kwargs: iter(["根据工具证据，7月折旧为774.68元。"])
                events = list(service.stream({
                    "scenario_id": "BASELINE",
                    "question": "这项资产7月折旧是多少？",
                    "messages": [{"role": "user", "content": "这项资产7月折旧是多少？"}],
                    "external_model_consent": True,
                }))
                self.assertEqual(calls[0][0], "get_asset_detail")
                self.assertTrue(any(item["type"] == "progress" and item.get("stage") == "tool" for item in events))
                done = events[-1]
                self.assertTrue(done["used_llm"])
                self.assertEqual(done["protocol_version"], "knowledge-agent-v2")
                self.assertEqual(done["tool_trace"][0]["tool"], "get_asset_detail")
            finally:
                state.close()

    def test_knowledge_chat_rejects_unconfirmed_tool_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                service = state.knowledge_chat
                service.available_api_key = "test-key"
                service._plan_tools = lambda **_kwargs: {
                    "action": "tool_calls",
                    "tool_calls": [{"name": "get_source_snapshot", "arguments": {}}],
                }
                service.tool_executor = lambda *_args: {
                    "items": [{"type": "unsafe", "title": "unsafe", "text": "unsafe"}],
                    "sources": [],
                    "summary": {},
                }
                events = list(service.stream({
                    "scenario_id": "BASELINE",
                    "question": "当前数据快照是什么？",
                    "messages": [{"role": "user", "content": "当前数据快照是什么？"}],
                    "external_model_consent": True,
                }))
                self.assertEqual(events[-1]["type"], "error")
                self.assertIn("Ontology Evidence Gateway", events[-1]["error"])
            finally:
                state.close()

    def test_knowledge_chat_accepts_wrapped_or_python_style_tool_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                service = state.knowledge_chat
                fenced = service._decode_tool_plan("```json\n{\"action\": \"ready\", \"tool_calls\": []}\n```")
                python_style = service._decode_tool_plan("{'action': 'ready', 'tool_calls': []}")
                nested = service._decode_tool_plan("\"{\\\"action\\\": \\\"ready\\\", \\\"tool_calls\\\": []}\"")
                self.assertEqual(fenced["action"], "ready")
                self.assertEqual(python_style["tool_calls"], [])
                self.assertEqual(nested["action"], "ready")
                fallback = service._fallback_tool_plan(
                    messages=[{"role": "user", "content": "资产201000120127-0在2026-07折旧是多少？"}], scenario_id="BASELINE",
                )
                self.assertEqual(fallback["tool_calls"][0]["name"], "get_asset_detail")
            finally:
                state.close()

    def test_business_term_resolution_returns_auditable_no_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            try:
                resolved = state._execute_knowledge_chat_tool(
                    "resolve_business_term", {"term": "煤矿类资产"}, "BASELINE",
                )
                self.assertEqual(resolved["summary"]["matched_count"], 0)
                self.assertIn("没有直接匹配", resolved["items"][0]["text"])
                self.assertIn("必须由用户确认", resolved["items"][0]["text"])

                pumping_unit = state._execute_knowledge_chat_tool(
                    "resolve_business_term", {"term": "抽油机井"}, "BASELINE",
                )
                self.assertTrue(any(
                    item["object_type"] == "AssetCategory" and item["id"] == "01010002"
                    for item in pumping_unit["summary"]["matches"]
                ))

                policy = state._execute_knowledge_chat_tool(
                    "get_category_policy", {"category": "01010002"}, "BASELINE",
                )
                self.assertGreaterEqual(policy["summary"]["match_count"], 1)
                self.assertIn("方法 PRODUCTION", policy["items"][0]["text"])
                self.assertIn("折旧年限 10 年", policy["items"][0]["text"])

                cost_center = state._execute_knowledge_chat_tool(
                    "resolve_business_term", {"term": "成本中心980005005R"}, "BASELINE",
                )
                self.assertTrue(any(
                    item["object_type"] == "CostCenter" and item["id"] == "CostCenter:980005005R"
                    for item in cost_center["summary"]["matches"]
                ))

                asset_detail = state._execute_knowledge_chat_tool(
                    "get_asset_detail", {"asset_ref": "101000146848-0"}, "BASELINE",
                )
                self.assertIn("成本中心：98000300G6", asset_detail["items"][0]["text"])
                self.assertIn("利润中心：9800100013", asset_detail["items"][0]["text"])

                raw_node = state._execute_knowledge_chat_tool(
                    "get_ontology_node", {"object_id": "101000146848-0"}, "BASELINE",
                )
                self.assertEqual(raw_node["items"][0]["object_id"], "FixedAsset:101000146848-0")
                self.assertIn("98000300G6", raw_node["items"][0]["text"])

                ontology = state._execute_knowledge_chat_tool(
                    "search_ontology", {"query": "06109901", "object_type": "资产类别"}, "BASELINE",
                )
                self.assertGreaterEqual(ontology["summary"]["match_count"], 1)
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
