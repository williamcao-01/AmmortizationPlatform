const state = {
  view: "overview",
  scenarioId: "BASELINE",
  dashboard: null,
  scenarios: [],
  assetCards: [],
  assetWorkbenchCards: [],
  graph: null,
  ruleCatalog: null,
  wideCatalog: null,
  wideExpanded: new Set(),
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
  el("scenarioPeriod").value = firstForecast;
  el("scenarioInServiceDate").value = firstForecast ? `${firstForecast}-01` : "";
  el("scenarioCompany").value = "";
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
  department: firstValue(card, ["department", "department_name", "cost_center"], "-"),
  category: firstValue(card, ["asset_category", "category", "category_code"], "-"),
  source: firstValue(card, ["asset_source_type", "source_type", "source"], "-"),
  status: firstValue(card, ["status_cn", "status", "asset_status"], "-"),
  isBlocking: Boolean(firstValue(card, ["is_blocking", "blocking"], false)),
  riskCount: Number(firstValue(card, ["risk_count", "anomaly_count"], 0)),
  policy: firstValue(card, ["depreciation_policy_label_cn", "policy_label_cn", "policy_name", "applicable_policy", "depreciation_policy", "policy_id"], "-"),
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
  const tools = asArray(result.harness?.tool_trace || result.tool_trace).map((item) => `<li><strong>${escapeHtml(item.label_cn || item.tool_name || "-")}</strong><span>${escapeHtml(item.tool_name || "-")}${item.result_shape ? ` · ${escapeHtml(Object.entries(item.result_shape).map(([key, value]) => `${key}: ${typeof value === "object" ? JSON.stringify(value) : value}`).join("，"))}` : ""}</span></li>`).join("");
  const callCards = [["阶段 1 · 目标理解", calls.question_understanding], ["阶段 2 · 业务表述", calls.answer_composition]].map(([label, call]) => {
    const status = call?.used_llm ? "已调用" : "未调用/已降级";
    const duration = call?.latency_ms ? `${(Number(call.latency_ms) / 1000).toFixed(2)} 秒` : (call?.fallback_reason || "无调用记录");
    return `<div class="model-call-card ${call?.used_llm ? "verified" : "fallback"}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(status)} · ${escapeHtml(call?.provider || "-")}</strong><small>${escapeHtml(call?.model || "模板结论")} · ${escapeHtml(duration)}</small></div>`;
  }).join("");
  const executionSummary = result.harness?.evidence_summary || {};
  el("reversePlanningResult").innerHTML = `
    <section class="skill-status ${result.qa_skill?.used_llm ? "llm" : "fallback"}"><div><span>反向推演 Agent</span><strong>${escapeHtml(result.qa_skill?.skill_name || "reverse_depreciation_planning")}</strong></div><div><span>本次执行状态</span><strong>${escapeHtml(mode)}</strong></div><p>审计编号：${escapeHtml(result.audit_id || "-")}。本次执行 ${escapeHtml(executionSummary.simulation_count ?? 0)} 次临时规则试算；没有创建或保存 What-if 场景。</p></section>
    <section><h3>问题理解</h3><div class="analysis-grid"><div><span>问题类型</span><strong>${escapeHtml(analysis.intent_label_cn || "-")}</strong></div><div><span>目标范围</span><strong>${escapeHtml(analysis.scope_value || "-")}</strong></div><div><span>目标月份</span><strong>${escapeHtml(analysis.target_period || "-")}</strong></div><div><span>目标方向</span><strong>${escapeHtml(analysis.direction || "-")}</strong></div><div><span>置信度</span><strong>${escapeHtml(plan.confidence || analysis.confidence || "-")}</strong></div></div><div class="model-call-grid">${callCards}</div></section>
    <section><h3>目标与规则试算</h3><div class="reverse-target-summary"><div><span>基准折旧</span><strong>${money(result.baseline_amount)}</strong></div><div><span>目标折旧</span><strong>${money(result.target_amount)}</strong></div><div><span>需要变化</span><strong>${money(result.required_delta)}</strong></div><div><span>场景写入</span><strong>未写入</strong></div></div><ul class="tool-trace">${tools}</ul></section>
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
      <span>${escapeHtml(item.tool_name || "-")}${item.result_shape ? ` · ${escapeHtml(Object.entries(item.result_shape).map(([key, value]) => `${key}: ${typeof value === "object" ? JSON.stringify(value) : value}`).join("，"))}` : ""}</span>
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
  const select = el("ruleTemplate");
  if (!select) return;
  select.innerHTML = "";
  for (const method of state.ruleCatalog.methods || []) {
    for (const template of method.templates || []) {
      const option = document.createElement("option");
      option.value = template.id;
      option.textContent = `${method.label_cn} · ${template.label_cn}`;
      option.title = template.description_cn;
      select.appendChild(option);
    }
  }
};

const buildScenarioAssumption = () => {
  const templateId = el("ruleTemplate").value;
  const assetId = el("scenarioAsset").value;
  const assumption = {
    draft_id: `draft-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    template_id: templateId,
    target_id: assetId,
    asset_id: assetId,
    reference_asset_id: assetId,
    block_id: el("scenarioBlock").value.trim(),
    company: el("scenarioCompany").value.trim(),
    period: el("scenarioPeriod").value.trim(),
    effective_date: `${el("scenarioPeriod").value.trim()}-01`,
    amount: el("scenarioAmount").value || "0",
    production: el("scenarioProduction").value || "0",
    reserves: el("scenarioReserves").value || "0",
    workload: el("scenarioWorkload").value || "0",
    unit_fee: el("scenarioUnitFee").value || "0",
    start_rule: el("scenarioStartRule").value,
    asset_name: el("scenarioAssetName").value.trim(),
    asset_category: el("scenarioAssetCategory").value.trim(),
    depreciation_code: el("scenarioDepreciationCode").value.trim(),
    in_service_date: el("scenarioInServiceDate").value,
  };
  if (templateId === "production_driver" && !assumption.block_id) {
    throw new Error("产量法场景需要填写所属区块。");
  }
  if (["straight_impairment", "straight_accelerated", "straight_start_rule"].includes(templateId) && !assetId) {
    throw new Error("该规则场景需要选择目标资产。");
  }
  if (templateId === "straight_new_asset" && (!assumption.amount || !assumption.in_service_date)) {
    throw new Error("新增资产场景需要填写金额和资本化日期。");
  }
  return assumption;
};

const scenarioAssumptionLabel = (assumption) => {
  const template = (state.ruleCatalog?.methods || [])
    .flatMap((method) => method.templates || [])
    .find((item) => item.id === assumption.template_id);
  const target = assumption.block_id || assumption.asset_id || assumption.company || "待指定对象";
  return `${template?.label_cn || assumption.template_id} · ${target}`;
};

const renderScenarioAssumptions = () => {
  const container = el("scenarioAssumptionList");
  if (!state.scenarioDraftAssumptions.length) {
    container.innerHTML = `<p class="empty-note">尚未加入假设。填写上方规则输入后点击“加入场景假设”。</p>`;
    return;
  }
  container.innerHTML = state.scenarioDraftAssumptions.map((assumption, index) => `
    <article class="scenario-assumption-item">
      <div><strong>${escapeHtml(`${index + 1}. ${scenarioAssumptionLabel(assumption)}`)}</strong><span>${escapeHtml(assumption.period || assumption.effective_date || "预测期内生效")}</span></div>
      <button type="button" data-draft-id="${escapeHtml(assumption.draft_id)}" title="移除该假设">移除</button>
    </article>
  `).join("");
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
  el("whatIfResult").textContent = "正在生成新场景并重算...";
  const result = await api("/api/scenarios", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  state.scenarioId = result.scenario.scenario_id;
  state.scenarioDraftAssumptions = [];
  renderScenarioAssumptions();
  await loadScenarios();
  await loadDashboard();
  renderWhatIfResult(result);
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
    const payload = {
      scenario_ids: scenarioIds,
      scenarios: scenarioIds,
      row_type: el("compareRowType").value,
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
  const fixedColumns = data.fixed_columns || data.fixedColumns || inferCompareFixedColumns(rows);
  el("compareStatus").textContent = `${payload.scenario_ids.join(" / ")} · ${labels[payload.row_type] || payload.row_type} · ${rows.length} 行 · ${periods.length || (data.columns || []).length} 列 · ${labels[payload.diff_mode] || payload.diff_mode}`;
  if (data.columns && !periods.length) {
    renderGenericCompareTable(data.columns, rows);
    return;
  }
  el("compareTableHead").innerHTML = `
    <tr>
      ${fixedColumns.map((column, index) => `<th class="${index < 2 ? `sticky-col sticky-${index + 1}` : ""}">${escapeHtml(labels[column] || column)}</th>`).join("")}
      ${periods.map((period) => `<th class="month-head">${escapeHtml(period)}</th>`).join("")}
    </tr>
  `;
  el("compareTableBody").innerHTML = rows.map((row) => `
    <tr>
      ${fixedColumns.map((column, index) => compareFixedCell(row, column, index)).join("")}
      ${periods.map((period) => `<td class="amount compare-period-cell">${formatComparePeriodValue(getComparePeriodValue(row, period), payload)}</td>`).join("")}
    </tr>
  `).join("") || `<tr><td colspan="${fixedColumns.length + periods.length}">没有符合条件的数据</td></tr>`;
  el("compareTableBody").querySelectorAll("[data-policy-ref]").forEach((button) => {
    button.addEventListener("click", () => drillToPolicy(button.dataset.policyRef));
  });
};

const renderGenericCompareTable = (columns, rows) => {
  el("compareTableHead").innerHTML = `<tr>${columns.map((column) => `<th>${escapeHtml(labels[column] || column)}</th>`).join("")}</tr>`;
  el("compareTableBody").innerHTML = rows.map((row) => `
    <tr>${columns.map((column) => `<td>${escapeHtml(row[column] ?? "")}</td>`).join("")}</tr>
  `).join("") || `<tr><td colspan="${columns.length}">没有符合条件的数据</td></tr>`;
};

const inferCompareFixedColumns = (rows) => {
  const row = rows[0] || {};
  const blocked = new Set(["months", "values", "periods", "scenario_values", "annual_total"]);
  const columns = Object.keys(row).filter((key) => !blocked.has(key) && typeof row[key] !== "object");
  return columns.length ? columns.slice(0, 4) : ["asset_ref"];
};

const compareFixedCell = (row, column, index) => {
  const sticky = index < 2 ? ` sticky-col sticky-${index + 1}` : "";
  if (column === "asset_ref") {
    const ref = row.asset_ref || row.asset_id || row.id || "";
    return `<td class="key-cell${sticky}"><button class="link-button" type="button" data-policy-ref="${escapeHtml(ref)}">${escapeHtml(ref)}</button></td>`;
  }
  if (column === "asset_source_type") {
    return `<td class="${sticky}">${escapeHtml(labels[row[column]] || row[column] || "")}</td>`;
  }
  if (column === "asset_category") {
    return `<td class="${sticky}">${escapeHtml(categoryLabels[row[column]] || row[column] || "")}</td>`;
  }
  return `<td class="${sticky}">${escapeHtml(row[column] ?? "")}</td>`;
};

const getComparePeriodValue = (row, period) => {
  if (row.months && row.months[period] !== undefined) return row.months[period];
  if (row.values && row.values[period] !== undefined) return row.values[period];
  if (row.periods && row.periods[period] !== undefined) return row.periods[period];
  if (row.scenario_values && row.scenario_values[period] !== undefined) return row.scenario_values[period];
  return "";
};

const formatComparePeriodValue = (value, payload) => {
  if (value === "" || value === null || value === undefined) return "-";
  if (typeof value === "number" || !Number.isNaN(Number(value))) return money(value);
  if (typeof value !== "object") return escapeHtml(value);
  const scenarioLines = payload.scenario_ids.map((scenarioId) => {
    const scenarioValue = value[scenarioId] ?? value.scenarios?.[scenarioId];
    return scenarioValue === undefined ? "" : `<span>${escapeHtml(scenarioId)}: ${money(scenarioValue)}</span>`;
  }).filter(Boolean);
  const diffAmount = firstValue(value, ["diff_amount", "difference", "amount_diff"]);
  const diffPercent = firstValue(value, ["diff_percent", "percentage_diff", "percent_diff"]);
  const diffLines = [];
  if ((payload.diff_mode === "amount" || payload.diff_mode === "both") && diffAmount !== "") {
    diffLines.push(`<strong>${money(diffAmount)}</strong>`);
  }
  if ((payload.diff_mode === "percent" || payload.diff_mode === "both") && diffPercent !== "") {
    const number = Number(diffPercent) <= 1 ? Number(diffPercent) * 100 : Number(diffPercent);
    diffLines.push(`<em>${percent(number)}</em>`);
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
  const focus = el("graphFocus")?.value || "business";
  el("graphCanvas").textContent = "正在加载知识图谱...";
  try {
    state.graph = await api(`/api/knowledge-graph?scenario_id=${encodeURIComponent(state.scenarioId)}&focus=${encodeURIComponent(focus)}`);
    hydrateGraphTypeFilter(state.graph.object_types || []);
    renderKnowledgeGraph(state.graph);
  } catch (error) {
    el("graphCanvas").innerHTML = `<p class="empty-note">知识图谱加载失败：${escapeHtml(error.message)}</p>`;
    el("graphKpis").innerHTML = "";
    el("graphNodeDetail").textContent = "暂无节点详情";
    el("graphLineage").innerHTML = "";
  }
};

const hydrateGraphTypeFilter = (objectTypes) => {
  const select = el("graphTypeFilter");
  if (!select) return;
  const current = select.value;
  select.innerHTML = `<option value="">全部对象</option>`;
  asArray(objectTypes).forEach((item) => {
    const option = document.createElement("option");
    option.value = item.type_id;
    option.textContent = item.label_cn || item.type_id;
    select.appendChild(option);
  });
  select.value = current && [...select.options].some((option) => option.value === current) ? current : "";
};

const renderKnowledgeGraph = (data) => {
  renderGraphKpis(data.summary || {});
  const selectedType = el("graphTypeFilter")?.value || "";
  const rawNodes = asArray(data.nodes || data.graph?.nodes);
  const nodes = selectedType
    ? rawNodes.filter((node) => (node.object_type || node.type) === selectedType)
    : rawNodes;
  const nodeIds = new Set(nodes.map((node) => String(node.id || node.object_id)));
  const edges = asArray(data.edges || data.graph?.edges)
    .filter((edge) => nodeIds.has(String(edge.source || edge.from)) && nodeIds.has(String(edge.target || edge.to)));
  const positioned = positionGraphNodes(nodes);
  const nodeById = new Map(positioned.map((node) => [String(node.id), node]));
  const edgeMarkup = edges.map((edge) => {
    const source = nodeById.get(String(edge.source || edge.from));
    const target = nodeById.get(String(edge.target || edge.to));
    if (!source || !target) return "";
    const midX = (source.x + target.x) / 2;
    const midY = (source.y + target.y) / 2;
    return `
      <g class="graph-edge" data-edge-id="${escapeHtml(edge.id || "")}">
        <line x1="${source.x}" y1="${source.y}" x2="${target.x}" y2="${target.y}"></line>
        <text x="${midX}" y="${midY}">${escapeHtml(truncate(edge.label_cn || edge.predicate_label_cn || edge.label || edge.relation || edge.predicate || "", 16))}</text>
      </g>
    `;
  }).join("");
  const nodeMarkup = positioned.map((node) => `
    <g class="graph-node" data-node-id="${escapeHtml(node.id)}" transform="translate(${node.x} ${node.y})">
      <circle r="${node.r}" class="node-${escapeHtml(node.type || "default")}"></circle>
      <text y="-3">${escapeHtml(truncate(node.label || node.name || node.id, 14))}</text>
      <text y="14" class="node-type">${escapeHtml(truncate(node.type_label_cn || node.type_cn || node.type || "节点", 12))}</text>
    </g>
  `).join("");
  el("graphCanvas").innerHTML = `
    <svg viewBox="0 0 1180 760" preserveAspectRatio="xMidYMid meet">
      <defs>
        <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <path d="M0,0 L8,4 L0,8 Z"></path>
        </marker>
      </defs>
      ${edgeMarkup}
      ${nodeMarkup}
    </svg>
  `;
  el("graphCanvas").querySelectorAll(".graph-node").forEach((nodeEl) => {
    nodeEl.addEventListener("click", () => {
      const node = nodeById.get(nodeEl.dataset.nodeId);
      markGraphSelection(node?.id);
      loadGraphNodeDetail(node?.id);
    });
  });
  el("graphCanvas").querySelectorAll(".graph-edge").forEach((edgeEl) => {
    edgeEl.addEventListener("click", () => {
      document.querySelectorAll(".graph-edge").forEach((item) => item.classList.toggle("selected", item === edgeEl));
    });
  });
  renderGraphLineage(data.lineage || {});
  if (positioned[0]) {
    markGraphSelection(positioned[0].id);
    loadGraphNodeDetail(positioned[0].id);
  } else {
    renderGraphNodeDetail(null);
  }
};

const positionGraphNodes = (nodes) => {
  const width = 1180;
  const height = 760;
  const padX = 72;
  const padY = 70;
  const normalized = nodes.map((node) => ({
    ...node,
    id: node.id || node.object_id || node.node_id || node.name,
    label: node.label_cn || node.label || node.name || node.id,
    type: node.object_type || node.type || node.node_type || "default",
  }));
  const preferred = [
    "Scenario",
    "Department",
    "CostCenter",
    "ProfitCenter",
    "FixedAsset",
    "PlannedAsset",
    "AssetEvent",
    "AssetCategory",
    "DepreciationCode",
    "DepreciationPolicy",
    "ForecastLine",
    "Anomaly",
  ];
  const groups = new Map();
  normalized.forEach((node) => {
    if (!groups.has(node.type)) groups.set(node.type, []);
    groups.get(node.type).push(node);
  });
  const orderedTypes = [
    ...preferred.filter((type) => groups.has(type)),
    ...[...groups.keys()].filter((type) => !preferred.includes(type)).sort(),
  ];
  const rowGap = (height - padY * 2) / Math.max(orderedTypes.length - 1, 1);
  return orderedTypes.flatMap((type, rowIndex) => {
    const row = groups.get(type).sort((a, b) => String(a.id).localeCompare(String(b.id), "zh-CN"));
    const colGap = (width - padX * 2) / Math.max(row.length - 1, 1);
    return row.map((node, colIndex) => ({
      ...node,
      x: Math.round(row.length === 1 ? width / 2 : padX + colIndex * colGap),
      y: Math.round(padY + rowIndex * rowGap),
      r: node.type === "DepreciationPolicy" || node.type === "Anomaly" ? 28 : 24,
    }));
  });
};

const renderGraphKpis = (summary) => {
  const items = [
    ["对象数", summary.node_count ?? summary.object_count ?? 0],
    ["关系数", summary.edge_count ?? summary.link_count ?? 0],
    ["推理关系", summary.inferred_link_count ?? summary.inferred_triple_count ?? 0],
    ["异常数", summary.risk_count ?? 0],
    ["动作数", summary.action_count ?? 0],
    ["函数数", summary.function_count ?? 0],
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
    <strong>${escapeHtml(node.label_cn || node.label || node.name || node.id)}</strong>
    <p>${escapeHtml(node.subtitle_cn || firstValue(node, ["description_cn", "description", "summary"], ""))}</p>
    <dl>
      <dt>类型</dt><dd>${escapeHtml(node.type_label_cn || node.type_cn || node.object_type || node.type || "-")}</dd>
      <dt>标识</dt><dd>${escapeHtml(nodeId || "-")}</dd>
      <dt>来源</dt><dd>${escapeHtml(node.source_system || "-")}</dd>
    </dl>
    <h4>业务属性</h4>
    <div class="mini-list">${renderGraphKeyValues(properties)}</div>
    <h4>预测摘要</h4>
    <div class="mini-list">${renderGraphKeyValues(metrics)}</div>
    <h4>关联对象</h4>
    <div class="chip-list">${related.slice(0, 8).map((item) => {
      const edge = item.edge || {};
      const relatedNode = item.node || {};
      return `<button type="button" class="graph-related" data-node-id="${escapeHtml(relatedNode.object_id || relatedNode.id)}">${escapeHtml(edge.label_cn || "关联")} · ${escapeHtml(relatedNode.label_cn || relatedNode.id)}</button>`;
    }).join("") || `<span>暂无关联对象</span>`}</div>
    <h4>可执行动作</h4>
    <div class="chip-list">${actions.map((action) => `<button type="button" class="graph-action" data-action="${escapeHtml(action.type_id)}" data-node-id="${escapeHtml(nodeId)}">${escapeHtml(action.label_cn)}</button>`).join("") || `<span>暂无动作</span>`}</div>
    <h4>可调用函数</h4>
    <div class="function-list">${functions.map((fn) => `<article><strong>${escapeHtml(fn.label_cn)}</strong><span>${escapeHtml(fn.description_cn)}</span></article>`).join("") || `<p class="empty-note">暂无函数</p>`}</div>
    ${risks.length ? `<h4>风险</h4><div class="risk-list">${risks.map((risk) => `<article class="risk-item"><strong>${escapeHtml(risk.severity_label_cn || risk.severity || "提示")}</strong><p>${escapeHtml(risk.message_cn || risk.message || "")}</p></article>`).join("")}</div>` : ""}
  `;
  el("graphNodeDetail").querySelectorAll(".graph-related").forEach((button) => {
    button.addEventListener("click", () => {
      markGraphSelection(button.dataset.nodeId);
      loadGraphNodeDetail(button.dataset.nodeId);
    });
  });
  el("graphNodeDetail").querySelectorAll(".graph-action").forEach((button) => {
    button.addEventListener("click", () => prefillWhatIfFromGraph(button.dataset.action, node));
  });
};

const renderGraphKeyValues = (object) => {
  const entries = Object.entries(object || {}).filter(([, value]) => value !== null && value !== undefined && value !== "");
  if (!entries.length) return `<span>暂无数据</span>`;
  return entries.slice(0, 10).map(([key, value]) => `
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
  if (node.object_type === "Block") {
    el("ruleTemplate").value = "production_driver";
    el("scenarioBlock").value = properties.block_id || node.technical_ref || "";
  }
  if (node.object_type === "MonthlyDriver") {
    el("ruleTemplate").value = properties.driver_type === "WORKLOAD" ? "workload_driver" : "production_driver";
    el("scenarioPeriod").value = properties.period || el("scenarioPeriod").value;
    el("scenarioBlock").value = properties.driver_type === "PRODUCTION" ? properties.target_id || "" : el("scenarioBlock").value;
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
  }
  if (view === "assets") await loadAssetWorkbench();
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
el("scenarioForm").addEventListener("submit", submitScenario);
el("addScenarioAssumptionBtn").addEventListener("click", addScenarioAssumption);
el("clearScenarioAssumptionsBtn").addEventListener("click", () => {
  state.scenarioDraftAssumptions = [];
  renderScenarioAssumptions();
});
el("compareBtn").addEventListener("click", loadScenarioCompare);
el("compareRowType").addEventListener("change", loadScenarioCompare);
el("compareDiffMode").addEventListener("change", loadScenarioCompare);
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
