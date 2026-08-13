const state = {
  view: "overview",
  scenarioId: "BASELINE",
  dashboard: null,
  scenarios: [],
  assetCards: [],
  assetWorkbenchCards: [],
  graph: null,
  graphSelectedType: "",
  graphSelectedNodeId: null,
  ruleCatalog: null,
  scenarioAssetDetail: null,
  scenarioDefaultPeriod: "",
  scenarioEditingId: null,
  wideCatalog: null,
  wideExpanded: new Set(),
  compareExpanded: new Set(),
  wideQuestionConversationId: null,
  wideQuestionPending: false,
  reversePlanningConversationId: null,
  scenarioDraftAssumptions: [],
  reverseCatalog: null,
  snapshotStatus: null,
};

const labels = {
  BASE: "存量/常规折旧",
  ADDITION: "新增资产影响",
  DISPOSAL: "减少资产影响",
  IMPAIRMENT: "减值影响",
  CURRENT: "存量资产",
  PLANNED: "计划资产",
  ASSET_AMOUNT_CHANGE: "资产金额变化",
  IN_SERVICE_DATE_CHANGE: "投产日期变化",
  DISPOSAL_EVENT: "减少事件",
  IMPAIRMENT_EVENT: "减值事件",
  POLICY_PARAMETER_CHANGE: "政策参数变化",
  WHAT_IF_CHANGE: "测算假设变化",
  UNCLASSIFIED: "未分类差异",
  asset: "资产",
  department: "部门",
  category: "类别",
  asset_ref: "资产",
  asset_source_type: "来源",
  cost_center: "成本中心",
  asset_category: "资产类别",
  depreciation_code: "折旧码",
  depreciation_policy: "政策",
  annual_total: "期间合计",
  scenario_id: "当前场景",
  first_depreciation_period: "首次计提月份",
  monthly_depreciation_at_start: "起始月折旧",
  forecast_depreciation_total: "预测期折旧合计",
  ending_net_value: "预测期末净值",
  calculation_rule_label_cn: "计算规则",
  amount: "金额差异",
  percent: "百分比差异",
  both: "金额 + 百分比",
};

const categoryLabels = new Proxy({}, {
  get: (_target, key) => state.wideCatalog?.category_labels?.[key],
});

const policyDisplay = (value) => value || "-";

const categoryDisplay = (value) => categoryLabels[value] || value || "-";

const ruleBranchDisplay = (value) => ({
  CONFIGURED_DEPLETION_RATE: "按区块配置折耗率计提",
  LIFE_EXPIRED: "折旧到期，停止计提",
  STRAIGHT_LINE: "年限平均法计提",
  IMPAIRMENT_RECALC: "减值后重算",
  PRODUCTION: "产量法计提",
  WORKLOAD: "工作量法计提",
}[value] || value || "-");

const money = (value) => Number(value || 0).toLocaleString("zh-CN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const compactMoney = (value) => Number(value || 0).toLocaleString("zh-CN", {
  notation: "compact",
  maximumFractionDigits: 1,
});

const percent = (value) => {
  const number = Number(value || 0);
  return `${number.toLocaleString("zh-CN", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })}%`;
};

const api = async (url, options = {}) => {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || response.statusText);
  return data;
};

const softApi = async (url, fallback, options = {}) => {
  try {
    return await api(url, options);
  } catch (error) {
    console.warn(`Optional API failed: ${url}`, error);
    return fallback;
  }
};

const el = (id) => document.getElementById(id);

const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
}[char]));

const truncate = (value, length = 22) => {
  const text = String(value ?? "");
  return text.length > length ? `${text.slice(0, length - 1)}...` : text;
};

const asArray = (value) => {
  if (Array.isArray(value)) return value;
  if (!value) return [];
  if (Array.isArray(value.items)) return value.items;
  if (Array.isArray(value.rows)) return value.rows;
  if (Array.isArray(value.assets)) return value.assets;
  return [];
};

const firstValue = (object, keys, fallback = "") => {
  for (const key of keys) {
    if (object && object[key] !== undefined && object[key] !== null && object[key] !== "") {
      return object[key];
    }
  }
  return fallback;
};

const displayValue = (value) => {
  if (value === null || value === undefined || value === "") return "";
  if (Array.isArray(value)) return value.map(displayValue).filter(Boolean).join("，");
  if (typeof value === "object") {
    return firstValue(value, [
      "value_label_cn",
      "label_cn",
      "content_cn",
      "description_cn",
      "message_cn",
      "value",
      "label",
      "name",
      "id",
    ], "");
  }
  return String(value);
};

const setOptions = (select, values, allLabel) => {
  const current = select.value;
  select.innerHTML = "";
  if (allLabel) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = allLabel;
    select.appendChild(option);
  }
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = categoryLabels[value] ? `${categoryLabels[value]} · ${value}` : value;
    select.appendChild(option);
  }
  if (Array.from(select.options).some((option) => option.value === current)) {
    select.value = current;
  }
};

const renderRows = (tbody, rows, columns) => {
  tbody.innerHTML = "";
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = columns.length;
    td.textContent = "没有符合条件的数据";
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const column of columns) {
      const td = document.createElement("td");
      td.textContent = column.format ? column.format(row[column.key], row) : (row[column.key] ?? "");
      if (column.className) td.className = column.className;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
};

const sourceStatus = (status = {}) => {
  el("sourceScenario").textContent = status.scenario_id || state.scenarioId || "-";
  el("sourceBudget").textContent = status.budget_version || "-";
  el("sourceUpdated").textContent = status.business_db_updated_at || "-";
  el("sourceTriples").textContent = status.snapshot?.calculation_version
    || (status.triple_count === undefined ? "-" : `${status.triple_count} / 推理 ${status.inferred_triple_count || 0}`);
  const snapshot = status.snapshot || state.snapshotStatus || {};
  el("sourceFiles").textContent = (snapshot.source_files || []).join("、") || "-";
  el("sourceAssetCounts").textContent = snapshot.asset_count === undefined
    ? "-"
    : `${snapshot.asset_count} / ${snapshot.excluded_asset_count ?? 0}`;
  if (snapshot.forecast_start) {
    const actual = snapshot.actual_snapshot_period || snapshot.snapshot_period;
    const periods = snapshot.forecast_periods || [];
    el("forecastScope").textContent = actual
      ? `客户资产台账 · ${actual} 实际 + ${periods.join("、")} 规则预测`
      : `客户资产台账 · ${periods.join("、")} 规则预测`;
  }
};

const loadSnapshotStatus = async () => {
  state.snapshotStatus = await api("/api/snapshot/status");
  const snapshot = state.snapshotStatus;
  const periods = snapshot.forecast_periods || [];
  const firstForecast = periods[0] || snapshot.forecast_start || "";
  const lastForecast = periods.at(-1) || firstForecast;
  el("comparePeriodFrom").value = snapshot.actual_snapshot_period || snapshot.snapshot_period || firstForecast;
  el("comparePeriodTo").value = lastForecast;
  state.scenarioDefaultPeriod = firstForecast;
  sourceStatus({ snapshot, scenario_id: state.scenarioId });
};

const loadScenarios = async () => {
  state.scenarios = await api("/api/scenarios");
  const scenarioSelect = el("scenarioSelect");
  scenarioSelect.innerHTML = "";
  for (const scenario of state.scenarios) {
    const option = document.createElement("option");
    option.value = scenario.scenario_id;
    option.textContent = scenario.scenario_name || (scenario.base_scenario_id
      ? `${scenario.scenario_id} · What-if`
      : `${scenario.scenario_id} · 基准`);
    scenarioSelect.appendChild(option);
  }
  const scenarioBase = el("scenarioBase");
  if (scenarioBase) {
    const selected = scenarioBase.value || "BASELINE";
    scenarioBase.innerHTML = "";
    for (const scenario of state.scenarios) {
      const option = document.createElement("option");
      option.value = scenario.scenario_id;
      option.textContent = scenario.scenario_name || scenario.scenario_id;
      scenarioBase.appendChild(option);
    }
    scenarioBase.value = Array.from(scenarioBase.options).some((option) => option.value === selected)
      ? selected
      : "BASELINE";
  }
  if (state.scenarios.some((item) => item.scenario_id === state.scenarioId)) {
    scenarioSelect.value = state.scenarioId;
  } else if (state.scenarios[0]) {
    state.scenarioId = state.scenarios[0].scenario_id;
    scenarioSelect.value = state.scenarioId;
  }
  hydrateCompareScenarios();
};

const hydrateCompareScenarios = () => {
  const select = el("compareScenarios");
  const selected = new Set(Array.from(select.selectedOptions).map((option) => option.value));
  select.innerHTML = "";
  for (const scenario of state.scenarios) {
    const option = document.createElement("option");
    option.value = scenario.scenario_id;
    option.textContent = scenario.base_scenario_id
      ? `${scenario.scenario_id} · What-if`
      : `${scenario.scenario_id} · 基准`;
    if (selected.has(option.value)) option.selected = true;
    select.appendChild(option);
  }
  const hasSelection = Array.from(select.options).some((option) => option.selected);
  if (!hasSelection) {
    Array.from(select.options).slice(0, 2).forEach((option) => {
      option.selected = true;
    });
  }
};

const loadAssetCards = async () => {
  const data = await softApi(`/api/assets/cards?scenario_id=${encodeURIComponent(state.scenarioId)}`, []);
  state.assetCards = asArray(data).map(normalizeAssetCard).filter((card) => card.assetRef);
  renderAssetCards("assetCards", state.assetCards.slice(0, 8));
  hydrateAssetFilters();
  hydrateScenarioAssetOptions();
};

const normalizeAssetCard = (card) => ({
  assetRef: firstValue(card, ["asset_ref", "asset_id", "planned_asset_id", "object_id", "id"]),
  name: firstValue(card, ["asset_name", "name", "title", "asset_title"], firstValue(card, ["asset_ref", "id"])),
  company: firstValue(card, ["company", "company_code"], ""),
  department: firstValue(card, ["department", "department_name", "cost_center"], "-"),
  category: firstValue(card, ["asset_category", "category", "category_code"], "-"),
  source: firstValue(card, ["asset_source_type", "source_type", "source"], "-"),
  status: firstValue(card, ["status_cn", "status", "asset_status"], "-"),
  isBlocking: Boolean(firstValue(card, ["is_blocking", "blocking"], false)),
  riskCount: Number(firstValue(card, ["risk_count", "anomaly_count"], 0)),
  policy: firstValue(card, ["depreciation_policy_label_cn", "policy_label_cn", "policy_name", "applicable_policy", "depreciation_policy", "policy_id"], "-"),
  depreciationCode: firstValue(card, ["depreciation_code"], ""),
  depreciationCodeLabel: firstValue(card, ["depreciation_code_label_cn", "depreciation_code"], "-"),
  depreciationMethod: firstValue(card, ["depreciation_method"], ""),
  depreciationMethodLabel: firstValue(card, ["depreciation_method_label_cn", "depreciation_method"], "-"),
  baseAmount: firstValue(card, ["base_amount", "original_or_planned_amount"], 0),
  depreciation: firstValue(card, [
    "forecast_depreciation_total",
    "total_depreciation",
    "five_year_depreciation",
    "depreciation",
    "annual_depreciation",
    "monthly_depreciation",
  ], 0),
});

const hydrateScenarioAssetOptions = () => {
  const select = el("scenarioAsset");
  if (!select) return;
  const current = select.value;
  const cards = state.assetCards.filter((card) => card.source === "CURRENT");
  select.innerHTML = "";
  if (!cards.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "暂无可用资产";
    select.appendChild(option);
    select.disabled = true;
    return;
  }
  select.disabled = false;
  for (const card of cards) {
    const option = document.createElement("option");
    option.value = card.assetRef;
    option.textContent = `${card.assetRef} · ${card.name || categoryLabels[card.category] || card.category}`;
    select.appendChild(option);
  }
  if (Array.from(select.options).some((option) => option.value === current)) {
    select.value = current;
  }
};

const loadDashboard = async () => {
  state.dashboard = await api(`/api/dashboard?scenario_id=${encodeURIComponent(state.scenarioId)}`);
  const data = state.dashboard;
  sourceStatus(data.source_status);
  el("kpiTotal").textContent = money(data.kpis.total_depreciation);
  el("kpiPlanned").textContent = money(data.kpis.planned_depreciation);
  el("kpiCurrent").textContent = money(data.kpis.current_depreciation);
  el("kpiAnomalies").textContent = data.source_status?.snapshot?.asset_count || data.kpis.forecast_line_count;
  renderAnnualTrend(data.monthly_trend || data.annual_trend || []);
  renderDepartmentRank(data.department_rank || []);
  renderDepreciationComposition(el("driverBreakdown"), data.driver_breakdown || []);
  renderTopAssets(data.top_assets || []);
  hydrateFilterOptions();
  await loadAssetCards();
};

const renderAnnualTrend = (rows) => {
  const container = el("annualChart");
  if (!rows.length) {
    container.innerHTML = `<p class="empty-note">暂无年度趋势数据</p>`;
    return;
  }
  const max = Math.max(...rows.map((item) => Number(item.depreciation)), 1);
  const min = Math.min(...rows.map((item) => Number(item.depreciation)), max);
  const width = 720;
  const height = 260;
  const left = 72;
  const right = 28;
  const top = 28;
  const bottom = 48;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const range = Math.max(max - min, 1);
  const points = rows.map((row, index) => {
    const x = left + (rows.length === 1 ? plotWidth / 2 : (index / (rows.length - 1)) * plotWidth);
    const y = top + (1 - ((Number(row.depreciation) - min) / range)) * plotHeight;
    return { ...row, x, y };
  });
  const polyline = points.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
  const grid = [0, 0.5, 1].map((ratio) => {
    const y = top + ratio * plotHeight;
    const value = max - ratio * range;
    return `
      <line x1="${left}" y1="${y}" x2="${width - right}" y2="${y}" class="line-grid"></line>
      <text x="${left - 10}" y="${y + 4}" class="axis-label" text-anchor="end">${escapeHtml(compactMoney(value))}</text>
    `;
  }).join("");
  const markers = points.map((point) => `
    <g class="line-point">
      <circle cx="${point.x}" cy="${point.y}" r="5"></circle>
      <text x="${point.x}" y="${height - 18}" text-anchor="middle">${escapeHtml(point.period || point.year)}</text>
      <title>${escapeHtml(point.period || point.year)}：${money(point.depreciation)}</title>
    </g>
  `).join("");
  container.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="年度折旧趋势折线图">
      ${grid}
      <line x1="${left}" y1="${top}" x2="${left}" y2="${height - bottom}" class="axis-line"></line>
      <line x1="${left}" y1="${height - bottom}" x2="${width - right}" y2="${height - bottom}" class="axis-line"></line>
      <polyline class="trend-line" points="${polyline}"></polyline>
      ${markers}
    </svg>
    <div class="line-chart-values">
      ${points.map((point) => `<span><b>${escapeHtml(point.period || point.year)}</b>${money(point.depreciation)}</span>`).join("")}
    </div>
  `;
};

const renderDepartmentRank = (rows) => {
  const container = el("departmentRank");
  container.innerHTML = "";
  const max = Math.max(...rows.map((item) => Number(item.depreciation)), 1);
  rows.forEach((row, index) => {
    const button = document.createElement("button");
    button.className = "rank-item";
    button.type = "button";
    button.innerHTML = `
      <span>${index + 1}</span>
      <strong>${escapeHtml(row.department)}</strong>
      <em>${money(row.depreciation)}</em>
      <i style="width:${(Number(row.depreciation) / max) * 100}%"></i>
    `;
    button.addEventListener("click", async () => {
      el("wideDimension1").value = "department";
      state.wideExpanded = new Set();
      await showView("wide");
    });
    container.appendChild(button);
  });
};

const renderDepreciationComposition = (container, rows) => {
  container.innerHTML = "";
  const total = Number(state.dashboard?.kpis?.total_depreciation || 0);
  if (!rows.length) {
    container.innerHTML = `<p class="empty-note">暂无折旧构成数据</p>`;
    return;
  }
  for (const row of rows) {
    const amount = Number(firstValue(row, ["depreciation", "amount", "value"], 0));
    const rawShare = firstValue(row, ["percentage", "share", "ratio"], total ? amount / total : 0);
    const share = Number(rawShare) <= 1 ? Number(rawShare) * 100 : Number(rawShare);
    const driver = labels[row.driver] || row.driver_cn || row.driver || "折旧构成";
    const source = row.asset_source_type ? ` · ${labels[row.asset_source_type] || row.asset_source_type}` : "";
    const description = firstValue(row, [
      "description_cn",
      "explanation_cn",
      "narrative_cn",
      "description",
    ], `${driver}${source}贡献 ${percent(share)}，金额 ${money(amount)}。`);
    const assets = asArray(row.main_assets || row.top_assets || row.contributing_assets).slice(0, 4);
    const assetLinks = assets.map((asset) => {
      const ref = typeof asset === "string" ? asset : firstValue(asset, ["asset_ref", "asset_id", "id"]);
      const title = typeof asset === "string" ? asset : firstValue(asset, ["asset_name", "name", "title"], ref);
      return ref
        ? `<button class="asset-pill" type="button" data-policy-ref="${escapeHtml(ref)}">${escapeHtml(title)}</button>`
        : "";
    }).join("");
    const card = document.createElement("article");
    card.className = "driver-card composition-card";
    card.innerHTML = `
      <div class="composition-top">
        <span>${escapeHtml(driver + source)}</span>
        <strong>${percent(share)}</strong>
      </div>
      <p>${escapeHtml(description)}</p>
      <div class="composition-amount">${money(amount)}</div>
      <div class="asset-pill-row">${assetLinks || "<span>暂无主要贡献资产</span>"}</div>
    `;
    card.querySelectorAll("[data-policy-ref]").forEach((button) => {
      button.addEventListener("click", () => drillToPolicy(button.dataset.policyRef));
    });
    container.appendChild(card);
  }
};

const renderTopAssets = (rows) => {
  const tbody = el("topAssetsBody");
  tbody.innerHTML = "";
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="4">没有符合条件的数据</td></tr>`;
    return;
  }
  for (const row of rows) {
    const assetRef = row.asset_ref || row.asset_id || row.planned_asset_id || "";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><button class="link-button" type="button" data-policy-ref="${escapeHtml(assetRef)}">${escapeHtml(assetRef)}</button></td>
      <td>${escapeHtml(row.department)}</td>
      <td>${escapeHtml(categoryLabels[row.asset_category] || row.asset_category)}</td>
      <td class="amount">${money(row.depreciation)}</td>
    `;
    tr.querySelector("button")?.addEventListener("click", () => drillToPolicy(assetRef));
    tbody.appendChild(tr);
  }
};

const renderAssetCards = (containerId, cards) => {
  const container = el(containerId);
  if (!container) return;
  container.innerHTML = "";
  if (!cards.length) {
    container.innerHTML = `<p class="empty-note">暂无资产卡片数据</p>`;
    return;
  }
  for (const card of cards) {
    const article = document.createElement("article");
    article.className = "asset-card";
    article.innerHTML = `
      <div>
        <span>${escapeHtml(labels[card.source] || card.source)}</span>
        <strong>${escapeHtml(card.assetRef)}</strong>
        <p>${escapeHtml(card.name)}</p>
      </div>
      <dl>
        <dt>部门</dt><dd>${escapeHtml(card.department)}</dd>
        <dt>类别</dt><dd>${escapeHtml(categoryLabels[card.category] || card.category)}</dd>
        <dt>政策</dt><dd>${escapeHtml(card.policy)}</dd>
        <dt>折旧</dt><dd>${card.isBlocking && Number(card.depreciation) === 0 ? "阻断未生成预测" : money(card.depreciation)}</dd>
      </dl>
      <button class="inline-action" type="button">打开资产档案</button>
    `;
    article.querySelector("button").addEventListener("click", () => openAssetDetail(card.assetRef));
    container.appendChild(article);
  }
};

const hydrateFilterOptions = () => {
  hydrateAssetFilters();
};

const hydrateAssetFilters = () => {
  const departments = [...new Set(state.assetCards.map((item) => item.department).filter(Boolean))].sort();
  const categories = [...new Set(state.assetCards.map((item) => item.category).filter(Boolean))].sort();
  setOptions(el("assetDepartmentFilter"), departments, "全部所属单位");
  setOptions(el("assetCategoryFilter"), categories, "全部资产类别");
};

const loadAssetWorkbench = async () => {
  const params = new URLSearchParams({ scenario_id: state.scenarioId });
  const search = el("assetSearchInput")?.value.trim();
  const department = el("assetDepartmentFilter")?.value;
  const category = el("assetCategoryFilter")?.value;
  const source = el("assetSourceFilter")?.value;
  if (search) params.set("search", search);
  if (department) params.set("department", department);
  if (category) params.set("asset_category", category);
  if (source) params.set("asset_source_type", source);
  const data = await api(`/api/assets/cards?${params.toString()}`);
  state.assetWorkbenchCards = asArray(data).map(normalizeAssetCard).filter((card) => card.assetRef);
  el("assetWorkbenchStatus").textContent = `${state.scenarioId} · 找到 ${state.assetWorkbenchCards.length} 项资产；选择一项即可查看完整档案和月度折旧记录。`;
  renderAssetCards("assetWorkbenchCards", state.assetWorkbenchCards);
};

const openAssetDetail = async (assetRef) => {
  if (!assetRef) return;
  const drawer = el("assetDrawer");
  const backdrop = el("assetDrawerBackdrop");
  drawer.setAttribute("aria-hidden", "false");
  drawer.classList.add("open");
  backdrop.hidden = false;
  el("assetDrawerTitle").textContent = `${assetRef} · 正在读取资产档案`;
  el("assetDrawerBody").innerHTML = `<p class="empty-note">正在从台账、预测结果和 Ontology 关系中读取资产详情...</p>`;
  try {
    const data = await api(`/api/assets/detail?scenario_id=${encodeURIComponent(state.scenarioId)}&asset_ref=${encodeURIComponent(assetRef)}`);
    renderAssetDetail(data);
  } catch (error) {
    el("assetDrawerTitle").textContent = `${assetRef} · 资产档案`;
    el("assetDrawerBody").innerHTML = `<p class="empty-note">资产详情读取失败：${escapeHtml(error.message)}</p>`;
  }
};

const closeAssetDetail = () => {
  el("assetDrawer").classList.remove("open");
  el("assetDrawer").setAttribute("aria-hidden", "true");
  el("assetDrawerBackdrop").hidden = true;
};

const renderAssetDetail = (data) => {
  const asset = data.asset || {};
  const policy = data.policy_narrative || {};
  const applicablePolicy = policy.applicable_policy || {};
  const lines = asArray(data.forecast_lines);
  const executions = asArray(data.rule_executions);
  const relationships = asArray(data.relationships);
  el("assetDrawerTitle").textContent = `${asset.asset_ref || "资产"} · ${asset.name || "资产档案"}`;
  el("assetDrawerBody").innerHTML = `
    <section class="asset-dossier-section">
      <h3>资产概览</h3>
      <div class="asset-detail-grid">
        <article><span>资产来源</span><strong>${escapeHtml(asset.asset_source_type_label_cn || asset.asset_source_type || "-")}</strong></article>
        <article><span>所属单位</span><strong>${escapeHtml(asset.department || "-")}</strong></article>
        <article><span>成本中心</span><strong>${escapeHtml(asset.cost_center || "-")}</strong></article>
        <article><span>资产类别</span><strong>${escapeHtml(asset.asset_category_label_cn || asset.asset_category || "-")}</strong></article>
        <article><span>折旧码</span><strong>${escapeHtml(asset.depreciation_code_label_cn || asset.depreciation_code || "-")}</strong></article>
        <article><span>资产状态</span><strong>${escapeHtml(asset.status || "-")}</strong></article>
        <article><span>原值 / 计划金额</span><strong>${money(asset.base_amount)}</strong></article>
        <article><span>预测期折旧合计</span><strong>${money(asset.forecast_depreciation_total)}</strong></article>
        <article><span>首次计提月份</span><strong>${escapeHtml(asset.first_depreciation_period || "-")}</strong></article>
      </div>
    </section>
    <section class="asset-dossier-section">
      <h3>适用政策与计算依据</h3>
      <p class="asset-narrative">${escapeHtml(policy.narrative_cn || "没有匹配到可展示的政策说明。")}</p>
      <div class="asset-detail-grid compact-detail-grid">
        <article><span>适用政策</span><strong>${escapeHtml(applicablePolicy.policy_label_cn || asset.depreciation_policy_label_cn || "-")}</strong></article>
        <article><span>折旧方法</span><strong>${escapeHtml(applicablePolicy.method_label_cn || asset.depreciation_method_label_cn || "-")}</strong></article>
        <article><span>使用年限</span><strong>${escapeHtml(applicablePolicy.useful_life_months || "-")} 月</strong></article>
        <article><span>残值率</span><strong>${escapeHtml(applicablePolicy.residual_rate_label_cn || "-")}</strong></article>
      </div>
    </section>
    <section class="asset-dossier-section">
      <h3>业务关系链</h3>
      <div class="relationship-chain">${relationships.map((item) => `<article><span>${escapeHtml(item.label_cn)}</span><strong>${escapeHtml(item.value_cn)}</strong></article>`).join("")}</div>
    </section>
    <section class="asset-dossier-section">
      <h3>月度折旧记录</h3>
      <div class="table-wrap compact"><table><thead><tr><th>期间</th><th>月折旧</th><th>期初净值</th><th>期末净值</th><th>计算规则</th></tr></thead><tbody>
        ${lines.map((line) => `<tr><td>${escapeHtml(line.period)}</td><td class="amount">${money(line.monthly_depreciation)}</td><td class="amount">${money(line.opening_net_value)}</td><td class="amount">${money(line.closing_net_value)}</td><td>${escapeHtml(ruleBranchDisplay(line.rule_branch_id || line.calculation_rule_id))}</td></tr>`).join("") || `<tr><td colspan="5">当前场景没有该资产的预测记录。</td></tr>`}
      </tbody></table></div>
    </section>
    <section class="asset-dossier-section">
      <h3>规则执行记录</h3>
      <div class="table-wrap compact"><table><thead><tr><th>期间</th><th>命中分支</th><th>计算公式</th><th>计算结论</th></tr></thead><tbody>
        ${executions.map((item) => `<tr><td>${escapeHtml(item.period)}</td><td>${escapeHtml(ruleBranchDisplay(item.branch_id))}</td><td>${escapeHtml(item.formula_cn || "-")}</td><td>${escapeHtml(item.conclusion_cn || "-")}</td></tr>`).join("") || `<tr><td colspan="4">没有可展示的规则执行记录。</td></tr>`}
      </tbody></table></div>
    </section>
  `;
};

const loadWideDimensionCatalog = async () => {
  state.wideCatalog = await api("/api/wide-table/dimensions");
  const dimensions = state.wideCatalog.dimensions || [];
  for (const id of ["wideDimension1", "wideDimension2", "wideDimension3"]) {
    const select = el(id);
    const current = select.value;
    select.innerHTML = `<option value="">不下钻</option>`;
    for (const dimension of dimensions) {
      const option = document.createElement("option");
      option.value = dimension.id;
      option.textContent = dimension.label_cn;
      option.title = dimension.description_cn || dimension.label_cn;
      select.appendChild(option);
    }
    if (Array.from(select.options).some((option) => option.value === current)) select.value = current;
  }
  for (const id of ["compareDimension1", "compareDimension2"]) {
    const select = el(id);
    const current = select.value;
    select.innerHTML = `<option value="">不下钻</option>`;
    for (const dimension of dimensions) {
      const option = document.createElement("option");
      option.value = dimension.id;
      option.textContent = dimension.label_cn;
      option.title = dimension.description_cn || dimension.label_cn;
      select.appendChild(option);
    }
    if (Array.from(select.options).some((option) => option.value === current)) select.value = current;
  }
};

const selectedWideDimensions = () => {
  const values = [];
  for (const id of ["wideDimension1", "wideDimension2", "wideDimension3"]) {
    const value = el(id).value;
    if (!value) break;
    values.push(value);
  }
  return values;
};

const normalizeWideDimensions = () => {
  const ids = ["wideDimension1", "wideDimension2", "wideDimension3"];
  const seen = new Set();
  let gapFound = false;
  for (const id of ids) {
    const select = el(id);
    if (gapFound || !select.value || seen.has(select.value)) {
      if (select.value && (gapFound || seen.has(select.value))) select.value = "";
      gapFound = true;
      continue;
    }
    seen.add(select.value);
  }
};

const selectedCompareDimensions = () => {
  const values = [];
  for (const id of ["compareDimension1", "compareDimension2"]) {
    const value = el(id).value;
    if (!value) break;
    values.push(value);
  }
  return values;
};

const normalizeCompareDimensions = () => {
  const first = el("compareDimension1");
  const second = el("compareDimension2");
  if (!first.value || second.value === first.value) second.value = "";
};

const loadWideTable = async () => {
  const params = new URLSearchParams({ scenario_id: state.scenarioId, row_type: "overview" });
  for (const dimension of selectedWideDimensions()) params.append("dimension", dimension);
  const data = await api(`/api/wide-table?${params.toString()}`);
  renderWideTable(data);
};

const askWideQuestion = async () => {
  const question = el("wideQuestionInput").value.trim();
  if (!question || state.wideQuestionPending) return;
  state.wideQuestionPending = true;
  const button = el("wideQuestionBtn");
  button.disabled = true;
  button.textContent = "正在推理...";
  el("wideQuestionResult").innerHTML = `<p class="empty-note">正在理解问题、对比逐资产折旧并追溯规则依据。模型表述通常需要数十秒，请勿重复提交。</p>`;
  const payload = {
    scenario_id: state.scenarioId,
    question,
    row_type: selectedWideDimensions().join(",") || "overview",
    conversation_id: state.wideQuestionConversationId,
  };
  try {
    const result = await api("/api/wide-table/question", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderWideQuestionResult(result);
    state.wideQuestionConversationId = result.conversation?.conversation_id || null;
    await loadWideQaStatus();
  } catch (error) {
    el("wideQuestionResult").innerHTML = `<section class="answer-card clarification-card"><span>本次问答未完成</span><p>${escapeHtml(error.message || "服务暂时不可用，请重试。")}</p><small>数据不会丢失。请稍后再次提问；系统会重新执行问题理解、逐资产对比和规则追溯。</small></section>`;
  } finally {
    state.wideQuestionPending = false;
    button.disabled = false;
    button.textContent = "提问";
  }
};

const bindClarificationFollowUp = ({ inputId, buttonId, sourceInputId, submit }) => {
  const input = el(inputId);
  const button = el(buttonId);
  if (!input || !button) return;
  const send = () => {
    const question = input.value.trim();
    if (!question) {
      input.focus();
      return;
    }
    el(sourceInputId).value = question;
    submit();
  };
  button.addEventListener("click", send);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") send();
  });
  input.focus();
};

const loadWideQaStatus = async () => {
  const status = await api("/api/qa/status");
  const last = status.last_call;
  const callText = last
    ? (last.used_llm ? `最近一次已调用 ${last.model || "DeepSeek"}` : `最近一次未调用模型：${last.fallback_reason || "模板降级"}`)
    : "尚未发起问答";
  el("wideQaStatus").className = `qa-connection-status ${status.configured ? "connected" : "disconnected"}`;
  el("wideQaStatus").textContent = `${status.message_cn || "模型状态未知"} ${callText}`;
};

const askReversePlanning = async () => {
  const question = el("reverseQuestionInput").value.trim();
  if (!question) return;
  const button = el("reverseQuestionBtn");
  const startedAt = Date.now();
  button.disabled = true;
  button.textContent = "推演中";
  const renderProgress = () => {
    const elapsed = ((Date.now() - startedAt) / 1000).toFixed(1);
    el("reversePlanningResult").innerHTML = `
      <section class="agent-progress" aria-live="polite">
        <strong>反向推演 Agent 正在执行</strong>
        <ol>
          <li><b>1</b><span>正在调用 DeepSeek 理解目标、范围、月份与金额单位</span></li>
          <li><b>2</b><span>等待 Harness 读取基准、定位对象并调用规则引擎试算</span></li>
          <li><b>3</b><span>Harness 完成后，将再次调用 DeepSeek 输出业务建议</span></li>
        </ol>
        <small>已等待 ${elapsed} 秒。金额、候选动作和推荐排序由本地规则引擎决定。</small>
      </section>`;
  };
  renderProgress();
  const timer = window.setInterval(renderProgress, 250);
  try {
    const result = await api("/api/reverse-planning/question", {
      method: "POST",
      body: JSON.stringify({ scenario_id: state.scenarioId, conversation_id: state.reversePlanningConversationId, question }),
    });
    state.reversePlanningConversationId = result.conversation?.conversation_id || null;
    renderReversePlanningResult(result);
  } catch (error) {
    el("reversePlanningResult").innerHTML = `<p class="empty-note">${escapeHtml(error.message)}</p>`;
  } finally {
    window.clearInterval(timer);
    button.disabled = false;
    button.textContent = "开始推演";
  }
};

const renderReversePlanningResult = (result) => {
  if (result.clarification) {
    const candidates = result.clarification.candidates || {};
    el("reversePlanningResult").innerHTML = `
      <section class="answer-card clarification-card">
        <span>需要补充</span>
        <p>${escapeHtml(result.clarification.question_cn || result.clarification_cn || "请补充目标信息。")}</p>
        <small>可推演期间：${escapeHtml(asArray(candidates.available_periods).join("、") || "-")}</small>
        <div class="clarification-input"><input id="reverseClarificationInput" aria-label="补充反向推演问题" placeholder="在这里补充目标范围、月份、金额或单位"><button id="reverseClarificationBtn" type="button">继续推演</button></div>
      </section>`;
    bindClarificationFollowUp({ inputId: "reverseClarificationInput", buttonId: "reverseClarificationBtn", sourceInputId: "reverseQuestionInput", submit: askReversePlanning });
    return;
  }
  const analysis = result.question_analysis || {};
  const recommendations = asArray(result.recommendations);
  const cards = recommendations.map((plan, index) => {
    const actions = asArray(plan.actions).map((action) => `<li><strong>${escapeHtml(action.label_cn)}</strong>${action.notice_cn ? `<span>${escapeHtml(action.notice_cn)}</span>` : ""}</li>`).join("");
    const rules = asArray(plan.rule_execution_trace).slice(0, 8).map((rule) => `<li>${escapeHtml(`${rule.asset_ref} · ${rule.branch_id} · ${rule.conclusion_cn}`)}</li>`).join("");
    return `<article class="reverse-plan-card">
      <div class="reverse-plan-head"><span>方案 ${index + 1} · ${escapeHtml(plan.selection_label_cn || "推荐方案")}</span><strong>目标偏差 ${money(plan.gap)}</strong></div>
      <dl><dt>试算结果</dt><dd>${money(plan.target_amount)}</dd><dt>影响对象</dt><dd>${escapeHtml(plan.affected_object_count)}</dd></dl>
      <p class="reverse-plan-reason">策略：${escapeHtml(plan.strategy_label_cn || "-")}。${escapeHtml(plan.selection_reason_cn || "")}</p>
      <h3>建议动作</h3><ul>${actions}</ul>
      <details><summary>规则执行证据</summary><ul>${rules || "<li>当前方案未返回明细证据</li>"}</ul></details>
    </article>`;
  }).join("");
  const paths = asArray(result.ontology_paths).map((path) => `<li><strong>方案 ${escapeHtml(path.recommendation_number || "-")}</strong><span>${escapeHtml(path.path_cn || "")}</span></li>`).join("");
  const mode = result.qa_skill?.used_llm ? `实时大模型 · ${result.qa_skill.model || "DeepSeek"}` : "确定性规则结论";
  const plan = result.question_plan || {};
  const calls = result.model_calls || {};
  const tools = asArray(result.harness?.tool_trace || result.tool_trace).map((item) => `<li><strong>${escapeHtml(item.label_cn || item.tool_name || "-")}</strong><span>${escapeHtml(item.data_source || "-")} · ${escapeHtml(item.tool_name || "-")}${item.result_shape ? ` · ${escapeHtml(Object.entries(item.result_shape).map(([key, value]) => `${key}: ${typeof value === "object" ? JSON.stringify(value) : value}`).join("，"))}` : ""}</span></li>`).join("");
  const callCards = [["阶段 1 · 目标理解", calls.question_understanding], ["阶段 2 · 业务表述", calls.answer_composition]].map(([label, call]) => {
    const status = call?.used_llm ? "已调用" : "未调用/已降级";
    const duration = call?.latency_ms ? `${(Number(call.latency_ms) / 1000).toFixed(2)} 秒` : (call?.fallback_reason || "无调用记录");
    return `<div class="model-call-card ${call?.used_llm ? "verified" : "fallback"}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(status)} · ${escapeHtml(call?.provider || "-")}</strong><small>${escapeHtml(call?.model || "模板结论")} · ${escapeHtml(duration)}</small></div>`;
  }).join("");
  const executionSummary = result.harness?.evidence_summary || {};
  const evaluation = result.candidate_evaluation || {};
  el("reversePlanningResult").innerHTML = `
    <section class="skill-status ${result.qa_skill?.used_llm ? "llm" : "fallback"}"><div><span>反向推演 Agent</span><strong>${escapeHtml(result.qa_skill?.skill_name || "reverse_depreciation_planning")}</strong></div><div><span>本次执行状态</span><strong>${escapeHtml(mode)}</strong></div><p>审计编号：${escapeHtml(result.audit_id || "-")}。本次执行 ${escapeHtml(executionSummary.simulation_count ?? 0)} 次临时规则试算；没有创建或保存 What-if 场景。</p></section>
    <section><h3>问题理解</h3><div class="analysis-grid"><div><span>问题类型</span><strong>${escapeHtml(analysis.intent_label_cn || "-")}</strong></div><div><span>目标范围</span><strong>${escapeHtml(analysis.scope_value || "-")}</strong></div><div><span>目标月份</span><strong>${escapeHtml(analysis.target_period || "-")}</strong></div><div><span>目标方向</span><strong>${escapeHtml(analysis.direction || "-")}</strong></div><div><span>置信度</span><strong>${escapeHtml(plan.confidence || analysis.confidence || "-")}</strong></div></div><div class="model-call-grid">${callCards}</div></section>
    <section><h3>目标与规则试算</h3><div class="reverse-target-summary"><div><span>基准折旧</span><strong>${money(result.baseline_amount)}</strong></div><div><span>目标折旧</span><strong>${money(result.target_amount)}</strong></div><div><span>需要变化</span><strong>${money(result.required_delta)}</strong></div><div><span>场景写入</span><strong>未写入</strong></div></div><p class="reverse-feasibility ${result.feasible ? "verified" : "limited"}">${escapeHtml(result.feasibility_cn || "正在核验目标可行性。")}</p><p class="reverse-evaluation">${escapeHtml(evaluation.coverage_cn || "候选动作正在按规则引擎试算。 ")} 已生成 ${escapeHtml(evaluation.generated_count ?? executionSummary.candidate_count ?? 0)} 项候选，执行 ${escapeHtml(evaluation.executed_count ?? executionSummary.simulation_count ?? 0)} 次试算，保留 ${escapeHtml(evaluation.valid_count ?? executionSummary.valid_simulation_count ?? 0)} 个有效结果，淘汰 ${escapeHtml(evaluation.rejected_count ?? 0)} 个无效结果。</p><ul class="tool-trace">${tools}</ul></section>
    <section class="reverse-path"><h3>Ontology 推演</h3><ul class="ontology-path-list">${paths || "<li><span>当前问题没有可展开的 Ontology 路径。</span></li>"}</ul></section>
    <section><h3>推荐结论</h3><div class="answer-card"><span>建议结论</span><p>${escapeHtml(result.answer_cn || "暂无建议")}</p><small>审计编号：${escapeHtml(result.audit_id || "-")} · ${escapeHtml(result.answer_validation?.reason_cn || "")}</small></div><div class="reverse-plan-grid">${cards || "<p class=\"empty-note\">当前范围没有可有效改变目标期折旧的规则动作。</p>"}</div></section>
  `;
};

const renderWideQuestionResult = (result) => {
  if (result.clarification) {
    const candidates = result.clarification.candidates || {};
    el("wideQuestionResult").innerHTML = `
      <section class="answer-card clarification-card">
        <span>需要补充</span>
        <p>${escapeHtml(result.clarification.question_cn || result.answer_cn || "请补充查询范围。")}</p>
        <small>当前可用期间：${escapeHtml(asArray(candidates.available_periods).join("、") || "-")}</small>
        <div class="clarification-input"><input id="wideClarificationInput" aria-label="补充宽表问题" placeholder="在这里补充所属单位、资产类别、月份或比较范围"><button id="wideClarificationBtn" type="button">继续提问</button></div>
      </section>`;
    bindClarificationFollowUp({ inputId: "wideClarificationInput", buttonId: "wideClarificationBtn", sourceInputId: "wideQuestionInput", submit: askWideQuestion });
    return;
  }
  const analysis = result.question_analysis || {};
  const qaSkill = result.qa_skill || {};
  const amountOrDash = (value) => (value === undefined || value === null || value === "-" ? "-" : money(value));
  const steps = asArray(result.reasoning_steps).map((step) => `
    <article class="reason-step">
      <span>${escapeHtml(step.step)}</span>
      <div>
        <strong>${escapeHtml(step.title_cn || "推理步骤")}</strong>
        <p>${escapeHtml(step.detail_cn || "")}</p>
      </div>
    </article>
  `).join("");
  const facts = result.facts || {};
  const significantSummary = facts.significant_driver_count
    ? `按 ${facts.significance_rule_cn || "显著差异口径"}，展示 ${facts.significant_driver_count} 项，覆盖绝对差异 ${facts.significance_coverage_percent || "-"}%；其余 ${facts.immaterial_driver_count || 0} 项小额差异合计 ${money(facts.immaterial_difference)}。`
    : "";
  const assetRows = asArray(facts.drivers || facts.top_assets).map((asset) => `
    <tr>
      <td>${escapeHtml(asset.asset_ref)}</td>
      <td>${escapeHtml(asset.asset_category_label_cn || categoryLabels[asset.asset_category] || asset.asset_category || "-")}</td>
      <td>${escapeHtml(asset.depreciation_policy_label_cn || policyDisplay(asset.depreciation_policy))}</td>
      <td class="amount">${amountOrDash(asset.previous_amount)}</td>
      <td class="amount">${amountOrDash(asset.target_amount ?? asset.depreciation)}</td>
      <td class="amount">${amountOrDash(asset.difference)}</td>
      <td>${escapeHtml(asset.calculation_evidence_cn || asset.driver_reason_cn || asset.driver_type || asset.driver_category || "-")}</td>
    </tr>
  `).join("");
  const plan = result.question_plan || {};
  const modelCalls = result.model_calls || {};
  const planItems = [
    ["问题类型", analysis.intent_label_cn || "-"],
    ["识别对象", analysis.department || analysis.asset_category_label_cn || analysis.asset_ref || "当前宽表范围"],
    ["目标期间", analysis.target_period || "-"],
    ["对比期间", analysis.previous_period || "-"],
    ["置信度", plan.confidence || analysis.confidence || "-"],
  ];
  const skillMode = qaSkill.used_llm ? `实时大模型 · ${qaSkill.model || qaSkill.provider || "-"}` : `模板降级 · ${qaSkill.provider || "-"}`;
  const traceRows = asArray(result.harness?.tool_trace || qaSkill.tool_trace).map((item) => `
    <li>
      <strong>${escapeHtml(item.label_cn || item.tool_name || "-")}</strong>
      <span>${escapeHtml(item.data_source || "-")} · ${escapeHtml(item.tool_name || "-")}${item.result_shape ? ` · ${escapeHtml(Object.entries(item.result_shape).map(([key, value]) => `${key}: ${typeof value === "object" ? JSON.stringify(value) : value}`).join("，"))}` : ""}</span>
    </li>
  `).join("");
  const pathRows = asArray(result.ontology_paths).map((item) => {
    const path = item.path || item;
    return `<li><strong>${escapeHtml(item.asset_ref || path.asset_ref || "关联对象")}</strong><span>${escapeHtml(path.narrative_cn || item.driver_reason_cn || "已追溯到资产、折旧码、方法和规则。")}</span></li>`;
  }).join("");
  const calls = [
    ["问题理解", modelCalls.question_understanding],
    ["业务表述", modelCalls.answer_composition],
  ].map(([label, call]) => `<div><span>${escapeHtml(label)}</span><strong>${call?.used_llm ? `DeepSeek${call.model ? ` · ${call.model}` : ""}` : "未调用模型"}</strong><small>${call?.latency_ms ? `${call.latency_ms} ms` : (call?.fallback_reason || "")}</small></div>`).join("");
  const executionRows = asArray(result.rule_execution_trace).map((item) => `
    <tr>
      <td>${escapeHtml(item.asset_ref)}</td>
      <td>${escapeHtml(item.period)}</td>
      <td>${escapeHtml(ruleBranchDisplay(item.branch_id))}</td>
      <td>${escapeHtml(item.formula_cn)}</td>
      <td>${escapeHtml(item.conclusion_cn)}</td>
    </tr>
  `).join("");
  el("wideQuestionResult").innerHTML = `
    <section class="skill-status ${qaSkill.used_llm ? "llm" : "fallback"}">
      <div>
        <span>问答 Skill</span>
        <strong>${escapeHtml(qaSkill.skill_name || "wide_table_finance_qa")}</strong>
      </div>
      <div>
        <span>生成模式</span>
        <strong>${escapeHtml(skillMode)}</strong>
      </div>
      ${qaSkill.fallback_reason ? `<p>本次使用已核验的确定性证据结论；资产差异、规则依据和图谱路径均已完整保留。</p>` : ""}
    </section>
    <section>
      <h3>问题理解</h3>
      <div class="analysis-grid">
        ${planItems.map(([label, value]) => `
          <div>
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(value)}</strong>
          </div>
        `).join("")}
      </div>
      <div class="model-call-grid">${calls}</div>
    </section>
    <section>
      <h3>数据与计算</h3>
      ${result.harness?.evidence_summary ? `<p class="table-note">${escapeHtml(Object.entries(result.harness.evidence_summary).map(([key, value]) => `${key}: ${value}`).join("；"))}</p>` : ""}
      <ul class="tool-trace">${traceRows || `<li><strong>暂无轨迹</strong><span>-</span></li>`}</ul>
    </section>
    <section>
      <h3>Ontology 推理</h3>
      <div class="reason-list">${steps}</div>
      <ul class="ontology-path-list">${pathRows || "<li><span>当前问题没有需要展开的图谱路径。</span></li>"}</ul>
    </section>
    <section>
      <h3>业务结论</h3>
      <div class="answer-card"><span>结论</span><p>${escapeHtml(result.answer_cn || "暂无回答")}</p><small>审计编号：${escapeHtml(result.audit_id || "-")} · ${escapeHtml(result.answer_validation?.reason_cn || "")}</small></div>
    </section>
    <section>
      <h3>规则执行证据</h3>
      <div class="table-wrap compact">
        <table>
          <thead><tr><th>资产</th><th>月份</th><th>命中分支</th><th>公式</th><th>业务结论</th></tr></thead>
          <tbody>${executionRows || `<tr><td colspan="5">当前问题没有需要展开的规则执行记录</td></tr>`}</tbody>
        </table>
      </div>
    </section>
    <section>
      <h3>${facts.drivers ? "差异驱动资产" : "主要贡献资产"}</h3>
      ${significantSummary ? `<p class="table-note">${escapeHtml(significantSummary)}</p>` : ""}
      <div class="table-wrap compact">
        <table>
          <thead><tr><th>资产</th><th>类别</th><th>政策</th><th>上期</th><th>本期</th><th>差异</th><th>原因</th></tr></thead>
          <tbody>${assetRows || `<tr><td colspan="7">暂无相关资产</td></tr>`}</tbody>
        </table>
      </div>
    </section>
  `;
};

const renderWideTable = (data) => {
  const dimensions = data.dimensions || [];
  const dimensionNames = dimensions.map((item) => (
    data.dimension_catalog?.dimensions?.find((dimension) => dimension.id === item)?.label_cn || labels[item] || item
  ));
  const visibleNodes = [];
  const appendNodes = (nodes) => {
    for (const node of nodes || []) {
      visibleNodes.push(node);
      if ((node.children || []).length && state.wideExpanded.has(node.id)) appendNodes(node.children);
    }
  };
  appendNodes(data.tree || []);
  const scope = dimensionNames.length ? `下钻维度：${dimensionNames.join(" > ")}` : "总览：全部资产";
  el("wideStatus").textContent = `${data.scenario_id} · ${scope} · ${visibleNodes.length} 行 · ${data.periods.length} 个月`;
  el("wideTableHead").innerHTML = `
    <tr>
      <th class="sticky-col sticky-1">层级</th>
      <th class="sticky-col sticky-2">分析对象</th>
      <th class="sticky-col sticky-3">期间合计</th>
      ${data.periods.map((period) => {
        const meta = data.period_metadata?.[period];
        return `<th class="month-head">${escapeHtml(period)}${meta ? `<small>${escapeHtml(meta.label_cn)}</small>` : ""}</th>`;
      }).join("")}
    </tr>
  `;
  el("wideTableBody").innerHTML = visibleNodes.map((node) => `
    <tr>
      <td class="sticky-col sticky-1">${escapeHtml(node.dimension_label_cn || node.dimension)}</td>
      <td class="wide-node sticky-col sticky-2" style="--node-depth:${Number(node.depth || 0)}">
        ${(node.children || []).length ? `<button class="tree-toggle" type="button" data-tree-id="${escapeHtml(node.id)}" aria-label="展开或收起">${state.wideExpanded.has(node.id) ? "−" : "+"}</button>` : "<span class=\"tree-leaf\"></span>"}
        ${node.dimension === "asset" ? `<button class="link-button" type="button" data-policy-ref="${escapeHtml(node.value)}">${escapeHtml(node.label_cn)}</button>` : escapeHtml(node.label_cn)}
      </td>
      <td class="amount total-cell sticky-col sticky-3">${money(node.annual_total)}</td>
      ${data.periods.map((period) => {
        const amount = Number(node.months?.[period] || 0);
        return `<td class="amount ${amount === 0 ? "zero" : ""}">${amount === 0 ? "-" : money(amount)}</td>`;
      }).join("")}
    </tr>
  `).join("") || `<tr><td colspan="${data.periods.length + 3}">没有符合条件的数据</td></tr>`;
  el("wideTableBody").querySelectorAll("[data-tree-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const id = button.dataset.treeId;
      if (state.wideExpanded.has(id)) state.wideExpanded.delete(id);
      else state.wideExpanded.add(id);
      renderWideTable(data);
    });
  });
  el("wideTableBody").querySelectorAll("[data-policy-ref]").forEach((button) => {
    button.addEventListener("click", () => drillToPolicy(button.dataset.policyRef));
  });
};


const loadAnomalies = async () => {
  const rows = await api(`/api/anomalies?scenario_id=${encodeURIComponent(state.scenarioId)}`);
  const tbody = el("anomalyBody");
  tbody.innerHTML = "";
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6">暂无异常</td></tr>`;
    return;
  }
  for (const row of rows) {
    const objectId = firstValue(row, ["object_id", "asset_ref", "target_id"]);
    const objectLabel = firstValue(row, [
      "object_cn",
      "object_name_cn",
      "target_cn",
    ], "");
    const objectText = objectLabel || `${firstValue(row, ["object_type_label_cn"], "对象")} ${semanticObjectDisplay(objectId)}`;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(firstValue(row, ["severity_label_cn", "level_cn", "severity_cn", "severity", "level"], "-"))}</td>
      <td>${escapeHtml(objectText)}</td>
      <td>${escapeHtml(firstValue(row, ["problem_cn", "issue_cn", "message_cn", "message", "rule_id"], "-"))}</td>
      <td>${escapeHtml(firstValue(row, ["impact_cn", "impact", "business_impact"], "-"))}</td>
      <td>${escapeHtml(firstValue(row, ["suggestion_cn", "recommended_action_cn", "handling_cn", "suggestion"], "-"))}</td>
      <td>${objectId ? `<button class="inline-action" type="button">追溯</button>` : escapeHtml(firstValue(row, ["trace_cn", "trace"], "-"))}</td>
    `;
    tr.querySelector("button")?.addEventListener("click", () => drillToPolicy(objectId));
    tbody.appendChild(tr);
  }
};

const loadRuleCatalog = async () => {
  state.ruleCatalog = await api("/api/rule-catalog");
  await refreshScenarioAssetComposer();
};

const showWhatIfList = () => {
  el("whatIfListView").hidden = false;
  el("whatIfEditorView").hidden = true;
};

const showWhatIfEditor = () => {
  el("whatIfListView").hidden = true;
  el("whatIfEditorView").hidden = false;
};

const resetScenarioEditor = () => {
  state.scenarioEditingId = null;
  state.scenarioDraftAssumptions = [];
  state.scenarioAssetDetail = null;
  el("whatIfEditorTitle").textContent = "新建测算场景";
  el("scenarioName").value = "";
  el("scenarioDescription").value = "";
  el("scenarioBase").value = "BASELINE";
  el("scenarioBase").disabled = false;
  el("whatIfResult").innerHTML = "";
  hydrateScenarioAssetOptions();
  renderScenarioAssumptions();
};

const loadWhatIfScenarioList = async () => {
  const scenarios = state.scenarios.filter((scenario) => scenario.scenario_id !== "BASELINE");
  const tbody = el("whatIfScenarioList");
  el("whatIfScenarioStatus").textContent = scenarios.length
    ? `已保存 ${scenarios.length} 个测算场景。`
    : "暂未创建测算场景。点击“新建测算场景”开始。";
  tbody.innerHTML = scenarios.map((scenario) => `
    <tr>
      <td><strong>${escapeHtml(scenario.scenario_name || scenario.scenario_id)}</strong><small>${escapeHtml(scenario.scenario_id)}</small></td>
      <td>${escapeHtml(scenario.base_scenario_id || "BASELINE")}</td>
      <td class="amount">${Array.isArray(scenario.assumptions) ? scenario.assumptions.length : 0}</td>
      <td>${escapeHtml(scenario.description || "-")}</td>
      <td>${escapeHtml(scenario.updated_at || scenario.created_at || "-")}</td>
      <td class="scenario-row-actions">
        <button type="button" data-edit-scenario="${escapeHtml(scenario.scenario_id)}">编辑</button>
        <button type="button" data-delete-scenario="${escapeHtml(scenario.scenario_id)}">删除</button>
      </td>
    </tr>
  `).join("") || `<tr><td colspan="6">暂无已保存的测算场景。</td></tr>`;
  tbody.querySelectorAll("[data-edit-scenario]").forEach((button) => {
    button.addEventListener("click", () => editScenario(button.dataset.editScenario));
  });
  tbody.querySelectorAll("[data-delete-scenario]").forEach((button) => {
    button.addEventListener("click", () => deleteScenario(button.dataset.deleteScenario));
  });
};

const editScenario = async (scenarioId) => {
  const detail = await api(`/api/scenarios/${encodeURIComponent(scenarioId)}`);
  state.scenarioEditingId = scenarioId;
  state.scenarioDraftAssumptions = (detail.assumptions || []).map((assumption) => ({
    ...assumption,
    draft_id: `draft-${Date.now()}-${Math.random().toString(16).slice(2)}`,
  }));
  el("whatIfEditorTitle").textContent = `编辑测算场景 · ${detail.scenario.scenario_name || scenarioId}`;
  el("scenarioName").value = detail.scenario.scenario_name || "";
  el("scenarioDescription").value = detail.scenario.description || "";
  el("scenarioBase").value = detail.scenario.base_scenario_id || "BASELINE";
  el("scenarioBase").disabled = true;
  el("whatIfResult").innerHTML = "";
  renderScenarioAssumptions();
  showWhatIfEditor();
  await refreshScenarioAssetComposer();
};

const deleteScenario = async (scenarioId) => {
  const scenario = state.scenarios.find((item) => item.scenario_id === scenarioId);
  if (!window.confirm(`确定删除“${scenario?.scenario_name || scenarioId}”及其计算结果吗？此操作不可恢复。`)) return;
  await api(`/api/scenarios/${encodeURIComponent(scenarioId)}`, { method: "DELETE" });
  if (state.scenarioId === scenarioId) state.scenarioId = "BASELINE";
  await loadScenarios();
  await loadDashboard();
  await loadWhatIfScenarioList();
};

const scenarioSelectedCard = () => state.assetCards.find((card) => card.assetRef === el("scenarioAsset")?.value) || null;

const scenarioTemplatesFor = (asset) => {
  if (!asset || !state.ruleCatalog) return [];
  return (state.ruleCatalog.methods || []).find((method) => method.method === asset.depreciationMethod)?.templates || [];
};

const scenarioTemplate = (templateId = el("ruleTemplate")?.value) => (
  (state.ruleCatalog?.methods || []).flatMap((method) => method.templates || []).find((item) => item.id === templateId)
);

const scenarioPeriods = () => state.snapshotStatus?.forecast_periods || (state.scenarioDefaultPeriod ? [state.scenarioDefaultPeriod] : []);

const scenarioPeriodOptions = (selected = state.scenarioDefaultPeriod) => scenarioPeriods().map((period) => (
  `<option value="${escapeHtml(period)}" ${period === selected ? "selected" : ""}>${escapeHtml(period)}</option>`
)).join("");

const scenarioField = (id, label, { type = "number", value = "", hint = "", wide = false, min = "", step = "" } = {}) => `
  <label class="${wide ? "wide-field" : ""}">${escapeHtml(label)}
    <input id="${id}" type="${type}" value="${escapeHtml(value)}" ${min !== "" ? `min="${min}"` : ""} ${step ? `step="${step}"` : ""}>
    ${hint ? `<span class="scenario-field-hint">${escapeHtml(hint)}</span>` : ""}
  </label>`;

const scenarioSelectField = (id, label, options, hint = "") => `
  <label>${escapeHtml(label)}
    <select id="${id}">${options}</select>
    ${hint ? `<span class="scenario-field-hint">${escapeHtml(hint)}</span>` : ""}
  </label>`;

const renderScenarioAssetBaseline = () => {
  const container = el("scenarioAssetBaseline");
  const detail = state.scenarioAssetDetail;
  const asset = detail?.asset || scenarioSelectedCard();
  if (!asset) {
    container.classList.remove("active");
    container.innerHTML = "";
    return;
  }
  const policy = detail?.policy_narrative?.applicable_policy || {};
  const source = detail?.source_context || {};
  const lines = detail?.forecast_lines || [];
  const periods = lines.map((line) => `${line.period} ${money(line.monthly_depreciation)}`).join(" · ") || "当前场景尚无月度记录";
  const method = asset.depreciationMethodLabel || asset.depreciation_method_label_cn || "未匹配";
  const code = asset.depreciationCodeLabel || asset.depreciation_code_label_cn || asset.depreciation_code || "未登记";
  const usefulLife = source.useful_life_months || policy.useful_life_months || "-";
  const startRule = source.start_rule === "CURRENT_MONTH" ? "当月开始计提" : source.start_rule === "NEXT_MONTH" ? "次月开始计提" : (policy.start_rule_label_cn || "-");
  container.classList.add("active");
  container.innerHTML = `
    <div class="scenario-baseline-head"><strong>当前资产折旧信息（只读）</strong><span>${escapeHtml(asset.asset_ref || asset.assetRef || "")}</span></div>
    <div class="scenario-baseline-grid">
      <span>资产名称<b>${escapeHtml(asset.name || "-")}</b></span>
      <span>折旧方法<b>${escapeHtml(method)}</b></span>
      <span>折旧码<b>${escapeHtml(code)}</b></span>
      <span>原值<b>${money(asset.baseAmount ?? asset.base_amount ?? asset.original_or_planned_amount)}</b></span>
      <span>累计折旧<b>${money(lines[0]?.opening_accumulated_depreciation)}</b></span>
      <span>使用年限 / 残值率<b>${escapeHtml(`${usefulLife} 个月 / ${policy.residual_rate_label_cn || source.residual_rate || "-"}`)}</b></span>
      <span>开始计提<b>${escapeHtml(startRule)}</b></span>
      <span>资产类别<b>${escapeHtml(asset.asset_category_label_cn || asset.category || "-")}</b></span>
    </div>
    <p class="scenario-baseline-note">月度折旧：${escapeHtml(periods)}。这些是当前场景的基准信息，仅用于核对，不可在此修改。</p>`;
};

const renderScenarioDynamicFields = () => {
  const container = el("scenarioDynamicFields");
  const asset = scenarioSelectedCard();
  const template = scenarioTemplate();
  const detail = state.scenarioAssetDetail;
  if (!asset || !template) {
    container.innerHTML = asset ? `<p class="empty-note">该资产未匹配可用的规则场景模板。</p>` : `<p class="empty-note">请先选择一项现有资产。</p>`;
    return;
  }
  const driver = detail?.driver_context || {};
  const baseline = driver.by_period?.[state.scenarioDefaultPeriod] || {};
  const periodSelect = scenarioSelectField("scenarioPeriod", "生效月份", scenarioPeriodOptions(), "只可选择当前预测范围内的月份。");
  if (template.id === "straight_impairment") {
    container.innerHTML = [
      scenarioField("scenarioAmount", "减值金额", { min: "0", step: "0.01", hint: "输入本次假设新增的减值金额。" }),
      periodSelect,
    ].join("");
  } else if (template.id === "straight_accelerated") {
    container.innerHTML = `<div class="scenario-readonly-target full-field"><b>无需额外填写</b><span class="scenario-field-hint">系统将把该资产的使用年限按基准年限的 60% 重算；原使用年限可在上方只读信息中核对。</span></div>`;
  } else if (template.id === "straight_start_rule") {
    container.innerHTML = scenarioSelectField(
      "scenarioStartRule", "开始计提规则",
      `<option value="NEXT_MONTH">次月开始计提</option><option value="CURRENT_MONTH">当月开始计提</option>`,
      "仅调整该资产的开始计提规则。"
    );
  } else if (template.id === "straight_new_asset") {
    container.innerHTML = [
      scenarioField("scenarioAssetName", "新增资产名称", { type: "text", value: `${asset.name || "资产"}（新增）`, hint: "将沿用所选资产的组织、类别和折旧码。" }),
      scenarioField("scenarioAmount", "新增资产原值", { min: "0", step: "0.01" }),
      scenarioField("scenarioInServiceDate", "资本化日期", { type: "date", value: state.scenarioDefaultPeriod ? `${state.scenarioDefaultPeriod}-01` : "" }),
    ].join("");
  } else if (template.id === "production_driver") {
    if (!driver.target_id) {
      container.innerHTML = `<p class="empty-note">该资产未登记所属区块，无法建立产量法假设。</p>`;
      return;
    }
    container.innerHTML = [
      `<div class="scenario-readonly-target"><b>${escapeHtml(driver.target_label_cn || `区块 ${driver.target_id}`)}</b><span class="scenario-field-hint">目标区块由资产台账自动带入，不可修改。</span></div>`,
      periodSelect,
      scenarioField("scenarioProduction", "区块产量", { min: "0", step: "0.0001", hint: `基准 ${baseline.production ?? "-"}；留空表示保持基准。` }),
      scenarioField("scenarioReserves", "剩余储量", { min: "0", step: "0.0001", hint: `基准 ${baseline.reserves ?? "-"}；留空表示保持基准。` }),
    ].join("");
  } else if (template.id === "workload_driver") {
    if (!driver.target_id) {
      container.innerHTML = `<p class="empty-note">该资产未登记工作量法分摊对象，无法建立工作量法假设。</p>`;
      return;
    }
    container.innerHTML = [
      `<div class="scenario-readonly-target"><b>${escapeHtml(driver.target_label_cn || driver.target_id)}</b><span class="scenario-field-hint">分摊对象由资产台账自动带入，不可修改。</span></div>`,
      periodSelect,
      scenarioField("scenarioWorkload", "工作量", { min: "0", step: "0.0001", hint: `基准 ${baseline.workload ?? "-"}；留空表示保持基准。` }),
      scenarioField("scenarioUnitFee", "单位费用", { min: "0", step: "0.0001", hint: `基准 ${baseline.unit_fee ?? "-"}；留空表示保持基准。` }),
      scenarioField("scenarioTotalAmortization", "当月总摊销额", { min: "0", step: "0.01", hint: `基准 ${baseline.total_amortization || "-"}；填写后优先采用该金额。`, wide: true }),
    ].join("");
  } else {
    container.innerHTML = `<p class="empty-note">当前规则模板尚未配置输入项。</p>`;
  }
};

const refreshScenarioTemplateOptions = () => {
  const select = el("ruleTemplate");
  const asset = scenarioSelectedCard();
  if (!select) return;
  const current = select.value;
  const templates = scenarioTemplatesFor(asset);
  select.innerHTML = "";
  if (!asset) {
    select.disabled = true;
    select.innerHTML = `<option value="">请先选择资产</option>`;
    return;
  }
  if (!templates.length) {
    select.disabled = true;
    select.innerHTML = `<option value="">该折旧方法暂不支持场景假设</option>`;
    return;
  }
  select.disabled = false;
  for (const template of templates) {
    const option = document.createElement("option");
    option.value = template.id;
    option.textContent = template.label_cn;
    option.title = template.description_cn;
    select.appendChild(option);
  }
  select.value = templates.some((item) => item.id === current) ? current : templates[0].id;
};

const refreshScenarioAssetComposer = async () => {
  refreshScenarioTemplateOptions();
  const asset = scenarioSelectedCard();
  if (!asset) {
    state.scenarioAssetDetail = null;
    renderScenarioAssetBaseline();
    renderScenarioDynamicFields();
    return;
  }
  const baseline = el("scenarioAssetBaseline");
  baseline.classList.add("active");
  baseline.innerHTML = `<p class="scenario-baseline-note">正在读取 ${escapeHtml(asset.assetRef)} 的当前折旧信息和规则参数...</p>`;
  try {
    state.scenarioAssetDetail = await api(`/api/assets/detail?scenario_id=${encodeURIComponent(el("scenarioBase")?.value || state.scenarioId)}&asset_ref=${encodeURIComponent(asset.assetRef)}`);
  } catch (error) {
    state.scenarioAssetDetail = null;
    baseline.innerHTML = `<p class="scenario-baseline-note">未能读取资产折旧信息：${escapeHtml(error.message)}</p>`;
  }
  renderScenarioAssetBaseline();
  renderScenarioDynamicFields();
};

const buildScenarioAssumption = () => {
  const templateId = el("ruleTemplate").value;
  const asset = scenarioSelectedCard();
  const detail = state.scenarioAssetDetail || {};
  if (!asset || !templateId) throw new Error("请先选择目标资产和假设类型。");
  const value = (id) => el(id)?.value?.trim?.() ?? el(id)?.value ?? "";
  const period = value("scenarioPeriod");
  const assumption = {
    draft_id: `draft-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    template_id: templateId,
    target_id: asset.assetRef,
    asset_id: asset.assetRef,
    reference_asset_id: asset.assetRef,
  };
  if (templateId === "straight_impairment") {
    if (value("scenarioAmount") === "" || !period) throw new Error("请填写减值金额和生效月份。");
    Object.assign(assumption, { amount: value("scenarioAmount"), period, effective_date: `${period}-01` });
  } else if (templateId === "straight_start_rule") {
    Object.assign(assumption, { start_rule: value("scenarioStartRule") || "NEXT_MONTH" });
  } else if (templateId === "straight_new_asset") {
    if (value("scenarioAmount") === "" || !value("scenarioInServiceDate")) throw new Error("请填写新增资产原值和资本化日期。");
    Object.assign(assumption, {
      asset_id: `NEW-${Date.now().toString().slice(-8)}`,
      asset_name: value("scenarioAssetName") || `${asset.name}（新增）`,
      asset_category: asset.category,
      depreciation_code: asset.depreciationCode,
      amount: value("scenarioAmount"),
      in_service_date: value("scenarioInServiceDate"),
    });
  } else if (templateId === "production_driver") {
    const driver = detail.driver_context || {};
    if (!driver.target_id || !period) throw new Error("该产量法资产缺少区块或生效月份，无法建立假设。");
    if (value("scenarioProduction") === "" && value("scenarioReserves") === "") throw new Error("请至少填写区块产量或剩余储量中的一项。");
    Object.assign(assumption, {
      block_id: driver.target_id, company: asset.company, period,
      ...(value("scenarioProduction") !== "" ? { production: value("scenarioProduction") } : {}),
      ...(value("scenarioReserves") !== "" ? { reserves: value("scenarioReserves") } : {}),
    });
  } else if (templateId === "workload_driver") {
    const driver = detail.driver_context || {};
    if (!driver.target_id || !period) throw new Error("该工作量法资产缺少分摊对象或生效月份，无法建立假设。");
    if (["scenarioWorkload", "scenarioUnitFee", "scenarioTotalAmortization"].every((id) => value(id) === "")) {
      throw new Error("请至少填写工作量、单位费用或当月总摊销额中的一项。");
    }
    Object.assign(assumption, {
      company: driver.target_id, period,
      ...(value("scenarioWorkload") !== "" ? { workload: value("scenarioWorkload") } : {}),
      ...(value("scenarioUnitFee") !== "" ? { unit_fee: value("scenarioUnitFee") } : {}),
      ...(value("scenarioTotalAmortization") !== "" ? { total_amortization: value("scenarioTotalAmortization") } : {}),
    });
  }
  return assumption;
};

const scenarioAssumptionLabel = (assumption) => {
  const template = (state.ruleCatalog?.methods || [])
    .flatMap((method) => method.templates || [])
    .find((item) => item.id === assumption.template_id);
  const target = assumption.template_id === "straight_new_asset"
    ? `${assumption.asset_name || "新增资产"}（参照 ${assumption.reference_asset_id || "-"}）`
    : assumption.block_id || assumption.asset_id || assumption.company || "待指定对象";
  return `${template?.label_cn || assumption.template_id} · ${target}`;
};

const scenarioAssumptionSummary = (assumption) => {
  const pairs = [];
  if (assumption.period) pairs.push(`生效月份 ${assumption.period}`);
  if (assumption.amount !== undefined) pairs.push(`${assumption.template_id === "straight_impairment" ? "减值金额" : "金额"} ${money(assumption.amount)}`);
  if (assumption.production !== undefined) pairs.push(`产量 ${assumption.production}`);
  if (assumption.reserves !== undefined) pairs.push(`剩余储量 ${assumption.reserves}`);
  if (assumption.workload !== undefined) pairs.push(`工作量 ${assumption.workload}`);
  if (assumption.unit_fee !== undefined) pairs.push(`单位费用 ${assumption.unit_fee}`);
  if (assumption.total_amortization !== undefined) pairs.push(`总摊销额 ${money(assumption.total_amortization)}`);
  if (assumption.start_rule) pairs.push(assumption.start_rule === "CURRENT_MONTH" ? "当月开始计提" : "次月开始计提");
  return pairs.join("；") || "按规则默认参数执行";
};

const renderScenarioAssumptions = () => {
  const container = el("scenarioAssumptionList");
  if (!state.scenarioDraftAssumptions.length) {
    container.innerHTML = `<p class="empty-note">尚未加入假设。填写上方规则输入后点击“加入场景假设”。</p>`;
    el("scenarioDraftHint").textContent = "请先添加至少一条资产假设。";
    return;
  }
  container.innerHTML = state.scenarioDraftAssumptions.map((assumption, index) => `
    <article class="scenario-assumption-item">
      <div class="assumption-summary"><strong>${escapeHtml(`${index + 1}. ${scenarioAssumptionLabel(assumption)}`)}</strong><span>${escapeHtml(scenarioAssumptionSummary(assumption))}</span></div>
      <button type="button" data-draft-id="${escapeHtml(assumption.draft_id)}" title="移除该假设">移除</button>
    </article>
  `).join("");
  el("scenarioDraftHint").textContent = `已添加 ${state.scenarioDraftAssumptions.length} 条资产假设，保存后统一重算。`;
  container.querySelectorAll("[data-draft-id]").forEach((button) => {
    button.addEventListener("click", () => {
      state.scenarioDraftAssumptions = state.scenarioDraftAssumptions.filter((item) => item.draft_id !== button.dataset.draftId);
      renderScenarioAssumptions();
    });
  });
};

const addScenarioAssumption = () => {
  try {
    state.scenarioDraftAssumptions.push(buildScenarioAssumption());
    renderScenarioAssumptions();
    el("whatIfResult").textContent = `已加入 ${state.scenarioDraftAssumptions.length} 条场景假设，保存后会统一重算。`;
    renderScenarioDynamicFields();
  } catch (error) {
    el("whatIfResult").textContent = error.message;
  }
};

const submitScenario = async (event) => {
  event.preventDefault();
  if (!state.scenarioDraftAssumptions.length) {
    try {
      state.scenarioDraftAssumptions.push(buildScenarioAssumption());
    } catch (error) {
      el("whatIfResult").textContent = error.message;
      return;
    }
  }
  await runWhatIf({
    base_scenario_id: el("scenarioBase").value || "BASELINE",
    scenario_name: el("scenarioName").value.trim() || "规则场景测算",
    description: el("scenarioDescription").value.trim(),
    assumptions: state.scenarioDraftAssumptions.map(({ draft_id, ...assumption }) => assumption),
  });
};

const runWhatIf = async (payload) => {
  el("whatIfResult").textContent = state.scenarioEditingId ? "正在更新场景并重算..." : "正在确认场景并计算...";
  const url = state.scenarioEditingId
    ? `/api/scenarios/${encodeURIComponent(state.scenarioEditingId)}/assumptions`
    : "/api/scenarios";
  const body = state.scenarioEditingId
    ? {
      assumptions: payload.assumptions,
      replace_existing: true,
      scenario_name: payload.scenario_name,
      description: payload.description,
    }
    : payload;
  const result = await api(url, {
    method: "POST",
    body: JSON.stringify(body),
  });
  state.scenarioId = result.scenario.scenario_id;
  state.scenarioDraftAssumptions = [];
  await loadScenarios();
  await loadDashboard();
  await loadWhatIfScenarioList();
  showWhatIfList();
};

const renderWhatIfResult = (result) => {
  const changes = (result.changes || result.assumptions || []).map((row) => `
    <tr>
      <td>${escapeHtml(row.field_name || row.template_id || "规则场景")}</td>
      <td>${escapeHtml(row.old_value || "基准假设")}</td>
      <td>${escapeHtml(row.new_value || row.target_id || row.block_id || "已输入")}</td>
      <td>${escapeHtml(row.reason || row.note || "已保存并参与重算")}</td>
    </tr>
  `).join("");
  const rows = (result.attributions || []).slice(0, 12).map((row) => `
    <tr>
      <td>${escapeHtml(row.period)}</td>
      <td>${escapeHtml(row.object_id)}</td>
      <td>${escapeHtml(labels[row.driver_type] || row.driver_type)}</td>
      <td class="amount">${money(row.baseline_depreciation)}</td>
      <td class="amount">${money(row.scenario_depreciation)}</td>
      <td class="amount">${money(row.difference)}</td>
    </tr>
  `).join("");
  el("whatIfResult").innerHTML = `
    <h3>${escapeHtml(result.scenario.scenario_name || result.scenario.scenario_id)}</h3>
    <p>已生成独立场景并写入业务库。当前预测范围折旧合计：<strong>${money(result.dashboard.kpis.total_depreciation)}</strong></p>
    <div class="table-wrap compact">
      <table>
        <thead><tr><th>字段</th><th>原值</th><th>新值</th><th>说明</th></tr></thead>
        <tbody>${changes}</tbody>
      </table>
    </div>
    <div class="table-wrap compact">
      <table>
        <thead><tr><th>期间</th><th>对象</th><th>归因类型</th><th>基准</th><th>场景</th><th>差异</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
};

const loadScenarioCompare = async () => {
  const scenarioIds = Array.from(el("compareScenarios").selectedOptions).map((option) => option.value);
  if (scenarioIds.length < 2) {
    el("compareStatus").textContent = "请选择至少两个场景进行对比。";
    el("compareTableHead").innerHTML = "";
    el("compareTableBody").innerHTML = "";
    return;
  }
  el("compareStatus").textContent = "正在生成对比宽表...";
  try {
    normalizeCompareDimensions();
    const payload = {
      scenario_ids: scenarioIds,
      scenarios: scenarioIds,
      row_type: "overview",
      dimensions: selectedCompareDimensions(),
      period_from: el("comparePeriodFrom").value,
      period_to: el("comparePeriodTo").value,
      diff_mode: el("compareDiffMode").value,
    };
    const data = await api("/api/wide-table/compare", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderScenarioCompare(data, payload);
  } catch (error) {
    el("compareStatus").textContent = `场景对比加载失败：${error.message}`;
  }
};

const renderScenarioCompare = (data, payload) => {
  const rows = data.rows || [];
  const periods = data.periods || data.months || [];
  const dimensions = data.dimensions || payload.dimensions || [];
  const dimensionNames = dimensions.map((dimension) => (
    data.dimension_catalog?.dimensions?.find((item) => item.id === dimension)?.label_cn || labels[dimension] || dimension
  ));
  const visibleNodes = [];
  const appendNodes = (nodes) => {
    for (const node of nodes || []) {
      visibleNodes.push(node);
      if ((node.children || []).length && state.compareExpanded.has(node.id)) appendNodes(node.children);
    }
  };
  appendNodes(data.tree || []);
  const scope = dimensionNames.length ? `下钻维度：${dimensionNames.join(" > ")}` : "总览：全部资产";
  el("compareStatus").textContent = `${payload.scenario_ids.join(" / ")} · ${scope} · ${visibleNodes.length} 行 · ${periods.length} 个月 · ${labels[payload.diff_mode] || payload.diff_mode}`;
  el("compareTableHead").innerHTML = `
    <tr>
      <th class="sticky-col sticky-1">层级</th>
      <th class="sticky-col sticky-2">分析对象</th>
      ${periods.map((period) => `<th class="month-head">${escapeHtml(period)}</th>`).join("")}
    </tr>
  `;
  el("compareTableBody").innerHTML = visibleNodes.map((node) => `
    <tr>
      <td class="sticky-col sticky-1">${escapeHtml(node.dimension_label_cn || labels[node.dimension] || node.dimension || "总览")}</td>
      <td class="wide-node sticky-col sticky-2" style="--node-depth:${Number(node.depth || 0)}">
        ${(node.children || []).length ? `<button class="tree-toggle" type="button" data-compare-tree-id="${escapeHtml(node.id)}" aria-label="展开或收起">${state.compareExpanded.has(node.id) ? "−" : "+"}</button>` : "<span class=\"tree-leaf\"></span>"}
        ${node.dimension === "asset" ? `<button class="link-button" type="button" data-policy-ref="${escapeHtml(node.value)}">${escapeHtml(node.label_cn || node.value)}</button>` : escapeHtml(node.label_cn || node.value || "全部资产")}
      </td>
      ${periods.map((period) => `<td class="amount compare-period-cell">${formatComparePeriodValue(node.months?.[period], payload)}</td>`).join("")}
    </tr>
  `).join("") || `<tr><td colspan="${periods.length + 2}">没有符合条件的数据</td></tr>`;
  el("compareTableBody").querySelectorAll("[data-compare-tree-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const id = button.dataset.compareTreeId;
      if (state.compareExpanded.has(id)) state.compareExpanded.delete(id);
      else state.compareExpanded.add(id);
      renderScenarioCompare(data, payload);
    });
  });
  el("compareTableBody").querySelectorAll("[data-policy-ref]").forEach((button) => {
    button.addEventListener("click", () => drillToPolicy(button.dataset.policyRef));
  });
};

const formatComparePeriodValue = (value, payload) => {
  if (value === "" || value === null || value === undefined) return "-";
  if (typeof value === "number" || !Number.isNaN(Number(value))) return money(value);
  if (typeof value !== "object") return escapeHtml(value);
  const scenarioLabel = (scenarioId) => state.scenarios.find((item) => item.scenario_id === scenarioId)?.scenario_name || scenarioId;
  const baselineId = Object.keys(value).find((key) => key === "BASELINE") || payload.scenario_ids[0];
  const orderedScenarioIds = [...new Set([baselineId, ...payload.scenario_ids])];
  const scenarioLines = orderedScenarioIds.map((scenarioId) => {
    const scenarioValue = value[scenarioId] ?? value.scenarios?.[scenarioId];
    if (scenarioValue === undefined) return "";
    const prefix = scenarioId === baselineId ? "基准" : scenarioLabel(scenarioId);
    return `<span class="compare-value-line">${escapeHtml(prefix)}: ${money(scenarioValue)}</span>`;
  }).filter(Boolean);
  const diffAmount = firstValue(value, ["diff_amount", "difference", "amount_diff"]);
  const diffPercent = firstValue(value, ["diff_percent", "percentage_diff", "percent_diff"]);
  const diffLines = [];
  const differenceNumber = Number(diffAmount || 0);
  const diffClass = differenceNumber > 0 ? "compare-diff-positive" : differenceNumber < 0 ? "compare-diff-negative" : "compare-diff-neutral";
  if ((payload.diff_mode === "amount" || payload.diff_mode === "both") && diffAmount !== "") {
    diffLines.push(`<strong class="${diffClass}">差异: ${differenceNumber > 0 ? "+" : ""}${money(diffAmount)}</strong>`);
  }
  if ((payload.diff_mode === "percent" || payload.diff_mode === "both") && diffPercent !== "") {
    const number = Number(diffPercent) <= 1 ? Number(diffPercent) * 100 : Number(diffPercent);
    diffLines.push(`<em class="${diffClass}">${number > 0 ? "+" : ""}${percent(number)}</em>`);
  }
  return [...scenarioLines, ...diffLines].join("") || escapeHtml(JSON.stringify(value));
};

const loadDetails = async () => {
  const params = new URLSearchParams({
    scenario_id: state.scenarioId,
    limit: "240",
  });
  if (el("detailDepartment").value) params.set("department", el("detailDepartment").value);
  if (el("detailCategory").value) params.set("asset_category", el("detailCategory").value);
  if (el("detailSource").value) params.set("asset_source_type", el("detailSource").value);
  if (el("periodFrom").value) params.set("period_from", el("periodFrom").value);
  if (el("periodTo").value) params.set("period_to", el("periodTo").value);
  const rows = await api(`/api/forecast-lines?${params.toString()}`);
  renderRows(el("detailsBody"), rows, [
    { key: "period" },
    { key: "asset_id", format: (value, row) => value || row.planned_asset_id },
    { key: "asset_source_type", format: (value) => labels[value] || value },
    { key: "department" },
    { key: "asset_category", format: (value) => categoryLabels[value] || value },
    { key: "depreciation_policy", format: policyDisplay },
    { key: "monthly_depreciation", format: money, className: "amount" },
    { key: "closing_net_value", format: money, className: "amount" },
  ]);
};

const loadKnowledgeGraph = async () => {
  const focus = "full";
  el("graphCanvas").textContent = "正在加载知识图谱...";
  try {
    state.graph = await api(`/api/knowledge-graph?scenario_id=${encodeURIComponent(state.scenarioId)}&focus=${encodeURIComponent(focus)}`);
    hydrateGraphObjectTypeFilter(state.graph.object_types || []);
    if (!state.graphSelectedType || !asArray(state.graph.object_types).some((item) => item.type_id === state.graphSelectedType)) {
      state.graphSelectedType = asArray(state.graph.object_types).find((item) => item.type_id === "FixedAsset")?.type_id
        || asArray(state.graph.object_types).find((item) => asArray(state.graph.nodes).some((node) => node.object_type === item.type_id))?.type_id
        || "";
    }
    state.graphSelectedNodeId = null;
    renderGraphExplorer();
  } catch (error) {
    el("graphCanvas").innerHTML = `<p class="empty-note">知识图谱加载失败：${escapeHtml(error.message)}</p>`;
    el("graphKpis").innerHTML = "";
    el("graphNodeDetail").textContent = "暂无节点详情";
    el("graphObjectList").innerHTML = "";
  }
};

const hydrateGraphObjectTypeFilter = (objectTypes) => {
  const select = el("graphObjectType");
  const current = state.graphSelectedType || select.value;
  select.innerHTML = `<option value="">选择业务对象类型</option>`;
  asArray(objectTypes).forEach((item) => {
    const option = document.createElement("option");
    option.value = item.type_id;
    option.textContent = item.label_cn || item.type_id;
    select.appendChild(option);
  });
  select.value = current && [...select.options].some((option) => option.value === current) ? current : "";
};

const filteredGraphNodes = () => {
  const rawNodes = asArray(state.graph?.nodes);
  const selectedType = state.graphSelectedType || el("graphObjectType")?.value || "";
  const keyword = (el("graphObjectSearch")?.value || "").trim().toLowerCase();
  return rawNodes.filter((node) => {
    if (!selectedType || node.object_type !== selectedType) return false;
    if (!keyword) return true;
    const text = [
      node.id, node.label_cn, node.subtitle_cn, node.technical_ref,
      ...Object.values(node.properties || {}),
    ].join(" ").toLowerCase();
    return text.includes(keyword);
  });
};

const renderGraphExplorer = () => {
  const nodes = filteredGraphNodes();
  renderGraphKpis(state.graph?.summary || {});
  renderGraphTypeModel();
  const selectedType = asArray(state.graph?.object_types).find((item) => item.type_id === state.graphSelectedType);
  el("graphObjectListTitle").textContent = selectedType ? `${selectedType.label_cn || selectedType.type_id}实体清单` : "业务实体清单";
  el("graphObjectCount").textContent = selectedType ? `显示 ${nodes.length}` : "请先选择类型";
  const list = el("graphObjectList");
  if (!selectedType) {
    list.innerHTML = `<p class="empty-note">请在中间图中选择一个业务对象类型。</p>`;
    el("graphNodeDetail").textContent = "先在中间选择业务对象类型，再从左侧选择一个业务实体。";
    return;
  }
  list.innerHTML = nodes.map((node) => {
    const id = node.id || node.object_id;
    return `<button type="button" class="graph-object-row ${state.graphSelectedNodeId === id ? "selected" : ""}" data-graph-node-id="${escapeHtml(id)}">
      <span class="graph-object-type">${escapeHtml(node.type_label_cn || node.object_type || "业务对象")}</span>
      <strong>${escapeHtml(node.label_cn || id)}</strong>
      <small>${escapeHtml(node.subtitle_cn || node.technical_ref || "")}</small>
    </button>`;
  }).join("") || `<p class="empty-note">没有匹配的业务对象。</p>`;
  list.querySelectorAll("[data-graph-node-id]").forEach((button) => {
    button.addEventListener("click", () => selectGraphNode(button.dataset.graphNodeId));
  });
  if (state.graphSelectedNodeId && nodes.some((node) => (node.id || node.object_id) === state.graphSelectedNodeId)) {
    return;
  }
  if (nodes[0]) selectGraphNode(nodes[0].id || nodes[0].object_id);
  else {
    state.graphSelectedNodeId = null;
    el("graphNodeDetail").textContent = "当前类型没有可展示的业务实体。";
  }
};

const selectGraphType = (typeId) => {
  if (!typeId) return;
  state.graphSelectedType = typeId;
  state.graphSelectedNodeId = null;
  el("graphObjectType").value = typeId;
  renderGraphExplorer();
};

const selectGraphNode = async (nodeId) => {
  if (!nodeId) return;
  state.graphSelectedNodeId = nodeId;
  document.querySelectorAll(".graph-object-row").forEach((item) => {
    item.classList.toggle("selected", item.dataset.graphNodeId === nodeId);
  });
  await loadGraphNodeDetail(nodeId);
};

const renderGraphTypeModel = () => {
  const types = asArray(state.graph?.object_types).map((item) => ({
    ...item,
    id: item.type_id,
    label: item.label_cn || item.type_id,
    instanceCount: asArray(state.graph?.nodes).filter((node) => node.object_type === item.type_id).length,
  }));
  const typeIds = new Set(types.map((item) => item.type_id));
  const links = asArray(state.graph?.link_types).filter((item) => typeIds.has(item.source_type) && typeIds.has(item.target_type));
  const positioned = positionGraphTypeNodes(types, links);
  const nodeById = new Map(positioned.map((node) => [node.id, node]));
  const edgeMarkup = links.map((link) => {
    const source = nodeById.get(link.source_type);
    const target = nodeById.get(link.target_type);
    if (!source || !target) return "";
    const midX = Math.round((source.x + target.x) / 2);
    const midY = Math.round((source.y + target.y) / 2);
    return `<g class="graph-type-edge ${source.id === state.graphSelectedType || target.id === state.graphSelectedType ? "connected" : ""}">
      <line x1="${source.x}" y1="${source.y}" x2="${target.x}" y2="${target.y}"></line>
      <text x="${midX}" y="${midY}">${escapeHtml(truncate(link.label_cn || link.type_id, 18))}</text>
    </g>`;
  }).join("");
  const nodeMarkup = positioned.map((node) => `<g class="graph-type-node ${node.id === state.graphSelectedType ? "selected" : ""}" data-graph-type-id="${escapeHtml(node.id)}" transform="translate(${node.x} ${node.y})">
    <rect x="-${node.width / 2}" y="-${node.height / 2}" width="${node.width}" height="${node.height}" rx="6"></rect>
    <text y="-5">${escapeHtml(truncate(node.label, 16))}</text>
    <text y="15" class="node-type">${escapeHtml(`${node.instanceCount} 个实体`)}</text>
  </g>`).join("");
  el("graphCanvas").innerHTML = `<svg viewBox="0 0 1180 760" preserveAspectRatio="xMidYMid meet" role="img" aria-label="业务对象类型和关系语义图">
    <defs><marker id="typeArrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z"></path></marker></defs>
    ${edgeMarkup}${nodeMarkup}
  </svg>`;
  el("graphSelectionHint").textContent = state.graphSelectedType
    ? `已选择“${types.find((item) => item.id === state.graphSelectedType)?.label || state.graphSelectedType}”；左侧显示该类型的业务实体。`
    : "点击类型节点，查看该类型下的真实业务实体。";
  el("graphCanvas").querySelectorAll("[data-graph-type-id]").forEach((node) => {
    node.addEventListener("click", () => selectGraphType(node.dataset.graphTypeId));
  });
};

const positionGraphTypeNodes = (types, links) => {
  const columns = [
    ["Department", "CostCenter", "ProfitCenter", "Block"],
    ["FixedAsset", "PlannedAsset", "AssetEvent", "Anomaly"],
    ["AssetCategory", "DepreciationCode", "DepreciationPolicy", "DepreciationMethod", "CalculationRule"],
    ["Scenario", "ScenarioAssumption", "MonthlyDriver", "ForecastLine"],
    ["ReversePlanningTarget", "RecommendedAction", "ReverseRecommendation"],
  ];
  const columnByType = new Map();
  columns.forEach((column, index) => column.forEach((type) => columnByType.set(type, index)));
  const grouped = new Map();
  types.forEach((type) => {
    const column = columnByType.get(type.id) ?? columns.length - 1;
    if (!grouped.has(column)) grouped.set(column, []);
    grouped.get(column).push(type);
  });
  return [...grouped.entries()].flatMap(([column, items]) => {
    const ordered = items.sort((left, right) => String(left.label).localeCompare(String(right.label), "zh-CN"));
    return ordered.map((item, index) => ({
      ...item,
      x: 118 + column * 236,
      y: Math.round(92 + index * ((590 / Math.max(ordered.length - 1, 1)))),
      width: 148,
      height: 56,
    }));
  });
};

const renderGraphKpis = (summary) => {
  const database = summary.graph_database || "图数据库未连接";
  const engine = summary.graph_query_engine || "-";
  const records = summary.forecast_record_count ?? 0;
  const summaries = summary.forecast_summary_count ?? 0;
  const executions = summary.rule_execution_count ?? 0;
  const changes = summary.scenario_change_count ?? 0;
  const attributions = summary.attribution_count ?? 0;
  el("graphDatabaseStatus").innerHTML = `<strong>${escapeHtml(database)}</strong><span>${escapeHtml(engine)}</span><span>已投影 ${escapeHtml(records)} 条逐月计算、${escapeHtml(summaries)} 条汇总、${escapeHtml(executions)} 条规则执行、${escapeHtml(changes)} 条场景变更和 ${escapeHtml(attributions)} 条归因记录</span>`;
  const items = [
    ["对象数", summary.node_count ?? summary.object_count ?? 0],
    ["关系数", summary.edge_count ?? summary.link_count ?? 0],
    ["推理关系", summary.inferred_link_count ?? summary.inferred_triple_count ?? 0],
    ["逐月记录", records],
    ["汇总记录", summaries],
    ["场景动作", summary.action_count ?? 0],
  ];
  el("graphKpis").innerHTML = items.map(([label, value]) => `
    <article>
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </article>
  `).join("");
};

const loadGraphNodeDetail = async (nodeId) => {
  if (!nodeId) {
    el("graphNodeDetail").textContent = "暂无节点详情";
    return;
  }
  try {
    const detail = await api(`/api/knowledge-graph/node?scenario_id=${encodeURIComponent(state.scenarioId)}&id=${encodeURIComponent(nodeId)}`);
    renderGraphNodeDetail(detail);
  } catch (error) {
    el("graphNodeDetail").innerHTML = `<p class="empty-note">节点详情加载失败：${escapeHtml(error.message)}</p>`;
  }
};

const renderGraphFocus = (detail) => {
  if (!detail?.node) {
    el("graphSelectionHint").textContent = "从左侧选择一个业务对象";
    el("graphCanvas").innerHTML = `<p class="empty-note">选择对象后，显示其直接业务关系。</p>`;
    return;
  }
  const node = detail.node;
  const related = asArray(detail.related_nodes);
  const visible = related.slice(0, 14);
  const width = 840;
  const height = 540;
  const center = { x: 420, y: 270 };
  const radius = Math.min(190, Math.max(118, visible.length * 14));
  const positions = visible.map((item, index) => {
    const angle = (Math.PI * 2 * index / Math.max(visible.length, 1)) - Math.PI / 2;
    return {
      item,
      x: Math.round(center.x + Math.cos(angle) * radius),
      y: Math.round(center.y + Math.sin(angle) * radius * 0.76),
    };
  });
  const edgeMarkup = positions.map(({ item, x, y }) => `
    <g class="graph-focus-edge">
      <line x1="${center.x}" y1="${center.y}" x2="${x}" y2="${y}"></line>
      <text x="${Math.round((center.x + x) / 2)}" y="${Math.round((center.y + y) / 2)}">${escapeHtml(truncate(item.edge?.label_cn || "关联", 12))}</text>
    </g>`).join("");
  const neighborMarkup = positions.map(({ item, x, y }) => {
    const relatedNode = item.node || {};
    const id = relatedNode.object_id || relatedNode.id;
    return `<g class="graph-focus-node neighbor" data-focus-node-id="${escapeHtml(id)}" transform="translate(${x} ${y})">
      <rect x="-70" y="-26" width="140" height="52" rx="6"></rect>
      <text y="-3">${escapeHtml(truncate(relatedNode.label_cn || id, 17))}</text>
      <text y="15" class="node-type">${escapeHtml(truncate(relatedNode.object_type || "业务对象", 15))}</text>
    </g>`;
  }).join("");
  el("graphSelectionHint").textContent = related.length > visible.length
    ? `显示 ${visible.length} / ${related.length} 条直接关系；右侧可查看全部关系。`
    : `共 ${related.length} 条直接业务关系。`;
  el("graphCanvas").innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="所选业务对象的关系图">
    ${edgeMarkup}
    <g class="graph-focus-node center" transform="translate(${center.x} ${center.y})">
      <rect x="-94" y="-34" width="188" height="68" rx="7"></rect>
      <text y="-5">${escapeHtml(truncate(node.label_cn || node.object_id, 22))}</text>
      <text y="17" class="node-type">${escapeHtml(node.object_type || "业务对象")}</text>
    </g>
    ${neighborMarkup}
  </svg>`;
  el("graphCanvas").querySelectorAll("[data-focus-node-id]").forEach((element) => {
    element.addEventListener("click", () => selectGraphNode(element.dataset.focusNodeId));
  });
};

const renderGraphNodeDetail = (detail) => {
  const node = detail?.node || detail;
  if (!node) {
    el("graphNodeDetail").textContent = "暂无节点详情";
    return;
  }
  const properties = node.properties || {};
  const metrics = node.metrics || detail?.forecast_summary || {};
  const related = asArray(detail.related_nodes);
  const actions = asArray(detail.actions);
  const functions = asArray(detail.functions);
  const risks = asArray(detail.risks);
  const nodeId = node.id || node.object_id;
  el("graphNodeDetail").innerHTML = `
    <section class="graph-detail-title">
      <span>${escapeHtml(node.object_type || "业务对象")}</span>
      <strong>${escapeHtml(node.label_cn || node.label || node.name || node.id)}</strong>
      <p>${escapeHtml(node.subtitle_cn || firstValue(node, ["description_cn", "description", "summary"], ""))}</p>
    </section>
    <section class="graph-detail-section">
      <h4>对象标识</h4>
      <div class="graph-meta-list"><span><b>对象 ID</b>${escapeHtml(nodeId || "-")}</span><span><b>来源</b>${escapeHtml(node.source_system || "-")}</span><span><b>业务引用</b>${escapeHtml(node.technical_ref || "-")}</span></div>
    </section>
    <section class="graph-detail-section"><h4>全部业务属性</h4><div class="mini-list">${renderGraphKeyValues(properties)}</div></section>
    <section class="graph-detail-section"><h4>预测与计算指标</h4><div class="mini-list">${renderGraphKeyValues(metrics)}</div></section>
    <section class="graph-detail-section"><h4>全部直接关系 (${related.length})</h4>
    <div class="graph-relation-list">${related.map((item) => {
      const edge = item.edge || {};
      const relatedNode = item.node || {};
      return `<button type="button" class="graph-related" data-node-id="${escapeHtml(relatedNode.object_id || relatedNode.id)}"><b>${escapeHtml(edge.label_cn || "关联")}</b><span>${escapeHtml(relatedNode.label_cn || relatedNode.id)}</span><small>${escapeHtml(edge.business_text || "")}</small></button>`;
    }).join("") || `<span>暂无关联对象</span>`}</div></section>
    ${(actions.length || functions.length || risks.length) ? `<details class="graph-advanced"><summary>计算与治理信息</summary>
      ${actions.length ? `<div class="chip-list">${actions.map((action) => `<button type="button" class="graph-action" data-action="${escapeHtml(action.type_id)}" data-node-id="${escapeHtml(nodeId)}">${escapeHtml(action.label_cn)}</button>`).join("")}</div>` : ""}
      ${functions.length ? `<div class="function-list">${functions.map((fn) => `<article><strong>${escapeHtml(fn.label_cn)}</strong><span>${escapeHtml(fn.description_cn)}</span></article>`).join("")}</div>` : ""}
      ${risks.length ? `<div class="risk-list">${risks.map((risk) => `<article class="risk-item"><strong>${escapeHtml(risk.severity_label_cn || risk.severity || "提示")}</strong><p>${escapeHtml(risk.message_cn || risk.message || "")}</p></article>`).join("")}</div>` : ""}
    </details>` : ""}
  `;
  el("graphNodeDetail").querySelectorAll(".graph-related").forEach((button) => {
    button.addEventListener("click", () => {
      const relatedNode = asArray(state.graph?.nodes).find((item) => String(item.id || item.object_id) === button.dataset.nodeId);
      if (relatedNode?.object_type) {
        state.graphSelectedType = relatedNode.object_type;
        state.graphSelectedNodeId = button.dataset.nodeId;
        el("graphObjectType").value = relatedNode.object_type;
        renderGraphExplorer();
        loadGraphNodeDetail(button.dataset.nodeId);
      }
    });
  });
  el("graphNodeDetail").querySelectorAll(".graph-action").forEach((button) => {
    button.addEventListener("click", () => prefillWhatIfFromGraph(button.dataset.action, node));
  });
};

const renderGraphKeyValues = (object) => {
  const entries = Object.entries(object || {}).filter(([, value]) => value !== null && value !== undefined && value !== "");
  if (!entries.length) return `<span>暂无数据</span>`;
  return entries.map(([key, value]) => `
    <span><b>${escapeHtml(labels[key] || categoryLabels[key] || key)}</b>${escapeHtml(displayValue(value))}</span>
  `).join("");
};

const renderGraphLineage = (lineage) => {
  const triples = asArray(lineage?.technical_triples_preview);
  const inferred = triples.filter((item) => item.inferred).length;
  const items = [
    ["源数据", lineage?.source_data || "data/customer_snapshot/ 受控客户 Excel"],
    ["业务结果库", lineage?.object_store || "-"],
    ["图数据库", lineage?.graph_store || "-"],
    ["技术 triples 预览", `${triples.length} 条，其中推理生成 ${inferred} 条`],
  ];
  el("graphLineage").innerHTML = items.map(([label, value]) => `
    <article>
      <strong>${escapeHtml(label)}</strong>
      <span>${escapeHtml(value)}</span>
    </article>
  `).join("");
};

const markGraphSelection = (nodeId) => {
  document.querySelectorAll(".graph-node").forEach((item) => {
    item.classList.toggle("selected", item.dataset.nodeId === String(nodeId));
  });
};

const prefillWhatIfFromGraph = async (actionType, node) => {
  const properties = node.properties || {};
  await showView("whatif");
  await hydrateScenarioAssetOptions();
  if (properties.asset_ref || node.technical_ref) {
    el("scenarioAsset").value = properties.asset_ref || node.technical_ref;
  }
  await refreshScenarioAssetComposer();
  if (node.object_type === "MonthlyDriver" && properties.period) {
    const period = el("scenarioPeriod");
    if (period) period.value = properties.period;
  }
};

const loadPolicyProof = async (assetRef = el("policyAssetRef").value || state.assetCards[0]?.assetRef || "") => {
  if (!assetRef) {
    el("policyProof").textContent = "请先从资产卡片或宽表选择资产。";
    return;
  }
  const data = await api(`/api/policy-narrative?scenario_id=${encodeURIComponent(state.scenarioId)}&asset_ref=${encodeURIComponent(assetRef)}`);
  renderPolicyNarrative(data);
  renderDiagnostics(data.technical_details || data.diagnostics || {});
  const triples = await softApi("/api/technical/graph-triples?limit=160", []);
  renderRows(el("tripleBody"), triples, [
    { key: "subject" },
    { key: "predicate", format: (value) => predicateLabel(value) },
    { key: "object" },
    { key: "inferred", format: (value, row) => tripleNarrative(row) },
  ]);
};

const renderPolicyNarrative = (data) => {
  const policy = data.applicable_policy || data.policy || {};
  const basisItems = asArray(data.basis_items);
  const matchPath = asArray(data.match_path || data.category_chain);
  const impact = data.calculation_impact || {};
  el("policyProof").innerHTML = `
    <p class="narrative">${escapeHtml(data.narrative_cn || data.narrative || "没有找到政策依据")}</p>
    <div class="proof-grid">
      <article><span>资产</span><strong>${escapeHtml(data.asset_ref || el("policyAssetRef").value)}</strong></article>
      <article><span>适用政策</span><strong>${escapeHtml(firstValue(policy, ["policy_label_cn", "policy_name_cn", "policy_name", "name"], "-"))}</strong></article>
      <article><span>使用年限</span><strong>${escapeHtml(firstValue(policy, ["useful_life_months", "life_months"], "-"))} 月</strong></article>
      <article><span>残值率</span><strong>${escapeHtml(firstValue(policy, ["residual_rate_label_cn", "residual_rate", "salvage_rate"], "-"))}</strong></article>
    </div>
    <section class="basis-section">
      <h3>依据条目</h3>
      <div class="basis-list">${renderBasisItems(basisItems)}</div>
    </section>
    <section class="basis-section">
      <h3>匹配路径</h3>
      <div class="chain">${renderMatchPath(matchPath)}</div>
    </section>
    <section class="basis-section">
      <h3>计算影响</h3>
      <div class="impact-grid">${renderCalculationImpact(impact)}</div>
    </section>
  `;
};

const renderBasisItems = (items) => {
  if (!items.length) return `<p class="empty-note">暂无依据条目</p>`;
  return items.map((item) => `
    <article class="basis-item">
      <strong>${escapeHtml(firstValue(item, ["label_cn", "title_cn", "title", "name"], "依据"))}</strong>
      <p>${escapeHtml(displayValue(firstValue(item, ["value_label_cn", "content_cn", "description_cn", "evidence_cn", "value", "content", "description", "evidence"], item)))}</p>
      <span>${escapeHtml(displayValue(firstValue(item, ["source_cn", "source", "basis"], "")))}</span>
    </article>
  `).join("");
};

const renderMatchPath = (items) => {
  if (!items.length) return `<span>暂无匹配路径</span>`;
  return items.map((item) => {
    const text = typeof item === "string"
      ? (categoryLabels[item] || item)
      : firstValue(item, ["category_label_cn", "label_cn", "label", "name", "id"], "");
    return `<span>${escapeHtml(text)}</span>`;
  }).join("<b>→</b>");
};

const renderCalculationImpact = (impact) => {
  const rows = Array.isArray(impact)
    ? impact
    : Object.entries(impact || {})
      .filter(([key]) => key !== "calculation_rule_id")
      .map(([key, value]) => ({ label: labels[key] || key, value }));
  if (!rows.length) return `<p class="empty-note">暂无计算影响</p>`;
  return rows.map((item) => `
    <article>
      <span>${escapeHtml(firstValue(item, ["label_cn", "label", "name"], ""))}</span>
      <strong>${escapeHtml(displayValue(firstValue(item, ["value_cn", "value", "amount"], "")))}</strong>
    </article>
  `).join("");
};

const renderDiagnostics = (diagnostics) => {
  const rows = Object.entries(diagnostics || {}).map(([layer, value]) => ({
    layer,
    description: diagnosticDescription(layer, value),
    value: typeof value === "string" ? value : JSON.stringify(value),
  }));
  renderRows(el("diagnosticBody"), rows, [
    { key: "layer" },
    { key: "description" },
    { key: "value" },
  ]);
};

const diagnosticDescription = (layer, value) => {
  if (layer === "源数据") return "从样例资产/计划资产台账中读取对象的部门、类别和折旧码。";
  if (layer === "图谱推理") return "沿资产类别层级向上查找可继承的预算折旧政策。";
  if (layer === "政策匹配") return "根据公司、预算视角和资产类别匹配最终适用政策。";
  if (layer === "计算规则") return `折旧引擎使用的规则标识：${value || "-"}`;
  if (layer === "落库结果") return "预测明细、异常和汇总已经写入 SQLite 业务库。";
  return "内部数据链路信息。";
};

const predicateLabel = (value) => ({
  is_a: "属于",
  has_policy: "适用政策",
  policy_method: "折旧方法",
  useful_life_months: "使用年限",
  residual_rate: "残值率",
  start_rule: "开始计提规则",
  "rdf:type": "对象类型",
  "rdfs:label": "业务名称",
  "rdfs:subClassOf": "属于",
  appliesToCategory: "适用资产类别",
  appliesToCompany: "适用公司",
  appliesToPerspective: "适用口径",
  allowedForCategory: "可用于资产类别",
  mapsToPolicy: "映射政策",
  usefulLifeMonths: "使用年限",
  residualRate: "残值率",
  startRule: "开始计提规则",
  method: "折旧方法",
}[value] || value);

const tripleNarrative = (row) => {
  const subject = graphDisplay(row.subject);
  const object = graphDisplay(row.object);
  return `${subject} ${predicateLabel(row.predicate)} ${object}${row.inferred ? "（推理得到）" : ""}`;
};

const graphDisplay = (value) => {
  const text = String(value || "");
  const local = text.includes(":") ? text.split(":").slice(1).join(":") : text;
  if (text.startsWith("category:")) return categoryLabels[local] || local;
  if (text.startsWith("policy:")) return policyLabels[local] || local;
  return categoryLabels[text] || policyLabels[text] || text;
};

const semanticObjectDisplay = (value) => {
  const text = String(value || "");
  if (text.startsWith("P_")) return policyDisplay(text);
  if (text.startsWith("CODE_")) return text.replace("CODE_MACHINE_10Y", "机器设备 10 年折旧码")
    .replace("CODE_ELECTRONIC_3Y", "电子设备 3 年折旧码")
    .replace("CODE_BUILDING_20Y", "房屋建筑物 20 年折旧码");
  return text;
};

const drillToPolicy = async (assetRef) => {
  await openAssetDetail(assetRef);
};

const showView = async (view) => {
  state.view = view;
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === view);
  });
  document.querySelectorAll(".view").forEach((item) => {
    item.classList.toggle("active", item.id === view);
  });
  const titles = {
    overview: "预算总览",
    assets: "资产工作台",
    graph: "知识图谱",
    wide: "折旧宽表",
    whatif: "What-if 测算",
    reverse: "反向推演",
    compare: "场景对比",
  };
  el("pageTitle").textContent = titles[view];
  if (view === "wide") {
    await loadWideTable();
    await loadWideQaStatus();
  }
  if (view === "whatif") {
    await loadRuleCatalog();
    renderScenarioAssumptions();
    await loadWhatIfScenarioList();
    showWhatIfList();
  }
  if (view === "assets") await loadAssetWorkbench();
  if (view === "graph") await loadKnowledgeGraph();
  if (view === "reverse") state.reverseCatalog = await softApi("/api/reverse-planning/catalog", null);
  if (view === "compare") await loadScenarioCompare();
};

const refresh = async () => {
  await loadSnapshotStatus();
  await loadWideDimensionCatalog();
  await loadScenarios();
  await loadDashboard();
  await showView(state.view);
};

document.querySelectorAll(".nav-item").forEach((item) => {
  item.addEventListener("click", () => showView(item.dataset.view));
});
el("scenarioSelect").addEventListener("change", async () => {
  state.scenarioId = el("scenarioSelect").value;
  state.wideQuestionConversationId = null;
  state.reversePlanningConversationId = null;
  await loadDashboard();
  await showView(state.view);
});
el("refreshBtn").addEventListener("click", refresh);
el("wideSearchBtn").addEventListener("click", loadWideTable);
["wideDimension1", "wideDimension2", "wideDimension3"].forEach((id) => {
  el(id).addEventListener("change", () => {
    normalizeWideDimensions();
    state.wideExpanded = new Set();
    state.wideQuestionConversationId = null;
    loadWideTable();
  });
});
el("wideQuestionBtn").addEventListener("click", askWideQuestion);
el("wideQuestionInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter") askWideQuestion();
});
el("reverseQuestionBtn").addEventListener("click", askReversePlanning);
el("reverseQuestionInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter") askReversePlanning();
});
el("graphObjectSearch").addEventListener("input", renderGraphExplorer);
el("graphObjectType").addEventListener("change", () => {
  selectGraphType(el("graphObjectType").value);
});
el("scenarioForm").addEventListener("submit", submitScenario);
el("newScenarioBtn").addEventListener("click", async () => {
  resetScenarioEditor();
  showWhatIfEditor();
  await refreshScenarioAssetComposer();
});
el("backToScenarioListBtn").addEventListener("click", async () => {
  resetScenarioEditor();
  await loadWhatIfScenarioList();
  showWhatIfList();
});
el("addScenarioAssumptionBtn").addEventListener("click", addScenarioAssumption);
el("scenarioAsset").addEventListener("change", refreshScenarioAssetComposer);
el("ruleTemplate").addEventListener("change", renderScenarioDynamicFields);
el("scenarioBase").addEventListener("change", refreshScenarioAssetComposer);
el("clearScenarioAssumptionsBtn").addEventListener("click", () => {
  state.scenarioDraftAssumptions = [];
  renderScenarioAssumptions();
});
el("compareBtn").addEventListener("click", loadScenarioCompare);
el("compareDiffMode").addEventListener("change", loadScenarioCompare);
["compareDimension1", "compareDimension2"].forEach((id) => {
  el(id).addEventListener("change", () => {
    normalizeCompareDimensions();
    state.compareExpanded = new Set();
    loadScenarioCompare();
  });
});
el("openAssetsBtn").addEventListener("click", () => showView("assets"));
el("assetSearchBtn").addEventListener("click", loadAssetWorkbench);
el("assetSearchInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadAssetWorkbench();
});
["assetDepartmentFilter", "assetCategoryFilter", "assetSourceFilter"].forEach((id) => {
  el(id).addEventListener("change", loadAssetWorkbench);
});
el("assetDrawerCloseBtn").addEventListener("click", closeAssetDetail);
el("assetDrawerBackdrop").addEventListener("click", closeAssetDetail);

refresh().catch((error) => {
  document.body.innerHTML = `<main class="fatal">Demo load failed: ${escapeHtml(error.message)}</main>`;
});
