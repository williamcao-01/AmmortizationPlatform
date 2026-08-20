"""Build a JSON data package for the customer-baseline depreciation validation workbook."""
from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from depreciation_poc.app.demo_server import DemoState
from depreciation_poc.domain.models import Month, money
from depreciation_poc.infrastructure.customer_excel_repository import CustomerExcelRepository


def amount(value: object) -> float:
    return float(Decimal(str(value or "0")))


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the customer baseline validation package.")
    parser.add_argument("--customer-data-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    customer_data_dir = Path(args.customer_data_dir)
    repository = CustomerExcelRepository(customer_data_dir)
    snapshot_period = Month.parse(str(repository.source_summary()["snapshot_period"]))
    start_period = snapshot_period.add(1)
    months = repository.verified_forecast_months(start_period, maximum=6)
    if months <= 0:
        raise ValueError("当前客户配置未覆盖任何未来月份，不能生成核验包。")

    with tempfile.TemporaryDirectory(prefix="asset-poc-validation-") as temporary:
        root = Path(temporary)
        state = DemoState(
            customer_data_dir=customer_data_dir,
            graph_db_path=root / "graph.sqlite",
            business_db_path=root / "business.sqlite",
            start_period=start_period,
            months=months,
        )
        try:
            assets = state.repository.load_fixed_assets()
            asset_by_id = {asset.asset_id: asset for asset in assets}
            lines = state.business_store.forecast_lines(scenario_id="BASELINE", limit=10000)
            rules = {
                (item["asset_ref"], item["period"]): item
                for item in state.business_store.rule_executions(scenario_id="BASELINE")
            }
            periods = sorted({str(line["period"]) for line in lines})
            category_names = state.wide_table_dimension_catalog().get("category_labels", {})
            code_names = state.wide_table_dimension_catalog().get("depreciation_code_labels", {})

            source_assets = [
                {
                    "asset_id": asset.asset_id,
                    "asset_name": asset.name,
                    "company": asset.company,
                    "department": asset.department,
                    "cost_center": asset.cost_center,
                    "profit_center": asset.profit_center,
                    "asset_category": asset.asset_category,
                    "asset_category_name": asset.asset_category_name,
                    "asset_major_category": asset.asset_major_category,
                    "asset_major_category_name": asset.asset_major_category_name,
                    "depreciation_code": asset.depreciation_code,
                    "depreciation_code_name": code_names.get(asset.depreciation_code, asset.depreciation_code),
                    "original_cost": amount(asset.original_cost),
                    "in_service_date": str(asset.in_service_date or ""),
                    "accumulated_depreciation": amount(asset.accumulated_depreciation),
                    "accumulated_impairment": amount(asset.accumulated_impairment),
                    "useful_life_months": asset.useful_life_months or 0,
                    "residual_rate": amount(asset.residual_rate),
                    "status": asset.status,
                    "block_id": asset.block_id or "",
                    "asset_type": asset.asset_type,
                    "source_row": asset.source_row or 0,
                }
                for asset in sorted(assets, key=lambda item: item.asset_id)
            ]

            detail_rows: list[dict[str, object]] = []
            for line in sorted(lines, key=lambda item: (str(item["asset_id"] or item["planned_asset_id"]), str(item["period"]))):
                asset_ref = str(line["asset_id"] or line["planned_asset_id"] or "")
                asset = asset_by_id.get(asset_ref)
                rule = rules.get((asset_ref, str(line["period"])))
                # Snapshot actuals are source-ledger records, not rule-engine runs.
                if rule is None:
                    rule = {
                        "rule_id": line["calculation_rule_id"],
                        "branch_id": line["calculation_rule_id"],
                        "formula_cn": "来源台账实际折旧额，不重新计算。",
                        "conclusion_cn": "读取台账快照。",
                        "inputs": {},
                    }
                inputs = rule["inputs"]
                detail_rows.append(
                    {
                        "scenario_id": line["scenario_id"],
                        "period": line["period"],
                        "asset_id": asset_ref,
                        "asset_name": asset.name if asset else asset_ref,
                        "company": line["company"],
                        "department": line["department"],
                        "cost_center": line["cost_center"],
                        "profit_center": line["profit_center"],
                        "asset_category": line["asset_category"],
                        "asset_category_name": category_names.get(line["asset_category"], line["asset_category"]),
                        "depreciation_code": line["depreciation_code"],
                        "depreciation_code_name": code_names.get(line["depreciation_code"], line["depreciation_code"]),
                        "depreciation_method": line["depreciation_method"],
                        "rule_id": rule["rule_id"],
                        "branch_id": rule["branch_id"],
                        "source_row": asset.source_row if asset else "",
                        "opening_original_cost": amount(line["opening_original_cost"]),
                        "opening_accumulated_depreciation": amount(line["opening_accumulated_depreciation"]),
                        "opening_accumulated_impairment": amount(line["opening_accumulated_impairment"]),
                        "opening_net_value": amount(line["opening_net_value"]),
                        "addition_amount": amount(line["addition_amount"]),
                        "disposal_amount": amount(line["disposal_amount"]),
                        "impairment_amount": amount(line["impairment_amount"]),
                        "depreciable_base": amount(line["depreciable_base"]),
                        "monthly_depreciation": amount(line["monthly_depreciation"]),
                        "accumulated_depreciation": amount(line["accumulated_depreciation"]),
                        "closing_net_value": amount(line["closing_net_value"]),
                        "validation_status": line["validation_status"],
                        "formula_cn": rule["formula_cn"],
                        "conclusion_cn": rule["conclusion_cn"],
                        "remaining_depreciable_amount": amount(inputs.get("剩余可折旧金额")),
                        "remaining_months": amount(inputs.get("剩余折旧月数")),
                        "rule_opening_net_value": amount(inputs.get("期初油气资产账面净额") or inputs.get("期初净值")),
                        "production_rate": amount(inputs.get("当期折耗率") or inputs.get("折耗率")),
                        "workload_total_amortization": amount(inputs.get("当月总摊销额")),
                        "workload_asset_opening_net": amount(inputs.get("资产期初净额") or inputs.get("资产期初净值")),
                        "workload_pool_opening_net": amount(inputs.get("计算采用资产池期初净额") or inputs.get("资产池期初净额")),
                        "source_event_id": line["source_event_id"] or "",
                    }
                )

            def dimensions(key: str) -> list[dict[str, object]]:
                values = sorted({str(line[key]) for line in lines})
                result = []
                for value in values:
                    result.append({
                        "key": value,
                        "label": category_names.get(value, value) if key == "asset_category" else value,
                    })
                return result

            rule_errors = 0
            close_errors = 0
            continuity_errors = 0
            per_asset: dict[str, list[dict[str, object]]] = defaultdict(list)
            for row in detail_rows:
                branch = str(row["branch_id"])
                calculated: Decimal | None = None
                if branch in ("STRAIGHT_LINE", "IMPAIRMENT_RECALC"):
                    calculated = money(Decimal(str(row["remaining_depreciable_amount"])) / Decimal(str(row["remaining_months"])))
                elif branch == "NORMAL_PRODUCTION":
                    calculated = money(Decimal(str(row["rule_opening_net_value"])) * Decimal(str(row["production_rate"])))
                elif branch in ("NO_PRODUCTION", "BEFORE_START", "LIFE_EXPIRED", "NO_WORKLOAD", "DISPOSAL_STOP"):
                    calculated = Decimal("0.00")
                elif branch in ("NO_RESERVES", "PRODUCTION_EXCEEDS_RESERVES"):
                    calculated = money(Decimal(str(row["rule_opening_net_value"])))
                elif branch == "WORKLOAD_ALLOCATION":
                    calculated = money(
                        Decimal(str(row["workload_total_amortization"]))
                        * Decimal(str(row["workload_asset_opening_net"]))
                        / Decimal(str(row["workload_pool_opening_net"]))
                    )
                if branch == "LEDGER_SNAPSHOT":
                    continue
                if calculated is None or calculated != Decimal(str(row["monthly_depreciation"])):
                    rule_errors += 1
                closing_expected = money(
                    Decimal(str(row["opening_original_cost"]))
                    - Decimal(str(row["accumulated_depreciation"]))
                    - Decimal(str(row["opening_accumulated_impairment"]))
                    - Decimal(str(row["impairment_amount"]))
                    - Decimal(str(row["disposal_amount"]))
                )
                if closing_expected != Decimal(str(row["closing_net_value"])):
                    close_errors += 1
                per_asset[str(row["asset_id"])].append(row)
            for rows in per_asset.values():
                rows.sort(key=lambda item: str(item["period"]))
                for current, following in zip(rows, rows[1:]):
                    if Decimal(str(current["closing_net_value"])) != Decimal(str(following["opening_net_value"])):
                        continuity_errors += 1

            monthly_totals = [
                {
                    "period": period,
                    "monthly_depreciation": amount(sum(Decimal(str(row["monthly_depreciation"])) for row in detail_rows if row["period"] == period)),
                }
                for period in periods
            ]
            branch_counts = [
                {"branch_id": branch, "line_count": count}
                for branch, count in sorted(Counter(str(row["branch_id"]) for row in detail_rows).items())
            ]
            method_counts = [
                {"method": method, "line_count": count}
                for method, count in sorted(Counter(str(row["depreciation_method"]) for row in detail_rows).items())
            ]
            checks = [
                {"check": "源台账资产数 = 预测唯一资产数", "actual": len({row["asset_id"] for row in detail_rows}), "expected": len(source_assets), "difference": len({row["asset_id"] for row in detail_rows}) - len(source_assets), "note": "按资产编号核对。"},
                {"check": "明细数 = 资产数 × 实际及预测期间", "actual": len(detail_rows), "expected": len(source_assets) * len(periods), "difference": len(detail_rows) - len(source_assets) * len(periods), "note": "每项纳入计算资产覆盖每个期间。"},
                {"check": "规则执行记录数 = 预测明细数", "actual": len(rules), "expected": len(source_assets) * (len(periods) - 1), "difference": len(rules) - len(source_assets) * (len(periods) - 1), "note": "台账实际月不重新执行规则；每条预测明细均有规则执行轨迹。"},
                {"check": "月末净值滚动复核差异数", "actual": close_errors, "expected": 0, "difference": close_errors, "note": "月末净值 = 原值 - 累计折旧 - 累计减值 - 当月减值 - 当月减少。"},
                {"check": "资产跨月净值衔接差异数", "actual": continuity_errors, "expected": 0, "difference": continuity_errors, "note": "当月期末净值应等于下月期初净值。"},
                {"check": "按规则输入回放月折旧差异数", "actual": rule_errors, "expected": 0, "difference": rule_errors, "note": "按记录的规则分支、精确输入和四舍五入规则复算。"},
            ]
            drivers = [
                {
                    "driver_type": item.driver_type,
                    "period": str(item.period),
                    "company": item.company,
                    "target_id": item.target_id,
                    "production": amount(item.production),
                    "reserves": amount(item.reserves),
                    "workload": amount(item.workload),
                    "unit_fee": amount(item.unit_fee),
                    "assumption_note": item.assumption_note,
                    "source_refs": " | ".join(item.source_refs),
                }
                for item in state.repository.baseline_drivers(start_period=state.start_period, months=state.months)
            ]
            package = {
                "metadata": {
                    "title": "资产折旧预测基准场景核验包",
                    "scenario_id": "BASELINE",
                    "snapshot_period": state.repository.source_summary()["snapshot_period"],
                    "forecast_start": str(state.start_period),
                    "forecast_end": str(state.start_period.add(state.months - 1)),
                    "forecast_months": state.months,
                    "asset_count": len(source_assets),
                    "forecast_line_count": len(detail_rows),
                    "rule_execution_count": len(rules),
                    "calculation_version": state.calculation_version,
                    "source_files": state.repository.source_summary()["source_files"],
                    "baseline_assumption": "仅使用当前源数据已覆盖的 2026-07、2026-08 驱动月份；不对后续月份延用参数。",
                    "verification_scope": "本包验证源 Excel 装载、规则重算、逐项明细与各维度汇总勾稽；未包含 SAP 预测结果自动对账。",
                },
                "periods": periods,
                "source_assets": source_assets,
                "drivers": drivers,
                "detail_rows": detail_rows,
                "departments": dimensions("department"),
                "categories": dimensions("asset_category"),
                "monthly_totals": monthly_totals,
                "branch_counts": branch_counts,
                "method_counts": method_counts,
                "checks": checks,
            }
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            state.close()


if __name__ == "__main__":
    main()
