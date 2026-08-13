from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import combinations
from typing import Any, Callable

from depreciation_poc.qa.skill import _as_text_list, _optional_text


LOGGER = logging.getLogger("depreciation_poc.reverse_planning")


@dataclass(frozen=True)
class ReversePlanningTools:
    forecast_lines: Callable[..., list[dict[str, Any]]]
    candidate_actions: Callable[[dict[str, Any]], list[dict[str, Any]]]
    simulate: Callable[[list[dict[str, Any]], dict[str, Any]], dict[str, Any]]
    ontology_path: Callable[[dict[str, Any]], list[dict[str, Any]]]
    catalog: Callable[..., dict[str, Any]]


@dataclass
class ReverseConversationState:
    conversation_id: str
    created_at: datetime
    updated_at: datetime
    active_plan: dict[str, Any]
    recommendations: list[dict[str, Any]]
    last_audit_id: str | None = None
    turns: list[dict[str, Any]] | None = None


class ReverseConversationStore:
    ttl = timedelta(minutes=30)
    max_turns = 10

    def __init__(self) -> None:
        self._items: dict[str, ReverseConversationState] = {}
        self._lock = threading.RLock()

    def open(self, conversation_id: str | None, scenario_id: str) -> tuple[ReverseConversationState, bool]:
        now = datetime.now()
        with self._lock:
            if conversation_id:
                item = self._items.get(conversation_id)
                if item and now - item.updated_at <= self.ttl and item.active_plan.get("scenario_id") == scenario_id:
                    return item, False
            item = ReverseConversationState(str(uuid.uuid4()), now, now, {"scenario_id": scenario_id}, [], turns=[])
            self._items[item.conversation_id] = item
            self._prune(now)
            return item, True

    def record(self, state: ReverseConversationState, *, plan: dict[str, Any], recommendations: list[dict[str, Any]], audit_id: str | None, conclusion: str) -> None:
        with self._lock:
            state.updated_at = datetime.now()
            state.active_plan = plan
            state.recommendations = recommendations
            state.last_audit_id = audit_id
            turns = state.turns or []
            turns.append({
                "at": state.updated_at.isoformat(timespec="seconds"),
                "intent": plan.get("intent"),
                "target_period": plan.get("target_period"),
                "scope_type": plan.get("scope_type"),
                "scope_value": plan.get("scope_value"),
                "target_amount": str(plan.get("target_amount") or ""),
                "recommendation_count": len(recommendations),
                "audit_id": audit_id,
                "conclusion_summary": conclusion[:500],
            })
            state.turns = turns[-self.max_turns:]

    def view(self, state: ReverseConversationState, *, is_new: bool) -> dict[str, Any]:
        return {
            "conversation_id": state.conversation_id,
            "is_new": is_new,
            "expires_in_seconds": int(self.ttl.total_seconds()),
            "turn_count": len(state.turns or []),
            "active_plan": {key: value for key, value in state.active_plan.items() if key != "recommendations"},
            "last_audit_id": state.last_audit_id,
        }

    def _prune(self, now: datetime) -> None:
        for key in [key for key, value in self._items.items() if now - value.updated_at > self.ttl]:
            self._items.pop(key, None)


class ReversePlanningHarness:
    function_catalog = {
        "list_available_periods": "读取当前场景可推演月份",
        "resolve_target_scope": "解析目标范围和业务对象",
        "read_scope_baseline": "读取目标期基准折旧",
        "resolve_eligible_objects": "定位可作用资产和区块",
        "load_action_templates": "读取可用规则场景模板",
        "generate_candidate_actions": "按已注册规则生成候选动作",
        "simulate_rule_actions": "调用折旧规则引擎进行临时试算",
        "rank_distinct_recommendations": "按目标偏差和策略差异排序方案",
        "trace_reverse_ontology_path": "追溯目标到规则和结果的 Ontology 路径",
        "get_rule_execution_evidence": "读取方案实际命中的规则与输入",
    }

    intent_evidence = {
        "reverse_target": ["baseline", "eligible_objects", "action_templates", "simulations", "rules", "ontology_paths"],
        "explain_recommendation": ["recommendation", "rules", "ontology_paths"],
        "compare_recommendations": ["recommendations", "rules", "ontology_paths"],
    }


class ReversePlanningSkill:
    """Two-stage controlled Agent: DeepSeek understands and expresses; Harness calculates and recommends."""

    skill_name = "reverse_depreciation_planning"

    def __init__(self, *, tools: ReversePlanningTools, provider: Any) -> None:
        self.tools = tools
        self.provider = provider
        self.conversations = ReverseConversationStore()

    def catalog(self, scenario_id: str = "BASELINE") -> dict[str, Any]:
        catalog = self.tools.catalog(scenario_id)
        return {
            **catalog,
            "scenario_id": scenario_id,
            "intents": [
                {"id": "reverse_target", "label_cn": "反向推演目标"},
                {"id": "explain_recommendation", "label_cn": "解释推荐方案"},
                {"id": "compare_recommendations", "label_cn": "比较推荐方案"},
                {"id": "clarification", "label_cn": "需要补充信息"},
            ],
            "harness_functions": [
                {"id": key, "label_cn": value, "read_only": True}
                for key, value in ReversePlanningHarness.function_catalog.items()
            ],
            "evidence_types": ReversePlanningHarness.intent_evidence,
            "constraints": ["推荐只做临时试算，不创建 What-if 场景，不写入数据库。", "金额、动作、资产和排序由本地 Harness 决定。"],
        }

    def answer(self, payload: dict[str, Any]) -> dict[str, Any]:
        scenario_id = str(payload.get("scenario_id") or "BASELINE")
        question = str(payload.get("question") or "").strip()
        conversation, is_new = self.conversations.open(_optional_text(payload.get("conversation_id")), scenario_id)
        catalog = self.catalog(scenario_id)
        understanding_context = self._understanding_context(question, scenario_id, catalog, conversation)
        understanding = self.provider.plan_reverse(understanding_context)
        validation = self._validate_plan(understanding, scenario_id, catalog, conversation)
        if not validation["valid"]:
            clarification = validation["clarification"]
            self.conversations.record(conversation, plan=conversation.active_plan, recommendations=conversation.recommendations, audit_id=conversation.last_audit_id, conclusion=clarification["question_cn"])
            return self._clarification_response(question, conversation, is_new, understanding, validation)

        plan = validation["plan"]
        audit_id = f"RP-{uuid.uuid4().hex[:12].upper()}"
        started_at = time.perf_counter()
        execution = self._execute_plan(plan, conversation)
        composition_context = {
            "task": "reverse_planning_answer_composition",
            "question": question,
            "validated_question_plan": plan,
            "evidence_package": execution["evidence"],
            "template_answer_cn": execution["template_answer_cn"],
            "guardrails": [
                "所有金额、动作、规则和排序均由 Harness 产生。",
                "不得改写方案、计算金额或把业务假设描述为已发生事实。",
                "必须覆盖每一套推荐方案的编号、试算金额和目标偏差。",
            ],
        }
        generation = self.provider.compose_reverse(composition_context)
        generation = self._validate_generation(generation, execution)
        self.conversations.record(conversation, plan=plan, recommendations=execution["recommendations"], audit_id=audit_id, conclusion=str(generation["answer_cn"]))
        result = {
            "audit_id": audit_id,
            "question": question,
            "question_analysis": self._question_analysis(plan),
            "conversation": self.conversations.view(conversation, is_new=is_new),
            "question_plan": plan,
            "plan_validation": validation,
            "clarification": None,
            "baseline_amount": execution.get("baseline_amount"),
            "target_amount": execution.get("target_amount"),
            "required_delta": execution.get("required_delta"),
            "feasible": bool(execution["recommendations"]),
            "recommendations": execution["recommendations"],
            "ontology_paths": execution["ontology_paths"],
            "rule_execution_evidence": execution["rule_execution_evidence"],
            "harness": execution["harness"],
            "tool_trace": execution["harness"]["tool_trace"],
            "answer_cn": generation["answer_cn"],
            "key_findings": generation.get("key_findings", []),
            "next_steps": generation.get("next_steps", []),
            "model_calls": {
                "question_understanding": self._model_call_metadata(understanding),
                "answer_composition": self._model_call_metadata(generation),
            },
            "answer_validation": self._answer_validation(generation, execution),
            "qa_skill": self._metadata(generation, execution["harness"]["tool_trace"]),
        }
        self._write_audit_log(audit_id, plan, result, started_at, execution)
        return result

    def _understanding_context(self, question: str, scenario_id: str, catalog: dict[str, Any], conversation: ReverseConversationState) -> dict[str, Any]:
        return {
            "task": "reverse_planning_understanding",
            "question": question,
            "scenario_id": scenario_id,
            "available_periods": catalog.get("periods", []),
            "available_scopes": catalog.get("scopes", []),
            "supported_action_templates": catalog.get("actions", []),
            "allowed_intents": list(ReversePlanningHarness.intent_evidence),
            "conversation_context": {
                "active_plan": conversation.active_plan,
                "recommendations": [{"number": index + 1, "strategy": item.get("strategy_label_cn"), "action_key": item.get("action_key")} for index, item in enumerate(conversation.recommendations)],
                "recent_turns": list(conversation.turns or [])[-3:],
            },
        }

    def _validate_plan(self, raw: dict[str, Any], scenario_id: str, catalog: dict[str, Any], conversation: ReverseConversationState) -> dict[str, Any]:
        if raw.get("intent") == "clarification" or raw.get("clarification"):
            inferred = self._infer_recommendation_reference(str(raw.get("_question") or ""), conversation)
            if raw.get("used_llm") and inferred:
                return {"valid": True, "reason_cn": "Harness 从明确的方案序号补全了会话引用。", "plan": inferred}
            return self._invalid_plan(str(raw.get("clarification_question") or "请补充目标范围、目标月份和目标金额。"), catalog)
        intent = str(raw.get("intent") or "")
        if intent not in ReversePlanningHarness.intent_evidence:
            return self._invalid_plan("模型返回了未注册的反向推演意图。", catalog)
        if str(raw.get("scenario_id") or scenario_id) != scenario_id:
            return self._invalid_plan("当前会话不能切换到未确认的场景。", catalog)
        if intent in ("explain_recommendation", "compare_recommendations"):
            if not conversation.recommendations:
                return self._invalid_plan("当前会话没有可解释的推荐方案，请先提出一个完整反向推演目标。", catalog)
            selected = self._recommendation_numbers(raw, conversation.recommendations)
            if not selected:
                return self._invalid_plan("请说明要查看第几个方案，或明确要比较哪些方案。", catalog)
            plan = {**conversation.active_plan, "intent": intent, "recommendation_numbers": selected, "requested_evidence": ReversePlanningHarness.intent_evidence[intent], "confidence": str(raw.get("confidence") or "high")}
            return {"valid": True, "reason_cn": "会话上下文中的推荐方案已确认。", "plan": plan}

        periods = {str(item) for item in catalog.get("periods", [])}
        scopes = {(str(item["type"]), str(item["value"])) for item in catalog.get("scopes", [])}
        target_period = _optional_text(raw.get("target_period"))
        scope_type = _optional_text(raw.get("scope_type"))
        scope_value = _optional_text(raw.get("scope_value"))
        target_amount = self._decimal(raw.get("target_amount"), raw.get("target_amount_unit"))
        change_amount = self._decimal(raw.get("target_change_amount"), raw.get("target_change_unit"))
        direction = str(raw.get("direction") or "target")
        if not target_period or target_period not in periods:
            return self._invalid_plan("请提供当前预测期内的目标月份。", catalog)
        if not scope_type or not scope_value or (scope_type, scope_value) not in scopes:
            return self._invalid_plan("请从当前业务范围中明确全公司、所属单位或资产类别。", catalog)
        if target_amount is None and change_amount is None:
            return self._invalid_plan("请提供目标金额，或说明相对基准要增加/减少多少金额。", catalog)
        if direction not in ("increase", "decrease", "target"):
            direction = "target"
        return {
            "valid": True,
            "reason_cn": "推演计划已通过场景、目标范围、目标月份、金额和只读权限校验。",
            "plan": {
                "intent": "reverse_target",
                "intent_label_cn": "反向推演目标",
                "scenario_id": scenario_id,
                "target_period": target_period,
                "scope_type": scope_type,
                "scope_value": scope_value,
                "target_amount": target_amount,
                "target_change_amount": change_amount,
                "direction": direction,
                "requested_evidence": [item for item in _as_text_list(raw.get("requested_evidence")) if item in ReversePlanningHarness.intent_evidence["reverse_target"]] or ReversePlanningHarness.intent_evidence["reverse_target"],
                "resolved_entities": raw.get("resolved_entities") if isinstance(raw.get("resolved_entities"), dict) else {},
                "confidence": str(raw.get("confidence") or "medium"),
            },
        }

    @staticmethod
    def _infer_recommendation_reference(question: str, conversation: ReverseConversationState) -> dict[str, Any] | None:
        if not conversation.recommendations:
            return None
        numbers = [int(value) for value in re.findall(r"第\s*([1-3])\s*(?:个)?方案", question)]
        if not numbers or any(number > len(conversation.recommendations) for number in numbers):
            return None
        intent = "compare_recommendations" if any(word in question for word in ("比较", "对比", "区别", "差异")) or len(numbers) > 1 else "explain_recommendation"
        return {**conversation.active_plan, "intent": intent, "recommendation_numbers": list(dict.fromkeys(numbers)), "requested_evidence": ReversePlanningHarness.intent_evidence[intent], "confidence": "high"}

    @staticmethod
    def _recommendation_numbers(raw: dict[str, Any], recommendations: list[dict[str, Any]]) -> list[int]:
        values = _as_text_list(raw.get("recommendation_numbers") or raw.get("recommendation_number"))
        selected: list[int] = []
        for value in values:
            try:
                number = int(value)
            except ValueError:
                continue
            if 1 <= number <= len(recommendations) and number not in selected:
                selected.append(number)
        return selected

    def _invalid_plan(self, message: str, catalog: dict[str, Any]) -> dict[str, Any]:
        return {"valid": False, "reason_cn": message, "clarification": {"question_cn": message, "candidates": {"available_periods": catalog.get("periods", []), "scopes": catalog.get("scopes", []), "examples": ["2026年12月全公司折旧降低10000", "解释第 2 个推荐方案", "比较方案 1 和方案 2"]}}}

    def _clarification_response(self, question: str, conversation: ReverseConversationState, is_new: bool, raw: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
        clarification = validation["clarification"]
        return {
            "question": question,
            "conversation": self.conversations.view(conversation, is_new=is_new),
            "question_plan": {"intent": "clarification", "confidence": raw.get("confidence", "low")},
            "plan_validation": {"valid": False, "reason_cn": validation["reason_cn"]},
            "clarification": clarification,
            "clarification_cn": clarification["question_cn"],
            "answer_cn": clarification["question_cn"],
            "feasible": False,
            "recommendations": [],
            "harness": {"tool_trace": [], "evidence_summary": {"status": "not_executed", "reason_cn": "目标尚未确认，未生成候选动作或执行规则试算。"}},
            "tool_trace": [],
            "model_calls": {"question_understanding": self._model_call_metadata(raw), "answer_composition": None},
            "answer_validation": {"valid": True, "status": "clarification"},
            "qa_skill": self._metadata(raw, []),
        }

    def _execute_plan(self, plan: dict[str, Any], conversation: ReverseConversationState) -> dict[str, Any]:
        if plan["intent"] in ("explain_recommendation", "compare_recommendations"):
            recommendations = [conversation.recommendations[number - 1] for number in plan["recommendation_numbers"]]
            paths = self.tools.ontology_path({**plan, "recommendations": recommendations})
            rules = [rule for item in recommendations for rule in item.get("rule_execution_trace", [])]
            template = self._explain_template(plan, recommendations)
            trace = [
                self._trace("get_rule_execution_evidence", {"recommendation_count": len(recommendations), "execution_count": len(rules)}),
                self._trace("trace_reverse_ontology_path", {"path_count": len(paths)}),
            ]
            return {"recommendations": recommendations, "ontology_paths": paths, "rule_execution_evidence": rules, "evidence": {"recommendations": recommendations, "ontology_paths": paths, "rule_execution_evidence": rules}, "harness": {"tool_trace": trace, "evidence_summary": {"recommendation_count": len(recommendations), "mode": plan["intent"]}}, "template_answer_cn": template}

        trace = [self._trace("list_available_periods", {"target_period": plan["target_period"]}), self._trace("resolve_target_scope", {"scope_type": plan["scope_type"], "scope_value": plan["scope_value"]})]
        baseline = self._scope_total(plan)
        target = plan["target_amount"]
        if target is None:
            delta = plan["target_change_amount"] or Decimal("0")
            target = baseline + delta if plan["direction"] == "increase" else baseline - delta
        plan["target_amount"] = target
        required_delta = target - baseline
        plan["required_delta"] = required_delta
        trace.append(self._trace("read_scope_baseline", {"baseline_amount": f"{baseline:.2f}", "target_amount": f"{target:.2f}", "required_delta": f"{required_delta:.2f}"}))
        candidates = self.tools.candidate_actions(plan)
        trace.extend([
            self._trace("resolve_eligible_objects", {"candidate_count": len(candidates)}),
            self._trace("load_action_templates", {"template_count": len({action.get('template_id') for item in candidates for action in item.get('actions', [])})}),
            self._trace("generate_candidate_actions", {"candidate_count": len(candidates)}),
        ])
        simulations = self._simulate_candidates(candidates, plan, target)
        trace.append(self._trace("simulate_rule_actions", {"simulation_count": len(simulations), "scenario_written": False}))
        recommendations = self._select_distinct_recommendations(simulations)
        self._decorate_recommendations(recommendations)
        trace.append(self._trace("rank_distinct_recommendations", {"recommendation_count": len(recommendations)}))
        paths = self.tools.ontology_path({**plan, "recommendations": recommendations})
        rules = [rule for item in recommendations for rule in item.get("rule_execution_trace", [])]
        trace.extend([
            self._trace("get_rule_execution_evidence", {"execution_count": len(rules), "recommendation_count": len(recommendations)}),
            self._trace("trace_reverse_ontology_path", {"path_count": len(paths)}),
        ])
        return {
            "baseline_amount": f"{baseline:.2f}", "target_amount": f"{target:.2f}", "required_delta": f"{required_delta:.2f}",
            "recommendations": recommendations, "ontology_paths": paths, "rule_execution_evidence": rules,
            "evidence": {"baseline_amount": f"{baseline:.2f}", "target_amount": f"{target:.2f}", "required_delta": f"{required_delta:.2f}", "recommendations": recommendations, "ontology_paths": paths, "rule_execution_evidence": rules, "scenario_written": False},
            "harness": {"tool_trace": trace, "evidence_summary": {"candidate_count": len(candidates), "simulation_count": len(simulations), "recommendation_count": len(recommendations), "scenario_written": False}},
            "template_answer_cn": self._template_answer(plan, baseline, target, recommendations),
        }

    @staticmethod
    def _trace(tool_name: str, result_shape: dict[str, Any]) -> dict[str, Any]:
        return {"tool_name": tool_name, "label_cn": ReversePlanningHarness.function_catalog[tool_name], "read_only": True, "result_shape": result_shape}

    def _scope_total(self, plan: dict[str, Any]) -> Decimal:
        filters = {"scenario_id": plan["scenario_id"], "period_from": plan["target_period"], "period_to": plan["target_period"], "limit": 10000}
        if plan["scope_type"] == "department": filters["department"] = plan["scope_value"]
        if plan["scope_type"] == "asset_category": filters["asset_category"] = plan["scope_value"]
        lines = self.tools.forecast_lines(**filters)
        if plan["scope_type"] == "company":
            lines = [row for row in lines if str(row.get("company")) == plan["scope_value"]]
        return sum((Decimal(str(row.get("monthly_depreciation") or "0")) for row in lines), Decimal("0"))

    def _simulate_candidates(self, candidates: list[dict[str, Any]], plan: dict[str, Any], target: Decimal) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for candidate in candidates:
            simulated = self.tools.simulate(candidate["assumptions"], plan)
            if Decimal(str(simulated["target_amount"])) == Decimal(str(plan["baseline_amount"] if "baseline_amount" in plan else self._scope_total(plan))):
                continue
            results.append({**candidate, **simulated, "gap": Decimal(str(simulated["target_amount"])) - target})
        baseline = Decimal(str(plan["baseline_amount"] if "baseline_amount" in plan else self._scope_total(plan)))
        influential = sorted(
            results,
            key=lambda row: abs(Decimal(str(row["target_amount"])) - baseline),
            reverse=True,
        )[:6]
        for left, right in combinations(influential, 2):
            if left["action_key"] == right["action_key"]:
                continue
            left_targets = {(str(action.get("template_id") or ""), str(action.get("target_object") or "")) for action in left["actions"]}
            right_targets = {(str(action.get("template_id") or ""), str(action.get("target_object") or "")) for action in right["actions"]}
            if left_targets & right_targets:
                continue
            simulated = self.tools.simulate([*left["assumptions"], *right["assumptions"]], plan)
            gap = Decimal(str(simulated["target_amount"])) - target
            if abs(gap) >= min(abs(Decimal(str(left["gap"]))), abs(Decimal(str(right["gap"])) )):
                continue
            results.append({"action_key": f"{left['action_key']}+{right['action_key']}", "actions": [*left["actions"], *right["actions"]], "assumptions": [*left["assumptions"], *right["assumptions"]], **simulated, "gap": gap, "affected_object_count": left["affected_object_count"] + right["affected_object_count"]})
        return self._deduplicate_simulations(results)

    @staticmethod
    def _deduplicate_simulations(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique: dict[str, dict[str, Any]] = {}
        for row in results:
            signature = json.dumps(row.get("assumptions", []), ensure_ascii=False, sort_keys=True, default=str)
            rank = (abs(Decimal(str(row["gap"]))), len(row["actions"]), row["affected_object_count"])
            if signature not in unique or rank < (abs(Decimal(str(unique[signature]["gap"]))), len(unique[signature]["actions"]), unique[signature]["affected_object_count"]):
                unique[signature] = row
        return list(unique.values())

    @staticmethod
    def _strategy_signature(row: dict[str, Any]) -> tuple[str, ...]:
        return tuple(sorted({str(action.get("template_id") or "") for action in row.get("actions", [])}))

    @staticmethod
    def _strategy_label(strategy: tuple[str, ...]) -> str:
        labels = {"straight_new_asset": "新增资产", "straight_impairment": "减值后重算", "straight_accelerated": "加速折旧", "straight_start_rule": "开始计提规则调整", "production_driver": "产量/储量调整", "workload_driver": "工作量/单位费用调整"}
        return " + ".join(labels.get(item, item) for item in strategy)

    def _select_distinct_recommendations(self, simulations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        selected_strategies: set[tuple[str, ...]] = set()
        for row in sorted(simulations, key=lambda row: (abs(Decimal(str(row["gap"]))), len(row["actions"]), row["affected_object_count"])):
            strategy = self._strategy_signature(row)
            if strategy in selected_strategies:
                continue
            selected_strategies.add(strategy)
            selected.append({**row, "strategy_key": "+".join(strategy), "strategy_label_cn": self._strategy_label(strategy), "selection_label_cn": "最优方案" if not selected else "差异化备选", "selection_reason_cn": "该方案与目标金额的偏差最小。" if not selected else f"保留不同的“{self._strategy_label(strategy)}”规则策略供比较。"})
            if len(selected) == 3:
                break
        return selected

    @staticmethod
    def _decorate_recommendations(recommendations: list[dict[str, Any]]) -> None:
        for index, recommendation in enumerate(recommendations, start=1):
            recommendation["recommendation_number"] = index
            recommendation["scenario_written"] = False
            recommendation["assumption_notice_cn"] = "本方案仅为临时业务假设，尚未创建或保存 What-if 场景。"

    @staticmethod
    def _template_answer(plan: dict[str, Any], baseline: Decimal, target: Decimal, recommendations: list[dict[str, Any]]) -> str:
        if not recommendations:
            return f"{plan['scope_value']} 在 {plan['target_period']} 的基准折旧为 {baseline:.2f}，目标为 {target:.2f}。当前已注册规则动作未找到可有效改变该目标期结果的方案。"
        details = "；".join(f"方案 {item['recommendation_number']}（{item['strategy_label_cn']}）：试算 {Decimal(str(item['target_amount'])):.2f}，目标偏差 {Decimal(str(item['gap'])):.2f}" for item in recommendations)
        return f"{plan['scope_value']} 在 {plan['target_period']} 的基准折旧为 {baseline:.2f}，目标为 {target:.2f}。{details}。所有推荐均为临时业务假设，未创建或保存 What-if 场景；减值等假设需按财务制度确认。"

    @staticmethod
    def _explain_template(plan: dict[str, Any], recommendations: list[dict[str, Any]]) -> str:
        if plan["intent"] == "compare_recommendations":
            return "；".join(f"方案 {item.get('recommendation_number')} 采用{item.get('strategy_label_cn')}，试算金额为 {item.get('target_amount')}，目标偏差为 {item.get('gap')}。" for item in recommendations)
        item = recommendations[0]
        action_text = "；".join(str(action.get("label_cn") or "") for action in item.get("actions", []))
        return f"方案 {item.get('recommendation_number')} 的策略为{item.get('strategy_label_cn')}，包含动作：{action_text}。试算金额为 {item.get('target_amount')}，目标偏差为 {item.get('gap')}。该方案仅为临时业务假设。"

    @staticmethod
    def _question_analysis(plan: dict[str, Any]) -> dict[str, Any]:
        return {"intent": plan["intent"], "intent_label_cn": {"reverse_target": "反向推演目标", "explain_recommendation": "解释推荐方案", "compare_recommendations": "比较推荐方案"}[plan["intent"]], "target_period": plan.get("target_period"), "scope_type": plan.get("scope_type"), "scope_value": plan.get("scope_value"), "direction": plan.get("direction"), "target_amount": str(plan.get("target_amount") or ""), "target_change_amount": str(plan.get("target_change_amount") or ""), "confidence": plan.get("confidence")}

    @staticmethod
    def _decimal(value: object, unit: object = "") -> Decimal | None:
        if value in (None, ""):
            return None
        try:
            amount = Decimal(str(value).replace(",", ""))
            return amount * Decimal("10000") if str(unit) in ("万", "万元") else amount
        except Exception:
            return None

    @staticmethod
    def _validate_generation(generation: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
        answer = str(generation.get("answer_cn") or "")
        missing = [str(item.get("recommendation_number")) for item in execution.get("recommendations", []) if f"方案 {item.get('recommendation_number')}" not in answer]
        if missing:
            return {**generation, "answer_cn": execution["template_answer_cn"], "evidence_complete_template_used": True}
        return generation

    @staticmethod
    def _answer_validation(generation: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
        return {"valid": True, "expression_layer": "template_fallback" if generation.get("evidence_complete_template_used") or not generation.get("used_llm") else "deepseek", "reason_cn": "业务表述已覆盖全部可用推荐方案。"}

    @staticmethod
    def _model_call_metadata(result: dict[str, Any] | None) -> dict[str, Any] | None:
        if result is None:
            return None
        return {"provider": result.get("provider"), "model": result.get("model"), "used_llm": bool(result.get("used_llm")), "latency_ms": result.get("latency_ms"), "fallback_reason": result.get("fallback_reason")}

    @staticmethod
    def _write_audit_log(audit_id: str, plan: dict[str, Any], result: dict[str, Any], started_at: float, execution: dict[str, Any]) -> None:
        skill = result.get("qa_skill") or {}
        LOGGER.info("reverse_planning=%s", json.dumps({"audit_id": audit_id, "intent": plan.get("intent"), "scenario_id": plan.get("scenario_id"), "target_period": plan.get("target_period"), "scope_type": plan.get("scope_type"), "scope_value": plan.get("scope_value"), "candidate_count": execution.get("harness", {}).get("evidence_summary", {}).get("candidate_count"), "recommendations": [{"number": item.get("recommendation_number"), "strategy": item.get("strategy_key"), "target_amount": item.get("target_amount"), "gap": str(item.get("gap"))} for item in result.get("recommendations", [])], "used_llm": bool(skill.get("used_llm")), "duration_ms": round((time.perf_counter() - started_at) * 1000)}, ensure_ascii=False, default=str))

    def _metadata(self, generation: dict[str, Any] | None, trace: list[dict[str, Any]]) -> dict[str, Any]:
        return {"skill_name": self.skill_name, "provider": generation.get("provider") if generation else None, "used_llm": bool(generation and generation.get("used_llm")), "model": generation.get("model") if generation else None, "fallback_reason": generation.get("fallback_reason") if generation else None, "tool_trace": trace}
