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
  helium10:           { label: "Helium10",  tone: "industry" },
  sellerpolicywatch:  { label: "政策监控",  tone: "official" },
  ecomengine:         { label: "EcomEngine", tone: "industry" },
  amazon_seller_news: { label: "卖家新闻", tone: "official" },
  amazon_seller_blog: { label: "Amazon博客", tone: "official" },
  wearesellers:       { label: "知无不言", tone: "community" },
  kjds365:            { label: "跨境365", tone: "aggregate" },
  tophub:             { label: "TopHub", tone: "aggregate" },
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
  platformFilter: "",    // 平台维度筛选
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
  const sites = state.sourceStatus && Array.isArray(state.sourceStatus.sites)
    ? state.sourceStatus.sites.filter(s => s.ok === true).length
    : 0;

  el.innerHTML = `
    <span class="stat-pill"><span class="v">${fmtNumber(ai)}</span><span class="k">信号</span></span>
    <span class="stat-pill"><span class="v">${fmtNumber(sites)}</span><span class="k">源</span></span>
  `;
}

/* ========== 来源健康条 ========== */

/**
 * 渲染数据源健康状态条
 */
function renderCoverageStrip() {
  return;
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

  let html = `<button class="pill ${state.siteFilter === '' ? 'active' : ''}" data-site="">全部来源</button>`;
  for (const [sid, info] of sorted) {
    const active = state.siteFilter === sid ? "active" : "";
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
    // 跳到信号流
    const target = $("sectionSignal");
    if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
  };
}

/* ========== 影响维度筛选 ========== */

/**
 * 渲染影响维度筛选按钮
 */
function renderImpactFilter() {
  const wrap = $("impactPills");
  if (!wrap) return;

  let html = `<button class="pill ${state.impactFilter === '' ? 'active' : ''}" data-impact="">全部维度</button>`;
  for (const [key, label] of Object.entries(LABELS)) {
    const active = state.impactFilter === key ? "active" : "";
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
    renderCrossPicks(); // 精选也按维度过滤
  };
}


/* ========== 平台筛选 ========== */

/**
 * 渲染平台筛选按钮
 */
function renderPlatformFilter() {
  const wrap = $("platformPills");
  if (!wrap) return;
  
  // Count platforms from items
  const platMap = new Map();
  const items = state.mode === "cross" ? state.itemsAi : (state.allDedup ? state.itemsAll : state.itemsAllRaw);
  items.forEach(it => {
    (it.cross_platforms || []).forEach(p => {
      platMap.set(p, (platMap.get(p) || 0) + 1);
    });
  });
  
  wrap.innerHTML = "";
  const allBtn = document.createElement("button");
  allBtn.className = `pill ${state.platformFilter === "" ? "active" : ""}`;
  allBtn.textContent = "全部";
  allBtn.onclick = () => { state.platformFilter = ""; renderPlatformFilter(); renderList(); };
  wrap.appendChild(allBtn);
  
  [...platMap.entries()].sort((a,b) => b[1]-a[1]).slice(0, 10).forEach(([plat, count]) => {
    const btn = document.createElement("button");
    btn.className = `pill ${state.platformFilter === plat ? "active" : ""}`;
    btn.textContent = `${plat} ${count}`;
    btn.onclick = () => { state.platformFilter = plat; renderPlatformFilter(); renderList(); };
    wrap.appendChild(btn);
  });
}

/* ========== 行动清单 ========== */

/**
 * 渲染行动清单
 */
function renderActionItems() {
  const listEl = $("actionList");
  const metaEl = $("actionMeta");
  if (!listEl) return;
  
  // Build action items from policy calendar + high-score signals
  const actions = [];
  const now = Date.now();
  
  // From policy calendar
  (state.policyData || []).forEach(p => {
    const deadline = new Date(p.effective_date);
    const daysLeft = Math.ceil((deadline - now) / 86400000);
    if (daysLeft > 60) return; // Skip far-future
    
    const urgency = daysLeft <= 0 ? "done" : daysLeft <= 7 ? "high" : daysLeft <= 30 ? "medium" : "low";
    if (urgency === "done") return;
    
    actions.push({
      title: p.title,
      detail: p.description || "",
      deadline: `${p.effective_date}（${daysLeft}天后）`,
      urgency: urgency,
      type: "policy",
    });
  });
  
  // From high-score seller_action signals (official sources only)
  const OFFICIAL_AGGREGATORS = ["amz123", "amzdh", "cifnews", "kjds365", "ennews", "tophub",
    "ecommercebytes", "channelx", "marketplace_pulse", "ecomengine",
    "wearesellers"];
  (state.itemsAi || []).filter(it => it.cross_score >= 0.80).forEach(it => {
    const tone = sourceTone(it.site_id);
    const isAggregator = OFFICIAL_AGGREGATORS.includes(it.site_id);
    if (tone !== "official" || isAggregator) return;
    if (it.cross_label === "seller_action" || (it.impact_summary && it.impact_summary.urgency === "立即行动")) {
      actions.push({
        title: (it.title || "").substring(0, 80),
        detail: it.impact_summary ? `${it.impact_summary.who || "卖家"} · ${it.impact_summary.urgency}` : "高影响信号",
        deadline: it.impact_summary && it.impact_summary.deadline ? it.impact_summary.deadline : "",
        urgency: "high",
        type: "signal",
        url: it.url,
      });
    }
  });
  
  // Sort: high first, then by deadline
  actions.sort((a, b) => {
    const urgOrder = { high: 0, medium: 1, low: 2 };
    return (urgOrder[a.urgency] || 9) - (urgOrder[b.urgency] || 9);
  });
  
  if (metaEl) metaEl.textContent = `${actions.length} 项待跟进`;
  
  if (!actions.length) {
    listEl.innerHTML = "<div class='empty'>暂无紧急行动项</div>";
    return;
  }
  
  listEl.innerHTML = actions.slice(0, 8).map(a => `
    <div class="action-item">
      <div class="action-urgency ${a.urgency}">${a.urgency === 'high' ? '🔴' : a.urgency === 'medium' ? '🟡' : '🟢'}</div>
      <div class="action-body">
        <div class="action-title">${a.url ? `<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a>` : esc(a.title)}</div>
        <div class="action-detail">${esc(a.detail)}</div>
        ${a.deadline ? `<span class="action-deadline ${a.urgency === 'high' ? 'urgent' : a.urgency === 'medium' ? 'warn' : 'ok'}">⏰ ${esc(a.deadline)}</span>` : ""}
      </div>
    </div>
  `).join("");
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

  // 平台筛选
  if (state.platformFilter) {
    items = items.filter(it => (it.cross_platforms || []).includes(state.platformFilter));
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
 * 渲染新闻列表 — 按站点→来源分组（对齐 AI News Radar）
 */
function renderList() {
  const listEl = $("newsList");
  const countEl = $("resultCount");
  const titleEl = $("listTitle");
  if (!listEl) return;

  const items = getFilteredItems();

  if (countEl) countEl.textContent = `${fmtNumber(items.length)} 条`;
  if (titleEl) titleEl.textContent = state.mode === "cross" ? "跨境信号流" : "全量信号";

  listEl.innerHTML = "";
  if (!items.length) {
    listEl.innerHTML = `<div class="empty">当前筛选条件下没有结果。</div>`;
    return;
  }

  if (state.siteFilter) {
    // 站点筛选激活时 → 按来源分组
    renderGroupedBySource(items, listEl);
  } else {
    // 默认 → 按站点分组，站点内按来源分组
    renderGroupedBySiteAndSource(items, listEl);
  }
}

/** 按来源分组 */
function renderGroupedBySource(items, container) {
  const groupMap = new Map();
  items.forEach(it => {
    const key = it.source || "未分区";
    if (!groupMap.has(key)) groupMap.set(key, []);
    groupMap.get(key).push(it);
  });
  const groups = [...groupMap.entries()].sort((a, b) => b[1].length - a[1].length);
  const frag = document.createDocumentFragment();
  groups.forEach(([source, groupItems]) => {
    frag.appendChild(buildSourceGroupNode(source, groupItems));
  });
  container.appendChild(frag);
}

/** 按站点→来源分组 */
function renderGroupedBySiteAndSource(items, container) {
  const siteMap = new Map();
  items.forEach(it => {
    if (!siteMap.has(it.site_id)) siteMap.set(it.site_id, { name: it.site_name || it.site_id, items: [] });
    siteMap.get(it.site_id).items.push(it);
  });
  const sites = [...siteMap.entries()].sort((a, b) => b[1].items.length - a[1].items.length);
  const frag = document.createDocumentFragment();
  sites.forEach(([, site]) => {
    const section = document.createElement("section");
    section.className = "site-group";
    const header = document.createElement("header");
    header.className = "site-group-head";
    header.innerHTML = `<h3>${esc(site.name)}</h3><span>${fmtNumber(site.items.length)} 条</span>`;
    const list = document.createElement("div");
    list.className = "site-group-list";
    section.append(header, list);

    // 站点内按来源分组
    const sourceMap = new Map();
    site.items.forEach(it => {
      const key = it.source || "未分区";
      if (!sourceMap.has(key)) sourceMap.set(key, []);
      sourceMap.get(key).push(it);
    });
    const sourceGroups = [...sourceMap.entries()].sort((a, b) => b[1].length - a[1].length);
    sourceGroups.forEach(([source, groupItems]) => {
      list.appendChild(buildSourceGroupNode(source, groupItems));
    });
    frag.appendChild(section);
  });
  container.appendChild(frag);
}

/** 构建来源分组节点 */
function buildSourceGroupNode(source, items) {
  const section = document.createElement("section");
  section.className = "source-group";
  const header = document.createElement("header");
  header.className = "source-group-head";
  header.innerHTML = `<h3>${esc(source)}</h3><span>${fmtNumber(items.length)} 条</span>`;
  const list = document.createElement("div");
  list.className = "source-group-list";
  section.append(header, list);
  items.forEach(it => list.appendChild(renderItemNode(it)));
  return section;
}

/** 构建单条新闻卡片 */
function renderItemNode(it) {
  const tpl = document.getElementById("itemTpl");
  const node = tpl.content.firstElementChild.cloneNode(true);
  const kind = SOURCE_KINDS[it.site_id] || { label: "来源", tone: "default" };

  node.querySelector(".site").textContent = it.site_name || sourceLabel(it.site_id);
  const catEl = node.querySelector(".category");
  const label = LABELS[it.cross_label] || "行业资讯";
  const labelEmoji = LABEL_EMOJI[it.cross_label] || "📰";
  const score = Math.round((it.cross_score || 0) * 100);
  catEl.textContent = `${labelEmoji} ${label} · ${score}分`;
  catEl.className = `category kind-${kind.tone}`;
  node.querySelector(".source").textContent = it.source || "";
  node.querySelector(".time").textContent = timeAgo(it.published_at) || fmtTime(it.published_at);

  // Add platform tags
  const platformTags = (it.cross_platforms || []).map(p => `<span class="platform-tag">${esc(p)}</span>`).join("");
  if (platformTags) {
    const metaRow = node.querySelector(".meta-row");
    const timeEl = node.querySelector(".time");
    const platformSpan = document.createElement("span");
    platformSpan.innerHTML = platformTags;
    metaRow.insertBefore(platformSpan, timeEl);
  }

  const titleEl = node.querySelector(".title");
  // Show bilingual title if available
  const zh = (it.title_zh || "").trim();
  const en = (it.title || "").trim();
  if (zh && en && zh !== en) {
    titleEl.textContent = "";
    const primary = document.createElement("span");
    primary.textContent = zh;
    const sub = document.createElement("span");
    sub.className = "title-sub";
    sub.textContent = en;
    titleEl.append(primary, sub);
  } else {
    titleEl.textContent = it.title || zh || "";
  }
  titleEl.href = it.url || "#";
  return node;
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
 * 从标题中提取事件实体 key（平台 + 关键词组合）
 * 扩展了平台和关键词列表以覆盖更多跨境新闻类型
 * @param {string} title
 * @returns {string}
 */
function extractEventKey(title) {
  const t = (title || "").toLowerCase();

  // 平台实体（扩展）
  const platforms = [
    "amazon", "亚马逊",
    "temu", "shein",
    "tiktok", "tiktok shop",
    "walmart", "沃尔玛",
    "ebay",
    "shopee",
    "lazada",
    "速卖通", "aliexpress",
    "美客多", "mercadolibre",
    "ozon",
    "depop",
    "flipkart",
    "拼多多", "pinduoduo",
    "京东", "joybuy",
    "etsy",
    "shopify",
    "谷歌", "google",
    "meta", "facebook",
  ];
  let platform = "";
  for (const p of platforms) {
    if (t.includes(p)) { platform = p; break; }
  }

  // 关键词实体（扩展覆盖物流/税务/政策/运营等多维度）
  const keywords = [
    // 政策合规
    "政策", "policy", "新规", "法规", "regulation",
    "合规", "compliance", "禁止", "ban", "禁售",
    "截止", "deadline", "生效", "强制", "要求",
    // 税务
    "关税", "tariff", "duty", "税率", "tax", "征税",
    "增值税", "vat", "退税", "免税",
    // 物流仓储
    "物流", "logistics", "配送", "delivery", "运费",
    "仓储", "warehouse", "fba", "fbm", "海外仓",
    "货运", "shipping", "海关", "customs",
    // 费用佣金
    "费率", "fee", "佣金", "commission", "涨价",
    "降价", "费用", "附加费", "补贴",
    // 广告运营
    "广告", "advertising", "ppc", "广告费",
    "运营", "流量", "权重", "转化", "销量",
    // 平台规则
    "上架", "listing", "选品", "产品",
    "封号", "冻结", "审核", "suspension",
    "buy box", "购物车",
    // 市场趋势
    "旺季", "prime", "黑五", "black friday",
    "网一", "大促", "增长", "下降", "飙升", "暴跌",
    "趋势", "报告", "数据",
    // 跨境专属
    "跨境", "跨境电商", "卖家的", "卖家",
    "出口", "进口",
    // 行业事件
    "破产", "倒闭", "裁员", "收购", "投资",
    "查获", "查扣", "扣留", "维权", "起诉",
    "专利", "侵权", "infringement",
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
function pickCrossItems(items, maxPicks = 10) {
  // 内容质量黑名单 — 工具/营销/软文/常青内容
  // 注意：不要过度过滤，避免误杀合法新闻
  const BLACKLIST = /西柚找词|卖家精灵|keepa|helium.?10|jungle.?scout|pacvue|sellics|perpetua|tool4seller|uaalim|优麦云|H10H10|领星|积加|赛盈|马帮|店小秘|通途|易仓|sellerboard|sif|Sif|招商峰会|免费领取|知识星球|课程培训|陪跑社群|邀请码|注册链接|affiliate|ERP利润|选品运营工具|关键词反查|运营工具|利润分析|分析工具|亚马逊.*工具|沃尔玛.*工具|选品工具|广告工具|erp工具|超精准|高时效|定制化|系统性|专属顾问|成长服务|卖家服务|官方服务|爆单秘籍|独家揭秘|必看攻略|快速理清|引爆.*先机|开店即用|跨境电商365.*Agent|Agent.*亚马逊|选品分析|补货周期|周期表|安全补货|FBA补货|FBA周期|利润计算器|费用计算器|成本计算器/i;

  // 按事件 key 分组
  const clusters = new Map();

  for (const it of items) {
    const title = (it.title || "").trim();

    // 跳过工具推荐/软文
    if (BLACKLIST.test(title)) continue;

    // 跳过纯品类名/常青标题（无动作词、无数字、无具体事件）
    // 放宽到标题<15字（原来20）才检查
    const HAS_ACTION = /发布|调整|生效|禁止|截止|要求|更新|新规|变更|推出|上线|启动|关闭|取消|增加|降低|提高|限制|打击|整治|严查|罚款|下架|封号|暴涨|暴跌|飙升|增长|下降|观察|分析|解读|获悉|报告|调查|警告|提醒|注意|影响|冲击|利好|利空|关闭|暂停|停止|恢复|开放|扩张|投资|合作|收购|起诉|维权|查获|查扣|征收|加征|加税|免签|开通|新增|升级|布局|发力|爆单|震荡|崩了|暴雷|噩梦|倒闭|欠薪|裁员|\d{4}|\d+[%亿万元]|prime|day|黑五|网一/i;
    if (title.length < 15 && !HAS_ACTION.test(title)) continue;

    // 跳过没有URL或URL指向非文章页
    const url = it.url || "";
    if (!url.startsWith("http")) continue;

    // 影响维度过滤
    if (state.impactFilter && it.cross_label !== state.impactFilter) continue;

    // 提取事件key — 放宽要求，平台或关键词有一即可，不再强制两者都有
    const key = extractEventKey(title);
    if (!key || key === "|") continue;

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
    .filter(c => c.sourceCount >= 2 || c.maxScore >= 0.60) // 多源优先，单源需高分(≥0.60)
    .sort((a, b) => {
      // 分数优先
      if (Math.abs(b.maxScore - a.maxScore) > 0.01) return b.maxScore - a.maxScore;
      // 同分时多源优先
      if (b.sourceCount !== a.sourceCount) return b.sourceCount - a.sourceCount;
      // 时间
      return b.latestTime - a.latestTime;
    })
    .slice(0, maxPicks);

  // 为每个 cluster 选择代表性条目（分数最高 + 标题最长）
  return sorted.map(c => {
    const representative = c.items.sort((a, b) => {
      const scoreDiff = (b.cross_score || 0) - (a.cross_score || 0);
      if (Math.abs(scoreDiff) > 0.01) return scoreDiff;
      return (b.title || "").length - (a.title || "").length; // 标题长的信息量更大
    })[0];
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

  let html = '<div class="cross-compact-list">';
  picks.forEach((pick, idx) => {
    const urg = urgencyOf(pick.cross_score || 0);
    const label = LABELS[pick.cross_label] || "行业资讯";
    const score = Math.round((pick.cross_score || 0) * 100);

    const sourceHits = (pick._allItems || [])
      .map(i => i.site_name || sourceLabel(i.site_id))
      .filter((v, i, a) => a.indexOf(v) === i)
      .map(name => `<span class="pick-source-hit">${esc(name)}</span>`)
      .join("");

    html += `
      <a class="pick-row" href="${esc(pick.url)}" target="_blank" rel="noopener noreferrer">
        <div class="pick-row-time">${timeAgo(pick.published_at) || fmtTime(pick.published_at)}</div>
        <div class="pick-row-body">
          <div class="pick-row-meta">
            <span>#${idx + 1}</span>
            <span>${esc(label)}</span>
            <span>${pick._sourceCount} 个来源</span>
            <strong>${score} 分</strong>
            ${sourceHits}
          </div>
          <div class="pick-row-title">${esc(pick.title_zh || pick.title)}</div>
        </div>
      </a>`;
  });
  html += "</div>";

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
    }, 200);
  });

  // Enter键也触发搜索+跳转
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      state.query = input.value.trim();
      renderList();
      const target = $("sectionSignal");
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
}

function bindSearchBtn() {
  const btn = $("searchBtn");
  if (!btn) return;
  btn.addEventListener("click", () => {
    const input = $("searchInput");
    if (input) state.query = input.value.trim();
    renderList();
    const target = $("sectionSignal");
    if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

/* ========== 去重开关绑定 ========== */



/* ========== 综合渲染 ========== */

/**
 * 重新渲染所有 UI 组件
 */
function renderAll() {
  setStats();
  renderCoverageStrip();
  renderSiteFilters();
  renderImpactFilter();
  renderPlatformFilter();
  renderList();
  renderCrossPicks();
  renderPolicyCalendar();
  renderActionItems();
  renderUpdatedAt();
}

/* ========== 初始化 ========== */

/**
 * 应用入口：加载数据并渲染
 */
/* ========== 回到顶部按钮 ========== */

function bindBackToTop() {
  const btn = $("backToTop");
  if (!btn) return;
  let ticking = false;
  window.addEventListener("scroll", () => {
    if (!ticking) {
      requestAnimationFrame(() => {
        btn.classList.toggle("visible", window.scrollY > 400);
        ticking = false;
      });
      ticking = true;
    }
  }, { passive: true });
  btn.addEventListener("click", () => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
}

/* ========== 筛选标签点击跳转 ========== */

function bindFilterScroll() {
  // 影响维度 → 跳到精选区
  const impactWrap = $("impactPills");
  if (impactWrap) {
    impactWrap.addEventListener("click", () => {
      const target = $("sectionPicks") || $("sectionSignal");
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }
  // 紧急度 → 跳到行动清单
  const urgencyWrap = $("urgencyPills");
  if (urgencyWrap) {
    urgencyWrap.addEventListener("click", () => {
      const target = $("sectionActions") || $("sectionSignal");
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }
  // 平台 → 跳到信号流
  const platformWrap = $("platformPills");
  if (platformWrap) {
    platformWrap.addEventListener("click", () => {
      const target = $("sectionSignal");
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }
}

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
    bindSearchBtn();
    bindBackToTop();
    bindFilterScroll();

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
