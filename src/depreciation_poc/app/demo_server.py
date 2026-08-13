from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import re
from dataclasses import fields, is_dataclass, replace
from decimal import Decimal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from depreciation_poc.aggregation.service import DepreciationAggregator
from depreciation_poc.attribution.service import AttributionService
from depreciation_poc.calculation.engine import DepreciationCalculationEngine
from depreciation_poc.domain.models import AssetEvent, FixedAsset, Month, MonthlyDriver, PlannedAsset, WhatIfChange, parse_date
from depreciation_poc.explanation.provider import FallbackExplanationProvider
from depreciation_poc.harness.service import ControlledExplanationHarness, HarnessTool, ToolRegistry
from depreciation_poc.infrastructure.business_store import BusinessResultStore
from depreciation_poc.infrastructure.customer_excel_repository import CustomerExcelRepository
from depreciation_poc.infrastructure.env_loader import load_local_env
from depreciation_poc.infrastructure.graph_store import SQLiteGraphStore
from depreciation_poc.ontology_model import (
    ACTION_TYPES,
    FUNCTION_TYPES,
    LINK_TYPES,
    OBJECT_TYPES,
    LinkInstance,
    ObjectInstance,
    code_object_label,
    default_actions_for,
    object_id,
    object_type_label,
)
from depreciation_poc.ontology.graph_semantic_model import GraphBackedSemanticModel
from depreciation_poc.policy.resolver import PolicyResolver
from depreciation_poc.qa.skill import FallbackWideTableQAProvider, WideTableQASkill, WideTableQATools
from depreciation_poc.qa.reverse_planning import ReversePlanningSkill, ReversePlanningTools
from depreciation_poc.semantic_labels import (
    ASSET_SOURCE_TYPE_LABEL_CN,
    CATEGORY_LABEL_CN,
    EVENT_LABEL_CN,
    GRAPH_PREDICATE_LABEL_CN,
    OBJECT_TYPE_LABEL_CN,
    category_label,
    calculation_rule_label,
    depreciation_code_label,
    graph_node_label,
    local_graph_id,
    method_label,
    percent_label,
    policy_label,
    semantic_catalog,
    start_rule_label,
)
from depreciation_poc.validation.rules import DepreciationValidator


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CUSTOMER_DATA_DIR = ROOT / "data" / "customer_snapshot"
DEFAULT_GRAPH_DB = ROOT / "tmp" / "customer_graph.sqlite"
DEFAULT_BUSINESS_DB = ROOT / "tmp" / "customer_business.sqlite"
DEFAULT_WEB_DIR = ROOT / "web"

load_local_env(ROOT / ".env")


def configure_audit_logging(log_dir: Path) -> Path:
    """Persist compact reverse-planning audit events without logging model prompts or credentials."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "depreciation_poc.log"
    logger = logging.getLogger("depreciation_poc")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(
        isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename).resolve() == log_path.resolve()
        for handler in logger.handlers
    ):
        handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    return log_path


def to_jsonable(value):
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, Month):
        return str(value)
    if is_dataclass(value):
        return {
            field.name: to_jsonable(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    return value


class DemoState:
    def __init__(
        self,
        *,
        graph_db_path: Path,
        business_db_path: Path,
        start_period: Month,
        months: int,
        customer_data_dir: Path,
    ) -> None:
        self.graph_db_path = graph_db_path
        self.business_db_path = business_db_path
        self.start_period = start_period
        self.months = months
        self.customer_data_dir = customer_data_dir
        self.is_customer_data = True
        self.repository = CustomerExcelRepository(customer_data_dir)
        self.graph_store = SQLiteGraphStore(graph_db_path)
        self.business_store = BusinessResultStore(business_db_path)
        self.perspective = "BUDGET"
        snapshot_period = self.repository.source_summary().get("snapshot_period")
        self.budget_version = f"CUSTOMER-{snapshot_period}"
        self.snapshot_id = f"CUSTOMER-{snapshot_period}"
        self.calculation_version = "asset-depreciation-rules-v1"
        self.explanation_provider = FallbackExplanationProvider()
        self.explanation_harness = self._build_explanation_harness()
        self.wide_table_qa_skill = WideTableQASkill(
            tools=WideTableQATools(
                forecast_lines=self.business_store.forecast_lines,
                knowledge_graph_path=self.knowledge_graph_path,
                policy_narrative=self.policy_narrative,
                rule_executions=self.business_store.rule_executions,
                available_periods=self._forecast_periods,
            ),
            provider=FallbackWideTableQAProvider(),
        )
        self.reverse_planning_skill = ReversePlanningSkill(
            tools=ReversePlanningTools(
                forecast_lines=self.business_store.forecast_lines,
                candidate_actions=self._reverse_candidate_actions,
                simulate=self._simulate_reverse_assumptions,
                ontology_path=self._reverse_ontology_paths,
                catalog=self.reverse_planning_catalog,
            ),
            provider=FallbackWideTableQAProvider(),
        )
        self.initialize()

    def close(self) -> None:
        self.graph_store.close()
        self.business_store.close()

    def initialize(self) -> None:
        # Customer scenarios are durable business records. Bootstrap the SQLite stores
        # only once; subsequent service starts reopen the existing baseline and all
        # saved What-if scenarios without recalculating or deleting them.
        if self.business_store.scenario("BASELINE") is not None:
            if self.graph_store.count_triples() == 0:
                self._run_forecast(
                    scenario_id="GRAPH-BOOTSTRAP",
                    budget_version=self.budget_version,
                    start_period=self.start_period,
                    months=self.months,
                )
            self._refresh_ontology_model()
            return

        self.business_store.reset()
        self.graph_store.reset()
        if self.is_customer_data:
            verified_months = self.repository.verified_forecast_months(self.start_period, maximum=self.months)
            self.business_store.save_snapshot(
                snapshot_id=self.snapshot_id,
                status={
                    **self.repository.source_summary(),
                    "source_mode": "customer_excel_only",
                    "actual_snapshot_period": self.repository.source_summary().get("snapshot_period"),
                    "forecast_start": str(self.start_period),
                    "forecast_months": self.months,
                    "forecast_periods": [str(self.start_period.add(offset)) for offset in range(self.months)],
                    "driver_verified_months": verified_months,
                    "calculation_version": self.calculation_version,
                    "baseline_assumption": "仅使用当前源数据已提供的驱动月份，不对未覆盖月份延用历史参数。",
                },
            )
        result = self._run_forecast(
            scenario_id="BASELINE",
            budget_version=self.budget_version,
            start_period=self.start_period,
            months=self.months,
        )
        result = self._include_customer_snapshot_lines(
            scenario_id="BASELINE", budget_version=self.budget_version, result=result,
        )
        self.business_store.save_scenario(
            scenario_id="BASELINE",
            base_scenario_id=None,
            budget_version=self.budget_version,
            perspective=self.perspective,
            start_period=str(self.start_period),
            months=self.months,
            description="客户资产台账实际快照 + 后续规则预测",
        )
        self.business_store.replace_scenario_results(
            scenario_id="BASELINE",
            anomalies=result["anomalies"],
            forecast_lines=result["forecast_lines"],
            summary_lines=result["summary_lines"],
        )
        self.business_store.save_rule_executions(
            scenario_id="BASELINE", executions=result.get("rule_executions", [])
        )
        self.business_store.save_scenario_metadata(
            scenario_id="BASELINE",
            scenario_name="基准场景",
            source_snapshot_id=self.snapshot_id,
            calculation_version=self.calculation_version,
            assumptions=self._baseline_assumptions(result.get("monthly_drivers", [])),
        )
        self._refresh_ontology_model()

    def _include_customer_snapshot_lines(
        self,
        *,
        scenario_id: str,
        budget_version: str,
        result: dict[str, object],
    ) -> dict[str, object]:
        if not self.is_customer_data or not hasattr(self.repository, "ledger_snapshot_lines"):
            return result
        snapshot_lines = self.repository.ledger_snapshot_lines(
            scenario_id=scenario_id,
            budget_version=budget_version,
        )
        all_lines = [*snapshot_lines, *list(result["forecast_lines"])]
        return {
            **result,
            "forecast_lines": all_lines,
            "summary_lines": DepreciationAggregator().summarize(all_lines),
            "snapshot_line_count": len(snapshot_lines),
        }

    def dashboard(self, scenario_id: str) -> dict[str, object]:
        dashboard = self.business_store.dashboard(scenario_id)
        dashboard["source_status"] = self.source_status(scenario_id)
        return dashboard

    def source_status(self, scenario_id: str) -> dict[str, object]:
        scenario = self.business_store.scenario(scenario_id)
        status = {
            "scenario_id": scenario_id,
            "budget_version": scenario["budget_version"] if scenario else self.budget_version,
            "graph_db_path": str(self.graph_db_path),
            "business_db_path": str(self.business_db_path),
            "business_db_updated_at": scenario["updated_at"] if scenario else None,
            "triple_count": self.graph_store.count_triples(),
            "inferred_triple_count": self.graph_store.count_triples(inferred=True),
        }
        if self.is_customer_data:
            status["snapshot"] = self.business_store.snapshot_status(self.snapshot_id)
        return status

    def snapshot_status(self) -> dict[str, object]:
        return self.business_store.snapshot_status(self.snapshot_id) or {}

    def rule_catalog(self) -> dict[str, object]:
        return {
            "calculation_version": self.calculation_version,
            "methods": [
                {"method": "STRAIGHT_LINE", "label_cn": "年限平均法", "templates": [
                    {"id": "straight_impairment", "label_cn": "减值后重算", "description_cn": "对指定资产输入减值金额和生效日期。", "fields": ["asset_id", "amount", "effective_date"]},
                    {"id": "straight_accelerated", "label_cn": "加速折旧", "description_cn": "将指定资产按使用年限 60% 的规则重算。", "fields": ["asset_id"]},
                    {"id": "straight_start_rule", "label_cn": "调整开始计提", "description_cn": "选择当月或次月开始计提。", "fields": ["asset_id", "start_rule"]},
                    {"id": "straight_new_asset", "label_cn": "新增同类资产", "description_cn": "以所选资产为参照，输入新增资产原值和资本化日期。", "fields": ["asset_name", "amount", "in_service_date"]},
                ]},
                {"method": "PRODUCTION", "label_cn": "产量法", "templates": [
                    {"id": "production_driver", "label_cn": "调整区块产量/储量", "description_cn": "按区块和月份输入产量、剩余储量，规则自动识别无产量、无储量和产量超过储量分支。", "fields": ["block_id", "period", "production", "reserves"]},
                ]},
                {"method": "WORKLOAD", "label_cn": "工作量法", "templates": [
                    {"id": "workload_driver", "label_cn": "调整工作量及单位费用", "description_cn": "输入月份工作量和单位费用，系统按期初净额分摊总摊销额。", "fields": ["company", "period", "workload", "unit_fee"]},
                ]},
            ],
        }

    def forecast_lines(self, query: dict[str, list[str]]) -> list[dict[str, object]]:
        self._validate_period_range(
            self._optional_arg(query, "period_from"), self._optional_arg(query, "period_to")
        )
        return self.business_store.forecast_lines(
            scenario_id=self._str_arg(query, "scenario_id", "BASELINE"),
            department=self._optional_arg(query, "department"),
            asset_category=self._optional_arg(query, "asset_category"),
            asset_source_type=self._optional_arg(query, "asset_source_type"),
            period_from=self._optional_arg(query, "period_from"),
            period_to=self._optional_arg(query, "period_to"),
            limit=self._int_arg(query, "limit", 200),
            offset=self._int_arg(query, "offset", 0),
        )

    def _forecast_periods(self, scenario_id: str) -> list[str]:
        """Return the actual scenario horizon used to resolve yearless month questions."""
        return sorted({
            str(line.get("period"))
            for line in self.business_store.forecast_lines(scenario_id=scenario_id, limit=10000)
            if line.get("period")
        })

    def summaries(self, query: dict[str, list[str]]) -> list[dict[str, object]]:
        return self.business_store.summaries(
            scenario_id=self._str_arg(query, "scenario_id", "BASELINE"),
            group=self._str_arg(query, "group", "department_category"),
        )

    def anomalies(self, query: dict[str, list[str]]) -> list[dict[str, object]]:
        return self.business_store.anomalies(
            scenario_id=self._str_arg(query, "scenario_id", "BASELINE"),
            severity=self._optional_arg(query, "severity"),
        )

    def semantic_catalog(self) -> dict[str, object]:
        return semantic_catalog()

    def ontology_meta(self) -> dict[str, object]:
        return self.business_store.ontology_meta()

    def _build_explanation_harness(self) -> ControlledExplanationHarness:
        registry = ToolRegistry()
        registry.register_many([
            HarnessTool(
                name="queryDashboard",
                label_cn="读取预算总览",
                description_cn="从业务结果库读取 KPI、年度趋势、部门排行和资产贡献。",
                read_only=True,
                runner=lambda args: self.business_store.dashboard(str(args.get("scenario_id") or "BASELINE")),
            ),
            HarnessTool(
                name="explainChange",
                label_cn="读取变化解释事实",
                description_cn="按场景、部门和年度读取驱动拆分与贡献资产。",
                read_only=True,
                runner=lambda args: self.business_store.explain_change(
                    scenario_id=str(args.get("scenario_id") or "BASELINE"),
                    department=str(args.get("department")) if args.get("department") else None,
                    year=int(args["year"]) if args.get("year") else None,
                ),
            ),
            HarnessTool(
                name="listAnomalies",
                label_cn="读取异常清单",
                description_cn="读取当前场景的中文异常、影响和建议。",
                read_only=True,
                runner=lambda args: self.business_store.anomalies(
                    scenario_id=str(args.get("scenario_id") or "BASELINE")
                ),
            ),
            HarnessTool(
                name="listAssetCards",
                label_cn="读取资产卡片",
                description_cn="读取资产主数据、适用政策和预测期折旧合计。",
                read_only=True,
                runner=lambda args: self.assets_cards(
                    {"scenario_id": [str(args.get("scenario_id") or "BASELINE")]}
                ),
            ),
            HarnessTool(
                name="ontologyMeta",
                label_cn="读取 ontology 元模型",
                description_cn="读取可用对象类型、动作类型和函数类型，用于解释可执行建议。",
                read_only=True,
                runner=lambda _args: self.business_store.ontology_meta(),
            ),
            HarnessTool(
                name="reversePlanningCatalog",
                label_cn="读取反向推演能力目录",
                description_cn="读取可推演期间、目标范围和受规则约束的动作类型。",
                read_only=True,
                runner=lambda _args: self.reverse_planning_catalog(),
            ),
        ])
        return ControlledExplanationHarness(registry)

    def explain_change(self, query: dict[str, list[str]]) -> dict[str, object]:
        return self.business_store.explain_change(
            scenario_id=self._str_arg(query, "scenario_id", "BASELINE"),
            department=self._optional_arg(query, "department"),
            year=self._optional_int_arg(query, "year"),
        )

    def explanation(self, query: dict[str, list[str]]) -> dict[str, object]:
        return self.explanation_harness.explain(
            scenario_id=self._str_arg(query, "scenario_id", "BASELINE"),
            department=self._optional_arg(query, "department"),
            year=self._optional_int_arg(query, "year"),
            style=self._str_arg(query, "style", "finance"),
            provider=self.explanation_provider,
        )

    def policy_proof(self, asset_ref: str, scenario_id: str = "BASELINE") -> dict[str, object]:
        matching = self.business_store._all(
            """
            select *
            from forecast_lines
            where scenario_id = ?
              and coalesce(asset_id, planned_asset_id) = ?
            order by period
            limit 1
            """,
            (scenario_id, asset_ref),
        )
        first = matching[0] if matching else None
        source_context = None
        if first is None:
            source_context = self._source_context(asset_ref)
            if source_context and source_context.get("asset_category"):
                first = {
                    "company": source_context.get("company", "CN01"),
                    "department": source_context.get("department", "-"),
                    "asset_category": source_context.get("asset_category"),
                    "depreciation_code": source_context.get("depreciation_code", "-"),
                    "depreciation_policy": "-",
                }
        if first is None:
            return {
                "asset_ref": asset_ref,
                "policy_match": None,
                "category_chain": [],
                "source_context": source_context,
                "first_lines": [],
            }
        proof = self.graph_store.explain_policy_match(
            company=first["company"],
            perspective=self.perspective,
            asset_category=first["asset_category"],
        )
        ancestors = self.graph_store.ancestors_including_self(first["asset_category"])
        lines = self.business_store._all(
            """
            select period, monthly_depreciation, source_event_id, calculation_rule_id,
                   opening_net_value, closing_net_value
            from forecast_lines
            where scenario_id = ?
              and coalesce(asset_id, planned_asset_id) = ?
            order by period
            limit 24
            """,
            (scenario_id, asset_ref),
        )
        return {
            "asset_ref": asset_ref,
            "scenario_id": scenario_id,
            "asset": {
                "department": first["department"],
                "asset_category": first["asset_category"],
                "depreciation_code": first["depreciation_code"],
                "depreciation_policy": first["depreciation_policy"],
            },
            "source_context": source_context,
            "category_chain": ancestors,
            "policy_match": proof,
            "first_lines": lines,
        }

    def _source_context(self, object_id: str) -> dict[str, object] | None:
        for asset in self.repository.load_fixed_assets():
            if asset.asset_id == object_id:
                return {
                    "object_type": "FixedAsset",
                    "object_id": asset.asset_id,
                    "name": asset.name,
                    "company": asset.company,
                    "department": asset.department,
                    "asset_category": asset.asset_category,
                    "depreciation_code": asset.depreciation_code,
                }
        for asset in self.repository.load_planned_assets():
            if asset.planned_asset_id == object_id:
                return {
                    "object_type": "PlannedAsset",
                    "object_id": asset.planned_asset_id,
                    "name": asset.name,
                    "company": asset.company,
                    "department": asset.department,
                    "asset_category": asset.asset_category,
                    "depreciation_code": asset.depreciation_code,
                }
        for event in self.repository.load_asset_events():
            if event.event_id == object_id:
                return {
                    "object_type": "AssetEvent",
                    "object_id": event.event_id,
                    "event_type": event.event_type,
                    "target_asset_id": event.target_asset_id,
                    "target_planned_asset_id": event.target_planned_asset_id,
                    "company": event.company,
                    "department": event.department,
                    "description": event.description,
                }
        return None

    def graph_triples(self, limit: int) -> list[dict[str, object]]:
        return self.graph_store.triples(limit=limit)

    def wide_table(self, query: dict[str, list[str]]) -> dict[str, object]:
        self._validate_period_range(
            self._optional_arg(query, "period_from"), self._optional_arg(query, "period_to")
        )
        dimensions = self._list_arg(query, "dimension")
        table = self.business_store.wide_table(
            scenario_id=self._str_arg(query, "scenario_id", "BASELINE"),
            row_type=self._str_arg(query, "row_type", "overview"),
            department=self._optional_arg(query, "department"),
            asset_category=self._optional_arg(query, "asset_category"),
            dimensions=dimensions if dimensions else None,
        )
        return self._decorate_wide_table(table)

    def wide_table_dimension_catalog(self) -> dict[str, object]:
        if self.is_customer_data and hasattr(self.repository, "dimension_catalog"):
            return self.repository.dimension_catalog()
        return {
            "dimensions": [
                {"id": "department", "label_cn": "部门"},
                {"id": "asset_category", "label_cn": "资产类别"},
                {"id": "depreciation_code", "label_cn": "折旧码"},
                {"id": "asset", "label_cn": "资产"},
            ],
            "category_labels": CATEGORY_LABEL_CN,
            "asset_labels": {},
            "depreciation_code_labels": {},
        }

    def qa_status(self) -> dict[str, object]:
        return self.wide_table_qa_skill.status()

    def wide_table_question_catalog(self, query: dict[str, list[str]]) -> dict[str, object]:
        return self.wide_table_qa_skill.catalog(self._str_arg(query, "scenario_id", "BASELINE"))

    def reverse_planning_catalog(self, scenario_id: str = "BASELINE") -> dict[str, object]:
        lines = self.business_store.forecast_lines(scenario_id=scenario_id, limit=10000)
        scopes: list[dict[str, object]] = []
        for scope_type, field, label in (("company", "company", "公司"), ("department", "department", "所属单位"), ("asset_category", "asset_category", "资产类别")):
            values = sorted({str(line.get(field) or "") for line in lines if line.get(field)})
            scopes.extend({"type": scope_type, "value": value, "label_cn": value if scope_type != "asset_category" else self._category_name(value)} for value in values)
        periods = sorted({str(line["period"]) for line in lines})
        return {"periods": periods, "default_company": lines[0]["company"] if lines else "", "scenario_id": scenario_id,
                "scopes": scopes,
                "actions": [
                    {"id": "straight_new_asset", "label_cn": "新增资产", "description_cn": "新增资产在目标月进入折旧。"},
                    {"id": "straight_impairment", "label_cn": "减值后重算", "description_cn": "减值后按剩余期间重算折旧。"},
                    {"id": "straight_accelerated", "label_cn": "加速折旧", "description_cn": "按规则采用 60% 使用年限。"},
                    {"id": "production_driver", "label_cn": "调整产量/储量", "description_cn": "影响产量法资产当月折耗。"},
                    {"id": "workload_driver", "label_cn": "调整工作量/单位费用", "description_cn": "影响工作量法资产当月摊销。"},
                ]}

    def ask_reverse_planning(self, payload: dict[str, object]) -> dict[str, object]:
        payload.setdefault("scenario_id", "BASELINE")
        return self.reverse_planning_skill.answer(payload)

    def _category_name(self, category: str) -> str:
        catalog = self.wide_table_dimension_catalog()
        return str(catalog.get("category_labels", {}).get(category) or category)

    def _matches_reverse_scope(self, asset: FixedAsset, analysis: dict[str, object]) -> bool:
        return ((analysis.get("scope_type") == "company" and asset.company == analysis.get("scope_value"))
                or (analysis.get("scope_type") == "department" and asset.department == analysis.get("scope_value"))
                or (analysis.get("scope_type") == "asset_category" and asset.asset_category == analysis.get("scope_value")))

    def _reverse_candidate_actions(self, analysis: dict[str, object]) -> list[dict[str, object]]:
        assets = [item for item in self.repository.load_fixed_assets() if self._matches_reverse_scope(item, analysis)]
        target_period = Month.parse(str(analysis["target_period"]))
        delta = Decimal(str(analysis["required_delta"]))
        direction = str(analysis.get("direction") or ("increase" if delta > 0 else "decrease"))
        candidates: list[dict[str, object]] = []
        straight = sorted(
            [item for item in assets if item.depreciation_code in ("Z111", "Z112")],
            key=lambda item: item.original_cost - item.accumulated_depreciation - item.accumulated_impairment,
            reverse=True,
        )
        if direction != "decrease" and straight:
            reference = straight[0]
            life = Decimal(str(reference.useful_life_months or 120))
            amount = max(Decimal("1"), abs(delta) * life / (Decimal("1") - (reference.residual_rate or Decimal("0"))))
            in_service = target_period if reference.depreciation_code == "Z111" else target_period.add(-1)
            assumptions = [{"template_id": "straight_new_asset", "asset_id": "REVERSE-NEW-001", "reference_asset_id": reference.asset_id,
                            "asset_name": "反向推演新增资产", "asset_category": reference.asset_category, "depreciation_code": reference.depreciation_code,
                            "amount": str(amount), "in_service_date": f"{in_service.year:04d}-{in_service.month:02d}-01"}]
            candidates.append({"action_key": "new_asset", "actions": [{"label_cn": f"新增资产（参考 {reference.asset_id}）", "template_id": "straight_new_asset", "target_object": reference.asset_id}], "assumptions": assumptions, "affected_object_count": 1})
        if direction != "increase":
            for asset in straight[:4]:
                amount = min(asset.original_cost, max(Decimal("1"), abs(delta) * Decimal(str(asset.useful_life_months or 120))))
                candidates.append({"action_key": f"impair:{asset.asset_id}", "actions": [{"label_cn": f"{asset.asset_id} 减值后重算", "template_id": "straight_impairment", "target_object": asset.asset_id, "notice_cn": "减值为业务假设，需按财务制度确认。"}],
                                   "assumptions": [{"template_id": "straight_impairment", "asset_id": asset.asset_id, "amount": str(amount), "effective_date": f"{target_period.year:04d}-{target_period.month:02d}-01"}], "affected_object_count": 1})
        if direction != "decrease":
            for asset in straight[:4]:
                candidates.append({"action_key": f"accelerate:{asset.asset_id}", "actions": [{"label_cn": f"{asset.asset_id} 加速折旧", "template_id": "straight_accelerated", "target_object": asset.asset_id}],
                                   "assumptions": [{"template_id": "straight_accelerated", "asset_id": asset.asset_id}], "affected_object_count": 1})
        production_assets = [item for item in assets if item.depreciation_code == "Z802" and item.block_id]
        baseline_production = {
            driver.target_id: driver
            for driver in self.repository.baseline_drivers(start_period=self.start_period, months=self.months)
            if driver.driver_type == "PRODUCTION" and str(driver.period) == str(target_period)
        }
        for block_id in list(dict.fromkeys(str(item.block_id) for item in production_assets))[:2]:
            driver = baseline_production.get(block_id)
            if driver is None or driver.reserves <= 0:
                continue
            ratios = (Decimal("1.25"), Decimal("1.50")) if direction == "increase" else (Decimal("0.75"), Decimal("0.50"), Decimal("0"))
            for ratio in ratios:
                production = min(driver.reserves, driver.production * ratio)
                ratio_percent = int(ratio * Decimal("100"))
                assumption = {
                    "template_id": "production_driver", "block_id": block_id, "period": str(target_period),
                    "production": str(production), "reserves": str(driver.reserves),
                }
                label = f"区块 {block_id} 当月产量调整为基准的 {ratio_percent}%"
                candidates.append({
                    "action_key": f"production:{block_id}:{ratio_percent}",
                    "actions": [{"label_cn": label, "template_id": "production_driver", "target_object": block_id}],
                    "assumptions": [assumption],
                    "affected_object_count": 1,
                })
        workload_assets = [item for item in assets if item.depreciation_code == "Z901"]
        if workload_assets and direction != "decrease":
            company = workload_assets[0].company
            candidates.append({"action_key": f"workload:{company}", "actions": [{"label_cn": f"公司 {company} 调整工作量与单位费用", "template_id": "workload_driver", "target_object": company}],
                               "assumptions": [{"template_id": "workload_driver", "company": company, "period": str(target_period), "workload": "1", "unit_fee": str(max(Decimal("1"), abs(delta)))}], "affected_object_count": len(workload_assets)})
        return candidates

    def _simulate_reverse_assumptions(self, assumptions: list[dict[str, object]], analysis: dict[str, object]) -> dict[str, object]:
        scenario_id = str(analysis.get("scenario_id") or "BASELINE")
        inherited_assumptions = [] if scenario_id == "BASELINE" else self.business_store.scenario_assumptions(scenario_id)
        fixed_assets, planned_assets, events, drivers, _changes = self._apply_customer_assumptions(
            fixed_assets=self.repository.load_fixed_assets(), planned_assets=[], events=[],
            drivers=self.repository.baseline_drivers(start_period=self.start_period, months=self.months), assumptions=[*inherited_assumptions, *assumptions],
        )
        result = self._run_forecast(scenario_id="REVERSE-PLAN", budget_version=self.budget_version, start_period=self.start_period,
                                    months=self.months, fixed_assets=fixed_assets, planned_assets=planned_assets, events=events,
                                    monthly_drivers=drivers, load_graph=False)
        lines = [line for line in result["forecast_lines"] if str(line.period) == str(analysis["target_period"])]
        scope_type, scope_value = analysis["scope_type"], analysis["scope_value"]
        lines = [line for line in lines if (scope_type == "company" and line.company == scope_value)
                 or (scope_type == "department" and line.department == scope_value)
                 or (scope_type == "asset_category" and line.asset_category == scope_value)]
        total = sum((line.monthly_depreciation for line in lines), Decimal("0"))
        executions = [item for item in result.get("rule_executions", []) if str(item.period) == str(analysis["target_period"])]
        relevant_refs = {
            str(assumption.get("asset_id") or assumption.get("reference_asset_id") or "")
            for assumption in assumptions
            if assumption.get("asset_id") or assumption.get("reference_asset_id")
        }
        relevant_blocks = {str(assumption.get("block_id") or "") for assumption in assumptions if assumption.get("block_id")}
        relevant_companies = {str(assumption.get("company") or "") for assumption in assumptions if assumption.get("company")}
        filtered_executions = [
            item for item in executions
            if str(item.asset_ref) in relevant_refs
            or (relevant_blocks and str((item.inputs or {}).get("区块") or "") in relevant_blocks)
            or (relevant_companies and str((item.inputs or {}).get("公司") or "") in relevant_companies)
        ]
        return {"target_amount": f"{total:.2f}", "rule_execution_trace": to_jsonable(filtered_executions or executions), "scenario_written": False}

    def _reverse_ontology_paths(self, context: dict[str, object]) -> list[dict[str, object]]:
        scope_label = {"company": "公司", "department": "所属单位", "asset_category": "资产类别"}.get(str(context.get("scope_type")), "目标范围")
        scope = f"{scope_label} {context.get('scope_value')}"
        paths: list[dict[str, object]] = []
        for recommendation in context.get("recommendations", []):
            for action in recommendation.get("actions", []):
                target = str(action.get("target_object") or "")
                template = str(action.get("template_id") or "")
                asset = next((item for item in self.repository.load_fixed_assets() if item.asset_id == target), None)
                if asset is not None:
                    method = "年限平均法" if asset.depreciation_code in ("Z111", "Z112") else "产量法" if asset.depreciation_code == "Z802" else "工作量法" if asset.depreciation_code == "Z901" else "折旧规则"
                    rule = next((item for item in recommendation.get("rule_execution_trace", []) if str(item.get("asset_ref") or "") == asset.asset_id), {})
                    branch = str(rule.get("branch_id") or "实际规则分支")
                    path_cn = f"{scope} -> 资产 {asset.asset_id} -> 折旧码 {asset.depreciation_code} -> {method} -> {branch} -> 临时假设 {template} -> {context.get('target_period')} 试算结果"
                    nodes = [scope, f"FixedAsset:{asset.asset_id}", f"DepreciationCode:{asset.depreciation_code}", method, branch, f"ScenarioAssumption:{template}", "ForecastLine"]
                elif template == "production_driver":
                    path_cn = f"{scope} -> 区块 {target} -> 月度产量/储量参数 -> 产量法 -> 临时假设 {template} -> {context.get('target_period')} 试算结果"
                    nodes = [scope, f"Block:{target}", "MonthlyDriver:PRODUCTION", "产量法", f"ScenarioAssumption:{template}", "ForecastLine"]
                elif template == "workload_driver":
                    path_cn = f"{scope} -> 公司 {target} -> 月度工作量/单位费用 -> 工作量法 -> 临时假设 {template} -> {context.get('target_period')} 试算结果"
                    nodes = [scope, f"Company:{target}", "MonthlyDriver:WORKLOAD", "工作量法", f"ScenarioAssumption:{template}", "ForecastLine"]
                else:
                    path_cn = f"{scope} -> 可作用对象 {target} -> 临时假设 {template} -> {context.get('target_period')} 试算结果"
                    nodes = [scope, target, f"ScenarioAssumption:{template}", "ForecastLine"]
                paths.append({"recommendation_number": recommendation.get("recommendation_number"), "action_template": template, "path_cn": path_cn, "nodes": nodes, "inferred": True})
        return paths

    def _decorate_wide_table(self, table: dict[str, object]) -> dict[str, object]:
        catalog = self.wide_table_dimension_catalog()
        dimension_labels = {
            str(item["id"]): str(item["label_cn"])
            for item in catalog.get("dimensions", [])
        }
        dimension_labels["scope_label"] = "总览"
        category_labels = catalog.get("category_labels", {})
        asset_labels = catalog.get("asset_labels", {})
        code_labels = catalog.get("depreciation_code_labels", {})

        def label_for(dimension: str, value: str) -> str:
            if dimension == "asset_category":
                return f"{category_labels.get(value, value)}（{value}）"
            if dimension == "asset":
                return f"{asset_labels.get(value, value)}（{value}）"
            if dimension == "depreciation_code":
                return f"{code_labels.get(value, value)}（{value}）"
            return value

        def decorate(nodes: list[dict[str, object]]) -> None:
            for node in nodes:
                dimension = str(node.get("dimension") or "")
                value = str(node.get("value") or "")
                node["dimension_label_cn"] = dimension_labels.get(dimension, dimension)
                node["label_cn"] = label_for(dimension, value)
                decorate(node.get("children", []))

        decorate(table.get("tree", []))
        if self.is_customer_data:
            snapshot_period = self.repository.source_summary().get("snapshot_period")
            table["period_metadata"] = {
                period: {
                    "data_type": "actual_snapshot" if period == snapshot_period else "forecast",
                    "label_cn": "台账实际" if period == snapshot_period else "规则预测",
                }
                for period in table.get("periods", [])
            }
        table["dimension_catalog"] = catalog
        return table

    def wide_table_compare(self, payload: dict[str, object]) -> dict[str, object]:
        self._validate_period_range(
            str(payload.get("period_from") or "") or None,
            str(payload.get("period_to") or "") or None,
        )
        baseline = self._payload_scenario_id(
            payload.get("baseline") or payload.get("baseline_scenario_id") or "BASELINE"
        )
        scenarios_value = payload.get("scenarios") or payload.get("scenario_ids") or []
        if isinstance(scenarios_value, str):
            scenario_ids = [item.strip() for item in scenarios_value.split(",") if item.strip()]
        elif isinstance(scenarios_value, list):
            scenario_ids = [self._payload_scenario_id(item) for item in scenarios_value]
        else:
            scenario_ids = []
        dimensions_value = payload.get("dimensions") or []
        if isinstance(dimensions_value, str):
            dimensions = [item.strip() for item in dimensions_value.split(",") if item.strip()]
        elif isinstance(dimensions_value, list):
            dimensions = [str(item).strip() for item in dimensions_value if str(item).strip()]
        else:
            dimensions = []
        table = self.business_store.wide_table_compare(
            baseline_scenario_id=baseline,
            scenario_ids=[item for item in scenario_ids if item and item != baseline],
            row_type=str(payload.get("row_type") or "overview"),
            department=str(payload.get("department")) if payload.get("department") else None,
            asset_category=str(payload.get("asset_category")) if payload.get("asset_category") else None,
            period_from=str(payload.get("period_from")) if payload.get("period_from") else None,
            period_to=str(payload.get("period_to")) if payload.get("period_to") else None,
            dimensions=dimensions,
        )
        return self._decorate_compare_wide_table(table)

    def _decorate_compare_wide_table(self, table: dict[str, object]) -> dict[str, object]:
        catalog = self.wide_table_dimension_catalog()
        dimension_labels = {str(item["id"]): str(item["label_cn"]) for item in catalog.get("dimensions", [])}
        dimension_labels["scope_label"] = "总览"
        category_labels = catalog.get("category_labels", {})
        asset_labels = catalog.get("asset_labels", {})
        code_labels = catalog.get("depreciation_code_labels", {})

        def label_for(dimension: str, value: str) -> str:
            if dimension == "asset_category":
                return f"{category_labels.get(value, value)}（{value}）"
            if dimension == "asset":
                return f"{asset_labels.get(value, value)}（{value}）"
            if dimension == "depreciation_code":
                return f"{code_labels.get(value, value)}（{value}）"
            return value

        def decorate(nodes: list[dict[str, object]]) -> None:
            for node in nodes:
                dimension = str(node.get("dimension") or "")
                value = str(node.get("value") or "")
                node["dimension_label_cn"] = dimension_labels.get(dimension, dimension)
                node["label_cn"] = label_for(dimension, value)
                decorate(node.get("children", []))

        decorate(table.get("tree", []))
        table["dimension_catalog"] = catalog
        return table

    def ask_wide_table_question(self, payload: dict[str, object]) -> dict[str, object]:
        self._validate_period_range(
            str(payload.get("period_from") or "") or None,
            str(payload.get("period_to") or "") or None,
        )
        return self.wide_table_qa_skill.answer(payload)

    def _validate_period_range(self, period_from: str | None, period_to: str | None) -> None:
        available = self._forecast_periods("BASELINE")
        if not available:
            return
        lower, upper = available[0], available[-1]
        for label, period in (("起始期间", period_from), ("结束期间", period_to)):
            if period and (period < lower or period > upper):
                raise ValueError(f"{label} {period} 超出当前源数据覆盖范围 {lower} 至 {upper}。")

    def _analyze_wide_question(
        self,
        *,
        question: str,
        row_type: str,
        payload_category: str | None,
        period_from: str | None,
        period_to: str | None,
    ) -> dict[str, object]:
        normalized = question.replace(" ", "")
        target_period = self._period_from_question(normalized) or period_to or period_from
        previous_period = self._previous_period(target_period) if target_period else None
        asset_ref_match = re.search(r"\b(?:FA|PA)-\d+\b", question, re.IGNORECASE)
        asset_category = self._category_from_question(normalized) or payload_category
        has_change_words = any(word in normalized for word in ("提升", "增加", "上涨", "上升", "变高", "大幅", "为什么多", "为什么高"))
        has_decrease_words = any(word in normalized for word in ("下降", "减少", "降低", "变少"))
        has_rank_words = any(word in normalized for word in ("最高", "最大", "最多", "贡献最高", "贡献最大"))
        if target_period and (has_change_words or has_decrease_words):
            intent = "period_change"
            intent_label = "期间变化原因"
        elif has_rank_words:
            intent = "top_contributor"
            intent_label = "主要贡献对象"
        else:
            intent = "scope_explanation"
            intent_label = "范围折旧解释"
        return {
            "intent": intent,
            "intent_label_cn": intent_label,
            "row_type": row_type,
            "asset_ref": asset_ref_match.group(0).upper() if asset_ref_match else None,
            "asset_category": asset_category,
            "asset_category_label_cn": category_label(asset_category) if asset_category else None,
            "target_period": target_period,
            "previous_period": previous_period,
            "recognized_terms": [
                item for item in [
                    category_label(asset_category) if asset_category else "",
                    target_period or "",
                    "环比提升" if has_change_words else "",
                    "环比下降" if has_decrease_words else "",
                    "贡献排序" if has_rank_words else "",
                ] if item
            ],
        }

    def _period_from_question(self, text: str) -> str | None:
        match = re.search(r"(20\d{2})[-/年](\d{1,2})月?", text)
        if match:
            return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}"
        match = re.search(r"(\d{2})年(\d{1,2})月", text)
        if match:
            return f"20{int(match.group(1)):02d}-{int(match.group(2)):02d}"
        return None

    def _previous_period(self, period: str | None) -> str | None:
        if not period:
            return None
        try:
            return str(Month.parse(period).add(-1))
        except (ValueError, IndexError):
            return None

    def _category_from_question(self, text: str) -> str | None:
        aliases: dict[str, list[str]] = {
            "BUILDING": ["房屋建筑物", "房屋建筑", "建筑物", "厂房", "建筑"],
            "INJECTION_EQUIPMENT": ["注塑设备", "注塑机", "注塑"],
            "MACHINE_EQUIPMENT": ["机器设备", "机械设备", "设备"],
            "ELECTRONIC_EQUIPMENT": ["电子设备", "电脑", "笔记本", "办公设备"],
            "PRODUCTION_EQUIPMENT": ["生产设备"],
        }
        for category_id, label_cn in CATEGORY_LABEL_CN.items():
            if category_id in text or label_cn in text:
                return category_id
        for category_id, terms in aliases.items():
            if any(term in text for term in terms):
                return category_id
        return None

    def _wide_question_facts(self, lines: list[dict[str, object]]) -> dict[str, object]:
        total = sum(Decimal(str(line.get("monthly_depreciation") or "0")) for line in lines)
        by_asset: dict[str, dict[str, object]] = {}
        by_policy: dict[str, Decimal] = {}
        by_source: dict[str, Decimal] = {}
        for line in lines:
            asset_ref = str(line.get("asset_id") or line.get("planned_asset_id") or "")
            if not asset_ref:
                continue
            amount = Decimal(str(line.get("monthly_depreciation") or "0"))
            row = by_asset.setdefault(
                asset_ref,
                {
                    "asset_ref": asset_ref,
                    "asset_source_type": line.get("asset_source_type"),
                    "department": line.get("department"),
                    "asset_category": line.get("asset_category"),
                    "asset_category_label_cn": category_label(line.get("asset_category")),
                    "depreciation_code": line.get("depreciation_code"),
                    "depreciation_code_label_cn": depreciation_code_label(line.get("depreciation_code")),
                    "depreciation_policy": line.get("depreciation_policy"),
                    "depreciation_policy_label_cn": policy_label(line.get("depreciation_policy")),
                    "depreciation": Decimal("0"),
                },
            )
            row["depreciation"] = Decimal(str(row["depreciation"])) + amount
            by_policy[str(line.get("depreciation_policy") or "-")] = by_policy.get(str(line.get("depreciation_policy") or "-"), Decimal("0")) + amount
            by_source[str(line.get("asset_source_type") or "-")] = by_source.get(str(line.get("asset_source_type") or "-"), Decimal("0")) + amount
        top_assets = sorted(by_asset.values(), key=lambda item: Decimal(str(item["depreciation"])), reverse=True)[:5]
        for item in top_assets:
            item["depreciation"] = f"{Decimal(str(item['depreciation'])):.2f}"
        policy_breakdown = [
            {
                "depreciation_policy": policy_id,
                "depreciation_policy_label_cn": policy_label(policy_id),
                "depreciation": f"{amount:.2f}",
            }
            for policy_id, amount in sorted(by_policy.items(), key=lambda item: item[1], reverse=True)
        ]
        source_breakdown = [
            {
                "asset_source_type": source,
                "asset_source_type_label_cn": ASSET_SOURCE_TYPE_LABEL_CN.get(source, source),
                "depreciation": f"{amount:.2f}",
            }
            for source, amount in sorted(by_source.items(), key=lambda item: item[1], reverse=True)
        ]
        return {
            "line_count": len(lines),
            "total_depreciation": f"{total:.2f}",
            "top_asset": top_assets[0] if top_assets else None,
            "top_assets": top_assets,
            "policy_breakdown": policy_breakdown,
            "source_breakdown": source_breakdown,
        }

    def _wide_question_period_comparison(
        self,
        *,
        scenario_id: str,
        department: str | None,
        asset_category: str | None,
        target_period: str,
        previous_period: str,
    ) -> dict[str, object]:
        previous_lines = self.business_store.forecast_lines(
            scenario_id=scenario_id,
            department=department,
            asset_category=asset_category,
            period_from=previous_period,
            period_to=previous_period,
            limit=10000,
        )
        target_lines = self.business_store.forecast_lines(
            scenario_id=scenario_id,
            department=department,
            asset_category=asset_category,
            period_from=target_period,
            period_to=target_period,
            limit=10000,
        )
        previous_by_asset = self._amount_by_asset(previous_lines)
        target_by_asset = self._amount_by_asset(target_lines)
        asset_ids = sorted(set(previous_by_asset) | set(target_by_asset))
        drivers: list[dict[str, object]] = []
        for asset_ref in asset_ids:
            previous_amount = Decimal(str(previous_by_asset.get(asset_ref, {}).get("amount", "0")))
            target_amount = Decimal(str(target_by_asset.get(asset_ref, {}).get("amount", "0")))
            difference = target_amount - previous_amount
            if difference == 0:
                continue
            source = target_by_asset.get(asset_ref) or previous_by_asset.get(asset_ref) or {}
            driver_type = "新增计提" if previous_amount == 0 and target_amount > 0 else "月折旧变化"
            if target_amount == 0 and previous_amount > 0:
                driver_type = "停止计提"
            elif difference < 0:
                driver_type = "折旧减少"
            drivers.append(
                {
                    **source,
                    "asset_ref": asset_ref,
                    "previous_amount": f"{previous_amount:.2f}",
                    "target_amount": f"{target_amount:.2f}",
                    "difference": f"{difference:.2f}",
                    "abs_difference": f"{abs(difference):.2f}",
                    "driver_type": driver_type,
                    "driver_text_cn": self._wide_question_driver_text(source, previous_amount, target_amount, difference),
                }
            )
        drivers.sort(key=lambda item: Decimal(str(item["abs_difference"])), reverse=True)
        previous_total = sum(Decimal(str(line.get("monthly_depreciation") or "0")) for line in previous_lines)
        target_total = sum(Decimal(str(line.get("monthly_depreciation") or "0")) for line in target_lines)
        difference_total = target_total - previous_total
        top_driver = drivers[0] if drivers else None
        return {
            "line_count": len(previous_lines) + len(target_lines),
            "scenario_id": scenario_id,
            "asset_category": asset_category,
            "asset_category_label_cn": category_label(asset_category) if asset_category else "全部资产类别",
            "previous_period": previous_period,
            "target_period": target_period,
            "previous_total": f"{previous_total:.2f}",
            "target_total": f"{target_total:.2f}",
            "difference": f"{difference_total:.2f}",
            "direction_cn": "提升" if difference_total > 0 else "下降" if difference_total < 0 else "持平",
            "drivers": drivers[:8],
            "top_driver_asset": top_driver,
            "top_asset": top_driver,
            "top_assets": drivers[:5],
        }

    def _amount_by_asset(self, lines: list[dict[str, object]]) -> dict[str, dict[str, object]]:
        output: dict[str, dict[str, object]] = {}
        for line in lines:
            asset_ref = str(line.get("asset_id") or line.get("planned_asset_id") or "")
            if not asset_ref:
                continue
            amount = Decimal(str(line.get("monthly_depreciation") or "0"))
            row = output.setdefault(
                asset_ref,
                {
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
                },
            )
            row["amount"] = Decimal(str(row["amount"])) + amount
            row["addition_amount"] = Decimal(str(row["addition_amount"])) + Decimal(str(line.get("addition_amount") or "0"))
            row["disposal_amount"] = Decimal(str(row["disposal_amount"])) + Decimal(str(line.get("disposal_amount") or "0"))
            row["impairment_amount"] = Decimal(str(row["impairment_amount"])) + Decimal(str(line.get("impairment_amount") or "0"))
        return output

    def _wide_question_driver_text(
        self,
        source: dict[str, object],
        previous_amount: Decimal,
        target_amount: Decimal,
        difference: Decimal,
    ) -> str:
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

    def _wide_question_graph_reasoning(self, *, scenario_id: str, top_asset: dict[str, object] | None) -> dict[str, object] | None:
        if not top_asset:
            return None
        asset_ref = str(top_asset.get("asset_ref") or "")
        source_type = "PlannedAsset" if top_asset.get("asset_source_type") == "PLANNED" else "FixedAsset"
        policy_id = str(top_asset.get("depreciation_policy") or "")
        path = None
        if policy_id:
            path = self.knowledge_graph_path(
                {
                    "from": [object_id(source_type, asset_ref)],
                    "to": [object_id("DepreciationPolicy", policy_id)],
                    "scenario_id": [scenario_id],
                }
            )
        narrative = self.policy_narrative(asset_ref, scenario_id) if asset_ref else None
        return {
            "asset_ref": asset_ref,
            "asset_object_id": object_id(source_type, asset_ref),
            "policy_object_id": object_id("DepreciationPolicy", policy_id) if policy_id else None,
            "path": path,
            "policy_narrative": narrative,
        }

    def _wide_question_steps(
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

    def _wide_question_change_steps(
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
                "detail_cn": (
                    f"问题是“{question}”。系统识别为“{question_analysis.get('intent_label_cn')}”，"
                    f"识别出的关键词为：{recognized}。"
                ),
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
                    f"{category} 在 {previous_period} 的折旧为 {comparison.get('previous_total')}，"
                    f"{target_period} 的折旧为 {comparison.get('target_total')}，"
                    f"环比{comparison.get('direction_cn')} {comparison.get('difference')}。"
                ),
            },
            {
                "step": 4,
                "title_cn": "定位变化驱动资产",
                "detail_cn": top_driver.get("driver_text_cn") or "没有发现产生差异的资产。",
            },
            {
                "step": 5,
                "title_cn": "知识图谱追溯政策",
                "detail_cn": path.get("narrative_cn") or "没有找到该资产到折旧政策的完整图谱路径。",
            },
            {
                "step": 6,
                "title_cn": "解释计算规则",
                "detail_cn": (
                    f"图谱匹配到的政策为 {policy.get('policy_label_cn') or top_driver.get('depreciation_policy_label_cn', '-')}，"
                    f"规则为 {policy.get('method_label_cn', '-')} / {policy.get('useful_life_months', '-')} 个月 / "
                    f"{policy.get('residual_rate_label_cn', '-')} / {policy.get('start_rule_label_cn', '-')}。"
                ),
            },
        ]

    def _wide_question_answer(self, facts: dict[str, object], graph_reasoning: dict[str, object] | None) -> str:
        top_asset = facts.get("top_asset") or {}
        narrative = ((graph_reasoning or {}).get("policy_narrative") or {}).get("narrative_cn")
        return (
            f"当前宽表范围内折旧合计为 {facts.get('total_depreciation', '0.00')}。"
            f"主要原因是 {top_asset.get('asset_ref', '无')} 贡献最高，金额为 {top_asset.get('depreciation', '0.00')}。"
            f"该对象登记为 {top_asset.get('asset_category_label_cn', '-')}，使用 {top_asset.get('depreciation_code_label_cn', '-')}，"
            f"并匹配 {top_asset.get('depreciation_policy_label_cn', '-')}。"
            f"{narrative or ''}"
        )

    def _wide_question_change_answer(self, comparison: dict[str, object], graph_reasoning: dict[str, object] | None) -> str:
        top_driver = comparison.get("top_driver_asset") or {}
        narrative = ((graph_reasoning or {}).get("policy_narrative") or {}).get("narrative_cn")
        return (
            f"{comparison.get('asset_category_label_cn')} 在 {comparison.get('target_period')} 出现{comparison.get('direction_cn')}，"
            f"原因在于当前问题限定的资产类别和月份发生了环比变化："
            f"{comparison.get('previous_period')} 为 {comparison.get('previous_total')}，"
            f"{comparison.get('target_period')} 为 {comparison.get('target_total')}，"
            f"差异为 {comparison.get('difference')}。"
            f"主要驱动是 {top_driver.get('asset_ref', '无')}：{top_driver.get('driver_text_cn', '')}"
            f"{narrative or ''}"
        )

    def assets_cards(self, query: dict[str, list[str]]) -> list[dict[str, object]]:
        scenario_id = self._str_arg(query, "scenario_id", "BASELINE")
        department = self._optional_arg(query, "department")
        asset_category = self._optional_arg(query, "asset_category")
        source_type = self._optional_arg(query, "asset_source_type")
        search = self._optional_arg(query, "search")
        amount_by_asset = self.business_store.asset_card_amounts(scenario_id)
        anomalies_by_object: dict[str, list[dict[str, object]]] = {}
        for anomaly in self.business_store.anomalies(scenario_id=scenario_id):
            anomalies_by_object.setdefault(str(anomaly["object_id"]), []).append(anomaly)

        cards: list[dict[str, object]] = []
        for asset in self.repository.load_fixed_assets():
            cards.append(
                self._asset_card(
                    scenario_id=scenario_id,
                    asset_ref=asset.asset_id,
                    object_type="FixedAsset",
                    source_type="CURRENT",
                    name=asset.name,
                    company=asset.company,
                    department=asset.department,
                    cost_center=asset.cost_center,
                    profit_center=asset.profit_center,
                    asset_category=asset.asset_category,
                    depreciation_code=asset.depreciation_code,
                    base_amount=asset.original_cost,
                    in_service_date=str(asset.in_service_date) if asset.in_service_date else None,
                    status=asset.status,
                    amount_row=amount_by_asset.get(asset.asset_id),
                    anomalies=anomalies_by_object.get(asset.asset_id, []),
                )
            )
        for asset in self.repository.load_planned_assets():
            cards.append(
                self._asset_card(
                    scenario_id=scenario_id,
                    asset_ref=asset.planned_asset_id,
                    object_type="PlannedAsset",
                    source_type="PLANNED",
                    name=asset.name,
                    company=asset.company,
                    department=asset.department,
                    cost_center=asset.cost_center,
                    profit_center=asset.profit_center,
                    asset_category=asset.asset_category,
                    depreciation_code=asset.depreciation_code,
                    base_amount=asset.planned_amount,
                    in_service_date=str(asset.expected_in_service_date) if asset.expected_in_service_date else None,
                    status=asset.status,
                    amount_row=amount_by_asset.get(asset.planned_asset_id),
                    anomalies=anomalies_by_object.get(asset.planned_asset_id, []),
                )
            )
        needle = str(search or "").strip().casefold()
        def matches(card: dict[str, object]) -> bool:
            if department and str(card.get("department")) != department:
                return False
            if asset_category and str(card.get("asset_category")) != asset_category:
                return False
            if source_type and str(card.get("asset_source_type")) != source_type:
                return False
            if not needle:
                return True
            searchable = " ".join(
                str(card.get(key) or "")
                for key in ("asset_ref", "name", "department", "asset_category_label_cn", "depreciation_code_label_cn")
            ).casefold()
            return needle in searchable

        return [card for card in cards if matches(card)]

    def asset_detail(self, query: dict[str, list[str]]) -> dict[str, object]:
        """Return the business-facing asset dossier assembled from real result stores."""
        scenario_id = self._str_arg(query, "scenario_id", "BASELINE")
        asset_ref = self._str_arg(query, "asset_ref", "")
        if not asset_ref:
            return {"asset": None, "message_cn": "请先选择一项资产。"}
        asset = next(
            (card for card in self.assets_cards({"scenario_id": [scenario_id]}) if card["asset_ref"] == asset_ref),
            None,
        )
        if asset is None:
            return {"asset": None, "message_cn": f"没有找到资产 {asset_ref}。"}

        forecast_lines = [
            line for line in self.business_store.forecast_lines(scenario_id=scenario_id, limit=10000)
            if str(line.get("asset_id") or line.get("planned_asset_id") or "") == asset_ref
        ]
        forecast_lines.sort(key=lambda line: str(line.get("period") or ""))
        executions = self.business_store.rule_executions(scenario_id=scenario_id, asset_refs=[asset_ref])
        policy = self.policy_narrative(asset_ref, scenario_id)
        applicable_policy = policy.get("applicable_policy") or {}
        source_asset = next(
            (item for item in self.repository.load_fixed_assets() if item.asset_id == asset_ref),
            None,
        )
        driver_type = str(asset.get("depreciation_method") or "")
        driver_target = ""
        if source_asset and driver_type == "PRODUCTION":
            driver_target = source_asset.block_id or ""
        elif source_asset and driver_type == "WORKLOAD":
            driver_target = source_asset.organization_id or source_asset.company
        driver_context: dict[str, object] | None = None
        if driver_target:
            expected_driver_type = "PRODUCTION" if driver_type == "PRODUCTION" else "WORKLOAD"
            driver_rows = [
                item for item in self.repository.baseline_drivers(start_period=self.start_period, months=self.months)
                if item.driver_type == expected_driver_type and item.target_id == driver_target
            ]
            driver_context = {
                "driver_type": expected_driver_type,
                "target_id": driver_target,
                "target_label_cn": (
                    f"区块 {driver_target}" if expected_driver_type == "PRODUCTION"
                    else f"所属单位 {driver_target}"
                ),
                "by_period": {
                    str(item.period): {
                        "production": str(item.production),
                        "reserves": str(item.reserves),
                        "workload": str(item.workload),
                        "unit_fee": str(item.unit_fee),
                        "total_amortization": (
                            str(item.total_amortization) if item.total_amortization is not None else ""
                        ),
                        "assumption_note": item.assumption_note,
                    }
                    for item in driver_rows
                },
            }
        source_context = {
            "block_id": source_asset.block_id if source_asset else None,
            "organization_id": source_asset.organization_id if source_asset else None,
            "useful_life_months": source_asset.useful_life_months if source_asset else applicable_policy.get("useful_life_months"),
            "residual_rate": str(source_asset.residual_rate) if source_asset and source_asset.residual_rate is not None else applicable_policy.get("residual_rate"),
            "start_rule": source_asset.start_rule if source_asset else applicable_policy.get("start_rule"),
        }
        relationships = [
            {"label_cn": "归属所属单位", "value_cn": asset.get("department") or "未登记"},
            {"label_cn": "归属成本中心", "value_cn": asset.get("cost_center") or "未登记"},
            {"label_cn": "登记为资产类别", "value_cn": asset.get("asset_category_label_cn") or "未登记"},
            {"label_cn": "使用折旧码", "value_cn": asset.get("depreciation_code_label_cn") or "未登记"},
            {"label_cn": "适用折旧方法", "value_cn": applicable_policy.get("method_label_cn") or asset.get("depreciation_method_label_cn") or "未匹配"},
            {"label_cn": "适用折旧政策", "value_cn": applicable_policy.get("policy_label_cn") or asset.get("depreciation_policy_label_cn") or "未匹配"},
            {"label_cn": "生成预测明细", "value_cn": f"{len(forecast_lines)} 条月度记录"},
        ]
        return {
            "asset": asset,
            "scenario_id": scenario_id,
            "policy_narrative": policy,
            "relationships": relationships,
            "forecast_lines": forecast_lines,
            "rule_executions": executions,
            "source_context": source_context,
            "driver_context": driver_context,
            "message_cn": "",
        }

    def knowledge_graph(self, query: dict[str, list[str]]) -> dict[str, object]:
        scenario_id = self._str_arg(query, "scenario_id", "BASELINE")
        focus = self._str_arg(query, "focus", "full")
        objects = self.business_store.ontology_objects()
        links = self.business_store.ontology_links()
        nodes, edges = self._filter_ontology_graph(objects, links, focus)
        policy_matches = [self._policy_match_summary(item, scenario_id) for item in self._asset_source_records()]
        risks = self.business_store.anomalies(scenario_id=scenario_id)
        inferred_count = len([item for item in edges if item.get("inferred")])
        meta = self.business_store.ontology_meta()
        return {
            "summary": {
                "scenario_id": scenario_id,
                "focus": focus,
                "node_count": len(nodes),
                "edge_count": len(edges),
                "object_count": len(objects),
                "link_count": len(links),
                "inferred_link_count": inferred_count,
                "triple_count": self.graph_store.count_triples(),
                "inferred_triple_count": self.graph_store.count_triples(inferred=True),
                "policy_match_count": len([item for item in policy_matches if item.get("applicable_policy")]),
                "risk_count": len(risks),
                "action_count": len(meta["action_types"]),
                "function_count": len(meta["function_types"]),
            },
            "object_types": meta["object_types"],
            "nodes": nodes,
            "edges": edges,
            "actions": meta["action_types"],
            "functions": meta["function_types"],
            "policy_matches": policy_matches,
            "risks": risks,
            "lineage": {
                "source_data": "data/customer_snapshot/ 受控客户 Excel",
                "object_store": str(self.business_db_path),
                "graph_store": str(self.graph_db_path),
                "technical_triples_preview": self.graph_store.triples(limit=80),
            },
        }

    def knowledge_graph_node(self, query: dict[str, list[str]]) -> dict[str, object]:
        scenario_id = self._str_arg(query, "scenario_id", "BASELINE")
        node_id = self._str_arg(query, "id", "")
        node = self.business_store.ontology_node(node_id)
        if not node:
            return {"node": None, "message_cn": "没有找到该图谱对象。"}
        adjacent = self.business_store.ontology_adjacent_links(node_id)
        objects_by_id = {item["object_id"]: item for item in self.business_store.ontology_objects()}
        action_ids = set(default_actions_for(str(node["object_type"])))
        meta = self.business_store.ontology_meta()
        actions = [item for item in meta["action_types"] if item["type_id"] in action_ids]
        functions = self._functions_for_node(str(node["object_type"]), meta["function_types"])
        related = []
        for edge in adjacent:
            other_id = edge["target_object_id"] if edge["source_object_id"] == node_id else edge["source_object_id"]
            if other_id in objects_by_id:
                related.append(
                    {
                        "edge": edge,
                        "node": objects_by_id[other_id],
                    }
                )
        return {
            "scenario_id": scenario_id,
            "node": node,
            "adjacent_edges": adjacent,
            "related_nodes": related,
            "actions": actions,
            "functions": functions,
            "risks": self._risks_for_node(node, scenario_id),
            "forecast_summary": self._forecast_summary_for_node(node, scenario_id),
        }

    def knowledge_graph_path(self, query: dict[str, list[str]]) -> dict[str, object]:
        from_id = self._str_arg(query, "from", "")
        to_id = self._str_arg(query, "to", "")
        scenario_id = self._str_arg(query, "scenario_id", "BASELINE")
        objects = {item["object_id"]: item for item in self.business_store.ontology_objects()}
        links = self.business_store.ontology_links()
        if from_id not in objects or to_id not in objects:
            return {
                "scenario_id": scenario_id,
                "from": from_id,
                "to": to_id,
                "path_nodes": [],
                "path_edges": [],
                "narrative_cn": "没有找到完整路径，请确认起点和终点对象是否存在。",
            }
        path_edges = self._bfs_path(from_id, to_id, links)
        path_nodes = []
        if path_edges:
            ids = [from_id]
            for edge in path_edges:
                ids.append(edge["target_object_id"] if ids[-1] == edge["source_object_id"] else edge["source_object_id"])
            path_nodes = [objects[item] for item in ids if item in objects]
        narrative = self._path_narrative(path_nodes, path_edges)
        return {
            "scenario_id": scenario_id,
            "from": from_id,
            "to": to_id,
            "path_nodes": path_nodes,
            "path_edges": path_edges,
            "narrative_cn": narrative,
        }

    def _refresh_ontology_model(self) -> None:
        objects: dict[str, ObjectInstance] = {}
        links: dict[str, LinkInstance] = {}
        baseline_asset_amounts = self.business_store.asset_card_amounts("BASELINE")

        def put_object(instance: ObjectInstance) -> None:
            objects[instance.object_id] = instance

        def put_link(
            link_type: str,
            source: str,
            target: str,
            label_cn: str,
            business_text: str,
            *,
            inferred: bool = False,
            evidence: dict[str, object] | None = None,
        ) -> None:
            if source not in objects or target not in objects:
                return
            link_id = f"{link_type}:{source}->{target}"
            links[link_id] = LinkInstance(
                link_id=link_id,
                link_type=link_type,
                source_object_id=source,
                target_object_id=target,
                label_cn=label_cn,
                business_text=business_text,
                inferred=inferred,
                evidence=evidence or {},
            )

        rule_definitions = {
            "STRAIGHT_LINE": ("年限平均法", "月折旧 = 剩余可折旧金额 ÷ 剩余折旧月数"),
            "PRODUCTION": ("产量法", "月折耗 = 期初净值 × 当月产量 ÷ 剩余储量"),
            "WORKLOAD": ("工作量法", "月摊销 = 当月总摊销额 × 资产期初净值 ÷ 资产池期初净额"),
        }
        for method_id, (method_name, formula_cn) in rule_definitions.items():
            put_object(ObjectInstance(
                object_id=object_id("DepreciationMethod", method_id), object_type="DepreciationMethod",
                label_cn=method_name, subtitle_cn="折旧规则文档定义的计算方法。",
                properties={"method_id": method_id, "name": method_name}, source_system="资产价值计算规则及示例", technical_ref=method_id,
            ))
            rule_id = f"RULE-{method_id}"
            put_object(ObjectInstance(
                object_id=object_id("CalculationRule", rule_id), object_type="CalculationRule",
                label_cn=f"{method_name}计算规则", subtitle_cn=formula_cn,
                properties={"rule_id": rule_id, "formula_cn": formula_cn, "description_cn": "由规则引擎执行并留下计算追溯。"},
                source_system="资产价值计算规则及示例", technical_ref=rule_id,
            ))
            put_link("methodUsesRule", object_id("DepreciationMethod", method_id), object_id("CalculationRule", rule_id),
                     "方法包含规则", f"{method_name}使用该计算规则。")

        for category in self.repository.load_asset_categories():
            put_object(
                ObjectInstance(
                    object_id=object_id("AssetCategory", category.category_id),
                    object_type="AssetCategory",
                    label_cn=category_label(category.category_id),
                    subtitle_cn=f"资产类别：{category.name}",
                    properties={
                        "category_id": category.category_id,
                        "name": category.name,
                        "parent_id": category.parent_id,
                        "parent_label_cn": category_label(category.parent_id),
                    },
                    source_system="客户资产台账 / 资产相关配置表",
                    technical_ref=category.category_id,
                )
            )
        for category in self.repository.load_asset_categories():
            if category.parent_id:
                put_link(
                    "categoryInheritsCategory",
                    object_id("AssetCategory", category.category_id),
                    object_id("AssetCategory", category.parent_id),
                    "继承上级类别",
                    f"{category_label(category.category_id)} 属于 {category_label(category.parent_id)}，可继承上级类别的政策覆盖。",
                    inferred=True,
                    evidence={"source": "客户资产相关配置表", "parent_id": category.parent_id},
                )

        for policy in self.repository.load_depreciation_policies():
            put_object(
                ObjectInstance(
                    object_id=object_id("DepreciationPolicy", policy.policy_id),
                    object_type="DepreciationPolicy",
                    label_cn=policy_label(policy.policy_id),
                    subtitle_cn=(
                        f"{method_label(policy.method)} / {policy.useful_life_months} 个月 / "
                        f"残值率 {percent_label(policy.residual_rate)} / {start_rule_label(policy.start_rule)}"
                    ),
                    properties={
                        "policy_id": policy.policy_id,
                        "name": policy.name,
                        "company": policy.company,
                        "perspective": policy.perspective,
                        "asset_category": policy.asset_category,
                        "asset_category_label_cn": category_label(policy.asset_category),
                        "method": policy.method,
                        "method_label_cn": method_label(policy.method),
                        "useful_life_months": policy.useful_life_months,
                        "residual_rate": str(policy.residual_rate),
                        "residual_rate_label_cn": percent_label(policy.residual_rate),
                        "start_rule": policy.start_rule,
                        "start_rule_label_cn": start_rule_label(policy.start_rule),
                    },
                    source_system="客户资产相关配置表",
                    technical_ref=policy.policy_id,
                )
            )
            put_link(
                "policyAppliesToCategory",
                object_id("DepreciationPolicy", policy.policy_id),
                object_id("AssetCategory", policy.asset_category),
                "政策适用于类别",
                f"{policy_label(policy.policy_id)} 适用于 {category_label(policy.asset_category)}。",
                evidence={"company": policy.company, "perspective": policy.perspective},
            )

        for code in self.repository.load_depreciation_codes():
            put_object(
                ObjectInstance(
                    object_id=object_id("DepreciationCode", code.code_id),
                    object_type="DepreciationCode",
                    label_cn=depreciation_code_label(code.code_id),
                    subtitle_cn=f"允许用于 {category_label(code.asset_category)}，映射 {policy_label(code.policy_id)}",
                    properties={
                        "code_id": code.code_id,
                        "name": code.name,
                        "asset_category": code.asset_category,
                        "asset_category_label_cn": category_label(code.asset_category),
                        "policy_id": code.policy_id,
                        "policy_label_cn": policy_label(code.policy_id),
                    },
                    source_system="客户资产相关配置表",
                    technical_ref=code.code_id,
                )
            )
            put_link(
                "codeMapsToPolicy",
                object_id("DepreciationCode", code.code_id),
                object_id("DepreciationPolicy", code.policy_id),
                "折旧码映射政策",
                f"{depreciation_code_label(code.code_id)} 映射到 {policy_label(code.policy_id)}。",
                evidence={"source": "客户资产相关配置表"},
            )
            policy = next((item for item in self.repository.load_depreciation_policies() if item.policy_id == code.policy_id), None)
            if policy:
                put_link("codeUsesMethod", object_id("DepreciationCode", code.code_id), object_id("DepreciationMethod", policy.method),
                         "折旧码对应方法", f"{code.code_id} 对应 {method_label(policy.method)}。")

        for asset in self.repository.load_fixed_assets():
            self._put_organization_objects(objects, asset.department, asset.cost_center, asset.profit_center)
            put_object(
                ObjectInstance(
                    object_id=object_id("FixedAsset", asset.asset_id),
                    object_type="FixedAsset",
                    label_cn=f"存量资产 {asset.asset_id}",
                    subtitle_cn=asset.name,
                    properties={
                        "asset_ref": asset.asset_id,
                        "name": asset.name,
                        "company": asset.company,
                        "department": asset.department,
                        "cost_center": asset.cost_center,
                        "profit_center": asset.profit_center,
                        "asset_category": asset.asset_category,
                        "asset_category_label_cn": category_label(asset.asset_category),
                        "depreciation_code": asset.depreciation_code,
                        "depreciation_code_label_cn": depreciation_code_label(asset.depreciation_code),
                        "original_cost": f"{asset.original_cost:.2f}",
                        "in_service_date": str(asset.in_service_date) if asset.in_service_date else None,
                        "status": asset.status,
                    },
                    source_system="客户资产明细表",
                    technical_ref=asset.asset_id,
                    metrics=baseline_asset_amounts.get(asset.asset_id, {}),
                )
            )
            self._link_asset_dimensions(put_link, "FixedAsset", asset.asset_id, asset.department, asset.cost_center, asset.profit_center, asset.asset_category, asset.depreciation_code)
            if asset.block_id:
                block_object_id = object_id("Block", asset.block_id)
                put_object(ObjectInstance(
                    object_id=block_object_id, object_type="Block", label_cn=f"区块 {asset.block_id}",
                    subtitle_cn="产量法折耗的业务归属区块。", properties={"block_id": asset.block_id, "company": asset.company},
                    source_system="资产台账/所属区块", technical_ref=asset.block_id,
                ))
                put_link("assetBelongsToBlock", object_id("FixedAsset", asset.asset_id), block_object_id,
                         "资产属于区块", f"{asset.asset_id} 属于区块 {asset.block_id}。")

        if self.is_customer_data:
            for driver in self.repository.baseline_drivers(start_period=self.start_period, months=self.months):
                raw_id = f"{driver.driver_type}:{driver.target_id}:{driver.period}"
                driver_object_id = object_id("MonthlyDriver", raw_id)
                label = "区块产量/储量" if driver.driver_type == "PRODUCTION" else "工作量/单位费用"
                put_object(ObjectInstance(
                    object_id=driver_object_id, object_type="MonthlyDriver", label_cn=f"{driver.period} {label}",
                    subtitle_cn=driver.assumption_note, properties={"period": str(driver.period), "driver_type": driver.driver_type,
                    "target_id": driver.target_id, "production": str(driver.production), "reserves": str(driver.reserves),
                    "workload": str(driver.workload), "unit_fee": str(driver.unit_fee)}, source_system="基准场景假设", technical_ref=raw_id,
                ))
                if driver.driver_type == "PRODUCTION":
                    put_link("blockHasMonthlyDriver", object_id("Block", driver.target_id), driver_object_id,
                             "区块具有月度参数", f"区块 {driver.target_id} 在 {driver.period} 使用该产量和储量参数。")
                put_link("driverAffectsMethod", driver_object_id, object_id("DepreciationMethod", driver.driver_type),
                         "参数影响折旧方法", f"{driver.period} 的业务参数影响 {method_label(driver.driver_type)} 计算。")

        for asset in self.repository.load_planned_assets():
            self._put_organization_objects(objects, asset.department, asset.cost_center, asset.profit_center)
            put_object(
                ObjectInstance(
                    object_id=object_id("PlannedAsset", asset.planned_asset_id),
                    object_type="PlannedAsset",
                    label_cn=f"计划资产 {asset.planned_asset_id}",
                    subtitle_cn=asset.name,
                    properties={
                        "asset_ref": asset.planned_asset_id,
                        "name": asset.name,
                        "company": asset.company,
                        "department": asset.department,
                        "cost_center": asset.cost_center,
                        "profit_center": asset.profit_center,
                        "asset_category": asset.asset_category,
                        "asset_category_label_cn": category_label(asset.asset_category),
                        "depreciation_code": asset.depreciation_code,
                        "depreciation_code_label_cn": depreciation_code_label(asset.depreciation_code),
                        "planned_amount": f"{asset.planned_amount:.2f}",
                        "expected_in_service_date": str(asset.expected_in_service_date) if asset.expected_in_service_date else None,
                        "budget_version": asset.budget_version,
                        "status": asset.status,
                    },
                    source_system="当前测算场景假设",
                    technical_ref=asset.planned_asset_id,
                    metrics=baseline_asset_amounts.get(asset.planned_asset_id, {}),
                )
            )
            self._link_asset_dimensions(put_link, "PlannedAsset", asset.planned_asset_id, asset.department, asset.cost_center, asset.profit_center, asset.asset_category, asset.depreciation_code)

        for event in self.repository.load_asset_events():
            event_label = EVENT_LABEL_CN.get(event.event_type, event.event_type)
            put_object(
                ObjectInstance(
                    object_id=object_id("AssetEvent", event.event_id),
                    object_type="AssetEvent",
                    label_cn=f"{event_label} {event.event_id}",
                    subtitle_cn=event.description,
                    properties={
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "event_type_label_cn": event_label,
                        "target_asset_id": event.target_asset_id,
                        "target_planned_asset_id": event.target_planned_asset_id,
                        "department": event.department,
                        "amount": f"{event.amount:.2f}",
                        "effective_date": str(event.effective_date),
                        "budget_version": event.budget_version,
                        "description": event.description,
                    },
                    source_system="当前测算场景假设",
                    technical_ref=event.event_id,
                )
            )
            target_type = "FixedAsset" if event.target_asset_id else "PlannedAsset"
            target_ref = event.target_asset_id or event.target_planned_asset_id or ""
            put_link(
                "eventAffectsAsset",
                object_id("AssetEvent", event.event_id),
                object_id(target_type, target_ref),
                "事件影响资产",
                f"{event_label}事件 {event.event_id} 影响 {target_ref}，金额 {event.amount:.2f}。",
                evidence={"effective_date": str(event.effective_date)},
            )

        for scenario in self.business_store.scenarios():
            scenario_id = str(scenario["scenario_id"])
            put_object(
                ObjectInstance(
                    object_id=object_id("Scenario", scenario_id),
                    object_type="Scenario",
                    label_cn=f"测算场景 {scenario_id}",
                    subtitle_cn=str(scenario["description"]),
                    properties=scenario,
                    source_system="business_result_store.scenarios",
                    technical_ref=scenario_id,
                )
            )
            for index, assumption in enumerate(self.business_store.scenario_assumptions(scenario_id), start=1):
                assumption_id = f"{scenario_id}:ASM-{index:03d}"
                template_id = str(assumption.get("template_id") or "")
                method_id = "PRODUCTION" if template_id == "production_driver" else "WORKLOAD" if template_id == "workload_driver" else "STRAIGHT_LINE"
                put_object(ObjectInstance(
                    object_id=object_id("ScenarioAssumption", assumption_id), object_type="ScenarioAssumption",
                    label_cn=str(assumption.get("label_cn") or template_id or "场景假设"),
                    subtitle_cn=str(assumption.get("note") or "业务人员输入的 What-if 假设。"),
                    properties=assumption, source_system="scenario_assumptions", technical_ref=assumption_id,
                ))
                put_link("scenarioContainsAssumption", object_id("Scenario", scenario_id), object_id("ScenarioAssumption", assumption_id),
                         "场景包含假设", f"{scenario.get('scenario_name') or scenario_id} 包含 {template_id} 假设。")
                put_link("assumptionTriggersRule", object_id("ScenarioAssumption", assumption_id), object_id("CalculationRule", f"RULE-{method_id}"),
                         "假设触发规则", f"该假设触发{method_label(method_id)}计算规则。")
            for asset_ref, amount_row in self.business_store.asset_card_amounts(scenario_id).items():
                forecast_id = f"{scenario_id}:{asset_ref}"
                forecast_object_id = object_id("ForecastLine", forecast_id)
                put_object(
                    ObjectInstance(
                        object_id=forecast_object_id,
                        object_type="ForecastLine",
                        label_cn=f"预测摘要 {asset_ref}",
                        subtitle_cn=f"{scenario_id} / {amount_row.get('forecast_month_count', 0)} 个月",
                        properties={
                            "scenario_id": scenario_id,
                            "asset_ref": asset_ref,
                            "asset_source_type": amount_row.get("asset_source_type"),
                            "department": amount_row.get("department"),
                            "asset_category": amount_row.get("asset_category"),
                            "asset_category_label_cn": category_label(amount_row.get("asset_category")),
                            "depreciation_policy": amount_row.get("depreciation_policy"),
                            "depreciation_policy_label_cn": policy_label(amount_row.get("depreciation_policy")),
                            "first_depreciation_period": amount_row.get("first_depreciation_period"),
                            "last_forecast_period": amount_row.get("last_forecast_period"),
                        },
                        metrics={
                            "forecast_depreciation_total": amount_row.get("forecast_depreciation_total"),
                            "ending_net_value": amount_row.get("ending_net_value"),
                            "forecast_month_count": amount_row.get("forecast_month_count"),
                        },
                        source_system="business_result_store.forecast_lines",
                        technical_ref=forecast_id,
                    )
                )
                put_link(
                    "scenarioContainsForecast",
                    object_id("Scenario", scenario_id),
                    forecast_object_id,
                    "场景包含预测",
                    f"{scenario_id} 场景生成 {asset_ref} 的折旧预测摘要。",
                    evidence={"scenario_id": scenario_id},
                )
                source_type = "PlannedAsset" if amount_row.get("asset_source_type") == "PLANNED" else "FixedAsset"
                put_link(
                    "forecastForAsset",
                    forecast_object_id,
                    object_id(source_type, str(asset_ref)),
                    "预测对应资产",
                    f"{asset_ref} 的预测摘要来自 {scenario_id} 场景折旧计算结果。",
                    evidence={"source_table": "forecast_lines"},
                )

            for anomaly in self.business_store.anomalies(scenario_id=scenario_id):
                anomaly_object_id = object_id("Anomaly", str(anomaly["anomaly_id"]))
                put_object(
                    ObjectInstance(
                        object_id=anomaly_object_id,
                        object_type="Anomaly",
                        label_cn=str(anomaly.get("rule_label_cn") or anomaly["rule_id"]),
                        subtitle_cn=str(anomaly.get("message_cn") or anomaly["message"]),
                        properties=anomaly,
                        source_system="business_result_store.anomalies",
                        technical_ref=str(anomaly["anomaly_id"]),
                    )
                )
                put_link(
                    "anomalyRaisedInScenario",
                    anomaly_object_id,
                    object_id("Scenario", scenario_id),
                    "异常发生在场景",
                    f"{anomaly.get('rule_label_cn')} 发生在 {scenario_id} 场景。",
                    evidence={"severity": anomaly.get("severity")},
                )
                target_type = str(anomaly.get("object_type") or "")
                target_ref = str(anomaly.get("object_id") or "")
                put_link(
                    "anomalyAffectsObject",
                    anomaly_object_id,
                    object_id(target_type, target_ref),
                    "异常影响对象",
                    f"{anomaly.get('message_cn')} 影响对象 {target_ref}。",
                    evidence={"rule_id": anomaly.get("rule_id"), "suggestion_cn": anomaly.get("suggestion_cn")},
                )

        self.business_store.save_ontology_model(
            object_types=OBJECT_TYPES,
            link_types=LINK_TYPES,
            action_types=ACTION_TYPES,
            function_types=FUNCTION_TYPES,
            objects=sorted(objects.values(), key=lambda item: item.object_id),
            links=sorted(links.values(), key=lambda item: item.link_id),
        )

    def _put_organization_objects(self, objects: dict[str, ObjectInstance], department: str, cost_center: str, profit_center: str) -> None:
        for object_type, value, label in (
            ("Department", department, "部门"),
            ("CostCenter", cost_center, "成本中心"),
            ("ProfitCenter", profit_center, "利润中心"),
        ):
            objects.setdefault(
                object_id(object_type, value),
                ObjectInstance(
                    object_id=object_id(object_type, value),
                    object_type=object_type,
                    label_cn=f"{label} {value}",
                    subtitle_cn=f"资产预算归集维度：{value}",
                    properties={"code": value, "name": value},
                    source_system="asset_master_data",
                    technical_ref=value,
                ),
            )

    def _link_asset_dimensions(self, put_link, object_type: str, asset_ref: str, department: str, cost_center: str, profit_center: str, category: str, code: str) -> None:
        source = object_id(object_type, asset_ref)
        put_link("assetBelongsToDepartment", source, object_id("Department", department), "属于部门", f"{asset_ref} 由 {department} 负责预算。")
        put_link("assetBelongsToCostCenter", source, object_id("CostCenter", cost_center), "归集到成本中心", f"{asset_ref} 的折旧费用归集到成本中心 {cost_center}。")
        put_link("assetBelongsToProfitCenter", source, object_id("ProfitCenter", profit_center), "归属利润中心", f"{asset_ref} 归属利润中心 {profit_center}。")
        put_link("assetHasCategory", source, object_id("AssetCategory", category), "登记为资产类别", f"{asset_ref} 登记为 {category_label(category)}。")
        put_link("assetUsesDepreciationCode", source, object_id("DepreciationCode", code), "使用折旧码", f"{asset_ref} 使用 {depreciation_code_label(code)}。")

    def _filter_ontology_graph(self, objects: list[dict[str, object]], links: list[dict[str, object]], focus: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        include_types = {
            "business": {"FixedAsset", "PlannedAsset", "Department", "CostCenter", "ProfitCenter", "AssetCategory", "DepreciationCode", "DepreciationPolicy", "DepreciationMethod", "CalculationRule", "Block", "MonthlyDriver"},
            "policy": {"FixedAsset", "PlannedAsset", "AssetCategory", "DepreciationCode", "DepreciationPolicy", "DepreciationMethod", "CalculationRule"},
            "exception": {"Anomaly"},
            "scenario": {"Scenario", "ScenarioAssumption", "ForecastLine", "FixedAsset", "PlannedAsset", "DepreciationPolicy", "CalculationRule", "MonthlyDriver"},
        }.get(focus)
        nodes_by_id = {str(item["object_id"]): self._graph_node_payload(item) for item in objects}
        if include_types:
            included = {
                node_id
                for node_id, node in nodes_by_id.items()
                if str(node.get("object_type")) in include_types
            }
            if focus == "exception":
                for link in links:
                    if link["source_object_id"] in included or link["target_object_id"] in included:
                        included.add(str(link["source_object_id"]))
                        included.add(str(link["target_object_id"]))
            if focus == "policy":
                for link in links:
                    if str(link.get("link_type")) in {"assetHasCategory", "assetUsesDepreciationCode", "codeMapsToPolicy", "policyAppliesToCategory", "categoryInheritsCategory"}:
                        included.add(str(link["source_object_id"]))
                        included.add(str(link["target_object_id"]))
            if focus == "scenario":
                for link in links:
                    if link["source_object_id"] in included or link["target_object_id"] in included:
                        included.add(str(link["source_object_id"]))
                        included.add(str(link["target_object_id"]))
        else:
            included = set(nodes_by_id)
        edges = [
            self._graph_edge_payload(link)
            for link in links
            if str(link["source_object_id"]) in included and str(link["target_object_id"]) in included
        ]
        edge_nodes = {str(edge["source"]) for edge in edges} | {str(edge["target"]) for edge in edges}
        selected_nodes = [
            node
            for node_id, node in nodes_by_id.items()
            if node_id in included and (node_id in edge_nodes or focus in {"full", "business"})
        ]
        return (
            sorted(selected_nodes, key=lambda item: (str(item["object_type"]), str(item["id"]))),
            sorted(edges, key=lambda item: str(item["id"])),
        )

    def _graph_node_payload(self, row: dict[str, object]) -> dict[str, object]:
        object_type = str(row["object_type"])
        return {
            "id": row["object_id"],
            "object_type": object_type,
            "type": object_type,
            "type_label_cn": object_type_label(object_type),
            "label_cn": row["label_cn"],
            "label": row["label_cn"],
            "subtitle_cn": row.get("subtitle_cn"),
            "properties": row.get("properties") or {},
            "metrics": row.get("metrics") or {},
            "source_system": row.get("source_system"),
            "technical_ref": row.get("technical_ref"),
        }

    def _graph_edge_payload(self, row: dict[str, object]) -> dict[str, object]:
        return {
            "id": row["link_id"],
            "source": row["source_object_id"],
            "target": row["target_object_id"],
            "link_type": row["link_type"],
            "label_cn": row["label_cn"],
            "business_text": row["business_text"],
            "inferred": bool(row["inferred"]),
            "evidence": row.get("evidence") or {},
        }

    def _functions_for_node(self, object_type: str, functions: list[dict[str, object]]) -> list[dict[str, object]]:
        ids_by_type = {
            "FixedAsset": {"calculateDepreciation", "explainPolicyMatch", "summarizeForecast", "traceKnowledgeGraph"},
            "PlannedAsset": {"calculateDepreciation", "explainPolicyMatch", "summarizeForecast", "traceKnowledgeGraph"},
            "DepreciationPolicy": {"explainPolicyMatch", "calculateDepreciation", "traceKnowledgeGraph"},
            "Scenario": {"compareScenarios", "summarizeForecast", "traceKnowledgeGraph"},
            "ForecastLine": {"summarizeForecast", "traceKnowledgeGraph"},
            "Anomaly": {"traceKnowledgeGraph"},
        }
        wanted = ids_by_type.get(object_type, {"traceKnowledgeGraph"})
        return [item for item in functions if item["type_id"] in wanted]

    def _risks_for_node(self, node: dict[str, object], scenario_id: str) -> list[dict[str, object]]:
        properties = node.get("properties") or {}
        technical_ref = str(node.get("technical_ref") or properties.get("asset_ref") or "")
        if node.get("object_type") == "Anomaly":
            return [properties]
        return [
            risk
            for risk in self.business_store.anomalies(scenario_id=scenario_id)
            if str(risk.get("object_id")) == technical_ref
        ]

    def _forecast_summary_for_node(self, node: dict[str, object], scenario_id: str) -> dict[str, object] | None:
        object_type = str(node.get("object_type") or "")
        if object_type not in {"FixedAsset", "PlannedAsset", "ForecastLine"}:
            return None
        properties = node.get("properties") or {}
        asset_ref = str(properties.get("asset_ref") or node.get("technical_ref") or "")
        if object_type == "ForecastLine":
            asset_ref = str(properties.get("asset_ref") or "")
        return self.business_store.asset_card_amounts(scenario_id).get(asset_ref)

    def _bfs_path(self, from_id: str, to_id: str, links: list[dict[str, object]]) -> list[dict[str, object]]:
        adjacency: dict[str, list[tuple[str, dict[str, object]]]] = {}
        for link in links:
            source = str(link["source_object_id"])
            target = str(link["target_object_id"])
            adjacency.setdefault(source, []).append((target, link))
            adjacency.setdefault(target, []).append((source, link))
        queue: list[tuple[str, list[dict[str, object]]]] = [(from_id, [])]
        visited = {from_id}
        while queue:
            current, path = queue.pop(0)
            if current == to_id:
                return path
            for neighbor, edge in adjacency.get(current, []):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                queue.append((neighbor, [*path, edge]))
        return []

    def _path_narrative(self, nodes: list[dict[str, object]], edges: list[dict[str, object]]) -> str:
        if not nodes or not edges:
            return "没有找到可解释路径。"
        node_text = " → ".join(str(node.get("label_cn") or node.get("object_id")) for node in nodes)
        edge_text = "；".join(str(edge.get("business_text") or edge.get("label_cn")) for edge in edges)
        return f"路径：{node_text}。业务含义：{edge_text}"

    def policy_narrative(self, asset_ref: str, scenario_id: str = "BASELINE") -> dict[str, object]:
        proof = self.policy_proof(asset_ref, scenario_id)
        # Customer ledger codes are the authoritative policy selector. Category inheritance
        # remains a graph explanation aid and must not override the actual ledger code.
        policy_id = (proof.get("asset") or {}).get("depreciation_policy")
        if not policy_id:
            policy_id = proof.get("policy_match", {}).get("policy_id") if proof.get("policy_match") else None
        policy = None
        for item in self.repository.load_depreciation_policies():
            if item.policy_id == policy_id:
                policy = item
                break
        first_line = next((line for line in proof.get("first_lines", []) if Decimal(str(line.get("monthly_depreciation", "0"))) > 0), None)
        asset = proof.get("asset") or {}
        category_chain = proof.get("category_chain", [])
        category_label_cn = category_label(str(asset.get("asset_category") or ""))
        chain_text = " 属于 ".join(category_label(item) for item in category_chain) if category_chain else "未找到类别链"
        policy_text = (
            f"该资产登记为{category_label_cn}，类别链路为：{chain_text}。"
        )
        if policy is not None:
            policy_text += (
                f"系统据此匹配{policy_label(policy.policy_id)}："
                f"{method_label(policy.method)}，使用年限 {policy.useful_life_months} 个月，"
                f"残值率 {percent_label(policy.residual_rate)}，{start_rule_label(policy.start_rule)}。"
            )
        if first_line:
            policy_text += (
                f"因此在当前预测中，该资产从 {first_line['period']} 起每月计提 {first_line['monthly_depreciation']}。"
            )
        amount_row = self.business_store.asset_card_amounts(scenario_id).get(asset_ref, {})
        applicable_policy = None
        if policy is not None:
            applicable_policy = {
                "policy_id": policy.policy_id,
                "policy_label_cn": policy_label(policy.policy_id),
                "company": policy.company,
                "perspective": policy.perspective,
                "asset_category": policy.asset_category,
                "asset_category_label_cn": category_label(policy.asset_category),
                "method": policy.method,
                "method_label_cn": method_label(policy.method),
                "useful_life_months": policy.useful_life_months,
                "residual_rate": str(policy.residual_rate),
                "residual_rate_label_cn": percent_label(policy.residual_rate),
                "start_rule": policy.start_rule,
                "start_rule_label_cn": start_rule_label(policy.start_rule),
            }
        basis_items = [
            {"label_cn": "资产类别", "value": asset.get("asset_category"), "value_label_cn": category_label_cn},
            {"label_cn": "折旧码", "value": asset.get("depreciation_code"), "value_label_cn": depreciation_code_label(asset.get("depreciation_code"))},
            {"label_cn": "公司", "value": (proof.get("policy_match") or {}).get("company")},
            {"label_cn": "测算口径", "value": (proof.get("policy_match") or {}).get("perspective"), "value_label_cn": "预算口径"},
            {"label_cn": "类别继承链", "value": category_chain, "value_label_cn": chain_text},
        ]
        match_path = [
            {
                "category_id": item,
                "category_label_cn": category_label(item),
                "step_label_cn": "资产登记类别" if index == 0 else "上级类别",
            }
            for index, item in enumerate(category_chain)
        ]
        calculation_impact = {
            "scenario_id": scenario_id,
            "first_depreciation_period": amount_row.get("first_depreciation_period") or (first_line or {}).get("period"),
            "monthly_depreciation_at_start": (first_line or {}).get("monthly_depreciation"),
            "forecast_depreciation_total": amount_row.get("forecast_depreciation_total", "0.00"),
            "ending_net_value": amount_row.get("ending_net_value", "0.00"),
            "calculation_rule_id": (first_line or {}).get("calculation_rule_id"),
            "calculation_rule_label_cn": calculation_rule_label((first_line or {}).get("calculation_rule_id")),
        }
        diagnostics = {
            "源数据": proof.get("source_context") or {
                "asset_ref": asset_ref,
                "department": asset.get("department"),
                "asset_category": asset.get("asset_category"),
                "depreciation_code": asset.get("depreciation_code"),
            },
            "图谱推理": category_chain,
            "政策匹配": proof.get("policy_match"),
            "计算规则": first_line.get("calculation_rule_id") if first_line else None,
            "落库结果": {
                "scenario_id": scenario_id,
                "preview_line_count": len(proof.get("first_lines", [])),
            },
        }
        return {
            "asset_ref": asset_ref,
            "scenario_id": scenario_id,
            "asset_category_label_cn": category_label_cn,
            "depreciation_code_label_cn": depreciation_code_label(asset.get("depreciation_code")),
            "narrative": policy_text,
            "narrative_cn": policy_text,
            "applicable_policy": applicable_policy,
            "basis_items": basis_items,
            "match_path": match_path,
            "calculation_impact": calculation_impact,
            "technical_details": diagnostics,
            "policy": to_jsonable(policy) if policy else None,
            "category_chain": category_chain,
            "diagnostics": diagnostics,
            "raw_proof": proof,
        }

    def _asset_card(
        self,
        *,
        scenario_id: str,
        asset_ref: str,
        object_type: str,
        source_type: str,
        name: str,
        company: str,
        department: str,
        cost_center: str,
        profit_center: str,
        asset_category: str,
        depreciation_code: str,
        base_amount: Decimal,
        in_service_date: str | None,
        status: str,
        amount_row: dict[str, object] | None,
        anomalies: list[dict[str, object]],
    ) -> dict[str, object]:
        amount_row = amount_row or {}
        is_blocking = any(bool(item.get("is_blocking")) for item in anomalies)
        return {
            "scenario_id": scenario_id,
            "asset_ref": asset_ref,
            "object_type": object_type,
            "object_type_label_cn": OBJECT_TYPE_LABEL_CN.get(object_type, object_type),
            "asset_source_type": source_type,
            "asset_source_type_label_cn": ASSET_SOURCE_TYPE_LABEL_CN.get(source_type, source_type),
            "name": name,
            "company": company,
            "department": department,
            "cost_center": cost_center,
            "profit_center": profit_center,
            "asset_category": asset_category,
            "asset_category_label_cn": category_label(asset_category),
            "depreciation_code": depreciation_code,
            "depreciation_code_label_cn": depreciation_code_label(depreciation_code),
            "depreciation_policy": amount_row.get("depreciation_policy"),
            "depreciation_policy_label_cn": policy_label(amount_row.get("depreciation_policy")),
            "depreciation_method": amount_row.get("depreciation_method"),
            "depreciation_method_label_cn": method_label(amount_row.get("depreciation_method")),
            "base_amount": f"{base_amount:.2f}",
            "original_or_planned_amount": amount_row.get("original_or_planned_amount", "0.00"),
            "forecast_depreciation_total": amount_row.get("forecast_depreciation_total", "0.00"),
            "addition_amount_total": amount_row.get("addition_amount_total", "0.00"),
            "disposal_amount_total": amount_row.get("disposal_amount_total", "0.00"),
            "impairment_amount_total": amount_row.get("impairment_amount_total", "0.00"),
            "ending_net_value": amount_row.get("ending_net_value", "0.00"),
            "first_depreciation_period": amount_row.get("first_depreciation_period"),
            "last_forecast_period": amount_row.get("last_forecast_period"),
            "forecast_month_count": amount_row.get("forecast_month_count", 0),
            "in_service_date": in_service_date,
            "status": status,
            "is_blocking": is_blocking,
            "risk_count": len(anomalies),
            "risks": anomalies,
        }

    def _asset_source_records(self) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for asset in self.repository.load_fixed_assets():
            records.append(
                {
                    "asset_ref": asset.asset_id,
                    "object_type": "FixedAsset",
                    "name": asset.name,
                    "company": asset.company,
                    "department": asset.department,
                    "asset_category": asset.asset_category,
                    "depreciation_code": asset.depreciation_code,
                }
            )
        for asset in self.repository.load_planned_assets():
            records.append(
                {
                    "asset_ref": asset.planned_asset_id,
                    "object_type": "PlannedAsset",
                    "name": asset.name,
                    "company": asset.company,
                    "department": asset.department,
                    "asset_category": asset.asset_category,
                    "depreciation_code": asset.depreciation_code,
                }
            )
        return records

    def _policy_match_summary(self, record: dict[str, object], scenario_id: str) -> dict[str, object]:
        company = str(record.get("company") or "")
        asset_category = str(record.get("asset_category") or "")
        proof = self.graph_store.explain_policy_match(
            company=company,
            perspective=self.perspective,
            asset_category=asset_category,
        )
        return {
            "scenario_id": scenario_id,
            "asset_ref": record.get("asset_ref"),
            "asset_name": record.get("name"),
            "object_type": record.get("object_type"),
            "object_type_label_cn": OBJECT_TYPE_LABEL_CN.get(str(record.get("object_type")), str(record.get("object_type"))),
            "department": record.get("department"),
            "asset_category": asset_category,
            "asset_category_label_cn": category_label(asset_category),
            "depreciation_code": record.get("depreciation_code"),
            "depreciation_code_label_cn": depreciation_code_label(record.get("depreciation_code")),
            "applicable_policy": {
                "policy_id": proof["policy_id"],
                "policy_label_cn": policy_label(proof["policy_id"]),
                "matched_category": proof["matched_category"],
                "matched_category_label_cn": category_label(proof["matched_category"]),
            } if proof else None,
            "match_path": [
                {
                    "subject": item["subject"],
                    "subject_label_cn": graph_node_label(item["subject"]),
                    "predicate": item["predicate"],
                    "predicate_label_cn": GRAPH_PREDICATE_LABEL_CN.get(str(item["predicate"]), str(item["predicate"])),
                    "object": item["object"],
                    "object_label_cn": graph_node_label(item["object"]),
                    "inferred": item["inferred"],
                }
                for item in (proof or {}).get("proof", [])
            ],
        }

    @staticmethod
    def _payload_scenario_id(value: object) -> str:
        if isinstance(value, dict):
            return str(value.get("scenario_id") or value.get("id") or value.get("name") or "")
        return str(value or "")

    def run_what_if(self, payload: dict[str, object]) -> dict[str, object]:
        return self.create_customer_scenario(payload)

    def scenarios(self) -> list[dict[str, object]]:
        return self.business_store.scenarios()

    def scenario_detail(self, scenario_id: str) -> dict[str, object]:
        scenario = self.business_store.scenario(scenario_id)
        if scenario is None:
            raise ValueError(f"场景不存在：{scenario_id}")
        return {
            "scenario": scenario,
            "assumptions": self.business_store.scenario_assumptions(scenario_id),
            "dashboard": self.dashboard(scenario_id),
            "attributions": self.business_store.attributions(scenario_id),
        }

    def delete_scenario(self, scenario_id: str) -> dict[str, object]:
        self.business_store.delete_scenario(scenario_id)
        self._refresh_ontology_model()
        return {"scenario_id": scenario_id, "deleted": True, "message_cn": "场景及其计算结果已删除。"}

    def create_customer_scenario(self, payload: dict[str, object], *, scenario_id: str | None = None) -> dict[str, object]:
        base_scenario_id = str(payload.get("base_scenario_id") or "BASELINE")
        assumptions_value = payload.get("assumptions") or []
        assumptions = assumptions_value if isinstance(assumptions_value, list) else [assumptions_value]
        assumptions = [item for item in assumptions if isinstance(item, dict)]
        if base_scenario_id != "BASELINE" and not payload.get("assumptions_include_base"):
            base_scenario = self.business_store.scenario(base_scenario_id)
            if base_scenario is None:
                raise ValueError(f"基准场景不存在：{base_scenario_id}")
            assumptions = [*self.business_store.scenario_assumptions(base_scenario_id), *assumptions]
        if not assumptions:
            raise ValueError("请至少填写一项规则场景假设。")
        scenario_id = scenario_id or f"SCN-{self.business_store._int_value('select count(*) from scenarios where scenario_id like \'SCN-%\'', ()) + 1:03d}"
        scenario_name = str(payload.get("scenario_name") or scenario_id).strip()
        description = str(payload.get("description") or "基于规则场景模板创建的测算场景。")
        fixed_assets = self.repository.load_fixed_assets()
        planned_assets: list[PlannedAsset] = []
        events: list[AssetEvent] = []
        drivers = self.repository.baseline_drivers(start_period=self.start_period, months=self.months)
        fixed_assets, planned_assets, events, drivers, changes = self._apply_customer_assumptions(
            fixed_assets=fixed_assets, planned_assets=planned_assets, events=events,
            drivers=drivers, assumptions=assumptions,
        )
        result = self._run_forecast(
            scenario_id=scenario_id, budget_version=self.budget_version, start_period=self.start_period,
            months=self.months, fixed_assets=fixed_assets, planned_assets=planned_assets,
            events=events, monthly_drivers=drivers,
        )
        result = self._include_customer_snapshot_lines(
            scenario_id=scenario_id, budget_version=self.budget_version, result=result,
        )
        self.business_store.save_scenario(
            scenario_id=scenario_id, base_scenario_id=base_scenario_id,
            budget_version=self.budget_version, perspective=self.perspective,
            start_period=str(self.start_period), months=self.months, description=description,
        )
        self.business_store.save_scenario_metadata(
            scenario_id=scenario_id, scenario_name=scenario_name, source_snapshot_id=self.snapshot_id,
            calculation_version=self.calculation_version, assumptions=assumptions,
        )
        self.business_store.replace_scenario_results(
            scenario_id=scenario_id, anomalies=result["anomalies"], forecast_lines=result["forecast_lines"],
            summary_lines=result["summary_lines"],
        )
        self.business_store.save_rule_executions(scenario_id=scenario_id, executions=result["rule_executions"])
        baseline_lines = self._forecast_objects(base_scenario_id)
        attributions = AttributionService().attribute_what_if_difference(
            baseline_lines=baseline_lines, scenario_lines=result["forecast_lines"], changes=changes,
        )
        self.business_store.save_what_if(scenario_id=scenario_id, changes=changes, attributions=attributions)
        self._refresh_ontology_model()
        return self.scenario_detail(scenario_id)

    def _apply_customer_assumptions(
        self,
        *,
        fixed_assets: list[FixedAsset],
        planned_assets: list[PlannedAsset],
        events: list[AssetEvent],
        drivers: list[MonthlyDriver],
        assumptions: list[dict[str, object]],
    ) -> tuple[list[FixedAsset], list[PlannedAsset], list[AssetEvent], list[MonthlyDriver], list[WhatIfChange]]:
        asset_by_id = {asset.asset_id: asset for asset in fixed_assets}
        changed_assets = list(fixed_assets)
        changed_drivers = list(drivers)
        changes: list[WhatIfChange] = []
        for index, assumption in enumerate(assumptions, start=1):
            template = str(assumption.get("template_id") or "")
            target_id = str(assumption.get("target_id") or assumption.get("asset_id") or assumption.get("block_id") or "")
            period = str(assumption.get("period") or assumption.get("effective_date") or "")
            change_id = f"ASM-{index:03d}"
            if template == "straight_new_asset":
                reference = asset_by_id.get(str(assumption.get("reference_asset_id") or "")) or next(iter(asset_by_id.values()))
                new_id = str(assumption.get("asset_id") or f"NEW-{index:03d}")
                planned_assets.append(PlannedAsset(
                    planned_asset_id=new_id, name=str(assumption.get("asset_name") or "新增资产"),
                    company=reference.company, department=reference.department, cost_center=reference.cost_center,
                    profit_center=reference.profit_center,
                    asset_category=str(assumption.get("asset_category") or reference.asset_category),
                    depreciation_code=str(assumption.get("depreciation_code") or "Z112"),
                    planned_amount=Decimal(str(assumption.get("amount") or "0")),
                    expected_in_service_date=parse_date(str(assumption.get("in_service_date") or "")),
                    budget_version=self.budget_version, status="SCENARIO",
                ))
                changes.append(WhatIfChange(change_id, "PlannedAsset", new_id, "new_asset", "", str(assumption.get("amount") or "0"), "新增资产进入折旧预测。"))
            elif template == "straight_impairment":
                asset = asset_by_id.get(target_id)
                if asset is None:
                    raise ValueError(f"未找到减值目标资产：{target_id}")
                amount = Decimal(str(assumption.get("amount") or "0"))
                effective = parse_date(str(assumption.get("effective_date") or ""))
                if effective is None:
                    raise ValueError("减值场景需要生效日期。")
                events.append(AssetEvent(change_id, "IMPAIRMENT", asset.asset_id, None, asset.company, asset.department,
                                         asset.cost_center, asset.profit_center, amount, effective, self.budget_version, "规则场景：减值后重算"))
                changes.append(WhatIfChange(change_id, "FixedAsset", asset.asset_id, "impairment_amount", "0", str(amount), "减值后按剩余可折旧金额和剩余期间重算。"))
            elif template in ("straight_accelerated", "straight_start_rule"):
                asset = asset_by_id.get(target_id)
                if asset is None:
                    raise ValueError(f"未找到年限平均法目标资产：{target_id}")
                replacement = asset
                if template == "straight_accelerated":
                    life = max(1, int((asset.useful_life_months or 120) * Decimal("0.6")))
                    replacement = replace(asset, useful_life_months=life)
                    changes.append(WhatIfChange(change_id, "FixedAsset", asset.asset_id, "accelerated_life_months", str(asset.useful_life_months), str(life), "按规则使用原使用年限的 60% 进行加速折旧。"))
                else:
                    start_rule = str(assumption.get("start_rule") or "NEXT_MONTH")
                    replacement = replace(asset, start_rule=start_rule)
                    changes.append(WhatIfChange(change_id, "FixedAsset", asset.asset_id, "start_rule", "", start_rule, "调整开始计提规则。"))
                changed_assets = [replacement if item.asset_id == asset.asset_id else item for item in changed_assets]
                asset_by_id[asset.asset_id] = replacement
            elif template in ("production_driver", "workload_driver"):
                driver_type = "PRODUCTION" if template == "production_driver" else "WORKLOAD"
                driver_target = str(assumption.get("block_id") if driver_type == "PRODUCTION" else assumption.get("company") or "")
                driver_period = str(assumption.get("period") or "")
                if not driver_target or not driver_period:
                    raise ValueError("驱动场景需要目标对象和月份。")
                matched = False
                next_drivers: list[MonthlyDriver] = []
                for driver in changed_drivers:
                    if driver.driver_type == driver_type and driver.target_id == driver_target and str(driver.period) == driver_period:
                        matched = True
                        def scenario_value(field_name: str, baseline: Decimal) -> Decimal:
                            value = assumption.get(field_name)
                            return Decimal(str(baseline if value in (None, "") else value))

                        next_drivers.append(replace(
                            driver,
                            production=scenario_value("production", driver.production),
                            reserves=scenario_value("reserves", driver.reserves),
                            workload=scenario_value("workload", driver.workload),
                            unit_fee=scenario_value("unit_fee", driver.unit_fee),
                            total_amortization=(
                                Decimal(str(assumption["total_amortization"]))
                                if assumption.get("total_amortization") not in (None, "")
                                else driver.total_amortization
                            ),
                            assumption_note="What-if 场景输入。",
                        ))
                    else:
                        next_drivers.append(driver)
                if not matched:
                    company = str(assumption.get("company") or next(iter(asset_by_id.values())).company)
                    next_drivers.append(MonthlyDriver(
                        driver_type=driver_type, period=Month.parse(driver_period), company=company, target_id=driver_target,
                        production=Decimal(str(assumption.get("production") or "0")), reserves=Decimal(str(assumption.get("reserves") or "0")),
                        workload=Decimal(str(assumption.get("workload") or "0")), unit_fee=Decimal(str(assumption.get("unit_fee") or "0")),
                        total_amortization=(
                            Decimal(str(assumption["total_amortization"]))
                            if assumption.get("total_amortization") not in (None, "") else None
                        ),
                        assumption_note="What-if 场景新增驱动输入。",
                    ))
                changed_drivers = next_drivers
                changes.append(WhatIfChange(change_id, "MonthlyDriver", driver_target, template, "基准假设", json.dumps(assumption, ensure_ascii=False), "调整月度业务驱动并重算。"))
            else:
                raise ValueError(f"不支持的规则场景模板：{template}")
        return changed_assets, planned_assets, events, changed_drivers, changes

    @staticmethod
    def _baseline_assumptions(drivers: list[MonthlyDriver]) -> list[dict[str, object]]:
        return [
            {
                "assumption_id": f"BASE-{index + 1:03d}", "template_id": item.driver_type.lower(),
                "target_id": item.target_id, "period": str(item.period), "note": item.assumption_note,
                "production": str(item.production), "reserves": str(item.reserves),
                "workload": str(item.workload), "unit_fee": str(item.unit_fee),
                "total_amortization": str(item.total_amortization) if item.total_amortization is not None else "",
            }
            for index, item in enumerate(drivers)
        ]

    def _run_forecast(
        self,
        *,
        scenario_id: str,
        budget_version: str,
        start_period: Month,
        months: int,
        categories=None,
        policies=None,
        codes=None,
        fixed_assets=None,
        planned_assets=None,
        events=None,
        monthly_drivers=None,
        load_graph: bool = True,
    ) -> dict[str, object]:
        categories = categories if categories is not None else self.repository.load_asset_categories()
        policies = policies if policies is not None else self.repository.load_depreciation_policies()
        codes = codes if codes is not None else self.repository.load_depreciation_codes()
        fixed_assets = fixed_assets if fixed_assets is not None else self.repository.load_fixed_assets()
        planned_assets = planned_assets if planned_assets is not None else self.repository.load_planned_assets()
        events = events if events is not None else self.repository.load_asset_events()
        semantic_model = GraphBackedSemanticModel(
            graph_store=self.graph_store,
            categories=categories,
            policies=policies,
            codes=codes,
            load_graph=load_graph,
        )
        anomalies = [] if self.is_customer_data else DepreciationValidator(semantic_model, self.perspective).validate(
            fixed_assets, planned_assets, events,
        )
        invalid_object_ids = {
            anomaly.object_id for anomaly in anomalies if anomaly.severity == "ERROR"
        }
        monthly_drivers = monthly_drivers if monthly_drivers is not None else (
            self.repository.baseline_drivers(start_period=start_period, months=months)
            if self.is_customer_data else []
        )
        engine = DepreciationCalculationEngine(PolicyResolver(semantic_model, self.perspective))
        forecast_lines = engine.forecast(
            scenario_id=scenario_id,
            budget_version=budget_version,
            start_period=start_period,
            months=months,
            fixed_assets=fixed_assets,
            planned_assets=planned_assets,
            events=events,
            monthly_drivers=monthly_drivers,
            invalid_object_ids=invalid_object_ids,
        )
        summary_lines = DepreciationAggregator().summarize(forecast_lines)
        return {
            "anomalies": anomalies,
            "forecast_lines": forecast_lines,
            "summary_lines": summary_lines,
            "rule_executions": engine.executions,
            "monthly_drivers": monthly_drivers,
        }

    def _forecast_objects(self, scenario_id: str):
        from depreciation_poc.domain.models import ForecastLine

        rows = self.business_store.forecast_lines(
            scenario_id=scenario_id,
            limit=10000,
        )
        objects = []
        for row in rows:
            objects.append(
                ForecastLine(
                    scenario_id=row["scenario_id"],
                    budget_version=row["budget_version"],
                    asset_id=row["asset_id"],
                    planned_asset_id=row["planned_asset_id"],
                    asset_source_type=row["asset_source_type"],
                    company=row["company"],
                    department=row["department"],
                    cost_center=row["cost_center"],
                    profit_center=row["profit_center"],
                    asset_category=row["asset_category"],
                    depreciation_code=row["depreciation_code"],
                    depreciation_policy=row["depreciation_policy"],
                    depreciation_method=row["depreciation_method"],
                    period=Month.parse(row["period"]),
                    opening_original_cost=Decimal(str(row["opening_original_cost"])),
                    opening_accumulated_depreciation=Decimal(str(row["opening_accumulated_depreciation"])),
                    opening_accumulated_impairment=Decimal(str(row["opening_accumulated_impairment"])),
                    opening_net_value=Decimal(str(row["opening_net_value"])),
                    addition_amount=Decimal(str(row["addition_amount"])),
                    disposal_amount=Decimal(str(row["disposal_amount"])),
                    impairment_amount=Decimal(str(row["impairment_amount"])),
                    depreciable_base=Decimal(str(row["depreciable_base"])),
                    monthly_depreciation=Decimal(str(row["monthly_depreciation"])),
                    accumulated_depreciation=Decimal(str(row["accumulated_depreciation"])),
                    closing_net_value=Decimal(str(row["closing_net_value"])),
                    source_event_id=row["source_event_id"],
                    calculation_rule_id=row["calculation_rule_id"],
                    validation_status=row["validation_status"],
                )
            )
        return objects

    @staticmethod
    def _str_arg(query: dict[str, list[str]], name: str, default: str) -> str:
        return query.get(name, [default])[0] or default

    @staticmethod
    def _optional_arg(query: dict[str, list[str]], name: str) -> str | None:
        value = query.get(name, [""])[0]
        return value or None

    @staticmethod
    def _list_arg(query: dict[str, list[str]], name: str) -> list[str]:
        values: list[str] = []
        for item in query.get(name, []):
            values.extend(part.strip() for part in str(item).split(",") if part.strip())
        return values

    @staticmethod
    def _int_arg(query: dict[str, list[str]], name: str, default: int) -> int:
        try:
            return int(query.get(name, [str(default)])[0])
        except ValueError:
            return default

    @staticmethod
    def _optional_int_arg(query: dict[str, list[str]], name: str) -> int | None:
        try:
            value = query.get(name, [""])[0]
            return int(value) if value else None
        except ValueError:
            return None


class DemoRequestHandler(BaseHTTPRequestHandler):
    state: DemoState
    web_dir: Path

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_api(parsed.path, parse_qs(parsed.query))
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in (
            "/api/what-if",
            "/api/what-if/planned-asset",
            "/api/scenarios",
            "/api/scenarios/assumptions",
            "/api/wide-table/compare",
            "/api/wide-table/question",
            "/api/reverse-planning/question",
        ) and not (parsed.path.startswith("/api/scenarios/") and parsed.path.endswith("/assumptions")):
            self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if parsed.path == "/api/wide-table/compare":
                self._json(self.state.wide_table_compare(payload))
            elif parsed.path == "/api/wide-table/question":
                self._json(self.state.ask_wide_table_question(payload))
            elif parsed.path == "/api/reverse-planning/question":
                self._json(self.state.ask_reverse_planning(payload))
            elif parsed.path.startswith("/api/scenarios/") and parsed.path.endswith("/assumptions"):
                scenario_id = parsed.path.split("/")[3]
                existing = self.state.scenario_detail(scenario_id)
                incoming = payload.get("assumptions") or [payload]
                if not isinstance(incoming, list):
                    incoming = [incoming]
                merged = incoming if payload.get("replace_existing") else [*existing["assumptions"], *incoming]
                self._json(self.state.create_customer_scenario({
                    "base_scenario_id": existing["scenario"].get("base_scenario_id") or "BASELINE",
                    "scenario_name": payload.get("scenario_name") or existing["scenario"].get("scenario_name") or scenario_id,
                    "description": payload.get("description") or existing["scenario"].get("description") or "规则场景更新。",
                    "assumptions": merged,
                    "assumptions_include_base": True,
                }, scenario_id=scenario_id))
            else:
                self._json(self.state.run_what_if(payload))
        except ValueError as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if not (parsed.path.startswith("/api/scenarios/") and parsed.path.count("/") == 3):
            self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            scenario_id = parsed.path.rsplit("/", maxsplit=1)[-1]
            self._json(self.state.delete_scenario(scenario_id))
        except ValueError as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_api(self, path: str, query: dict[str, list[str]]) -> None:
        try:
            if path == "/api/dashboard":
                self._json(self.state.dashboard(DemoState._str_arg(query, "scenario_id", "BASELINE")))
            elif path == "/api/source-status":
                self._json(self.state.source_status(DemoState._str_arg(query, "scenario_id", "BASELINE")))
            elif path == "/api/snapshot/status":
                self._json(self.state.snapshot_status())
            elif path == "/api/rule-catalog":
                self._json(self.state.rule_catalog())
            elif path == "/api/scenarios":
                self._json(self.state.scenarios())
            elif path.startswith("/api/scenarios/"):
                self._json(self.state.scenario_detail(path.rsplit("/", maxsplit=1)[-1]))
            elif path == "/api/forecast-lines":
                self._json(self.state.forecast_lines(query))
            elif path == "/api/summaries":
                self._json(self.state.summaries(query))
            elif path == "/api/explain-change":
                self._json(self.state.explain_change(query))
            elif path == "/api/explanation":
                self._json(self.state.explanation(query))
            elif path == "/api/anomalies":
                self._json(self.state.anomalies(query))
            elif path == "/api/assets/cards":
                self._json(self.state.assets_cards(query))
            elif path == "/api/assets/detail":
                self._json(self.state.asset_detail(query))
            elif path == "/api/wide-table":
                self._json(self.state.wide_table(query))
            elif path == "/api/wide-table/dimensions":
                self._json(self.state.wide_table_dimension_catalog())
            elif path == "/api/wide-table/question/catalog":
                self._json(self.state.wide_table_question_catalog(query))
            elif path == "/api/qa/status":
                self._json(self.state.qa_status())
            elif path == "/api/reverse-planning/catalog":
                self._json(self.state.reverse_planning_skill.catalog(DemoState._str_arg(query, "scenario_id", "BASELINE")))
            elif path == "/api/semantic-catalog":
                self._json(self.state.semantic_catalog())
            elif path == "/api/ontology/meta":
                self._json(self.state.ontology_meta())
            elif path == "/api/knowledge-graph":
                self._json(self.state.knowledge_graph(query))
            elif path == "/api/knowledge-graph/node":
                self._json(self.state.knowledge_graph_node(query))
            elif path == "/api/knowledge-graph/path":
                self._json(self.state.knowledge_graph_path(query))
            elif path == "/api/policy-proof":
                self._json(
                    self.state.policy_proof(
                        DemoState._str_arg(query, "asset_ref", ""),
                        DemoState._str_arg(query, "scenario_id", "BASELINE"),
                    )
                )
            elif path == "/api/policy-narrative":
                self._json(
                    self.state.policy_narrative(
                        DemoState._str_arg(query, "asset_ref", ""),
                        DemoState._str_arg(query, "scenario_id", "BASELINE"),
                    )
                )
            elif path == "/api/technical/graph-triples":
                self._json(self.state.graph_triples(DemoState._int_arg(query, "limit", 300)))
            else:
                self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _serve_static(self, path: str) -> None:
        if path in ("", "/"):
            file_path = self.web_dir / "index.html"
        else:
            file_path = (self.web_dir / path.lstrip("/")).resolve()
            if self.web_dir.resolve() not in file_path.parents and file_path != self.web_dir.resolve():
                self.send_error(HTTPStatus.FORBIDDEN)
                return
        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if file_path.suffix.lower() in (".html", ".js", ".css"):
            self.send_header("Cache-Control", "no-store, max-age=0")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def run_server(
    *,
    host: str,
    port: int,
    graph_db_path: Path,
    business_db_path: Path,
    web_dir: Path,
    customer_data_dir: Path,
) -> None:
    audit_log_path = configure_audit_logging(business_db_path.parent / "logs")
    repository = CustomerExcelRepository(customer_data_dir)
    snapshot = repository.source_summary().get("snapshot_period")
    if not snapshot:
        raise ValueError("客户台账缺少快照月份，不能创建预测场景。")
    start_period = Month.parse(str(snapshot)).add(1)
    verified_months = repository.verified_forecast_months(start_period, maximum=6)
    if verified_months <= 0:
        raise ValueError("当前客户配置未覆盖任何未来月份，不能创建基准预测场景。")
    months = verified_months
    DemoRequestHandler.state = DemoState(
        graph_db_path=graph_db_path,
        business_db_path=business_db_path,
        start_period=start_period,
        months=months,
        customer_data_dir=customer_data_dir,
    )
    DemoRequestHandler.web_dir = web_dir
    server = ThreadingHTTPServer((host, port), DemoRequestHandler)
    print(f"Demo server running at http://{host}:{port}")
    print(f"Ontology graph database: {graph_db_path}")
    print(f"Business result database: {business_db_path}")
    print(f"Reverse-planning audit log: {audit_log_path}")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the end-to-end depreciation ontology demo.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--graph-db", default=str(DEFAULT_GRAPH_DB))
    parser.add_argument("--business-db", default=str(DEFAULT_BUSINESS_DB))
    parser.add_argument("--web-dir", default=str(DEFAULT_WEB_DIR))
    parser.add_argument("--customer-data-dir", default=str(DEFAULT_CUSTOMER_DATA_DIR))
    args = parser.parse_args()
    run_server(
        host=args.host,
        port=args.port,
        graph_db_path=Path(args.graph_db),
        business_db_path=Path(args.business_db),
        web_dir=Path(args.web_dir),
        customer_data_dir=Path(args.customer_data_dir),
    )


if __name__ == "__main__":
    main()
