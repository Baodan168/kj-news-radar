#!/usr/bin/env python3
"""Cross-border e-commerce relevance scoring for news records."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

# ──────────────────────────────────────────────────────────────
# Core cross-border e-commerce keywords (strong signals)
# ──────────────────────────────────────────────────────────────
CROSS_KEYWORDS = [
    # English terms
    "cross-border",
    "cross border",
    "跨境电商",
    "跨境",
    "海外仓",
    "保税仓",
    "保税区",
    "出口电商",
    "进口电商",
    "跨境支付",
    "跨境物流",
    "国际贸易",
    "外贸",
    "进出口",
    "海关",
    "关税",
    "清关",
    "报关",
    "国际物流",
    "海外直邮",
    "全球购",
    "代购",
    "速卖通",
    "aliexpress",
    "shopee",
    "lazada",
    "wish",
    "temu",
    "shein",
    "tiktok shop",
    "tiktok电商",
    "亚马逊",
    "amazon",
    "ebay",
    "etsy",
    "独立站",
    "shopify",
    "全球开店",
    "fba",
    "海外营销",
    "google shopping",
    "paypal",
    "stripe",
    "pingpong",
    "连连支付",
    "万里汇",
    "worldfirst",
    "airwallex",
    "xtransfer",
    # Missing e-commerce platform keywords
    "walmart",
    "沃尔玛",
    "美客多",
    "mercadolibre",
    "ozon",
    "wildberries",
    # E-commerce ecosystem
    "marketplace",
    "卖家",
    "seller",
    # FBA logistics
    "fbm",
    "mcf",
    "epr",
    "gpsr",
    "vat",
    "deleg",
    "ppwr",
]

# ──────────────────────────────────────────────────────────────
# E-commerce platform keywords (moderate signals)
# ──────────────────────────────────────────────────────────────
ECOMMERCE_KEYWORDS = [
    "电商平台",
    "电商",
    "e-commerce",
    "ecommerce",
    "卖家",
    "买家",
    "店铺",
    "listing",
    "sku",
    "asin",
    "选品",
    "铺货",
    "一件代发",
    "dropshipping",
    "直播带货",
    "短视频带货",
    "kOL",
    "koc",
    "达人",
    "网红",
    "affiliate",
    "联盟营销",
    "广告投放",
    "roas",
    "acos",
    "转化率",
    "流量",
    "爆款",
    "测评",
    "review",
    "feedback",
    "退货",
    "退款",
    "售后",
    "客服",
    "供应链",
    "采购",
    "批发",
    "库存",
]

# ──────────────────────────────────────────────────────────────
# Policy & logistics keywords (relevant context)
# ──────────────────────────────────────────────────────────────
POLICY_KEYWORDS = [
    "政策",
    "法规",
    "合规",
    "监管",
    "税费",
    "增值税",
    "消费税",
    "关税",
    "贸易摩擦",
    "贸易战",
    "制裁",
    "rcep",
    "自贸协定",
    "fta",
    "海关总署",
    "商务部",
    "税务总局",
    "外汇",
    "汇率",
]

LOGISTICS_KEYWORDS = [
    "物流",
    "仓储",
    "清关",
    "报关",
    "货运",
    "快递",
    "邮政",
    "ems",
    "dhl",
    "fedex",
    "ups",
    "tnt",
    "菜鸟",
    "极兔",
    "百世",
    "云途",
    "燕文",
    "包裹",
    "tracking",
    "追踪",
]

# ──────────────────────────────────────────────────────────────
# Advertising & marketing keywords
# ──────────────────────────────────────────────────────────────
ADVERTISING_KEYWORDS = [
    "广告",
    "投放",
    "竞价",
    "cpc",
    "cpm",
    "cpa",
    "roas",
    "acos",
    "站外推广",
    "deal",
    "coupon",
    "促销",
    "秒杀",
    "闪购",
    "prime day",
    "黑五",
    "网一",
    "大促",
]

# ──────────────────────────────────────────────────────────────
# Platform trend keywords
# ──────────────────────────────────────────────────────────────
PLATFORM_TREND_KEYWORDS = [
    "新功能",
    "更新",
    "政策变更",
    "算法",
    "排名",
    "权重",
    "封号",
    "关联",
    "品牌备案",
    "品牌",
    "商标",
    "侵权",
    "知识产权",
    "patent",
    "trademark",
    "copyright",
]

# ──────────────────────────────────────────────────────────────
# Seller-relevance keywords (penalize if platform mentioned but none of these)
# ──────────────────────────────────────────────────────────────
SELLER_RELEVANCE_KEYWORDS = [
    "卖家", "seller", "listing", "asin", "fba", "fbm", "mcf",
    "prime", "review", "rating", "feedback", "退货", "退款",
    "广告", "ppc", "acos", "sponsored", "bid",
    "库存", "inventory", "补货", "发货",
    "费用", "fee", "佣金", "commission", "费率",
    "政策", "policy", "合规", "compliance", "规则",
    "封号", "冻结", "受限", "suspension", "appeal",
    "选品", "上架", "品牌", "brand", "备案",
    "物流", "shipping", "配送", "仓储", "海外仓",
    "vat", "关税", "tariff", "tax", "epr", "gpsr",
]

# 运营卖家词：宏观新闻豁免/行动类加分的判断基准
# （"关税/政策"这类词不算——特朗普关税诉讼不是运营内容）
OPERATIONAL_SELLER_WORDS = [
    "卖家", "seller", "listing", "asin", "fba", "fbm", "mcf",
    "prime", "review", "feedback", "退货", "退款", "广告", "ppc",
    "acos", "sponsored", "库存", "inventory", "补货", "发货",
    "封号", "冻结", "受限", "suspension", "appeal", "申诉",
    "选品", "上架", "品牌", "brand", "备案", "跟卖",
    "配送费", "仓储费", "物流费", "佣金",
]

# Platform names that can appear in general corporate news
PLATFORM_KEYWORDS = [
    "amazon", "亚马逊", "fba", "fbm", "prime",
    "temu", "tiktok shop", "tiktok电商",
    "shein", "shopee", "lazada", "速卖通", "aliexpress",
    "ebay", "etsy", "walmart", "shopify", "独立站",
    "ozon", "wildberries", "jumia", "mercadolibre",
    "美客多", "乐天", "rakuten",
]

# Site/region detection
SITE_KEYWORDS = {
    "us": ["美国站", "amazon.com", "us站", "美区", "美国"],
    "uk": ["英国站", "amazon.co.uk", "uk站", "英区", "英国", "英代"],
    "eu": ["欧洲站", "amazon.de", "amazon.fr", "amazon.it", "amazon.es", "eu站", "欧区", "欧洲", "德国站", "法国站", "意大利站", "西班牙站"],
    "jp": ["日本站", "amazon.co.jp", "jp站", "日区", "日本"],
    "au": ["澳洲站", "amazon.com.au", "au站", "澳大利亚"],
    "ca": ["加拿大站", "amazon.ca", "ca站"],
}

# ──────────────────────────────────────────────────────────────
# Noise keywords (reduce relevance)
# ──────────────────────────────────────────────────────────────
NOISE_KEYWORDS = [
    "娱乐",
    "明星",
    "八卦",
    "足球",
    "篮球",
    "彩票",
    "情感",
    "旅游",
    "美食",
    "游戏",
    "动漫",
    "综艺",
    "电视剧",
    "电影",
    "音乐",
    # Streaming / entertainment (Amazon Newsroom noise)
    "prime video",
    "mgm studios",
    "emmy",
    "艾美奖",
    "预告片",
    "trailer",
    "teaser trailer",
    "streaming",
    "new romance",
    "debut",
    "premiere",
    "romantic drama",
    "tv series",
    "特辑",
    "好莱坞",
    # 企业CSR/泛商业内容（对卖家无决策价值，如亚马逊节水补水）
    "节水", "补水", "水资源", "碳中和", "碳排放", "可持续发展",
    "公益", "慈善", "员工福利", "企业文化", "环保奖", "绿色环保",
    "esg", "净零", "零碳", "生物多样", "社区服务",
]

# 标题党/震惊体（聚合站标题党 ≠ 信息量大，命中则禁止保底抬分）
CLICKBAIT_NOISE = [
    "重磅", "炸了", "震惊", "慌了", "吓人", "恐怖",
    "突发！", "紧急！", "注意！", "警惕", "小心", "避坑",
    "卖家圈", "刷屏", "炸锅",
]

# 企业CSR内容（独立列表：命中且无卖家上下文 → 与宏观新闻同等级重罚）
CSR_NOISE_KEYWORDS = [
    "节水", "补水", "水资源", "碳中和", "碳排放", "可持续发展",
    "公益", "慈善", "员工福利", "企业文化", "环保奖", "绿色环保",
    "esg", "净零", "零碳", "生物多样", "社区服务",
    # 英文标题（Amazon Newsroom等官方源用英文发布）
    "water conservation", "replenishment", "sustainability",
    "carbon neutral", "net zero", "wildfire", "communities",
    "charitable", "philanthropy", "donation", "volunteer",
    "climate", "environmental", "renewable energy",
]

# 宏观贸易/产业新闻（泛宏观，非卖家直接相关，命中则重罚）
MACRO_NOISE_KEYWORDS = [
    "贸易战", "关税战", "反倾销", "出口管制", "制裁",
    "中欧贸易", "中美贸易", "逆差", "顺差",
    "空客", "波音", "钢铁", "中钢协", "汽车产业", "工业产值",
    "GDP", "通胀", "央行", "利率", "汇率", "股市", "美股",
    "集装箱吞吐", "港口吞吐", "货运量", "贸易额",
    "去工业化", "产业危机", "工业危机", "经济衰退",
    "自动驾驶", "新能源车", "智能驾驶",
    # 海外政治/诉讼（对卖家无操作价值，如特朗普关税诉讼）
    "特朗普", "白宫", "国会", "大选", "联邦法院", "州政府",
    "诉讼", "起诉", "集体诉讼", "投诉至", "抗议", "罢工",
    ]

# 企业财务/资本新闻（如"亚马逊市值破3万亿"，对卖家决策价值低，即使含平台词也降权）
CORPORATE_FINANCE_NOISE = [
"市值", "财报", "营收", "利润", "股价", "估值",
"上市", "IPO", "港交所", "纳斯达克", "纽交所",
"融资", "并购", "收购", "募资", "融资额",
]

# Domestic e-commerce noise (not cross-border focused)
DOMESTIC_ECOMMERCE_NOISE = [
    "淘宝",
    "天猫",
    "京东",
    "拼多多",
    "抖音电商",
    "快手电商",
    "国内电商",
    "内贸",
    "新零售",
    "社区团购",
    "同城配送",
    "外卖",
]

# Promotional / non-news content noise
PROMOTION_NOISE = [
    "免费领取",
    "招商会",
    "招商峰会",
    "招商经理",
    "报名",
    "门票",
    "限时优惠",
    "折扣码",
    "优惠券",
    "团购",
    "知识星球",
    "课程",
    "培训",
    "陪跑",
    "社群",
    "私域",
    "找物流",
    "找海外仓",
    "找服务",
    "找活动",
    "查测评",
    "工具箱",
    "插件",
    "免费试用",
    "注册链接",
    "邀请码",
    "affiliate",
    "西柚找词",
    "sif",
    "卖家精灵",
    "keepa",
    "helium10",
    "jungle scout",
    "pacvue",
    "sellics",
    "perpetua",
    "aini",
    "tool4seller",
    "uaalim",
    "brand analytics",
]

# ──────────────────────────────────────────────────────────────
# English signal regex
# ──────────────────────────────────────────────────────────────
EN_SIGNAL_RE = re.compile(
    r"(?i)(?<![a-z0-9])("
    r"cross.?border|"
    r"aliexpress|shopee|lazada|temu|shein|wish|"
    r"amazon|ebay|etsy|shopify|"
    r"tiktok.?shop|"
    r"fba|fbm|"
    r"dropship(?:ping)?|"
    r"paypal|stripe|pingpong|airwallex|worldfirst|"
    r"e-?commerce|"
    r"international.?trade|"
    r"logistics|warehouse|customs|tariff|"
    r"export|import"
    r")(?![a-z0-9])"
)

MEANINGFUL_EN_SIGNAL_RE = re.compile(
    r"(?i)(?<![a-z0-9])("
    r"cross.?border|"
    r"aliexpress|shopee|lazada|temu|shein|"
    r"amazon.?seller|amazon.?fba|"
    r"shopify|"
    r"tiktok.?shop|"
    r"dropship(?:ping)?"
    r")(?![a-z0-9])"
)

# ──────────────────────────────────────────────────────────────
# Past-year detection: penalty for titles referencing previous years
# ──────────────────────────────────────────────────────────────
from datetime import date
_PAST_YEAR_STALE_RE = re.compile(r"(?<!\d)(20[2-9]\d)(?:\s*年|(?![-\d]))")
_CURRENT_YEAR = date.today().year

# ──────────────────────────────────────────────────────────────
# Source priors: base scores for trusted sources
# ──────────────────────────────────────────────────────────────
SOURCE_PRIORS = {
    # Official / platform sources (high credibility, focused content)
    "amazon_news": 0.20,
    "amazon_newsroom": 0.20,
    "shopee_news": 0.20,
    "lazada_news": 0.20,
    "temu_news": 0.20,
    "amazon_ads": 0.30,
    "shopify_blog": 0.25,
    "seller_central": 0.25,
    # Industry analysis (high quality, original insights)
    "marketplace_pulse": 0.20,
    "ecommercebytes": 0.20,
    "channelx": 0.20,
    # Aggregators / curated news
    "amz123": 0.15,
    "cifnews": 0.10,
    "ennews": 0.10,
    "tophub": 0.10,
    "ecomengine": 0.10,
    # Community / user-generated (lowest prior)
    "wearesellers": 0.05,
    "kjds365": 0.05,
    # Remaining (low volume / niche)
    "payoneer": 0.15,
    "worldfirst": 0.15,
    "pingpong": 0.10,
    "crossborder_ebay": 0.15,
    "walmart_seller": 0.15,
    "google_shopping": 0.10,
    "meta_ads": 0.10,
}

# Sources that are always cross-border relevant by default
# NOTE: Removed — all sources now go through unified keyword scoring.

# ──────────────────────────────────────────────────────────────
# Labels
# ──────────────────────────────────────────────────────────────
LABEL_KEYWORDS = [
    ("policy_update", ["政策", "policy", "规则", "法规", "合规", "compliance", "新规", "变更", "截止", "deadline", "欧盟", "eu", "gpsr", "epr", "ppwr", "deleg", "vat", "关税"]),
    ("fee_logistics", ["费用", "fee", "物流", "logistics", "配送", "仓储", "海外仓", "fba费", "fba fee", "配送费", "仓储费", "物流费"]),
    ("advertising", ["广告", "ppc", "acos", "sponsored", "cpc", "bid", "投放", "prime day", "黑五", "网一", "大促", "促销"]),
    ("listing_product", ["listing", "asin", "选品", "上架", "产品", "品牌", "review", "rating", "退货", "退款"]),
    ("platform_trend", ["趋势", "增长", "市场", "份额", "prime day", "旺季", "黑五", "季度", "财报", "报告"]),
    ("seller_action", ["封号", "冻结", "受限", "紧急", "截止", "倒计时", "注意", "警告", "suspension", "appeal"]),
    ("compliance_deadline", ["注册", "备案", "申报", "截止日期", "deadline", "最后", "限期", "合规截止"]),
    ("temu", ["temu", "拼多多跨境"]),
    ("tiktok", ["tiktok shop", "tiktok电商", "tiktok"]),
    ("walmart", ["walmart", "沃尔玛"]),
]

CROSS_RELEVANCE_THRESHOLD = 0.60  # 2026-07-10 从0.65降至0.60以扩大入选范围

# ──────────────────────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────────────────────


def contains_any_keyword(haystack: str, keywords: list[str]) -> bool:
    """Check if any keyword appears in the haystack (case-insensitive).

    英文纯字母关键词用词边界匹配（避免 vat 误匹配 conservation 等子串）。
    """
    h = haystack.lower()
    for k in keywords:
        kl = k.lower()
        if not kl:
            continue
        if kl.isascii() and kl.replace(" ", "").isalpha():
            if re.search(rf"(?<![a-z0-9]){re.escape(kl)}(?![a-z0-9])", h):
                return True
        elif kl in h:
            return True
    return False


def matched_keywords(haystack: str, keywords: list[str]) -> list[str]:
    """Return sorted list of unique keywords found in haystack.

    英文纯字母关键词用词边界匹配（避免 vat 误匹配 conservation 等子串），
    中文/含空格短语用子串匹配。
    """
    h = haystack.lower()
    found = set()
    for k in keywords:
        kl = k.lower()
        if not kl:
            continue
        if kl.isascii() and kl.replace(" ", "").isalpha():
            # 英文词：词边界匹配（"vat" 不匹配 "conservation"）
            if re.search(rf"(?<![a-z0-9]){re.escape(kl)}(?![a-z0-9])", h):
                found.add(k)
        elif kl in h:
            found.add(k)
    return sorted(found)


def contains_meaningful_cross_signal(haystack: str) -> bool:
    """Check for strong cross-border e-commerce signals."""
    h = haystack.lower()
    if MEANINGFUL_EN_SIGNAL_RE.search(h):
        return True
    # Strong Chinese signals
    strong_signals = [
        "跨境电商", "跨境", "海外仓", "保税仓", "出口电商",
        "进口电商", "速卖通", "独立站", "全球开店",
        "亚马逊", "amazon", "fba", "fbm", "prime",
        "卖家", "seller",
        "walmart", "沃尔玛", "美客多", "mercadolibre", "ozon", "wildberries",
        "temu", "shein", "tiktok shop", "tiktok电商",
        "epr", "gpsr", "vat", "deleg", "ppwr",
    ]
    return any(k in h for k in strong_signals)


def _label_for_text(text: str, has_ecommerce: bool) -> str:
    """Determine the most specific label for the text."""
    for label, keywords in LABEL_KEYWORDS:
        if contains_any_keyword(text, keywords):
            return label
    if has_ecommerce:
        return "seller_action"
    return "general"


def _result(
    *,
    is_cross_related: bool,
    score: float,
    label: str,
    reason: str,
    signals: list[str] | None = None,
    noise: list[str] | None = None,
) -> dict[str, Any]:
    """Build a standardized result dict."""
    return {
        "is_cross_related": bool(is_cross_related),
        "score": round(max(0.0, min(1.0, score)), 2),
        "label": label,
        "reason": reason,
        "signals": signals or [],
        "noise": noise or [],
    }


# ──────────────────────────────────────────────────────────────
# Main scoring function
# ──────────────────────────────────────────────────────────────


def score_cross_relevance(record: dict[str, Any]) -> dict[str, Any]:
    """Return an explainable cross-border e-commerce relevance score.

    Scoring formula:
        score = source_prior + keyword_bonus + signal_count_bonus - noise_penalty

    Args:
        record: Dict with keys: site_id, title, source, site_name, url

    Returns:
        Dict with: is_cross_related, score, label, reason, signals, noise
    """
    site_id = str(record.get("site_id") or "")
    title = str(record.get("title") or "")
    source = str(record.get("source") or "")
    site_name = str(record.get("site_name") or "")
    url = str(record.get("url") or "")
    # 打分只基于标题！source/site_name 是来源元数据（如"跨境资讯"/"亿恩网"），
    # 混入会导致所有条目自带"跨境"强信号+0.60保底（2026-08-04修复）
    text = title.lower()

    # Gather signals
    cross_signals = matched_keywords(text, CROSS_KEYWORDS)
    ecommerce_signals = matched_keywords(text, ECOMMERCE_KEYWORDS)
    policy_signals = matched_keywords(text, POLICY_KEYWORDS)
    logistics_signals = matched_keywords(text, LOGISTICS_KEYWORDS)
    all_good_signals = cross_signals + ecommerce_signals + policy_signals + logistics_signals

    noise = matched_keywords(text, NOISE_KEYWORDS) + matched_keywords(text, DOMESTIC_ECOMMERCE_NOISE) + matched_keywords(text, PROMOTION_NOISE)
    macro_noise = matched_keywords(text, MACRO_NOISE_KEYWORDS) + matched_keywords(text, CSR_NOISE_KEYWORDS)
    source_prior = SOURCE_PRIORS.get(site_id, 0.0)

    # ── Analyze signal strength ────────────────────────────────
    has_cross = contains_meaningful_cross_signal(text)
    has_ecommerce = contains_any_keyword(text, ECOMMERCE_KEYWORDS)
    has_en_signal = EN_SIGNAL_RE.search(text) is not None
    # 政策/物流信号（关税/邮政/法规等对卖家有直接影响的词）
    has_policy_logistics = bool(policy_signals or logistics_signals)

    # Seller-relevance penalty: if platform keyword present but no seller-relevance keyword
    has_platform = contains_any_keyword(text, PLATFORM_KEYWORDS)
    has_seller_relevance = contains_any_keyword(text, SELLER_RELEVANCE_KEYWORDS)

    # ── No signal at all ───────────────────────────────────────
    if not (has_cross or has_ecommerce or has_en_signal or has_policy_logistics):
        return _result(
            is_cross_related=False,
            score=source_prior + (0.15 if has_ecommerce else 0.0),
            label="not_cross",
            reason="missing_cross_border_signal",
            signals=all_good_signals,
            noise=noise,
        )

    # ── Domestic e-commerce noise without cross-border signal ──
    domestic_noise = matched_keywords(text, DOMESTIC_ECOMMERCE_NOISE)
    if domestic_noise and not has_cross and not has_en_signal:
        return _result(
            is_cross_related=False,
            score=0.20 + source_prior,
            label="domestic_ecommerce_noise",
            reason="domestic_ecommerce_without_cross_border_signal",
            signals=all_good_signals,
            noise=noise,
        )

    # ── General noise without strong signal ────────────────────
    general_noise = matched_keywords(text, NOISE_KEYWORDS)
    if general_noise and not has_cross:
        return _result(
            is_cross_related=False,
            score=0.25 + source_prior,
            label="noise",
            reason="noise_without_strong_cross_border_signal",
            signals=all_good_signals,
            noise=noise,
        )

    # ── Calculate score ────────────────────────────────────────
    # Base score from signal type
    if has_cross:
        base = 0.50
    elif has_policy_logistics:
        base = 0.35  # 政策/物流信号（关税/邮政/法规等）中等基础分
    elif has_en_signal:
        base = 0.40
    else:
        base = 0.30

    # Bonus for multiple signals (capped)
    signal_count = len(set(all_good_signals))
    signal_bonus = min(0.20, 0.04 * signal_count)

    # Penalty for noise (capped)
    noise_penalty = min(0.15, 0.03 * len(noise)) if noise else 0.0

    # Penalty for titles referencing a past year (stale evergreen content)
    year_penalty = 0.0
    title = str(record.get("title") or "")
    year_matches = _PAST_YEAR_STALE_RE.findall(title)
    for ym in year_matches:
        try:
            y = int(ym)
            if y < _CURRENT_YEAR - 1:
                year_penalty = max(year_penalty, 0.25)  # 2+ years old → heavy penalty
            elif y < _CURRENT_YEAR:
                year_penalty = max(year_penalty, 0.15)  # 1 year old → moderate penalty
        except ValueError:
            pass

    score = source_prior + base + signal_bonus - noise_penalty - year_penalty

    # Penalize platform news without seller-relevance keywords
    if has_platform and not has_seller_relevance:
        score -= 0.15

    # ── UK/EU 市场加分（英国站卖家核心市场）────────────────
    uk_keywords = ["英国站", "uk站", "amazon.co.uk", "英区", "英代", "英国",
                   "欧英站", "英欧站", ".co.uk"]
    has_uk = any(k in text for k in uk_keywords)
    eu_keywords = ["欧洲站", "欧洲", "欧盟", "欧元", "欧区",
                   "德国站", "法国站", "意大利站", "西班牙站", "荷兰站"]
    has_eu = any(k in text for k in eu_keywords)

    # Amazon platform boost (亚马逊是核心平台) — 提前定义供宏观惩罚判断
    amazon_keywords = ["亚马逊", "amazon", "fba", "fbm", "prime", "seller central"]
    has_amazon = any(k in text for k in amazon_keywords)

    # 宏观新闻/企业CSR重罚：命中宏观词 且 无卖家/平台/Amazon上下文 → -0.30
    # （如空客和解/中欧贸易战/去工业化等对卖家无决策价值的宏观新闻）
    # 注意：裸"英国/欧洲"不算卖家上下文——空客新闻含"英国"但对卖家无用
    macro_penalty = 0.0
    if matched_keywords(text, MACRO_NOISE_KEYWORDS):
        has_seller_ctx = (contains_any_keyword(text, OPERATIONAL_SELLER_WORDS)
                          or has_platform or has_amazon)
        if not has_seller_ctx:
            macro_penalty = 0.30

    # 企业CSR内容：即使含平台词（如"亚马逊节水补水"）也重罚，除非有卖家词
    csr_penalty = 0.0
    if matched_keywords(text, CSR_NOISE_KEYWORDS) and not has_seller_relevance:
        csr_penalty = 0.30

    # 企业财务/资本新闻（市值/财报/IPO等）：即使含平台词也降权，除非有卖家词
    # 例："亚马逊市值首次突破3万亿美元"——含"亚马逊"但非卖家决策内容
    corp_penalty = 0.0
    if matched_keywords(text, CORPORATE_FINANCE_NOISE) and not has_seller_relevance:
        corp_penalty = 0.15

    # 宏观新闻背景下 UK/EU boost 减半（"中欧贸易战重创欧洲工业"不该靠"欧洲"拿高分）
    if has_uk:
        score += 0.12 if not macro_penalty else 0.06  # UK-specific: 最高优先级
    elif has_eu:
        score += 0.08 if not macro_penalty else 0.04  # EU: 高优先级（政策法规直接影响英国站卖家）

    if has_amazon:
        score += 0.12  # Amazon 是核心平台，权重最高（2026-08-04 从0.08提升）
        if has_uk:
            score += 0.05  # Amazon UK 叠加：同时提及"英国"+"亚马逊"额外加分

    # 纯竞品平台降权：Temu/Shopee/SHEIN/TikTok Shop等竞品平台，无Amazon词 → -0.10
    # （英国站Amazon卖家不关注竞品平台的泛新闻）
    competitor_platforms = ["temu", "拼多多跨境", "shopee", "虾皮", "shein", "希音",
                            "tiktok shop", "tiktok电商", "lazada", "速卖通",
                            "aliexpress", "ebay", "walmart", "沃尔玛"]
    if any(k in text for k in competitor_platforms) and not has_amazon:
        score -= 0.10

    # 决策价值分层：L1行动类内容（政策/合规截止/卖家行动）保底额外加分
    # 对卖家：政策变更/合规截止/费用调整/封号冻结直接影响运营，必须置顶
    l1_action_keywords = [
        "政策", "政策变更", "新规", "规则", "合规", "compliance", "法规",
        "截止", "deadline", "最后期限", "限期", "生效", "变更",
        "费用", "费", "涨价", "上调", "调整", "佣金", "费率",
        "封号", "冻结", "受限", "suspension", "appeal", "申诉",
        "禁止", "取消", "收紧", "严查", "审核",
        "ppwr", "gpsr", "epr", "ukca", "英代", "欧代",
        "vat", "关税", "tariff", "tax",
    ]
    l1_hits = matched_keywords(text, l1_action_keywords)
    l1_action = bool(l1_hits) and (contains_any_keyword(text, OPERATIONAL_SELLER_WORDS) or has_amazon)
    if l1_action:
        score += 0.08  # 行动类内容加权（2026-08-04新增：决策价值分层）

    # 时效微调：24h窗口内 <6h 的新闻额外 +0.03（新闻雷达价值在"新"）
    try:
        pub = record.get("published_at")
        pub_dt = None
        if isinstance(pub, datetime):
            pub_dt = pub
        elif isinstance(pub, str) and pub:
            for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z",
                        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    pub_dt = datetime.strptime(pub, fmt)
                    break
                except ValueError:
                    continue
        if pub_dt is not None and pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        if pub_dt is not None:
            age_hours = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600
            if 0 <= age_hours < 6:
                score += 0.03
            elif age_hours >= 24:
                score -= 0.05  # 超24h旧闻微降权
    except Exception:
        pass

    # 宏观新闻/企业CSR/财务新闻惩罚统一应用（在全部加分后扣减，避免被保底逻辑抵消）
    score -= (macro_penalty + csr_penalty + corp_penalty)

    # UK/EU compliance boost (合规政策对于英国站卖家是高优先级)
    compliance_keywords = ["ppwr", "gpsr", "epr", "ukca", "ce marking", "英代", "欧代"]
    if any(k in text for k in compliance_keywords):
        score += 0.05

    # 非目标市场降权（避免印度/俄罗斯/中东等非相关市场占据精选）
    non_target_market_keywords = [
        "印度", "印度站", "india",
        "俄罗斯", "俄", "ozon", "wildberries",
        "中东", "noon", "迪拜", "沙特", "阿联酋",
        "日本站", "澳洲站", "加拿大站",
    ]
    has_non_target = any(k in text for k in non_target_market_keywords)
    if has_non_target and not (has_uk or has_eu):
        score -= 0.10  # 非目标市场且无UK/EU关联 → 降权
        if not has_amazon:
            score -= 0.05  # 非Amazon的非目标市场 → 额外降权

    # 非Amazon平台降权（非核心平台，不与上面重复计算）
    non_amazon_platforms = ["ebay", "shopee", "lazada", "walmart", "jumia",
                            "美客多", "mercadolibre", "depop"]
    if any(k in text for k in non_amazon_platforms) and not has_amazon:
        score -= 0.15

    # Ensure threshold for strong signals
    # 注意：命中宏观/CSR/财务噪音、推广内容、标题党或竞品平台的条目不享受保底抬分
    # （否则惩罚会被max()抵消）
    promo_hits = matched_keywords(text, PROMOTION_NOISE)
    clickbait_hits = matched_keywords(text, CLICKBAIT_NOISE)
    has_competitor = (any(k in text for k in competitor_platforms) and not has_amazon)
    if (macro_penalty == 0.0 and csr_penalty == 0.0 and corp_penalty == 0.0
            and not promo_hits and not clickbait_hits and not has_competitor):
        if has_cross:
            score = max(score, CROSS_RELEVANCE_THRESHOLD)
        elif has_en_signal and has_ecommerce:
            score = max(score, CROSS_RELEVANCE_THRESHOLD)
        elif has_policy_logistics and (has_uk or has_eu):
            # 政策/物流信号 + UK/EU上下文 → 允许过线（如皇家邮政关税提醒）
            score = max(score, CROSS_RELEVANCE_THRESHOLD)

    return _result(
        is_cross_related=True,
        score=score,
        label=_label_for_text(text, has_ecommerce),
        reason="matched_cross_border_signal" if has_cross else "matched_ecommerce_signal",
        signals=all_good_signals,
        noise=noise,
    )


# ──────────────────────────────────────────────────────────────
# Convenience functions
# ──────────────────────────────────────────────────────────────


def is_cross_related_record(record: dict[str, Any]) -> bool:
    """Return True if the record is cross-border e-commerce related."""
    return bool(score_cross_relevance(record)["is_cross_related"])


def add_cross_relevance_fields(record: dict[str, Any]) -> dict[str, Any]:
    """Return a new dict with cross-border relevance fields added.

    Added fields:
        - cross_is_related: bool
        - cross_score: float
        - cross_label: str
        - cross_relevance_reason: str
        - cross_signals: list[str]
        - cross_platforms: list[str]
        - cross_sites: list[str]
    """
    relevance = score_cross_relevance(record)
    out = dict(record)
    out["cross_is_related"] = relevance["is_cross_related"]
    out["cross_score"] = relevance["score"]
    out["cross_label"] = relevance["label"]
    out["cross_relevance_reason"] = relevance["reason"]
    out["cross_signals"] = relevance["signals"]

    # Platform detection
    text = f"{out.get('title','')} {out.get('source','')} {out.get('site_name','')}".lower()
    platforms = []
    for pk in PLATFORM_KEYWORDS:
        if pk in text:
            platforms.append(pk)
    out["cross_platforms"] = list(set(platforms))

    # Site/region detection
    sites = []
    for site_key, site_kws in SITE_KEYWORDS.items():
        if any(kw in text for kw in site_kws):
            sites.append(site_key)
    out["cross_sites"] = sites

    return out
