from __future__ import annotations

from decimal import Decimal
from typing import Any


CATEGORY_LABEL_CN = {
    "FIXED_ASSET": "固定资产",
    "PRODUCTION_EQUIPMENT": "生产设备",
    "MACHINE_EQUIPMENT": "机器设备",
    "INJECTION_EQUIPMENT": "注塑设备",
    "ELECTRONIC_EQUIPMENT": "电子设备",
    "BUILDING": "房屋建筑物",
}

POLICY_LABEL_CN = {
    "P_MACHINE_CN_BUDGET": "机器设备预算折旧政策",
    "P_ELECTRONIC_CN_BUDGET": "电子设备预算折旧政策",
    "P_BUILDING_CN_BUDGET": "房屋建筑物预算折旧政策",
    "P_BAD_RESIDUAL_TEST": "残值率校验测试政策",
}

DEPRECIATION_CODE_LABEL_CN = {
    "CODE_MACHINE_10Y": "机器设备 10 年折旧码",
    "CODE_ELECTRONIC_3Y": "电子设备 3 年折旧码",
    "CODE_BUILDING_20Y": "房屋建筑物 20 年折旧码",
    "Z111": "年限平均法（当月开始计提）",
    "Z112": "年限平均法（次月开始计提）",
    "Z802": "产量法（次月开始计提）",
    "Z901": "工作量法（当月开始计提）",
}

METHOD_LABEL_CN = {
    "STRAIGHT_LINE": "直线法",
    "PRODUCTION": "产量法",
    "WORKLOAD": "工作量法",
}

SEVERITY_LABEL_CN = {
    "ERROR": "阻断错误",
    "WARNING": "预警",
    "INFO": "提示",
}

OBJECT_TYPE_LABEL_CN = {
    "FixedAsset": "存量固定资产",
    "PlannedAsset": "计划资本开支",
    "Department": "部门",
    "CostCenter": "成本中心",
    "ProfitCenter": "利润中心",
    "AssetEvent": "资产事件",
    "DepreciationPolicy": "折旧政策",
    "DepreciationCode": "折旧码",
    "AssetCategory": "资产类别",
    "Scenario": "测算场景",
    "ForecastLine": "折旧预测明细",
    "Anomaly": "异常",
    "DepreciationMethod": "折旧方法",
    "CalculationRule": "计算规则",
    "Block": "所属区块",
    "MonthlyDriver": "月度驱动参数",
    "ScenarioAssumption": "场景假设",
}

ASSET_SOURCE_TYPE_LABEL_CN = {
    "CURRENT": "存量资产",
    "PLANNED": "计划资产",
}

EVENT_LABEL_CN = {
    "BASE": "基础折旧",
    "ADDITION": "新增转固",
    "DISPOSAL": "资产减少",
    "IMPAIRMENT": "资产减值",
    "PLANNED_ADDITION": "计划新增转固",
}

START_RULE_LABEL_CN = {
    "NEXT_MONTH": "次月开始计提",
    "CURRENT_MONTH": "当月开始计提",
}

GRAPH_PREDICATE_LABEL_CN = {
    "rdf:type": "对象类型",
    "rdfs:label": "中文/业务名称",
    "rdfs:subClassOf": "上级资产类别",
    "rdfs:subClassOf*": "资产类别继承链",
    "appliesToCompany": "适用公司",
    "appliesToPerspective": "适用测算口径",
    "appliesToCategory": "适用资产类别",
    "method": "折旧方法",
    "usefulLifeMonths": "使用年限(月)",
    "residualRate": "残值率",
    "startRule": "开始计提规则",
    "allowedForCategory": "折旧码适用类别",
    "mapsToPolicy": "折旧码映射政策",
}

RULE_LABEL_CN = {
    "POLICY_RESIDUAL_RATE_RANGE": "政策残值率范围校验",
    "POLICY_USEFUL_LIFE_POSITIVE": "政策使用年限校验",
    "POLICY_CATEGORY_EXISTS": "政策资产类别存在性校验",
    "CODE_POLICY_EXISTS": "折旧码政策引用校验",
    "FIXED_ASSET_COST_REQUIRED": "存量资产原值必填校验",
    "FIXED_ASSET_IN_SERVICE_DATE_REQUIRED": "存量资产转固日期必填校验",
    "PLANNED_ASSET_AMOUNT_REQUIRED": "计划资产金额必填校验",
    "PLANNED_ASSET_IN_SERVICE_DATE_REQUIRED": "计划资产预计转固日期必填校验",
    "ASSET_CATEGORY_EXISTS": "资产类别存在性校验",
    "DEPRECIATION_CODE_EXISTS": "折旧码存在性校验",
    "DEPRECIATION_CODE_CATEGORY_MATCH": "折旧码与资产类别匹配校验",
    "DEPRECIATION_POLICY_MATCH": "折旧政策匹配校验",
    "EVENT_TARGET_EXISTS": "资产事件目标存在性校验",
}

ANOMALY_CN = {
    "POLICY_RESIDUAL_RATE_RANGE": (
        "折旧政策的残值率必须在 0 到 1 之间。",
        "请修正政策残值率后重新测算。",
        "政策参数无效，受影响资产不能可靠计算折旧。",
    ),
    "POLICY_USEFUL_LIFE_POSITIVE": (
        "折旧政策的使用年限必须大于 0。",
        "请补充有效的使用年限(月)。",
        "缺少有效年限会导致月折旧额无法计算。",
    ),
    "POLICY_CATEGORY_EXISTS": (
        "折旧政策引用的资产类别未在图谱样本中定义。",
        "请先维护资产类别，或调整政策适用类别。",
        "政策无法进入资产类别匹配链。",
    ),
    "CODE_POLICY_EXISTS": (
        "折旧码引用了不存在的折旧政策。",
        "请维护折旧码到有效政策的映射。",
        "使用该折旧码的资产无法追溯政策依据。",
    ),
    "FIXED_ASSET_COST_REQUIRED": (
        "存量资产原值必须大于 0。",
        "请补齐资产原值后重新测算。",
        "资产会被阻断，不进入折旧预测。",
    ),
    "FIXED_ASSET_IN_SERVICE_DATE_REQUIRED": (
        "存量资产缺少转固日期。",
        "请补齐转固日期。",
        "无法判断开始计提月份，资产会被阻断。",
    ),
    "PLANNED_ASSET_AMOUNT_REQUIRED": (
        "计划资产金额必须大于 0。",
        "请补齐计划资本化金额。",
        "计划资产会被阻断，不进入新增折旧预测。",
    ),
    "PLANNED_ASSET_IN_SERVICE_DATE_REQUIRED": (
        "计划资产缺少预计转固日期。",
        "请补齐预计转固日期。",
        "无法判断新增折旧开始月份，计划资产会被阻断。",
    ),
    "ASSET_CATEGORY_EXISTS": (
        "资产类别未在图谱样本中定义。",
        "请修正资产类别编码或维护类别主数据。",
        "资产无法参与类别继承和政策匹配。",
    ),
    "DEPRECIATION_CODE_EXISTS": (
        "折旧码未在样本主数据中定义。",
        "请修正折旧码或维护折旧码主数据。",
        "资产无法追溯折旧码与政策映射。",
    ),
    "DEPRECIATION_CODE_CATEGORY_MATCH": (
        "折旧码与资产类别层级不兼容。",
        "请选择适用于该资产类别或其上级类别的折旧码。",
        "资产会被阻断，避免按错误政策计提。",
    ),
    "DEPRECIATION_POLICY_MATCH": (
        "未找到适用于公司、测算口径和资产类别的折旧政策。",
        "请维护对应政策或调整资产类别。",
        "资产无法取得使用年限、残值率和开始计提规则。",
    ),
    "EVENT_TARGET_EXISTS": (
        "资产事件指向的资产不存在。",
        "请修正事件目标资产编号。",
        "事件不会被纳入有效折旧影响链。",
    ),
}


def label(mapping: dict[str, str], value: Any, default: str | None = None) -> str:
    text = "" if value is None else str(value)
    return mapping.get(text, default if default is not None else text or "-")


def category_label(value: Any) -> str:
    return label(CATEGORY_LABEL_CN, value)


def policy_label(value: Any) -> str:
    text = "" if value is None else str(value)
    if text.startswith("POLICY-Z"):
        return f"{depreciation_code_label(text.removeprefix('POLICY-'))}对应折旧政策"
    return label(POLICY_LABEL_CN, value)


def depreciation_code_label(value: Any) -> str:
    return label(DEPRECIATION_CODE_LABEL_CN, value)


def method_label(value: Any) -> str:
    return label(METHOD_LABEL_CN, value)


def start_rule_label(value: Any) -> str:
    return label(START_RULE_LABEL_CN, value)


def percent_label(value: Decimal | str | int | None) -> str:
    if value in (None, ""):
        return "-"
    return f"{(Decimal(str(value)) * Decimal('100')).normalize()}%"


def local_graph_id(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.split(":", maxsplit=1)[1] if ":" in text else text


def graph_node_label(value: Any) -> str:
    text = "" if value is None else str(value)
    local = local_graph_id(text)
    if text.startswith("category:"):
        return category_label(local)
    if text.startswith("policy:"):
        return policy_label(local)
    if text.startswith("code:"):
        return depreciation_code_label(local)
    return label(
        {
            "AssetCategory": "资产类别",
            "DepreciationPolicy": "折旧政策",
            "DepreciationCode": "折旧码",
            "BUDGET": "预算口径",
            "CN01": "中国公司 CN01",
        },
        local,
    )


def calculation_rule_label(rule_id: Any) -> str:
    text = "" if rule_id is None else str(rule_id)
    parts = text.split(":")
    if len(parts) == 3:
        return f"{policy_label(parts[0])} / {method_label(parts[1])} / {start_rule_label(parts[2])}"
    return label(RULE_LABEL_CN, text)


def decorate_anomaly(row: dict[str, Any]) -> dict[str, Any]:
    rule_id = str(row.get("rule_id") or "")
    message_cn, suggestion_cn, impact_cn = ANOMALY_CN.get(
        rule_id,
        (
            row.get("message") or "发现待处理的数据或规则异常。",
            "请核对相关主数据和折旧规则。",
            "可能影响折旧预测准确性。",
        ),
    )
    severity = str(row.get("severity") or "")
    object_type = str(row.get("object_type") or "")
    return {
        **row,
        "severity_label_cn": label(SEVERITY_LABEL_CN, severity),
        "object_type_label_cn": label(OBJECT_TYPE_LABEL_CN, object_type),
        "rule_label_cn": label(RULE_LABEL_CN, rule_id),
        "message_cn": message_cn,
        "suggestion_cn": suggestion_cn,
        "impact_cn": impact_cn,
        "is_blocking": severity == "ERROR",
    }


def semantic_catalog() -> dict[str, dict[str, str]]:
    return {
        "policies": POLICY_LABEL_CN,
        "categories": CATEGORY_LABEL_CN,
        "depreciation_codes": DEPRECIATION_CODE_LABEL_CN,
        "rules": RULE_LABEL_CN,
        "severity": SEVERITY_LABEL_CN,
        "object_types": OBJECT_TYPE_LABEL_CN,
        "asset_source_types": ASSET_SOURCE_TYPE_LABEL_CN,
        "events": EVENT_LABEL_CN,
        "start_rules": START_RULE_LABEL_CN,
        "graph_predicates": GRAPH_PREDICATE_LABEL_CN,
        "methods": METHOD_LABEL_CN,
    }
