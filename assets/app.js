/**
 * kj-news-radar · 跨境电商新闻雷达前端
 *
 * 功能：
 *   1. 加载 AI 筛选信号 / 全量信号 / 来源健康状态 / 政策日历
 *   2. 事件聚类（跨源去重、按事件维度聚合）
 *   3. 多维筛选：来源、影响维度、紧急程度、关键词搜索
 *   4. 双视图模式：cross（AI筛选）/ all（全量）
 */

/* ========== 常量 ========== */

/** 来源分类映射 */
const SOURCE_KINDS = {
  amazon_official:    { label: "亚马逊官方", tone: "official" },
  amazon_ads:         { label: "亚马逊广告", tone: "official" },
  amazon_newsroom:    { label: "亚马逊新闻", tone: "official" },
  sp_api:             { label: "SP-API",    tone: "official" },
  gs_amazon:          { label: "全球开店",  tone: "official" },
  amz123:             { label: "AMZ123",    tone: "aggregate" },
  amzdh:              { label: "AMZDH",     tone: "aggregate" },
  cifnews:            { label: "雨果跨境",  tone: "aggregate" },
  ecombrainly:        { label: "EcomBrainly", tone: "blogs" },
  novadata:           { label: "NovaData",  tone: "blogs" },
  helium10:           { label: "Helium10",  tone: "industry" },
  sellerpolicywatch:  { label: "政策监控",  tone: "official" },
  ecomengine:         { label: "EcomEngine", tone: "industry" },
  wearesellers:       { label: "WeAreSellers", tone: "community" },
  podcasts:           { label: "播客",      tone: "media" },
  opmlrss:            { label: "OPML",      tone: "private" },
};

/** 影响维度标签 */
const LABELS = {
  policy_update:    "政策变动",
  fee_logistics:    "费用物流",
  advertising:      "广告运营",
  listing_product:  "选品上架",
  platform_trend:   "平台趋势",
  seller_action:    "紧急行动",
  general:          "行业资讯",
};

/** 影响维度对应的 emoji 颜色 */
const LABEL_EMOJI = {
  policy_update:   "📋",
  fee_logistics:   "📦",
  advertising:     "📢",
  listing_product: "🏷️",
  platform_trend:  "📈",
  seller_action:   "⚡",
  general:         "📰",
};

/* ========== 全局状态 ========== */

const state = {
  itemsAi: [],
  itemsAll: [],
  itemsAllRaw: [],
  statsAi: [],
  totalAi: 0,
  totalRaw: 0,
  totalAllMode: 0,
  allDedup: true,
  allDataLoaded: false,
  allDataUrl: "data/latest-24h-all.json",
  allDataPromise: null,
  siteFilter: "",
  impactFilter: "",      // 影响维度筛选
  urgencyFilter: "",     // 紧急程度筛选
  query: "",
  mode: "cross",         // 'cross' | 'all'
  policyData: null,
  sourceStatus: null,
  generatedAt: null,
};

/* ========== 工具函数 ========== */

/**
 * 数字格式化（千分位）
 * @param {number} n
 * @returns {string}
 */
function fmtNumber(n) {
  if (n == null) return "—";
  return Number(n).toLocaleString("zh-CN");
}

/**
 * 时间格式化 → "6月26日 14:30"
 * @param {string} isoStr
 * @returns {string}
 */
function fmtTime(isoStr) {
  if (!isoStr) return "—";
  const d = new Date(isoStr);
  if (isNaN(d)) return "—";
  const m = d.getMonth() + 1;
  const day = d.getDate();
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${m}月${day}日 ${hh}:${mm}`;
}

/**
 * 日期格式化 → "2026-06-26"
 * @param {string} isoStr
 * @returns {string}
 */
function fmtDate(isoStr) {
  if (!isoStr) return "—";
  const d = new Date(isoStr);
  if (isNaN(d)) return "—";
  return d.toISOString().slice(0, 10);
}

/**
 * 相对时间 "3小时前"
 * @param {string} isoStr
 * @returns {string}
 */
function timeAgo(isoStr) {
  if (!isoStr) return "";
  const diff = Date.now() - new Date(isoStr).getTime();
  if (diff < 0) return "刚刚";
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins}分钟前`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}小时前`;
  const days = Math.floor(hrs / 24);
  return `${days}天前`;
}

/**
 * 转义 HTML 特殊字符
 * @param {string} s
 * @returns {string}
 */
function esc(s) {
  if (!s) return "";
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * 安全获取 DOM 元素
 * @param {string} id
 * @returns {HTMLElement|null}
 */
function $(id) {
  return document.getElementById(id);
}

/**
 * 影响等级对应紧急程度
 * @param {number} score - cross_score (0-1)
 * @returns {{ level: string, emoji: string, label: string }}
 */
function urgencyOf(score) {
  if (score >= 0.85) return { level: "high", emoji: "🔴", label: "高影响" };
  if (score >= 0.70) return { level: "mid", emoji: "🟡", label: "中影响" };
  return { level: "low", emoji: "🟢", label: "低影响" };
}

/**
 * 来源标签 (site_name)
 * @param {string} siteId
 * @returns {string}
 */
function sourceLabel(siteId) {
  const kind = SOURCE_KINDS[siteId];
  return kind ? kind.label : siteId || "未知";
}

/**
 * 来源色调类名
 * @param {string} siteId
 * @returns {string}
 */
function sourceTone(siteId) {
  const kind = SOURCE_KINDS[siteId];
  return kind ? kind.tone : "general";
}

/* ========== 统计卡片渲染 ========== */

/**
 * 渲染顶部统计卡片
 */
function setStats() {
  const el = $("stats");
  if (!el) return;

  const ai = state.totalAi;
  const raw = state.totalRaw;
  const sites = state.statsAi.length;
  const filtered = state.mode === "cross" ? ai : (state.allDedup ? state.itemsAll.length : state.totalAllMode);

  el.innerHTML = `
    <div class="stat-card">
      <div class="stat-num">${fmtNumber(filtered)}</div>
      <div class="stat-label">${state.mode === "cross" ? "AI筛选信号" : "全量信号"}</div>
    </div>
    <div class="stat-card">
      <div class="stat-num">${fmtNumber(raw)}</div>
      <div class="stat-label">原始采集量</div>
    </div>
    <div class="stat-card">
      <div class="stat-num">${fmtNumber(sites)}</div>
      <div class="stat-label">活跃来源</div>
    </div>
    <div class="stat-card">
      <div class="stat-num">${state.generatedAt ? fmtTime(state.generatedAt) : "—"}</div>
      <div class="stat-label">数据更新</div>
    </div>
  `;
}

/* ========== 来源健康条 ========== */

/**
 * 渲染数据源健康状态条
 */
function renderCoverageStrip() {
  const el = $("sourceHealth");
  if (!el || !state.sourceStatus) return;

  const sources = state.sourceStatus;
  const total = Object.keys(sources).length;
  const ok = Object.values(sources).filter(s => s.status === "ok").length;

  let html = `<div class="coverage-strip"><span class="coverage-badge">${ok}/${total} 来源在线</span>`;

  for (const [id, info] of Object.entries(sources)) {
    const cls = info.status === "ok" ? "cov-ok" : "cov-err";
    const tip = `${sourceLabel(id)}: ${info.status}${info.last_item_at ? " · 最近 " + timeAgo(info.last_item_at) : ""}`;
    html += `<span class="cov-dot ${cls}" title="${esc(tip)}"></span>`;
  }

  html += `</div>`;
  el.innerHTML = html;
}

/* ========== 来源筛选栏 ========== */

/**
 * 渲染来源筛选按钮（pill 样式）
 */
function renderSiteFilters() {
  const wrap = $("sitePills");
  if (!wrap) return;

  // 收集当前数据中出现的来源
  const items = state.mode === "cross" ? state.itemsAi : state.itemsAll;
  const siteMap = new Map();

  for (const it of items) {
    const sid = it.site_id || "unknown";
    if (!siteMap.has(sid)) {
      siteMap.set(sid, { count: 0, name: it.site_name || sourceLabel(sid) });
    }
    siteMap.get(sid).count++;
  }

  // 按数量降序
  const sorted = [...siteMap.entries()].sort((a, b) => b[1].count - a[1].count);

  let html = `<button class="pill ${state.siteFilter === '' ? 'pill-active' : ''}" data-site="">全部来源</button>`;
  for (const [sid, info] of sorted) {
    const active = state.siteFilter === sid ? "pill-active" : "";
    const tone = sourceTone(sid);
    html += `<button class="pill pill-${tone} ${active}" data-site="${esc(sid)}">${esc(info.name)} <span class="pill-count">${info.count}</span></button>`;
  }

  wrap.innerHTML = html;

  // 事件委托
  wrap.onclick = (e) => {
    const btn = e.target.closest("[data-site]");
    if (!btn) return;
    state.siteFilter = btn.dataset.site;
    renderSiteFilters();
    renderList();
  };
}

/* ========== 影响维度筛选 ========== */

/**
 * 渲染影响维度筛选按钮
 */
function renderImpactFilter() {
  const wrap = $("impactPills");
  if (!wrap) return;

  let html = `<button class="pill ${state.impactFilter === '' ? 'pill-active' : ''}" data-impact="">全部维度</button>`;
  for (const [key, label] of Object.entries(LABELS)) {
    const active = state.impactFilter === key ? "pill-active" : "";
    const emoji = LABEL_EMOJI[key] || "";
    html += `<button class="pill ${active}" data-impact="${esc(key)}">${emoji} ${esc(label)}</button>`;
  }

  wrap.innerHTML = html;

  wrap.onclick = (e) => {
    const btn = e.target.closest("[data-impact]");
    if (!btn) return;
    state.impactFilter = btn.dataset.impact;
    renderImpactFilter();
    renderList();
  };
}

/* ========== 紧急程度筛选 ========== */

/**
 * 渲染紧急程度筛选按钮
 */
function renderUrgencyFilter() {
  const wrap = $("urgencyPills");
  if (!wrap) return;

  const levels = [
    { key: "",    emoji: "",   label: "全部等级" },
    { key: "high", emoji: "🔴", label: "高影响" },
    { key: "mid",  emoji: "🟡", label: "中影响" },
    { key: "low",  emoji: "🟢", label: "低影响" },
  ];

  let html = "";
  for (const lv of levels) {
    const active = state.urgencyFilter === lv.key ? "pill-active" : "";
    html += `<button class="pill ${active}" data-urgency="${esc(lv.key)}">${lv.emoji} ${esc(lv.label)}</button>`;
  }

  wrap.innerHTML = html;

  wrap.onclick = (e) => {
    const btn = e.target.closest("[data-urgency]");
    if (!btn) return;
    state.urgencyFilter = btn.dataset.urgency;
    renderUrgencyFilter();
    renderList();
  };
}

/* ========== 视图模式切换 ========== */

/**
 * 渲染 cross / all 模式切换按钮
 */
function renderModeSwitch() {
  const crossBtn = $("modeCrossBtn");
  const allBtn = $("modeAllBtn");
  const hint = $("modeHint");
  const dedupeWrap = $("allDedupeWrap");

  if (crossBtn) {
    crossBtn.classList.toggle("mode-active", state.mode === "cross");
    crossBtn.onclick = () => switchMode("cross");
  }
  if (allBtn) {
    allBtn.classList.toggle("mode-active", state.mode === "all");
    allBtn.onclick = () => switchMode("all");
  }

  if (hint) {
    hint.textContent = state.mode === "cross"
      ? "仅展示 AI 跨境相关性筛选后的信号"
      : "展示所有采集到的信号（含本地资讯）";
  }

  // 全量模式下显示去重开关
  if (dedupeWrap) {
    dedupeWrap.style.display = state.mode === "all" ? "" : "none";
  }
}

/**
 * 切换视图模式
 * @param {"cross"|"all"} mode
 */
function switchMode(mode) {
  if (state.mode === mode) return;
  state.mode = mode;
  state.siteFilter = "";
  state.impactFilter = "";
  state.urgencyFilter = "";
  state.query = "";

  const searchInput = $("searchInput");
  if (searchInput) searchInput.value = "";

  // 全量模式 → 懒加载数据
  if (mode === "all" && !state.allDataLoaded) {
    loadAllData();
  }

  renderAll();
}

/**
 * 懒加载全量数据
 */
function loadAllData() {
  if (state.allDataPromise) return state.allDataPromise;

  state.allDataPromise = fetch(state.allDataUrl)
    .then(r => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    })
    .then(data => {
      state.itemsAllRaw = Array.isArray(data.items) ? data.items : [];
      state.totalAllMode = state.itemsAllRaw.length;
      state.itemsAll = dedupItems(state.itemsAllRaw);
      state.allDataLoaded = true;
      renderAll();
    })
    .catch(err => {
      console.error("加载全量数据失败:", err);
      state.allDataLoaded = true; // 标记已尝试
    });

  return state.allDataPromise;
}

/**
 * 按标题 + 来源去重
 * @param {Array} items
 * @returns {Array}
 */
function dedupItems(items) {
  const seen = new Set();
  return items.filter(it => {
    const key = `${(it.title || "").trim().toLowerCase()}|${it.site_id || ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/* ========== 综合筛选 ========== */

/**
 * 根据当前所有筛选条件获取可见条目
 * @returns {Array}
 */
function getFilteredItems() {
  let items = state.mode === "cross" ? [...state.itemsAi] : (state.allDedup ? [...state.itemsAll] : [...state.itemsAllRaw]);

  // 来源筛选
  if (state.siteFilter) {
    items = items.filter(it => it.site_id === state.siteFilter);
  }

  // 影响维度筛选
  if (state.impactFilter) {
    items = items.filter(it => it.cross_label === state.impactFilter);
  }

  // 紧急程度筛选
  if (state.urgencyFilter) {
    items = items.filter(it => {
      const u = urgencyOf(it.cross_score || 0).level;
      return u === state.urgencyFilter;
    });
  }

  // 关键词搜索
  if (state.query) {
    const q = state.query.toLowerCase();
    items = items.filter(it => {
      const hay = `${it.title || ""} ${it.cross_relevance_reason || ""} ${(it.cross_signals || []).join(" ")}`.toLowerCase();
      return hay.includes(q);
    });
  }

  // 按 cross_score 降序，再按时间降序
  items.sort((a, b) => {
    const sd = (b.cross_score || 0) - (a.cross_score || 0);
    if (Math.abs(sd) > 0.01) return sd;
    return new Date(b.published_at || 0) - new Date(a.published_at || 0);
  });

  return items;
}

/* ========== 新闻列表渲染 ========== */

/**
 * 渲染新闻列表
 */
function renderList() {
  const listEl = $("newsList");
  const countEl = $("resultCount");
  const titleEl = $("listTitle");
  if (!listEl) return;

  const items = getFilteredItems();

  if (countEl) countEl.textContent = `${items.length} 条`;
  if (titleEl) titleEl.textContent = state.mode === "cross" ? "AI 跨境信号" : "全量信号";

  if (items.length === 0) {
    listEl.innerHTML = `<div class="empty-state">暂无匹配的信号，请调整筛选条件</div>`;
    return;
  }

  let html = "";
  for (const it of items) {
    const urg = urgencyOf(it.cross_score || 0);
    const label = LABELS[it.cross_label] || "行业资讯";
    const labelEmoji = LABEL_EMOJI[it.cross_label] || "📰";
    const signals = (it.cross_signals || []).map(s => `<span class="signal-tag">${esc(s)}</span>`).join("");
    const reason = it.cross_relevance_reason ? `<div class="item-reason">${esc(it.cross_relevance_reason)}</div>` : "";
    const toneCls = sourceTone(it.site_id);

    html += `
    <article class="news-item" data-id="${esc(it.id)}">
      <div class="item-header">
        <span class="urgency-dot" title="${urg.label}">${urg.emoji}</span>
        <span class="label-badge label-${esc(it.cross_label || 'general')}">${labelEmoji} ${esc(label)}</span>
        <span class="source-badge tone-${toneCls}">${esc(it.site_name || sourceLabel(it.site_id))}</span>
        <span class="item-score" title="跨境相关性分数">${((it.cross_score || 0) * 100).toFixed(0)}%</span>
        <span class="item-time" title="${esc(it.published_at)}">${timeAgo(it.published_at)}</span>
      </div>
      <h3 class="item-title">
        <a href="${esc(it.url)}" target="_blank" rel="noopener">${esc(it.title)}</a>
      </h3>
      ${reason}
      ${signals ? `<div class="item-signals">${signals}</div>` : ""}
    </article>`;
  }

  listEl.innerHTML = html;
}

/* ========== 事件聚类（精选推荐） ========== */

/**
 * 标准化标题用于聚类：
 *   - 去除标点、数字前缀
 *   - 提取平台实体 + 关键词组合
 * @param {string} title
 * @returns {string}
 */
function normalizeTitle(title) {
  if (!title) return "";
  return title
    .replace(/[\s\-_—–·|｜:：,，。.!！?？""''""【】\[\]（）(){}]+/g, " ")
    .replace(/^\d+\s*/, "")
    .trim()
    .toLowerCase()
    .slice(0, 80);
}

/**
 * 从标题中提取事件实体 key（平台 + 政策关键词）
 * @param {string} title
 * @returns {string}
 */
function extractEventKey(title) {
  const t = (title || "").toLowerCase();

  // 平台实体
  const platforms = ["amazon", "亚马逊", "temu", "shein", "tiktok", "walmart", "ebay", "shopee", "lazada", "速卖通", "aliexpress"];
  let platform = "";
  for (const p of platforms) {
    if (t.includes(p)) { platform = p; break; }
  }

  // 关键词实体
  const keywords = [
    "政策", "policy", "费率", "fee", "佣金", "commission",
    "仓储", "fba", "物流", "logistics", "配送",
    "广告", "advertising", "ppc", "广告费",
    "上架", "listing", "选品", "产品",
    "封号", "冻结", "审核", "compliance",
    "旺季", "prime", "黑五", "black friday",
  ];
  let keyword = "";
  for (const k of keywords) {
    if (t.includes(k)) { keyword = k; break; }
  }

  return `${platform}|${keyword}`;
}

/**
 * 事件聚类：从 AI 筛选结果中挑选高质量跨源事件
 * @param {Array} items - AI 筛选后的条目
 * @param {number} maxPicks - 最多挑选数
 * @returns {Array} 聚类后的事件数组
 */
function pickCrossItems(items, maxPicks = 8) {
  // 按事件 key 分组
  const clusters = new Map();

  for (const it of items) {
    const key = extractEventKey(it.title);
    if (!key || key === "|") continue; // 无法归类的跳过

    if (!clusters.has(key)) {
      clusters.set(key, { items: [], sources: new Set(), maxScore: 0 });
    }
    const cluster = clusters.get(key);
    cluster.items.push(it);
    cluster.sources.add(it.site_id || "unknown");
    cluster.maxScore = Math.max(cluster.maxScore, it.cross_score || 0);
  }

  // 排序：来源数降序 → cross_score 降序 → 时间降序
  const sorted = [...clusters.entries()]
    .map(([key, cluster]) => ({
      key,
      items: cluster.items,
      sourceCount: cluster.sources.size,
      maxScore: cluster.maxScore,
      latestTime: Math.max(...cluster.items.map(i => new Date(i.published_at || 0).getTime())),
    }))
    .filter(c => c.sourceCount >= 1) // 至少1个不同来源
    .sort((a, b) => {
      // 多源优先
      const sd = b.sourceCount - a.sourceSourceCount;
      if (b.sourceCount !== a.sourceCount) return b.sourceCount - a.sourceCount;
      // 分数
      if (Math.abs(b.maxScore - a.maxScore) > 0.01) return b.maxScore - a.maxScore;
      // 时间
      return b.latestTime - a.latestTime;
    })
    .slice(0, maxPicks);

  // 为每个 cluster 选择代表性条目（分数最高的）
  return sorted.map(c => {
    const representative = c.items.sort((a, b) => (b.cross_score || 0) - (a.cross_score || 0))[0];
    return {
      ...representative,
      _clusterSize: c.items.length,
      _sourceCount: c.sourceCount,
      _allItems: c.items,
    };
  });
}

/**
 * 渲染精选推荐（事件聚类）
 */
function renderCrossPicks() {
  const listEl = $("crossPicksList");
  const metaEl = $("crossPicksMeta");
  if (!listEl) return;

  const picks = pickCrossItems(state.itemsAi);

  if (metaEl) {
    metaEl.textContent = picks.length > 0
      ? `基于 ${state.itemsAi.length} 条信号，聚类出 ${picks.length} 个跨源事件`
      : "暂无足够数据进行事件聚类";
  }

  if (picks.length === 0) {
    listEl.innerHTML = `<div class="empty-state">暂无跨源事件推荐</div>`;
    return;
  }

  let html = "";
  for (const pick of picks) {
    const urg = urgencyOf(pick.cross_score || 0);
    const label = LABELS[pick.cross_label] || "行业资讯";
    const labelEmoji = LABEL_EMOJI[pick.cross_label] || "📰";
    const reason = pick.cross_relevance_reason ? `<div class="pick-reason">${esc(pick.cross_relevance_reason)}</div>` : "";

    // 聚类来源标签
    const sourceTags = (pick._allItems || [])
      .map(i => i.site_name || sourceLabel(i.site_id))
      .filter((v, idx, arr) => arr.indexOf(v) === idx)
      .map(name => `<span class="cluster-source">${esc(name)}</span>`)
      .join("");

    html += `
    <div class="pick-card">
      <div class="pick-header">
        <span class="urgency-dot">${urg.emoji}</span>
        <span class="label-badge label-${esc(pick.cross_label || 'general')}">${labelEmoji} ${esc(label)}</span>
        <span class="cluster-badge" title="跨源事件 · ${pick._sourceCount} 个不同来源">${pick._sourceCount} 源</span>
      </div>
      <h3 class="pick-title">
        <a href="${esc(pick.url)}" target="_blank" rel="noopener">${esc(pick.title)}</a>
      </h3>
      ${reason}
      <div class="pick-sources">${sourceTags}</div>
    </div>`;
  }

  listEl.innerHTML = html;
}

/* ========== 政策日历 ========== */

/**
 * 渲染政策日历模块
 */
function renderPolicyCalendar() {
  const listEl = $("policyCalendarList");
  const metaEl = $("policyCalendarMeta");
  if (!listEl) return;

  if (!state.policyData || !Array.isArray(state.policyData) || state.policyData.length === 0) {
    if (metaEl) metaEl.textContent = "暂无政策日历数据";
    listEl.innerHTML = `<div class="empty-state">暂无即将生效的政策变更</div>`;
    return;
  }

  // 按生效日期升序排列
  const sorted = [...state.policyData].sort((a, b) => {
    return new Date(a.effective_date || 0) - new Date(b.effective_date || 0);
  });

  if (metaEl) {
    metaEl.textContent = `共 ${sorted.length} 项即将生效的政策变更`;
  }

  let html = "";
  for (const policy of sorted) {
    const effectiveDate = fmtDate(policy.effective_date);
    const daysLeft = Math.ceil((new Date(policy.effective_date) - Date.now()) / 86400000);
    const urgencyCls = daysLeft <= 7 ? "policy-urgent" : daysLeft <= 30 ? "policy-warn" : "policy-normal";
    const daysLabel = daysLeft <= 0 ? "已生效" : `${daysLeft} 天后生效`;

    const platforms = (policy.affected_platforms || [])
      .map(p => `<span class="policy-platform">${esc(p)}</span>`)
      .join("");

    html += `
    <div class="policy-card ${urgencyCls}">
      <div class="policy-header">
        <span class="policy-date">${effectiveDate}</span>
        <span class="policy-countdown">${daysLabel}</span>
        ${policy.impact_level ? `<span class="policy-impact">${esc(policy.impact_level)}</span>` : ""}
      </div>
      <h4 class="policy-title">${esc(policy.title)}</h4>
      ${policy.description ? `<p class="policy-desc">${esc(policy.description)}</p>` : ""}
      ${platforms ? `<div class="policy-platforms">${platforms}</div>` : ""}
    </div>`;
  }

  listEl.innerHTML = html;
}

/* ========== 高级摘要 ========== */

/**
 * 渲染高级筛选摘要
 */
function renderAdvancedSummary() {
  const el = $("advancedSummary");
  if (!el) return;

  const parts = [];
  if (state.siteFilter) parts.push(`来源: ${sourceLabel(state.siteFilter)}`);
  if (state.impactFilter) parts.push(`维度: ${LABELS[state.impactFilter] || state.impactFilter}`);
  if (state.urgencyFilter) {
    const map = { high: "🔴高影响", mid: "🟡中影响", low: "🟢低影响" };
    parts.push(`等级: ${map[state.urgencyFilter] || state.urgencyFilter}`);
  }
  if (state.query) parts.push(`搜索: "${state.query}"`);

  el.textContent = parts.length > 0 ? parts.join(" · ") : "";
}

/* ========== 更新时间 ========== */

/**
 * 渲染更新时间
 */
function renderUpdatedAt() {
  const el = $("updatedAt");
  if (!el) return;
  el.textContent = state.generatedAt ? `数据更新于 ${fmtTime(state.generatedAt)}` : "";
}

/* ========== 搜索绑定 ========== */

function bindSearch() {
  const input = $("searchInput");
  if (!input) return;

  let timer = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      state.query = input.value.trim();
      renderList();
      renderAdvancedSummary();
    }, 200);
  });
}

/* ========== 去重开关绑定 ========== */

function bindDedupeToggle() {
  const toggle = $("allDedupeToggle");
  const label = $("allDedupeLabel");
  if (!toggle) return;

  toggle.checked = state.allDedup;
  if (label) label.textContent = state.allDedup ? "已去重" : "显示全部";

  toggle.addEventListener("change", () => {
    state.allDedup = toggle.checked;
    if (label) label.textContent = state.allDedup ? "已去重" : "显示全部";
    renderList();
    setStats();
  });
}

/* ========== 综合渲染 ========== */

/**
 * 重新渲染所有 UI 组件
 */
function renderAll() {
  setStats();
  renderCoverageStrip();
  renderModeSwitch();
  renderSiteFilters();
  renderImpactFilter();
  renderUrgencyFilter();
  renderList();
  renderCrossPicks();
  renderPolicyCalendar();
  renderAdvancedSummary();
  renderUpdatedAt();
}

/* ========== 初始化 ========== */

/**
 * 应用入口：加载数据并渲染
 */
async function init() {
  try {
    // 并行加载 AI 数据、来源状态、政策日历
    const [aiRes, statusRes, policyRes] = await Promise.allSettled([
      fetch("data/latest-24h.json").then(r => {
        if (!r.ok) throw new Error(`AI data HTTP ${r.status}`);
        return r.json();
      }),
      fetch("data/source-status.json").then(r => {
        if (!r.ok) throw new Error(`Source status HTTP ${r.status}`);
        return r.json();
      }),
      fetch("data/policy-calendar.json").then(r => {
        if (!r.ok) throw new Error(`Policy calendar HTTP ${r.status}`);
        return r.json();
      }),
    ]);

    // 处理 AI 数据
    if (aiRes.status === "fulfilled" && aiRes.value) {
      const data = aiRes.value;
      state.itemsAi = Array.isArray(data.items) ? data.items : [];
      state.statsAi = Array.isArray(data.stats) ? data.stats : [];
      state.totalAi = data.total_ai || state.itemsAi.length;
      state.totalRaw = data.total_raw || state.totalAi;
      state.generatedAt = data.generated_at || null;
    } else {
      console.error("加载 AI 数据失败:", aiRes.reason);
    }

    // 处理来源状态
    if (statusRes.status === "fulfilled" && statusRes.value) {
      state.sourceStatus = statusRes.value;
    } else {
      console.warn("加载来源状态失败:", statusRes.reason);
    }

    // 处理政策日历
    if (policyRes.status === "fulfilled" && policyRes.value) {
      state.policyData = Array.isArray(policyRes.value) ? policyRes.value : (policyRes.value.policies || []);
    } else {
      console.warn("加载政策日历失败:", policyRes.reason);
    }

    // 首次渲染
    renderAll();

    // 绑定交互
    bindSearch();
    bindDedupeToggle();

    // 预加载全量数据（后台，不阻塞）
    setTimeout(() => loadAllData(), 3000);

  } catch (err) {
    console.error("初始化失败:", err);
    const listEl = $("newsList");
    if (listEl) listEl.innerHTML = `<div class="empty-state">数据加载失败，请刷新页面重试</div>`;
  }
}

// DOM 就绪后启动
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
