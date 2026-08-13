from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import threading
import time
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Protocol

from depreciation_poc.domain.models import Month
from depreciation_poc.ontology_model import object_id
from depreciation_poc.semantic_labels import (
    ASSET_SOURCE_TYPE_LABEL_CN,
    category_label,
    depreciation_code_label,
    policy_label,
)


SIGNIFICANT_DRIVER_MIN_AMOUNT = Decimal("1000")
SIGNIFICANT_DRIVER_SHARE = Decimal("0.001")


class WideTableQAProvider(Protocol):
    provider_name: str

    def plan_question(self, context: dict[str, Any]) -> dict[str, Any]:
        ...

    def compose_answer(self, context: dict[str, Any]) -> dict[str, Any]:
        ...

    def plan_reverse(self, context: dict[str, Any]) -> dict[str, Any]:
        ...

    def compose_reverse(self, context: dict[str, Any]) -> dict[str, Any]:
        ...

    def answer(self, context: dict[str, Any]) -> dict[str, Any]:
        ...


class TemplateWideTableQAProvider:
    provider_name = "template_fallback"

    def __init__(self, reason: str = "DEEPSEEK_API_KEY 未配置，使用确定性业务结论。") -> None:
        self.reason = reason

    def answer(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "used_llm": False,
            "fallback_reason": self.reason,
            "answer_cn": context.get("template_answer_cn") or "当前没有足够证据生成回答。",
        }

    def plan_question(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "used_llm": False,
            "fallback_reason": self.reason,
            "clarification": True,
            "clarification_question": "问答理解模型当前不可用。请稍后重试，或明确说明要查询的所属单位、资产类别和月份。",
        }

    def compose_answer(self, context: dict[str, Any]) -> dict[str, Any]:
        return self.answer(context)

    def plan_reverse(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "used_llm": False,
            "fallback_reason": self.reason,
            "intent": "clarification",
            "clarification_question": "反向推演理解模型当前不可用。请稍后重试。",
        }

    def compose_reverse(self, context: dict[str, Any]) -> dict[str, Any]:
        return self.answer(context)


class DeepSeekWideTableQAProvider:
    provider_name = "deepseek"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: int = 30,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _call_json(
        self,
        *,
        system: str,
        context: dict[str, Any],
        required_field: str,
        max_tokens: int = 800,
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": system,
                },
                {
                    "role": "user",
                    "content": json.dumps(_jsonable(context), ensure_ascii=False),
                },
            ],
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            # Amounts, rule selection, and graph paths are already determined by
            # the Harness. Disable model thinking so it produces the required
            # structured response instead of exhausting the response budget in
            # reasoning_content.
            "thinking": {"type": "disabled"},
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = data["choices"][0]["message"].get("content") or ""
        parsed = _extract_json(content)
        # Some OpenAI-compatible gateways return a valid outer JSON object whose
        # answer field is itself a serialized JSON object. Normalize that shape so
        # the UI always receives plain business text rather than JSON source.
        if isinstance(parsed, dict) and isinstance(parsed.get(required_field), str):
            nested = _extract_json(parsed[required_field])
            if isinstance(nested, dict) and str(nested.get(required_field) or "").strip():
                parsed = {**parsed, **nested}
        if required_field not in parsed or not str(parsed.get(required_field) or "").strip():
            raise ValueError(f"LLM response does not contain {required_field}")
        return {
            "provider": self.provider_name,
            "used_llm": True,
            "model": self.model,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "result": parsed,
        }

    def plan_question(self, context: dict[str, Any]) -> dict[str, Any]:
        result = self._call_json(
            system=(
                "你是企业财务折旧问答的 QuestionUnderstandingSkill。只理解问题，不回答，不计算，不访问数据库。"
                "必须输出单个 JSON 对象，字段：intent、scope、target_period、comparison_period、requested_evidence、"
                "resolved_entities、confidence、clarification_question。"
                "intent 只能是 period_variance、scope_summary、top_contributors、policy_trace、clarification。"
                "scope 只能使用输入目录中存在的 scenario_id、department、asset_category、asset_refs、block_ids。"
                "对于“某月上涨/下跌/提升/减少/为何变化”这类问题，出现一个明确月份时，将该月作为 target_period，"
                "comparison_period 留空即可，Harness 会按环比自动取上月；不要因此追问。"
                "期间、对象或范围确实不唯一/缺失时 intent 必须为 clarification，并写 clarification_question。"
                "不要输出答案、SQL、工具调用或解释文字。"
            ),
            context=context,
            required_field="intent",
        )
        return {**result, **result["result"]}

    def compose_answer(self, context: dict[str, Any]) -> dict[str, Any]:
        model_context = {
            key: value
            for key, value in context.items()
            if key not in {"template_answer_cn", "significant_asset_refs"}
        }
        system = (
            "你是企业财务折旧问答的 AnswerCompositionSkill。只能依据已验证的证据包生成中文业务结论。"
            "不得计算金额、增加或隐藏资产、编造规则或政策。必须覆盖核心归因组及关键资产；"
            "完整资产清单由 Harness 的确定性证据表展示，不要在 answer_cn 中逐条复述全部编号。"
            "answer_cn 控制在 450 个中文字符以内，按“结论、主要上升因素、主要抵消因素、口径提示”表达。"
            "输出单个 JSON 对象，字段必须包含非空 answer_cn、key_findings、next_steps。"
        )
        try:
            result = self._call_json(
                system=system,
                context=model_context,
                required_field="answer_cn",
                max_tokens=1_200,
            )
        except ValueError as exc:
            # Some OpenAI-compatible gateways occasionally return an empty content
            # field. Retry once with the same verified evidence before using the
            # deterministic expression layer.
            if "answer_cn" not in str(exc):
                raise
            result = self._call_json(
                system=f"{system} 上一次返回为空；本次必须在 answer_cn 写出结论。",
                context=model_context,
                required_field="answer_cn",
                max_tokens=900,
            )
        answer = str(result["result"].get("answer_cn") or "").strip()
        if not answer:
            raise ValueError("LLM response contains blank answer_cn")
        return {**result, **result["result"], "answer_cn": answer}

    def plan_reverse(self, context: dict[str, Any]) -> dict[str, Any]:
        result = self._call_json(
            system=(
                "你是企业财务折旧反向推演的 ReversePlanningUnderstandingSkill。只理解目标，不推荐动作，不计算金额，不访问数据库。"
                "必须输出单个 JSON 对象，字段：intent、scenario_id、target_period、scope_type、scope_value、target_amount、target_amount_unit、"
                "target_change_amount、target_change_unit、direction、requested_evidence、resolved_entities、confidence、clarification_question。"
                "intent 只能是 reverse_target、explain_recommendation、compare_recommendations、clarification。"
                "scope_type 只能是 company、department、asset_category；scope_value 必须来自输入目录。"
                "目标金额或变化金额必须保留为数字，并用 target_amount_unit 或 target_change_unit 填写 元、万或万元；单位由 Harness 统一转换。范围、月份、金额、单位或上轮指代不明确时必须 clarification。"
                "不得决定候选资产、动作、规则、金额调整值或方案排序；不要输出答案、SQL、工具调用或解释文字。"
            ),
            context=context,
            required_field="intent",
        )
        return {**result, **result["result"]}

    def compose_reverse(self, context: dict[str, Any]) -> dict[str, Any]:
        result = self._call_json(
            system=(
                "你是企业财务折旧反向推演的 AnswerCompositionSkill。只能依据 Harness 已验证的试算证据生成中文业务建议。"
                "不得计算金额、调整任何建议动作、增加或隐藏对象、编造规则或将减值等假设描述为已发生事实。"
                "必须覆盖 evidence_package.recommendations 中每一套方案的方案编号、试算金额和目标偏差。"
                "输出单个 JSON 对象，字段必须包含 answer_cn、key_findings、next_steps。"
            ),
            context=context,
            required_field="answer_cn",
        )
        answer = str(result["result"].get("answer_cn") or "").strip()
        if not answer:
            raise ValueError("LLM response contains blank answer_cn")
        return {**result, **result["result"], "answer_cn": answer}

    def answer(self, context: dict[str, Any]) -> dict[str, Any]:
        return self.compose_answer(context)


class FallbackWideTableQAProvider:
    provider_name = "fallback_wide_table_qa"

    def __init__(self) -> None:
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        self.template = TemplateWideTableQAProvider()
        self.deepseek: DeepSeekWideTableQAProvider | None = None
        if api_key:
            self.deepseek = DeepSeekWideTableQAProvider(
                api_key=api_key,
                model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
                base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
                timeout_seconds=int(os.environ.get("DEEPSEEK_TIMEOUT_SECONDS", "45")),
            )

    def answer(self, context: dict[str, Any]) -> dict[str, Any]:
        return self.compose_answer(context)

    def plan_question(self, context: dict[str, Any]) -> dict[str, Any]:
        if self.deepseek is None:
            return self.template.plan_question(context)
        try:
            return self.deepseek.plan_question(context)
        except (KeyError, ValueError, TimeoutError, json.JSONDecodeError, urllib.error.URLError) as exc:
            return TemplateWideTableQAProvider(f"DeepSeek 问题理解调用失败，已降级：{exc}").plan_question(context)

    def compose_answer(self, context: dict[str, Any]) -> dict[str, Any]:
        if self.deepseek is None:
            return self.template.compose_answer(context)
        try:
            return self.deepseek.compose_answer(context)
        except (KeyError, ValueError, TimeoutError, json.JSONDecodeError, urllib.error.URLError) as exc:
            return TemplateWideTableQAProvider(f"DeepSeek 业务表述调用失败，已降级：{exc}").compose_answer(context)

    def plan_reverse(self, context: dict[str, Any]) -> dict[str, Any]:
        if self.deepseek is None:
            return self.template.plan_reverse(context)
        try:
            return self.deepseek.plan_reverse(context)
        except (KeyError, ValueError, TimeoutError, json.JSONDecodeError, urllib.error.URLError) as exc:
            return TemplateWideTableQAProvider(f"DeepSeek 反推问题理解调用失败，已降级：{exc}").plan_reverse(context)

    def compose_reverse(self, context: dict[str, Any]) -> dict[str, Any]:
        if self.deepseek is None:
            return self.template.compose_reverse(context)
        try:
            return self.deepseek.compose_reverse(context)
        except (KeyError, ValueError, TimeoutError, json.JSONDecodeError, urllib.error.URLError) as exc:
            return TemplateWideTableQAProvider(f"DeepSeek 反推业务表述调用失败，已降级：{exc}").compose_reverse(context)

    def status(self) -> dict[str, Any]:
        if self.deepseek is None:
            return {
                "configured": False,
                "provider": "deepseek",
                "message_cn": "未检测到 DEEPSEEK_API_KEY；宽表问答将只返回确定性规则结论。",
            }
        return {
            "configured": True,
            "provider": "deepseek",
            "model": self.deepseek.model,
            "base_url": self.deepseek.base_url,
            "message_cn": "DeepSeek 已配置；每次提问会在确定性取数和图谱推理完成后调用模型生成业务表述。",
        }


@dataclass(frozen=True)
class WideTableQATools:
    forecast_lines: Callable[..., list[dict[str, Any]]]
    knowledge_graph_path: Callable[[dict[str, list[str]]], dict[str, Any]]
    policy_narrative: Callable[[str, str], dict[str, Any]]
    rule_executions: Callable[..., list[dict[str, Any]]] | None = None
    available_periods: Callable[[str], list[str]] | None = None


@dataclass
class ConversationState:
    """A short-lived, structured business context. No prompts or model reasoning are retained."""

    conversation_id: str
    created_at: datetime
    updated_at: datetime
    active_scope: dict[str, Any]
    resolved_entities: dict[str, list[str]]
    last_intent: str | None = None
    last_evidence_ref: str | None = None
    turns: list[dict[str, Any]] | None = None


class ConversationStore:
    ttl = timedelta(minutes=30)
    max_turns = 10

    def __init__(self) -> None:
        self._items: dict[str, ConversationState] = {}
        self._lock = threading.RLock()

    def open(self, conversation_id: str | None, default_scope: dict[str, Any]) -> tuple[ConversationState, bool]:
        now = datetime.now()
        with self._lock:
            if conversation_id:
                item = self._items.get(conversation_id)
                if item and now - item.updated_at <= self.ttl:
                    return item, False
            item = ConversationState(
                conversation_id=str(uuid.uuid4()),
                created_at=now,
                updated_at=now,
                active_scope=default_scope,
                resolved_entities={},
                turns=[],
            )
            self._items[item.conversation_id] = item
            self._prune(now)
            return item, True

    def record(
        self,
        state: ConversationState,
        *,
        scope: dict[str, Any],
        resolved_entities: dict[str, list[str]],
        intent: str,
        evidence_ref: str | None,
        conclusion_summary: str,
    ) -> None:
        with self._lock:
            state.updated_at = datetime.now()
            state.active_scope = scope
            state.resolved_entities = {key: list(value) for key, value in resolved_entities.items() if value}
            state.last_intent = intent
            state.last_evidence_ref = evidence_ref
            turns = state.turns if state.turns is not None else []
            turns.append({
                "at": state.updated_at.isoformat(timespec="seconds"),
                "intent": intent,
                "scope": scope,
                "resolved_entities": state.resolved_entities,
                "evidence_ref": evidence_ref,
                "conclusion_summary": conclusion_summary[:500],
            })
            state.turns = turns[-self.max_turns:]

    def view(self, state: ConversationState, *, is_new: bool = False) -> dict[str, Any]:
        return {
            "conversation_id": state.conversation_id,
            "is_new": is_new,
            "expires_in_seconds": int(self.ttl.total_seconds()),
            "turn_count": len(state.turns or []),
            "active_scope": state.active_scope,
            "resolved_entities": state.resolved_entities,
            "last_intent": state.last_intent,
            "last_evidence_ref": state.last_evidence_ref,
        }

    def _prune(self, now: datetime) -> None:
        expired = [key for key, item in self._items.items() if now - item.updated_at > self.ttl]
        for key in expired:
            self._items.pop(key, None)


class OntologyQuestionHarness:
    """Validates a model plan and runs only registered read-only evidence functions."""

    function_catalog = {
        "list_available_periods": "读取当前场景可查询月份",
        "resolve_ontology_entities": "解析资产、组织、类别等业务对象",
        "query_forecast_lines": "读取折旧预测或台账快照明细",
        "summarize_scope": "汇总当前业务范围的折旧金额",
        "compare_forecast_periods": "按资产对比两个期间的折旧金额",
        "get_rule_execution_evidence": "读取实际命中的折旧规则、公式和输入",
        "trace_asset_policy_path": "追溯资产到折旧规则的 Ontology 路径",
        "get_ontology_neighbors": "读取对象的关联业务对象",
    }

    intent_evidence = {
        "period_variance": ["comparison", "material_drivers", "rule_execution", "ontology_path"],
        "scope_summary": ["scope_summary", "top_contributors", "ontology_path"],
        "top_contributors": ["scope_summary", "top_contributors", "ontology_path"],
        "policy_trace": ["policy", "ontology_path", "rule_execution"],
    }

    def catalog(self, *, scenario_id: str, periods: list[str]) -> dict[str, Any]:
        return {
            "intents": [
                {"id": key, "label_cn": label}
                for key, label in {
                    "period_variance": "期间涨跌原因",
                    "scope_summary": "范围折旧汇总",
                    "top_contributors": "主要贡献资产",
                    "policy_trace": "折旧依据追溯",
                    "clarification": "需要补充信息",
                }.items()
            ],
            "evidence_types": [
                {"id": item, "label_cn": label}
                for item, label in {
                    "comparison": "期间金额对比",
                    "material_drivers": "显著差异资产",
                    "rule_execution": "规则执行证据",
                    "ontology_path": "Ontology 路径",
                    "scope_summary": "范围汇总",
                    "top_contributors": "主要贡献资产",
                    "policy": "适用折旧依据",
                }.items()
            ],
            "functions": [
                {"id": key, "label_cn": label, "read_only": True}
                for key, label in self.function_catalog.items()
            ],
            "scenario_id": scenario_id,
            "available_periods": periods,
        }


class WideTableQASkill:
    skill_name = "wide_table_finance_qa"

    def __init__(self, *, tools: WideTableQATools, provider: WideTableQAProvider | None = None) -> None:
        self.tools = tools
        self.provider = provider or FallbackWideTableQAProvider()
        self.last_call: dict[str, Any] | None = None
        self.conversations = ConversationStore()
        self.harness = OntologyQuestionHarness()

    def status(self) -> dict[str, Any]:
        provider_status = self.provider.status() if hasattr(self.provider, "status") else {
            "configured": False,
            "provider": getattr(self.provider, "provider_name", "unknown"),
        }
        return {**provider_status, "last_call": self.last_call}

    def answer(self, payload: dict[str, object]) -> dict[str, object]:
        scenario_id = str(payload.get("scenario_id") or "BASELINE")
        question = str(payload.get("question") or "为什么折旧变化？").strip()
        payload_department = _optional_text(payload.get("department"))
        payload_category = _optional_text(payload.get("asset_category"))
        period_from = _optional_text(payload.get("period_from"))
        period_to = _optional_text(payload.get("period_to"))
        available_periods = self.tools.available_periods(scenario_id) if self.tools.available_periods else []
        default_scope = {
            "scenario_id": scenario_id,
            "department": payload_department,
            "asset_category": payload_category,
            "period_from": period_from,
            "period_to": period_to,
            "row_type": str(payload.get("row_type") or "overview"),
        }
        conversation, is_new = self.conversations.open(_optional_text(payload.get("conversation_id")), default_scope)
        planning_context = self._planning_context(
            question=question,
            default_scope=default_scope,
            available_periods=available_periods,
            conversation=conversation,
        )
        understanding = self.provider.plan_question(planning_context)
        understanding = {**understanding, "_question": question}
        validation = self._validate_question_plan(
            plan=understanding,
            default_scope=default_scope,
            conversation=conversation,
            available_periods=available_periods,
        )
        if not validation["valid"]:
            self._record_call(understanding)
            clarification = validation["clarification"]
            self.conversations.record(
                conversation,
                scope=default_scope,
                resolved_entities=conversation.resolved_entities,
                intent="clarification",
                evidence_ref=None,
                conclusion_summary=clarification["question_cn"],
            )
            return self._clarification_response(
                question=question,
                conversation=conversation,
                is_new=is_new,
                plan=understanding,
                validation=validation,
                clarification=clarification,
            )

        plan = validation["plan"]
        execution = self._execute_plan(question=question, plan=plan)
        composition_context = self._composition_context(
            question=question,
            plan=plan,
            evidence=execution,
        )
        generation = self.provider.compose_answer(composition_context)
        generation = self._validated_generation(generation, composition_context, execution.get("comparison") or {})
        self._record_call(generation)
        audit_id = f"QA-{uuid.uuid4().hex[:12].upper()}"
        resolved = self._resolved_entities_from_execution(plan, execution)
        self.conversations.record(
            conversation,
            scope=plan["scope"],
            resolved_entities=resolved,
            intent=str(plan["intent"]),
            evidence_ref=audit_id,
            conclusion_summary=str(generation["answer_cn"]),
        )
        answer_validation = self._answer_validation(generation, execution)
        return {
            "question": question,
            "scope": plan["scope"],
            "conversation": self.conversations.view(conversation, is_new=is_new),
            "question_plan": plan,
            "plan_validation": validation,
            "question_analysis": self._question_analysis_from_plan(plan),
            "clarification": None,
            "comparison": execution.get("comparison"),
            "reasoning_steps": execution["reasoning_steps"],
            "graph_reasoning": execution.get("graph_reasoning"),
            "ontology_paths": execution.get("ontology_paths", []),
            "rule_execution_trace": execution.get("rule_execution_trace", []),
            "harness": execution["harness"],
            "evidence": execution["evidence"],
            "facts": execution["facts"],
            "answer_cn": generation["answer_cn"],
            "key_findings": generation.get("key_findings", []),
            "next_steps": generation.get("next_steps", []),
            "model_calls": {
                "question_understanding": self._model_call_metadata(understanding),
                "answer_composition": self._model_call_metadata(generation),
            },
            "answer_validation": answer_validation,
            "audit_id": audit_id,
            "qa_skill": self._skill_metadata(generation, execution["harness"]["tool_trace"]),
        }

    def catalog(self, scenario_id: str = "BASELINE") -> dict[str, Any]:
        periods = self.tools.available_periods(scenario_id) if self.tools.available_periods else []
        lines = self.tools.forecast_lines(scenario_id=scenario_id, limit=10000)
        return {
            **self.harness.catalog(scenario_id=scenario_id, periods=periods),
            "ontology_objects": [
                {"id": "FixedAsset", "label_cn": "存量资产"},
                {"id": "PlannedAsset", "label_cn": "计划资产"},
                {"id": "Department", "label_cn": "所属单位"},
                {"id": "AssetCategory", "label_cn": "资产类别"},
                {"id": "DepreciationCode", "label_cn": "折旧码"},
                {"id": "DepreciationMethod", "label_cn": "折旧方法"},
                {"id": "CalculationRule", "label_cn": "计算规则分支"},
            ],
            "ontology_relations": [
                {"id": "belongs_to_department", "label_cn": "归属所属单位"},
                {"id": "registered_as_category", "label_cn": "登记为资产类别"},
                {"id": "uses_depreciation_code", "label_cn": "使用折旧码"},
                {"id": "uses_method", "label_cn": "对应折旧方法"},
                {"id": "executes_rule", "label_cn": "命中计算规则"},
            ],
            "filter_values": {
                "departments": sorted({str(line.get("department")) for line in lines if line.get("department")} ),
                "asset_categories": sorted({str(line.get("asset_category")) for line in lines if line.get("asset_category")} ),
            },
        }

    def _planning_context(
        self,
        *,
        question: str,
        default_scope: dict[str, Any],
        available_periods: list[str],
        conversation: ConversationState,
    ) -> dict[str, Any]:
        lines = self.tools.forecast_lines(scenario_id=str(default_scope["scenario_id"]), limit=10000)
        return {
            "task": "question_understanding",
            "question": question,
            "current_wide_table_scope": default_scope,
            "available_periods": available_periods,
            "available_entities": {
                "departments": sorted({str(line.get("department")) for line in lines if line.get("department")} ),
                "asset_categories": sorted({str(line.get("asset_category")) for line in lines if line.get("asset_category")} ),
                "asset_refs": sorted({str(line.get("asset_id") or line.get("planned_asset_id")) for line in lines if line.get("asset_id") or line.get("planned_asset_id")} ),
            },
            "allowed_intents": list(OntologyQuestionHarness.intent_evidence),
            "allowed_evidence": OntologyQuestionHarness.intent_evidence,
            "ontology_catalog": self.catalog(str(default_scope["scenario_id"])),
            "conversation_context": {
                "active_scope": conversation.active_scope,
                "resolved_entities": conversation.resolved_entities,
                "last_intent": conversation.last_intent,
                "recent_turn_summaries": list(conversation.turns or [])[-3:],
            },
            "rules": [
                "仅返回 JSON 查询计划。",
                "只有范围、期间和对象均可确定时，才选择非 clarification 意图。",
                "不得编造目录之外的资产、部门、类别、期间或场景。",
            ],
        }

    def _validate_question_plan(
        self,
        *,
        plan: dict[str, Any],
        default_scope: dict[str, Any],
        conversation: ConversationState,
        available_periods: list[str],
    ) -> dict[str, Any]:
        if plan.get("clarification") or plan.get("intent") == "clarification":
            inferred = self._infer_period_variance_plan(
                question=str(plan.get("_question") or ""),
                default_scope=default_scope,
                available_periods=available_periods,
            )
            if inferred:
                return inferred
            return {
                "valid": False,
                "reason_cn": "问题理解阶段要求补充业务范围或期间。",
                "clarification": {
                    "question_cn": str(plan.get("clarification_question") or "请明确要查看的所属单位、资产类别或月份。"),
                    "candidates": self._clarification_candidates(default_scope, available_periods),
                },
            }
        intent = str(plan.get("intent") or "")
        if intent not in OntologyQuestionHarness.intent_evidence:
            return self._invalid_plan("模型返回了未注册的问答意图。", default_scope, available_periods)
        raw_scope = plan.get("scope") if isinstance(plan.get("scope"), dict) else {}
        scope = {
            "scenario_id": str(raw_scope.get("scenario_id") or default_scope["scenario_id"]),
            "department": _optional_text(raw_scope.get("department")) or _optional_text(default_scope.get("department")),
            "asset_category": _optional_text(raw_scope.get("asset_category")) or _optional_text(default_scope.get("asset_category")),
            "asset_refs": _as_text_list(raw_scope.get("asset_refs")) or _as_text_list(raw_scope.get("asset_ref")),
            "block_ids": _as_text_list(raw_scope.get("block_ids")),
            "period_from": _optional_text(raw_scope.get("period_from")) or _optional_text(default_scope.get("period_from")),
            "period_to": _optional_text(raw_scope.get("period_to")) or _optional_text(default_scope.get("period_to")),
            "row_type": default_scope.get("row_type") or "overview",
        }
        if scope["scenario_id"] != default_scope["scenario_id"]:
            return self._invalid_plan("当前会话不允许切换到未确认的场景。", default_scope, available_periods)
        all_lines = self.tools.forecast_lines(scenario_id=scope["scenario_id"], limit=10000)
        departments = {str(item.get("department")) for item in all_lines if item.get("department")}
        categories = {str(item.get("asset_category")) for item in all_lines if item.get("asset_category")}
        asset_refs = {str(item.get("asset_id") or item.get("planned_asset_id")) for item in all_lines if item.get("asset_id") or item.get("planned_asset_id")}
        if scope["department"] and scope["department"] not in departments:
            return self._invalid_plan("模型识别的所属单位不在当前台账范围内。", default_scope, available_periods)
        if scope["asset_category"] and scope["asset_category"] not in categories:
            return self._invalid_plan("模型识别的资产类别不在当前台账范围内。", default_scope, available_periods)
        unknown_assets = [item for item in scope["asset_refs"] if item not in asset_refs]
        if unknown_assets:
            return self._invalid_plan("模型识别了当前台账不存在的资产。", default_scope, available_periods)
        target = _optional_text(plan.get("target_period")) or scope["period_to"]
        compare = _optional_text(plan.get("comparison_period"))
        if intent == "period_variance":
            if not target or target not in available_periods:
                return self._invalid_plan("未能确认需要比较的目标月份。", default_scope, available_periods)
            if not compare:
                compare = self._previous_period(target)
            if not compare or compare not in available_periods:
                return self._invalid_plan("未能确认目标月份对应的可比较上期。", default_scope, available_periods)
            scope["period_from"] = compare
            scope["period_to"] = target
        elif target:
            if target not in available_periods:
                return self._invalid_plan("目标月份不在当前宽表可查询期间。", default_scope, available_periods)
            scope["period_from"] = target
            scope["period_to"] = target
        requested = [item for item in _as_text_list(plan.get("requested_evidence")) if item in OntologyQuestionHarness.intent_evidence[intent]]
        if not requested:
            requested = OntologyQuestionHarness.intent_evidence[intent]
        resolved = plan.get("resolved_entities") if isinstance(plan.get("resolved_entities"), dict) else {}
        resolved_entities = {
            "asset_refs": scope["asset_refs"] or _as_text_list(resolved.get("asset_refs")),
            "departments": [scope["department"]] if scope["department"] else _as_text_list(resolved.get("departments")),
            "asset_categories": [scope["asset_category"]] if scope["asset_category"] else _as_text_list(resolved.get("asset_categories")),
            "block_ids": scope["block_ids"] or _as_text_list(resolved.get("block_ids")),
        }
        return {
            "valid": True,
            "reason_cn": "查询计划已通过范围、期间、对象和只读权限校验。",
            "plan": {
                "intent": intent,
                "intent_label_cn": {
                    "period_variance": "期间涨跌原因",
                    "scope_summary": "范围折旧汇总",
                    "top_contributors": "主要贡献资产",
                    "policy_trace": "折旧依据追溯",
                }[intent],
                "scope": scope,
                "target_period": target,
                "comparison_period": compare,
                "requested_evidence": requested,
                "resolved_entities": resolved_entities,
                "confidence": str(plan.get("confidence") or "medium"),
            },
        }

    def _infer_period_variance_plan(
        self,
        *,
        question: str,
        default_scope: dict[str, Any],
        available_periods: list[str],
    ) -> dict[str, Any] | None:
        """A narrow validation repair, not a business answer: explicit month + variance words defaults to MoM."""
        normalized = question.replace(" ", "")
        target = self._period_from_question(normalized, available_periods)
        has_variance = any(word in normalized.lower() for word in (
            "上涨", "上升", "提升", "增加", "提高", "下跌", "下降", "下滑", "回落", "减少", "降低", "为何", "为什么", "why", "increase", "decrease", "drop",
        ))
        if not target or not has_variance:
            return None
        previous = self._previous_period(target)
        if not previous or previous not in available_periods:
            return None
        scope = {
            "scenario_id": str(default_scope["scenario_id"]),
            "department": _optional_text(default_scope.get("department")),
            "asset_category": _optional_text(default_scope.get("asset_category")),
            "asset_refs": [],
            "block_ids": [],
            "period_from": previous,
            "period_to": target,
            "row_type": default_scope.get("row_type") or "overview",
        }
        return {
            "valid": True,
            "reason_cn": "模型未明确输出比较期间；Harness 根据明确月份和变化语义，按默认环比口径补全上期。",
            "plan": {
                "intent": "period_variance",
                "intent_label_cn": "期间涨跌原因",
                "scope": scope,
                "target_period": target,
                "comparison_period": previous,
                "requested_evidence": OntologyQuestionHarness.intent_evidence["period_variance"],
                "resolved_entities": {"asset_refs": [], "departments": [scope["department"]] if scope["department"] else [], "asset_categories": [scope["asset_category"]] if scope["asset_category"] else [], "block_ids": []},
                "confidence": "medium",
            },
        }

    def _invalid_plan(self, reason: str, default_scope: dict[str, Any], available_periods: list[str]) -> dict[str, Any]:
        return {
            "valid": False,
            "reason_cn": reason,
            "clarification": {
                "question_cn": "请补充要分析的所属单位、资产类别或具体月份后再提问。",
                "candidates": self._clarification_candidates(default_scope, available_periods),
            },
        }

    @staticmethod
    def _clarification_candidates(scope: dict[str, Any], periods: list[str]) -> dict[str, Any]:
        return {
            "current_scope": scope,
            "available_periods": periods,
            "examples": ["比较 2026-08 与 2026-07 的折旧变化", "查询某所属单位 2026-08 的主要折旧资产"],
        }

    def _clarification_response(
        self,
        *,
        question: str,
        conversation: ConversationState,
        is_new: bool,
        plan: dict[str, Any],
        validation: dict[str, Any],
        clarification: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "question": question,
            "conversation": self.conversations.view(conversation, is_new=is_new),
            "question_plan": {"intent": "clarification", "confidence": plan.get("confidence", "low")},
            "plan_validation": {"valid": False, "reason_cn": validation["reason_cn"]},
            "clarification": clarification,
            "answer_cn": clarification["question_cn"],
            "reasoning_steps": [{"step": 1, "title_cn": "问题理解", "detail_cn": validation["reason_cn"]}],
            "harness": {"tool_trace": [], "evidence_summary": {"status": "not_executed", "reason_cn": "范围或期间尚未确认，未读取金额数据。"}},
            "model_calls": {"question_understanding": self._model_call_metadata(plan), "answer_composition": None},
            "answer_validation": {"valid": True, "status": "clarification"},
            "qa_skill": self._skill_metadata(plan, []),
        }

    def _execute_plan(self, *, question: str, plan: dict[str, Any]) -> dict[str, Any]:
        """The only place a validated plan may reach business data and graph functions."""
        scope = plan["scope"]
        scenario_id = str(scope["scenario_id"])
        department = _optional_text(scope.get("department"))
        asset_category = _optional_text(scope.get("asset_category"))
        intent = str(plan["intent"])
        trace: list[dict[str, Any]] = [
            {
                "tool_name": "list_available_periods",
                "label_cn": OntologyQuestionHarness.function_catalog["list_available_periods"],
                "read_only": True,
                "result_shape": {"period_count": len(self.tools.available_periods(scenario_id) if self.tools.available_periods else [])},
            },
            {
                "tool_name": "resolve_ontology_entities",
                "label_cn": OntologyQuestionHarness.function_catalog["resolve_ontology_entities"],
                "read_only": True,
                "result_shape": plan["resolved_entities"],
            },
        ]
        if intent == "period_variance":
            comparison = self._period_comparison(
                scenario_id=scenario_id,
                department=department,
                asset_category=asset_category,
                target_period=str(plan["target_period"]),
                previous_period=str(plan["comparison_period"]),
            )
            trace.append({
                "tool_name": "compare_forecast_periods",
                "label_cn": OntologyQuestionHarness.function_catalog["compare_forecast_periods"],
                "read_only": True,
                "result_shape": {
                    "previous_period": comparison["previous_period"],
                    "target_period": comparison["target_period"],
                    "input_line_count": comparison["line_count"],
                    "significant_driver_count": comparison["significant_driver_count"],
                    "coverage_percent": comparison["significance_coverage_percent"],
                },
            })
            material_refs = [str(item.get("asset_ref")) for item in comparison.get("material_drivers", []) if item.get("asset_ref")]
            rule_execution_trace = self._rule_execution_evidence(
                scenario_id=scenario_id,
                asset_refs=material_refs,
                periods=[str(plan["comparison_period"]), str(plan["target_period"])],
            )
            self._attach_rule_execution_evidence(comparison, rule_execution_trace)
            graph_reasoning = self._graph_reasoning_for_drivers(scenario_id=scenario_id, drivers=comparison.get("material_drivers", []))
            trace.extend([
                {
                    "tool_name": "get_rule_execution_evidence",
                    "label_cn": OntologyQuestionHarness.function_catalog["get_rule_execution_evidence"],
                    "read_only": True,
                    "result_shape": {"execution_count": len(rule_execution_trace), "asset_count": len(material_refs)},
                },
                self._harness_graph_trace(graph_reasoning),
            ])
            analysis = self._question_analysis_from_plan(plan)
            self._set_actual_comparison_direction(analysis, comparison)
            plan["direction"] = analysis["direction"]
            steps = self._change_steps(
                question=question,
                scenario_id=scenario_id,
                department=department,
                question_analysis=analysis,
                comparison=comparison,
                graph_reasoning=graph_reasoning,
            )
            return {
                "comparison": comparison,
                "facts": comparison,
                "graph_reasoning": graph_reasoning,
                "ontology_paths": list((graph_reasoning or {}).get("driver_paths") or []),
                "rule_execution_trace": rule_execution_trace,
                "reasoning_steps": steps,
                "evidence": {"comparison": comparison, "rule_executions": rule_execution_trace, "ontology_paths": list((graph_reasoning or {}).get("driver_paths") or [])},
                "harness": {"tool_trace": trace, "evidence_summary": self._comparison_evidence_summary(comparison)},
                "template_answer_cn": self._change_answer(comparison, graph_reasoning),
            }

        asset_refs = _as_text_list(scope.get("asset_refs"))
        period = _optional_text(plan.get("target_period"))
        lines = self.tools.forecast_lines(
            scenario_id=scenario_id,
            department=department,
            asset_category=asset_category,
            period_from=period or _optional_text(scope.get("period_from")),
            period_to=period or _optional_text(scope.get("period_to")),
            limit=10000,
        )
        if asset_refs:
            lines = [line for line in lines if str(line.get("asset_id") or line.get("planned_asset_id") or "") in asset_refs]
        trace.append({
            "tool_name": "query_forecast_lines",
            "label_cn": OntologyQuestionHarness.function_catalog["query_forecast_lines"],
            "read_only": True,
            "result_shape": {"line_count": len(lines), "period": period},
        })
        facts = self._scope_facts(lines)
        trace.append({
            "tool_name": "summarize_scope",
            "label_cn": OntologyQuestionHarness.function_catalog["summarize_scope"],
            "read_only": True,
            "result_shape": {"total_depreciation": facts["total_depreciation"], "top_asset_count": len(facts["top_assets"])},
        })
        top_assets = facts.get("top_assets", []) if intent == "top_contributors" else [facts.get("top_asset")] if facts.get("top_asset") else []
        graph_reasoning = self._graph_reasoning(scenario_id=scenario_id, top_asset=top_assets[0] if top_assets else None)
        refs_for_rules = [str(item.get("asset_ref")) for item in top_assets if item and item.get("asset_ref")]
        rule_execution_trace = self._rule_execution_evidence(
            scenario_id=scenario_id,
            asset_refs=refs_for_rules,
            periods=[period] if period else [],
        )
        trace.extend([
            {
                "tool_name": "get_rule_execution_evidence",
                "label_cn": OntologyQuestionHarness.function_catalog["get_rule_execution_evidence"],
                "read_only": True,
                "result_shape": {"execution_count": len(rule_execution_trace), "asset_count": len(refs_for_rules)},
            },
            self._harness_graph_trace(graph_reasoning),
        ])
        if intent == "policy_trace":
            if not asset_refs:
                raise ValueError("政策依据追溯需要先确认具体资产。")
            narratives = [self.tools.policy_narrative(asset_ref, scenario_id) for asset_ref in asset_refs]
            facts["policy_narratives"] = narratives
            template = "；".join(str(item.get("narrative_cn") or "") for item in narratives if item.get("narrative_cn"))
        else:
            template = self._scope_answer(facts, graph_reasoning)
        steps = self._scope_steps(
            question=question,
            scenario_id=scenario_id,
            department=department,
            asset_category=asset_category,
            period_from=period or _optional_text(scope.get("period_from")),
            period_to=period or _optional_text(scope.get("period_to")),
            facts=facts,
            graph_reasoning=graph_reasoning,
        )
        return {
            "facts": facts,
            "graph_reasoning": graph_reasoning,
            "ontology_paths": [graph_reasoning] if graph_reasoning else [],
            "rule_execution_trace": rule_execution_trace,
            "reasoning_steps": steps,
            "evidence": {"facts": facts, "rule_executions": rule_execution_trace, "ontology_paths": [graph_reasoning] if graph_reasoning else []},
            "harness": {"tool_trace": trace, "evidence_summary": {"line_count": len(lines), "total_depreciation": facts["total_depreciation"]}},
            "template_answer_cn": template or "当前范围没有可供解释的折旧明细。",
        }

    def _rule_execution_evidence(self, *, scenario_id: str, asset_refs: list[str], periods: list[str]) -> list[dict[str, Any]]:
        if not self.tools.rule_executions or not asset_refs:
            return []
        executions: list[dict[str, Any]] = []
        for period in periods:
            executions.extend(self.tools.rule_executions(scenario_id=scenario_id, asset_refs=asset_refs, period=period))
        return executions

    def _composition_context(self, *, question: str, plan: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
        comparison = evidence.get("comparison") or {}
        material_drivers = [item for item in comparison.get("material_drivers", []) if item.get("asset_ref")]
        material_refs = [str(item.get("asset_ref")) for item in material_drivers]
        if not material_refs:
            material_drivers = [item for item in (evidence.get("facts") or {}).get("top_assets", []) if item.get("asset_ref")]
            material_refs = [str(item.get("asset_ref")) for item in material_drivers]
        key_drivers = self._key_drivers_for_composition(material_drivers)
        required_answer_refs = self._required_answer_refs(material_drivers)
        composition_evidence = self._composition_evidence(evidence, key_drivers)
        return {
            "task": "answer_composition",
            "question": question,
            "validated_question_plan": plan,
            "evidence_package": composition_evidence,
            "significant_asset_refs": material_refs,
            "key_asset_refs": [str(item.get("asset_ref")) for item in key_drivers],
            "required_answer_asset_refs": required_answer_refs,
            "significant_driver_count": len(material_refs),
            "template_answer_cn": evidence["template_answer_cn"],
            "guardrails": [
                "金额、资产集合和规则结论均由 Ontology Question Harness 产生。",
                "必须覆盖 required_answer_asset_refs 和每个核心归因组；完整 significant_asset_refs 清单已由 Harness 在页面证据表中展示。",
                "不得增加未在证据包中的资产。",
                "不得展示模型内部思维或未验证的推测。",
            ],
        }

    @staticmethod
    def _key_drivers_for_composition(drivers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep the LLM prompt compact while retaining every material attribution group."""
        positive = sorted(
            (item for item in drivers if Decimal(str(item.get("difference") or "0")) > 0),
            key=lambda item: Decimal(str(item.get("abs_difference") or "0")),
            reverse=True,
        )
        negative = sorted(
            (item for item in drivers if Decimal(str(item.get("difference") or "0")) < 0),
            key=lambda item: Decimal(str(item.get("abs_difference") or "0")),
            reverse=True,
        )
        selected: list[dict[str, Any]] = []
        seen_groups: set[tuple[str, str]] = set()
        for item in [*positive, *negative]:
            group = (str(item.get("depreciation_code") or ""), str(item.get("driver_category") or ""))
            if group not in seen_groups:
                selected.append(item)
                seen_groups.add(group)
        for item in [*positive[:3], *negative[:3]]:
            if item not in selected:
                selected.append(item)
        if not selected:
            selected.extend(sorted(
                drivers,
                key=lambda item: Decimal(str(item.get("abs_difference") or "0")),
                reverse=True,
            )[:8])
        return selected[:8]

    @staticmethod
    def _required_answer_refs(drivers: list[dict[str, Any]]) -> list[str]:
        positive = sorted(
            (item for item in drivers if Decimal(str(item.get("difference") or "0")) > 0),
            key=lambda item: Decimal(str(item.get("abs_difference") or "0")), reverse=True,
        )
        negative = sorted(
            (item for item in drivers if Decimal(str(item.get("difference") or "0")) < 0),
            key=lambda item: Decimal(str(item.get("abs_difference") or "0")), reverse=True,
        )
        chosen = [*positive[:2], *negative[:1]] or drivers[:2]
        return [str(item.get("asset_ref")) for item in chosen if item.get("asset_ref")]

    @staticmethod
    def _composition_evidence(evidence: dict[str, Any], key_drivers: list[dict[str, Any]]) -> dict[str, Any]:
        comparison = dict(evidence.get("comparison") or {})
        facts = {
            "comparison_summary": WideTableQASkill._comparison_evidence_summary(comparison),
            "driver_groups": WideTableQASkill._driver_groups_for_composition(
                [item for item in comparison.get("material_drivers", []) if item.get("asset_ref")]
            ),
            "key_drivers": [
                {
                    key: item.get(key)
                    for key in (
                        "asset_ref", "asset_name", "difference", "previous_amount", "target_amount",
                        "depreciation_code", "depreciation_code_label_cn", "depreciation_policy_label_cn",
                        "driver_category", "calculation_evidence_cn",
                    )
                }
                for item in key_drivers
            ],
            "all_significant_assets_available_in_ui": len(comparison.get("material_drivers", [])),
        }
        rules_by_asset = {
            str(item.get("asset_ref")): {
                key: item.get(key)
                for key in ("asset_ref", "period", "branch_id", "formula_cn", "conclusion_cn")
            }
            for item in evidence.get("rule_execution_trace", [])
            if item.get("asset_ref")
        }
        return {
            "facts": facts,
            "key_rule_executions": [rules_by_asset[ref] for ref in [str(item.get("asset_ref")) for item in key_drivers] if ref in rules_by_asset],
            "ontology_paths": WideTableQASkill._model_ontology_paths(evidence.get("ontology_paths", []), key_drivers),
        }

    @staticmethod
    def _model_ontology_paths(
        ontology_paths: list[dict[str, Any]],
        key_drivers: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Send concise business paths to the LLM; keep raw graph diagnostics for the UI."""
        key_refs = {str(item.get("asset_ref")) for item in key_drivers if item.get("asset_ref")}
        summaries: list[dict[str, Any]] = []
        for graph_reasoning in ontology_paths:
            for item in graph_reasoning.get("driver_paths", []) if isinstance(graph_reasoning, dict) else []:
                asset_ref = str(item.get("asset_ref") or "")
                if asset_ref not in key_refs:
                    continue
                path = item.get("path") or {}
                policy = item.get("policy_narrative") or {}
                summaries.append({
                    "asset_ref": asset_ref,
                    "path_cn": path.get("narrative_cn"),
                    "policy_cn": policy.get("narrative_cn"),
                    "driver_reason_cn": item.get("driver_reason_cn"),
                })
        return summaries

    @staticmethod
    def _driver_groups_for_composition(drivers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for item in drivers:
            key = (str(item.get("depreciation_code_label_cn") or "其他规则"), str(item.get("driver_category") or "other"))
            group = groups.setdefault(key, {
                "depreciation_code_label_cn": key[0],
                "driver_category": key[1],
                "asset_count": 0,
                "difference": Decimal("0"),
                "sample_asset_refs": [],
            })
            group["asset_count"] += 1
            group["difference"] += Decimal(str(item.get("difference") or "0"))
            if len(group["sample_asset_refs"]) < 3:
                group["sample_asset_refs"].append(str(item.get("asset_ref")))
        return [
            {**item, "difference": f"{item['difference']:.2f}"}
            for item in sorted(groups.values(), key=lambda item: abs(item["difference"]), reverse=True)
        ]

    @staticmethod
    def _comparison_evidence_summary(comparison: dict[str, Any]) -> dict[str, Any]:
        return {
            "previous_period": comparison.get("previous_period"),
            "target_period": comparison.get("target_period"),
            "previous_total": comparison.get("previous_total"),
            "target_total": comparison.get("target_total"),
            "difference": comparison.get("difference"),
            "significant_driver_count": comparison.get("significant_driver_count"),
            "significance_rule_cn": comparison.get("significance_rule_cn"),
            "coverage_percent": comparison.get("significance_coverage_percent"),
        }

    @staticmethod
    def _harness_graph_trace(graph_reasoning: dict[str, Any] | None) -> dict[str, Any]:
        trace = WideTableQASkill._graph_trace(graph_reasoning)
        trace["tool_name"] = "trace_asset_policy_path"
        trace["label_cn"] = OntologyQuestionHarness.function_catalog["trace_asset_policy_path"]
        return trace

    @staticmethod
    def _question_analysis_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
        scope = plan["scope"]
        return {
            "intent": plan["intent"],
            "intent_label_cn": plan["intent_label_cn"],
            "direction": "increase" if str(plan.get("direction") or "") == "increase" else "decrease" if str(plan.get("direction") or "") == "decrease" else "unknown",
            "comparison_mode": "period_over_period" if plan["intent"] == "period_variance" else None,
            "confidence": plan["confidence"],
            "department": scope.get("department"),
            "asset_category": scope.get("asset_category"),
            "asset_category_label_cn": category_label(scope.get("asset_category")) if scope.get("asset_category") else None,
            "asset_ref": ", ".join(scope.get("asset_refs") or []) or None,
            "target_period": plan.get("target_period"),
            "previous_period": plan.get("comparison_period"),
            "recognized_terms": [item for item in [scope.get("department"), category_label(scope.get("asset_category")) if scope.get("asset_category") else None, plan.get("target_period")] if item],
        }

    @staticmethod
    def _resolved_entities_from_execution(plan: dict[str, Any], execution: dict[str, Any]) -> dict[str, list[str]]:
        scope = plan["scope"]
        comparison = execution.get("comparison") or {}
        return {
            "asset_refs": [str(item.get("asset_ref")) for item in comparison.get("material_drivers", []) if item.get("asset_ref")] or _as_text_list(scope.get("asset_refs")),
            "departments": [str(scope["department"])] if scope.get("department") else [],
            "asset_categories": [str(scope["asset_category"])] if scope.get("asset_category") else [],
            "block_ids": _as_text_list(plan.get("resolved_entities", {}).get("block_ids")),
        }

    @staticmethod
    def _model_call_metadata(result: dict[str, Any] | None) -> dict[str, Any] | None:
        if result is None:
            return None
        return {
            "provider": result.get("provider"),
            "model": result.get("model"),
            "used_llm": bool(result.get("used_llm")),
            "latency_ms": result.get("latency_ms"),
            "fallback_reason": result.get("fallback_reason"),
        }

    @staticmethod
    def _answer_validation(generation: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
        comparison = execution.get("comparison") or {}
        drivers = [item for item in comparison.get("material_drivers", []) if item.get("asset_ref")]
        expected = WideTableQASkill._required_answer_refs(drivers)
        answer = str(generation.get("answer_cn") or "")
        missing = [item for item in expected if item not in answer]
        return {
            "valid": not missing,
            "missing_asset_refs": missing,
            "expression_layer": "template_fallback" if generation.get("evidence_complete_template_used") or not generation.get("used_llm") else "deepseek",
            "reason_cn": "业务表述已覆盖核心归因；全部显著资产均在确定性差异资产表中展示。" if not missing else "模型表述遗漏关键归因资产，已由确定性证据结论替换。",
        }

    def _record_call(self, generation: dict[str, Any]) -> None:
        from datetime import datetime

        self.last_call = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "used_llm": bool(generation.get("used_llm")),
            "model": generation.get("model"),
            "fallback_reason": generation.get("fallback_reason"),
        }

    @staticmethod
    def _period_from_question(text: str, available_periods: list[str] | None = None) -> str | None:
        match = re.search(r"(20\d{2})[-/年](\d{1,2})月?", text)
        if match:
            return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}"
        match = re.search(r"(\d{2})年(\d{1,2})月", text)
        if match:
            return f"20{int(match.group(1)):02d}-{int(match.group(2)):02d}"
        # 宽表中的口语问题常省略年份，例如“8月比7月少的原因”。
        # 只在当前场景中该月份唯一时补全年月，避免跨年度时擅自猜测。
        month_matches = re.findall(r"(?<!\d)(1[0-2]|0?[1-9])月", text)
        if not month_matches or not available_periods:
            return None
        target_month = int(month_matches[0])
        candidates = sorted({
            period for period in available_periods
            if re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", period)
            and int(period[-2:]) == target_month
        })
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    def _previous_period(period: str | None) -> str | None:
        if not period:
            return None
        try:
            return str(Month.parse(period).add(-1))
        except (ValueError, IndexError):
            return None

    def _scope_facts(self, lines: list[dict[str, object]]) -> dict[str, object]:
        total = sum(Decimal(str(line.get("monthly_depreciation") or "0")) for line in lines)
        by_asset: dict[str, dict[str, object]] = {}
        by_policy: dict[str, Decimal] = {}
        by_source: dict[str, Decimal] = {}
        for line in lines:
            asset_ref = str(line.get("asset_id") or line.get("planned_asset_id") or "")
            if not asset_ref:
                continue
            amount = Decimal(str(line.get("monthly_depreciation") or "0"))
            row = by_asset.setdefault(asset_ref, self._asset_row(line))
            row["depreciation"] = Decimal(str(row["depreciation"])) + amount
            policy_id = str(line.get("depreciation_policy") or "-")
            source_type = str(line.get("asset_source_type") or "-")
            by_policy[policy_id] = by_policy.get(policy_id, Decimal("0")) + amount
            by_source[source_type] = by_source.get(source_type, Decimal("0")) + amount
        top_assets = sorted(by_asset.values(), key=lambda item: Decimal(str(item["depreciation"])), reverse=True)[:5]
        for item in top_assets:
            item["depreciation"] = f"{Decimal(str(item['depreciation'])):.2f}"
        return {
            "line_count": len(lines),
            "total_depreciation": f"{total:.2f}",
            "top_asset": top_assets[0] if top_assets else None,
            "top_assets": top_assets,
            "policy_breakdown": [
                {
                    "depreciation_policy": policy_id,
                    "depreciation_policy_label_cn": policy_label(policy_id),
                    "depreciation": f"{amount:.2f}",
                }
                for policy_id, amount in sorted(by_policy.items(), key=lambda item: item[1], reverse=True)
            ],
            "source_breakdown": [
                {
                    "asset_source_type": source,
                    "asset_source_type_label_cn": ASSET_SOURCE_TYPE_LABEL_CN.get(source, source),
                    "depreciation": f"{amount:.2f}",
                }
                for source, amount in sorted(by_source.items(), key=lambda item: item[1], reverse=True)
            ],
        }

    def _period_comparison(
        self,
        *,
        scenario_id: str,
        department: str | None,
        asset_category: str | None,
        target_period: str,
        previous_period: str,
    ) -> dict[str, object]:
        previous_lines = self.tools.forecast_lines(
            scenario_id=scenario_id,
            department=department,
            asset_category=asset_category,
            period_from=previous_period,
            period_to=previous_period,
            limit=10000,
        )
        target_lines = self.tools.forecast_lines(
            scenario_id=scenario_id,
            department=department,
            asset_category=asset_category,
            period_from=target_period,
            period_to=target_period,
            limit=10000,
        )
        scope_lines = self.tools.forecast_lines(
            scenario_id=scenario_id,
            department=department,
            asset_category=asset_category,
            limit=10000,
        )
        target_is_snapshot = self._is_snapshot_period(target_lines)
        comparison_is_snapshot = self._is_snapshot_period(previous_lines)
        comparison_basis = (
            "snapshot_to_forecast"
            if target_is_snapshot != comparison_is_snapshot
            else "period_to_period"
        )
        lifecycle_by_asset = self._lifecycle_by_asset(scope_lines, target_period=target_period)
        previous_by_asset = self._amount_by_asset(previous_lines)
        target_by_asset = self._amount_by_asset(target_lines)
        drivers: list[dict[str, object]] = []
        for asset_ref in sorted(set(previous_by_asset) | set(target_by_asset)):
            previous_amount = Decimal(str(previous_by_asset.get(asset_ref, {}).get("amount", "0")))
            target_amount = Decimal(str(target_by_asset.get(asset_ref, {}).get("amount", "0")))
            difference = target_amount - previous_amount
            if difference == 0:
                continue
            source = target_by_asset.get(asset_ref) or previous_by_asset.get(asset_ref) or {}
            lifecycle = lifecycle_by_asset.get(asset_ref, {})
            driver_category = (
                "snapshot_forecast_transition"
                if comparison_basis == "snapshot_to_forecast"
                else self._driver_category(previous_amount, target_amount, difference)
            )
            driver_type = {
                "new_depreciation": "新增计提",
                "stopped_depreciation": "停止计提",
                "rounding": "四舍五入差异",
                "amount_change": "月折旧变化",
                "snapshot_forecast_transition": "台账实际与规则预测差异",
            }[driver_category]
            driver_reason = self._driver_reason(
                source=source,
                previous_amount=previous_amount,
                target_amount=target_amount,
                difference=difference,
                driver_category=driver_category,
                lifecycle=lifecycle,
                target_period=target_period,
                comparison_period=previous_period,
                comparison_basis=comparison_basis,
                target_is_snapshot=target_is_snapshot,
                comparison_is_snapshot=comparison_is_snapshot,
            )
            drivers.append(
                {
                    **source,
                    "asset_ref": asset_ref,
                    "previous_amount": f"{previous_amount:.2f}",
                    "target_amount": f"{target_amount:.2f}",
                    "difference": f"{difference:.2f}",
                    "abs_difference": f"{abs(difference):.2f}",
                    "driver_type": driver_type,
                    "driver_category": driver_category,
                    "driver_reason_cn": driver_reason,
                    "driver_text_cn": driver_reason,
                    "lifecycle": lifecycle,
                }
            )
        drivers.sort(key=lambda item: Decimal(str(item["abs_difference"])), reverse=True)
        increase_drivers = [driver for driver in drivers if Decimal(str(driver["difference"])) > 0]
        decrease_drivers = [driver for driver in drivers if Decimal(str(driver["difference"])) < 0]
        material_drivers = [
            driver for driver in drivers
            if Decimal(str(driver.get("abs_difference") or "0")) > Decimal("0.01")
        ]
        previous_total = sum(Decimal(str(line.get("monthly_depreciation") or "0")) for line in previous_lines)
        target_total = sum(Decimal(str(line.get("monthly_depreciation") or "0")) for line in target_lines)
        difference_total = target_total - previous_total
        gross_difference = sum((Decimal(str(driver["abs_difference"])) for driver in material_drivers), Decimal("0"))
        significance_threshold = max(
            SIGNIFICANT_DRIVER_MIN_AMOUNT,
            gross_difference * SIGNIFICANT_DRIVER_SHARE,
        )
        significant_drivers = [
            driver for driver in material_drivers
            if Decimal(str(driver["abs_difference"])) >= significance_threshold
        ]
        significant_gross_difference = sum(
            (Decimal(str(driver["abs_difference"])) for driver in significant_drivers), Decimal("0"),
        )
        significance_coverage_percent = (
            significant_gross_difference * Decimal("100") / gross_difference
            if gross_difference else Decimal("100")
        )
        immaterial_difference = sum(
            (Decimal(str(driver["difference"])) for driver in material_drivers if driver not in significant_drivers), Decimal("0"),
        )
        return {
            "line_count": len(previous_lines) + len(target_lines),
            "scenario_id": scenario_id,
            "department": department,
            "asset_category": asset_category,
            "asset_category_label_cn": category_label(asset_category) if asset_category else "全部资产类别",
            "previous_period": previous_period,
            "target_period": target_period,
            "comparison_basis": comparison_basis,
            "comparison_basis_label_cn": (
                "台账实际与规则预测对比" if comparison_basis == "snapshot_to_forecast" else "同口径期间对比"
            ),
            "target_data_type": "actual_snapshot" if target_is_snapshot else "forecast",
            "comparison_data_type": "actual_snapshot" if comparison_is_snapshot else "forecast",
            "previous_total": f"{previous_total:.2f}",
            "target_total": f"{target_total:.2f}",
            "difference": f"{difference_total:.2f}",
            "direction_cn": "提升" if difference_total > 0 else "下降" if difference_total < 0 else "持平",
            "drivers": significant_drivers,
            "increase_drivers": [driver for driver in significant_drivers if Decimal(str(driver["difference"])) > 0],
            "decrease_drivers": [driver for driver in significant_drivers if Decimal(str(driver["difference"])) < 0],
            "material_drivers": significant_drivers,
            "all_driver_count": len(drivers),
            "significant_driver_count": len(significant_drivers),
            "significance_threshold": f"{significance_threshold:.2f}",
            "significance_rule_cn": "单项差异绝对值不少于 1,000 元，且不少于本次绝对变动总额的 0.1%。",
            "significance_coverage_percent": f"{significance_coverage_percent:.2f}",
            "immaterial_driver_count": len(drivers) - len(significant_drivers),
            "immaterial_difference": f"{immaterial_difference:.2f}",
            "top_driver_asset": drivers[0] if drivers else None,
            "top_asset": drivers[0] if drivers else None,
            "top_assets": drivers[:5],
        }

    @staticmethod
    def _is_snapshot_period(lines: list[dict[str, object]]) -> bool:
        return bool(lines) and all(
            str(line.get("validation_status") or "") == "SOURCE_SNAPSHOT"
            or str(line.get("calculation_rule_id") or "") == "LEDGER_SNAPSHOT"
            for line in lines
        )

    @staticmethod
    def _set_actual_comparison_direction(
        question_analysis: dict[str, object],
        comparison: dict[str, object],
    ) -> None:
        if question_analysis.get("direction") != "unknown":
            return
        difference = Decimal(str(comparison.get("difference") or "0"))
        direction = "increase" if difference > 0 else "decrease" if difference < 0 else "unknown"
        question_analysis["direction"] = direction
        if direction != "unknown":
            terms = list(question_analysis.get("recognized_terms") or [])
            label = "环比提升" if direction == "increase" else "环比下降"
            if label not in terms:
                terms.append(label)
            question_analysis["recognized_terms"] = terms

    @staticmethod
    def _attach_rule_execution_evidence(
        comparison: dict[str, object],
        executions: list[dict[str, Any]],
    ) -> None:
        """Attach both-period calculation inputs to every material driver.

        The deterministic comparison identifies *which* asset changed. Rule
        executions make the business reason auditable instead of asking the LLM
        to infer it from a policy label.
        """
        by_asset_period = {
            (str(item.get("asset_ref") or ""), str(item.get("period") or "")): item
            for item in executions
        }
        previous_period = str(comparison.get("previous_period") or "")
        target_period = str(comparison.get("target_period") or "")
        for driver in comparison.get("drivers", []):
            asset_ref = str(driver.get("asset_ref") or "")
            previous_execution = by_asset_period.get((asset_ref, previous_period))
            target_execution = by_asset_period.get((asset_ref, target_period))
            evidence = [item for item in (previous_execution, target_execution) if item]
            if not evidence:
                continue
            if comparison.get("comparison_basis") == "snapshot_to_forecast":
                forecast_execution = target_execution or previous_execution
                calculation_evidence = (
                    f"{comparison.get('target_period') if target_execution else comparison.get('previous_period')} 为台账实际/规则预测切换后的规则执行证据："
                    f"{forecast_execution.get('conclusion_cn') if forecast_execution else '未找到对应规则执行记录'}"
                )
            else:
                calculation_evidence = WideTableQASkill._calculation_evidence_text(
                    previous_execution=previous_execution,
                    target_execution=target_execution,
                )
            driver["rule_execution_evidence"] = [
                {
                    "period": item["period"],
                    "branch_id": item["branch_id"],
                    "formula_cn": item["formula_cn"],
                    "inputs": item["inputs"],
                    "conclusion_cn": item["conclusion_cn"],
                }
                for item in evidence
            ]
            driver["calculation_evidence_cn"] = calculation_evidence
            if calculation_evidence:
                driver["driver_reason_cn"] = f"{driver['driver_reason_cn']}计算依据：{calculation_evidence}"
                driver["driver_text_cn"] = driver["driver_reason_cn"]

    @staticmethod
    def _calculation_evidence_text(
        *,
        previous_execution: dict[str, Any] | None,
        target_execution: dict[str, Any] | None,
    ) -> str:
        target = target_execution or {}
        previous = previous_execution or {}
        previous_inputs = previous.get("inputs") or {}
        target_inputs = target.get("inputs") or {}
        target_branch = str(target.get("branch_id") or "")

        if target_branch == "LIFE_EXPIRED":
            life = target_inputs.get("使用年限(月)") or "-"
            return f"年限平均法规则在目标月命中“折旧到期”分支，使用年限为 {life} 个月，因此目标月不再计提。"

        if target_branch == "CONFIGURED_DEPLETION_RATE":
            changes = []
            for label in ("区块", "当月产量", "剩余储量", "折耗率"):
                before = previous_inputs.get(label)
                after = target_inputs.get(label)
                if before is not None and after is not None:
                    if before != after:
                        changes.append(f"{label}由 {before} 变为 {after}")
                    elif label == "区块":
                        changes.append(f"区块为 {after}")
            changed_text = "，".join(changes) or "区块配置参数按目标月取值"
            return (
                f"产量法按“期初净值 × 区块配置折耗率”计算；{changed_text}。"
                "折耗率直接读取区块配置表。"
            )

        if target_branch == "WORKLOAD_ALLOCATION":
            total = target_inputs.get("当月总摊销额") or "-"
            pool = target_inputs.get("资产池期初净额") or "-"
            return (
                "工作量法按“当月总摊销额 × 资产期初净值 ÷ 资产池期初净额”分摊；"
                f"配置表当月总摊销额为 {total}，资产池期初净额为 {pool}。"
            )

        if target_branch == "STRAIGHT_LINE":
            remaining = target_inputs.get("剩余折旧月数") or "-"
            return f"年限平均法按剩余可折旧金额在剩余 {remaining} 个月内计提。"

        target_conclusion = str(target.get("conclusion_cn") or "")
        if target_conclusion:
            return f"目标月命中 {target_branch or '计算规则'} 分支：{target_conclusion}"
        return "已读取对应期间的规则执行记录。"

    def _lifecycle_by_asset(self, lines: list[dict[str, object]], *, target_period: str) -> dict[str, dict[str, object]]:
        periods_by_asset: dict[str, list[str]] = {}
        target_month = Month.parse(target_period)
        for line in lines:
            asset_ref = str(line.get("asset_id") or line.get("planned_asset_id") or "")
            if not asset_ref:
                continue
            if Decimal(str(line.get("monthly_depreciation") or "0")) > 0:
                periods_by_asset.setdefault(asset_ref, []).append(str(line.get("period")))
        output: dict[str, dict[str, object]] = {}
        for asset_ref, periods in periods_by_asset.items():
            ordered = sorted(set(periods))
            last_period = ordered[-1] if ordered else None
            stopped_at_target = False
            if last_period:
                stopped_at_target = Month.parse(last_period).add(1) == target_month
            output[asset_ref] = {
                "first_positive_period": ordered[0] if ordered else None,
                "last_positive_period": last_period,
                "positive_month_count": len(ordered),
                "target_period": target_period,
                "stopped_at_target_period": stopped_at_target,
            }
        return output

    @staticmethod
    def _driver_category(previous_amount: Decimal, target_amount: Decimal, difference: Decimal) -> str:
        if abs(difference) <= Decimal("0.01"):
            return "rounding"
        if previous_amount == 0 and target_amount > 0:
            return "new_depreciation"
        if previous_amount > 0 and target_amount == 0:
            return "stopped_depreciation"
        return "amount_change"

    @staticmethod
    def _driver_reason(
        *,
        source: dict[str, object],
        previous_amount: Decimal,
        target_amount: Decimal,
        difference: Decimal,
        driver_category: str,
        lifecycle: dict[str, object],
        target_period: str,
        comparison_period: str,
        comparison_basis: str,
        target_is_snapshot: bool,
        comparison_is_snapshot: bool,
    ) -> str:
        asset_ref = source.get("asset_ref", "-")
        category = source.get("asset_category_label_cn") or category_label(source.get("asset_category"))
        policy = source.get("depreciation_policy_label_cn") or policy_label(source.get("depreciation_policy"))
        code = source.get("depreciation_code_label_cn") or depreciation_code_label(source.get("depreciation_code"))
        if comparison_basis == "snapshot_to_forecast":
            target_label = "台账实际" if target_is_snapshot else "规则预测"
            comparison_label = "台账实际" if comparison_is_snapshot else "规则预测"
            return (
                f"{asset_ref} 在 {target_period} 的{target_label}折旧为 {target_amount:.2f}，"
                f"{comparison_period} 的{comparison_label}折旧为 {previous_amount:.2f}，"
                f"差异为 {difference:.2f}。该资产使用{code}，适用{policy}。"
                "该差异来自台账快照切换到后续期间规则预测，不应解读为资产在快照月停止计提。"
            )
        if driver_category == "new_depreciation":
            return (
                f"{asset_ref} 在目标月开始产生折旧，月折旧从 0.00 增至 {target_amount:.2f}，"
                f"贡献变化 {difference:.2f}。该资产类别为{category}，使用{code}，适用{policy}。"
            )
        if driver_category == "stopped_depreciation":
            lifecycle_text = ""
            if lifecycle:
                lifecycle_text = (
                    f"预测明细显示首次计提月为 {lifecycle.get('first_positive_period')}，"
                    f"最后计提月为 {lifecycle.get('last_positive_period')}，"
                    f"共计提 {lifecycle.get('positive_month_count')} 个月。"
                )
            return (
                f"{asset_ref} 在 {target_period} 停止计提，月折旧从 {previous_amount:.2f} 降为 0.00，"
                f"贡献变化 {difference:.2f}。该资产类别为{category}，使用{code}，适用{policy}。"
                f"{lifecycle_text}因此该变化属于折旧到期/停止计提。"
            )
        if driver_category == "rounding":
            return (
                f"{asset_ref} 月折旧从 {previous_amount:.2f} 变为 {target_amount:.2f}，"
                f"变化 {difference:.2f}，属于四舍五入或尾差级别影响。"
            )
        return (
            f"{asset_ref} 月折旧从 {previous_amount:.2f} 变为 {target_amount:.2f}，"
            f"变化 {difference:.2f}。该资产类别为{category}，使用{code}，适用{policy}。"
        )

    def _amount_by_asset(self, lines: list[dict[str, object]]) -> dict[str, dict[str, object]]:
        output: dict[str, dict[str, object]] = {}
        for line in lines:
            asset_ref = str(line.get("asset_id") or line.get("planned_asset_id") or "")
            if not asset_ref:
                continue
            amount = Decimal(str(line.get("monthly_depreciation") or "0"))
            row = output.setdefault(asset_ref, self._asset_row(line))
            row["amount"] = Decimal(str(row["amount"])) + amount
            row["addition_amount"] = Decimal(str(row["addition_amount"])) + Decimal(str(line.get("addition_amount") or "0"))
            row["disposal_amount"] = Decimal(str(row["disposal_amount"])) + Decimal(str(line.get("disposal_amount") or "0"))
            row["impairment_amount"] = Decimal(str(row["impairment_amount"])) + Decimal(str(line.get("impairment_amount") or "0"))
        return output

    @staticmethod
    def _asset_row(line: dict[str, object]) -> dict[str, object]:
        asset_ref = str(line.get("asset_id") or line.get("planned_asset_id") or "")
        return {
            "asset_ref": asset_ref,
            "asset_source_type": line.get("asset_source_type"),
            "department": line.get("department"),
            "asset_category": line.get("asset_category"),
            "asset_category_label_cn": category_label(line.get("asset_category")),
            "depreciation_code": line.get("depreciation_code"),
            "depreciation_code_label_cn": depreciation_code_label(line.get("depreciation_code")),
            "depreciation_policy": line.get("depreciation_policy"),
            "depreciation_policy_label_cn": policy_label(line.get("depreciation_policy")),
            "source_event_id": line.get("source_event_id"),
            "addition_amount": Decimal("0"),
            "disposal_amount": Decimal("0"),
            "impairment_amount": Decimal("0"),
            "amount": Decimal("0"),
            "depreciation": Decimal("0"),
        }

    @staticmethod
    def _driver_text(source: dict[str, object], previous_amount: Decimal, target_amount: Decimal, difference: Decimal) -> str:
        asset_ref = source.get("asset_ref", "-")
        category = source.get("asset_category_label_cn") or category_label(source.get("asset_category"))
        policy = source.get("depreciation_policy_label_cn") or policy_label(source.get("depreciation_policy"))
        code = source.get("depreciation_code_label_cn") or depreciation_code_label(source.get("depreciation_code"))
        if previous_amount == 0 and target_amount > 0:
            return (
                f"{asset_ref} 在目标月开始产生折旧，月折旧从 0.00 增至 {target_amount:.2f}，"
                f"贡献变化 {difference:.2f}。该资产类别为{category}，使用{code}，适用{policy}。"
            )
        return (
            f"{asset_ref} 月折旧从 {previous_amount:.2f} 变为 {target_amount:.2f}，"
            f"变化 {difference:.2f}。该资产类别为{category}，使用{code}，适用{policy}。"
        )

    def _graph_reasoning(self, *, scenario_id: str, top_asset: dict[str, object] | None) -> dict[str, object] | None:
        if not top_asset:
            return None
        asset_ref = str(top_asset.get("asset_ref") or "")
        source_type = "PlannedAsset" if top_asset.get("asset_source_type") == "PLANNED" else "FixedAsset"
        policy_id = str(top_asset.get("depreciation_policy") or "")
        path = None
        if policy_id:
            path = self.tools.knowledge_graph_path(
                {
                    "from": [object_id(source_type, asset_ref)],
                    "to": [object_id("DepreciationPolicy", policy_id)],
                    "scenario_id": [scenario_id],
                }
            )
        narrative = self.tools.policy_narrative(asset_ref, scenario_id) if asset_ref else None
        return {
            "asset_ref": asset_ref,
            "asset_object_id": object_id(source_type, asset_ref),
            "policy_object_id": object_id("DepreciationPolicy", policy_id) if policy_id else None,
            "path": path,
            "policy_narrative": narrative,
        }

    def _graph_reasoning_for_drivers(self, *, scenario_id: str, drivers: list[dict[str, object]]) -> dict[str, object] | None:
        material_drivers = [
            driver for driver in drivers
            if Decimal(str(driver.get("abs_difference") or "0")) > Decimal("0.01")
        ] or drivers[:1]
        # The detailed table retains every material asset. For ontology paths,
        # one representative per calculation grouping proves the applicable
        # business rule without issuing a duplicate graph query for each asset.
        graph_drivers: list[dict[str, object]] = []
        seen_groups: set[tuple[str, str, str]] = set()
        for driver in material_drivers:
            evidence = list(driver.get("rule_execution_evidence") or [])
            target_branch = str(evidence[-1].get("branch_id") if evidence else "")
            key = (
                str(driver.get("driver_category") or ""),
                str(driver.get("depreciation_code") or ""),
                target_branch,
            )
            if key not in seen_groups:
                seen_groups.add(key)
                graph_drivers.append(driver)
        primary = self._graph_reasoning(
            scenario_id=scenario_id,
            top_asset=graph_drivers[0] if graph_drivers else None,
        )
        if primary is None:
            return None
        driver_paths = []
        for driver in graph_drivers:
            reasoning = self._graph_reasoning(scenario_id=scenario_id, top_asset=driver)
            if reasoning is not None:
                driver_paths.append(
                    {
                        "asset_ref": reasoning.get("asset_ref"),
                        "depreciation_policy_label_cn": driver.get("depreciation_policy_label_cn"),
                        "difference": driver.get("difference"),
                        "driver_category": driver.get("driver_category"),
                        "driver_reason_cn": driver.get("driver_reason_cn"),
                        "lifecycle": driver.get("lifecycle"),
                        "driver_text_cn": driver.get("driver_text_cn"),
                        "path": reasoning.get("path"),
                        "policy_narrative": reasoning.get("policy_narrative"),
                    }
                )
        return {
            **primary,
            "driver_paths": driver_paths,
        }

    @staticmethod
    def _graph_trace(graph_reasoning: dict[str, object] | None) -> dict[str, Any]:
        path = (graph_reasoning or {}).get("path") or {}
        driver_paths = (graph_reasoning or {}).get("driver_paths") or []
        return {
            "tool_name": "traceKnowledgeGraphPath",
            "label_cn": "追溯知识图谱路径",
            "read_only": True,
            "result_shape": {
                "asset_ref": (graph_reasoning or {}).get("asset_ref"),
                "path_edges": len(path.get("path_edges") or []),
                "driver_path_count": len(driver_paths),
            },
        }

    def _scope_steps(
        self,
        *,
        question: str,
        scenario_id: str,
        department: str | None,
        asset_category: str | None,
        period_from: str | None,
        period_to: str | None,
        facts: dict[str, object],
        graph_reasoning: dict[str, object] | None,
    ) -> list[dict[str, object]]:
        scope_text = "、".join(
            item for item in [
                f"场景 {scenario_id}",
                f"部门 {department}" if department else "",
                f"类别 {category_label(asset_category)}" if asset_category else "",
                f"期间 {period_from} 至 {period_to}" if period_from or period_to else "",
            ] if item
        )
        top_asset = facts.get("top_asset") or {}
        path = (graph_reasoning or {}).get("path") or {}
        policy = ((graph_reasoning or {}).get("policy_narrative") or {}).get("applicable_policy") or {}
        return [
            {"step": 1, "title_cn": "理解问题和筛选范围", "detail_cn": f"问题是“{question}”，当前宽表筛选范围为：{scope_text}。"},
            {"step": 2, "title_cn": "读取折旧宽表底层明细", "detail_cn": f"从业务结果库读取 {facts.get('line_count', 0)} 条预测明细，合计折旧 {facts.get('total_depreciation', '0.00')}。"},
            {"step": 3, "title_cn": "定位主要贡献对象", "detail_cn": f"贡献最高的是 {top_asset.get('asset_ref', '无')}，折旧 {top_asset.get('depreciation', '0.00')}，类别为 {top_asset.get('asset_category_label_cn', '-')}。"},
            {"step": 4, "title_cn": "知识图谱追溯政策", "detail_cn": path.get("narrative_cn") or "没有找到资产到政策的完整图谱路径。"},
            {"step": 5, "title_cn": "解释折旧原因", "detail_cn": f"适用政策为 {policy.get('policy_label_cn') or top_asset.get('depreciation_policy_label_cn', '-')}，规则为 {policy.get('method_label_cn', '-')} / {policy.get('useful_life_months', '-')} 个月 / {policy.get('residual_rate_label_cn', '-')} / {policy.get('start_rule_label_cn', '-')}。"},
        ]

    def _change_steps(
        self,
        *,
        question: str,
        scenario_id: str,
        department: str | None,
        question_analysis: dict[str, object],
        comparison: dict[str, object],
        graph_reasoning: dict[str, object] | None,
    ) -> list[dict[str, object]]:
        category = comparison.get("asset_category_label_cn") or "当前范围"
        previous_period = comparison.get("previous_period")
        target_period = comparison.get("target_period")
        top_driver = comparison.get("top_driver_asset") or {}
        path = (graph_reasoning or {}).get("path") or {}
        policy = ((graph_reasoning or {}).get("policy_narrative") or {}).get("applicable_policy") or {}
        material_drivers = self._material_drivers(comparison)
        driver_summary = self._driver_summary_text(material_drivers)
        graph_summary = self._driver_graph_summary_text(graph_reasoning)
        policy_summary = self._driver_policy_summary_text(graph_reasoning, top_driver, policy)
        recognized = "、".join(str(item) for item in question_analysis.get("recognized_terms", []) if item) or "未识别到明确业务对象"
        scope_items = [
            f"场景 {scenario_id}",
            f"部门 {department}" if department else "",
            f"资产类别 {category}",
            f"对比 {previous_period} 和 {target_period}",
        ]
        return [
            {
                "step": 1,
                "title_cn": "识别问题意图",
                "detail_cn": f"问题是“{question}”。系统识别为“{question_analysis.get('intent_label_cn')}”，识别出的关键词为：{recognized}。",
            },
            {
                "step": 2,
                "title_cn": "锁定宽表范围",
                "detail_cn": f"本次只读取 {'、'.join(item for item in scope_items if item)} 的业务库预测明细，不使用前端硬编码结果。",
            },
            {
                "step": 3,
                "title_cn": "执行期间对比",
                "detail_cn": (
                    f"本次采用“{comparison.get('comparison_basis_label_cn', '同口径期间对比')}”口径。"
                    f"{category} 在 {previous_period} 的折旧为 {comparison.get('previous_total')}，"
                    f"{target_period} 的折旧为 {comparison.get('target_total')}，"
                    f"差异{comparison.get('direction_cn')} {comparison.get('difference')}。"
                ),
            },
            {"step": 4, "title_cn": "定位变化驱动资产", "detail_cn": driver_summary or top_driver.get("driver_text_cn") or "没有发现产生差异的资产。"},
            {"step": 5, "title_cn": "知识图谱追溯政策", "detail_cn": graph_summary or path.get("narrative_cn") or "没有找到该资产到折旧政策的完整图谱路径。"},
            {
                "step": 6,
                "title_cn": "解释计算规则",
                "detail_cn": policy_summary,
            },
        ]

    @staticmethod
    def _material_drivers(comparison: dict[str, object]) -> list[dict[str, object]]:
        if comparison.get("material_drivers"):
            return list(comparison.get("material_drivers", []))
        return [
            driver for driver in comparison.get("drivers", [])
            if Decimal(str(driver.get("abs_difference") or "0")) > Decimal("0.01")
        ]

    @staticmethod
    def _driver_summary_text(drivers: list[dict[str, object]]) -> str:
        if not drivers:
            return ""
        groups: dict[tuple[str, str, str], list[dict[str, object]]] = {}
        for driver in drivers:
            evidence = list(driver.get("rule_execution_evidence") or [])
            target_branch = str(evidence[-1].get("branch_id") if evidence else "")
            key = (
                str(driver.get("driver_type") or "月折旧变化"),
                str(driver.get("depreciation_code_label_cn") or driver.get("depreciation_code") or "折旧规则"),
                target_branch,
            )
            groups.setdefault(key, []).append(driver)

        parts = []
        for (driver_type, code_label, _branch), group in groups.items():
            difference = sum((Decimal(str(item.get("difference") or "0")) for item in group), Decimal("0"))
            asset_list = "、".join(
                f"{item.get('asset_ref')}（{item.get('difference')}）" for item in group
            )
            evidence = str(group[0].get("calculation_evidence_cn") or "")
            parts.append(
                f"{driver_type} / {code_label}（{len(group)} 项，合计 {difference:.2f}）："
                f"{asset_list}。{evidence}"
            )
        return _sentence_join(parts)

    @staticmethod
    def _driver_graph_summary_text(graph_reasoning: dict[str, object] | None) -> str:
        driver_paths = (graph_reasoning or {}).get("driver_paths") or []
        parts = []
        for item in driver_paths:
            path = item.get("path") or {}
            if path.get("narrative_cn"):
                parts.append(f"{item.get('asset_ref')}：{path.get('narrative_cn')}")
        return _sentence_join(parts)

    @staticmethod
    def _driver_policy_summary_text(
        graph_reasoning: dict[str, object] | None,
        top_driver: dict[str, object],
        fallback_policy: dict[str, object],
    ) -> str:
        driver_paths = (graph_reasoning or {}).get("driver_paths") or []
        parts = []
        for item in driver_paths:
            policy = (item.get("policy_narrative") or {}).get("applicable_policy") or {}
            if policy:
                lifecycle = item.get("lifecycle") or {}
                lifecycle_text = ""
                if lifecycle.get("stopped_at_target_period"):
                    lifecycle_text = (
                        f"，预测明细显示 {lifecycle.get('first_positive_period')} 首次计提、"
                        f"{lifecycle.get('last_positive_period')} 最后计提，"
                        f"共 {lifecycle.get('positive_month_count')} 个月，"
                        f"{lifecycle.get('target_period')} 停止计提"
                    )
                parts.append(
                    f"{item.get('asset_ref')} 适用 {policy.get('policy_label_cn')}，"
                    f"规则为 {policy.get('method_label_cn')} / {policy.get('useful_life_months')} 个月 / "
                    f"{policy.get('residual_rate_label_cn')} / {policy.get('start_rule_label_cn')}{lifecycle_text}"
                )
            else:
                parts.append(f"{item.get('asset_ref')} 适用 {item.get('depreciation_policy_label_cn')}")
        if parts:
            return "；".join(parts) + "。"
        return (
            f"图谱匹配到的政策为 {fallback_policy.get('policy_label_cn') or top_driver.get('depreciation_policy_label_cn', '-')}，"
            f"规则为 {fallback_policy.get('method_label_cn', '-')} / {fallback_policy.get('useful_life_months', '-')} 个月 / "
            f"{fallback_policy.get('residual_rate_label_cn', '-')} / {fallback_policy.get('start_rule_label_cn', '-')}。"
        )

    @staticmethod
    def _validated_generation(
        generation: dict[str, Any],
        context: dict[str, Any],
        comparison: dict[str, object],
    ) -> dict[str, Any]:
        answer = str(generation.get("answer_cn") or "")
        material_drivers = WideTableQASkill._material_drivers(comparison)
        expected = _as_text_list(context.get("required_answer_asset_refs"))
        if not expected:
            expected = [
                asset_ref
                for asset_ref in WideTableQASkill._required_answer_refs(material_drivers)
                if asset_ref
            ]
        missing = [
            asset_ref for asset_ref in expected if asset_ref not in answer
        ]
        invalid_phrases = ("无法判断", "无法精确判断", "缺少月度对比", "未提供月度对比", "没有月度对比")
        must_replace = any(phrase in answer for phrase in invalid_phrases) and material_drivers
        if must_replace:
            generation = {**generation, "answer_cn": str(context.get("template_answer_cn") or answer)}
            answer = str(generation["answer_cn"])
            missing = [
                asset_ref for asset_ref in expected if asset_ref not in answer
            ]
        if missing:
            generation = {
                **generation,
                "answer_cn": str(context.get("template_answer_cn") or answer),
                "evidence_complete_template_used": True,
            }
        return generation

    @staticmethod
    def _scope_answer(facts: dict[str, object], graph_reasoning: dict[str, object] | None) -> str:
        top_asset = facts.get("top_asset") or {}
        narrative = ((graph_reasoning or {}).get("policy_narrative") or {}).get("narrative_cn")
        return (
            f"当前宽表范围内折旧合计为 {facts.get('total_depreciation', '0.00')}。"
            f"主要原因是 {top_asset.get('asset_ref', '无')} 贡献最高，金额为 {top_asset.get('depreciation', '0.00')}。"
            f"该对象登记为 {top_asset.get('asset_category_label_cn', '-')}，使用 {top_asset.get('depreciation_code_label_cn', '-')}，"
            f"并匹配 {top_asset.get('depreciation_policy_label_cn', '-')}。"
            f"{narrative or ''}"
        )

    @staticmethod
    def _change_answer(comparison: dict[str, object], graph_reasoning: dict[str, object] | None) -> str:
        material_drivers = WideTableQASkill._material_drivers(comparison)
        required_refs = set(WideTableQASkill._required_answer_refs(material_drivers))
        key_drivers = [item for item in material_drivers if str(item.get("asset_ref")) in required_refs]
        key_summary = WideTableQASkill._driver_summary_text(key_drivers)
        scope_label = comparison.get("department") or comparison.get("asset_category_label_cn") or "当前范围"
        basis = comparison.get("comparison_basis_label_cn", "同口径期间对比")
        basis_note = (
            "这不是同口径的两个月预测，而是台账实际快照与规则预测的切换对比；差异应按资产的实际折旧额和后续规则输入逐项复核，不代表快照月资产停止计提。"
            if comparison.get("comparison_basis") == "snapshot_to_forecast" else ""
        )
        return (
            f"{scope_label} 在 {comparison.get('target_period')} 相对 {comparison.get('previous_period')} 出现{comparison.get('direction_cn')}，"
            f"本次口径为{basis}。"
            f"原因来自当前问题范围内资产的环比变化："
            f"{comparison.get('previous_period')} 为 {comparison.get('previous_total')}，"
            f"{comparison.get('target_period')} 为 {comparison.get('target_total')}，"
            f"差异为 {comparison.get('difference')}。"
            f"按“{comparison.get('significance_rule_cn', '显著差异口径')}”识别出 "
            f"{comparison.get('significant_driver_count', len(material_drivers))} 项显著差异资产，"
            f"覆盖绝对差异的 {comparison.get('significance_coverage_percent', '100.00')}%；"
            f"核心归因资产为：{key_summary}"
            f"全部显著资产及规则证据已在下方“差异驱动资产”表中完整列示。"
            f"{basis_note}"
        )

    @staticmethod
    def _provider_context(
        *,
        question: str,
        question_analysis: dict[str, object],
        facts: dict[str, object],
        graph_reasoning: dict[str, object] | None,
        reasoning_steps: list[dict[str, object]],
        trace: list[dict[str, Any]],
        template_answer: str,
        rule_execution_trace: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "task": "wide_table_finance_qa",
            "question": question,
            "question_analysis": question_analysis,
            "facts": facts,
            "decrease_drivers": facts.get("decrease_drivers", []) if isinstance(facts, dict) else [],
            "increase_drivers": facts.get("increase_drivers", []) if isinstance(facts, dict) else [],
            "asset_lifecycle": {
                str(driver.get("asset_ref")): driver.get("lifecycle")
                for driver in facts.get("material_drivers", [])
                if isinstance(facts, dict) and driver.get("asset_ref")
            } if isinstance(facts, dict) else {},
            "graph_reasoning": graph_reasoning,
            "driver_paths": (graph_reasoning or {}).get("driver_paths", []),
            "reasoning_steps": reasoning_steps,
            "rule_execution_trace": rule_execution_trace or [],
            "tool_trace": trace,
            "template_answer_cn": template_answer,
            "guardrails": [
                "只读工具取数，不修改业务库或图数据库。",
                "LLM 只解释结构化事实，不重新计算金额。",
                "若 LLM 不可用，返回会明确标注模板降级。",
            ],
        }

    def _skill_metadata(self, generation: dict[str, Any], trace: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "provider": generation.get("provider"),
            "used_llm": bool(generation.get("used_llm")),
            "model": generation.get("model"),
            "fallback_reason": generation.get("fallback_reason"),
            "tool_trace": trace,
        }


def _extract_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return {"answer_cn": text}


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _as_text_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, Month):
        return str(value)
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _sentence_join(parts: list[str]) -> str:
    text = "；".join(part.rstrip("。") for part in parts if part)
    return f"{text}。" if text else ""
