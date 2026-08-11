(() => {
  "use strict";

  const RELEVANCE_LEVELS = ["high", "mid", "low"];
  const SCENARIO_METRICS = {
    market_share: ["total_traffic", "market_share", "rank_in_market"],
    keyword_competition: ["keyword_traffic", "organic_rank", "keyword_traffic_share"],
    ad_competition: ["ad_traffic", "ad_rank", "ad_traffic_share"],
    growth_quality: ["weighted_traffic", "organic_traffic", "ad_traffic"],
  };
  const SCENARIO_LABELS = {
    market_share: "市场概览",
    keyword_competition: "关键词雷达",
    ad_competition: "广告流量",
    growth_quality: "增长质量",
  };
  const METRIC_LABELS = {
    total_traffic: "总流量", market_share: "市场份额", rank_in_market: "市场排名",
    keyword_traffic: "关键词流量", organic_rank: "自然排名", keyword_traffic_share: "关键词份额",
    ad_traffic: "广告流量", ad_rank: "广告排名", ad_traffic_share: "广告份额",
    weighted_traffic: "加权流量", organic_traffic: "自然流量",
    search_volume: "搜索量", category_search_volume: "类目搜索量",
    competitive_difficulty: "竞争难度", organic_scroll_rate: "自然滚动率",
  };
  const PLACEHOLDER_IMAGE = "/static/placeholder-product.svg";
  const charts = {};
  const MI = {
    loaded: false,
    marketId: "slip_lead_leash",
    dateFrom: null,
    dateTo: null,
    rangePreset: "30d",
    ownAsin: "B0D6G27DNH",
    competitors: [],
    scenario: "market_share",
    metric: "total_traffic",
    relevance: "high",
    dashboard: null,
    comparison: null,
    keywordSummary: null,
    keywordTrend: [],
    asins: [],
    keywords: [],
    asinSnapshots: [],
    asinTrend: [],
    asinKeywords: [],
    competition: [],
    keyword: "",
    keywordMetric: "search_volume",
    keywordSearch: "",
    keywordSort: "search_volume",
    keywordSortDirection: "desc",
    expandedKeyword: null,
    selectedEvidenceId: null,
    evidenceInvoker: null,
    dashboardInsights: [],
    comparisonInsights: [],
    evidenceIndex: new Map(),
    partialErrors: { comparisonDetail: null, comparisonLoad: null, keywordDetail: null, keywordTrend: null },
    mainLoadCount: 0,
    requestToken: 0,
    controllers: { main: null, detail: null, comparison: null, keyword: null },
    eventsBound: false,
    rangeNotice: "",
  };

  function element(id) {
    return document.getElementById(id);
  }

  function queryAll(selector) {
    return typeof document.querySelectorAll === "function" ? [...document.querySelectorAll(selector)] : [];
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[character]);
  }

  function number(value, digits = 0) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return "—";
    return parsed.toLocaleString("zh-CN", {
      maximumFractionDigits: digits,
      minimumFractionDigits: digits,
    });
  }

  function percent(value, digits = 1) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(digits)}%` : "—";
  }

  function formatMetric(metric, value) {
    if (value == null) return "—";
    return metric.includes("share") ? percent(value) : number(value, metric.includes("rank") ? 0 : 1);
  }

  function formatRatio(value) {
    return value == null ? "—" : percent(value);
  }

  function normalizeUnavailableReason(reason, metric = MI.metric) {
    const fallback = `当前完整日快照未采集“${METRIC_LABELS[metric] || metric}”，需补充对应采集项后展示。`;
    if (!reason) return fallback;
    return String(reason).includes("This metric is unavailable") ? fallback : reason;
  }

  function safeImageUrl(value) {
    const url = String(value || "").trim();
    return /^https?:\/\//i.test(url) ? url : PLACEHOLDER_IMAGE;
  }

  function makeController() {
    if (typeof AbortController === "undefined") return { signal: undefined, abort() {} };
    return new AbortController();
  }

  function abortAllRequests() {
    Object.values(MI.controllers).forEach((controller) => controller?.abort());
    Object.keys(MI.controllers).forEach((key) => { MI.controllers[key] = null; });
  }

  function beginGeneration(family) {
    abortAllRequests();
    const controller = makeController();
    MI.controllers[family] = controller;
    return { signal: controller.signal, requestToken: ++MI.requestToken };
  }

  function isCurrentGeneration(requestToken, signal) {
    return requestToken === MI.requestToken && !signal?.aborted;
  }

  async function getJson(path, signal) {
    const response = await fetch(path, { signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  function queryDates() {
    const parts = [];
    if (MI.dateFrom) parts.push(`date_from=${encodeURIComponent(MI.dateFrom)}`);
    if (MI.dateTo) parts.push(`date_to=${encodeURIComponent(MI.dateTo)}`);
    return parts.length ? `&${parts.join("&")}` : "";
  }

  function comparisonPath() {
    const competitors = MI.competitors.map((asin) => `&competitor_asin=${encodeURIComponent(asin)}`).join("");
    return `/api/market-monitor/comparison?market_id=${encodeURIComponent(MI.marketId)}`
      + `&own_asin=${encodeURIComponent(MI.ownAsin)}${competitors}`
      + `&scenario=${encodeURIComponent(MI.scenario)}&metric=${encodeURIComponent(MI.metric)}${queryDates()}`;
  }

  function applyPresetRange(latestDate) {
    if (!latestDate || MI.rangePreset === "custom") return;
    MI.dateTo = latestDate;
    const start = new Date(`${latestDate}T00:00:00Z`);
    start.setUTCDate(start.getUTCDate() - (MI.rangePreset === "7d" ? 6 : 29));
    MI.dateFrom = start.toISOString().slice(0, 10);
  }

  function applyDateContext(dateContext) {
    MI.rangeNotice = "";
    const latestDate = dateContext.latest_complete_date || dateContext.latest_data_date;
    if (MI.rangePreset !== "custom") {
      applyPresetRange(latestDate);
      return;
    }
    if (!MI.dateFrom || !MI.dateTo) {
      throw new Error("自定义日期范围必须同时包含开始和结束日期");
    }
    const earliestDate = dateContext.earliest_data_date;
    if (!earliestDate || !latestDate) {
      throw new Error("新市场没有可用于校验自定义日期范围的数据");
    }
    if (MI.dateTo < earliestDate || MI.dateFrom > latestDate) {
      throw new Error(`自定义日期范围在新市场不可用（可用范围 ${earliestDate} 至 ${latestDate}）`);
    }
    const originalFrom = MI.dateFrom;
    const originalTo = MI.dateTo;
    MI.dateFrom = originalFrom < earliestDate ? earliestDate : originalFrom;
    MI.dateTo = originalTo > latestDate ? latestDate : originalTo;
    if (MI.dateFrom !== originalFrom || MI.dateTo !== originalTo) {
      MI.rangeNotice = `自定义日期范围已校正为 ${MI.dateFrom} 至 ${MI.dateTo}`;
    }
  }

  function setRegionState(id, kind, message) {
    const region = element(id);
    if (!region) return;
    if (region.matches?.("section") && region.querySelector?.(".mi-section-body")) {
      region.querySelector(".mi-section-body").innerHTML = `<div class="mi-${kind}">${escapeHtml(message)}</div>`;
      return;
    }
    region.innerHTML = `<div class="mi-${kind}">${escapeHtml(message)}</div>`;
  }

  function renderLoading() {
    ["mi-data-quality", "mi-kpis", "mi-keyword-contribution", "mi-insights", "mi-anomalies", "mi-opportunities", "mi-trend-state", "mi-comparison-state", "mi-keyword-state"]
      .forEach((id) => setRegionState(id, "loading", "正在读取最新完整日数据…"));
  }

  function renderError(error) {
    destroyChart("trend");
    destroyChart("comparison");
    destroyChart("keyword");
    const message = `加载失败：${error?.message || "未知错误"}。当前筛选条件已保留，请重试。`;
    ["mi-data-quality", "mi-kpis", "mi-keyword-contribution", "mi-insights", "mi-anomalies", "mi-opportunities", "mi-trend-state", "mi-comparison-state", "mi-keyword-state"]
      .forEach((id) => setRegionState(id, "error", message));
    const status = element("mi-status");
    if (status) status.textContent = message;
  }

  async function loadDashboard(signal) {
    const path = `/api/market-monitor/dashboard?market_id=${encodeURIComponent(MI.marketId)}${queryDates()}`;
    return getJson(path, signal);
  }

  async function load(force = false) {
    if (MI.loaded && !force) return;
    bindEvents();
    MI.marketId = element("mi-market")?.value || MI.marketId;
    const { signal, requestToken } = beginGeneration("main");
    renderLoading();
    const refresh = element("mi-refresh");
    MI.mainLoadCount += 1;
    if (refresh) refresh.disabled = true;
    try {
      const dateContext = await getJson(`/api/market-monitor/date-context?market_id=${encodeURIComponent(MI.marketId)}`, signal);
      if (!isCurrentGeneration(requestToken, signal)) return;
      applyDateContext(dateContext);
      const [dashboardResult, asinResult, summaryResult, keywordResult, comparisonResult] = await Promise.allSettled([
        loadDashboard(signal),
        getJson(`/api/market-monitor/asins?market_id=${encodeURIComponent(MI.marketId)}`, signal),
        getJson(`/api/market-monitor/keyword-summary?market_id=${encodeURIComponent(MI.marketId)}${queryDates()}`, signal),
        getJson(`/api/market-monitor/keywords?market_id=${encodeURIComponent(MI.marketId)}&relevance_level=${encodeURIComponent(MI.relevance)}${queryDates()}`, signal),
        MI.ownAsin ? getJson(comparisonPath(), signal) : Promise.resolve(null),
      ]);
      if (!isCurrentGeneration(requestToken, signal)) return;
      if (dashboardResult.status === "rejected") throw dashboardResult.reason;
      MI.dashboard = dashboardResult.value;
      MI.dashboardInsights = MI.dashboard.insights || [];
      rebuildEvidenceIndex();
      MI.asins = asinResult.status === "fulfilled" ? asinResult.value.asins || [] : [];
      MI.keywordSummary = summaryResult.status === "fulfilled" ? summaryResult.value : null;
      MI.keywords = keywordResult.status === "fulfilled" ? keywordResult.value.keywords || [] : [];
      MI.dateTo = MI.dateTo || MI.dashboard.context?.latest_data_date || null;
      const dashboardOwn = MI.dashboard.context?.own_asin;
      if (dashboardOwn) MI.ownAsin = dashboardOwn;
      if (!MI.asins.some((row) => (row.asin || row.child_asin) === MI.ownAsin) && MI.asins[0]) {
        MI.ownAsin = MI.asins[0].asin || MI.asins[0].child_asin;
      }
      if (!MI.keyword || !MI.keywords.some((row) => row.keyword === MI.keyword)) {
        MI.keyword = MI.keywords[0]?.keyword || "";
      }
      const initialComparison = comparisonResult.status === "fulfilled" ? comparisonResult.value : null;
      if (initialComparison?.context?.own_asin === MI.ownAsin) {
        const fallback = initialComparison.context.metric_available === false
          ? SCENARIO_METRICS[MI.scenario].find((candidate) => initialComparison.context.metric_availability?.[candidate] === true)
          : null;
        if (fallback && fallback !== MI.metric) {
          MI.metric = fallback;
          MI.comparison = null;
        } else {
          MI.comparison = initialComparison;
          MI.comparisonInsights = buildComparisonInsights();
          rebuildEvidenceIndex();
        }
      } else {
        MI.comparison = null;
        MI.comparisonInsights = [];
        rebuildEvidenceIndex();
      }
      renderContext();
      renderDataQuality();
      renderKpis();
      renderKeywordContribution();
      renderTrend();
      renderInsights();
      renderAnomalies();
      renderOpportunities();
      MI.loaded = true;
      const detailResults = await Promise.allSettled([
        loadObjectDetail(signal, requestToken),
        MI.comparison ? Promise.resolve() : loadComparison(signal, requestToken),
        loadKeywordTrend(signal, requestToken),
      ]);
      if (!isCurrentGeneration(requestToken, signal)) return;
      if (detailResults[1].status === "rejected") {
        MI.comparison = null;
        MI.comparisonInsights = [];
        MI.partialErrors.comparisonLoad = "深度对比暂时不可用，其余模块保留。可刷新重试。";
        rebuildEvidenceIndex();
        destroyChart("comparison");
        destroyChart("trend");
        setRegionState("mi-trend-state", "error", MI.partialErrors.comparisonLoad);
        setRegionState("mi-comparison-state", "error", MI.partialErrors.comparisonLoad);
      }
      if (detailResults[2].status === "rejected") {
        MI.keywordTrend = [];
        MI.partialErrors.keywordTrend = "关键词趋势暂时不可用，其余模块保留。可切换关键词重试。";
        destroyChart("keyword");
        setRegionState("mi-keyword-state", "error", MI.partialErrors.keywordTrend);
      }
      const status = element("mi-status");
      if (status) {
        const baseStatus = MI.dateTo ? `数据截至 ${MI.dateTo} · 基线：前一完整日 / 近 7 个完整日` : "暂无完整日数据";
        status.textContent = MI.rangeNotice ? `${baseStatus} · ${MI.rangeNotice}` : baseStatus;
      }
    } catch (error) {
      if (error?.name !== "AbortError" && requestToken === MI.requestToken) renderError(error);
    } finally {
      MI.mainLoadCount = Math.max(0, MI.mainLoadCount - 1);
      if (refresh) refresh.disabled = MI.mainLoadCount > 0;
    }
  }

  async function loadObjectDetail(signal, requestToken) {
    if (!MI.ownAsin) {
      if (!isCurrentGeneration(requestToken, signal)) return;
      MI.asinSnapshots = [];
      MI.asinTrend = [];
      MI.asinKeywords = [];
      MI.competition = [];
      renderComparison();
      renderKeywordDashboard();
      return;
    }
    const market = `market_id=${encodeURIComponent(MI.marketId)}`;
    const date = MI.dateTo ? `&date=${encodeURIComponent(MI.dateTo)}` : "";
    const keyword = MI.keyword ? encodeURIComponent(MI.keyword) : "";
    const requests = [
      getJson(`/api/market-monitor/asin-snapshots?${market}${date}&limit=200`, signal),
      getJson(`/api/market-monitor/asin-trend?${market}&child_asin=${encodeURIComponent(MI.ownAsin)}${queryDates()}`, signal),
      getJson(`/api/market-monitor/asin-keywords?${market}&child_asin=${encodeURIComponent(MI.ownAsin)}${date}`, signal),
      keyword ? getJson(`/api/market-monitor/keyword-competition?${market}&keyword=${keyword}${date}&limit=30`, signal) : Promise.resolve({ asins: [] }),
    ];
    const [snapshots, trend, asinKeywords, competition] = await Promise.allSettled(requests);
    if (!isCurrentGeneration(requestToken, signal)) return;
    MI.asinSnapshots = snapshots.status === "fulfilled" ? snapshots.value.asins || [] : [];
    MI.asinTrend = trend.status === "fulfilled" ? trend.value.rows || [] : [];
    MI.asinKeywords = asinKeywords.status === "fulfilled" ? asinKeywords.value.keywords || [] : [];
    MI.competition = competition.status === "fulfilled" ? competition.value.asins || [] : [];
    MI.partialErrors.comparisonDetail = [snapshots, trend].some((result) => result.status === "rejected")
      ? "部分 ASIN 数据加载失败，可刷新重试。" : null;
    MI.partialErrors.keywordDetail = [asinKeywords, competition].some((result) => result.status === "rejected")
      ? "部分关键词证据加载失败，可切换关键词重试。" : null;
    renderKpis();
    renderKeywordContribution();
    renderComparison();
    renderKeywordDashboard();
  }

  async function loadComparison(signal, requestToken) {
    if (!MI.ownAsin) return;
    const payload = await getJson(comparisonPath(), signal);
    if (!isCurrentGeneration(requestToken, signal)) return;
    if (payload.context?.metric_available === false) {
      const fallback = SCENARIO_METRICS[MI.scenario].find((candidate) => payload.context?.metric_availability?.[candidate] === true);
      if (fallback && fallback !== MI.metric) {
        MI.metric = fallback;
        return loadComparison(signal, requestToken);
      }
    }
    MI.comparison = payload;
    MI.partialErrors.comparisonLoad = null;
    MI.comparisonInsights = buildComparisonInsights();
    rebuildEvidenceIndex();
    renderLinkedAnalysis();
  }

  async function loadKeywordTrend(signal, requestToken) {
    if (!MI.keyword) {
      if (!isCurrentGeneration(requestToken, signal)) return;
      MI.keywordTrend = [];
      renderKeywordDashboard();
      return;
    }
    const path = `/api/market-monitor/keyword-trend?market_id=${encodeURIComponent(MI.marketId)}`
      + `&keyword=${encodeURIComponent(MI.keyword)}${queryDates()}`;
    const payload = await getJson(path, signal);
    if (!isCurrentGeneration(requestToken, signal)) return;
    MI.keywordTrend = payload.rows || [];
    MI.partialErrors.keywordTrend = null;
    renderKeywordDashboard();
  }

  function renderContext() {
    renderObjectSelector();
    renderKeywordSelector();
    renderScenarioControls();
    queryAll("[data-mi-range]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.miRange === MI.rangePreset);
      button.setAttribute("aria-pressed", String(button.dataset.miRange === MI.rangePreset));
    });
    const customDates = element("mi-custom-dates");
    if (customDates) customDates.hidden = MI.rangePreset !== "custom";
    if (element("mi-date-from")) element("mi-date-from").value = MI.dateFrom || "";
    if (element("mi-date-to")) element("mi-date-to").value = MI.dateTo || "";
  }

  function renderObjectSelector() {
    const trigger = element("mi-object-trigger");
    const options = element("mi-object-options");
    const select = element("mi-asin");
    if (!trigger || !options || !select) return;
    select.innerHTML = "";
    options.replaceChildren();
    const groups = new Map();
    MI.asins.forEach((row) => {
      const asin = String(row.asin || row.child_asin || "");
      const parent = String(row.parent_asin || asin || "未识别父体");
      const nativeOption = document.createElement("option");
      nativeOption.value = asin;
      nativeOption.textContent = `${asin} / ${parent}`;
      select.append(nativeOption);
      if (!groups.has(parent)) groups.set(parent, []);
      groups.get(parent).push(row);
    });
    let index = 0;
    groups.forEach((rows, parent) => {
      const group = document.createElement("li");
      group.className = "mi-object-group";
      group.setAttribute("role", "group");
      group.setAttribute("aria-label", `父 ASIN ${parent}`);
      const groupLabel = document.createElement("span");
      groupLabel.className = "mi-object-group-label";
      groupLabel.textContent = `父 ASIN · ${parent}`;
      const groupOptions = document.createElement("ul");
      groupOptions.setAttribute("role", "presentation");
      groupOptions.className = "mi-object-group-options";
      group.append(groupLabel, groupOptions);
      rows.forEach((row) => {
      const asin = String(row.asin || row.child_asin || "");
      const option = document.createElement("li");
      option.id = `mi-object-${index++}`;
      option.className = "mi-object-option";
      option.dataset.asin = asin;
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", String(asin === MI.ownAsin));
      option.tabIndex = -1;
      option.append(createObjectImage(row), createObjectCopy(row));
      option.addEventListener("click", () => chooseObject(asin));
      option.addEventListener("keydown", handleObjectOptionKeydown);
      groupOptions.append(option);
      });
      options.append(group);
    });
    select.value = MI.ownAsin;
    const selected = MI.asins.find((row) => (row.asin || row.child_asin) === MI.ownAsin) || { asin: MI.ownAsin };
    trigger.replaceChildren(createObjectImage(selected), createObjectCopy(selected));
  }

  function createObjectImage(row) {
    const image = document.createElement("img");
    image.className = "mi-object-thumb";
    image.src = safeImageUrl(row.image_url);
    image.alt = "";
    image.loading = "lazy";
    image.addEventListener("error", () => {
      if (!image.src.endsWith("placeholder-product.svg")) image.src = PLACEHOLDER_IMAGE;
    }, { once: true });
    return image;
  }

  function createObjectCopy(row) {
    const copy = document.createElement("span");
    const asin = row.asin || row.child_asin || "暂无代表子体";
    const parent = row.parent_asin || asin || "—";
    const traffic = row.latest_total_traffic ?? row.total_traffic;
    copy.className = "mi-object-copy";
    const title = document.createElement("strong");
    title.textContent = asin;
    const meta = document.createElement("span");
    meta.textContent = `父 ASIN ${parent} · 最新完整日流量 ${number(traffic)}`;
    copy.append(title, meta);
    if (row.is_own_product || row.is_representative_child) {
      const flag = document.createElement("em");
      flag.textContent = row.is_own_product ? "自有 Listing 代表子体" : "代表子体";
      copy.append(flag);
    }
    return copy;
  }

  function renderKeywordSelector() {
    const select = element("mi-keyword");
    if (!select) return;
    select.innerHTML = MI.keywords.map((row) => (
      `<option value="${escapeHtml(row.keyword)}">${escapeHtml(row.keyword)}${row.translation ? ` · ${escapeHtml(row.translation)}` : ""}</option>`
    )).join("");
    select.value = MI.keyword;
  }

  function renderScenarioControls() {
    const scenarios = element("mi-scenarios");
    const metrics = element("mi-metrics");
    if (scenarios) {
      scenarios.innerHTML = Object.keys(SCENARIO_METRICS).map((scenario) => (
        `<button type="button" data-mi-scenario="${scenario}" class="${scenario === MI.scenario ? "is-active" : ""}" aria-pressed="${scenario === MI.scenario}">${SCENARIO_LABELS[scenario]}</button>`
      )).join("");
      scenarios.querySelectorAll("[data-mi-scenario]").forEach((button) => button.addEventListener("click", () => setScenario(button.dataset.miScenario)));
    }
    if (metrics) {
      const availability = MI.comparison?.context?.metric_availability || {};
      metrics.innerHTML = SCENARIO_METRICS[MI.scenario].map((metric) => (
        `<button type="button" data-mi-metric="${metric}" class="${metric === MI.metric ? "is-active" : ""}" aria-pressed="${metric === MI.metric}" ${availability[metric] === false ? 'disabled title="当前完整日快照不支持此指标"' : ""}>${METRIC_LABELS[metric]}</button>`
      )).join("");
      metrics.querySelectorAll("[data-mi-metric]").forEach((button) => button.addEventListener("click", () => setMetric(button.dataset.miMetric)));
    }
  }

  function renderDataQuality() {
    const region = element("mi-data-quality");
    if (!region) return;
    const quality = MI.dashboard?.data_quality;
    if (!quality) return setRegionState("mi-data-quality", "empty", "暂无采集质量信息，指标不会被误判为市场下滑。"), undefined;
    const completeness = Math.round(Number(quality.completeness_score || 0) * 100);
    const coverage = Math.round(Number(quality.provider_coverage || 0) * 100);
    const partial = completeness < 100 || coverage < 100 || quality.missing_data_types?.length;
    const statusClass = quality.is_stale ? "is-stale" : partial ? "is-partial" : "";
    const statusText = quality.is_stale ? "数据已过期" : partial ? "部分数据" : "数据完整";
    region.innerHTML = `<span class="mi-quality-status ${statusClass}">${statusText}</span>`
      + `<span class="mi-quality-item">目标日 <strong>${escapeHtml(quality.target_data_date || "—")}</strong></span>`
      + `<span class="mi-quality-item">可用至 <strong>${escapeHtml(quality.effective_data_available_through || "—")}</strong></span>`
      + `<span class="mi-quality-item">采集 <strong>${escapeHtml(quality.collect_time_bj || "缺失")}</strong> ${escapeHtml(quality.collection_timezone || "时区缺失")}</span>`
      + `<span class="mi-quality-item">来源 <strong>${escapeHtml((quality.providers || []).join(" / ") || "未记录")}</strong></span>`
      + `<span class="mi-quality-item">覆盖 <strong>${coverage}%</strong></span>`
      + `<span class="mi-quality-item">缺失 <strong>${escapeHtml((quality.missing_data_types || []).join(" / ") || "无")}</strong></span>`
      + `<span class="mi-quality-item">完整度 <strong>${completeness}%</strong></span>`
      + `<span class="mi-quality-meter" aria-label="完整度 ${completeness}%"><span style="width:${Math.min(100, Math.max(0, completeness))}%"></span></span>`;
  }

  function renderKpis() {
    const region = element("mi-kpis");
    if (!region) return;
    const comparisonMatches = MI.comparison?.context?.own_asin === MI.ownAsin
      && MI.comparison?.context?.scenario === MI.scenario
      && MI.comparison?.context?.metric === MI.metric;
    if (comparisonMatches && MI.comparison.context.metric_available === false) {
      return setRegionState("mi-kpis", "empty", normalizeUnavailableReason(MI.comparison.context.unavailable_reason)), undefined;
    }
    let kpis = comparisonMatches ? (MI.comparison.comparisons || []).map((row) => ({
      label: `${row.asin} · ${METRIC_LABELS[MI.metric] || MI.metric}`,
      metric: MI.metric,
      current: row.current,
      previous_complete_day: row.previous_complete_day,
      latest_7_complete_days: row.latest_7_complete_days,
    })) : MI.dashboard?.kpis || [];
    if (!kpis.length && MI.asinTrend.length) {
      const own = MI.asinTrend.at(-1) || {};
      const topKeyword = MI.asinKeywords[0] || {};
      const topCompetitor = MI.competition[0] || {};
      kpis = [
        { label: "所选子体流量", metric: "total_traffic", current: own.total_traffic },
        { label: "自然流量", metric: "organic_traffic", current: own.organic_traffic },
        { label: "广告流量", metric: "ad_traffic", current: own.ad_traffic },
        { label: "市场排名", metric: "rank_in_market", current: own.rank_in_market, summary: MI.ownAsin },
        { label: "子体核心词", metric: "keyword_traffic", current: topKeyword.keyword_traffic, summary: topKeyword.keyword },
        { label: "词下头部子体", metric: "keyword_traffic_share", current: topCompetitor.keyword_traffic_share, summary: topCompetitor.child_asin },
      ];
    }
    if (!kpis.length) return setRegionState("mi-kpis", "empty", "暂无完整日 KPI。"), undefined;
    const nodes = kpis.map((kpi) => {
      const card = document.createElement("article");
      card.className = "mi-kpi";
      const heading = document.createElement("div");
      heading.className = "mi-kpi-label";
      heading.textContent = kpi.label || METRIC_LABELS[kpi.metric] || kpi.id || "指标";
      const value = document.createElement("div");
      value.className = "mi-kpi-value";
      value.textContent = formatMetric(kpi.metric || "", kpi.current);
      const baselines = document.createElement("div");
      baselines.className = "mi-kpi-baselines";
      if (kpi.summary) {
        baselines.textContent = String(kpi.summary);
        baselines.title = String(kpi.summary);
      } else {
        const previous = document.createElement("span");
        previous.className = "mi-delta";
        previous.textContent = `较前日 ${formatRatio(kpi.previous_complete_day?.change)}`;
        const average = document.createElement("span");
        average.className = "mi-delta";
        average.textContent = `较 7 日均值 ${formatRatio(kpi.latest_7_complete_days?.deviation)}`;
        baselines.append(previous, average);
      }
      card.append(heading, value, baselines);
      return card;
    });
    region.replaceChildren(...nodes);
  }

  function renderKeywordContribution() {
    const region = element("mi-keyword-contribution");
    const body = region?.querySelector(".mi-section-body");
    if (!body) return;
    if (!MI.ownAsin) {
      body.innerHTML = '<div class="mi-empty">请选择一个 Listing 代表子体后查看关键词贡献。</div>';
      return;
    }
    const rows = (MI.asinKeywords || [])
      .filter((row) => Number(row.keyword_traffic) > 0)
      .sort((left, right) => Number(right.keyword_traffic || 0) - Number(left.keyword_traffic || 0))
      .slice(0, 8);
    if (!rows.length) {
      body.innerHTML = '<div class="mi-empty">所选代表子体暂无最新完整日关键词贡献快照。需要先采集 ASIN × 关键词流量明细。</div>';
      return;
    }
    const total = rows.reduce((sum, row) => sum + Number(row.keyword_traffic || 0), 0);
    body.innerHTML = `<div class="mi-contribution-list">${rows.map((row) => {
      const share = total ? Number(row.keyword_traffic || 0) / total : null;
      const level = RELEVANCE_LEVELS.includes(row.relevance_level) ? row.relevance_level : "low";
      return `<button type="button" class="mi-contribution-row" data-mi-keyword-jump="${escapeHtml(row.keyword)}" data-mi-relevance-jump="${level}">`
        + `<span class="mi-contribution-main"><strong>${escapeHtml(row.keyword)}</strong><span>${escapeHtml(row.translation || "未记录中文翻译")}</span></span>`
        + `<span class="mi-tier ${level}">${tierLabel(level)}</span>`
        + `<span class="mi-contribution-metric">${number(row.keyword_traffic)}<small>${percent(share)}</small></span>`
        + `<span class="mi-contribution-ranks">自然 ${number(row.organic_rank)} · 广告 ${number(row.ad_rank)}</span>`
        + `<span class="mi-contribution-bar"><i style="width:${Math.max(2, Math.min(100, (share || 0) * 100))}%"></i></span>`
      + `</button>`;
    }).join("")}</div>`;
    body.querySelectorAll("[data-mi-keyword-jump]").forEach((button) => {
      button.addEventListener("click", () => jumpToKeyword(button.dataset.miKeywordJump, button.dataset.miRelevanceJump));
    });
  }

  function renderTrend() {
    const region = element("mi-trend");
    if (!region) return;
    const comparisonMatches = MI.comparison?.context?.own_asin === MI.ownAsin
      && MI.comparison?.context?.scenario === MI.scenario
      && MI.comparison?.context?.metric === MI.metric;
    const series = (comparisonMatches
      ? (MI.comparison.series || []).map((item) => ({ ...item, label: item.asin }))
      : MI.dashboard?.trend?.series || [])
      .map((item) => ({ ...item, points: completePoints(item) }));
    if (comparisonMatches && MI.comparison.context.metric_available === false) {
      destroyChart("trend");
      return setRegionState("mi-trend-state", "empty", normalizeUnavailableReason(MI.comparison.context.unavailable_reason)), undefined;
    }
    if (!hasUsableValues(series)) {
      destroyChart("trend");
      return setRegionState("mi-trend-state", "empty", "当前范围没有可绘制的完整日趋势。可切换 30 天范围或刷新采集数据。"), undefined;
    }
    const stateRegion = element("mi-trend-state");
    if (stateRegion) stateRegion.replaceChildren();
    const canvas = element("miTrendChart");
    if (!canvas || typeof Chart === "undefined") return;
    destroyChart("trend");
    const labels = [...new Set(series.flatMap((item) => (item.points || []).map((point) => point.date)))].sort();
    const datasets = series.map((item, index) => {
      const byDate = Object.fromEntries((item.points || []).map((point) => [point.date, point]));
      const color = item.role === "own" ? cssToken("--mi-own")
        : item.role === "competitor" ? competitorColor(item.asin)
          : item.id === "own_market_share" ? cssToken("--mi-own")
            : cssToken(index === 0 ? "--mi-primary" : "--mi-competitor-1");
      return {
        label: item.label, data: labels.map((date) => byDate[date]?.value ?? null),
        borderColor: color, backgroundColor: "transparent", tension: .24,
        pointRadius: 2, spanGaps: true, metric: item.metric || MI.metric,
        baseline: comparisonMatches ? comparisonBaseline(item.asin) : null,
      };
    });
    datasets.slice().forEach((dataset) => datasets.push({
      label: `${dataset.label} · 7日均线`, data: movingAverage(dataset.data, 7),
      borderColor: dataset.borderColor, backgroundColor: "transparent",
      borderDash: [5, 4], borderWidth: 1, pointRadius: 0, spanGaps: true,
    }));
    const anomalyMarkers = MI.dashboard?.trend?.anomaly_markers || [];
    anomalyMarkers.forEach((marker) => datasets.push({
      label: "异常", data: labels.map((date) => date === marker.date ? marker.value : null),
      borderColor: cssToken("--mi-risk"), backgroundColor: cssToken("--mi-risk"),
      pointRadius: 6, showLine: false, insightId: marker.insight_id,
    }));
    (MI.dashboard?.trend?.collection_markers || []).forEach((marker) => {
      const markerIndex = labels.indexOf(marker.date);
      const value = markerIndex >= 0 ? datasets[0]?.data[markerIndex] : null;
      datasets.push({ label: "采集/来源变更", data: labels.map((date) => date === marker.date ? value : null), borderColor: cssToken("--mi-mid"), backgroundColor: cssToken("--mi-mid"), pointStyle: "triangle", pointRadius: 6, showLine: false, collectionMarker: true });
    });
    charts.trend = new Chart(canvas, {
      type: "line", data: { labels, datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        onClick(_event, active) {
          const anomaly = active.find((item) => datasets[item.datasetIndex]?.insightId);
          if (anomaly) openEvidence(datasets[anomaly.datasetIndex].insightId, canvas);
        },
        plugins: { legend: { position: "top" }, tooltip: { callbacks: { afterBody: tooltipBaselines } } },
        scales: { y: { beginAtZero: false } },
      },
    });
  }

  function hasUsableValues(series) {
    return series.some((item) => completePoints(item).some((point) => point.value != null && Number.isFinite(Number(point.value))));
  }

  function completePoints(item) {
    return (item?.points || []).filter((point) => point.is_complete !== false);
  }

  function comparisonBaseline(asin) {
    return (MI.comparison?.comparisons || []).find((row) => row.asin === asin) || null;
  }

  function movingAverage(values, windowSize) {
    return values.map((_value, index) => {
      const sample = values.slice(Math.max(0, index - windowSize + 1), index + 1).filter((value) => value != null && Number.isFinite(Number(value)));
      return sample.length ? sample.reduce((sum, value) => sum + Number(value), 0) / sample.length : null;
    });
  }

  function tooltipBaselines(items) {
    const dataset = items?.[0]?.dataset || {};
    const label = dataset.label;
    const kpi = dataset.baseline || (MI.dashboard?.kpis || []).find((item) => item.label === label);
    const metric = dataset.metric || kpi?.metric || MI.metric;
    return [
      `前一完整日：${kpi?.previous_complete_day?.value == null ? "N/A" : formatMetric(metric, kpi.previous_complete_day.value)}`,
      `近 7 个完整日均值：${kpi?.latest_7_complete_days?.average == null ? "N/A" : formatMetric(metric, kpi.latest_7_complete_days.average)}`,
    ];
  }

  function renderInsights() {
    renderInsightList("mi-insights", currentInsights(), "当前没有可展示的智能研判。", false);
  }

  function currentInsights() {
    if (MI.comparison?.context?.metric === MI.metric && MI.comparison?.context?.metric_available === false) return [];
    return MI.comparison?.context?.metric === MI.metric && MI.comparisonInsights.length
      ? MI.comparisonInsights
      : MI.dashboardInsights;
  }

  function buildComparisonInsights() {
    return (MI.comparison?.comparisons || []).map((row) => {
        const change = Number(row.previous_complete_day?.change);
        const direction = Number.isFinite(change) && change !== 0 ? (change > 0 ? "up" : "down") : "flat";
        return { insight_id: `comparison:${row.asin}:${MI.metric}`, severity: "info", metric: MI.metric, current: row.current, fact: `${row.asin} 的${METRIC_LABELS[MI.metric] || MI.metric}为 ${formatMetric(MI.metric, row.current)}。`, confidence: MI.dashboard?.data_quality?.completeness_score, previous_complete_day: row.previous_complete_day, latest_7_complete_days: row.latest_7_complete_days, persistence_days: row.persistence_days, direction, contributors: MI.comparison?.gap_contributors || [], provider_coverage: MI.dashboard?.data_quality?.provider_coverage, evidence_ids: row.evidence_ids || [] };
      });
  }

  function rebuildEvidenceIndex() {
    MI.dashboardInsights = MI.dashboard?.insights || MI.dashboardInsights;
    MI.evidenceIndex = new Map();
    [...MI.dashboardInsights, ...MI.comparisonInsights, ...(MI.dashboard?.opportunities || [])].forEach((row) => {
      const id = row.insight_id || row.opportunity_id || row.evidence_id;
      if (id) MI.evidenceIndex.set(id, row);
    });
  }

  function dashboardScopeLabel() {
    const dashboardAsin = MI.dashboard?.context?.own_asin || "市场";
    const changed = dashboardAsin !== MI.ownAsin || MI.scenario !== "market_share" || MI.metric !== "total_traffic";
    return changed
      ? `原始日报研判：${dashboardAsin} · 市场概览。上方图表已切换为 ${SCENARIO_LABELS[MI.scenario]} / ${METRIC_LABELS[MI.metric] || MI.metric}，右侧异常与机会仍按日报基线展示。`
      : `日报研判基线：${dashboardAsin} · 市场概览`;
  }

  function renderAnomalies() {
    const anomalies = MI.dashboardInsights.filter((item) => ["risk", "warning", "critical"].includes(item.severity));
    renderInsightList("mi-anomalies", anomalies, "未发现达到阈值的异常。数据不足时不会生成异常结论。", true, dashboardScopeLabel());
  }

  function renderOpportunities() {
    renderInsightList("mi-opportunities", MI.dashboard?.opportunities || [], "暂无高置信机会，继续观察完整日趋势。", true, dashboardScopeLabel());
  }

  function renderInsightList(regionId, rows, emptyMessage, actionable, scopeLabel = "") {
    const region = element(regionId);
    const body = region?.querySelector(".mi-section-body");
    if (!body) return;
    const scope = scopeLabel ? `<p class="mi-context-note">${escapeHtml(scopeLabel)}</p>` : "";
    if (!rows.length) {
      body.innerHTML = `${scope}<div class="mi-empty">${escapeHtml(emptyMessage)}</div>`;
      return;
    }
    body.innerHTML = scope + rows.map((row) => {
      const id = row.insight_id || row.opportunity_id || row.evidence_id || "";
      const severity = row.severity === "risk" || row.severity === "critical" ? "is-risk" : row.severity === "opportunity" ? "is-opportunity" : "";
      return `<article class="mi-insight"><div class="mi-insight-meta"><span class="mi-severity ${severity}">${escapeHtml(row.severity || "info")}</span><span>置信度 ${percent(row.confidence)}</span></div>`
        + `<p>${escapeHtml(row.fact || row.title || row.recommendation || "暂无结论")}</p>`
        + `${(actionable || row.evidence_ids?.length) && id ? `<button type="button" data-mi-evidence="${escapeHtml(id)}">查看证据</button>` : ""}</article>`;
    }).join("");
    body.querySelectorAll("[data-mi-evidence]").forEach((button) => button.addEventListener("click", () => openEvidence(button.dataset.miEvidence, button)));
  }

  function openEvidence(insightId, invoker = null) {
    MI.selectedEvidenceId = insightId;
    MI.evidenceInvoker = invoker || document.activeElement;
    rebuildEvidenceIndex();
    renderEvidenceDrawer();
  }

  function renderEvidenceDrawer() {
    const drawer = element("mi-evidence-drawer");
    const content = element("mi-evidence-content");
    if (!drawer || !content) return;
    const insight = MI.evidenceIndex.get(MI.selectedEvidenceId);
    if (!MI.selectedEvidenceId || !insight) {
      drawer.hidden = true;
      drawer.setAttribute("aria-hidden", "true");
      return;
    }
    const blocks = [
      ["结论", insight.fact || insight.title],
      ["严重度", insight.severity || "info"],
      ["触发指标", METRIC_LABELS[insight.metric] || insight.metric || insight.scope],
      ["当前值", formatMetric(insight.metric || "", insight.current)],
      ["对比基线", `前一完整日 ${formatMetric(insight.metric || "", insight.previous_complete_day?.value)}（变化 ${formatRatio(insight.previous_complete_day?.change)}）；近7完整日均值 ${formatMetric(insight.metric || "", insight.latest_7_complete_days?.average)}（偏离 ${formatRatio(insight.latest_7_complete_days?.deviation)}）`],
      ["持续与方向", `${number(insight.persistence_days)} 天 · ${escapeDirection(insight.direction)}`],
      ["贡献项", contributorText(insight.contributors)],
      ["来源覆盖", percent(insight.provider_coverage)],
      ["可能原因", insight.inference], ["建议动作", insight.recommendation],
      ["置信度", percent(insight.confidence)], ["证据编号", (insight.evidence_ids || []).join("\n")],
    ].filter(([, value]) => value != null && value !== "");
    content.innerHTML = blocks.map(([label, value]) => `<section class="mi-evidence-block"><h4>${label}</h4><p class="${label === "证据编号" ? "mi-evidence-ids" : ""}">${escapeHtml(value)}</p></section>`).join("")
      + '<a class="mi-deep-link" href="#mi-comparison">带当前上下文进入深度对比</a>';
    drawer.hidden = false;
    drawer.setAttribute("aria-hidden", "false");
    element("mi-evidence-close")?.focus();
  }

  function closeEvidence() {
    const invoker = MI.evidenceInvoker;
    MI.selectedEvidenceId = null;
    MI.evidenceInvoker = null;
    renderEvidenceDrawer();
    if (invoker && typeof invoker.focus === "function" && invoker.isConnected !== false) invoker.focus();
  }

  function contributorText(contributors) {
    if (!Array.isArray(contributors) || !contributors.length) return "暂无可归因贡献项";
    return contributors.slice(0, 5).map((row) => `${row.keyword || row.competitor_asin || row.asin || "对象"}: ${number(row.traffic_gap ?? row.contribution ?? row.value, 1)}`).join("；");
  }

  function escapeDirection(direction) {
    return direction === "up" ? "上升" : direction === "down" ? "下降" : "持平/未知";
  }

  function renderComparison() {
    renderCompetitorPicker();
    renderComparisonChart();
    renderComparisonTable();
    renderGapAnalysis();
    renderAsinTable();
    renderPersistentError("comparison");
  }

  function renderLinkedAnalysis() {
    renderContext();
    renderKpis();
    renderKeywordContribution();
    renderTrend();
    renderInsights();
    renderAnomalies();
    renderOpportunities();
    renderComparison();
  }

  function renderCompetitorPicker() {
    const picker = element("mi-competitor-picker");
    if (!picker) return;
    const candidates = MI.asins.filter((row) => (row.asin || row.child_asin) !== MI.ownAsin);
    if (!candidates.length) {
      picker.innerHTML = '<span class="mi-empty">暂无可选代表子体</span>';
      return;
    }
    picker.innerHTML = candidates.map((row) => {
      const asin = row.asin || row.child_asin;
      const selected = MI.competitors.includes(asin);
      const disabled = !selected && MI.competitors.length >= 3;
      return `<button type="button" class="mi-competitor-chip ${selected ? "is-selected" : ""}" data-mi-competitor="${escapeHtml(asin)}" aria-pressed="${selected}" ${disabled ? "disabled" : ""}><span class="mi-chip-dot"></span>${escapeHtml(asin)} · 父 ${escapeHtml(row.parent_asin || asin)}</button>`;
    }).join("");
    picker.querySelectorAll("[data-mi-competitor]").forEach((button) => button.addEventListener("click", () => toggleCompetitor(button.dataset.miCompetitor)));
  }

  function renderComparisonChart() {
    const series = (MI.comparison?.series || []).map((item) => ({ ...item, points: completePoints(item) }));
    const canvas = element("miComparisonChart");
    if (!canvas || typeof Chart === "undefined") return;
    destroyChart("comparison");
    if (MI.comparison?.context?.metric_available === false || !hasUsableValues(series)) {
      const message = MI.comparison?.context?.metric_available === false
        ? normalizeUnavailableReason(MI.comparison?.context?.unavailable_reason)
        : "暂无可对齐的有效商品趋势。选择其他指标或扩大日期范围。";
      setRegionState("mi-comparison-state", "empty", message);
      return;
    }
    const stateRegion = element("mi-comparison-state");
    if (stateRegion) stateRegion.replaceChildren();
    const labels = [...new Set(series.flatMap((item) => item.points.map((point) => point.date)))].sort();
    const datasets = series.map((item) => {
      const values = Object.fromEntries(item.points.map((point) => [point.date, point.value]));
      return {
        label: item.asin, data: labels.map((date) => values[date] ?? null),
        borderColor: item.role === "own" ? cssToken("--mi-own") : competitorColor(item.asin),
        backgroundColor: "transparent", tension: .24, pointRadius: 2, spanGaps: true,
        metric: MI.metric, baseline: comparisonBaseline(item.asin),
      };
    });
    (MI.dashboard?.trend?.collection_markers || []).forEach((marker) => {
      const markerIndex = labels.indexOf(marker.date);
      const value = markerIndex >= 0 ? datasets[0]?.data[markerIndex] : null;
      datasets.push({ label: "采集/来源变更", data: labels.map((date) => date === marker.date ? value : null), borderColor: cssToken("--mi-mid"), backgroundColor: cssToken("--mi-mid"), pointStyle: "triangle", pointRadius: 6, showLine: false, collectionMarker: true });
    });
    charts.comparison = new Chart(canvas, {
      type: "line", data: { labels, datasets },
      options: { responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false }, plugins: { legend: { position: "top" }, tooltip: { callbacks: { afterBody: tooltipBaselines } } } },
    });
  }

  function renderComparisonTable() {
    const region = element("mi-comparison-table");
    if (!region) return;
    const rows = MI.comparison?.comparisons || [];
    if (MI.comparison?.context?.metric_available === false) {
      region.innerHTML = `<div class="mi-empty">${escapeHtml(normalizeUnavailableReason(MI.comparison.context.unavailable_reason))}</div>`;
      return;
    }
    if (!rows.length) {
      region.innerHTML = '<div class="mi-empty">选择 1-3 个竞品代表子体后显示“自身 vs 竞品”的当前值、前一日和 7 日均值。</div>';
      return;
    }
    region.innerHTML = `<table class="mi-table"><thead><tr><th>对象</th><th>角色</th><th>当前</th><th>前一日</th><th>7 日均值</th><th>样本</th></tr></thead><tbody>${rows.map((row) => (
      `<tr class="${row.asin === MI.ownAsin ? "mi-own" : ""}"><td>${escapeHtml(row.asin)}</td><td>${row.asin === MI.ownAsin ? "自身" : "竞品"}</td><td>${formatMetric(MI.metric, row.current)}</td><td>${formatMetric(MI.metric, row.previous_complete_day?.value)}</td><td>${formatMetric(MI.metric, row.latest_7_complete_days?.average)}</td><td>${number(row.baseline_sample_size)}</td></tr>`
    )).join("")}</tbody></table>`;
  }

  function renderGapAnalysis() {
    const region = element("mi-gap-analysis");
    if (!region) return;
    const rows = (MI.comparison?.gap_contributors || []).slice(0, 12);
    if (!rows.length) {
      const reason = MI.comparison?.action_priority_analysis?.unavailable_reason
        || "当前对象组合暂无可归因差距；需要竞品与同日关键词快照。";
      region.innerHTML = `<div class="mi-empty">${escapeHtml(reason)}</div>`;
      return;
    }
    const actions = MI.comparison?.action_priorities || [];
    region.innerHTML = `<div class="mi-gap-layout"><section><h4 class="mi-subsection-title">关键词差距贡献</h4><div class="mi-table-wrap"><table class="mi-table"><thead><tr><th>关键词</th><th>竞品</th><th>我方</th><th>竞品</th><th>差距</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${escapeHtml(row.keyword)}</td><td>${escapeHtml(row.competitor_asin)}</td><td>${number(row.own_traffic)}</td><td>${number(row.competitor_traffic)}</td><td>${number(row.traffic_gap)}</td></tr>`).join("")}</tbody></table></div></section>`
      + `<section><h4 class="mi-subsection-title">行动优先级</h4>${actions.length ? actions.map((action) => `<article class="mi-insight"><div class="mi-insight-meta"><span class="mi-severity ${action.priority === "high" ? "is-risk" : ""}">${escapeHtml(action.priority || "unavailable")}</span><span>优先分 ${action.priority_score == null ? "—" : number(action.priority_score, 1)} · 置信度 ${percent(action.confidence)}</span></div><p>${escapeHtml(action.recommended_action || action.unavailable_reason || "后端分析暂不可用")}</p><p class="mi-context-note">影响 ${action.impact?.score == null ? "—" : number(action.impact.score, 1)} · 可行动性 ${action.actionability?.score == null ? "—" : number(action.actionability.score, 1)} · ${escapeHtml((action.evidence_ids || []).join(" / ") || "无证据编号")}</p></article>`).join("") : '<div class="mi-empty">后端未返回可用行动优先级；不会按表格顺序推断。</div>'}</section></div>`;
  }

  function renderAsinTable() {
    const region = element("mi-asin-table");
    if (!region) return;
    const rows = MI.asinSnapshots.slice(0, 50);
    if (!rows.length) {
      region.innerHTML = '<div class="mi-empty">暂无最新完整日 ASIN 快照。</div>';
      return;
    }
    region.innerHTML = `<table class="mi-table"><thead><tr><th>子体 ASIN</th><th>父 ASIN</th><th>品牌</th><th>总流量</th><th>自然</th><th>广告</th><th>排名</th></tr></thead><tbody>${rows.map((row) => (
      `<tr class="${row.is_own_product ? "mi-own" : ""}" data-mi-asin="${escapeHtml(row.child_asin)}"><td>${escapeHtml(row.child_asin)}</td><td>${escapeHtml(row.parent_asin || "—")}</td><td>${escapeHtml(row.brand || "—")}</td><td>${number(row.total_traffic)}</td><td>${number(row.organic_traffic)}</td><td>${number(row.ad_traffic)}</td><td>${number(row.rank_in_market)}</td></tr>`
    )).join("")}</tbody></table>`;
    region.querySelectorAll("[data-mi-asin]").forEach((row) => {
      row.tabIndex = 0;
      row.addEventListener("click", () => selectAsin(row.dataset.miAsin));
      row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") selectAsin(row.dataset.miAsin); });
    });
  }

  function renderKeywordDashboard() {
    renderKeywordCounts();
    renderKeywordSelector();
    renderKeywordMetricControls();
    renderKeywordChart();
    renderAsinKeywordTable();
    renderKeywordOpportunity();
    renderCompetitionTable();
    renderPersistentError("keyword");
  }

  function renderPersistentError(section) {
    const messages = section === "comparison"
      ? [MI.partialErrors.comparisonLoad, MI.partialErrors.comparisonDetail]
      : [MI.partialErrors.keywordTrend, MI.partialErrors.keywordDetail];
    const message = messages.filter(Boolean).join(" ");
    if (message) setRegionState(section === "comparison" ? "mi-comparison-state" : "mi-keyword-state", "error", message);
  }

  function renderKeywordMetricControls() {
    const region = element("mi-keyword-metrics");
    if (!region) return;
    const metrics = ["search_volume", "category_search_volume", "competitive_difficulty", "organic_scroll_rate"];
    const labels = { search_volume: "搜索量", category_search_volume: "类目搜索量", competitive_difficulty: "竞争难度", organic_scroll_rate: "自然滚动率" };
    region.innerHTML = metrics.map((metric) => {
      const available = MI.keywordTrend.some((row) => row[metric] != null && Number.isFinite(Number(row[metric])));
      return `<button type="button" data-mi-keyword-metric="${metric}" class="${metric === MI.keywordMetric ? "is-active" : ""}" aria-pressed="${metric === MI.keywordMetric}" ${available ? "" : 'disabled title="历史快照暂无此指标"'}>${labels[metric]}</button>`;
    }).join("");
    region.querySelectorAll("[data-mi-keyword-metric]").forEach((button) => button.addEventListener("click", () => {
      MI.keywordMetric = button.dataset.miKeywordMetric;
      renderKeywordDashboard();
    }));
  }

  function renderKeywordCounts() {
    const summary = element("mi-keyword-summary");
    const counts = MI.keywordSummary?.counts || {};
    const levels = MI.keywordSummary?.levels || [];
    queryAll("[data-mi-relevance]").forEach((button) => {
      const level = button.dataset.miRelevance;
      button.classList.toggle("is-active", level === MI.relevance);
      button.setAttribute("aria-pressed", String(level === MI.relevance));
      const strong = button.querySelector("strong");
      if (strong) strong.textContent = number(counts[level] ?? { high: 77, mid: 40, low: 139 }[level]);
    });
    if (!summary) return;
    const row = levels.find((item) => item.relevance_level === MI.relevance) || {};
    const totalVolume = levels.reduce((sum, item) => sum + Number(item.search_volume || 0), 0);
    summary.innerHTML = `<span class="mi-keyword-stat">词数<strong>${number(counts[MI.relevance])}</strong></span>`
      + `<span class="mi-keyword-stat">搜索量<strong>${number(row.search_volume)}</strong></span>`
      + `<span class="mi-keyword-stat">流量占比<strong>${percent(totalVolume ? Number(row.search_volume || 0) / totalVolume : null)}</strong></span>`
      + `<span class="mi-keyword-stat">平均相关度<strong>${number(row.average_relevance_weight, 3)}</strong></span>`;
  }

  function renderKeywordChart() {
    const canvas = element("miKeywordChart");
    if (!canvas || typeof Chart === "undefined") return;
    destroyChart("keyword");
    const keywordRows = MI.keywordTrend.filter((row) => row.is_complete !== false);
    const levelRows = (MI.keywordSummary?.trend || []).filter((row) => row.relevance_level === MI.relevance && row.is_complete !== false);
    const usable = keywordRows.some((row) => row[MI.keywordMetric] != null && Number.isFinite(Number(row[MI.keywordMetric])));
    const aggregateUsable = levelRows.some((row) => row.keyword_traffic != null && Number.isFinite(Number(row.keyword_traffic)));
    if (!usable && !aggregateUsable) {
      setRegionState("mi-keyword-state", "empty", `所选关键词暂无${METRIC_LABELS[MI.keywordMetric] || MI.keywordMetric}历史趋势。`);
      return;
    }
    const stateRegion = element("mi-keyword-state");
    if (stateRegion) {
      if (usable) stateRegion.replaceChildren();
      else setRegionState("mi-keyword-state", "empty", `所选词暂无${METRIC_LABELS[MI.keywordMetric] || MI.keywordMetric}历史；当前显示相关性层级汇总。`);
    }
    const labels = [...new Set([...keywordRows, ...levelRows].map((row) => row.date))].sort();
    const keywordByDate = Object.fromEntries(keywordRows.map((row) => [row.date, row]));
    const levelByDate = Object.fromEntries(levelRows.map((row) => [row.date, row]));
    const datasets = [];
    if (usable) datasets.push({ label: `${MI.keyword} · ${METRIC_LABELS[MI.keywordMetric] || MI.keywordMetric}`, data: labels.map((date) => keywordByDate[date]?.[MI.keywordMetric] ?? null), borderColor: tierColor(MI.relevance), backgroundColor: "transparent", tension: .24 });
    if (aggregateUsable) datasets.push({ label: `${tierLabel(MI.relevance)}相关性层级汇总流量`, data: labels.map((date) => levelByDate[date]?.keyword_traffic ?? null), borderColor: cssToken("--mi-primary"), backgroundColor: "transparent", borderDash: [5, 4], tension: .24 });
    charts.keyword = new Chart(canvas, {
      type: "line",
      data: { labels, datasets },
      options: { responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false }, plugins: { legend: { position: "top" } } },
    });
  }

  function renderAsinKeywordTable() {
    const region = element("mi-asin-keyword-table");
    if (!region) return;
    const rows = MI.asinKeywords.filter((row) => row.relevance_level === MI.relevance).slice(0, 25);
    if (!rows.length) {
      region.innerHTML = `<div class="mi-empty">该代表子体暂无${tierLabel(MI.relevance)}关键词快照。</div>`;
      return;
    }
    region.innerHTML = `<table class="mi-table"><thead><tr><th>关键词</th><th>层级</th><th>流量</th><th>自然位</th><th>广告位</th><th>广告流量</th></tr></thead><tbody>${rows.map((row) => (
      `<tr><td>${escapeHtml(row.keyword)}</td><td><span class="mi-tier ${row.relevance_level}">${tierLabel(row.relevance_level)}</span></td><td>${number(row.keyword_traffic)}</td><td>${number(row.organic_rank)}</td><td>${number(row.ad_rank)}</td><td>${number(row.ad_traffic)}</td></tr>`
    )).join("")}</tbody></table>`;
  }

  function renderKeywordOpportunity() {
    const region = element("mi-keyword-opportunity");
    if (!region) return;
    const term = MI.keywordSearch.trim().toLocaleLowerCase();
    const rows = MI.keywords.filter((row) => !term || `${row.keyword || ""} ${row.translation || ""}`.toLocaleLowerCase().includes(term))
      .sort((left, right) => {
      const direction = MI.keywordSortDirection === "asc" ? 1 : -1;
      return (keywordSortValue(left, MI.keywordSort) - keywordSortValue(right, MI.keywordSort)) * direction;
    }).slice(0, 40);
    if (!rows.length) {
      region.innerHTML = '<div class="mi-empty">当前相关性与搜索条件下没有关键词。</div>';
      return;
    }
    const sortable = (metric, label) => `<button type="button" data-mi-keyword-sort="${metric}" aria-label="按${label}排序">${label}</button>`;
    region.innerHTML = `<h4 class="mi-subsection-title">关键词机会矩阵 · ${tierLabel(MI.relevance)}</h4><p class="mi-context-note">机会分、影响、可行动性与置信度由后端分析层返回；缺失数据保持不可用。</p><div class="mi-table-wrap"><table class="mi-table"><thead><tr><th>关键词</th><th>翻译</th><th>${sortable("search_volume", "搜索量")}</th><th>${sortable("category_search_volume", "类目搜索量")}</th><th>${sortable("relevance_weight", "相关度")}</th><th>${sortable("competitive_difficulty", "竞争难度")}</th><th>${sortable("organic_scroll_rate", "自然滚动率")}</th><th>${sortable("opportunity_demand", "需求分")}</th><th>${sortable("opportunity_attainability", "可行动分")}</th><th>${sortable("opportunity", "机会分")}</th><th>${sortable("opportunity_confidence", "置信度")}</th><th>历史</th></tr></thead><tbody>${rows.map((row) => {
      const expanded = MI.expandedKeyword === row.keyword;
      const opportunity = row.opportunity || {};
      const history = expanded ? `<tr class="mi-keyword-history-row"><td colspan="12">${keywordHistoryMarkup(row.keyword)}</td></tr>` : "";
      return `<tr><td>${escapeHtml(row.keyword)}</td><td>${escapeHtml(row.translation || "—")}</td><td>${number(row.search_volume)}</td><td>${number(row.category_search_volume)}</td><td>${number(row.relevance_weight, 3)}</td><td>${number(row.competitive_difficulty, 1)}</td><td>${percent(row.organic_scroll_rate)}</td><td>${opportunity.dimensions?.demand?.score == null ? "—" : number(opportunity.dimensions.demand.score, 1)}</td><td>${opportunity.dimensions?.attainability?.score == null ? "—" : number(opportunity.dimensions.attainability.score, 1)}</td><td><span class="mi-score-pill">${opportunity.score == null ? "—" : number(opportunity.score, 1)}<small>${escapeHtml(opportunity.impact || opportunity.status || "不可用")}</small></span></td><td><span class="mi-score-pill">${percent(opportunity.confidence)}<small>${escapeHtml(opportunity.actionability || "不可用")}</small></span></td><td><button type="button" data-mi-keyword-history="${escapeHtml(row.keyword)}" aria-expanded="${expanded}">${expanded ? "收起" : "历史趋势"}</button><small>${escapeHtml((opportunity.evidence_ids || []).join(" / ") || opportunity.unavailable_reason || "无证据")}</small></td></tr>${history}`;
    }).join("")}</tbody></table></div>`;
    region.querySelectorAll("[data-mi-keyword-sort]").forEach((button) => button.addEventListener("click", () => {
      const metric = button.dataset.miKeywordSort;
      MI.keywordSortDirection = MI.keywordSort === metric && MI.keywordSortDirection === "desc" ? "asc" : "desc";
      MI.keywordSort = metric;
      renderKeywordOpportunity();
    }));
    region.querySelectorAll("[data-mi-keyword-history]").forEach((button) => button.addEventListener("click", () => {
      const keyword = button.dataset.miKeywordHistory;
      if (MI.expandedKeyword === keyword) {
        MI.expandedKeyword = null;
        renderKeywordOpportunity();
      } else {
        MI.expandedKeyword = keyword;
        if (keyword === MI.keyword) renderKeywordOpportunity();
        else selectKeyword(keyword);
      }
    }));
  }

  function keywordSortValue(row, metric) {
    if (metric === "opportunity") return Number(row.opportunity?.score ?? -1);
    if (metric === "opportunity_confidence") return Number(row.opportunity?.confidence ?? -1);
    if (metric === "opportunity_demand") return Number(row.opportunity?.dimensions?.demand?.score ?? -1);
    if (metric === "opportunity_attainability") return Number(row.opportunity?.dimensions?.attainability?.score ?? -1);
    return Number(row[metric] ?? -1);
  }

  function keywordHistoryMarkup(keyword) {
    if (keyword !== MI.keyword) return '<div class="mi-loading">正在加载该词历史…</div>';
    const rows = MI.keywordTrend.filter((row) => row.is_complete !== false);
    if (!rows.length) return '<div class="mi-empty">该词暂无完整日历史。</div>';
    return `<strong>${escapeHtml(keyword)} 历史明细</strong><table class="mi-table"><thead><tr><th>日期</th><th>${escapeHtml(METRIC_LABELS[MI.keywordMetric] || MI.keywordMetric)}</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${escapeHtml(row.date)}</td><td>${formatMetric(MI.keywordMetric, row[MI.keywordMetric])}</td></tr>`).join("")}</tbody></table>`;
  }

  function renderCompetitionTable() {
    const region = element("mi-keyword-comp-table");
    if (!region) return;
    const rows = MI.competition.filter((row) => !row.relevance_level || row.relevance_level === MI.relevance).slice(0, 30);
    if (!rows.length) {
      region.innerHTML = '<div class="mi-empty">所选关键词暂无竞品矩阵数据。</div>';
      return;
    }
    region.innerHTML = `<table class="mi-table"><thead><tr><th>关键词下子体</th><th>父 ASIN</th><th>品牌</th><th>词下流量</th><th>份额</th><th>自然位</th><th>广告位</th><th>标题</th></tr></thead><tbody>${rows.map((row) => (
      `<tr class="${row.is_own_product ? "mi-own" : ""}"><td>${escapeHtml(row.child_asin)}</td><td>${escapeHtml(row.parent_asin || "—")}</td><td>${escapeHtml(row.brand || "—")}</td><td>${number(row.keyword_traffic)}</td><td>${percent(row.keyword_traffic_share)}</td><td>${number(row.organic_rank)}</td><td>${number(row.ad_rank)}</td><td>${escapeHtml(row.title || "—")}</td></tr>`
    )).join("")}</tbody></table>`;
  }

  function selectAsin(value) {
    if (!value || value === MI.ownAsin) return;
    MI.ownAsin = value;
    MI.competitors = MI.competitors.filter((asin) => asin !== value);
    MI.comparison = null;
    MI.comparisonInsights = [];
    MI.asinSnapshots = [];
    MI.asinTrend = [];
    MI.asinKeywords = [];
    MI.competition = [];
    MI.partialErrors.comparisonLoad = null;
    MI.partialErrors.comparisonDetail = null;
    MI.partialErrors.keywordDetail = null;
    rebuildEvidenceIndex();
    destroyChart("trend");
    destroyChart("comparison");
    renderObjectSelector();
    const { signal, requestToken } = beginGeneration("detail");
    setRegionState("mi-comparison-state", "loading", "正在联动监控对象…");
    setRegionState("mi-trend-state", "loading", "正在联动监控对象…");
    return Promise.allSettled([loadObjectDetail(signal, requestToken), loadComparison(signal, requestToken)])
      .then((results) => {
        if (isCurrentGeneration(requestToken, signal) && results.some((result) => result.status === "rejected")) {
          MI.comparison = null;
          MI.comparisonInsights = [];
          MI.partialErrors.comparisonLoad = "监控对象联动部分失败，可重试或切换对象。";
          rebuildEvidenceIndex();
          destroyChart("comparison");
          destroyChart("trend");
          setRegionState("mi-trend-state", "error", MI.partialErrors.comparisonLoad);
          setRegionState("mi-comparison-state", "error", MI.partialErrors.comparisonLoad);
        }
      });
  }

  function selectKeyword(value) {
    if (!value || value === MI.keyword) return;
    MI.keyword = value;
    MI.expandedKeyword = value;
    MI.keywordTrend = [];
    MI.asinKeywords = [];
    MI.competition = [];
    MI.partialErrors.keywordTrend = null;
    MI.partialErrors.keywordDetail = null;
    destroyChart("keyword");
    const { signal, requestToken } = beginGeneration("keyword");
    setRegionState("mi-keyword-state", "loading", "正在加载关键词历史…");
    return Promise.allSettled([loadObjectDetail(signal, requestToken), loadKeywordTrend(signal, requestToken)])
      .then((results) => {
        if (isCurrentGeneration(requestToken, signal) && results.some((result) => result.status === "rejected")) {
          destroyChart("keyword");
          MI.partialErrors.keywordTrend = "关键词局部数据加载失败，可切换关键词重试。";
          setRegionState("mi-keyword-state", "error", MI.partialErrors.keywordTrend);
        }
      });
  }

  function setScenario(value) {
    if (!SCENARIO_METRICS[value]) return;
    if (value === MI.scenario) return;
    MI.scenario = value;
    MI.metric = SCENARIO_METRICS[value][0];
    renderScenarioControls();
    return refreshComparison();
  }

  function setMetric(value) {
    if (!SCENARIO_METRICS[MI.scenario].includes(value)) return;
    if (MI.comparison?.context?.metric_availability?.[value] === false) return;
    MI.metric = value;
    renderScenarioControls();
    return refreshComparison();
  }

  function setRange(value) {
    if (!["7d", "30d", "custom"].includes(value)) return;
    MI.rangePreset = value;
    if (value !== "custom" && MI.dateTo) applyPresetRange(MI.dateTo);
    renderContext();
    if (value !== "custom") {
      MI.loaded = false;
      load(true);
    }
  }

  function toggleCompetitor(asin) {
    const index = MI.competitors.indexOf(asin);
    if (index >= 0) MI.competitors.splice(index, 1);
    else if (asin && asin !== MI.ownAsin && MI.competitors.length < 3) MI.competitors.push(asin);
    renderCompetitorPicker();
    refreshComparison();
    return MI.competitors.slice();
  }

  function jumpToKeyword(keyword, level) {
    if (level && level !== MI.relevance) return setRelevance(level, keyword);
    return selectKeyword(keyword);
  }

  function setRelevance(level, preferredKeyword = "") {
    if (!RELEVANCE_LEVELS.includes(level) || level === MI.relevance) return;
    MI.relevance = level;
    MI.keywordTrend = [];
    MI.asinKeywords = [];
    MI.competition = [];
    MI.partialErrors.keywordTrend = null;
    MI.partialErrors.keywordDetail = null;
    const { signal, requestToken } = beginGeneration("keyword");
    setRegionState("mi-keyword-state", "loading", "正在切换关键词相关性…");
    return getJson(`/api/market-monitor/keywords?market_id=${encodeURIComponent(MI.marketId)}&relevance_level=${encodeURIComponent(MI.relevance)}${queryDates()}`, signal)
      .then((payload) => {
        if (!isCurrentGeneration(requestToken, signal)) return [];
        MI.keywords = payload.keywords || [];
        MI.keyword = MI.keywords.some((row) => row.keyword === preferredKeyword)
          ? preferredKeyword
          : MI.keywords[0]?.keyword || "";
        renderKeywordDashboard();
        return Promise.allSettled([loadObjectDetail(signal, requestToken), loadKeywordTrend(signal, requestToken)]);
      })
      .then((results) => {
        if (Array.isArray(results) && isCurrentGeneration(requestToken, signal) && results.some((result) => result.status === "rejected")) {
          destroyChart("keyword");
          setRegionState("mi-keyword-state", "error", "相关性已切换，但部分关键词证据加载失败，可重试。");
        }
      })
      .catch((error) => {
        if (error?.name !== "AbortError" && isCurrentGeneration(requestToken, signal)) {
          destroyChart("keyword");
          setRegionState("mi-keyword-state", "error", `相关性切换失败：${error.message}`);
        }
      });
  }

  function refreshComparison() {
    MI.comparison = null;
    MI.comparisonInsights = [];
    MI.partialErrors.comparisonLoad = null;
    rebuildEvidenceIndex();
    destroyChart("comparison");
    destroyChart("trend");
    const { signal, requestToken } = beginGeneration("comparison");
    setRegionState("mi-comparison-state", "loading", "正在更新全局对比上下文…");
    setRegionState("mi-trend-state", "loading", "正在更新全局对比上下文…");
    return loadComparison(signal, requestToken).catch((error) => {
      if (error?.name !== "AbortError" && isCurrentGeneration(requestToken, signal)) {
        MI.comparison = null;
        MI.comparisonInsights = [];
        MI.partialErrors.comparisonLoad = `对比加载失败：${error.message}`;
        rebuildEvidenceIndex();
        destroyChart("comparison");
        destroyChart("trend");
        setRegionState("mi-trend-state", "error", MI.partialErrors.comparisonLoad);
        setRegionState("mi-comparison-state", "error", MI.partialErrors.comparisonLoad);
      }
    });
  }

  function chooseObject(asin) {
    closeObjectList();
    selectAsin(asin);
  }

  function openObjectList() {
    const trigger = element("mi-object-trigger");
    const options = element("mi-object-options");
    if (!trigger || !options) return;
    options.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    const selected = options.querySelector('[aria-selected="true"]') || options.querySelector('[role="option"]');
    selected?.focus();
  }

  function closeObjectList() {
    const trigger = element("mi-object-trigger");
    const options = element("mi-object-options");
    if (!trigger || !options) return;
    options.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    trigger.focus();
  }

  function handleObjectOptionKeydown(event) {
    const options = [...element("mi-object-options").querySelectorAll('[role="option"]')];
    const index = options.indexOf(event.currentTarget);
    if (event.key === "ArrowDown") {
      event.preventDefault();
      options[(index + 1) % options.length]?.focus();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      options[(index - 1 + options.length) % options.length]?.focus();
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      chooseObject(event.currentTarget.dataset.asin);
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeObjectList();
    }
  }

  function bindEvents() {
    if (MI.eventsBound) return;
    MI.eventsBound = true;
    element("mi-market")?.addEventListener("change", () => {
      MI.loaded = false;
      if (MI.rangePreset !== "custom") {
        MI.dateFrom = null;
        MI.dateTo = null;
      }
      load(true);
    });
    element("mi-refresh")?.addEventListener("click", () => load(true));
    element("mi-keyword")?.addEventListener("change", (event) => selectKeyword(event.target.value));
    element("mi-keyword-search")?.addEventListener("input", (event) => {
      MI.keywordSearch = event.target.value;
      renderKeywordOpportunity();
    });
    element("mi-object-trigger")?.addEventListener("click", () => {
      if (element("mi-object-options")?.hidden) openObjectList(); else closeObjectList();
    });
    element("mi-object-trigger")?.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        openObjectList();
      } else if (event.key === "Escape") closeObjectList();
    });
    element("mi-evidence-close")?.addEventListener("click", closeEvidence);
    queryAll("[data-mi-range]").forEach((button) => button.addEventListener("click", () => setRange(button.dataset.miRange)));
    queryAll("[data-mi-relevance]").forEach((button) => button.addEventListener("click", () => setRelevance(button.dataset.miRelevance)));
    ["mi-date-from", "mi-date-to"].forEach((id) => element(id)?.addEventListener("change", () => {
      const from = element("mi-date-from")?.value || null;
      const to = element("mi-date-to")?.value || null;
      if (from && to && from <= to) {
        MI.dateFrom = from;
        MI.dateTo = to;
        MI.loaded = false;
        load(true);
      }
    }));
    document.addEventListener?.("keydown", (event) => { if (event.key === "Escape" && MI.selectedEvidenceId) closeEvidence(); });
  }

  function destroyChart(name) {
    if (charts[name]) {
      charts[name].destroy();
      delete charts[name];
    }
  }

  function cssToken(name) {
    if (typeof getComputedStyle === "undefined") return "#356ae6";
    return getComputedStyle(element("tab-mi") || document.documentElement).getPropertyValue(name).trim() || "#356ae6";
  }

  function competitorColor(asin) {
    const index = Math.max(0, MI.competitors.indexOf(asin));
    return cssToken(`--mi-competitor-${Math.min(index + 1, 3)}`);
  }

  function tierColor(level) {
    return cssToken(level === "high" ? "--mi-high" : level === "mid" ? "--mi-mid" : "--mi-low");
  }

  function tierLabel(level) {
    return level === "high" ? "高相关" : level === "mid" ? "中相关" : "低相关";
  }

  window.MarketMonitor = Object.freeze({
    load,
    selectAsin,
    selectKeyword,
    setScenario,
    setMetric,
    setRange,
    toggleCompetitor,
  });

  setTimeout(() => load(), 0);
})();
