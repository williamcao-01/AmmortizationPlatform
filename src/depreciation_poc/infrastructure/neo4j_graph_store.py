from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from depreciation_poc.ontology_model import LinkInstance, ObjectInstance


class Neo4jOntologyStore:
    """Projects the business ontology into Neo4j while SQLite remains the calculation store."""

    def __init__(self, *, uri: str, username: str, password: str, database: str = "neo4j") -> None:
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise RuntimeError("未安装 neo4j Python 驱动，无法启用 Neo4j 图谱库。") from exc
        self.database = database
        self.driver = GraphDatabase.driver(uri, auth=(username, password))
        self.driver.verify_connectivity()

    def close(self) -> None:
        self.driver.close()

    def sync(self, *, objects: list[ObjectInstance], links: list[LinkInstance]) -> None:
        nodes = [self._node_params(item) for item in objects]
        relationships = [self._relationship_params(item) for item in links]
        with self.driver.session(database=self.database) as session:
            session.run("MATCH (n:OntologyObject) DETACH DELETE n").consume()
            session.run("CREATE CONSTRAINT ontology_object_id IF NOT EXISTS FOR (n:OntologyObject) REQUIRE n.object_id IS UNIQUE").consume()
            session.run(
                """
                UNWIND $nodes AS row
                CREATE (n:OntologyObject)
                SET n = row,
                    n:BusinessObject
                """,
                nodes=nodes,
            ).consume()
            session.run(
                """
                UNWIND $relationships AS row
                MATCH (source:OntologyObject {object_id: row.source_object_id})
                MATCH (target:OntologyObject {object_id: row.target_object_id})
                CREATE (source)-[r:BUSINESS_RELATION]->(target)
                SET r = row
                """,
                relationships=relationships,
            ).consume()

    def objects(self) -> list[dict[str, Any]]:
        query = """
        MATCH (n:OntologyObject)
        RETURN n
        ORDER BY n.object_type, n.object_id
        """
        with self.driver.session(database=self.database) as session:
            return [self._node_record(record["n"]) for record in session.run(query)]

    def links(self) -> list[dict[str, Any]]:
        query = """
        MATCH (source:OntologyObject)-[r:BUSINESS_RELATION]->(target:OntologyObject)
        RETURN r
        ORDER BY r.link_type, r.source_object_id, r.target_object_id
        """
        with self.driver.session(database=self.database) as session:
            return [self._relationship_record(record["r"]) for record in session.run(query)]

    def node(self, object_id: str) -> dict[str, Any] | None:
        query = "MATCH (n:OntologyObject {object_id: $object_id}) RETURN n LIMIT 1"
        with self.driver.session(database=self.database) as session:
            record = session.run(query, object_id=object_id).single()
        return self._node_record(record["n"]) if record else None

    def adjacent_links(self, object_id: str) -> list[dict[str, Any]]:
        query = """
        MATCH (:OntologyObject {object_id: $object_id})-[r:BUSINESS_RELATION]-(:OntologyObject)
        RETURN r
        ORDER BY r.link_type, r.source_object_id, r.target_object_id
        """
        with self.driver.session(database=self.database) as session:
            return [self._relationship_record(record["r"]) for record in session.run(query, object_id=object_id)]

    def path(self, from_id: str, to_id: str) -> list[dict[str, Any]]:
        query = """
        MATCH (source:OntologyObject {object_id: $from_id}), (target:OntologyObject {object_id: $to_id})
        MATCH path = shortestPath((source)-[:BUSINESS_RELATION*..12]-(target))
        RETURN [relationship IN relationships(path) | properties(relationship)] AS links
        LIMIT 1
        """
        with self.driver.session(database=self.database) as session:
            record = session.run(query, from_id=from_id, to_id=to_id).single()
        return [self._relationship_record(item) for item in record["links"]] if record else []

    def counts(self) -> dict[str, int]:
        query = """
        CALL {
          MATCH (n:OntologyObject)
          RETURN count(n) AS node_count
        }
        CALL {
          MATCH ()-[r:BUSINESS_RELATION]->()
          RETURN count(r) AS link_count,
                 count(CASE WHEN r.inferred THEN r.link_id END) AS inferred_link_count
        }
        CALL {
          MATCH (n:ForecastRecord)
          RETURN count(n) AS forecast_record_count
        }
        CALL {
          MATCH (n:ForecastSummary)
          RETURN count(n) AS forecast_summary_count
        }
        CALL {
          MATCH (n:RuleExecution)
          RETURN count(n) AS rule_execution_count
        }
        CALL {
          MATCH (n:ScenarioChange)
          RETURN count(n) AS scenario_change_count
        }
        CALL {
          MATCH (n:AttributionRecord)
          RETURN count(n) AS attribution_count
        }
        RETURN node_count, link_count, inferred_link_count, forecast_record_count, forecast_summary_count,
               rule_execution_count, scenario_change_count, attribution_count
        """
        with self.driver.session(database=self.database) as session:
            row = session.run(query).single()
        return {
            key: int(row[key] or 0)
            for key in (
                "node_count",
                "link_count",
                "inferred_link_count",
                "forecast_record_count",
                "forecast_summary_count",
                "rule_execution_count",
                "scenario_change_count",
                "attribution_count",
            )
        }

    def sync_business_results(
        self,
        *,
        forecast_lines: list[dict[str, Any]],
        summary_lines: list[dict[str, Any]],
        rule_executions: list[dict[str, Any]],
        scenario_changes: list[dict[str, Any]],
        attributions: list[dict[str, Any]],
    ) -> None:
        forecast_rows = [self._jsonable_row(item) for item in forecast_lines]
        summary_rows = [self._jsonable_row(item) for item in summary_lines]
        execution_rows = [self._jsonable_row(item) for item in rule_executions]
        change_rows = [self._jsonable_row(item) for item in scenario_changes]
        attribution_rows = [self._jsonable_row(item) for item in attributions]
        with self.driver.session(database=self.database) as session:
            session.run("MATCH (n:ForecastRecord) DETACH DELETE n").consume()
            session.run("MATCH (n:ForecastSummary) DETACH DELETE n").consume()
            session.run("MATCH (n:RuleExecution) DETACH DELETE n").consume()
            session.run("MATCH (n:ScenarioChange) DETACH DELETE n").consume()
            session.run("MATCH (n:AttributionRecord) DETACH DELETE n").consume()
            session.run("CREATE CONSTRAINT forecast_record_id IF NOT EXISTS FOR (n:ForecastRecord) REQUIRE n.record_id IS UNIQUE").consume()
            session.run("CREATE CONSTRAINT forecast_summary_id IF NOT EXISTS FOR (n:ForecastSummary) REQUIRE n.summary_id IS UNIQUE").consume()
            session.run("CREATE CONSTRAINT rule_execution_id IF NOT EXISTS FOR (n:RuleExecution) REQUIRE n.execution_id IS UNIQUE").consume()
            session.run("CREATE CONSTRAINT scenario_change_id IF NOT EXISTS FOR (n:ScenarioChange) REQUIRE n.change_record_id IS UNIQUE").consume()
            session.run("CREATE CONSTRAINT attribution_record_id IF NOT EXISTS FOR (n:AttributionRecord) REQUIRE n.attribution_id IS UNIQUE").consume()
            session.run(
                """
                UNWIND $rows AS row
                CREATE (n:ForecastRecord)
                SET n = row
                WITH n, row
                MATCH (asset:OntologyObject {object_id: row.asset_object_id})
                MATCH (scenario:OntologyObject {object_id: row.scenario_object_id})
                CREATE (scenario)-[:HAS_FORECAST_RECORD]->(n)-[:CALCULATED_FOR]->(asset)
                """,
                rows=forecast_rows,
            ).consume()
            session.run(
                """
                UNWIND $rows AS row
                CREATE (n:ForecastSummary)
                SET n = row
                WITH n, row
                MATCH (scenario:OntologyObject {object_id: row.scenario_object_id})
                CREATE (scenario)-[:HAS_FORECAST_SUMMARY]->(n)
                """,
                rows=summary_rows,
            ).consume()
            session.run(
                """
                UNWIND $rows AS row
                CREATE (n:RuleExecution)
                SET n = row
                WITH n, row
                MATCH (scenario:OntologyObject {object_id: row.scenario_object_id})
                MATCH (asset:OntologyObject {object_id: row.asset_object_id})
                CREATE (scenario)-[:HAS_RULE_EXECUTION]->(n)-[:EXECUTED_FOR]->(asset)
                WITH n, row
                OPTIONAL MATCH (record:ForecastRecord {record_id: row.forecast_record_id})
                FOREACH (_ IN CASE WHEN record IS NULL THEN [] ELSE [1] END |
                  CREATE (n)-[:PRODUCED_FORECAST]->(record)
                )
                """,
                rows=execution_rows,
            ).consume()
            session.run(
                """
                UNWIND $rows AS row
                CREATE (n:ScenarioChange)
                SET n = row
                WITH n, row
                MATCH (scenario:OntologyObject {object_id: row.scenario_object_id})
                MATCH (asset:OntologyObject {object_id: row.asset_object_id})
                CREATE (scenario)-[:HAS_SCENARIO_CHANGE]->(n)-[:CHANGES_ASSUMPTION_FOR]->(asset)
                """,
                rows=change_rows,
            ).consume()
            session.run(
                """
                UNWIND $rows AS row
                CREATE (n:AttributionRecord)
                SET n = row
                WITH n, row
                MATCH (scenario:OntologyObject {object_id: row.scenario_object_id})
                MATCH (asset:OntologyObject {object_id: row.asset_object_id})
                CREATE (scenario)-[:HAS_ATTRIBUTION]->(n)-[:ATTRIBUTES_CHANGE_TO]->(asset)
                WITH n, row
                OPTIONAL MATCH (baseline:OntologyObject {object_id: row.compared_scenario_object_id})
                FOREACH (_ IN CASE WHEN baseline IS NULL THEN [] ELSE [1] END |
                  CREATE (n)-[:COMPARED_WITH]->(baseline)
                )
                """,
                rows=attribution_rows,
            ).consume()

    @staticmethod
    def _jsonable_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: (str(value) if isinstance(value, (Decimal, date, datetime)) else value)
            for key, value in row.items()
            if value is not None and not isinstance(value, (dict, list))
        }

    @staticmethod
    def _node_params(item: ObjectInstance) -> dict[str, Any]:
        return {
            "object_id": item.object_id,
            "object_type": item.object_type,
            "label_cn": item.label_cn,
            "subtitle_cn": item.subtitle_cn,
            "source_system": item.source_system,
            "technical_ref": item.technical_ref,
            "properties_json": json.dumps(item.properties, ensure_ascii=False, default=str),
            "metrics_json": json.dumps(item.metrics, ensure_ascii=False, default=str),
            "object_type_label": item.object_type,
        }

    @staticmethod
    def _relationship_params(item: LinkInstance) -> dict[str, Any]:
        return {
            "link_id": item.link_id,
            "link_type": item.link_type,
            "source_object_id": item.source_object_id,
            "target_object_id": item.target_object_id,
            "label_cn": item.label_cn,
            "business_text": item.business_text,
            "inferred": bool(item.inferred),
            "evidence_json": json.dumps(item.evidence, ensure_ascii=False, default=str),
        }

    @staticmethod
    def _node_record(node: Any) -> dict[str, Any]:
        values = dict(node)
        return {
            "object_id": values["object_id"],
            "object_type": values["object_type"],
            "label_cn": values["label_cn"],
            "subtitle_cn": values.get("subtitle_cn") or "",
            "source_system": values.get("source_system") or "",
            "technical_ref": values.get("technical_ref") or "",
            "properties": json.loads(values.get("properties_json") or "{}"),
            "metrics": json.loads(values.get("metrics_json") or "{}"),
        }

    @staticmethod
    def _relationship_record(relationship: Any) -> dict[str, Any]:
        values = dict(relationship)
        return {
            "link_id": values["link_id"],
            "link_type": values["link_type"],
            "source_object_id": values["source_object_id"],
            "target_object_id": values["target_object_id"],
            "label_cn": values["label_cn"],
            "business_text": values.get("business_text") or "",
            "inferred": bool(values.get("inferred")),
            "evidence": json.loads(values.get("evidence_json") or "{}"),
        }
