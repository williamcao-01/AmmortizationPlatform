from __future__ import annotations

import sqlite3
import threading
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from depreciation_poc.domain.models import (
    Anomaly,
    AttributionLine,
    ForecastLine,
    RuleExecution,
    SummaryLine,
    WhatIfChange,
)
from depreciation_poc.semantic_labels import decorate_anomaly
from depreciation_poc.ontology_model import (
    ActionTypeDefinition,
    FunctionTypeDefinition,
    LinkInstance,
    LinkTypeDefinition,
    ObjectInstance,
    ObjectTypeDefinition,
)


ZERO = Decimal("0")
MONEY_FIELDS = {
    "opening_original_cost",
    "opening_accumulated_depreciation",
    "opening_accumulated_impairment",
    "opening_net_value",
    "addition_amount",
    "disposal_amount",
    "impairment_amount",
    "depreciable_base",
    "monthly_depreciation",
    "accumulated_depreciation",
    "closing_net_value",
    "monthly_depreciation_sum",
    "addition_depreciation_impact",
    "disposal_depreciation_impact",
    "impairment_depreciation_impact",
    "depreciation",
    "baseline_depreciation",
    "scenario_depreciation",
    "difference",
    "original_or_planned_amount",
    "forecast_depreciation_total",
    "addition_amount_total",
    "disposal_amount_total",
    "impairment_amount_total",
    "ending_net_value",
    "baseline_annual_total",
    "scenario_annual_total",
    "annual_difference",
}


class BusinessResultStore:
    """SQLite-backed result store for the business-facing demo."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def reset(self) -> None:
        with self._lock:
            self.reset_business_data()
            for table in (
                "function_types",
                "action_types",
                "link_types",
                "property_types",
                "object_types",
            ):
                self.connection.execute(f"delete from {table}")
            self.connection.commit()

    def reset_business_data(self) -> None:
        """Clear imported instances and results while retaining ontology metadata tables."""
        with self._lock:
            for table in (
                "ontology_links",
                "ontology_objects",
                "attribution_lines",
                "rule_executions",
                "scenario_assumptions",
                "scenario_metadata",
                "source_snapshots",
                "what_if_changes",
                "summary_lines",
                "forecast_lines",
                "anomalies",
                "scenarios",
            ):
                self.connection.execute(f"delete from {table}")
            self.connection.commit()

    def clear_ontology_data(self) -> None:
        """Remove legacy Ontology metadata and instances from the calculation SQLite database."""
        with self._lock:
            for table in (
                "ontology_links", "ontology_objects", "function_types", "action_types",
                "link_types", "property_types", "object_types",
            ):
                self.connection.execute(f"delete from {table}")
            self.connection.commit()

    def save_ontology_model(
        self,
        *,
        object_types: list[ObjectTypeDefinition],
        link_types: list[LinkTypeDefinition],
        action_types: list[ActionTypeDefinition],
        function_types: list[FunctionTypeDefinition],
        objects: list[ObjectInstance],
        links: list[LinkInstance],
    ) -> None:
        with self._lock:
            for table in (
                "ontology_links",
                "ontology_objects",
                "function_types",
                "action_types",
                "link_types",
                "property_types",
                "object_types",
            ):
                self.connection.execute(f"delete from {table}")
            self.connection.executemany(
                """
                insert into object_types(type_id, label_cn, description_cn)
                values (?, ?, ?)
                """,
                [(item.type_id, item.label_cn, item.description_cn) for item in object_types],
            )
            self.connection.executemany(
                """
                insert into property_types(type_id, property_id, label_cn, value_type)
                values (?, ?, ?, ?)
                """,
                [
                    (item.type_id, prop.property_id, prop.label_cn, prop.value_type)
                    for item in object_types
                    for prop in item.properties
                ],
            )
            self.connection.executemany(
                """
                insert into link_types(type_id, label_cn, source_type, target_type, description_cn)
                values (?, ?, ?, ?, ?)
                """,
                [
                    (item.type_id, item.label_cn, item.source_type, item.target_type, item.description_cn)
                    for item in link_types
                ],
            )
            self.connection.executemany(
                """
                insert into action_types(type_id, label_cn, target_types_json, description_cn)
                values (?, ?, ?, ?)
                """,
                [
                    (item.type_id, item.label_cn, json.dumps(item.target_types, ensure_ascii=False), item.description_cn)
                    for item in action_types
                ],
            )
            self.connection.executemany(
                """
                insert into function_types(type_id, label_cn, description_cn)
                values (?, ?, ?)
                """,
                [(item.type_id, item.label_cn, item.description_cn) for item in function_types],
            )
            self.connection.executemany(
                """
                insert into ontology_objects(
                  object_id, object_type, label_cn, subtitle_cn, properties_json,
                  metrics_json, source_system, technical_ref
                )
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.object_id,
                        item.object_type,
                        item.label_cn,
                        item.subtitle_cn,
                        json.dumps(item.properties, ensure_ascii=False, default=str),
                        json.dumps(item.metrics, ensure_ascii=False, default=str),
                        item.source_system,
                        item.technical_ref,
                    )
                    for item in objects
                ],
            )
            self.connection.executemany(
                """
                insert into ontology_links(
                  link_id, link_type, source_object_id, target_object_id,
                  label_cn, business_text, inferred, evidence_json
                )
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.link_id,
                        item.link_type,
                        item.source_object_id,
                        item.target_object_id,
                        item.label_cn,
                        item.business_text,
                        1 if item.inferred else 0,
                        json.dumps(item.evidence, ensure_ascii=False, default=str),
                    )
                    for item in links
                ],
            )
            self.connection.commit()

    def ontology_meta(self) -> dict[str, Any]:
        object_types = self._all("select * from object_types order by type_id")
        properties = self._all("select * from property_types order by type_id, property_id")
        properties_by_type: dict[str, list[dict[str, Any]]] = {}
        for prop in properties:
            properties_by_type.setdefault(str(prop["type_id"]), []).append(prop)
        for item in object_types:
            item["properties"] = properties_by_type.get(str(item["type_id"]), [])
        action_types = self._all("select * from action_types order by type_id")
        for item in action_types:
            item["target_types"] = json.loads(item.pop("target_types_json") or "[]")
        return {
            "object_types": object_types,
            "link_types": self._all("select * from link_types order by type_id"),
            "action_types": action_types,
            "function_types": self._all("select * from function_types order by type_id"),
        }

    def ontology_objects(self) -> list[dict[str, Any]]:
        rows = self._all("select * from ontology_objects order by object_type, object_id")
        for row in rows:
            row["properties"] = json.loads(row.pop("properties_json") or "{}")
            row["metrics"] = json.loads(row.pop("metrics_json") or "{}")
        return rows

    def ontology_links(self) -> list[dict[str, Any]]:
        rows = self._all("select * from ontology_links order by link_type, source_object_id, target_object_id")
        for row in rows:
            row["inferred"] = bool(row["inferred"])
            row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
        return rows

    def ontology_node(self, object_id: str) -> dict[str, Any] | None:
        row = self._one("select * from ontology_objects where object_id = ?", (object_id,))
        if row is None:
            return None
        result = self._row(row)
        result["properties"] = json.loads(result.pop("properties_json") or "{}")
        result["metrics"] = json.loads(result.pop("metrics_json") or "{}")
        return result

    def ontology_adjacent_links(self, object_id: str) -> list[dict[str, Any]]:
        rows = self._all(
            """
            select *
            from ontology_links
            where source_object_id = ? or target_object_id = ?
            order by link_type, source_object_id, target_object_id
            """,
            (object_id, object_id),
        )
        for row in rows:
            row["inferred"] = bool(row["inferred"])
            row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
        return rows

    def save_scenario(
        self,
        *,
        scenario_id: str,
        budget_version: str,
        perspective: str,
        start_period: str,
        months: int,
        description: str,
        base_scenario_id: str | None = None,
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            self.connection.execute(
                """
                insert or replace into scenarios(
                  scenario_id, base_scenario_id, budget_version, perspective,
                  start_period, months, description, created_at, updated_at
                )
                values (?, ?, ?, ?, ?, ?, ?, coalesce((select created_at from scenarios where scenario_id = ?), ?), ?)
                """,
                (
                    scenario_id,
                    base_scenario_id,
                    budget_version,
                    perspective,
                    start_period,
                    months,
                    description,
                    scenario_id,
                    now,
                    now,
                ),
            )
            self.connection.commit()

    def save_snapshot(self, *, snapshot_id: str, status: dict[str, Any]) -> None:
        with self._lock:
            self.connection.execute(
                "insert or replace into source_snapshots(snapshot_id, status_json, updated_at) values (?, ?, ?)",
                (snapshot_id, json.dumps(status, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")),
            )
            self.connection.commit()

    def snapshot_status(self, snapshot_id: str = "CUSTOMER-2026-08") -> dict[str, Any] | None:
        row = self._one("select status_json, updated_at from source_snapshots where snapshot_id = ?", (snapshot_id,))
        if row is None:
            return None
        status = json.loads(row["status_json"])
        status["updated_at"] = row["updated_at"]
        return status

    def save_scenario_metadata(
        self, *, scenario_id: str, scenario_name: str, source_snapshot_id: str,
        calculation_version: str, assumptions: list[dict[str, Any]],
    ) -> None:
        with self._lock:
            self.connection.execute(
                """insert or replace into scenario_metadata(
                     scenario_id, scenario_name, source_snapshot_id, calculation_version, assumptions_json
                   ) values (?, ?, ?, ?, ?)""",
                (scenario_id, scenario_name, source_snapshot_id, calculation_version,
                 json.dumps(assumptions, ensure_ascii=False)),
            )
            self.connection.execute("delete from scenario_assumptions where scenario_id = ?", (scenario_id,))
            self.connection.executemany(
                """insert into scenario_assumptions(scenario_id, assumption_id, template_id, target_id, period, payload_json)
                   values (?, ?, ?, ?, ?, ?)""",
                [
                    (scenario_id, str(item.get("assumption_id") or f"ASM-{index + 1}"),
                     str(item.get("template_id") or ""), str(item.get("target_id") or ""),
                     str(item.get("period") or ""), json.dumps(item, ensure_ascii=False))
                    for index, item in enumerate(assumptions)
                ],
            )
            self.connection.commit()

    def scenario_assumptions(self, scenario_id: str) -> list[dict[str, Any]]:
        rows = self._all("select payload_json from scenario_assumptions where scenario_id = ? order by assumption_id", (scenario_id,))
        return [json.loads(row["payload_json"]) for row in rows]

    def save_chat_action_draft(
        self,
        *,
        draft_id: str,
        conversation_id: str,
        action_type: str,
        base_scenario_id: str,
        original_instruction: str,
        payload: dict[str, Any],
        preview: dict[str, Any],
        ontology_gateway: dict[str, Any],
        expires_at: str,
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            self.connection.execute(
                """insert into chat_action_drafts(
                     draft_id, conversation_id, action_type, status, base_scenario_id,
                     original_instruction, payload_json, preview_json, ontology_gateway_json,
                     created_at, expires_at, confirmed_at, scenario_id
                   ) values (?, ?, ?, 'PENDING', ?, ?, ?, ?, ?, ?, ?, null, null)""",
                (draft_id, conversation_id, action_type, base_scenario_id, original_instruction,
                 json.dumps(payload, ensure_ascii=False), json.dumps(preview, ensure_ascii=False),
                 json.dumps(ontology_gateway, ensure_ascii=False), now, expires_at),
            )
            self.connection.commit()

    def chat_action_draft(self, draft_id: str) -> dict[str, Any] | None:
        row = self._one("select * from chat_action_drafts where draft_id = ?", (draft_id,))
        if row is None:
            return None
        row = dict(row)
        for key in ("payload_json", "preview_json", "ontology_gateway_json"):
            row[key.removesuffix("_json")] = json.loads(row.pop(key) or "{}")
        return row

    def update_chat_action_draft(self, draft_id: str, *, status: str, scenario_id: str | None = None) -> None:
        confirmed_at = datetime.now().isoformat(timespec="seconds") if status == "CONFIRMED" else None
        with self._lock:
            self.connection.execute(
                """update chat_action_drafts
                   set status = ?, confirmed_at = coalesce(?, confirmed_at), scenario_id = coalesce(?, scenario_id)
                   where draft_id = ?""",
                (status, confirmed_at, scenario_id, draft_id),
            )
            self.connection.commit()

    def save_rule_executions(self, *, scenario_id: str, executions: list[RuleExecution]) -> None:
        with self._lock:
            self.connection.execute("delete from rule_executions where scenario_id = ?", (scenario_id,))
            self.connection.executemany(
                """insert into rule_executions(
                     scenario_id, asset_ref, period, rule_id, branch_id, formula_cn, inputs_json, conclusion_cn
                   ) values (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (item.scenario_id, item.asset_ref, str(item.period), item.rule_id, item.branch_id,
                     item.formula_cn, json.dumps(item.inputs, ensure_ascii=False), item.conclusion_cn)
                    for item in executions
                ],
            )
            self.connection.commit()

    def rule_executions(self, *, scenario_id: str, asset_refs: list[str] | None = None, period: str | None = None) -> list[dict[str, Any]]:
        where, values = ["scenario_id = ?"], [scenario_id]
        if asset_refs:
            where.append(f"asset_ref in ({','.join('?' for _ in asset_refs)})")
            values.extend(asset_refs)
        if period:
            where.append("period = ?")
            values.append(period)
        rows = self._all(f"select * from rule_executions where {' and '.join(where)} order by period, asset_ref", tuple(values))
        for row in rows:
            row["inputs"] = json.loads(row.pop("inputs_json") or "{}")
        return rows

    def replace_scenario_results(
        self,
        *,
        scenario_id: str,
        anomalies: list[Anomaly],
        forecast_lines: list[ForecastLine],
        summary_lines: list[SummaryLine],
    ) -> None:
        with self._lock:
            self.connection.execute("delete from anomalies where scenario_id = ?", (scenario_id,))
            self.connection.execute("delete from forecast_lines where scenario_id = ?", (scenario_id,))
            self.connection.execute("delete from summary_lines where scenario_id = ?", (scenario_id,))
            self.connection.executemany(
                """
                insert into anomalies(
                  scenario_id, anomaly_id, severity, object_type, object_id, rule_id, message
                )
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        scenario_id,
                        item.anomaly_id,
                        item.severity,
                        item.object_type,
                        item.object_id,
                        item.rule_id,
                        item.message,
                    )
                    for item in anomalies
                ],
            )
            self.connection.executemany(
                """
                insert into forecast_lines(
                  scenario_id, budget_version, asset_id, planned_asset_id,
                  asset_source_type, company, department, cost_center, profit_center,
                  asset_category, depreciation_code, depreciation_policy, depreciation_method,
                  period, year, opening_original_cost, opening_accumulated_depreciation,
                  opening_accumulated_impairment, opening_net_value, addition_amount,
                  disposal_amount, impairment_amount, depreciable_base, monthly_depreciation,
                  accumulated_depreciation, closing_net_value, source_event_id,
                  calculation_rule_id, validation_status
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [self._forecast_tuple(item) for item in forecast_lines],
            )
            self.connection.executemany(
                """
                insert into summary_lines(
                  scenario_id, budget_version, period, year, company, department,
                  cost_center, profit_center, asset_category, asset_source_type,
                  event_type, depreciation_policy, monthly_depreciation_sum,
                  addition_depreciation_impact, disposal_depreciation_impact,
                  impairment_depreciation_impact
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [self._summary_tuple(item) for item in summary_lines],
            )
            self.connection.execute(
                "update scenarios set updated_at = ? where scenario_id = ?",
                (datetime.now().isoformat(timespec="seconds"), scenario_id),
            )
            self.connection.commit()

    def save_what_if(
        self,
        *,
        scenario_id: str,
        changes: list[WhatIfChange],
        attributions: list[AttributionLine],
    ) -> None:
        with self._lock:
            self.connection.execute("delete from what_if_changes where scenario_id = ?", (scenario_id,))
            self.connection.execute("delete from attribution_lines where scenario_id = ?", (scenario_id,))
            self.connection.executemany(
                """
                insert into what_if_changes(
                  scenario_id, change_id, target_type, target_id, field_name,
                  old_value, new_value, reason
                )
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        scenario_id,
                        item.change_id,
                        item.target_type,
                        item.target_id,
                        item.field_name,
                        item.old_value,
                        item.new_value,
                        item.reason,
                    )
                    for item in changes
                ],
            )
            self.connection.executemany(
                """
                insert into attribution_lines(
                  scenario_id, compared_to_scenario_id, period, object_type,
                  object_id, driver_type, driver_id, baseline_depreciation,
                  scenario_depreciation, difference, explanation
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.scenario_id,
                        item.compared_to_scenario_id,
                        str(item.period),
                        item.object_type,
                        item.object_id,
                        item.driver_type,
                        item.driver_id,
                        str(item.baseline_depreciation),
                        str(item.scenario_depreciation),
                        str(item.difference),
                        item.explanation,
                    )
                    for item in attributions
                ],
            )
            self.connection.commit()

    def scenario(self, scenario_id: str) -> dict[str, Any] | None:
        row = self._one("select * from scenarios where scenario_id = ?", (scenario_id,))
        return self._decorate_scenario(dict(row)) if row else None

    def scenarios(self) -> list[dict[str, Any]]:
        return [self._decorate_scenario(row) for row in self._all("select * from scenarios order by created_at, scenario_id")]

    def delete_scenario(self, scenario_id: str) -> None:
        if scenario_id == "BASELINE":
            raise ValueError("基准场景不能删除。")
        with self._lock:
            if self.connection.execute("select 1 from scenarios where scenario_id = ?", (scenario_id,)).fetchone() is None:
                raise ValueError(f"场景不存在：{scenario_id}")
            for table in (
                "attribution_lines",
                "what_if_changes",
                "rule_executions",
                "scenario_assumptions",
                "scenario_metadata",
                "summary_lines",
                "forecast_lines",
                "anomalies",
                "scenarios",
            ):
                self.connection.execute(f"delete from {table} where scenario_id = ?", (scenario_id,))
            self.connection.commit()

    def _decorate_scenario(self, scenario: dict[str, Any]) -> dict[str, Any]:
        metadata = self._one("select * from scenario_metadata where scenario_id = ?", (scenario["scenario_id"],))
        if metadata is None:
            return scenario
        scenario["scenario_name"] = metadata["scenario_name"]
        scenario["source_snapshot_id"] = metadata["source_snapshot_id"]
        scenario["calculation_version"] = metadata["calculation_version"]
        scenario["assumptions"] = json.loads(metadata["assumptions_json"] or "[]")
        return scenario

    def forecast_lines(
        self,
        *,
        scenario_id: str,
        department: str | None = None,
        asset_category: str | None = None,
        asset_source_type: str | None = None,
        period_from: str | None = None,
        period_to: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where = ["scenario_id = ?"]
        values: list[Any] = [scenario_id]
        for field, value in (
            ("department", department),
            ("asset_category", asset_category),
            ("asset_source_type", asset_source_type),
        ):
            if value:
                where.append(f"{field} = ?")
                values.append(value)
        if period_from:
            where.append("period >= ?")
            values.append(period_from)
        if period_to:
            where.append("period <= ?")
            values.append(period_to)
        values.extend([limit, offset])
        return self._all(
            f"""
            select *
            from forecast_lines
            where {' and '.join(where)}
            order by period, department, coalesce(asset_id, planned_asset_id)
            limit ? offset ?
            """,
            tuple(values),
        )

    def forecast_projection_rows(self) -> list[dict[str, Any]]:
        """Return every persisted asset-month result for graph projection."""
        return self._all(
            """
            select *
            from forecast_lines
            order by scenario_id, period, department, coalesce(asset_id, planned_asset_id)
            """
        )

    def summary_projection_rows(self) -> list[dict[str, Any]]:
        """Return every persisted aggregate result for graph projection."""
        return self._all(
            """
            select *
            from summary_lines
            order by scenario_id, period, department, asset_category, depreciation_policy
            """
        )

    def rule_execution_projection_rows(self) -> list[dict[str, Any]]:
        return self._all(
            """
            select *
            from rule_executions
            order by scenario_id, period, asset_ref, id
            """
        )

    def scenario_change_projection_rows(self) -> list[dict[str, Any]]:
        return self._all(
            """
            select *
            from what_if_changes
            order by scenario_id, change_id
            """
        )

    def attribution_projection_rows(self) -> list[dict[str, Any]]:
        return self._all(
            """
            select *
            from attribution_lines
            order by scenario_id, period, object_id, id
            """
        )

    def wide_table(
        self,
        *,
        scenario_id: str,
        row_type: str,
        department: str | None = None,
        asset_category: str | None = None,
        dimensions: list[str] | None = None,
    ) -> dict[str, Any]:
        periods = [
            row["period"]
            for row in self._all(
                """
                select distinct period
                from forecast_lines
                where scenario_id = ?
                order by period
                """,
                (scenario_id,),
            )
        ]
        where = ["scenario_id = ?"]
        values: list[Any] = [scenario_id]
        if department:
            where.append("department = ?")
            values.append(department)
        if asset_category:
            where.append("asset_category = ?")
            values.append(asset_category)
        where_clause = " and ".join(where)
        dimension_fields = {
            "department": ("department", "department"),
            "asset_category": ("asset_category", "asset_category"),
            "depreciation_code": ("depreciation_code", "depreciation_code"),
            "asset": ("coalesce(asset_id, planned_asset_id) as asset_ref", "asset_ref"),
        }
        legacy_dimensions = {
            "department": ["department"],
            "category": ["asset_category"],
            "asset": ["asset"],
            "overview": [],
        }
        requested_dimensions = dimensions if dimensions is not None else legacy_dimensions.get(row_type, ["asset"])
        requested_dimensions = [item for item in requested_dimensions if item in dimension_fields]
        fields = [dimension_fields[item][0] for item in requested_dimensions]
        label_fields = [dimension_fields[item][1] for item in requested_dimensions]
        if not fields:
            fields = ["'全部资产' as scope_label"]
            label_fields = ["scope_label"]
        select_fields = ", ".join(fields)
        group_fields = ", ".join(label_fields)
        rows = self._all(
            f"""
            select {select_fields},
                   period,
                   round(sum(monthly_depreciation), 2) as depreciation
            from forecast_lines
            where {where_clause}
            group by {group_fields}, period
            order by {group_fields}, period
            """,
            tuple(values),
        )
        grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
        for row in rows:
            key = tuple(row[field] for field in label_fields)
            if key not in grouped:
                grouped[key] = {field: row[field] for field in label_fields}
                grouped[key]["annual_total"] = Decimal("0")
                grouped[key]["months"] = {period: "0.00" for period in periods}
            amount = Decimal(str(row["depreciation"]))
            grouped[key]["months"][row["period"]] = self._money(amount)
            grouped[key]["annual_total"] += amount
        output_rows = []
        for row in grouped.values():
            row["annual_total"] = self._money(row["annual_total"])
            output_rows.append(row)
        return {
            "scenario_id": scenario_id,
            "row_type": row_type if dimensions is None else "drilldown",
            "dimensions": requested_dimensions,
            "periods": periods,
            "fixed_columns": label_fields,
            "rows": output_rows,
            "tree": self._wide_table_tree(output_rows, label_fields, periods),
        }

    def _wide_table_tree(
        self,
        rows: list[dict[str, Any]],
        dimensions: list[str],
        periods: list[str],
    ) -> list[dict[str, Any]]:
        if dimensions == ["scope_label"]:
            return [self._wide_tree_node("scope_label", "全部资产", rows, periods, 0, [])]

        def build(level: int, subset: list[dict[str, Any]], path: list[str]) -> list[dict[str, Any]]:
            field = dimensions[level]
            groups: dict[str, list[dict[str, Any]]] = {}
            for row in subset:
                groups.setdefault(str(row.get(field) or "未设置"), []).append(row)
            nodes: list[dict[str, Any]] = []
            for value, grouped_rows in sorted(groups.items(), key=lambda item: item[0]):
                children = build(level + 1, grouped_rows, [*path, value]) if level + 1 < len(dimensions) else []
                nodes.append(self._wide_tree_node(field, value, grouped_rows, periods, level, children, path))
            return nodes

        return build(0, rows, [])

    def _wide_tree_node(
        self,
        dimension: str,
        value: str,
        rows: list[dict[str, Any]],
        periods: list[str],
        depth: int,
        children: list[dict[str, Any]],
        path: list[str] | None = None,
    ) -> dict[str, Any]:
        months = {
            period: self._money(sum(Decimal(str(row.get("months", {}).get(period, "0"))) for row in rows))
            for period in periods
        }
        annual_total = self._money(sum(Decimal(str(row.get("annual_total", "0"))) for row in rows))
        identity = "|".join([dimension, *(path or []), value])
        return {
            "id": identity,
            "dimension": dimension,
            "value": value,
            "depth": depth,
            "annual_total": annual_total,
            "months": months,
            "children": children,
        }

    def wide_table_compare(
        self,
        *,
        baseline_scenario_id: str,
        scenario_ids: list[str],
        row_type: str,
        department: str | None = None,
        asset_category: str | None = None,
        period_from: str | None = None,
        period_to: str | None = None,
        dimensions: list[str] | None = None,
    ) -> dict[str, Any]:
        scenario_ids = [item for item in scenario_ids if item]
        baseline = self.wide_table(
            scenario_id=baseline_scenario_id,
            row_type=row_type,
            department=department,
            asset_category=asset_category,
            dimensions=dimensions,
        )
        scenario_tables = [
            self.wide_table(
                scenario_id=scenario_id,
                row_type=row_type,
                department=department,
                asset_category=asset_category,
                dimensions=dimensions,
            )
            for scenario_id in scenario_ids
        ]
        periods = sorted(
            {
                *baseline["periods"],
                *(period for table in scenario_tables for period in table["periods"]),
            }
        )
        if period_from:
            periods = [period for period in periods if period >= period_from]
        if period_to:
            periods = [period for period in periods if period <= period_to]
        fixed_columns = list(baseline["fixed_columns"])
        if not fixed_columns and scenario_tables:
            fixed_columns = list(scenario_tables[0]["fixed_columns"])

        def key_for(row: dict[str, Any]) -> tuple[Any, ...]:
            return tuple(row.get(field) for field in fixed_columns)

        def normalize(row: dict[str, Any] | None) -> dict[str, Any]:
            if row is None:
                return {
                    "annual_total": "0.00",
                    "months": {period: "0.00" for period in periods},
                }
            months = {period: row.get("months", {}).get(period, "0.00") for period in periods}
            return {
                "annual_total": row.get("annual_total", "0.00"),
                "months": months,
            }

        baseline_by_key = {key_for(row): row for row in baseline["rows"]}
        scenarios_by_id = {
            table["scenario_id"]: {key_for(row): row for row in table["rows"]}
            for table in scenario_tables
        }
        keys = sorted(
            set(baseline_by_key)
            | {key for rows in scenarios_by_id.values() for key in rows}
        )
        rows: list[dict[str, Any]] = []
        for key in keys:
            base_values = normalize(baseline_by_key.get(key))
            row = {field: key[index] for index, field in enumerate(fixed_columns)}
            row["months"] = {}
            row["baseline"] = {
                "scenario_id": baseline_scenario_id,
                **base_values,
            }
            row["scenarios"] = []
            for scenario_id in scenario_ids:
                scenario_values = normalize(scenarios_by_id.get(scenario_id, {}).get(key))
                annual_difference = Decimal(str(scenario_values["annual_total"])) - Decimal(str(base_values["annual_total"]))
                month_differences = {
                    period: self._money(
                        Decimal(str(scenario_values["months"].get(period, "0.00")))
                        - Decimal(str(base_values["months"].get(period, "0.00")))
                    )
                    for period in periods
                }
                row["months"].update(
                    {
                        period: {
                            **row["months"].get(period, {}),
                            baseline_scenario_id: base_values["months"].get(period, "0.00"),
                            scenario_id: scenario_values["months"].get(period, "0.00"),
                            "diff_amount": month_differences.get(period, "0.00"),
                            "diff_percent": self._percent_difference(
                                scenario_values["months"].get(period, "0.00"),
                                base_values["months"].get(period, "0.00"),
                            ),
                        }
                        for period in periods
                    }
                )
                row["scenarios"].append(
                    {
                        "scenario_id": scenario_id,
                        **scenario_values,
                        "annual_difference": self._money(annual_difference),
                        "month_differences": month_differences,
                    }
                )
            rows.append(row)
        tree_dimensions = baseline.get("dimensions") or ["scope_label"]

        def decimal_sum(items: list[dict[str, Any]], getter) -> Decimal:
            return sum((Decimal(str(getter(item) or "0.00")) for item in items), ZERO)

        def compare_node(
            dimension: str,
            value: str,
            subset: list[dict[str, Any]],
            depth: int,
            path: list[str],
            children: list[dict[str, Any]],
        ) -> dict[str, Any]:
            baseline_months = {
                period: self._money(decimal_sum(subset, lambda row: row["baseline"]["months"].get(period)))
                for period in periods
            }
            baseline_total = self._money(decimal_sum(subset, lambda row: row["baseline"]["annual_total"]))
            scenario_nodes = []
            for scenario_id in scenario_ids:
                scenario_months = {
                    period: self._money(decimal_sum(
                        subset,
                        lambda row: next(
                            (item["months"].get(period) for item in row["scenarios"] if item["scenario_id"] == scenario_id),
                            "0.00",
                        ),
                    ))
                    for period in periods
                }
                scenario_total = self._money(decimal_sum(
                    subset,
                    lambda row: next(
                        (item["annual_total"] for item in row["scenarios"] if item["scenario_id"] == scenario_id),
                        "0.00",
                    ),
                ))
                scenario_nodes.append({
                    "scenario_id": scenario_id,
                    "annual_total": scenario_total,
                    "annual_difference": self._money(Decimal(scenario_total) - Decimal(baseline_total)),
                    "months": scenario_months,
                    "month_differences": {
                        period: self._money(Decimal(scenario_months[period]) - Decimal(baseline_months[period]))
                        for period in periods
                    },
                })
            months = {}
            for period in periods:
                values = {baseline_scenario_id: baseline_months[period]}
                values.update({item["scenario_id"]: item["months"][period] for item in scenario_nodes})
                primary = scenario_nodes[0] if scenario_nodes else None
                months[period] = {
                    **values,
                    "diff_amount": primary["month_differences"][period] if primary else "0.00",
                    "diff_percent": self._percent_difference(
                        primary["months"][period] if primary else baseline_months[period],
                        baseline_months[period],
                    ),
                }
            return {
                "id": "|".join([dimension, *path, value]),
                "dimension": dimension,
                "value": value,
                "depth": depth,
                "baseline": {"scenario_id": baseline_scenario_id, "annual_total": baseline_total, "months": baseline_months},
                "scenarios": scenario_nodes,
                "annual_total": baseline_total,
                "months": months,
                "children": children,
            }

        def build_tree(level: int, subset: list[dict[str, Any]], path: list[str]) -> list[dict[str, Any]]:
            dimension = tree_dimensions[level]
            groups: dict[str, list[dict[str, Any]]] = {}
            for row in subset:
                groups.setdefault(str(row.get(dimension) or "未设置"), []).append(row)
            nodes = []
            for value, grouped_rows in sorted(groups.items(), key=lambda item: item[0]):
                children = build_tree(level + 1, grouped_rows, [*path, value]) if level + 1 < len(tree_dimensions) else []
                nodes.append(compare_node(dimension, value, grouped_rows, level, path, children))
            return nodes

        return {
            "baseline_scenario_id": baseline_scenario_id,
            "scenario_ids": scenario_ids,
            "row_type": row_type,
            "dimensions": baseline.get("dimensions", []),
            "periods": periods,
            "fixed_columns": fixed_columns,
            "rows": rows,
            "tree": build_tree(0, rows, []),
        }

    @staticmethod
    def _percent_difference(scenario_amount: str, baseline_amount: str) -> str:
        baseline = Decimal(str(baseline_amount))
        scenario = Decimal(str(scenario_amount))
        if baseline == ZERO:
            return "0.00" if scenario == ZERO else "100.00"
        return BusinessResultStore._money(((scenario - baseline) / baseline) * Decimal("100"))

    def summaries(self, *, scenario_id: str, group: str) -> list[dict[str, Any]]:
        allowed = {
            "period": ("period",),
            "year": ("year",),
            "department": ("department",),
            "category": ("asset_category",),
            "department_category": ("department", "asset_category"),
            "policy": ("depreciation_policy",),
        }
        fields = allowed.get(group, allowed["department_category"])
        select_fields = ", ".join(fields)
        return self._all(
            f"""
            select {select_fields},
                   round(sum(monthly_depreciation_sum), 2) as monthly_depreciation_sum,
                   round(sum(addition_depreciation_impact), 2) as addition_depreciation_impact,
                   round(sum(disposal_depreciation_impact), 2) as disposal_depreciation_impact,
                   round(sum(impairment_depreciation_impact), 2) as impairment_depreciation_impact
            from summary_lines
            where scenario_id = ?
            group by {select_fields}
            order by {select_fields}
            """,
            (scenario_id,),
        )

    def anomalies(self, *, scenario_id: str, severity: str | None = None) -> list[dict[str, Any]]:
        if severity:
            rows = self._all(
                """
                select *
                from anomalies
                where scenario_id = ? and severity = ?
                order by severity, object_type, object_id
                """,
                (scenario_id, severity),
            )
        else:
            rows = self._all(
                """
                select *
                from anomalies
                where scenario_id = ?
                order by severity, object_type, object_id
                """,
                (scenario_id,),
            )
        return [decorate_anomaly(row) for row in rows]

    def asset_card_amounts(self, scenario_id: str) -> dict[str, dict[str, Any]]:
        rows = self._all(
            """
            with grouped as (
                select coalesce(asset_id, planned_asset_id) as asset_ref,
                       asset_source_type,
                       company,
                       department,
                       cost_center,
                       profit_center,
                       asset_category,
                       depreciation_code,
                       depreciation_policy,
                       depreciation_method,
                       round(max(opening_original_cost), 2) as original_or_planned_amount,
                       round(sum(monthly_depreciation), 2) as forecast_depreciation_total,
                       round(sum(addition_amount), 2) as addition_amount_total,
                       round(sum(disposal_amount), 2) as disposal_amount_total,
                       round(sum(impairment_amount), 2) as impairment_amount_total,
                       min(case when monthly_depreciation > 0 then period end) as first_depreciation_period,
                       max(period) as last_forecast_period,
                       count(*) as forecast_month_count
                from forecast_lines
                where scenario_id = ?
                group by asset_ref, asset_source_type, company, department, cost_center,
                         profit_center, asset_category, depreciation_code,
                         depreciation_policy, depreciation_method
            )
            select grouped.*,
                   round(line.closing_net_value, 2) as ending_net_value
            from grouped
            left join forecast_lines line
              on line.scenario_id = ?
             and coalesce(line.asset_id, line.planned_asset_id) = grouped.asset_ref
             and line.period = grouped.last_forecast_period
            order by grouped.asset_source_type, grouped.department, grouped.asset_ref
            """,
            (scenario_id, scenario_id),
        )
        return {row["asset_ref"]: row for row in rows}

    def dashboard(self, scenario_id: str) -> dict[str, Any]:
        scenario = self.scenario(scenario_id)
        total = self._decimal_value(
            "select coalesce(sum(monthly_depreciation), 0) from forecast_lines where scenario_id = ?",
            (scenario_id,),
        )
        planned_total = self._decimal_value(
            """
            select coalesce(sum(monthly_depreciation), 0)
            from forecast_lines
            where scenario_id = ? and asset_source_type = 'PLANNED'
            """,
            (scenario_id,),
        )
        current_total = self._decimal_value(
            """
            select coalesce(sum(monthly_depreciation), 0)
            from forecast_lines
            where scenario_id = ? and asset_source_type = 'CURRENT'
            """,
            (scenario_id,),
        )
        annual_trend = self._all(
            """
            select year, round(sum(monthly_depreciation), 2) as depreciation
            from forecast_lines
            where scenario_id = ?
            group by year
            order by year
            """,
            (scenario_id,),
        )
        monthly_trend = self._all(
            """
            select period, round(sum(monthly_depreciation), 2) as depreciation
            from forecast_lines where scenario_id = ?
            group by period order by period
            """,
            (scenario_id,),
        )
        department_rank = self._all(
            """
            select department, round(sum(monthly_depreciation), 2) as depreciation
            from forecast_lines
            where scenario_id = ?
            group by department
            order by depreciation desc
            """,
            (scenario_id,),
        )
        driver_breakdown = self._all(
            """
            select event_type as driver, round(sum(monthly_depreciation_sum), 2) as depreciation
            from summary_lines
            where scenario_id = ?
            group by event_type
            order by depreciation desc
            """,
            (scenario_id,),
        )
        anomaly_summary = self._all(
            """
            select severity, count(*) as count
            from anomalies
            where scenario_id = ?
            group by severity
            order by severity
            """,
            (scenario_id,),
        )
        top_assets = self._all(
            """
            select coalesce(asset_id, planned_asset_id) as asset_ref,
                   asset_source_type,
                   department,
                   asset_category,
                   depreciation_policy,
                   round(sum(monthly_depreciation), 2) as depreciation
            from forecast_lines
            where scenario_id = ?
            group by asset_ref, asset_source_type, department, asset_category, depreciation_policy
            order by depreciation desc
            limit 8
            """,
            (scenario_id,),
        )
        return {
            "scenario": scenario,
            "kpis": {
                "total_depreciation": self._money(total),
                "planned_depreciation": self._money(planned_total),
                "current_depreciation": self._money(current_total),
                "forecast_line_count": self._int_value(
                    "select count(*) from forecast_lines where scenario_id = ?",
                    (scenario_id,),
                ),
                "anomaly_count": self._int_value(
                    "select count(*) from anomalies where scenario_id = ?",
                    (scenario_id,),
                ),
            },
            "annual_trend": annual_trend,
            "monthly_trend": monthly_trend,
            "department_rank": department_rank,
            "driver_breakdown": driver_breakdown,
            "anomaly_summary": anomaly_summary,
            "top_assets": top_assets,
        }

    def explain_change(
        self,
        *,
        scenario_id: str,
        department: str | None,
        year: int | None,
    ) -> dict[str, Any]:
        where = ["scenario_id = ?"]
        values: list[Any] = [scenario_id]
        if department:
            where.append("department = ?")
            values.append(department)
        if year:
            where.append("year = ?")
            values.append(year)
        where_clause = " and ".join(where)
        drivers = self._all(
            f"""
            select event_type as driver,
                   asset_source_type,
                   round(sum(monthly_depreciation_sum), 2) as depreciation
            from summary_lines
            where {where_clause}
            group by event_type, asset_source_type
            order by depreciation desc
            """,
            tuple(values),
        )
        contributors = self._all(
            f"""
            select coalesce(asset_id, planned_asset_id) as asset_ref,
                   asset_source_type,
                   department,
                   asset_category,
                   depreciation_policy,
                   source_event_id,
                   round(sum(monthly_depreciation), 2) as depreciation
            from forecast_lines
            where {where_clause}
            group by asset_ref, asset_source_type, department, asset_category, depreciation_policy, source_event_id
            order by depreciation desc
            limit 10
            """,
            tuple(values),
        )
        return {
            "scenario_id": scenario_id,
            "department": department,
            "year": year,
            "drivers": drivers,
            "contributors": contributors,
        }

    def attributions(self, scenario_id: str) -> list[dict[str, Any]]:
        return self._all(
            """
            select *
            from attribution_lines
            where scenario_id = ?
            order by period, object_id
            """,
            (scenario_id,),
        )

    def what_if_changes(self, scenario_id: str) -> list[dict[str, Any]]:
        return self._all(
            "select * from what_if_changes where scenario_id = ? order by change_id",
            (scenario_id,),
        )

    def _init_schema(self) -> None:
        with self._lock:
            self.connection.executescript(
                """
                create table if not exists scenarios (
                  scenario_id text primary key,
                  base_scenario_id text,
                  budget_version text not null,
                  perspective text not null,
                  start_period text not null,
                  months integer not null,
                  description text not null,
                  created_at text not null,
                  updated_at text not null
                );

                create table if not exists source_snapshots (
                  snapshot_id text primary key,
                  status_json text not null,
                  updated_at text not null
                );

                create table if not exists scenario_metadata (
                  scenario_id text primary key,
                  scenario_name text not null,
                  source_snapshot_id text not null,
                  calculation_version text not null,
                  assumptions_json text not null
                );

                create table if not exists scenario_assumptions (
                  scenario_id text not null,
                  assumption_id text not null,
                  template_id text not null,
                  target_id text not null,
                  period text not null,
                  payload_json text not null,
                  primary key(scenario_id, assumption_id)
                );

                create table if not exists chat_action_drafts (
                  draft_id text primary key,
                  conversation_id text not null,
                  action_type text not null,
                  status text not null,
                  base_scenario_id text not null,
                  original_instruction text not null,
                  payload_json text not null,
                  preview_json text not null,
                  ontology_gateway_json text not null,
                  created_at text not null,
                  expires_at text not null,
                  confirmed_at text,
                  scenario_id text
                );
                create index if not exists idx_chat_action_drafts_status on chat_action_drafts(status, expires_at);

                create table if not exists object_types (
                  type_id text primary key,
                  label_cn text not null,
                  description_cn text not null
                );

                create table if not exists property_types (
                  type_id text not null,
                  property_id text not null,
                  label_cn text not null,
                  value_type text not null,
                  primary key(type_id, property_id)
                );

                create table if not exists link_types (
                  type_id text primary key,
                  label_cn text not null,
                  source_type text not null,
                  target_type text not null,
                  description_cn text not null
                );

                create table if not exists action_types (
                  type_id text primary key,
                  label_cn text not null,
                  target_types_json text not null,
                  description_cn text not null
                );

                create table if not exists function_types (
                  type_id text primary key,
                  label_cn text not null,
                  description_cn text not null
                );

                create table if not exists ontology_objects (
                  object_id text primary key,
                  object_type text not null,
                  label_cn text not null,
                  subtitle_cn text not null,
                  properties_json text not null,
                  metrics_json text not null,
                  source_system text not null,
                  technical_ref text not null
                );

                create table if not exists ontology_links (
                  link_id text primary key,
                  link_type text not null,
                  source_object_id text not null,
                  target_object_id text not null,
                  label_cn text not null,
                  business_text text not null,
                  inferred integer not null default 0,
                  evidence_json text not null
                );

                create table if not exists forecast_lines (
                  id integer primary key autoincrement,
                  scenario_id text not null,
                  budget_version text not null,
                  asset_id text,
                  planned_asset_id text,
                  asset_source_type text not null,
                  company text not null,
                  department text not null,
                  cost_center text not null,
                  profit_center text not null,
                  asset_category text not null,
                  depreciation_code text not null,
                  depreciation_policy text not null,
                  depreciation_method text not null,
                  period text not null,
                  year integer not null,
                  opening_original_cost numeric not null,
                  opening_accumulated_depreciation numeric not null,
                  opening_accumulated_impairment numeric not null,
                  opening_net_value numeric not null,
                  addition_amount numeric not null,
                  disposal_amount numeric not null,
                  impairment_amount numeric not null,
                  depreciable_base numeric not null,
                  monthly_depreciation numeric not null,
                  accumulated_depreciation numeric not null,
                  closing_net_value numeric not null,
                  source_event_id text,
                  calculation_rule_id text not null,
                  validation_status text not null
                );

                create index if not exists idx_forecast_filter
                on forecast_lines(scenario_id, period, department, asset_category, asset_source_type);

                create table if not exists summary_lines (
                  id integer primary key autoincrement,
                  scenario_id text not null,
                  budget_version text not null,
                  period text not null,
                  year integer not null,
                  company text not null,
                  department text not null,
                  cost_center text not null,
                  profit_center text not null,
                  asset_category text not null,
                  asset_source_type text not null,
                  event_type text not null,
                  depreciation_policy text not null,
                  monthly_depreciation_sum numeric not null,
                  addition_depreciation_impact numeric not null,
                  disposal_depreciation_impact numeric not null,
                  impairment_depreciation_impact numeric not null
                );

                create table if not exists anomalies (
                  id integer primary key autoincrement,
                  scenario_id text not null,
                  anomaly_id text not null,
                  severity text not null,
                  object_type text not null,
                  object_id text not null,
                  rule_id text not null,
                  message text not null
                );

                create table if not exists what_if_changes (
                  scenario_id text not null,
                  change_id text not null,
                  target_type text not null,
                  target_id text not null,
                  field_name text not null,
                  old_value text not null,
                  new_value text not null,
                  reason text not null,
                  primary key(scenario_id, change_id)
                );

                create table if not exists attribution_lines (
                  id integer primary key autoincrement,
                  scenario_id text not null,
                  compared_to_scenario_id text not null,
                  period text not null,
                  object_type text not null,
                  object_id text not null,
                  driver_type text not null,
                  driver_id text not null,
                  baseline_depreciation numeric not null,
                  scenario_depreciation numeric not null,
                  difference numeric not null,
                  explanation text not null
                );

                create table if not exists rule_executions (
                  id integer primary key autoincrement,
                  scenario_id text not null,
                  asset_ref text not null,
                  period text not null,
                  rule_id text not null,
                  branch_id text not null,
                  formula_cn text not null,
                  inputs_json text not null,
                  conclusion_cn text not null
                );
                create index if not exists idx_rule_execution_lookup
                on rule_executions(scenario_id, asset_ref, period);
                """
            )
            self.connection.commit()

    @staticmethod
    def _forecast_tuple(item: ForecastLine) -> tuple[Any, ...]:
        return (
            item.scenario_id,
            item.budget_version,
            item.asset_id,
            item.planned_asset_id,
            item.asset_source_type,
            item.company,
            item.department,
            item.cost_center,
            item.profit_center,
            item.asset_category,
            item.depreciation_code,
            item.depreciation_policy,
            item.depreciation_method,
            str(item.period),
            item.period.year,
            str(item.opening_original_cost),
            str(item.opening_accumulated_depreciation),
            str(item.opening_accumulated_impairment),
            str(item.opening_net_value),
            str(item.addition_amount),
            str(item.disposal_amount),
            str(item.impairment_amount),
            str(item.depreciable_base),
            str(item.monthly_depreciation),
            str(item.accumulated_depreciation),
            str(item.closing_net_value),
            item.source_event_id,
            item.calculation_rule_id,
            item.validation_status,
        )

    @staticmethod
    def _summary_tuple(item: SummaryLine) -> tuple[Any, ...]:
        return (
            item.scenario_id,
            item.budget_version,
            str(item.period),
            item.year,
            item.company,
            item.department,
            item.cost_center,
            item.profit_center,
            item.asset_category,
            item.asset_source_type,
            item.event_type,
            item.depreciation_policy,
            str(item.monthly_depreciation_sum),
            str(item.addition_depreciation_impact),
            str(item.disposal_depreciation_impact),
            str(item.impairment_depreciation_impact),
        )

    def _all(self, query: str, values: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.connection.execute(query, values).fetchall()
        return [self._row(row) for row in rows]

    def _one(self, query: str, values: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.connection.execute(query, values).fetchone()

    def _decimal_value(self, query: str, values: tuple[Any, ...]) -> Decimal:
        row = self._one(query, values)
        return Decimal(str(row[0] if row else "0"))

    def _int_value(self, query: str, values: tuple[Any, ...]) -> int:
        row = self._one(query, values)
        return int(row[0] if row else 0)

    @classmethod
    def _row(cls, row: sqlite3.Row) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in row.keys():
            value = row[key]
            if key in MONEY_FIELDS:
                result[key] = cls._money(Decimal(str(value or "0")))
            elif isinstance(value, float):
                result[key] = cls._money(Decimal(str(value)))
            else:
                result[key] = value
        return result

    @staticmethod
    def _money(value: Decimal) -> str:
        return f"{value.quantize(Decimal('0.01')):.2f}"
