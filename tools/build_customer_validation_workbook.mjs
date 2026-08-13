import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const repoRoot = process.cwd();
const sourcePath = path.join(repoRoot, "tmp", "validation_package.json");
const outputDir = path.join(repoRoot, "outputs", "customer_validation");
const outputPath = path.join(outputDir, "资产折旧预测_基准场景核验包.xlsx");
const data = JSON.parse(await fs.readFile(sourcePath, "utf8"));

const colors = {
  navy: "#14324B",
  teal: "#247A78",
  paleTeal: "#E7F3F2",
  lightBlue: "#EEF4F8",
  paleYellow: "#FFF8E6",
  paleGreen: "#EAF5EC",
  paleRed: "#FCEBE8",
  grid: "#D7E0E8",
  muted: "#5F6F7F",
  white: "#FFFFFF",
};
const moneyFormat = '#,##0.00;[Red](#,##0.00);-';
const percentFormat = '0.00%';

const colName = (index) => {
  let name = "";
  let value = index + 1;
  while (value > 0) {
    const remainder = (value - 1) % 26;
    name = String.fromCharCode(65 + remainder) + name;
    value = Math.floor((value - 1) / 26);
  }
  return name;
};

const toMatrix = (records, columns) => records.map((row) => columns.map((column) => row[column.key] ?? ""));

const styleHeader = (range) => {
  range.format.fill = colors.navy;
  range.format.font = { bold: true, color: colors.white };
  range.format.horizontalAlignment = "center";
  range.format.verticalAlignment = "center";
  range.format.wrapText = true;
  range.format.borders = { preset: "all", style: "thin", color: colors.grid };
};

const styleBody = (range) => {
  range.format.borders = { preset: "all", style: "thin", color: colors.grid };
  range.format.verticalAlignment = "center";
};

const writeTable = (sheet, startRow, columns, records, tableName) => {
  const startCol = 0;
  const endCol = columns.length - 1;
  const headerRange = sheet.getRangeByIndexes(startRow, startCol, 1, columns.length);
  headerRange.values = [columns.map((column) => column.label)];
  styleHeader(headerRange);
  if (records.length > 0) {
    const body = sheet.getRangeByIndexes(startRow + 1, startCol, records.length, columns.length);
    body.values = toMatrix(records, columns);
    styleBody(body);
    for (let index = 0; index < columns.length; index += 1) {
      const column = columns[index];
      if (column.format) {
        sheet.getRangeByIndexes(startRow + 1, index, records.length, 1).format.numberFormat = column.format;
      }
      if (column.width) sheet.getRangeByIndexes(0, index, 1, 1).format.columnWidth = column.width;
    }
  }
  sheet.tables.add(`${colName(startCol)}${startRow + 1}:${colName(endCol)}${startRow + records.length + 1}`, true, tableName);
  return { headerRow: startRow, endRow: startRow + records.length, endCol };
};

const workbook = Workbook.create();
const summary = workbook.worksheets.add("核验摘要");
const checks = workbook.worksheets.add("勾稽检查");
const monthly = workbook.worksheets.add("月度汇总");
const department = workbook.worksheets.add("所属单位宽表");
const category = workbook.worksheets.add("资产类别宽表");
const assetWide = workbook.worksheets.add("逐资产宽表");
const sapReconcile = workbook.worksheets.add("SAP人工对账");
const detail = workbook.worksheets.add("逐资产逐月明细");
const source = workbook.worksheets.add("源台账快照");
const drivers = workbook.worksheets.add("基准驱动参数");
const rules = workbook.worksheets.add("规则分支汇总");

for (const sheet of [summary, checks, monthly, department, category, assetWide, sapReconcile, detail, source, drivers, rules]) {
  sheet.showGridLines = false;
}

// Summary
summary.getRange("A1:H1").merge();
summary.getRange("A1").values = [[data.metadata.title]];
summary.getRange("A1:H1").format.fill = colors.navy;
summary.getRange("A1:H1").format.font = { bold: true, color: colors.white, size: 16 };
summary.getRange("A1:H1").format.horizontalAlignment = "left";
summary.getRange("A1:H1").format.rowHeight = 30;
summary.getRange("A3:B10").values = [
  ["基准场景", data.metadata.scenario_id],
  ["台账快照", data.metadata.snapshot_period],
  ["预测期间", `${data.metadata.forecast_start} 至 ${data.metadata.forecast_end}`],
  ["预测月数", data.metadata.forecast_months],
  ["资产数量", data.metadata.asset_count],
  ["预测明细", data.metadata.forecast_line_count],
  ["规则执行记录", data.metadata.rule_execution_count],
  ["计算版本", data.metadata.calculation_version],
];
styleBody(summary.getRange("A3:B10"));
summary.getRange("A3:A10").format.fill = colors.lightBlue;
summary.getRange("A3:A10").format.font = { bold: true, color: colors.navy };
summary.getRange("D3:H3").merge();
summary.getRange("D3").values = [["核验范围与基准假设"]];
summary.getRange("D3:H3").format.fill = colors.teal;
summary.getRange("D3:H3").format.font = { bold: true, color: colors.white };
summary.getRange("D4:H6").merge();
summary.getRange("D4").values = [[`${data.metadata.verification_scope}\n\n基准假设：${data.metadata.baseline_assumption}`]];
summary.getRange("D4:H6").format.fill = colors.paleYellow;
summary.getRange("D4:H6").format.wrapText = true;
summary.getRange("D4:H6").format.verticalAlignment = "top";
summary.getRange("D4:H6").format.borders = { preset: "all", style: "thin", color: colors.grid };
summary.getRange("A4:H6").format.rowHeight = 30;
summary.getRange("D8:H8").merge();
summary.getRange("D8").values = [["源文件"]];
summary.getRange("D8:H8").format.fill = colors.lightBlue;
summary.getRange("D8:H8").format.font = { bold: true, color: colors.navy };
summary.getRange("D9:H11").merge();
summary.getRange("D9").values = [[data.metadata.source_files.join("\n")]];
summary.getRange("D9:H11").format.wrapText = true;
summary.getRange("D9:H11").format.borders = { preset: "all", style: "thin", color: colors.grid };
summary.getRange("D9:H11").format.rowHeight = 23;

const totalForecast = data.monthly_totals.reduce((sum, row) => sum + row.monthly_depreciation, 0);
summary.getRange("A13:H13").merge();
summary.getRange("A13").values = [["未来六个月折旧汇总（元）"]];
summary.getRange("A13:H13").format.fill = colors.teal;
summary.getRange("A13:H13").format.font = { bold: true, color: colors.white };
summary.getRange("A14:G14").values = [["项目", ...data.monthly_totals.map((row) => row.period)]];
styleHeader(summary.getRange("A14:G14"));
summary.getRange("A15:G15").values = [["月折旧", ...data.monthly_totals.map((row) => row.monthly_depreciation)]];
styleBody(summary.getRange("A15:G15"));
summary.getRange("B15:G15").format.numberFormat = moneyFormat;
summary.getRange("A17:H17").merge();
summary.getRange("A17").values = [["使用方式：先看“勾稽检查”是否全部通过，再按所属单位/类别宽表核对业务口径；抽查时在“逐资产逐月明细”按资产编号和期间筛选，结合规则输入回放月折旧。SAP 预测数请由客户人工填入对应对账工作底稿。"]];
summary.getRange("A17:H17").format.fill = colors.paleGreen;
summary.getRange("A17:H17").format.wrapText = true;
summary.getRange("A17:H17").format.rowHeight = 48;
for (const column of ["A", "B", "C", "D", "E", "F", "G", "H"]) summary.getRange(`${column}:${column}`).format.columnWidth = column === "A" ? 24 : 16;

// Checks
const checkRecords = data.checks.map((row) => ({ ...row, status: row.difference === 0 ? "通过" : "不通过" }));
writeTable(checks, 0, [
  { key: "check", label: "检查项", width: 32 },
  { key: "actual", label: "实际值", width: 14, format: '#,##0.00;[Red](#,##0.00);-' },
  { key: "expected", label: "期望值", width: 14, format: '#,##0.00;[Red](#,##0.00);-' },
  { key: "difference", label: "差异", width: 14, format: '#,##0.00;[Red](#,##0.00);-' },
  { key: "status", label: "状态", width: 12 },
  { key: "note", label: "核验说明", width: 52 },
], checkRecords, "ChecksTable");
checks.getRange(`E2:E${checkRecords.length + 1}`).conditionalFormats.add("containsText", { text: "通过", format: { fill: colors.paleGreen, font: { bold: true, color: "#23633A" } } });
checks.getRange(`E2:E${checkRecords.length + 1}`).conditionalFormats.add("containsText", { text: "不通过", format: { fill: colors.paleRed, font: { bold: true, color: "#9F2D20" } } });
checks.freezePanes.freezeRows(1);

const sumBy = (rows, key) => {
  const grouped = new Map();
  for (const row of rows) {
    const groupKey = row[key];
    if (!grouped.has(groupKey)) grouped.set(groupKey, { key: groupKey, label: row[key], values: {} });
    const group = grouped.get(groupKey);
    group.values[row.period] = (group.values[row.period] || 0) + Number(row.monthly_depreciation);
  }
  return [...grouped.values()].map((group) => ({
    label: group.label,
    total: Object.values(group.values).reduce((sum, value) => sum + value, 0),
    ...Object.fromEntries(data.periods.map((period) => [period, group.values[period] || 0])),
  }));
};
const wideColumns = (label) => [
  { key: "label", label, width: 54 },
  { key: "total", label: "六个月合计", width: 16, format: moneyFormat },
  ...data.periods.map((period) => ({ key: period, label: period, width: 14, format: moneyFormat })),
];

// Monthly summary
const monthlyRecords = data.monthly_totals.map((row) => ({
  ...row,
  source_asset_count: new Set(data.detail_rows.filter((detail) => detail.period === row.period).map((detail) => detail.asset_id)).size,
  forecast_line_count: data.detail_rows.filter((detail) => detail.period === row.period).length,
}));
writeTable(monthly, 0, [
  { key: "period", label: "预测月份", width: 15 },
  { key: "source_asset_count", label: "资产数", width: 12, format: '#,##0' },
  { key: "forecast_line_count", label: "预测明细数", width: 15, format: '#,##0' },
  { key: "monthly_depreciation", label: "月折旧（元）", width: 18, format: moneyFormat },
], monthlyRecords, "MonthlyTotalsTable");
monthly.freezePanes.freezeRows(1);

// Wide tables
const departmentRecords = sumBy(data.detail_rows, "department");
writeTable(department, 0, wideColumns("所属单位"), departmentRecords, "DepartmentWideTable");
department.freezePanes.freezeRows(1);
department.freezePanes.freezeColumns(1);
const categoryRecords = sumBy(data.detail_rows, "asset_category_name");
writeTable(category, 0, wideColumns("资产类别"), categoryRecords, "CategoryWideTable");
category.freezePanes.freezeRows(1);
category.freezePanes.freezeColumns(1);

const forecastByAsset = new Map();
for (const row of data.detail_rows) {
  if (!forecastByAsset.has(row.asset_id)) forecastByAsset.set(row.asset_id, {});
  forecastByAsset.get(row.asset_id)[row.period] = row.monthly_depreciation;
}
const assetWideRecords = data.source_assets.map((asset) => {
  const monthlyValues = forecastByAsset.get(asset.asset_id) || {};
  return {
    asset_id: asset.asset_id,
    asset_name: asset.asset_name,
    department: asset.department,
    cost_center: asset.cost_center,
    asset_category_name: asset.asset_category_name,
    depreciation_code: asset.depreciation_code,
    depreciation_code_name: asset.depreciation_code_name,
    original_cost: asset.original_cost,
    total: data.periods.reduce((sum, period) => sum + Number(monthlyValues[period] || 0), 0),
    ...Object.fromEntries(data.periods.map((period) => [period, monthlyValues[period] || 0])),
  };
});
writeTable(assetWide, 0, [
  { key: "asset_id", label: "资产编号", width: 18 }, { key: "asset_name", label: "资产名称", width: 18 },
  { key: "department", label: "所属单位", width: 46 }, { key: "cost_center", label: "成本中心", width: 15 },
  { key: "asset_category_name", label: "资产类别", width: 22 }, { key: "depreciation_code", label: "折旧码", width: 12 },
  { key: "depreciation_code_name", label: "折旧码名称", width: 24 }, { key: "original_cost", label: "资产原值", width: 16, format: moneyFormat },
  { key: "total", label: "六个月合计", width: 16, format: moneyFormat },
  ...data.periods.map((period) => ({ key: period, label: period, width: 14, format: moneyFormat })),
], assetWideRecords, "AssetWideTable");
assetWide.freezePanes.freezeRows(1);
assetWide.freezePanes.freezeColumns(2);

// This is intentionally a manual-input sheet because the customer has not provided SAP output.
const sapHeaders = ["资产编号", "资产名称", "所属单位", ...data.periods.flatMap((period) => [`本模型 ${period}`, `SAP ${period}`, `差异 ${period}`])];
sapReconcile.getRangeByIndexes(0, 0, 1, sapHeaders.length).values = [sapHeaders];
styleHeader(sapReconcile.getRangeByIndexes(0, 0, 1, sapHeaders.length));
const sapRows = assetWideRecords.map((asset) => [asset.asset_id, asset.asset_name, asset.department, ...data.periods.flatMap((period) => [asset[period], null, null])]);
sapReconcile.getRangeByIndexes(1, 0, sapRows.length, sapHeaders.length).values = sapRows;
styleBody(sapReconcile.getRangeByIndexes(1, 0, sapRows.length, sapHeaders.length));
for (let periodIndex = 0; periodIndex < data.periods.length; periodIndex += 1) {
  const modelCol = 3 + periodIndex * 3;
  const sapCol = modelCol + 1;
  const diffCol = modelCol + 2;
  sapReconcile.getRangeByIndexes(1, modelCol, sapRows.length, 1).format.numberFormat = moneyFormat;
  const sapRange = sapReconcile.getRangeByIndexes(1, sapCol, sapRows.length, 1);
  sapRange.format.numberFormat = moneyFormat;
  sapRange.format.fill = colors.paleYellow;
  const diffRange = sapReconcile.getRangeByIndexes(1, diffCol, sapRows.length, 1);
  diffRange.format.numberFormat = moneyFormat;
  const modelLetter = colName(modelCol);
  const sapLetter = colName(sapCol);
  const diffLetter = colName(diffCol);
  diffRange.formulas = Array.from({ length: sapRows.length }, (_, index) => [
    `=IF(${sapLetter}${index + 2}=\"\",\"\",${sapLetter}${index + 2}-${modelLetter}${index + 2})`,
  ]);
  diffRange.conditionalFormats.add("cellIs", { operator: "notEqual", formula: 0, format: { fill: colors.paleRed, font: { color: "#9F2D20" } } });
  sapReconcile.getRangeByIndexes(0, modelCol, 1, 3).format.borders = { preset: "all", style: "thin", color: colors.grid };
  sapReconcile.getRangeByIndexes(0, modelCol, 1, 1).format.fill = colors.teal;
  sapReconcile.getRangeByIndexes(0, sapCol, 1, 1).format.fill = "#9A6B1E";
}
sapReconcile.getRange("A430:U431").merge();
sapReconcile.getRange("A430").values = [["填写说明：将 SAP 逐资产、逐月折旧结果粘贴到黄色列；差异列自动计算为“SAP - 本模型”。空白 SAP 单元格不参与对账。"]];
sapReconcile.getRange("A430:U431").format.fill = colors.paleYellow;
sapReconcile.getRange("A430:U431").format.wrapText = true;
sapReconcile.getRange("A430:U431").format.borders = { preset: "all", style: "thin", color: colors.grid };
sapReconcile.getRange("A:A").format.columnWidth = 18;
sapReconcile.getRange("B:B").format.columnWidth = 18;
sapReconcile.getRange("C:C").format.columnWidth = 42;
for (let column = 3; column < sapHeaders.length; column += 1) sapReconcile.getRangeByIndexes(0, column, 1, 1).format.columnWidth = 15;
sapReconcile.freezePanes.freezeRows(1);
sapReconcile.freezePanes.freezeColumns(3);

// Detail table
const detailColumns = [
  ["scenario_id", "场景", 12], ["period", "预测月份", 12], ["asset_id", "资产编号", 18], ["asset_name", "资产名称", 18],
  ["company", "公司", 12], ["department", "所属单位", 46], ["cost_center", "成本中心", 15], ["profit_center", "利润中心", 15],
  ["asset_category", "资产类别编码", 14], ["asset_category_name", "资产类别", 22], ["depreciation_code", "折旧码", 12], ["depreciation_code_name", "折旧码名称", 24],
  ["depreciation_method", "折旧方法", 16], ["rule_id", "规则", 16], ["branch_id", "规则分支", 24], ["source_row", "源文件行", 12],
  ["opening_original_cost", "期初原值", 16, moneyFormat], ["opening_accumulated_depreciation", "期初累计折旧", 16, moneyFormat], ["opening_accumulated_impairment", "期初累计减值", 16, moneyFormat], ["opening_net_value", "期初净值", 16, moneyFormat],
  ["addition_amount", "新增", 14, moneyFormat], ["disposal_amount", "减少", 14, moneyFormat], ["impairment_amount", "减值", 14, moneyFormat], ["depreciable_base", "可折旧基数", 16, moneyFormat], ["monthly_depreciation", "月折旧", 16, moneyFormat],
  ["accumulated_depreciation", "累计折旧", 16, moneyFormat], ["closing_net_value", "期末净值", 16, moneyFormat], ["validation_status", "状态", 12], ["formula_cn", "公式", 36], ["conclusion_cn", "规则结论", 30],
  ["remaining_depreciable_amount", "规则输入：剩余可折旧金额", 20, moneyFormat], ["remaining_months", "规则输入：剩余月数", 16, '#,##0.00'], ["rule_opening_net_value", "规则输入：期初净值", 18, moneyFormat], ["production_rate", "规则输入：折耗率", 16, percentFormat],
  ["workload_total_amortization", "规则输入：总摊销额", 18, moneyFormat], ["workload_asset_opening_net", "规则输入：资产期初净值", 18, moneyFormat], ["workload_pool_opening_net", "规则输入：资产池期初净额", 20, moneyFormat], ["source_event_id", "来源事件", 18],
];
writeTable(detail, 0, detailColumns.map(([key, label, width, format]) => ({ key, label, width, format })), data.detail_rows, "ForecastDetailTable");
detail.freezePanes.freezeRows(1);
detail.freezePanes.freezeColumns(4);

// Source ledger snapshot
const sourceColumns = [
  ["asset_id", "资产编号", 18], ["asset_name", "资产名称", 18], ["company", "公司", 12], ["department", "所属单位", 46], ["cost_center", "成本中心", 15], ["profit_center", "利润中心", 15],
  ["asset_category", "资产类别编码", 14], ["asset_category_name", "资产类别", 22], ["asset_major_category", "资产大类", 12], ["asset_major_category_name", "资产大类名称", 18],
  ["depreciation_code", "折旧码", 12], ["depreciation_code_name", "折旧码名称", 24], ["original_cost", "资产原值", 16, moneyFormat], ["in_service_date", "资本化日期", 14],
  ["accumulated_depreciation", "累计折旧", 16, moneyFormat], ["accumulated_impairment", "累计减值", 16, moneyFormat], ["useful_life_months", "计划折旧月数", 16, '#,##0'], ["residual_rate", "残值率", 12, percentFormat],
  ["status", "资产状态", 12], ["block_id", "所属区块", 16], ["asset_type", "资产类型", 14], ["source_row", "源文件行", 12],
];
writeTable(source, 0, sourceColumns.map(([key, label, width, format]) => ({ key, label, width, format })), data.source_assets, "SourceLedgerTable");
source.freezePanes.freezeRows(1);
source.freezePanes.freezeColumns(3);

// Driver parameters
writeTable(drivers, 0, [
  { key: "driver_type", label: "驱动类型", width: 16 }, { key: "period", label: "预测月份", width: 13 }, { key: "company", label: "公司", width: 12 },
  { key: "target_id", label: "区块/公司", width: 16 }, { key: "production", label: "产量", width: 14, format: '#,##0.0000' }, { key: "reserves", label: "剩余储量", width: 14, format: '#,##0.0000' },
  { key: "workload", label: "工作量", width: 14, format: '#,##0.0000' }, { key: "unit_fee", label: "单位费用", width: 14, format: moneyFormat }, { key: "assumption_note", label: "基准假设", width: 48 }, { key: "source_refs", label: "来源定位", width: 52 },
], data.drivers, "DriversTable");
drivers.freezePanes.freezeRows(1);

// Rule summary
const ruleRecords = [
  ...data.method_counts.map((row) => ({ type: "折旧方法", name: row.method, line_count: row.line_count })),
  ...data.branch_counts.map((row) => ({ type: "规则分支", name: row.branch_id, line_count: row.line_count })),
];
writeTable(rules, 0, [
  { key: "type", label: "分类", width: 16 }, { key: "name", label: "方法/规则分支", width: 32 }, { key: "line_count", label: "明细行数", width: 16, format: '#,##0' },
], ruleRecords, "RuleSummaryTable");
rules.freezePanes.freezeRows(1);

await fs.mkdir(outputDir, { recursive: true });
const file = await SpreadsheetFile.exportXlsx(workbook);
await file.save(outputPath);

for (const renderTarget of [
  ["核验摘要", "A1:H17", "summary"],
  ["勾稽检查", "A1:F7", "checks"],
  ["逐资产逐月明细", "A1:AJ12", "detail"],
  ["SAP人工对账", "A1:U10", "sap_reconcile"],
]) {
  const [sheetName, range, name] = renderTarget;
  const blob = await workbook.render({ sheetName, range, scale: 1.3, format: "png" });
  await fs.writeFile(path.join(outputDir, `${name}.png`), new Uint8Array(await blob.arrayBuffer()));
}

await workbook.inspect({ kind: "table", range: "勾稽检查!A1:F7", include: "values,formulas", tableMaxRows: 8, tableMaxCols: 6 });
console.log(outputPath);
