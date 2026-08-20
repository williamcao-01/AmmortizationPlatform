import sys
import time
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from depreciation_poc.qa.reverse_optimization import ReverseActionOption, ReverseOptimizationEngine
from depreciation_poc.qa.reverse_planning import ReversePlanningSkill
from depreciation_poc.ontology_model import REVERSE_ACTION_CAPABILITIES


class ReverseOptimizationTests(unittest.TestCase):
    def test_reverse_capability_catalog_excludes_new_assets(self):
        templates = {item["template_id"] for item in REVERSE_ACTION_CAPABILITIES}
        self.assertNotIn("straight_new_asset", templates)
        self.assertNotIn("CAP-STRAIGHT-NEW", {item["id"] for item in REVERSE_ACTION_CAPABILITIES})

    def test_candidate_validation_does_not_exhaust_solver_budget(self):
        """Slow rule-engine validation must not prevent the solver from running."""
        def simulate(assumptions, _plan):
            time.sleep(0.03)
            amount = sum((Decimal(str(item["amount"])) for item in assumptions), Decimal("0"))
            return {"target_amount": f"{Decimal('100') - amount:.2f}", "rule_execution_trace": []}

        options = [
            ReverseActionOption(
                option_id=f"impairment:{index}", capability_id="CAP-STRAIGHT-IMPAIRMENT",
                template_id="straight_impairment", target_object=str(index), label_cn=str(index),
                solution_kind="accounting", risk_weight=1, affected_object_ids=(str(index),),
                assumptions=({"template_id": "straight_impairment", "asset_id": str(index), "amount": "10"},),
                adjustable_field="amount", minimum_value=Decimal("0"), maximum_value=Decimal("10"), full_value=Decimal("10"),
            )
            for index in range(8)
        ]
        result = ReverseOptimizationEngine(simulate=simulate, timeout_seconds=0.2).optimize(
            plan={"target_amount": Decimal("90"), "required_delta": Decimal("-10")},
            baseline_amount=Decimal("100"), options=options,
        )

        self.assertTrue(result["optimization"]["is_exact"])
        self.assertEqual(result["recommendations"][0]["target_amount"], "90.00")

    def test_exact_accounting_plan_avoids_operational_fallback(self):
        calls: list[str] = []

        def simulate(assumptions, _plan):
            template = assumptions[0]["template_id"]
            calls.append(template)
            amount = Decimal(str(assumptions[0]["amount"]))
            return {"target_amount": f"{Decimal('100') - amount:.2f}", "rule_execution_trace": []}

        accounting = ReverseActionOption(
            option_id="impairment:A", capability_id="CAP-STRAIGHT-IMPAIRMENT",
            template_id="straight_impairment", target_object="A", label_cn="A",
            solution_kind="accounting", risk_weight=1, affected_object_ids=("A",),
            assumptions=({"template_id": "straight_impairment", "asset_id": "A", "amount": "10"},),
            adjustable_field="amount", minimum_value=Decimal("0"), maximum_value=Decimal("10"), full_value=Decimal("10"),
        )
        operational = ReverseActionOption(
            option_id="production:B", capability_id="CAP-PRODUCTION-DRIVER",
            template_id="production_driver", target_object="B", label_cn="B",
            solution_kind="operational_fallback", risk_weight=1, affected_object_ids=("B",),
            assumptions=({"template_id": "production_driver", "block_id": "B", "amount": "10"},),
            adjustable_field="amount", minimum_value=Decimal("0"), maximum_value=Decimal("10"), full_value=Decimal("10"),
        )
        result = ReverseOptimizationEngine(simulate=simulate, timeout_seconds=1).optimize(
            plan={"target_amount": Decimal("90"), "required_delta": Decimal("-10")},
            baseline_amount=Decimal("100"), options=[accounting, operational],
        )

        self.assertTrue(result["optimization"]["is_exact"])
        self.assertFalse(result["optimization"]["accounting_search_skipped"])
        self.assertIn("straight_impairment", calls)
        self.assertNotIn("production_driver", calls)
        self.assertEqual(result["recommendations"][0]["target_amount"], "90.00")

    def test_finds_three_distinct_verified_accounting_solutions(self):
        effects = {"A": Decimal("30"), "B": Decimal("20"), "C": Decimal("10"), "D": Decimal("25")}

        def simulate(assumptions, _plan):
            total = Decimal("100")
            for item in assumptions:
                total -= Decimal(str(item["amount"]))
            return {"target_amount": f"{total:.2f}", "rule_execution_trace": []}

        options = [
            ReverseActionOption(
                option_id=f"impairment:{key}", capability_id="CAP-STRAIGHT-IMPAIRMENT",
                template_id="straight_impairment", target_object=key, label_cn=key,
                solution_kind="accounting", risk_weight=40, affected_object_ids=(key,),
                assumptions=({"template_id": "straight_impairment", "asset_id": key, "amount": str(value)},),
                adjustable_field="amount", minimum_value=Decimal("0"), maximum_value=value, full_value=value,
            )
            for key, value in effects.items()
        ]
        plan = {"target_amount": Decimal("60"), "required_delta": Decimal("-40")}
        result = ReverseOptimizationEngine(simulate=simulate, timeout_seconds=4).optimize(
            plan=plan, baseline_amount=Decimal("100"), options=options,
        )

        self.assertTrue(result["optimization"]["is_exact"])
        self.assertGreaterEqual(len(result["recommendations"]), 1)
        self.assertEqual(result["recommendations"][0]["target_amount"], "60.00")
        signatures = [tuple(sorted(action["target_object"] for action in item["actions"])) for item in result["recommendations"]]
        self.assertEqual(len(signatures), len(set(signatures)))
        self.assertEqual(result["operational_fallback_recommendations"], [])

    def test_large_gap_explanation_is_mandatory_and_business_actionable(self):
        plan = {"required_delta": Decimal("-250000"), "direction": "decrease"}
        explanation = ReversePlanningSkill._large_gap_explanation(
            plan, [{"gap": "207058.24"}],
        )
        self.assertIsNotNone(explanation)
        self.assertIn("为什么当前不能达成", explanation)
        self.assertIn("怎样才可能达成", explanation)
        output = ReversePlanningSkill._append_large_gap_explanation(
            {"answer_cn": "模型业务表述"}, {"large_gap_explanation_cn": explanation},
        )
        self.assertIn("\n\n", output["answer_cn"])
        self.assertTrue(output["answer_cn"].endswith(explanation))


if __name__ == "__main__":
    unittest.main()
