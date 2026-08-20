from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import asdict
import json
from typing import Any

from depreciation_poc.ontology_model import (
    ActionTypeDefinition,
    FunctionTypeDefinition,
    LinkInstance,
    LinkTypeDefinition,
    ObjectInstance,
    ObjectTypeDefinition,
)


class InMemoryOntologyStore:
    """Explicit test-only Ontology store; production requires Neo4j."""

    def __init__(self) -> None:
        self._objects: dict[str, dict[str, Any]] = {}
        self._links: list[dict[str, Any]] = []
        self._meta = {"object_types": [], "link_types": [], "action_types": [], "function_types": []}
        self._forecast_lines: list[dict[str, Any]] = []
        self._summary_lines: list[dict[str, Any]] = []
        self._rule_executions: list[dict[str, Any]] = []
        self._scenario_changes: list[dict[str, Any]] = []
        self._attributions: list[dict[str, Any]] = []

    def close(self) -> None:
        return

    def sync_schema(
        self,
        *,
        object_types: list[ObjectTypeDefinition],
        link_types: list[LinkTypeDefinition],
        action_types: list[ActionTypeDefinition],
        function_types: list[FunctionTypeDefinition],
    ) -> None:
        self._meta = {
            "object_types": [asdict(item) for item in object_types],
            "link_types": [asdict(item) for item in link_types],
            "action_types": [asdict(item) for item in action_types],
            "function_types": [asdict(item) for item in function_types],
        }

    def ontology_meta(self) -> dict[str, Any]:
        return deepcopy(self._meta)

    def sync(self, *, objects: list[ObjectInstance], links: list[LinkInstance]) -> None:
        self._objects = {
            item.object_id: {
                "object_id": item.object_id, "object_type": item.object_type,
                "label_cn": item.label_cn, "subtitle_cn": item.subtitle_cn,
                "properties": deepcopy(item.properties), "metrics": deepcopy(item.metrics),
                "source_system": item.source_system, "technical_ref": item.technical_ref,
            }
            for item in objects
        }
        self._links = [
            {
                "link_id": item.link_id, "link_type": item.link_type,
                "source_object_id": item.source_object_id, "target_object_id": item.target_object_id,
                "label_cn": item.label_cn, "business_text": item.business_text,
                "inferred": item.inferred, "evidence": deepcopy(item.evidence),
            }
            for item in links
        ]

    def sync_business_results(self, **rows: list[dict[str, Any]]) -> None:
        self._forecast_lines = deepcopy(rows.get("forecast_lines") or [])
        self._summary_lines = deepcopy(rows.get("summary_lines") or [])
        self._rule_executions = deepcopy(rows.get("rule_executions") or [])
        self._scenario_changes = deepcopy(rows.get("scenario_changes") or [])
        self._attributions = deepcopy(rows.get("attributions") or [])

    def objects(self) -> list[dict[str, Any]]:
        return deepcopy(sorted(self._objects.values(), key=lambda item: (item["object_type"], item["object_id"])))

    def links(self) -> list[dict[str, Any]]:
        return deepcopy(sorted(self._links, key=lambda item: item["link_id"]))

    def node(self, object_id: str) -> dict[str, Any] | None:
        return deepcopy(self._objects.get(object_id))

    def adjacent_links(self, object_id: str) -> list[dict[str, Any]]:
        return deepcopy([item for item in self._links if object_id in {item["source_object_id"], item["target_object_id"]}])

    def path(self, from_id: str, to_id: str) -> list[dict[str, Any]]:
        queue = deque([(from_id, [])])
        seen = {from_id}
        while queue:
            current, path = queue.popleft()
            if current == to_id:
                return deepcopy(path)
            if len(path) >= 12:
                continue
            for link in self.adjacent_links(current):
                other = link["target_object_id"] if link["source_object_id"] == current else link["source_object_id"]
                if other not in seen:
                    seen.add(other)
                    queue.append((other, [*path, link]))
        return []

    def triples(self, *, limit: int = 300) -> list[dict[str, Any]]:
        return [
            {"subject": item["source_object_id"], "predicate": item["link_type"], "object": item["target_object_id"], "inferred": item["inferred"]}
            for item in self.links()[:limit]
        ]

    def counts(self) -> dict[str, int]:
        return {
            "node_count": len(self._objects), "link_count": len(self._links),
            "inferred_link_count": sum(bool(item["inferred"]) for item in self._links),
            "forecast_record_count": len(self._forecast_lines), "forecast_summary_count": len(self._summary_lines),
            "rule_execution_count": len(self._rule_executions), "scenario_change_count": len(self._scenario_changes),
            "attribution_count": len(self._attributions),
        }

    def forecast_lines(self, **filters: Any) -> list[dict[str, Any]]:
        rows = self._forecast_lines
        for field in ("scenario_id", "department", "asset_category", "asset_source_type"):
            value = filters.get(field)
            if value:
                rows = [item for item in rows if item.get(field) == value]
        if filters.get("period_from"):
            rows = [item for item in rows if str(item.get("period")) >= str(filters["period_from"])]
        if filters.get("period_to"):
            rows = [item for item in rows if str(item.get("period")) <= str(filters["period_to"])]
        if filters.get("asset_refs"):
            refs = set(filters["asset_refs"])
            rows = [item for item in rows if (item.get("asset_id") or item.get("planned_asset_id")) in refs]
        offset = int(filters.get("offset") or 0)
        limit = int(filters.get("limit") or 200)
        return deepcopy(rows[offset:offset + limit])

    def available_periods(self, scenario_id: str) -> list[str]:
        return sorted({str(item["period"]) for item in self._forecast_lines if item.get("scenario_id") == scenario_id})

    def rule_executions(self, *, scenario_id: str, asset_refs: list[str] | None = None, period: str | None = None) -> list[dict[str, Any]]:
        rows = [item for item in self._rule_executions if item.get("scenario_id") == scenario_id]
        if asset_refs:
            rows = [item for item in rows if item.get("asset_ref") in asset_refs]
        if period:
            rows = [item for item in rows if item.get("period") == period]
        result = deepcopy(rows)
        for row in result:
            row["inputs"] = json.loads(row.get("rule_inputs_json") or "{}")
        return result

    def entity_catalog(self, scenario_id: str) -> dict[str, list[str]]:
        rows = [item for item in self._forecast_lines if item.get("scenario_id") == scenario_id]
        return {
            "companies": sorted({str(item["company"]) for item in rows if item.get("company")}),
            "departments": sorted({str(item["department"]) for item in rows if item.get("department")}),
            "asset_categories": sorted({str(item["asset_category"]) for item in rows if item.get("asset_category")}),
            "asset_refs": sorted({str(item.get("asset_id") or item.get("planned_asset_id")) for item in rows if item.get("asset_id") or item.get("planned_asset_id")}),
        }

    def eligible_asset_refs(self, *, scenario_id: str, scope_type: str, scope_value: str) -> list[str]:
        field = {"company": "company", "department": "department", "asset_category": "asset_category"}.get(scope_type)
        if not field:
            return []
        return sorted({
            str(item.get("asset_id") or item.get("planned_asset_id"))
            for item in self._forecast_lines
            if item.get("scenario_id") == scenario_id and str(item.get(field)) == scope_value
        })

    def policy_object_id_for_asset(self, asset_object_id: str) -> str | None:
        codes = self._targets(asset_object_id, "assetUsesDepreciationCode")
        policies = self._targets(codes[0], "codeMapsToPolicy") if codes else []
        return policies[0] if policies else None

    def reverse_action_capabilities(self, *, scenario_id: str, scope_type: str, scope_value: str) -> dict[str, list[str]]:
        result = {}
        for asset_ref in self.eligible_asset_refs(scenario_id=scenario_id, scope_type=scope_type, scope_value=scope_value):
            asset_id = f"FixedAsset:{asset_ref}"
            codes = self._targets(asset_id, "assetUsesDepreciationCode")
            methods = self._targets(codes[0], "codeUsesMethod") if codes else []
            capabilities = self._targets(methods[0], "methodAllowsReverseAction") if methods else []
            result[asset_ref] = [item.split(":", 1)[-1] for item in capabilities]
        return result

    def ancestors_including_self(self, category_id: str) -> list[str]:
        result, current, seen = [], f"AssetCategory:{category_id}", set()
        while current and current not in seen:
            seen.add(current)
            result.append(current.split(":", 1)[-1])
            parents = self._targets(current, "categoryInheritsCategory")
            current = parents[0] if parents else ""
        return result

    def explain_policy_match(self, *, company: str, perspective: str, asset_category: str) -> dict[str, Any] | None:
        for category in self.ancestors_including_self(asset_category):
            category_id = f"AssetCategory:{category}"
            policies = [
                link["source_object_id"] for link in self._links
                if link["link_type"] == "policyAppliesToCategory" and link["target_object_id"] == category_id
            ]
            for policy_id in policies:
                policy = self._objects.get(policy_id, {}).get("properties", {})
                if policy.get("company") == company and policy.get("perspective") == perspective:
                    raw_policy = policy_id.split(":", 1)[-1]
                    return {
                        "policy_id": raw_policy, "matched_category": category, "asset_category": asset_category,
                        "company": company, "perspective": perspective,
                        "proof": [
                            {"subject": f"AssetCategory:{asset_category}", "predicate": "categoryInheritsCategory*", "object": category_id, "inferred": category != asset_category},
                            {"subject": policy_id, "predicate": "policyAppliesToCategory", "object": category_id, "inferred": False},
                        ],
                    }
        return None

    def _targets(self, source: str, link_type: str) -> list[str]:
        return [
            item["target_object_id"] for item in self._links
            if item["source_object_id"] == source and item["link_type"] == link_type
        ]
