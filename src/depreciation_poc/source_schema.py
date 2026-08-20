from __future__ import annotations


ASSET_MASTER_FIELDS = (
    "公司代码", "主资产号", "资产子编号", "唯一码", "资产名称", "规格型号", "折旧范围", "货币",
    "利润中心", "利润中心描述", "成本中心", "成本中心描述", "功能范围", "功能范围描述", "所属单位",
    "所属单位名称", "资产类型", "资产类型名称", "资产类别", "资产类别名称", "资产大类", "资产大类描述",
    "所属区块", "不活动日期", "创建日期", "资本化日期", "折旧码", "计划折旧年限", "计划折旧月份",
    "原值", "累计折旧", "年累折旧", "减值金额", "净值", "净额", "查询年", "查询月", "当月折旧",
    "当月补提", "技术状况", "使用状态", "原资产编码", "原资产编码EAM", "投产日期", "保管人",
    "复合资产唯一码", "复合资产号", "复合资产类型", "复合资产类别", "复合区块", "复合油气分类",
    "复合增加原因", "复合减少原因", "复合资金渠道", "到期日期", "是否到期", "提满折旧日期",
    "是否提满折旧", "需补提金额", "残值率", "自编号", "资产剔除状态", "资产停用", "WBS元素",
    "井号", "销售方式", "备注", "销售方式描述",
)

DEPRECIATION_CODE_FIELDS = ("折旧码", "折旧码名称")
ASSET_CATEGORY_POLICY_FIELDS = ("资产类别编码", "折旧范围", "折旧码", "折旧年限", "预计净残值率")
PRODUCTION_DRIVER_FIELDS = (
    "公司", "折旧码", "会计年度", "期间", "所属区块", "月总产量（吨/万方）",
    "月总储量（吨/万方）", "折耗率", "储量调整标识",
)
WORKLOAD_DRIVER_FIELDS = ("公司代码", "所属单位", "折旧码", "会计年度", "期间", "单位数", "总计")
MONTHLY_DRIVER_SOURCE_FIELDS = tuple(dict.fromkeys((*PRODUCTION_DRIVER_FIELDS, *WORKLOAD_DRIVER_FIELDS)))
ORGANIZATION_FIELDS = (
    "所属单位", "所属单位名称", "单位简称", "单位简拼", "父编码", "是否明细", "单位级别",
    "是否为新能源企业", "新能源分类", "装置类型", "是否启用", "启用日期", "停用日期", "单位性质",
    "控股比例", "Rg", "代码", "县代码", "税务管辖权", "详细地址", "公司", "部门", "利润中心",
    "利润中心内记帐标识", "成本中心", "部门__2", "范围", "申请类型", "C/R", "境内境外", "状态标识",
    "是否上市", "总部/各子集团", "专业公司", "地区公司级", "组织机构编码", "报表归属", "创建人",
    "创建日期", "创建时间", "修改人", "修改日期", "修改时间",
)


MONEY_FIELDS = {
    "原值", "累计折旧", "年累折旧", "减值金额", "净值", "净额", "当月折旧", "当月补提", "需补提金额",
    "单位数", "总计",
}
DECIMAL_FIELDS = {
    "残值率", "预计净残值率", "控股比例", "月总产量（吨/万方）", "月总储量（吨/万方）", "折耗率",
}
INTEGER_FIELDS = {"计划折旧年限", "计划折旧月份", "查询年", "查询月", "会计年度", "期间", "折旧年限", "单位级别"}
DATE_FIELDS = {
    "不活动日期", "创建日期", "资本化日期", "投产日期", "到期日期", "提满折旧日期", "启用日期", "停用日期", "修改日期",
}


def source_value_type(field: str) -> str:
    if field in MONEY_FIELDS:
        return "money"
    if field in DECIMAL_FIELDS:
        return "decimal"
    if field in INTEGER_FIELDS:
        return "integer"
    if field in DATE_FIELDS:
        return "date"
    return "string"


def source_properties(row: dict[str, str], fields: tuple[str, ...]) -> dict[str, str]:
    return {field: row[field] for field in fields if row.get(field) not in (None, "")}
