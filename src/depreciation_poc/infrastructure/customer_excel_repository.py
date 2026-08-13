from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from depreciation_poc.domain.models import (
    AssetCategory, AssetEvent, DepreciationCode, DepreciationPolicy, FixedAsset,
    ForecastLine, Month, MonthlyDriver, PlannedAsset, money, parse_decimal,
)
from depreciation_poc.infrastructure.xlsx_reader import read_sheet


CODE_METHODS = {
    "AM_年限平均法_当月": ("STRAIGHT_LINE", "CURRENT_MONTH"),
    "AM_年限平均法_次月": ("STRAIGHT_LINE", "NEXT_MONTH"),
    "AM_产量法_次月": ("PRODUCTION", "NEXT_MONTH"),
    "AM_工作量_当月": ("WORKLOAD", "CURRENT_MONTH"),
}

REQUIRED_WORKBOOKS = (
    "资产明细表_资产台账明细_20260812.xlsx",
    "资产相关配置表_20260812.xlsx",
)


class CustomerExcelRepository:
    """Maps the four fixed customer workbooks into the application DTO contract."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self._validate_source_directory()
        self.asset_file = self.data_dir / REQUIRED_WORKBOOKS[0]
        self.config_file = self.data_dir / REQUIRED_WORKBOOKS[1]
        self.organization_file = None
        self.asset_rows = self._sheet(self.asset_file, "在账资产明细")
        self.code_rows = read_sheet(self.config_file, "折旧码")
        self.category_rows = read_sheet(self.config_file, "资产类别表")
        self.block_rows = read_sheet(self.config_file, "所属区块")
        self.organization_rows = self._sheet(self.organization_file, "所属单位表") if self.organization_file else []
        self._codes = self._load_codes()
        self._assets, self._excluded_assets = self._load_assets()
        self._categories = self._load_categories()
        self._policies = self._load_policies()

    def _validate_source_directory(self) -> None:
        if not self.data_dir.is_dir():
            raise FileNotFoundError(f"客户数据目录不存在：{self.data_dir}")
        workbook_names = sorted(path.name for path in self.data_dir.iterdir() if path.is_file() and path.suffix.lower() == ".xlsx")
        expected = sorted(REQUIRED_WORKBOOKS)
        if workbook_names != expected:
            raise ValueError(
                "客户数据目录只能包含当前两份受控 Excel："
                f"{'、'.join(expected)}；当前发现：{'、'.join(workbook_names) or '无 Excel 文件'}。"
            )

    @staticmethod
    def _sheet(path: Path, preferred_name: str) -> list[dict[str, str]]:
        """Support dated customer extracts, such as `在账资产明细-6月底`."""
        from zipfile import ZipFile
        from xml.etree import ElementTree

        try:
            return read_sheet(path, preferred_name)
        except ValueError:
            with ZipFile(path) as archive:
                workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            names = [item.attrib["name"] for item in workbook.findall("main:sheets/main:sheet", {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"})]
            matching = next((name for name in names if name.startswith(preferred_name)), None)
            if matching is None:
                raise
            return read_sheet(path, matching)

    def load_asset_categories(self) -> list[AssetCategory]:
        return list(self._categories)

    def load_depreciation_policies(self) -> list[DepreciationPolicy]:
        return list(self._policies)

    def load_depreciation_codes(self) -> list[DepreciationCode]:
        return list(self._codes)

    def load_fixed_assets(self) -> list[FixedAsset]:
        return list(self._assets)

    def excluded_assets(self) -> list[dict[str, str]]:
        return list(self._excluded_assets)

    def load_planned_assets(self) -> list[PlannedAsset]:
        return []

    def load_asset_events(self) -> list[AssetEvent]:
        return []

    def ledger_snapshot_lines(self, *, scenario_id: str, budget_version: str) -> list[ForecastLine]:
        """Return the source-ledger month as actuals, without recalculating it.

        The customer's values are at the end of the query month. Keeping them
        alongside forward calculations provides a continuous 6/7/8 wide table
        while preserving the distinction between an actual ledger amount and a
        rule-engine forecast.
        """
        snapshot_period = Month.parse(self._snapshot_period())
        code_names = {code.code_id: code.name for code in self._codes}
        lines: list[ForecastLine] = []
        for asset in self._assets:
            method, _start_rule = _method_from_name(code_names.get(asset.depreciation_code, ""))
            current_depreciation = money(asset.snapshot_monthly_depreciation)
            closing_net_value = money(asset.snapshot_net_value)
            opening_accumulated_depreciation = money(max(Decimal("0"), asset.accumulated_depreciation - current_depreciation))
            lines.append(ForecastLine(
                scenario_id=scenario_id,
                budget_version=budget_version,
                asset_id=asset.asset_id,
                planned_asset_id=None,
                asset_source_type="CURRENT",
                company=asset.company,
                department=asset.department,
                cost_center=asset.cost_center,
                profit_center=asset.profit_center,
                asset_category=asset.asset_category,
                depreciation_code=asset.depreciation_code,
                depreciation_policy=f"POLICY-{asset.depreciation_code}",
                depreciation_method=method,
                period=snapshot_period,
                opening_original_cost=money(asset.original_cost),
                opening_accumulated_depreciation=opening_accumulated_depreciation,
                opening_accumulated_impairment=money(asset.accumulated_impairment),
                opening_net_value=money(closing_net_value + current_depreciation),
                addition_amount=Decimal("0"),
                disposal_amount=Decimal("0"),
                impairment_amount=Decimal("0"),
                depreciable_base=money(max(Decimal("0"), asset.original_cost - asset.accumulated_impairment)),
                monthly_depreciation=current_depreciation,
                accumulated_depreciation=money(asset.accumulated_depreciation),
                closing_net_value=closing_net_value,
                source_event_id=f"LEDGER-SNAPSHOT:{asset.asset_id}:{snapshot_period}",
                calculation_rule_id="LEDGER_SNAPSHOT",
                validation_status="SOURCE_SNAPSHOT",
            ))
        return lines

    def source_summary(self) -> dict[str, Any]:
        return {
            "snapshot_period": self._snapshot_period(),
            "asset_count": len(self._assets),
            "excluded_asset_count": len(self._excluded_assets),
            "organization_count": len(self.organization_rows),
            "source_files": [
                *[path.name for path in (self.asset_file, self.config_file, self.organization_file) if path],
            ],
            "organization_source_status": "已加载" if self.organization_file else "未提供组织机构表；计算使用资产台账所属单位字段。",
        }

    def verified_forecast_months(self, start_period: Month, *, maximum: int = 6) -> int:
        """Return the consecutive future months fully covered by customer driver inputs.

        A baseline forecast must not quietly extend past the months for which production
        and workload drivers were supplied. Straight-line assets do not restrict the horizon.
        """
        required_blocks = {
            asset.block_id for asset in self._assets
            if asset.depreciation_code == "Z802" and asset.block_id
        }
        required_workload_targets = {
            asset.organization_id or asset.company
            for asset in self._assets if asset.depreciation_code == "Z901"
        }
        block_periods = {
            (str(_value(row, "所属区块", "区块")), _int(_value(row, "会计年度", "年", "年份")), _int(_value(row, "期间", "月", "月份")))
            for row in self.block_rows
            if _value(row, "折旧码", "折码", "折旧方法") == "Z802"
        }
        workload_periods = {
            (str(_value(row, "所属单位")), _int(_value(row, "会计年度", "年", "年份")), _int(_value(row, "期间", "月", "月份")))
            for row in self._workload_rows_flat()
        }
        covered = 0
        for offset in range(maximum):
            period = start_period.add(offset)
            block_complete = all((str(block), period.year, period.month) in block_periods for block in required_blocks)
            workload_complete = all((target, period.year, period.month) in workload_periods for target in required_workload_targets)
            if not block_complete or not workload_complete:
                break
            covered += 1
        return covered

    def dimension_catalog(self) -> dict[str, Any]:
        """Business labels used by the financial wide-table, derived from the snapshot."""
        return {
            "dimensions": [
                {"id": "department", "label_cn": "所属单位", "description_cn": "按资产所属单位汇总"},
                {"id": "asset_category", "label_cn": "资产类别", "description_cn": "按台账资产类别汇总"},
                {"id": "depreciation_code", "label_cn": "折旧码", "description_cn": "按折旧方法代码汇总"},
                {"id": "asset", "label_cn": "资产", "description_cn": "展开至单项资产"},
            ],
            "category_labels": {
                asset.asset_category: asset.asset_category_name or asset.asset_category
                for asset in self._assets if asset.asset_category
            },
            "asset_labels": {asset.asset_id: asset.name or asset.asset_id for asset in self._assets},
            "depreciation_code_labels": {code.code_id: code.name or code.code_id for code in self._codes},
        }

    def baseline_drivers(self, *, start_period: Month, months: int) -> list[MonthlyDriver]:
        by_period_block: dict[tuple[str, str, int, int], list[dict[str, str]]] = defaultdict(list)
        grouped: dict[tuple[str, str, int, int], list[dict[str, str]]] = defaultdict(list)
        for row in self.block_rows:
            code = _value(row, "折旧码", "折码", "折旧方法")
            block = _value(row, "区块", "所属区块")
            year = _int(_value(row, "会计年度", "年", "年份"))
            month = _int(_value(row, "期间", "月", "每", "月份"))
            if code and block and year and month:
                grouped[(code, block, year, month)].append(row)
                by_period_block[(code, block, year, month)] = grouped[(code, block, year, month)]
        drivers: list[MonthlyDriver] = []
        block_keys = sorted({(code, block) for code, block, _year, _month in by_period_block if code == "Z802"})
        for code, block in block_keys:
            for offset in range(months):
                period = start_period.add(offset)
                rows, source_year, source_month, is_carry_forward = self._block_rows_for_period(
                    by_period_block, code, block, period.year, period.month,
                )
                production = _average(rows, "区块总产量", "月总产量（吨/万方）", "总产量", "当月产量")
                reserves = _average(rows, "区块总储量", "月总储量（吨/万方）", "总储量", "剩余储量")
                depletion_rate = _optional_average(rows, "折耗率")
                company = _value(rows[0], "公司代码", "公司") or "DEFAULT"
                refs = tuple(f"{self.config_file.name}:所属区块:{source_year}-{source_month:02d}:{block}" for _ in rows)
                drivers.append(MonthlyDriver(
                    driver_type="PRODUCTION", period=period, company=company, target_id=block,
                    production=production, reserves=reserves, depletion_rate=depletion_rate, source_refs=refs,
                    assumption_note=(
                        f"使用 {source_year}-{source_month:02d} 区块配置参数。"
                        if not is_carry_forward else f"未提供 {period} 区块参数，沿用 {source_year}-{source_month:02d} 参数作为基准假设。"
                    ),
                ))
        workload_rows = self._workload_rows()
        workload_targets = sorted({asset.organization_id or asset.company for asset in self._assets if asset.depreciation_code == "Z901"})
        for target in workload_targets:
            for offset in range(months):
                period = start_period.add(offset)
                rows, source_year, source_month, is_carry_forward = self._workload_rows_for_period(workload_rows, target, period.year, period.month)
                drivers.append(MonthlyDriver(
                    driver_type="WORKLOAD", period=period, company=_value(rows[0], "公司代码", "公司") if rows else "DEFAULT", target_id=target,
                    workload=_average(rows, "单位数", "工作量"), unit_fee=Decimal("0"),
                    total_amortization=_average(rows, "总计", "当月总摊销额") if rows else None,
                    source_refs=tuple(f"{self.config_file.name}:工作量法:{source_year}-{source_month:02d}:{target}" for _ in rows),
                    assumption_note=(
                        f"使用 {source_year}-{source_month:02d} 工作量法配置参数。"
                        if rows and not is_carry_forward else f"未提供 {period} 工作量法参数，基准场景以零值计算。"
                    ),
                ))
        return drivers

    @staticmethod
    def _block_rows_for_period(by_period_block, code: str, block: str, year: int, month: int):
        exact = by_period_block.get((code, block, year, month))
        if exact:
            return exact, year, month, False
        candidates = [key for key in by_period_block if key[:2] == (code, block) and (key[2], key[3]) < (year, month)]
        if not candidates:
            return [], year, month, True
        source = max(candidates, key=lambda key: (key[2], key[3]))
        return by_period_block[source], source[2], source[3], True

    def _workload_rows(self) -> dict[tuple[str, int, int], list[dict[str, str]]]:
        grouped: dict[tuple[str, int, int], list[dict[str, str]]] = defaultdict(list)
        for row in self._workload_rows_flat():
            target = _value(row, "所属单位")
            year = _int(_value(row, "会计年度", "年", "年份"))
            month = _int(_value(row, "期间", "月", "月份"))
            if target and year and month:
                grouped[(target, year, month)].append(row)
        return grouped

    def _workload_rows_flat(self) -> list[dict[str, str]]:
        try:
            return read_sheet(self.config_file, "工作量法")
        except ValueError:
            return []

    @staticmethod
    def _workload_rows_for_period(rows_by_period, target: str, year: int, month: int):
        exact = rows_by_period.get((target, year, month))
        if exact:
            return exact, year, month, False
        return [], year, month, True

    def _load_codes(self) -> list[DepreciationCode]:
        result: list[DepreciationCode] = []
        for row in self.code_rows:
            code = _value(row, "折旧码", "折旧码编码")
            name = _value(row, "折旧码名称", "名称", "折旧码描述")
            if code:
                result.append(DepreciationCode(code, name or code, "CUSTOMER_ASSET", f"POLICY-{code}"))
        return result

    def _load_categories(self) -> list[AssetCategory]:
        names: dict[str, str] = {}
        for row in self.category_rows:
            category = _value(row, "资产类别编码", "资产类别")
            if category:
                names[category] = _value(row, "资产类别名称", "名称") or category
        for row in self.asset_rows:
            category = _value(row, "资产类别", "资产类别编码")
            if category:
                names.setdefault(category, _value(row, "资产类别名称", "资产类别描述") or category)
        categories = [AssetCategory(category, name, "CUSTOMER_ASSET") for category, name in sorted(names.items())]
        return [AssetCategory("CUSTOMER_ASSET", "客户资产", None), *categories]

    def _load_policies(self) -> list[DepreciationPolicy]:
        names = {item.code_id: item.name for item in self._codes}
        companies = {_value(row, "公司代码", "公司") or "DEFAULT" for row in self.asset_rows}
        policies: list[DepreciationPolicy] = []
        for code, name in names.items():
            method, start_rule = _method_from_name(name)
            for company in companies:
                policies.append(DepreciationPolicy(
                    policy_id=f"POLICY-{code}", name=f"{name}（{code}）", company=company,
                    perspective="BUDGET", asset_category="CUSTOMER_ASSET", method=method,
                    useful_life_months=120, residual_rate=Decimal("0"), start_rule=start_rule,
                ))
        return policies

    def _load_assets(self) -> tuple[list[FixedAsset], list[dict[str, str]]]:
        result: list[FixedAsset] = []
        excluded: list[dict[str, str]] = []
        for index, row in enumerate(self.asset_rows, start=2):
            asset_no = _value(row, "主资产号", "资产主号", "资产编号")
            sub_no = _value(row, "子资产号", "子号") or "0"
            asset_id = f"{asset_no}-{sub_no}" if asset_no else _value(row, "唯一标识", "唯一ID")
            code = _value(row, "折旧码")
            exclusion_reason = _exclusion_reason(row, code)
            if exclusion_reason:
                excluded.append({
                    "asset_id": asset_id or _value(row, "唯一码") or f"ROW-{index}",
                    "asset_name": _value(row, "资产名称"),
                    "reason": exclusion_reason,
                    "source_row": str(index),
                    "status": _value(row, "使用状态", "资产状态"),
                    "net_value": _value(row, "净额", "净值"),
                    "depreciation_code": code,
                })
                continue
            years = _decimal(_value(row, "计划折旧年限", "折旧年限"))
            extra_months = _int(_value(row, "计划折旧月数", "折旧月数"))
            useful_life = int(years * Decimal("12")) + extra_months if years > 0 else None
            residual_raw = _decimal(_value(row, "预计净残值率", "残值率"))
            residual_rate = residual_raw / Decimal("100") if residual_raw > 1 else residual_raw
            result.append(FixedAsset(
                asset_id=asset_id,
                name=_value(row, "资产名称") or asset_id,
                company=_value(row, "公司代码", "公司") or "DEFAULT",
                department=_value(row, "资产所属单位名称", "所属单位名称", "所属单位") or "未分配单位",
                cost_center=_value(row, "成本中心") or "未分配成本中心",
                profit_center=_value(row, "利润中心") or "未分配利润中心",
                asset_category=_value(row, "资产类别", "资产类别编码") or "CUSTOMER_ASSET",
                depreciation_code=code,
                original_cost=_decimal(_value(row, "资产原值", "原值")),
                in_service_date=_date_value(_value(row, "资本化日期", "启用日期", "投产日期")),
                accumulated_depreciation=_decimal(_value(row, "累计折旧")),
                accumulated_impairment=_decimal(_value(row, "累计减值", "累计减值准备")),
                status=_value(row, "资产状态", "使用状态") or "在账",
                block_id=_value(row, "所属区块", "区块") or None,
                useful_life_months=useful_life,
                residual_rate=residual_rate,
                asset_type=_value(row, "资产类型", "资产类型名称"),
                source_row=index,
                asset_category_name=_value(row, "资产类别名称") or _value(row, "资产类别", "资产类别编码"),
                asset_major_category=_value(row, "资产大类"),
                asset_major_category_name=_value(row, "资产大类描述"),
                organization_id=_value(row, "所属单位"),
                snapshot_monthly_depreciation=_decimal(_value(row, "本月折旧", "当月折旧")),
                snapshot_net_value=_decimal(_value(row, "净额", "净值")),
            ))
        return result, excluded

    def _snapshot_period(self) -> str:
        if not self.asset_rows:
            return ""
        year = _int(_value(self.asset_rows[0], "查询年"))
        month = _int(_value(self.asset_rows[0], "查询月"))
        return f"{year:04d}-{month:02d}" if year and month else ""


def _value(row: dict[str, str], *names: str) -> str:
    for name in names:
        if row.get(name, "") not in (None, ""):
            return str(row[name]).strip()
    return ""


def _decimal(value: str) -> Decimal:
    try:
        return parse_decimal(value.replace(",", ""))
    except Exception:
        return Decimal("0")


def _int(value: str) -> int:
    try:
        return int(Decimal(value))
    except Exception:
        return 0


def _average(rows: list[dict[str, str]], *names: str) -> Decimal:
    values = [_decimal(_value(row, *names)) for row in rows]
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else Decimal("0")


def _optional_average(rows: list[dict[str, str]], *names: str) -> Decimal | None:
    populated = [_value(row, *names) for row in rows]
    values = [_decimal(value) for value in populated if value not in (None, "")]
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else None


def _exclusion_reason(row: dict[str, str], depreciation_code: str) -> str | None:
    """Business exclusion rules confirmed for the customer June ledger snapshot."""
    if not depreciation_code:
        return "折旧码为空，不参与折旧计算"
    if _value(row, "资产停用"):
        return "资产已停用，不参与折旧计算"
    if _value(row, "资产剔除状态"):
        return "资产已标记剔除，不参与折旧计算"
    if _value(row, "不活动日期"):
        return "资产处于不活动状态，不参与折旧计算"
    if _decimal(_value(row, "净额", "净值")) <= Decimal("0"):
        return "资产净额为零，不参与折旧计算"
    return None


def _method_from_name(name: str) -> tuple[str, str]:
    for text, mapping in CODE_METHODS.items():
        if text in name:
            return mapping
    if "产量" in name:
        return "PRODUCTION", "NEXT_MONTH"
    if "工作量" in name:
        return "WORKLOAD", "CURRENT_MONTH"
    return "STRAIGHT_LINE", "NEXT_MONTH"


def _date_value(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        try:
            return date(1899, 12, 30) + timedelta(days=float(value))
        except ValueError:
            return None
