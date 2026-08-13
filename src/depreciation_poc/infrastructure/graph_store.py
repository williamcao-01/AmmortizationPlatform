from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from depreciation_poc.domain.models import (
    AssetCategory,
    DepreciationCode,
    DepreciationPolicy,
)


class SQLiteGraphStore:
    """A small persistent RDF-like triple store for the demo.

    It deliberately uses a narrow interface so the POC can later swap this
    embedded store for Neo4j, GraphDB, Stardog, or Fuseki without changing the
    calculation and UI layers.
    """

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
            self.connection.execute("delete from triples")
            self.connection.commit()

    def load_ontology(
        self,
        *,
        categories: list[AssetCategory],
        policies: list[DepreciationPolicy],
        codes: list[DepreciationCode],
    ) -> None:
        self.reset()
        for category in categories:
            category_subject = self._category(category.category_id)
            self.add(category_subject, "rdf:type", "AssetCategory")
            self.add(category_subject, "rdfs:label", category.name)
            if category.parent_id:
                self.add(category_subject, "rdfs:subClassOf", self._category(category.parent_id))

        for policy in policies:
            policy_subject = self._policy(policy.policy_id)
            self.add(policy_subject, "rdf:type", "DepreciationPolicy")
            self.add(policy_subject, "rdfs:label", policy.name)
            self.add(policy_subject, "appliesToCompany", policy.company)
            self.add(policy_subject, "appliesToPerspective", policy.perspective)
            self.add(policy_subject, "appliesToCategory", self._category(policy.asset_category))
            self.add(policy_subject, "method", policy.method)
            self.add(policy_subject, "usefulLifeMonths", str(policy.useful_life_months))
            self.add(policy_subject, "residualRate", str(policy.residual_rate))
            self.add(policy_subject, "startRule", policy.start_rule)

        for code in codes:
            code_subject = self._code(code.code_id)
            self.add(code_subject, "rdf:type", "DepreciationCode")
            self.add(code_subject, "rdfs:label", code.name)
            self.add(code_subject, "allowedForCategory", self._category(code.asset_category))
            self.add(code_subject, "mapsToPolicy", self._policy(code.policy_id))

        self.materialize_inference()
        self.connection.commit()

    def add(self, subject: str, predicate: str, object_value: str, inferred: bool = False) -> None:
        with self._lock:
            self.connection.execute(
                """
                insert or ignore into triples(subject, predicate, object, inferred)
                values (?, ?, ?, ?)
                """,
                (subject, predicate, object_value, 1 if inferred else 0),
            )

    def triples(
        self,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        object_value: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, object]]:
        conditions: list[str] = []
        values: list[object] = []
        if subject is not None:
            conditions.append("subject = ?")
            values.append(subject)
        if predicate is not None:
            conditions.append("predicate = ?")
            values.append(predicate)
        if object_value is not None:
            conditions.append("object = ?")
            values.append(object_value)
        where_clause = f"where {' and '.join(conditions)}" if conditions else ""
        with self._lock:
            rows = self.connection.execute(
                f"""
                select subject, predicate, object, inferred
                from triples
                {where_clause}
                order by inferred, subject, predicate, object
                limit ?
                """,
                [*values, limit],
            ).fetchall()
        return [
            {
                "subject": row["subject"],
                "predicate": row["predicate"],
                "object": row["object"],
                "inferred": bool(row["inferred"]),
            }
            for row in rows
        ]

    def count_triples(self, *, inferred: bool | None = None) -> int:
        if inferred is None:
            with self._lock:
                row = self.connection.execute("select count(*) as count from triples").fetchone()
        else:
            with self._lock:
                row = self.connection.execute(
                    "select count(*) as count from triples where inferred = ?",
                    (1 if inferred else 0,),
                ).fetchone()
        return int(row["count"])

    def ancestors_including_self(self, category_id: str) -> list[str]:
        ancestors = [category_id]
        current = category_id
        seen = {category_id}
        while current:
            with self._lock:
                row = self.connection.execute(
                    """
                    select object
                    from triples
                    where subject = ?
                      and predicate = 'rdfs:subClassOf'
                      and inferred = 0
                    order by object
                    limit 1
                    """,
                    (self._category(current),),
                ).fetchone()
            if row is None:
                break
            parent = self._local_id(row["object"])
            if parent in seen:
                break
            seen.add(parent)
            ancestors.append(parent)
            current = parent
        return ancestors

    def is_category_or_descendant(self, category_id: str, ancestor_id: str) -> bool:
        if category_id == ancestor_id:
            return True
        with self._lock:
            rows = self.connection.execute(
                """
                select 1
                from triples
                where subject = ?
                  and predicate = 'rdfs:subClassOf'
                  and object = ?
                limit 1
                """,
                (self._category(category_id), self._category(ancestor_id)),
            ).fetchall()
        return bool(rows)

    def explain_policy_match(
        self,
        *,
        company: str,
        perspective: str,
        asset_category: str,
    ) -> dict[str, object] | None:
        ancestor_subjects = [self._category(item) for item in self.ancestors_including_self(asset_category)]
        placeholders = ",".join("?" for _ in ancestor_subjects)
        with self._lock:
            rows = self.connection.execute(
                f"""
                select p.subject as policy, c.object as category
                from triples p
                join triples c on c.subject = p.subject and c.predicate = 'appliesToCategory'
                join triples co on co.subject = p.subject and co.predicate = 'appliesToCompany'
                join triples pe on pe.subject = p.subject and pe.predicate = 'appliesToPerspective'
                where p.predicate = 'rdf:type'
                  and p.object = 'DepreciationPolicy'
                  and co.object = ?
                  and pe.object = ?
                  and c.object in ({placeholders})
                """,
                [company, perspective, *ancestor_subjects],
            ).fetchall()
        if not rows:
            return None
        ancestor_rank = {subject: index for index, subject in enumerate(ancestor_subjects)}
        best = sorted(rows, key=lambda row: ancestor_rank[row["category"]])[0]
        policy_id = self._local_id(best["policy"])
        category_id = self._local_id(best["category"])
        proof = [
            {
                "subject": self._category(asset_category),
                "predicate": "rdfs:subClassOf*",
                "object": self._category(category_id),
                "inferred": category_id != asset_category,
            },
            {
                "subject": best["policy"],
                "predicate": "appliesToCategory",
                "object": best["category"],
                "inferred": False,
            },
            {
                "subject": best["policy"],
                "predicate": "appliesToCompany",
                "object": company,
                "inferred": False,
            },
            {
                "subject": best["policy"],
                "predicate": "appliesToPerspective",
                "object": perspective,
                "inferred": False,
            },
        ]
        return {
            "policy_id": policy_id,
            "matched_category": category_id,
            "asset_category": asset_category,
            "company": company,
            "perspective": perspective,
            "proof": proof,
        }

    def materialize_inference(self) -> None:
        changed = True
        while changed:
            changed = False
            with self._lock:
                direct_rows = self.connection.execute(
                    """
                    select subject, object
                    from triples
                    where predicate = 'rdfs:subClassOf'
                    """
                ).fetchall()
            pairs = {(row["subject"], row["object"]) for row in direct_rows}
            for child, parent in list(pairs):
                grandparents = [right for left, right in pairs if left == parent]
                for grandparent in grandparents:
                    if (child, grandparent) not in pairs:
                        self.add(child, "rdfs:subClassOf", grandparent, inferred=True)
                        pairs.add((child, grandparent))
                        changed = True

    def _init_schema(self) -> None:
        with self._lock:
            self.connection.execute(
                """
                create table if not exists triples (
                    subject text not null,
                    predicate text not null,
                    object text not null,
                    inferred integer not null default 0,
                    primary key(subject, predicate, object)
                )
                """
            )
            self.connection.execute(
                "create index if not exists idx_triples_pred_obj on triples(predicate, object)"
            )
            self.connection.commit()

    @staticmethod
    def _category(category_id: str) -> str:
        return f"category:{category_id}"

    @staticmethod
    def _policy(policy_id: str) -> str:
        return f"policy:{policy_id}"

    @staticmethod
    def _code(code_id: str) -> str:
        return f"code:{code_id}"

    @staticmethod
    def _local_id(value: str) -> str:
        return value.split(":", maxsplit=1)[1] if ":" in value else value
