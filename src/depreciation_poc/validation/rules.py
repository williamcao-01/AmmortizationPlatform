from __future__ import annotations

from depreciation_poc.domain.models import (
    Anomaly,
    AssetEvent,
    FixedAsset,
    PlannedAsset,
)
from depreciation_poc.ontology.semantic_model import SemanticModel


class DepreciationValidator:
    def __init__(self, semantic_model: SemanticModel, perspective: str) -> None:
        self.semantic_model = semantic_model
        self.perspective = perspective

    def validate(
        self,
        fixed_assets: list[FixedAsset],
        planned_assets: list[PlannedAsset],
        events: list[AssetEvent],
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        fixed_by_id = {asset.asset_id: asset for asset in fixed_assets}
        planned_by_id = {asset.planned_asset_id: asset for asset in planned_assets}

        for policy in self.semantic_model.policies.values():
            if policy.residual_rate < 0 or policy.residual_rate > 1:
                anomalies.append(
                    self._anomaly(
                        "ERROR",
                        "DepreciationPolicy",
                        policy.policy_id,
                        "POLICY_RESIDUAL_RATE_RANGE",
                        "Depreciation policy residual rate must be between 0 and 1.",
                    )
                )
            if policy.useful_life_months <= 0:
                anomalies.append(
                    self._anomaly(
                        "ERROR",
                        "DepreciationPolicy",
                        policy.policy_id,
                        "POLICY_USEFUL_LIFE_POSITIVE",
                        "Depreciation policy useful life must be positive.",
                    )
                )
            if policy.asset_category not in self.semantic_model.categories:
                anomalies.append(
                    self._anomaly(
                        "ERROR",
                        "DepreciationPolicy",
                        policy.policy_id,
                        "POLICY_CATEGORY_EXISTS",
                        "Depreciation policy category is not defined in ontology sample data.",
                    )
                )

        for code in self.semantic_model.codes.values():
            if code.policy_id not in self.semantic_model.policies:
                anomalies.append(
                    self._anomaly(
                        "ERROR",
                        "DepreciationCode",
                        code.code_id,
                        "CODE_POLICY_EXISTS",
                        "Depreciation code references an undefined policy.",
                    )
                )

        for asset in fixed_assets:
            anomalies.extend(self._validate_fixed_asset(asset))

        for asset in planned_assets:
            anomalies.extend(self._validate_planned_asset(asset))

        for event in events:
            target_ok = True
            if event.target_asset_id and event.target_asset_id not in fixed_by_id:
                target_ok = False
            if event.target_planned_asset_id and event.target_planned_asset_id not in planned_by_id:
                target_ok = False
            if not target_ok:
                anomalies.append(
                    Anomaly(
                        anomaly_id=f"A-{event.event_id}-TARGET",
                        severity="ERROR",
                        object_type="AssetEvent",
                        object_id=event.event_id,
                        rule_id="EVENT_TARGET_EXISTS",
                        message="Asset event target does not exist in loaded sample data.",
                    )
                )

        return anomalies

    def _validate_fixed_asset(self, asset: FixedAsset) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        if asset.original_cost <= 0:
            anomalies.append(self._anomaly("ERROR", "FixedAsset", asset.asset_id, "FIXED_ASSET_COST_REQUIRED", "Fixed asset original cost must be positive."))
        if asset.in_service_date is None:
            anomalies.append(self._anomaly("ERROR", "FixedAsset", asset.asset_id, "FIXED_ASSET_IN_SERVICE_DATE_REQUIRED", "Fixed asset in-service date is required."))
        anomalies.extend(self._validate_category_code("FixedAsset", asset.asset_id, asset.company, asset.asset_category, asset.depreciation_code))
        return anomalies

    def _validate_planned_asset(self, asset: PlannedAsset) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        if asset.planned_amount <= 0:
            anomalies.append(self._anomaly("ERROR", "PlannedAsset", asset.planned_asset_id, "PLANNED_ASSET_AMOUNT_REQUIRED", "Planned asset amount must be positive."))
        if asset.expected_in_service_date is None:
            anomalies.append(self._anomaly("ERROR", "PlannedAsset", asset.planned_asset_id, "PLANNED_ASSET_IN_SERVICE_DATE_REQUIRED", "Planned asset expected in-service date is required."))
        anomalies.extend(self._validate_category_code("PlannedAsset", asset.planned_asset_id, asset.company, asset.asset_category, asset.depreciation_code))
        return anomalies

    def _validate_category_code(
        self,
        object_type: str,
        object_id: str,
        company: str,
        asset_category: str,
        depreciation_code: str,
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        if asset_category not in self.semantic_model.categories:
            anomalies.append(self._anomaly("ERROR", object_type, object_id, "ASSET_CATEGORY_EXISTS", "Asset category is not defined in ontology sample data."))
        if depreciation_code not in self.semantic_model.codes:
            anomalies.append(self._anomaly("ERROR", object_type, object_id, "DEPRECIATION_CODE_EXISTS", "Depreciation code is not defined in ontology sample data."))
        elif not self.semantic_model.code_is_compatible(asset_category=asset_category, code_id=depreciation_code):
            anomalies.append(self._anomaly("ERROR", object_type, object_id, "DEPRECIATION_CODE_CATEGORY_MATCH", "Depreciation code is incompatible with asset category hierarchy."))
        policy = self.semantic_model.match_policy(company=company, perspective=self.perspective, asset_category=asset_category)
        if policy is None:
            anomalies.append(self._anomaly("ERROR", object_type, object_id, "DEPRECIATION_POLICY_MATCH", "No depreciation policy matches company, perspective, and category."))
        return anomalies

    @staticmethod
    def _anomaly(
        severity: str,
        object_type: str,
        object_id: str,
        rule_id: str,
        message: str,
    ) -> Anomaly:
        return Anomaly(
            anomaly_id=f"A-{object_id}-{rule_id}",
            severity=severity,
            object_type=object_type,
            object_id=object_id,
            rule_id=rule_id,
            message=message,
        )
