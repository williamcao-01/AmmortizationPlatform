from __future__ import annotations

"""Mandatory, read-only ontology confirmation for every QA result.

The language model is deliberately kept outside this boundary.  It may select a
registered question tool, but this gateway performs a second graph read before
that tool result can become answer evidence.  A no-match result is therefore a
confirmed absence, rather than an assumption made from an incomplete prompt.
"""

from typing import Any

from depreciation_poc.ontology_model import object_id


class OntologyEvidenceGateway:
    """Adds a deterministic graph-query receipt to controlled QA evidence."""

    protocol_version = "ontology-evidence-gateway-v1"

    def __init__(self, ontology_store: Any) -> None:
        self.ontology_store = ontology_store

    def confirm_tool_result(
        self,
        *,
        tool_name: str,
        arguments: dict[str, object],
        scenario_id: str,
        result: dict[str, object],
    ) -> dict[str, object]:
        """Query graph anchors and attach an auditable receipt to one tool result."""
        anchors = self._anchors(arguments=arguments, scenario_id=scenario_id, result=result)
        checks = [self._check_anchor(anchor) for anchor in anchors]
        reachable = [item for item in checks if item["node_found"]]
        has_result = self._has_business_result(result)
        status = "verified" if has_result and reachable else "missing_after_query" if not has_result else "unverified"
        receipt: dict[str, object] = {
            "protocol": self.protocol_version,
            "tool": tool_name,
            "scenario_id": scenario_id,
            "query_executed": True,
            "access": "python_neo4j_driver_controlled_cypher",
            "status": status,
            "checked_anchors": checks,
            "reason_cn": (
                "已通过Ontology网关执行受控图查询并取得关联证据。"
                if status == "verified"
                else "已通过Ontology网关执行受控图查询，但没有命中可回答的数据；允许据此报告数据缺失。"
                if status == "missing_after_query"
                else "业务查询有结果，但Ontology网关未能确认其图谱锚点，结果不得用于生成回答。"
            ),
        }
        self._attach_receipt(result, receipt)
        return result

    def confirm_answer(
        self,
        *,
        answer_type: str,
        scenario_id: str,
        evidence: dict[str, object] | None,
        result: dict[str, object],
    ) -> dict[str, object]:
        """Confirm non-chat QA endpoints against their returned ontology paths."""
        payload = {"items": self._answer_items(evidence or {}, result)}
        return self.confirm_tool_result(
            tool_name=answer_type,
            arguments={},
            scenario_id=scenario_id,
            result=payload,
        )["summary"]["ontology_gateway"]  # type: ignore[index]

    @staticmethod
    def is_confirmed(receipt: object) -> bool:
        return isinstance(receipt, dict) and receipt.get("status") in {"verified", "missing_after_query"} and receipt.get("query_executed") is True

    def _anchors(self, *, arguments: dict[str, object], scenario_id: str, result: dict[str, object]) -> list[str]:
        anchors = [object_id("Scenario", scenario_id)]
        asset_ref = str(arguments.get("asset_ref") or "").strip()
        if asset_ref:
            anchors.append(object_id("FixedAsset", f"{asset_ref}-0" if asset_ref.isdigit() else asset_ref))
        object_ref = str(arguments.get("object_id") or "").strip()
        if object_ref:
            anchors.append(object_ref)
        for item in list(result.get("items") or []):
            if not isinstance(item, dict):
                continue
            item_object_id = str(item.get("object_id") or "").strip()
            if item_object_id:
                anchors.append(item_object_id)
            item_asset_ref = str(item.get("asset_ref") or "").strip()
            if item_asset_ref:
                anchors.append(object_id("FixedAsset", item_asset_ref))
        return list(dict.fromkeys(item for item in anchors if item))[:12]

    def _check_anchor(self, anchor: str) -> dict[str, object]:
        # Both calls execute parameterized Cypher in Neo4jOntologyStore.  The
        # in-memory implementation mirrors this contract for deterministic tests.
        node = self.ontology_store.node(anchor)
        links = self.ontology_store.adjacent_links(anchor) if node is not None else []
        return {
            "object_id": anchor,
            "node_found": node is not None,
            "object_type": node.get("object_type") if node else None,
            "label_cn": node.get("label_cn") if node else None,
            "relationship_count": len(links),
            "relationship_sample": [
                {
                    "link_type": link.get("link_type"),
                    "source_object_id": link.get("source_object_id"),
                    "target_object_id": link.get("target_object_id"),
                }
                for link in links[:3]
            ],
        }

    @staticmethod
    def _has_business_result(result: dict[str, object]) -> bool:
        summary = result.get("summary") or {}
        if isinstance(summary, dict):
            for key in ("matched_count", "match_count", "line_count", "execution_count", "row_count"):
                if key in summary:
                    return int(summary.get(key) or 0) > 0
        return bool(result.get("items"))

    @staticmethod
    def _attach_receipt(result: dict[str, object], receipt: dict[str, object]) -> None:
        summary = result.setdefault("summary", {})
        if not isinstance(summary, dict):
            raise ValueError("知识问答工具返回的summary必须是对象")
        summary["ontology_gateway"] = receipt
        items = result.get("items")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    item["ontology_gateway"] = receipt
        sources = result.get("sources")
        if isinstance(sources, list):
            sources.append({
                "label": f"Ontology网关核验：{receipt['tool']}",
                "kind": "ontology_gateway",
                "protocol": receipt["protocol"],
                "status": receipt["status"],
                "query_executed": True,
            })

    @staticmethod
    def _answer_items(evidence: dict[str, object], result: dict[str, object]) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for path in list(evidence.get("ontology_paths") or []):
            if not isinstance(path, dict):
                continue
            for key in ("asset_ref", "object_id"):
                value = path.get(key)
                if value:
                    items.append({"asset_ref": value} if key == "asset_ref" else {"object_id": value})
        for item in list(result.get("recommendations") or []):
            if isinstance(item, dict) and item.get("asset_ref"):
                items.append({"asset_ref": item["asset_ref"]})
        return items
