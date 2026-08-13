from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from depreciation_poc.domain.models import Anomaly, ForecastLine, SummaryLine

HARNESS_MODE = "controlled_tool_harness"
GUARDRAIL_NOTES = [
    "Harness 只调用注册过的只读工具。",
    "LLM/模板只解释工具返回的结构化事实，不重新计算金额。",
    "前端和 Provider 不接触 API key、CSV、SQLite 连接或图数据库连接。",
]
PROVIDER_CONTEXT_KEYS = {
    "scope",
    "dashboard",
    "drivers",
    "contributors",
    "anomalies",
    "top_assets",
    "policy_context",
    "available_actions",
    "available_functions",
    "tool_trace",
    "guardrails",
}


class ExplanationProvider(Protocol):
    provider_name: str

    def explain(self, context: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class HarnessTool:
    name: str
    label_cn: str
    description_cn: str
    read_only: bool
    runner: Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class ToolInvocation:
    tool_name: str
    label_cn: str
    arguments: dict[str, Any]
    result_key: str


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, HarnessTool] = {}

    def register(self, tool: HarnessTool) -> None:
        self._tools[tool.name] = tool

    def register_many(self, tools: list[HarnessTool]) -> None:
        for tool in tools:
            self.register(tool)

    def call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ValueError(f"Harness tool is not registered: {tool_name}")
        if not tool.read_only:
            raise ValueError(f"Harness explanation cannot call mutating tool: {tool_name}")
        return tool.runner(arguments)

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "label_cn": tool.label_cn,
                "description_cn": tool.description_cn,
                "read_only": tool.read_only,
            }
            for tool in sorted(self._tools.values(), key=lambda item: item.name)
        ]


class HarnessGuardrails:
    """Keeps the explanation path read-only and fact-bound."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def validate_plan(self, plan: list[ToolInvocation]) -> list[str]:
        catalog = {item["name"]: item for item in self.registry.catalog()}
        for step in plan:
            if step.tool_name not in catalog:
                raise ValueError(f"Harness plan references unknown tool: {step.tool_name}")
            if not catalog[step.tool_name]["read_only"]:
                raise ValueError(f"Harness plan references mutating tool: {step.tool_name}")
        return list(GUARDRAIL_NOTES)

    def provider_context(self, context: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in context.items() if key in PROVIDER_CONTEXT_KEYS}


class ControlledExplanationHarness:
    """Action-aware, tool-bound harness for business explanations."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self.guardrails = HarnessGuardrails(registry)

    def explain(
        self,
        *,
        scenario_id: str,
        department: str | None,
        year: int | None,
        style: str,
        provider: ExplanationProvider,
    ) -> dict[str, Any]:
        plan = self._plan(
            scenario_id=scenario_id,
            department=department,
            year=year,
            style=style,
        )
        guardrail_notes = self.guardrails.validate_plan(plan)
        facts, trace = self._execute_plan(plan)
        context = self._compose_context(
            scenario_id=scenario_id,
            department=department,
            year=year,
            style=style,
            facts=facts,
            trace=trace,
            guardrail_notes=guardrail_notes,
        )
        provider_context = self.guardrails.provider_context(context)
        explanation = provider.explain(provider_context)
        return {
            "context": context,
            "provider_context": provider_context,
            "explanation": explanation,
            "harness": {
                "mode": HARNESS_MODE,
                "tools": self.registry.catalog(),
                "tool_trace": trace,
                "guardrails": guardrail_notes,
            },
        }

    def _plan(
        self,
        *,
        scenario_id: str,
        department: str | None,
        year: int | None,
        style: str,
    ) -> list[ToolInvocation]:
        scope = {
            "scenario_id": scenario_id,
            "department": department,
            "year": year,
            "style": style,
        }
        return [
            ToolInvocation("queryDashboard", "读取预算总览", {"scenario_id": scenario_id}, "dashboard"),
            ToolInvocation("explainChange", "读取变化解释事实", scope, "change"),
            ToolInvocation("listAnomalies", "读取异常清单", {"scenario_id": scenario_id}, "anomalies"),
            ToolInvocation("listAssetCards", "读取资产卡片", {"scenario_id": scenario_id}, "asset_cards"),
            ToolInvocation("ontologyMeta", "读取 ontology 动作/函数", {}, "ontology_meta"),
        ]

    def _execute_plan(self, plan: list[ToolInvocation]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        facts: dict[str, Any] = {}
        trace: list[dict[str, Any]] = []
        for step in plan:
            result = self.registry.call(step.tool_name, step.arguments)
            facts[step.result_key] = result
            trace.append(
                {
                    "tool_name": step.tool_name,
                    "label_cn": step.label_cn,
                    "arguments": step.arguments,
                    "result_key": step.result_key,
                    "result_shape": self._shape(result),
                }
            )
        return facts, trace

    def _compose_context(
        self,
        *,
        scenario_id: str,
        department: str | None,
        year: int | None,
        style: str,
        facts: dict[str, Any],
        trace: list[dict[str, Any]],
        guardrail_notes: list[str],
    ) -> dict[str, Any]:
        change = facts.get("change") or {}
        dashboard = facts.get("dashboard") or {}
        asset_cards = facts.get("asset_cards") or []
        ontology_meta = facts.get("ontology_meta") or {}
        contributors = change.get("contributors", [])
        policy_context = self._policy_context_for_top_asset(contributors, asset_cards)
        return {
            "scope": {
                "scenario_id": scenario_id,
                "department": department,
                "year": year,
                "style": style,
            },
            "dashboard": dashboard,
            "drivers": change.get("drivers", []),
            "contributors": contributors,
            "anomalies": facts.get("anomalies") or [],
            "top_assets": asset_cards[:8],
            "policy_context": policy_context,
            "available_actions": ontology_meta.get("action_types", []),
            "available_functions": ontology_meta.get("function_types", []),
            "tool_trace": trace,
            "guardrails": guardrail_notes,
        }

    @staticmethod
    def _policy_context_for_top_asset(
        contributors: list[dict[str, Any]],
        asset_cards: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not contributors:
            return None
        top_ref = str(contributors[0].get("asset_ref") or "")
        for card in asset_cards:
            if str(card.get("asset_ref") or "") == top_ref:
                return {
                    "asset_ref": top_ref,
                    "asset_category": card.get("asset_category"),
                    "asset_category_label_cn": card.get("asset_category_label_cn"),
                    "depreciation_policy": card.get("depreciation_policy"),
                    "depreciation_policy_label_cn": card.get("depreciation_policy_label_cn"),
                    "depreciation_code": card.get("depreciation_code"),
                    "depreciation_code_label_cn": card.get("depreciation_code_label_cn"),
                    "first_depreciation_period": card.get("first_depreciation_period"),
                    "forecast_depreciation_total": card.get("forecast_depreciation_total"),
                }
        return {"asset_ref": top_ref}

    @staticmethod
    def _shape(value: Any) -> dict[str, Any]:
        if isinstance(value, list):
            return {"type": "list", "count": len(value)}
        if isinstance(value, dict):
            return {"type": "dict", "keys": sorted(value.keys())[:12]}
        return {"type": type(value).__name__}


class ExplanationHarness:
    """A deterministic stand-in for the future LLM harness tool layer."""

    def explain_variance(
        self,
        *,
        question: str,
        summaries: list[SummaryLine],
        anomalies: list[Anomaly],
    ) -> str:
        total = sum(line.monthly_depreciation_sum for line in summaries)
        by_event = Counter()
        for line in summaries:
            by_event[line.event_type] += line.monthly_depreciation_sum
        top_event = by_event.most_common(1)[0][0] if by_event else "BASE"
        return (
            f"Question: {question}\n"
            f"Total forecast depreciation in selected summaries is {total:.2f}. "
            f"The largest visible driver is {top_event}. "
            f"There are {len(anomalies)} validation anomalies that should be reviewed before using the result for budget submission."
        )

    def explain_asset_policy(self, line: ForecastLine) -> str:
        asset_ref = line.asset_id or line.planned_asset_id
        return (
            f"{asset_ref} uses policy {line.depreciation_policy}, method {line.depreciation_method}, "
            f"and generated {line.monthly_depreciation:.2f} depreciation for {line.period}."
        )
