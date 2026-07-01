#!/usr/bin/env python3
"""Cross-border e-commerce relevance scoring for news records."""

from __future__ import annotations

import re
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
# Seller action keywords
# ──────────────────────────────────────────────────────────────
SELLER_ACTION_KEYWORDS = [
    "运营",
    "打法",
    "策略",
    "技巧",
    "经验",
    "案例",
    "实战",
    "教程",
    "入门",
    "进阶",
    "优化",
    "提升",
    "增长",
    "爆单",
    "出单",
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
    "amazon_news": 0.40,
    "shopee_news": 0.40,
    "lazada_news": 0.40,
    "temu_news": 0.40,
    "shopify_blog": 0.35,
    "amazon_ads": 0.35,
    "marketplace_pulse": 0.35,
    "ecommercebytes": 0.35,
    "practicalecommerce": 0.30,
    "oberlo": 0.30,
    "oberlo_blog": 0.30,
    "junglescout": 0.30,
    "helium10": 0.30,
    "seller_central": 0.30,
    "payoneer": 0.25,
    "worldfirst": 0.25,
    "pingpong": 0.25,
    "crossborder_ebay": 0.25,
    "walmart_seller": 0.25,
    "google_shopping": 0.20,
    "meta_ads": 0.20,
}

# Sources that are always cross-border relevant by default
CROSS_DEFAULT_SOURCES = {
    "amazon_news",
    "shopee_news",
    "lazada_news",
    "temu_news",
    "marketplace_pulse",
    "ecommercebytes",
}

# ──────────────────────────────────────────────────────────────
# Labels
# ──────────────────────────────────────────────────────────────
LABEL_KEYWORDS = [
    ("policy_update", ["政策", "policy", "规则", "法规", "合规", "compliance", "新规", "变更"]),
    ("fee_logistics", ["费用", "fee", "物流", "logistics", "配送", "仓储", "海外仓", "fba费"]),
    ("advertising", ["广告", "ppc", "acos", "sponsored", "cpc", "bid", "投放"]),
    ("listing_product", ["listing", "asin", "选品", "上架", "产品", "品牌", "review"]),
    ("platform_trend", ["趋势", "增长", "市场", "份额", "prime day", "旺季", "黑五"]),
    ("seller_action", ["封号", "冻结", "受限", "紧急", "截止", "倒计时", "注意"]),
]

CROSS_RELEVANCE_THRESHOLD = 0.65

# ──────────────────────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────────────────────


def contains_any_keyword(haystack: str, keywords: list[str]) -> bool:
    """Check if any keyword appears in the haystack (case-insensitive)."""
    h = haystack.lower()
    return any(k in h for k in keywords)


def matched_keywords(haystack: str, keywords: list[str]) -> list[str]:
    """Return sorted list of unique keywords found in haystack."""
    h = haystack.lower()
    return sorted({k for k in keywords if k in h})


def contains_meaningful_cross_signal(haystack: str) -> bool:
    """Check for strong cross-border e-commerce signals."""
    h = haystack.lower()
    if MEANINGFUL_EN_SIGNAL_RE.search(h):
        return True
    # Strong Chinese signals
    strong_signals = [
        "跨境电商", "跨境", "海外仓", "保税仓", "出口电商",
        "进口电商", "速卖通", "独立站", "全球开店",
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
    text = f"{title} {source} {site_name} {url}".lower()

    # Gather signals
    cross_signals = matched_keywords(text, CROSS_KEYWORDS)
    ecommerce_signals = matched_keywords(text, ECOMMERCE_KEYWORDS)
    policy_signals = matched_keywords(text, POLICY_KEYWORDS)
    logistics_signals = matched_keywords(text, LOGISTICS_KEYWORDS)
    all_good_signals = cross_signals + ecommerce_signals + policy_signals + logistics_signals

    noise = matched_keywords(text, NOISE_KEYWORDS) + matched_keywords(text, DOMESTIC_ECOMMERCE_NOISE) + matched_keywords(text, PROMOTION_NOISE)
    source_prior = SOURCE_PRIORS.get(site_id, 0.0)

    # ── Trusted sources: default keep ──────────────────────────
    if site_id in CROSS_DEFAULT_SOURCES:
        return _result(
            is_cross_related=True,
            score=max(CROSS_RELEVANCE_THRESHOLD, 0.72 + source_prior),
            label=_label_for_text(text, bool(ecommerce_signals)),
            reason="trusted_cross_source_default_keep",
            signals=all_good_signals or [site_id],
            noise=noise,
        )

    # ── Analyze signal strength ────────────────────────────────
    has_cross = contains_meaningful_cross_signal(text)
    has_ecommerce = contains_any_keyword(text, ECOMMERCE_KEYWORDS)
    has_en_signal = EN_SIGNAL_RE.search(text) is not None

    # Seller-relevance penalty: if platform keyword present but no seller-relevance keyword
    has_platform = contains_any_keyword(text, PLATFORM_KEYWORDS)
    has_seller_relevance = contains_any_keyword(text, SELLER_RELEVANCE_KEYWORDS)

    # ── No signal at all ───────────────────────────────────────
    if not (has_cross or has_ecommerce or has_en_signal):
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

    # Amazon platform boost (核心平台优先)
    amazon_keywords = ["亚马逊", "amazon", "fba", "fbm", "prime", "seller central"]
    if any(k in text for k in amazon_keywords):
        score += 0.08

    # eBay/非Amazon平台降权（非核心平台）
    non_amazon_platforms = ["ebay", "shopee", "lazada", "walmart", "wildberries", "jumia"]
    if any(k in text for k in non_amazon_platforms) and not any(k in text for k in amazon_keywords):
        score -= 0.10

    # Ensure threshold for strong signals
    if has_cross:
        score = max(score, CROSS_RELEVANCE_THRESHOLD)
    elif has_en_signal and has_ecommerce:
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
