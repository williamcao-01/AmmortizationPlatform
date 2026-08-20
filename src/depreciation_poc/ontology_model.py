from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from depreciation_poc.semantic_labels import (
    ASSET_SOURCE_TYPE_LABEL_CN,
    OBJECT_TYPE_LABEL_CN,
    category_label,
    depreciation_code_label,
    policy_label,
)
from depreciation_poc.source_schema import (
    ASSET_CATEGORY_POLICY_FIELDS,
    ASSET_MASTER_FIELDS,
    DEPRECIATION_CODE_FIELDS,
    MONTHLY_DRIVER_SOURCE_FIELDS,
    ORGANIZATION_FIELDS,
    PRODUCTION_DRIVER_FIELDS,
    WORKLOAD_DRIVER_FIELDS,
    source_value_type,
)


@dataclass(frozen=True)
class PropertyDefinition:
    property_id: str
    label_cn: str
    value_type: str


@dataclass(frozen=True)
class ObjectTypeDefinition:
    type_id: str
    label_cn: str
    description_cn: str
    properties: list[PropertyDefinition] = field(default_factory=list)


@dataclass(frozen=True)
class LinkTypeDefinition:
    type_id: str
    label_cn: str
    source_type: str
    target_type: str
    description_cn: str


@dataclass(frozen=True)
class ActionTypeDefinition:
    type_id: str
    label_cn: str
    target_types: list[str]
    description_cn: str


@dataclass(frozen=True)
class FunctionTypeDefinition:
    type_id: str
    label_cn: str
    description_cn: str


@dataclass(frozen=True)
class ObjectInstance:
    object_id: str
    object_type: str
    label_cn: str
    subtitle_cn: str
    properties: dict[str, Any]
    source_system: str
    technical_ref: str
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LinkInstance:
    link_id: str
    link_type: str
    source_object_id: str
    target_object_id: str
    label_cn: str
    business_text: str
    inferred: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)


def prop(property_id: str, label_cn: str, value_type: str = "string") -> PropertyDefinition:
    return PropertyDefinition(property_id, label_cn, value_type)


OBJECT_TYPES = [
    ObjectTypeDefinition("FixedAsset", "存量资产", "已经投产并进入折旧预测的固定资产。", [
        prop("asset_ref", "资产编号"), prop("name", "资产名称"), prop("department", "部门"),
        prop("organization_id", "所属单位编码"),
        prop("asset_category", "资产类别"), prop("depreciation_code", "折旧码"),
        prop("original_cost", "资产原值", "money"), prop("accumulated_depreciation", "累计折旧", "money"),
        prop("accumulated_impairment", "累计减值", "money"), prop("snapshot_net_value", "期末账面净额", "money"),
        prop("in_service_date", "投产日期", "date"),
        prop("calculation_included", "是否纳入计算"), prop("exclusion_reason", "计算排除原因"),
        prop("source_row", "源表行号", "integer"),
        *[prop(field, field, source_value_type(field)) for field in ASSET_MASTER_FIELDS],
    ]),
    ObjectTypeDefinition("PlannedAsset", "计划资产", "预算期内计划资本化并形成折旧的资产。", [
        prop("asset_ref", "计划资产编号"), prop("name", "计划资产名称"), prop("department", "部门"),
        prop("asset_category", "资产类别"), prop("depreciation_code", "折旧码"),
        prop("planned_amount", "计划金额", "money"), prop("expected_in_service_date", "预计投产日期", "date"),
    ]),
    ObjectTypeDefinition("Department", "部门", "资产归属和预算责任部门。", [
        prop("code", "所属单位编码"), prop("name", "所属单位名称"), prop("short_name", "单位简称"),
        prop("parent_code", "父级单位编码"), prop("level", "单位级别"),
        prop("is_detail", "是否明细单位"), prop("enabled", "是否启用"),
        *[prop(field, field, source_value_type(field)) for field in ORGANIZATION_FIELDS],
    ]),
    ObjectTypeDefinition("CostCenter", "成本中心", "承接折旧费用的成本中心。", [
        prop("code", "成本中心编码"), prop("name", "成本中心名称"), prop("description", "成本中心描述"),
        prop("company", "公司代码"),
    ]),
    ObjectTypeDefinition("ProfitCenter", "利润中心", "资产所属利润中心。", [
        prop("code", "利润中心编码"), prop("name", "利润中心名称"), prop("description", "利润中心描述"),
        prop("company", "公司代码"),
    ]),
    ObjectTypeDefinition("AssetCategory", "资产类别", "承载继承、折旧码兼容性和政策匹配的资产分类。", [
        prop("category_id", "类别编码"), prop("name", "类别名称"), prop("parent_id", "上级类别"),
    ]),
    ObjectTypeDefinition("DepreciationCode", "折旧码", "资产台账上的折旧规则编码。", [
        prop("code_id", "折旧码"), prop("asset_category", "适用类别"), prop("policy_id", "映射政策"),
        *[prop(field, field, source_value_type(field)) for field in DEPRECIATION_CODE_FIELDS],
    ]),
    ObjectTypeDefinition("DepreciationPolicy", "折旧政策", "规定折旧方法、年限、残值率和开始计提规则。", [
        prop("policy_id", "政策编号"), prop("company", "公司"), prop("asset_category", "适用类别"),
        prop("method", "折旧方法"), prop("useful_life_months", "使用年限(月)", "integer"),
        prop("residual_rate", "残值率", "decimal"), prop("start_rule", "开始计提规则"),
    ]),
    ObjectTypeDefinition("AssetEvent", "资产事件", "减少、减值等改变折旧结果的业务事件。", [
        prop("event_id", "事件编号"), prop("event_type", "事件类型"), prop("amount", "影响金额", "money"),
        prop("effective_date", "生效日期", "date"), prop("description", "说明"),
    ]),
    ObjectTypeDefinition("Scenario", "测算场景", "基准或 What-if 折旧测算版本。", [
        prop("scenario_id", "场景编号"), prop("base_scenario_id", "基准场景"),
        prop("budget_version", "预算版本"), prop("description", "场景说明"),
    ]),
    ObjectTypeDefinition("ForecastLine", "折旧预测明细", "资产在某个月份的折旧计算结果。", [
        prop("scenario_id", "场景编号"), prop("asset_ref", "资产编号"),
        prop("first_depreciation_period", "首次计提月份"), prop("forecast_depreciation_total", "预测期折旧合计", "money"),
    ]),
    ObjectTypeDefinition("Anomaly", "异常", "阻断或提示类数据/规则问题。", [
        prop("anomaly_id", "异常编号"), prop("severity", "级别"), prop("object_id", "影响对象"),
        prop("rule_id", "规则"), prop("message_cn", "问题说明"), prop("suggestion_cn", "处理建议"),
    ]),
    ObjectTypeDefinition("DepreciationMethod", "折旧方法", "定义资产价值在预算期内分摊或折耗的计算方法。", [
        prop("method_id", "方法编码"), prop("name", "方法名称"),
    ]),
    ObjectTypeDefinition("CalculationRule", "计算规则", "规则文档中可执行的计算分支。", [
        prop("rule_id", "规则编号"), prop("formula_cn", "计算公式"), prop("rate_formula_cn", "比率公式"),
        prop("amount_formula_cn", "金额公式"), prop("cap_rule_cn", "金额封顶规则"), prop("description_cn", "业务说明"),
    ]),
    ObjectTypeDefinition("Block", "所属区块", "产量法资产归属的业务区块。", [
        prop("block_id", "区块编号"), prop("company", "公司"),
    ]),
    ObjectTypeDefinition("MonthlyDriver", "月度驱动参数", "产量法或工作量法在指定月份的业务输入。", [
        prop("period", "月份"), prop("driver_type", "驱动类型"), prop("target_id", "影响对象"),
        prop("production", "当期产量", "decimal"), prop("total_reserves", "当期总储量", "decimal"),
        prop("source_depletion_rate", "源表折耗率", "decimal"), prop("calculated_depletion_rate", "公式计算折耗率", "decimal"),
        prop("total_amortization", "当月总摊销额", "money"), prop("pool_opening_net_value", "资产池期初净额", "money"),
        prop("source_row", "源表行号", "integer"),
        *[prop(field, field, source_value_type(field)) for field in MONTHLY_DRIVER_SOURCE_FIELDS],
    ]),
    ObjectTypeDefinition("AssetCategoryPolicyConfig", "资产类别政策配置", "资产类别表中的逐行折旧配置记录。", [
        prop("record_id", "配置记录编号"), prop("source_row", "源表行号", "integer"),
        *[prop(field, field, source_value_type(field)) for field in ASSET_CATEGORY_POLICY_FIELDS],
    ]),
    ObjectTypeDefinition("SourceSnapshot", "源数据快照", "当前受控Excel源文件及导入覆盖信息。", [
        prop("snapshot_id", "快照编号"), prop("snapshot_period", "快照月份"), prop("source_files", "源文件"),
        prop("asset_count", "有效资产数", "integer"), prop("excluded_asset_count", "排除资产数", "integer"),
        prop("asset_source_row_count", "资产源记录数", "integer"), prop("organization_count", "组织记录数", "integer"),
        prop("category_policy_row_count", "类别政策配置数", "integer"), prop("monthly_driver_source_row_count", "月度驱动配置数", "integer"),
        prop("baseline_period_from", "Baseline起始月份"), prop("baseline_period_to", "Baseline结束月份"),
        prop("calculation_version", "计算版本"),
    ]),
    ObjectTypeDefinition("ScenarioAssumption", "场景假设", "What-if 场景中输入并触发重算的业务假设。", [
        prop("template_id", "规则场景"), prop("target_id", "目标对象"), prop("period", "生效月份"),
    ]),
    ObjectTypeDefinition("ReversePlanningTarget", "反向推演目标", "指定范围和月份的目标折旧金额。", [
        prop("scope_type", "范围类型"), prop("scope_value", "范围对象"), prop("target_period", "目标月份"), prop("target_amount", "目标金额", "money"),
    ]),
    ObjectTypeDefinition("ReverseRecommendation", "反向推演方案", "由规则引擎验证、但尚未保存为场景的建议方案。", [
        prop("target_amount", "试算金额", "money"), prop("gap", "目标偏差", "money"), prop("action_count", "动作数", "integer"),
    ]),
    ObjectTypeDefinition("RecommendedAction", "推荐动作", "反向推演方案中的临时业务动作。", [
        prop("template_id", "规则场景"), prop("target_object", "作用对象"), prop("notice_cn", "业务提示"),
    ]),
    ObjectTypeDefinition("ReverseActionCapability", "反向推演动作能力", "由 Ontology 定义的可作用对象、规则边界和业务风险。", [
        prop("capability_id", "能力编号"), prop("template_id", "动作模板"), prop("method", "适用折旧方法"),
        prop("risk_level", "业务风险等级"), prop("direction", "可影响方向"),
    ]),
]

LINK_TYPES = [
    LinkTypeDefinition("assetBelongsToDepartment", "属于部门", "FixedAsset", "Department", "资产由部门负责预算。"),
    LinkTypeDefinition("assetBelongsToCostCenter", "归集到成本中心", "FixedAsset", "CostCenter", "折旧费用归集到成本中心。"),
    LinkTypeDefinition("assetBelongsToProfitCenter", "归属利润中心", "FixedAsset", "ProfitCenter", "资产归属利润中心。"),
    LinkTypeDefinition("assetHasCategory", "登记为资产类别", "FixedAsset", "AssetCategory", "资产登记的业务类别。"),
    LinkTypeDefinition("assetUsesDepreciationCode", "使用折旧码", "FixedAsset", "DepreciationCode", "资产使用台账折旧码。"),
    LinkTypeDefinition("codeMapsToPolicy", "折旧码映射政策", "DepreciationCode", "DepreciationPolicy", "折旧码指向折旧政策。"),
    LinkTypeDefinition("policyAppliesToCategory", "政策适用于类别", "DepreciationPolicy", "AssetCategory", "政策适用的资产类别。"),
    LinkTypeDefinition("categoryInheritsCategory", "继承上级类别", "AssetCategory", "AssetCategory", "资产类别继承关系。"),
    LinkTypeDefinition("scenarioContainsForecast", "场景包含预测", "Scenario", "ForecastLine", "场景生成预测明细。"),
    LinkTypeDefinition("forecastForAsset", "预测对应资产", "ForecastLine", "FixedAsset", "预测摘要对应具体资产或计划资产。"),
    LinkTypeDefinition("anomalyAffectsObject", "异常影响对象", "Anomaly", "FixedAsset", "异常阻断或影响对象。"),
    LinkTypeDefinition("anomalyRaisedInScenario", "异常发生在场景", "Anomaly", "Scenario", "异常来自指定测算场景。"),
    LinkTypeDefinition("eventAffectsAsset", "事件影响资产", "AssetEvent", "FixedAsset", "事件改变资产折旧结果。"),
    LinkTypeDefinition("codeUsesMethod", "折旧码对应方法", "DepreciationCode", "DepreciationMethod", "折旧码指定资产应执行的折旧方法。"),
    LinkTypeDefinition("methodUsesRule", "方法包含规则", "DepreciationMethod", "CalculationRule", "折旧方法包含可执行的规则分支。"),
    LinkTypeDefinition("assetBelongsToBlock", "资产属于区块", "FixedAsset", "Block", "产量法资产归属到油气区块。"),
    LinkTypeDefinition("blockHasMonthlyDriver", "区块具有月度参数", "Block", "MonthlyDriver", "区块的产量和储量形成指定月份的折耗输入。"),
    LinkTypeDefinition("organizationHasMonthlyDriver", "所属单位具有月度参数", "Department", "MonthlyDriver", "所属单位的总摊销额和资产池期初净额形成工作量法输入。"),
    LinkTypeDefinition("organizationReportsToOrganization", "上级所属单位", "Department", "Department", "所属单位按照组织机构表归属上级单位。"),
    LinkTypeDefinition("driverAffectsMethod", "参数影响折旧方法", "MonthlyDriver", "DepreciationMethod", "月度业务驱动影响指定折旧方法的计算。"),
    LinkTypeDefinition("categoryHasPolicyConfig", "类别具有政策配置", "AssetCategory", "AssetCategoryPolicyConfig", "资产类别关联源表中的折旧配置记录。"),
    LinkTypeDefinition("policyConfigUsesCode", "配置使用折旧码", "AssetCategoryPolicyConfig", "DepreciationCode", "类别政策配置指定折旧码。"),
    LinkTypeDefinition("snapshotContainsObject", "快照包含对象", "SourceSnapshot", "FixedAsset", "源数据快照包含资产记录。"),
    LinkTypeDefinition("scenarioContainsAssumption", "场景包含假设", "Scenario", "ScenarioAssumption", "场景由业务假设组成。"),
    LinkTypeDefinition("assumptionTriggersRule", "假设触发规则", "ScenarioAssumption", "CalculationRule", "场景假设触发对应的规则分支。"),
    LinkTypeDefinition("reverseTargetUsesAction", "目标试算动作", "ReversePlanningTarget", "RecommendedAction", "为实现目标而尝试的规则动作。"),
    LinkTypeDefinition("reverseActionProducesRecommendation", "动作组成方案", "RecommendedAction", "ReverseRecommendation", "一个或多个动作组成可审计建议方案。"),
    LinkTypeDefinition("methodAllowsReverseAction", "方法允许推演动作", "DepreciationMethod", "ReverseActionCapability", "折旧方法决定可使用的反向推演动作。"),
    LinkTypeDefinition("reverseActionTriggersRule", "动作触发规则", "ReverseActionCapability", "CalculationRule", "动作会触发对应计算规则分支。"),
]


REVERSE_ACTION_CAPABILITIES = [
    {"id": "CAP-STRAIGHT-IMPAIRMENT", "label_cn": "减值后重算", "template_id": "straight_impairment", "method": "STRAIGHT_LINE", "risk_level": "高", "direction": "减少折旧", "description_cn": "在可减值净额范围内，按目标月生效后重新计算剩余折旧。"},
    {"id": "CAP-STRAIGHT-ACCELERATE", "label_cn": "加速折旧", "template_id": "straight_accelerated", "method": "STRAIGHT_LINE", "risk_level": "高", "direction": "增加折旧", "description_cn": "仅对目标月仍在计提的年限法资产尝试。"},
    {"id": "CAP-PRODUCTION-DRIVER", "label_cn": "产量/储量调整", "template_id": "production_driver", "method": "PRODUCTION", "risk_level": "经营假设", "direction": "双向", "description_cn": "仅对实际受月度产量和储量驱动的产量法规则分支尝试。"},
    {"id": "CAP-WORKLOAD-DRIVER", "label_cn": "总摊销额调整", "template_id": "workload_driver", "method": "WORKLOAD", "risk_level": "经营假设", "direction": "双向", "description_cn": "以目标月总摊销额为变量，按资产池净额比例分摊。"},
]

ACTION_TYPES = [
    ActionTypeDefinition("createWhatIfScenario", "创建 What-if 场景", ["Scenario"], "复制基准输入并生成独立测算场景。"),
    ActionTypeDefinition("changePlannedAssetAmount", "调整计划资产金额", ["PlannedAsset"], "修改计划资产资本化金额并重算。"),
    ActionTypeDefinition("changeInServiceDate", "调整投产日期", ["PlannedAsset"], "修改预计投产日期并重算开始计提月份。"),
    ActionTypeDefinition("addDisposalEvent", "增加减少事件", ["FixedAsset"], "模拟资产减少后折旧停止或降低。"),
    ActionTypeDefinition("addImpairmentEvent", "增加减值事件", ["FixedAsset"], "模拟减值后折旧基数下降。"),
    ActionTypeDefinition("changePolicyParameters", "调整政策参数", ["DepreciationPolicy"], "修改年限、残值率或开始计提规则。"),
    ActionTypeDefinition("resolveAnomaly", "处理异常", ["Anomaly"], "查看建议并追溯影响对象。"),
    ActionTypeDefinition("reversePlanDepreciation", "反向推演折旧目标", ["Scenario", "Department", "AssetCategory"], "根据目标折旧金额试算可行规则动作。"),
]

FUNCTION_TYPES = [
    FunctionTypeDefinition("calculateDepreciation", "计算折旧", "根据资产、事件和政策生成月度折旧预测。"),
    FunctionTypeDefinition("explainPolicyMatch", "解释政策匹配", "沿资产类别继承链解释适用政策。"),
    FunctionTypeDefinition("compareScenarios", "比较场景", "对比基准与多个 What-if 场景的月度差异。"),
    FunctionTypeDefinition("summarizeForecast", "汇总预测", "按年度、部门、类别等维度汇总折旧。"),
    FunctionTypeDefinition("traceKnowledgeGraph", "追溯知识图谱", "查找对象之间的业务关系路径。"),
    FunctionTypeDefinition("reversePlanDepreciation", "反向推演折旧", "基于目标范围和目标金额生成并验证临时规则方案。"),
]


def object_id(object_type: str, raw_id: str) -> str:
    return f"{object_type}:{raw_id}"


def default_actions_for(object_type: str) -> list[str]:
    return [
        action.type_id
        for action in ACTION_TYPES
        if object_type in action.target_types or object_type == "Scenario" and action.type_id == "createWhatIfScenario"
    ]


def object_type_label(object_type: str) -> str:
    return OBJECT_TYPE_LABEL_CN.get(object_type, object_type)


def asset_object_label(source_type: str, asset_ref: str, name: str) -> str:
    return f"{ASSET_SOURCE_TYPE_LABEL_CN.get(source_type, source_type)} {asset_ref}"


def category_object_label(category_id: str) -> str:
    return category_label(category_id)


def policy_object_label(policy_id: str) -> str:
    return policy_label(policy_id)


def code_object_label(code_id: str) -> str:
    return depreciation_code_label(code_id)
