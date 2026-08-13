import json
import sys
from pathlib import Path
from decimal import Decimal
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from depreciation_poc.qa.reverse_planning import ReversePlanningSkill
from depreciation_poc.qa.skill import FallbackWideTableQAProvider, WideTableQASkill
from depreciation_poc.qa.skill import ConversationState


class FakeResponse:
    def __init__(self, content=None):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def read(self):
        content = self.content
        if content is None:
            content = json.dumps(
                {"answer_cn": "这是大模型基于结构化证据生成的回答。"},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": content
                        }
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8")


class WideTableQAProviderTest(unittest.TestCase):
    def test_provider_uses_two_independent_deepseek_calls(self):
        plan_context = {
            "question": "比较 2026-08 与 2026-07 的折旧变化",
            "available_periods": ["2026-07", "2026-08"],
        }
        answer_context = {"template_answer_cn": "模板答案：A-001。", "significant_asset_refs": ["A-001"]}
        responses = [
            FakeResponse(json.dumps({"intent": "period_variance", "scope": {}, "target_period": "2026-08", "comparison_period": "2026-07", "requested_evidence": ["comparison"], "resolved_entities": {}, "confidence": "high"}, ensure_ascii=False)),
            FakeResponse(json.dumps({"answer_cn": "A-001 是主要差异资产。", "key_findings": ["A-001"], "next_steps": []}, ensure_ascii=False)),
        ]
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key", "DEEPSEEK_MODEL": "test-model", "DEEPSEEK_BASE_URL": "https://example.test/v1"}):
            with patch("urllib.request.urlopen", side_effect=responses) as urlopen:
                provider = FallbackWideTableQAProvider()
                plan = provider.plan_question(plan_context)
                answer = provider.compose_answer(answer_context)
        self.assertTrue(plan["used_llm"])
        self.assertEqual(plan["intent"], "period_variance")
        self.assertTrue(answer["used_llm"])
        self.assertIn("A-001", answer["answer_cn"])
        self.assertEqual(urlopen.call_count, 2)

    def test_provider_requests_json_mode_and_retries_a_blank_composition(self):
        context = {"template_answer_cn": "模板答案", "key_asset_refs": ["A-001"]}
        responses = [
            FakeResponse(json.dumps({"answer_cn": "", "key_findings": [], "next_steps": []}, ensure_ascii=False)),
            FakeResponse(json.dumps({"answer_cn": "A-001 是关键归因资产。", "key_findings": [], "next_steps": []}, ensure_ascii=False)),
        ]
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key", "DEEPSEEK_MODEL": "test-model", "DEEPSEEK_BASE_URL": "https://example.test/v1"}):
            with patch("urllib.request.urlopen", side_effect=responses) as urlopen:
                result = FallbackWideTableQAProvider().compose_answer(context)

        self.assertTrue(result["used_llm"])
        self.assertIn("A-001", result["answer_cn"])
        self.assertEqual(urlopen.call_count, 2)
        request_payload = json.loads(urlopen.call_args_list[0].args[0].data.decode("utf-8"))
        self.assertEqual(request_payload["response_format"], {"type": "json_object"})
        self.assertEqual(request_payload["thinking"], {"type": "disabled"})

    def test_provider_unwraps_nested_json_in_answer_field(self):
        nested = json.dumps({"answer_cn": "A-001 是关键归因资产。", "key_findings": [], "next_steps": []}, ensure_ascii=False)
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key", "DEEPSEEK_MODEL": "test-model", "DEEPSEEK_BASE_URL": "https://example.test/v1"}):
            with patch("urllib.request.urlopen", return_value=FakeResponse(json.dumps({"answer_cn": nested}, ensure_ascii=False))):
                result = FallbackWideTableQAProvider().compose_answer({"template_answer_cn": "模板答案"})

        self.assertEqual(result["answer_cn"], "A-001 是关键归因资产。")

    def test_provider_calls_deepseek_endpoint_when_key_exists(self):
        context = {
            "question": "房屋建筑为何27年4月有大幅提升",
            "template_answer_cn": "模板答案",
            "facts": {"difference": Decimal("19791.67")},
        }
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key", "DEEPSEEK_MODEL": "test-model", "DEEPSEEK_BASE_URL": "https://example.test/v1"}):
            with patch("urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
                result = FallbackWideTableQAProvider().answer(context)

        self.assertTrue(result["used_llm"])
        self.assertEqual(result["provider"], "deepseek")
        self.assertEqual(result["model"], "test-model")
        self.assertEqual(result["answer_cn"], "这是大模型基于结构化证据生成的回答。")
        self.assertTrue(urlopen.called)

    def test_provider_accepts_plain_text_model_answer(self):
        context = {
            "question": "测试单位 8月的折旧提高是因为什么",
            "template_answer_cn": "模板答案",
            "facts": {"difference": Decimal("14250.01")},
        }
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key", "DEEPSEEK_MODEL": "test-model", "DEEPSEEK_BASE_URL": "https://example.test/v1"}):
            with patch("urllib.request.urlopen", return_value=FakeResponse("测试单位的提升来自 ASSET-TEST-004。")):
                result = FallbackWideTableQAProvider().answer(context)

        self.assertTrue(result["used_llm"])
        self.assertEqual(result["provider"], "deepseek")
        self.assertEqual(result["answer_cn"], "测试单位的提升来自 ASSET-TEST-004。")

    def test_provider_marks_template_fallback_without_key(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}):
            result = FallbackWideTableQAProvider().answer({"template_answer_cn": "模板答案"})

        self.assertFalse(result["used_llm"])
        self.assertEqual(result["provider"], "template_fallback")
        self.assertEqual(result["answer_cn"], "模板答案")

    def test_generation_validation_replaces_incomplete_llm_answer_with_complete_template(self):
        comparison = {
            "material_drivers": [
                {
                    "asset_ref": "ASSET-TEST-007",
                    "abs_difference": "9700.00",
                    "driver_reason_cn": "ASSET-TEST-007 在测试期间停止计提，属于折旧到期/停止计提。",
                }
            ]
        }
        result = WideTableQASkill._validated_generation(
            {"answer_cn": "测试单位下跌原因不明确。"},
            {"template_answer_cn": "模板答案：ASSET-TEST-007 在测试期间停止计提。"},
            comparison,
        )

        self.assertIn("ASSET-TEST-007", result["answer_cn"])
        self.assertTrue(result["evidence_complete_template_used"])

    def test_composition_keeps_full_asset_list_in_evidence_but_limits_llm_key_drivers(self):
        drivers = [
            {
                "asset_ref": f"ASSET-TEST-{index:03d}",
                "difference": str(1000 if index % 2 else -1000),
                "abs_difference": "1000",
                "depreciation_code": "Z901" if index % 2 else "Z802",
                "depreciation_code_label_cn": "工作量法" if index % 2 else "产量法",
                "driver_category": "snapshot_forecast_transition",
            }
            for index in range(1, 40)
        ]
        key_drivers = WideTableQASkill._key_drivers_for_composition(drivers)
        evidence = WideTableQASkill._composition_evidence(
            {
                "comparison": {"material_drivers": drivers},
                "facts": {},
                "rule_execution_trace": [],
                "ontology_paths": [],
            },
            key_drivers,
        )

        self.assertLessEqual(len(key_drivers), 8)
        self.assertEqual(evidence["facts"]["all_significant_assets_available_in_ui"], 39)
        self.assertLessEqual(len(evidence["facts"]["key_drivers"]), 8)
        self.assertLessEqual(len(WideTableQASkill._required_answer_refs(drivers)), 3)
        self.assertNotIn("driver_reason_cn", evidence["facts"]["key_drivers"][0])

    def test_explicit_period_variance_repairs_even_when_model_protocol_falls_back(self):
        skill = object.__new__(WideTableQASkill)
        result = skill._validate_question_plan(
            plan={
                "intent": "clarification",
                "clarification": True,
                "_question": "7月折旧额比6月高，为什么",
                "used_llm": False,
            },
            default_scope={"scenario_id": "BASELINE", "row_type": "overview"},
            conversation=ConversationState(
                conversation_id="test", created_at=None, updated_at=None,
                active_scope={}, resolved_entities={},
            ),
            available_periods=["2026-06", "2026-07", "2026-08"],
        )
        self.assertTrue(result["valid"])
        self.assertEqual(result["plan"]["intent"], "period_variance")
        self.assertEqual(result["plan"]["target_period"], "2026-07")
        self.assertEqual(result["plan"]["comparison_period"], "2026-06")

    def test_model_ontology_paths_exclude_raw_graph_diagnostics(self):
        paths = [{
            "driver_paths": [{
                "asset_ref": "ASSET-TEST-001",
                "driver_reason_cn": "按产量法计算。",
                "path": {"narrative_cn": "资产 -> 折旧码 -> 政策", "path_nodes": [{"properties": {"large": "payload"}}]},
                "policy_narrative": {"narrative_cn": "资产适用产量法政策。", "technical_details": {"raw": "payload"}},
            }]
        }]
        result = WideTableQASkill._model_ontology_paths(paths, [{"asset_ref": "ASSET-TEST-001"}])

        self.assertEqual(result[0]["asset_ref"], "ASSET-TEST-001")
        self.assertNotIn("path_nodes", result[0])
        self.assertNotIn("technical_details", result[0])

    def test_reverse_planning_treats_reduce_amount_as_relative_change(self):
        direction, amount = ReversePlanningSkill._relative_change_from_question(
            "7月公司整体折旧减少6万元"
        )
        self.assertEqual(direction, "decrease")
        self.assertEqual(amount, Decimal("60000"))
        self.assertIsNone(
            ReversePlanningSkill._relative_change_from_question("7月公司整体折旧降至6万元")
        )

    def test_reverse_planning_keeps_distinct_same_strategy_alternatives(self):
        skill = object.__new__(ReversePlanningSkill)
        simulations = [
            {
                "target_amount": "940.00", "gap": "0.00", "affected_object_count": 1,
                "actions": [{"template_id": "straight_impairment", "target_object": "A-001"}],
            },
            {
                "target_amount": "941.00", "gap": "1.00", "affected_object_count": 1,
                "actions": [{"template_id": "straight_impairment", "target_object": "A-002"}],
            },
            {
                "target_amount": "942.00", "gap": "2.00", "affected_object_count": 1,
                "actions": [{"template_id": "straight_impairment", "target_object": "A-003"}],
            },
        ]
        selected = skill._select_distinct_recommendations(simulations)
        self.assertEqual(len(selected), 3)
        self.assertEqual(selected[1]["selection_label_cn"], "同策略资产组合备选")


if __name__ == "__main__":
    unittest.main()
