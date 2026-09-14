#!/usr/bin/env python3
"""跨境电商新闻聚合 — 从多个跨境信息源采集、去重、打分，生成24小时更新快照。"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
import random
import re
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dtparser
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from scripts.cross_relevance import add_cross_relevance_fields, score_cross_relevance
except ModuleNotFoundError:
    from cross_relevance import add_cross_relevance_fields, score_cross_relevance

try:
    import feedparser
except ModuleNotFoundError:
    feedparser = None

UTC = timezone.utc
BJ_TZ = ZoneInfo("Asia/Shanghai")
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
# JINA_PREFIX no longer used — direct HTML parsing preferred
BROWSERACT_BROWSER_ID = "chrome_local_103642719185797272"

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def iso(dt: datetime | None) -> str | None:
    if not dt:
        return None
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_iso(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    try:
        dt = dtparser.parse(dt_str)
    except Exception:
        return None
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=UTC)
    dt = dt.astimezone(UTC)
    # 防御：源RSS pubDate格式异常（如 ennews "32, 04 Aug 2026" 被dateutil解析为2032年）
    # 未来时间戳会让24h窗口过滤失效，archive历史条目每天全量复活
    if dt.year > utc_now().year:
        # 尝试提取 "04 Aug 2026" 标准部分重新解析
        m = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b", dt_str)
        if m:
            try:
                dt2 = dtparser.parse(f"{m.group(1)} {m.group(2)} {m.group(3)}")
                if not dt2.tzinfo:
                    dt2 = dt2.replace(tzinfo=UTC)
                dt2 = dt2.astimezone(UTC)
                if dt2.year <= utc_now().year:
                    return dt2
            except Exception:
                pass
        return None
    return dt


def normalize_url(raw_url: str) -> str:
    try:
        parsed = urlparse(raw_url.strip())
        if not parsed.scheme:
            return raw_url.strip()
        query = []
        for k, v in parse_qsl(parsed.query, keep_blank_values=True):
            lk = k.lower()
            if lk.startswith("utm_") or lk in {"ref", "spm", "fbclid", "gclid"}:
                continue
            query.append((k, v))
        parsed = parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
            fragment="",
            query=urlencode(query, doseq=True),
        )
        return urlunparse(parsed).rstrip("/")
    except Exception:
        return raw_url.strip()


def host_of_url(raw_url: str) -> str:
    try:
        return urlparse(raw_url).netloc.lower()
    except Exception:
        return ""


def first_non_empty(*values: Any) -> str:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def maybe_fix_mojibake(text: str) -> str:
    """Fix double-encoded UTF-8 (UTF-8 bytes misread as Latin-1 then re-encoded)."""
    s = (text or "").strip()
    if not s or not any(ord(c) > 127 for c in s):
        return s
    for enc in ("latin1", "cp1252"):
        try:
            fixed = s.encode(enc).decode("utf-8")
            if fixed != s and has_cjk(fixed):
                return fixed
        except Exception:
            continue
    return s


def parse_html(resp: requests.Response) -> BeautifulSoup:
    """按响应字节的真实编码解析HTML，避免中文乱码。

    某些站点（如 cifnews 雨果跨境）响应头不带 charset，requests 默认按
    ISO-8859-1 解码 resp.text，导致中文标题乱码（UTF-8 被双读），
    进而影响打分关键词匹配。用 resp.content + apparent_encoding 修复。
    """
    encoding = resp.apparent_encoding or "utf-8"
    try:
        return BeautifulSoup(resp.content, "html.parser", from_encoding=encoding)
    except Exception:
        return BeautifulSoup(resp.text, "html.parser")


def make_item_id(site_id: str, source: str, title: str, url: str) -> str:
    key = "||".join([
        site_id.strip().lower(),
        source.strip().lower(),
        title.strip().lower(),
        normalize_url(url),
    ])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def event_time(record: dict[str, Any]) -> datetime | None:
    return (
        parse_iso(record.get("published_at"))
        or parse_iso(record.get("first_seen_at"))
    )


def create_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": BROWSER_UA})
    retries = Retry(total=1, backoff_factor=0.3, status_forcelist=[502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


# ---------------------------------------------------------------------------
# RawItem 数据类
# ---------------------------------------------------------------------------

@dataclass
class RawItem:
    site_id: str
    site_name: str
    source: str
    title: str
    url: str
    published_at: datetime | None
    meta: dict[str, Any]


# ---------------------------------------------------------------------------
# BrowserAct 通用采集器（用于 JS 渲染的中文站点）
# ---------------------------------------------------------------------------

def fetch_via_browseract(
    url: str,
    site_id: str,
    site_name: str,
    source_label: str,
    url_pattern: str,
    base_url: str,
    max_items: int = 40,
    url_regex: str | None = None,
) -> list[RawItem]:
    """Fetch articles from a JS-rendered page using BrowserAct CLI.

    `url_pattern` 是子串粗筛（在浏览器内 JS 执行，性能好）；
    `url_regex` 是可选的正则精筛（在 Python 侧执行），用于排除同域导航链接。
    例：gs.amazon.cn 的 /policy /fba /service 也含 "amazon.cn" 子串，
    必须用 url_regex 精筛 /news/news- 才能只有真公告。

    Falls back to empty list if BrowserAct is unavailable or fails.
    """
    session_name = f"fetch-{uuid.uuid4().hex[:8]}"
    items: list[RawItem] = []
    _regex = re.compile(url_regex) if url_regex else None

    # Build JS extraction script
    js_extract = (
        "(function() {"
        "var links = document.querySelectorAll('a[href]');"
        "var results = [];"
        "var seen = {};"
        "for (var i = 0; i < links.length; i++) {"
        "  var a = links[i];"
        "  var href = a.href || '';"
        "  var title = (a.textContent || '').trim().replace(/\\s+/g, ' ');"
        "  if (title.length < 8) continue;"
        "  if (!href.startsWith('http')) continue;"
        f"  if (href.indexOf('{url_pattern}') === -1) continue;"
        "  var key = title + '||' + href;"
        "  if (seen[key]) continue;"
        "  seen[key] = true;"
        "  results.push({title: title, url: href});"
        f"  if (results.length >= {max_items}) break;"
        "}"
        "return JSON.stringify(results);"
        "})();"
    )
    js_path = f"/tmp/ba_extract_{session_name}.js"

    try:
        # Step 1: Open browser and navigate
        subprocess.run(
            ["browser-act", "--session", session_name, "browser", "open",
             BROWSERACT_BROWSER_ID, url],
            capture_output=True, encoding="utf-8", errors="replace", timeout=25,
        )
        # Step 2: Wait for page load
        time.sleep(3)

        # Step 3: Run JS extraction via stdin
        result = subprocess.run(
            ["browser-act", "--session", session_name, "eval", "--stdin"],
            input=js_extract,
            capture_output=True, encoding="utf-8", errors="replace", timeout=25,
        )

        if result.returncode == 0 and result.stdout.strip():
            raw = result.stdout.strip()
            # Try to parse JSON from output (may have leading/trailing text)
            json_start = raw.find("[")
            json_end = raw.rfind("]")
            if json_start >= 0 and json_end > json_start:
                parsed = json.loads(raw[json_start:json_end + 1])
                for entry in parsed:
                    title = maybe_fix_mojibake(str(entry.get("title", "")).strip())
                    link = str(entry.get("url", "")).strip()
                    if _regex and not _regex.search(link):
                        continue
                    if title and link:
                        items.append(RawItem(
                            site_id=site_id, site_name=site_name,
                            source=source_label, title=title,
                            url=normalize_url(link),
                            published_at=None, meta={},
                        ))
    except subprocess.TimeoutExpired:
        print(f"  [WARN] BrowserAct timeout for {site_name}")
    except Exception as e:
        print(f"  [WARN] BrowserAct failed for {site_name}: {e}")
    finally:
        # Cleanup session
        try:
            subprocess.run(
                ["browser-act", "session", "close", session_name],
                capture_output=True, text=True, timeout=10,
            )
        except Exception:
            pass
        try:
            Path(js_path).unlink(missing_ok=True)
        except Exception:
            pass

    return items


def dedupe_by_path(items: list[RawItem]) -> list[RawItem]:
    """按 URL 路径去重，同一路径保留最短标题。

    列表页常见问题：同一文章的标题链接和正文摘要链接指向同一 URL，
    摘要文本也是 <a> 内容 → 采集到重复条目。
    真实标题通常比摘要短，故保留最短的那个。
    """
    best: dict[str, RawItem] = {}
    order: list[str] = []
    for it in items:
        parsed = urlparse(it.url)
        key = f"{parsed.netloc}{parsed.path}"
        cur = best.get(key)
        if cur is None:
            best[key] = it
            order.append(key)
        elif len(it.title) < len(cur.title):
            best[key] = it
    return [best[k] for k in order]


def fetch_html_links(
    session: requests.Session,
    url: str,
    site_id: str,
    site_name: str,
    source_label: str,
    url_patterns: tuple[str, ...],
    base_url: str,
    min_title_len: int = 10,
    max_items: int = 30,
    exclude_title_re: str = "",
    dedupe_by_path: bool = True,
    timeout: int = 20,
) -> list[RawItem]:
    """通用 HTML 链接抽取 fallback（本地无 Chrome/BrowserAct 不可用时使用）。

    用于 JS 渲染站点的兜底采集：这些站点的列表页服务端已渲染部分内容，
    直接 requests + BeautifulSoup 即可拿到文章链接。

    Args:
        url_patterns: href 必须包含其中任一子串才算文章链接
        exclude_title_re: 标题匹配此正则则跳过（如脏导航项、含日期的旧条目）
        dedupe_by_path: 按 href 路径去重（列表页常有"回复"等多个锚点指向同帖）
    """
    items: list[RawItem] = []
    exclude_re = re.compile(exclude_title_re) if exclude_title_re else None
    seen_paths: set[str] = set()
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        soup = parse_html(resp)
        for a in soup.find_all("a", href=True):
            title = maybe_fix_mojibake(a.get_text(strip=True))
            href = a["href"].strip()
            if len(title) < min_title_len:
                continue
            if not any(p in href for p in url_patterns):
                continue
            if exclude_re and exclude_re.search(title):
                continue
            if not href.startswith("http"):
                href = urljoin(base_url, href)
            # 按路径去重（去掉锚点/查询串）
            parsed = urlparse(href)
            path_key = f"{parsed.netloc}{parsed.path}"
            if dedupe_by_path and path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            items.append(RawItem(
                site_id=site_id, site_name=site_name, source=source_label,
                title=title, url=normalize_url(href),
                published_at=None, meta={},
            ))
            if len(items) >= max_items:
                break
    except Exception as e:
        print(f"  [WARN] {site_name} HTML fallback failed: {e}")
    return items



def translate_title(title: str, title_cache: dict[str, str]) -> str:
    """Translate an English title to Chinese using MyMemory free API.

    - Skips if title already contains CJK characters
    - Uses cache to avoid duplicate API calls
    - Rate-limited to 1 request per second
    - Returns translated title or empty string if translation fails
    """
    if not title or has_cjk(title):
        return ""
    if title in title_cache:
        return title_cache[title]
    try:
        from urllib.parse import quote
        encoded = quote(title[:500])
        api_url = f"https://api.mymemory.translated.net/get?q={encoded}&langpair=en|zh"
        resp = requests.get(api_url, timeout=8, headers={"User-Agent": BROWSER_UA})
        if resp.status_code == 200:
            data = resp.json()
            translated = data.get("responseData", {}).get("translatedText", "")
            if translated and translated != title and has_cjk(translated):
                title_cache[title] = translated
                time.sleep(1)  # Rate limit
                return translated
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# RSS/Atom 通用解析
# ---------------------------------------------------------------------------

def parse_feed_entries(feed_xml: bytes) -> list[dict[str, Any]]:
    """解析 RSS/Atom XML 条目。

    2026-09-14 新增 feedparser fallback：ET.fromstring 是严格 XML 解析器，
    遇到源方未转义的裸字符（如 Amazon Ads RSS 的 em-dash "—"）会整体抛异常
    → 该源全部条目被丢弃，症状表现为"curl 能拿到内容但脚本解析 0 条"。
    feedparser 容错解析可读取这类 feed。
    """
    out = _parse_feed_et(feed_xml)
    if out:
        return out
    # ET 严格解析失败或 0 条 → 尝试 feedparser 容错解析
    if feedparser is not None:
        try:
            parsed = feedparser.parse(feed_xml)
            for entry in parsed.entries or []:
                title = str(entry.get("title") or "").strip()
                link = str(entry.get("link") or "").strip()
                if not title or not link:
                    continue
                published = (
                    entry.get("published") or entry.get("updated")
                    or entry.get("pubDate") or ""
                )
                desc = str(entry.get("summary") or entry.get("description") or "").strip()
                out.append({"title": title, "link": link,
                            "published": published, "desc": desc})
        except Exception:
            pass
    return out


def _parse_feed_et(feed_xml: bytes) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    try:
        root = ET.fromstring(feed_xml)
    except Exception:
        return out
    for tag in (".//item", ".//{*}item", ".//entry", ".//{*}entry"):
        for node in root.findall(tag):
            title = (node.findtext("title") or node.findtext("{*}title") or "").strip()
            link = ""
            link_node = node.find("link")
            if link_node is None:
                link_node = node.find("{*}link")
            if link_node is not None:
                link = (link_node.get("href") or link_node.text or "").strip()
            if not link:
                link = (node.findtext("{*}link") or node.findtext("link") or "").strip()
            published = (
                node.findtext("pubDate") or node.findtext("{*}pubDate")
                or node.findtext("published") or node.findtext("{*}published")
                or node.findtext("updated") or node.findtext("{*}updated")
            )
            desc = (node.findtext("description") or node.findtext("{*}summary") or "").strip()
            if title and link:
                key = (title, link)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"title": title, "link": link, "published": published, "desc": desc})
    return out


def fetch_rss(session: requests.Session, url: str, site_id: str, site_name: str,
              source_label: str, max_age_hours: int = 48) -> list[RawItem]:
    """通用 RSS 抓取器。"""
    items: list[RawItem] = []
    try:
        resp = session.get(url, timeout=12)
        resp.raise_for_status()
        entries = parse_feed_entries(resp.content)
        if not entries:
            print(f"  [WARN] RSS parsed 0 entries for {site_name} ({url}) — content may be blocked/non-XML (CDN/IP issue)")
        now = utc_now()
        cutoff = now - timedelta(hours=max_age_hours)
        for entry in entries[:50]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            if not title or not link:
                continue
            pub = parse_iso(entry.get("published"))
            if pub and pub < cutoff:
                continue
            items.append(RawItem(
                site_id=site_id,
                site_name=site_name,
                source=source_label,
                title=title,
                url=normalize_url(link),
                published_at=pub,
                meta={"desc": entry.get("desc", "")[:200]},
            ))
    except Exception as e:
        print(f"  [WARN] RSS fetch failed for {site_name} ({url}): {e}")
    return items


# ---------------------------------------------------------------------------
# 亚马逊官方源
# ---------------------------------------------------------------------------

def fetch_amazon_newsroom(session: requests.Session, now: datetime) -> list[RawItem]:
    """Amazon Newsroom — 官方新闻。

    2026-09-14 排查结论：该源**长期 0 条是正常的，不是 bug**。
    ① 更新频率低：实测最新条目距今 58-110h；
    ② 内容对企业无决策价值：返回的是 water conservation / communities /
       塑料包装 / 涨薪 等 CSR+企业新闻，正好命中 CSR_NOISE_KEYWORDS 被降权。
    48h 窗口已完整覆盖 24h 展示窗口（任何 24h 内发布的都能抓到），
    再放宽窗口只会把过期条目灌进 archive 而不可能上展示——故维持 48h。
    """
    return fetch_rss(session, "https://www.aboutamazon.com/news/feed",
                     "amazon_newsroom", "Amazon Newsroom", "Amazon官方")


def fetch_sp_api_changelog(session: requests.Session, now: datetime) -> list[RawItem]:
    """SP-API 变更日志。"""
    return fetch_rss(session, "https://developer-docs.amazon.com/sp-api/changelog.rss",
                     "sp_api", "SP-API Changelog", "SP-API变更", max_age_hours=168)


def fetch_amazon_ads_blog(session: requests.Session, now: datetime) -> list[RawItem]:
    """Amazon Ads 官方更新。

    2026-09-14 变更：原 https://advertising.amazon.com/blog/feed 返回 404（Amazon 已下线该 RSS）。
    改用 Amazon Ads API Release Notes RSS（CloudFront，100条，验证可访问）——
    对跑 SP-API/广告自动化的卖家直接有用。
    """
    return fetch_rss(session, "https://d3a0d0y2hgofx6.cloudfront.net/rss/en-us/ad-api-rss.xml",
                     "amazon_ads", "Amazon Ads 更新", "亚马逊广告")


def fetch_amz123(session: requests.Session, now: datetime) -> list[RawItem]:
    """AMZ123 跨境快讯 — BrowserAct + HTML fallback."""
    items = fetch_via_browseract(
        url="https://www.amz123.com/kx",
        site_id="amz123", site_name="AMZ123", source_label="跨境快讯",
        url_pattern="/kx/", base_url="https://www.amz123.com",
    )
    if not items:
        try:
            resp = session.get("https://www.amz123.com/kx", timeout=15)
            resp.raise_for_status()
            soup = parse_html(resp)
            for a in soup.find_all("a", href=True):
                title = a.get_text(strip=True)
                href = a["href"]
                if len(title) < 8 or not href.startswith(("http", "/")):
                    continue
                if "/kx/" not in href and "/article/" not in href:
                    continue
                if not href.startswith("http"):
                    href = urljoin("https://www.amz123.com", href)
                items.append(RawItem(
                    site_id="amz123", site_name="AMZ123", source="跨境快讯",
                    title=title, url=normalize_url(href),
                    published_at=None, meta={},
                ))
        except Exception as e:
            print(f"  [WARN] AMZ123 fallback HTML fetch failed: {e}")
    return dedupe_by_path(items)[:40]


def fetch_amzdh(session: requests.Session, now: datetime) -> list[RawItem]:
    """AMZDH 跨境头条 — BrowserAct + HTML fallback。

    2026-09-14：fallback 改用共享 fetch_html_links（原内联实现超时长、
    标题脏——列表页混入带日期后缀的旧条目，需按 /kjtt/NNNNN.html 精确匹配）。
    """
    items = fetch_via_browseract(
        url="https://www.amzdh.com/kjtt/",
        site_id="amzdh", site_name="AMZDH", source_label="跨境头条",
        url_pattern="/kjtt/", base_url="https://www.amzdh.com",
    )
    if not items:
        items = fetch_html_links(
            session,
            url="https://www.amzdh.com/kjtt/",
            site_id="amzdh", site_name="AMZDH", source_label="跨境头条",
            url_patterns=("/kjtt/",),
            base_url="https://www.amzdh.com",
            min_title_len=10,
            max_items=30,
            # 排除带日期后缀的旧条目（如 "...2024-06-03 11:55:32"）
            exclude_title_re=r"\d{4}-\d{2}-\d{2}",
        )
    return dedupe_by_path(items)[:30]


def fetch_cifnews(session: requests.Session, now: datetime) -> list[RawItem]:
    """雨果跨境 — BrowserAct + HTML fallback."""
    items = fetch_via_browseract(
        url="https://www.cifnews.com/",
        site_id="cifnews", site_name="雨果跨境", source_label="跨境资讯",
        url_pattern="cifnews.com", base_url="https://www.cifnews.com",
    )
    if not items:
        try:
            resp = session.get("https://www.cifnews.com/", timeout=15)
            resp.raise_for_status()
            soup = parse_html(resp)
            for a in soup.find_all("a", href=True):
                title = a.get_text(strip=True)
                href = a["href"]
                if len(title) < 10 or not href.startswith(("http", "/")):
                    continue
                if "/article/" not in href and "/news/" not in href:
                    continue
                if not href.startswith("http"):
                    href = urljoin("https://www.cifnews.com", href)
                items.append(RawItem(
                    site_id="cifnews", site_name="雨果跨境", source="跨境资讯",
                    title=title, url=normalize_url(href),
                    published_at=None, meta={},
                ))
        except Exception as e:
            print(f"  [WARN] cifnews fallback HTML fetch failed: {e}")
    return dedupe_by_path(items)[:30]


def fetch_kjds365(session: requests.Session, now: datetime) -> list[RawItem]:
    """跨境电商365 — 博客文章。

    2026-09-14 修复：该站首页是**导航站**（工具/课程/书单/社媒链接为主），
    原实现只要求 href 含 "kjds365.cn"，导致采到大量导航项：
      /sites/128.html（店小秘-跨境电商多平台采集上架…）
      /sites/135.html（领星ERP，亚马逊利润核算，广告管理）
      /amz_restock_table（亚马逊FBA补货周期表）
    这些锚文本含"跨境/亚马逊/ERP"等词，打分拿到 0.63-0.75，白占展示位。
    真文章路径形如 /894.html、/890.html（根目录纯数字 .html）。
    此处改为只收该形式的正文页。
    """
    items: list[RawItem] = []
    try:
        resp = session.get("https://kjds365.cn/", timeout=15)
        resp.raise_for_status()
        soup = parse_html(resp)
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("http"):
                href = urljoin("https://kjds365.cn", href)
            # 只收正文页 /<数字>.html，排除 /sites/ /tag/ /bulletin/ 等导航项
            if not re.search(r"kjds365\.cn/\d+\.html?$", href):
                continue
            title = a.get_text(strip=True)
            if len(title) < 8:
                continue
            items.append(RawItem(
                site_id="kjds365", site_name="跨境电商365", source="跨境电商365",
                title=title, url=normalize_url(href),
                published_at=None, meta={},
            ))
    except Exception as e:
        print(f"  [WARN] kjds365 HTML fetch failed: {e}")
    return dedupe_by_path(items)[:20]


def _is_nav_pollution(item: dict) -> bool:
    """判断条目是否为导航/目录页污染（不该被当成新闻）。

    2026-09-14 事故：gs.amazon.cn 导航菜单（/policy /fba /sell …）与
    kjds365.cn 工具目录（/sites/NNN.html、/amz_restock_table …）被当新闻
    采集，锚文本含"政策/FBA/跨境/亚马逊"等词 → 打分 0.60-0.78 高分 →
    挤掉真公告。已在采集层修掉，但归档里 21 天窗口的旧污染会持续展示，
    故此处再设一道持久化防线：读到归档时直接丢弃。
    """
    url = item.get("url") or ""
    sid = item.get("site_id") or ""
    if sid == "gs_amazon" and "/news/news-" not in url:
        return True
    if sid == "kjds365" and not re.search(r"kjds365\.cn/\d+\.html?$", url):
        return True
    return False


def fetch_gs_amazon_cn(session: requests.Session, now: datetime) -> list[RawItem]:
    """亚马逊全球开店中文 — 公告列表。

    2026-09-14 修复：此前把整页所有 <a> 都当新闻收，实测 /news 页上
    导航菜单链接（/policy、/fba、/sell、/nsi、/category…）比真新闻还多
    （实测 50 导航 vs 26 新闻）。这些导航项的锚文本恰含"政策/FBA/跨境"
    等词，在打分器里拿到 0.60-0.78 高分，白占 7/43 个展示位，而真公告被
    挤出。此处按 URL 特征只保留真公告：路径含 /news/news-。

    另清理标题：列表页会带 "● " 前缀和 "……[详细]" 截断后缀。
    """
    items: list[RawItem] = []
    try:
        resp = session.get("https://gs.amazon.cn/news", timeout=15)
        resp.raise_for_status()
        soup = parse_html(resp)
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("http"):
                href = urljoin("https://gs.amazon.cn", href)
            # 只收真公告：导航菜单链接（/policy /fba /sell /nsi …）不是新闻
            if "/news/news-" not in href:
                continue
            title = _clean_gs_amazon_title(a.get_text(strip=True))
            if len(title) < 8:
                continue
            items.append(RawItem(
                site_id="gs_amazon", site_name="亚马逊全球开店", source="全球开店公告",
                title=title, url=normalize_url(href),
                published_at=None, meta={},
            ))
    except Exception as e:
        print(f"  [WARN] gs.amazon.cn fetch failed: {e}")
    return dedupe_by_path(items)[:30]


def _clean_gs_amazon_title(raw: str) -> str:
    """清理 gs.amazon.cn 列表页标题：去 "● " 前缀、"……[详细]" 截断后缀、
    合并空白。"""
    t = raw.strip()
    t = re.sub(r"^[●•·\-\*]\s*", "", t)
    t = re.sub(r"[..…\u2026]+\[详细\]\s*$", "", t)
    t = re.sub(r"\[详细\]\s*$", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


# ---------------------------------------------------------------------------
# 英文跨境源
# ---------------------------------------------------------------------------

def fetch_ecomengine(session: requests.Session, now: datetime) -> list[RawItem]:
    """EcomEngine — 卖家新闻，直接HTML解析。"""
    items: list[RawItem] = []
    try:
        resp = session.get("https://www.ecomengine.com/amazon-seller-news", timeout=15)
        resp.raise_for_status()
        soup = parse_html(resp)
        for a in soup.find_all("a", href=True):
            title = a.get_text(strip=True)
            href = a["href"]
            if len(title) < 10 or not href.startswith("http"):
                continue
            if "ecomengine.com" not in href:
                continue
            items.append(RawItem(
                site_id="ecomengine", site_name="EcomEngine", source="卖家新闻",
                title=title, url=normalize_url(href),
                published_at=None, meta={},
            ))
    except Exception as e:
        print(f"  [WARN] EcomEngine fetch failed: {e}")
    return items[:20]


# ---------------------------------------------------------------------------
# 新增：英文卖家平台源
# ---------------------------------------------------------------------------

def fetch_ecommercenews(session: requests.Session, now: datetime) -> list[RawItem]:
    """Ecommerce News Europe — 欧洲电商行业新闻（替代已停刊的 EcommerceBytes）。

    2026-09-14 变更：EcommerceBytes 已于 2026-09 停刊
    （官网公告 "After 27 years, EcommerceBytes has ceased publication"），
    其 RSS 返回 5.8KB 的停刊公告 HTML 而非 XML，永久不可用。
    替换为 Ecommerce News Europe（欧洲电商行业新闻，RSS 正常，10条/feed）。
    """
    return fetch_rss(session, "https://ecommercenews.eu/feed/",
                     "ecommercenews", "Ecommerce News Europe", "欧洲电商新闻")


def fetch_channelx(session: requests.Session, now: datetime) -> list[RawItem]:
    """ChannelX — 全球Marketplace新闻，覆盖Temu/TikTok/Walmart/eBay等平台。

    2026-09-14 排查结论：该站更新频率低（实测周五集中发布），
    多数日子 0 条属正常。48h 抓取窗口已足够——**放宽窗口无法让内容上展示**：
    event_time 优先取 published_at，24h 展示窗口只看发布时间，
    抓取窗口只需 ≥24h 即可覆盖任何会展示的条目（详见 Pitfall 43）。
    """
    return fetch_rss(session, "https://channelx.world/feed/",
                     "channelx", "ChannelX", "平台新闻")


def fetch_marketplace_pulse(session: requests.Session, now: datetime) -> list[RawItem]:
    """Marketplace Pulse — 数据驱动的电商市场分析，HTML解析articles页面。"""
    items: list[RawItem] = []
    nav_titles = {"contact us", "privacy policy", "terms of use", "advertise with us",
                  "about", "careers", "press", "sign up", "subscribe", "login", "search"}
    try:
        resp = session.get("https://marketplacepulse.com/articles", timeout=15)
        resp.raise_for_status()
        soup = parse_html(resp)
        for a in soup.find_all("a", href=True):
            title = a.get_text(strip=True)
            href = a["href"]
            if len(title) < 10 or title.lower().strip() in nav_titles:
                continue
            if not href.startswith(("http", "/")):
                continue
            if not href.startswith("http"):
                href = urljoin("https://marketplacepulse.com", href)
            if "marketplacepulse.com" not in href or "/articles/" not in href:
                continue
            items.append(RawItem(
                site_id="marketplace_pulse", site_name="Marketplace Pulse", source="市场分析",
                title=title, url=normalize_url(href),
                published_at=None, meta={},
            ))
    except Exception as e:
        print(f"  [WARN] Marketplace Pulse fetch failed: {e}")
    return items[:15]


def fetch_ennews(session: requests.Session, now: datetime) -> list[RawItem]:
    """亿恩网 — 中文跨境行业头部媒体，日更5-10篇，直接RSS采集。
    RSS源返回的URL域名有误（www.en.com），需要修复为正确域名（www.ennews.com）。
    GHA runner（美国IP）实测被 ennews CDN 拦截：HTTP 200 但返回非XML内容
    （parse_feed_entries 解析出 0 条且无异常，2026-08-31 排查确认）。
    fallback 链：直连 → allorigins → r.jina.ai（两个代理对美区可达，
    直连成功时代理不触发，本地CN网络直连正常不受影响）。
    """
    items = fetch_rss(session, "https://www.ennews.com/rss.xml",
                     "ennews", "亿恩网", "跨境资讯")
    for it in items:
        if it.url and "www.en.com" in it.url:
            it.url = it.url.replace("www.en.com", "www.ennews.com").replace("http://", "https://")
    if items:
        return items
    # 代理 fallback：仅当直连解析出 0 条时触发
    proxies = [
        "https://api.allorigins.win/raw?url=https%3A%2F%2Fwww.ennews.com%2Frss.xml",
        "https://r.jina.ai/https://www.ennews.com/rss.xml",
    ]
    for proxy_url in proxies:
        try:
            resp = session.get(proxy_url, timeout=25)
            resp.raise_for_status()
            entries = parse_feed_entries(resp.content)
            if not entries:
                continue
            print(f"  [INFO] ennews recovered via proxy: {proxy_url.split('/')[2]} ({len(entries)} entries)")
            cutoff = now - timedelta(hours=48)
            for entry in entries[:50]:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                if not title or not link:
                    continue
                pub = parse_iso(entry.get("published"))
                if pub and pub < cutoff:
                    continue
                url_fixed = link.replace("www.en.com", "www.ennews.com").replace("http://", "https://")
                items.append(RawItem(
                    site_id="ennews", site_name="亿恩网", source="跨境资讯",
                    title=title, url=normalize_url(url_fixed),
                    published_at=pub, meta={"desc": entry.get("desc", "")[:200]},
                ))
            if items:
                break
        except Exception as e:
            print(f"  [WARN] ennews proxy fallback failed ({proxy_url.split('/')[2]}): {e}")
    return items


# ---------------------------------------------------------------------------
# 聚合源 (TopHub / Zeli / NewsNow)
# ---------------------------------------------------------------------------

def fetch_tophub_crossborder(session: requests.Session, now: datetime) -> list[RawItem]:
    """TopHub — 跨境电商相关热榜，直接HTML解析。"""
    items: list[RawItem] = []
    try:
        resp = session.get("https://tophub.today/", timeout=15)
        resp.raise_for_status()
        soup = parse_html(resp)
        for a in soup.find_all("a", href=True):
            title = a.get_text(strip=True)
            href = a["href"]
            if len(title) < 8 or not href.startswith(("http", "/")):
                continue
            if "/n/" not in href:
                continue
            if not href.startswith("http"):
                href = urljoin("https://tophub.today", href)
            items.append(RawItem(
                site_id="tophub", site_name="TopHub", source="热榜",
                title=title, url=normalize_url(href),
                published_at=None, meta={},
            ))
    except Exception as e:
        print(f"  [WARN] TopHub fetch failed: {e}")
    return items[:20]


def fetch_amazon_seller_blog(session: requests.Session, now: datetime) -> list[RawItem]:
    """Amazon Seller Blog (sell.amazon.com) — 官方卖家公告。

    2026-09-14：加 HTML fallback——本地 WSL 无 Chrome 时 BrowserAct 失败，
    该站公告列表页服务端已渲染，直接抓 /blog/announcements/ 下的链接即可。
    """
    items = fetch_via_browseract(
        url="https://sell.amazon.com/blog/announcements",
        site_id="amazon_seller_blog",
        site_name="Amazon卖家博客",
        source_label="官方公告",
        url_pattern="/blog/announcements/",
        base_url="https://sell.amazon.com",
        max_items=25,
    )
    if not items:
        items = fetch_html_links(
            session,
            url="https://sell.amazon.com/blog/announcements",
            site_id="amazon_seller_blog", site_name="Amazon卖家博客",
            source_label="官方公告",
            url_patterns=("/blog/announcements/",),
            base_url="https://sell.amazon.com",
            min_title_len=15,
            max_items=25,
            # 排除导航类通用标题（List products / Price products 等常青引导页）
            exclude_title_re=r"^(list products|price products|fulfill customer orders|create a brand store|how to sell new products)",
        )
    return dedupe_by_path(items)


def fetch_wearesellers(session: requests.Session, now: datetime) -> list[RawItem]:
    """知无不言论坛 — 中国亚马逊卖家最活跃的社区。

    2026-09-14：加 HTML fallback——本地 WSL 无 Chrome 时 BrowserAct 失败。
    社区首页服务端已渲染帖子列表，抓 /question/<id> 链接即可。
    """
    items = fetch_via_browseract(
        url="https://www.wearesellers.com/",
        site_id="wearesellers",
        site_name="知无不言",
        source_label="卖家社区",
        url_pattern="/question/",
        base_url="https://www.wearesellers.com",
        max_items=25,
    )
    if not items:
        items = fetch_html_links(
            session,
            url="https://www.wearesellers.com/",
            site_id="wearesellers", site_name="知无不言",
            source_label="卖家社区",
            url_patterns=("/question/",),
            base_url="https://www.wearesellers.com",
            min_title_len=12,
            max_items=25,
            # 跳过"回复"锚点等非标题项
            exclude_title_re=r"^(回复|赞|收藏|分享|登录|注册|搜索)$",
        )
    return dedupe_by_path(items)


# ---------------------------------------------------------------------------
# OPML RSS 支持
# ---------------------------------------------------------------------------

def fetch_opml_rss(now: datetime, opml_path: Path, max_feeds: int = 0
                   ) -> tuple[list[RawItem], dict[str, Any], list[dict[str, Any]]]:
    """从 OPML 文件读取 RSS 源并批量抓取。"""
    items: list[RawItem] = []
    feed_statuses: list[dict[str, Any]] = []
    try:
        tree = ET.parse(str(opml_path))
        root = tree.getroot()
    except Exception as e:
        return items, {"site_id": "opmlrss", "ok": False, "error": str(e)}, feed_statuses

    feeds: list[tuple[str, str]] = []
    for outline in root.iter("outline"):
        xml_url = outline.get("xmlUrl", "").strip()
        title = outline.get("title", "").strip() or outline.get("text", "").strip()
        if xml_url:
            feeds.append((title, xml_url))

    if max_feeds > 0:
        feeds = feeds[:max_feeds]

    ok_count = 0
    fail_count = 0

    def _fetch_one(title: str, xml_url: str) -> list[RawItem]:
        nonlocal ok_count, fail_count
        t0 = time.monotonic()
        try:
            resp = requests.get(xml_url, headers={"User-Agent": BROWSER_UA}, timeout=10)
            resp.raise_for_status()
            entries = parse_feed_entries(resp.content)
            ok_count += 1
            dur = int((time.monotonic() - t0) * 1000)
            feed_statuses.append({"title": title, "url": xml_url, "ok": True,
                                  "item_count": len(entries), "duration_ms": dur})
            out: list[RawItem] = []
            cutoff = now - timedelta(hours=48)
            for entry in entries[:30]:
                pub = parse_iso(entry.get("published"))
                if pub and pub < cutoff:
                    continue
                out.append(RawItem(
                    site_id="opmlrss",
                    site_name="OPML RSS",
                    source=title,
                    title=entry.get("title", "").strip(),
                    url=normalize_url(entry.get("link", "")),
                    published_at=pub,
                    meta={},
                ))
            return out
        except Exception as e:
            fail_count += 1
            dur = int((time.monotonic() - t0) * 1000)
            feed_statuses.append({"title": title, "url": xml_url, "ok": False,
                                  "error": str(e), "duration_ms": dur})
            return []

    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = {pool.submit(_fetch_one, t, u): (t, u) for t, u in feeds}
        for fut in as_completed(futs):
            try:
                items.extend(fut.result())
            except Exception:
                pass

    status = {
        "site_id": "opmlrss",
        "site_name": "OPML RSS",
        "ok": True,
        "item_count": len(items),
        "duration_ms": 0,
        "feed_count": len(feeds),
        "ok_feed_count": ok_count,
        "failed_feed_count": fail_count,
    }
    return items, status, feed_statuses


# ---------------------------------------------------------------------------
# 政策日历生成
# ---------------------------------------------------------------------------

def generate_policy_calendar(session: requests.Session, now: datetime) -> list[dict[str, Any]]:
    """从已知政策源提取即将生效的政策条目。"""
    policies: list[dict[str, Any]] = []
    # 已知的近期政策（硬编码 + 动态补充）
    known_policies = [
        {
            "title": "EU GPSR 通用产品安全法规（持续执行）",
            "effective_date": "2024-12-13",
            "platforms": ["Amazon EU"],
            "impact_level": "high",
            "description": "所有在欧盟销售的非食品类产品需有欧盟责任人、产品标签和安全信息。2026年Q2起Amazon已自动化检查，新建ASIN必须同步提交合规信息。"
        },
        {
            "title": "EU PPWR 包装法规（已生效）",
            "effective_date": "2026-08-12",
            "platforms": ["Amazon EU"],
            "impact_level": "high",
            "description": "未完成包装EPR合规注册的卖家，商品将面临下架。需在每个欧盟成员国单独注册包装EPR号码。"
        },
        {
            "title": "亚马逊泛欧计划MRN/EORI强制提交",
            "effective_date": "2026-09-01",
            "platforms": ["Amazon EU"],
            "impact_level": "high",
            "description": "非欧盟卖家开通泛欧计划后，发往所有欧盟FBA仓的每一票货件必须上传MRN和EORI。60天补报窗口期，逾期限制创建新货件。"
        },
        {
            "title": "欧洲FBA跨境送达窗口缩短至7天",
            "effective_date": "2026-09-01",
            "platforms": ["Amazon EU"],
            "impact_level": "medium",
            "description": "英德法意西五大站点非合作承运商的跨境FBA送达窗口从14天缩短至7天。"
        },
        {
            "title": "亚马逊英国站FBM自动备货时间(AHT)生效",
            "effective_date": "2026-09-01",
            "platforms": ["Amazon UK"],
            "impact_level": "medium",
            "description": "系统根据真实出单数据自动改写SKU备货时效，账户级仅保留0天/1天选项。SKU备货时间超实际表现30天将触发自动修改。"
        },
        {
            "title": "泛欧计划荷兰站强制上架",
            "effective_date": "2026-09-03",
            "platforms": ["Amazon EU"],
            "impact_level": "medium",
            "description": "泛欧ASIN须同步至荷兰站并保持可售，否则失去Pan-EU资格，切换为EFN跨境模式（物流费显著增加+丧失Prime标识）。比利时站2027-02-26生效。"
        },
        {
            "title": "亚马逊UK FBM商务时段送达率90%强制执行",
            "effective_date": "2026-09-30",
            "platforms": ["Amazon UK"],
            "impact_level": "high",
            "description": "FBM卖家须保持商务时段送达率≥90%。10月30日起不合规Listing将被下架给Amazon Business买家。月出单<20单豁免。"
        },
        {
            "title": "欧洲站锂电池合规要求升级",
            "effective_date": "2026-09-30",
            "platforms": ["Amazon EU"],
            "impact_level": "medium",
            "description": "欧洲站部分锂电池相关商品将执行更严格的合规审核。"
        },
        {
            "title": "Q4旺季FBA入仓截止日（Prime Big Deal Days）",
            "effective_date": "2026-09-16",
            "platforms": ["Amazon UK", "Amazon EU"],
            "impact_level": "high",
            "description": "UK站Prime Big Deal Days入仓截止9月16日。之后入仓的商品不具备Prime标识参与资格。"
        },
        {
            "title": "Q4旺季FBA旺季附加费生效",
            "effective_date": "2026-10-15",
            "platforms": ["Amazon UK", "Amazon DE"],
            "impact_level": "high",
            "description": "10月15日至2027年1月14日，UK站FBA旺季附加费平均£0.12/件（大信封£0.07/件），德国站€0.27/件。叠加1.5%燃油附加费。低价FBA/超大件/服装豁免。"
        },
        {
            "title": "Q4旺季FBA入仓截止日（Black Friday Week）",
            "effective_date": "2026-10-28",
            "platforms": ["Amazon UK", "Amazon EU"],
            "impact_level": "high",
            "description": "UK站黑五周入仓截止10月28日（Amazon优化分仓选项）。之后入仓不具备Prime标识参与资格。"
        },
        {
            "title": "Black Friday / Cyber Monday 2026",
            "effective_date": "2026-11-27",
            "platforms": ["Amazon UK", "Amazon EU", "Amazon US"],
            "impact_level": "high",
            "description": "黑五网一大促。Deal提交窗口已于7月8日开放，10月20日关闭。提前提交可省$50/promotion。"
        },
    ]
    return known_policies


# ---------------------------------------------------------------------------
# 主采集流程
# ---------------------------------------------------------------------------

BUILTIN_SOURCES: list[dict[str, Any]] = [
    {"func": "fetch_amazon_newsroom", "site_id": "amazon_newsroom", "site_name": "Amazon Newsroom", "kind": "official"},
    {"func": "fetch_sp_api_changelog", "site_id": "sp_api", "site_name": "SP-API Changelog", "kind": "official"},
    {"func": "fetch_amazon_ads_blog", "site_id": "amazon_ads", "site_name": "Amazon Ads Blog", "kind": "official"},
    {"func": "fetch_gs_amazon_cn", "site_id": "gs_amazon", "site_name": "全球开店", "kind": "official"},
    {"func": "fetch_amz123", "site_id": "amz123", "site_name": "AMZ123", "kind": "aggregate"},
    {"func": "fetch_amzdh", "site_id": "amzdh", "site_name": "AMZDH", "kind": "aggregate"},
    {"func": "fetch_cifnews", "site_id": "cifnews", "site_name": "雨果跨境", "kind": "aggregate"},
    {"func": "fetch_kjds365", "site_id": "kjds365", "site_name": "跨境电商365", "kind": "aggregate"},
    {"func": "fetch_ecomengine", "site_id": "ecomengine", "site_name": "EcomEngine", "kind": "industry"},
    {"func": "fetch_ecommercenews", "site_id": "ecommercenews", "site_name": "Ecommerce News Europe", "kind": "industry"},
    {"func": "fetch_channelx", "site_id": "channelx", "site_name": "ChannelX", "kind": "industry"},
    {"func": "fetch_marketplace_pulse", "site_id": "marketplace_pulse", "site_name": "Marketplace Pulse", "kind": "industry"},
    {"func": "fetch_tophub_crossborder", "site_id": "tophub", "site_name": "TopHub", "kind": "aggregate"},
    {"func": "fetch_amazon_seller_blog", "site_id": "amazon_seller_blog", "site_name": "Amazon卖家博客", "kind": "official"},
    {"func": "fetch_wearesellers", "site_id": "wearesellers", "site_name": "知无不言", "kind": "community"},
    {"func": "fetch_ennews", "site_id": "ennews", "site_name": "亿恩网", "kind": "aggregate"},
]

FETCH_FUNC_MAP: dict[str, Any] = {
    "fetch_amazon_newsroom": fetch_amazon_newsroom,
    "fetch_sp_api_changelog": fetch_sp_api_changelog,
    "fetch_amazon_ads_blog": fetch_amazon_ads_blog,
    "fetch_gs_amazon_cn": fetch_gs_amazon_cn,
    "fetch_amz123": fetch_amz123,
    "fetch_amzdh": fetch_amzdh,
    "fetch_cifnews": fetch_cifnews,
    "fetch_kjds365": fetch_kjds365,
    "fetch_ecomengine": fetch_ecomengine,
    "fetch_ecommercenews": fetch_ecommercenews,
    "fetch_channelx": fetch_channelx,
    "fetch_marketplace_pulse": fetch_marketplace_pulse,
    "fetch_tophub_crossborder": fetch_tophub_crossborder,
    "fetch_amazon_seller_blog": fetch_amazon_seller_blog,
    "fetch_wearesellers": fetch_wearesellers,
    "fetch_ennews": fetch_ennews,
}


def collect_all(session: requests.Session, now: datetime
                ) -> tuple[list[RawItem], list[dict[str, Any]]]:
    """并行采集所有内置源。"""
    all_items: list[RawItem] = []
    statuses: list[dict[str, Any]] = []

    def _run_source(src: dict[str, Any]) -> tuple[list[RawItem], dict[str, Any]]:
        func = FETCH_FUNC_MAP[src["func"]]
        t0 = time.monotonic()
        try:
            items = func(session, now)
            dur = int((time.monotonic() - t0) * 1000)
            return items, {
                "site_id": src["site_id"],
                "site_name": src["site_name"],
                "ok": True,
                "item_count": len(items),
                "duration_ms": dur,
            }
        except Exception as e:
            dur = int((time.monotonic() - t0) * 1000)
            return [], {
                "site_id": src["site_id"],
                "site_name": src["site_name"],
                "ok": False,
                "item_count": 0,
                "duration_ms": dur,
                "error": str(e)[:200],
            }

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_run_source, src): src for src in BUILTIN_SOURCES}
        for fut in as_completed(futs):
            try:
                items, status = fut.result()
                all_items.extend(items)
                statuses.append(status)
            except Exception:
                pass

    return all_items, statuses


# ---------------------------------------------------------------------------
# 归一化 + 归档
# ---------------------------------------------------------------------------

def load_archive(path: Path) -> dict[str, dict[str, Any]]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_archive(archive: dict[str, dict[str, Any]], path: Path) -> None:
    path.write_text(json.dumps(archive, ensure_ascii=False, indent=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# 标题中文翻译缓存
# ---------------------------------------------------------------------------

def load_title_cache(path: Path) -> dict[str, str]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_title_cache(cache: dict[str, str], path: Path) -> None:
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# 输出构建
# ---------------------------------------------------------------------------

def build_latest_payload(
    items_cross: list[dict[str, Any]],
    items_all: list[dict[str, Any]],
    items_all_raw: list[dict[str, Any]],
    statuses: list[dict[str, Any]],
    now: datetime,
    window_hours: int,
    archive_total: int,
) -> dict[str, Any]:
    """构建 latest-24h.json 的 payload。"""
    site_stats: dict[str, dict[str, Any]] = {}
    for item in items_cross:
        sid = item.get("site_id", "")
        if sid not in site_stats:
            site_stats[sid] = {"site_id": sid, "site_name": item.get("site_name", ""), "count": 0, "raw_count": 0}
        site_stats[sid]["count"] += 1

    for item in items_all_raw:
        sid = item.get("site_id", "")
        if sid in site_stats:
            site_stats[sid]["raw_count"] += 1
        else:
            site_stats[sid] = {"site_id": sid, "site_name": item.get("site_name", ""), "count": 0, "raw_count": 1}

    sorted_stats = sorted(site_stats.values(), key=lambda x: x["count"], reverse=True)
    unique_sites = len({s.get("site_id") for s in statuses if s.get("ok")})
    unique_sources = len({item.get("source") for item in items_all_raw})

    return {
        "generated_at": iso(now),
        "window_hours": window_hours,
        "total_items": len(items_cross),
        "total_items_cross_raw": len([i for i in items_all_raw if i.get("cross_is_related")]),
        "total_items_raw": len(items_all_raw),
        "total_items_all_mode": len(items_all),
        "topic_filter": "cross_relevance_scoring_v1_0",
        "cross_relevance_threshold": 0.60,
        "archive_total": archive_total,
        "site_count": unique_sites,
        "source_count": unique_sources,
        "site_stats": sorted_stats,
        "items": items_cross,
    }


def build_all_payload(
    items_all: list[dict[str, Any]],
    items_all_raw: list[dict[str, Any]],
    generated_at: str,
) -> dict[str, Any]:
    return {
        "generated_at": generated_at,
        "total_items_all_mode": len(items_all),
        "total_items_raw": len(items_all_raw),
        "items_all": items_all,
        "items_all_raw": items_all_raw,
    }


def enrich_items(items: list[dict[str, Any]], title_cache: dict[str, str]
                 ) -> list[dict[str, Any]]:
    """为条目添加可读性增强字段，包括标题翻译。"""
    out = []
    translation_count = 0
    max_translations_per_run = 30  # Rate limit: max 30 API calls per run
    for item in items:
        enriched = dict(item)
        title = maybe_fix_mojibake(enriched.get("title", ""))
        enriched["title"] = title
        # 简单的中英文判断和双语标题
        if has_cjk(title):
            enriched["title_zh"] = title
        elif title in title_cache:
            enriched["title_zh"] = title_cache[title]
        elif translation_count < max_translations_per_run:
            # Attempt translation for English titles
            translated = translate_title(title, title_cache)
            if translated:
                enriched["title_zh"] = translated
                translation_count += 1
        out.append(enriched)
    return out


def dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按标题+URL去重。"""
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        title = str(item.get("title_original") or item.get("title") or "").strip().lower()
        url = normalize_url(str(item.get("url") or ""))
        key = f"{title}||{url}"
        groups.setdefault(key, []).append(item)

    out = []
    for values in groups.values():
        chosen = max(values, key=lambda x: (event_time(x) or datetime.min.replace(tzinfo=UTC), str(x.get("id", ""))))
        out.append(chosen)
    out.sort(key=lambda x: event_time(x) or datetime.min.replace(tzinfo=UTC), reverse=True)
    return out


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="跨境电商新闻聚合")
    parser.add_argument("--output-dir", default="data", help="输出目录")
    parser.add_argument("--window-hours", type=int, default=24, help="时间窗口（小时）")
    parser.add_argument("--archive-days", type=int, default=21, help="归档保留天数")
    parser.add_argument("--rss-opml", default="", help="OPML文件路径")
    parser.add_argument("--rss-max-feeds", type=int, default=0, help="最大OPML源数（0=全部）")
    args = parser.parse_args()

    now = utc_now()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    archive_path = output_dir / "archive.json"
    latest_path = output_dir / "latest-24h.json"
    latest_all_path = output_dir / "latest-24h-all.json"
    status_path = output_dir / "source-status.json"
    title_cache_path = output_dir / "title-zh-cache.json"
    policy_path = output_dir / "policy-calendar.json"

    archive = load_archive(archive_path)
    # 迁移修复：亿恩网RSS返回的URL域名有误（www.en.com），统一修复
    for item in archive.values():
        if item.get("site_id") == "ennews" and "www.en.com" in (item.get("url") or ""):
            item["url"] = item["url"].replace("www.en.com", "www.ennews.com").replace("http://", "https://")
    title_cache = load_title_cache(title_cache_path)

    session = create_session()

    # 采集
    print("[INFO] 开始采集跨境新闻源...")
    raw_items, statuses = collect_all(session, now)
    print(f"[INFO] 采集完成，共 {len(raw_items)} 条原始条目")

    # OPML
    rss_feed_statuses: list[dict[str, Any]] = []
    if args.rss_opml:
        opml_path = Path(args.rss_opml).expanduser()
        if opml_path.exists():
            rss_items, rss_status, rss_feed_statuses = fetch_opml_rss(
                now, opml_path, max_feeds=max(0, int(args.rss_max_feeds)))
            raw_items.extend(rss_items)
            statuses.append(rss_status)
            print(f"[INFO] OPML采集完成，{rss_status.get('ok_feed_count', 0)} 个源成功")

    # 归入 archive
    seen_this_run: set[str] = set()
    for raw in raw_items:
        title = raw.title.strip()
        url = normalize_url(raw.url)
        if not title or not url or not url.startswith("http"):
            continue
        item_id = make_item_id(raw.site_id, raw.source, title, url)
        seen_this_run.add(item_id)
        existing = archive.get(item_id)
        if existing is None:
            archive[item_id] = {
                "id": item_id,
                "site_id": raw.site_id,
                "site_name": raw.site_name,
                "source": raw.source,
                "title": title,
                "url": url,
                "published_at": iso(raw.published_at),
                "first_seen_at": iso(now),
                "last_seen_at": iso(now),
            }
        else:
            existing["site_id"] = raw.site_id
            existing["site_name"] = raw.site_name
            existing["source"] = raw.source
            existing["title"] = title
            existing["url"] = url
            if raw.published_at and (raw.site_id == "opmlrss" or not existing.get("published_at")):
                existing["published_at"] = iso(raw.published_at)
            existing["last_seen_at"] = iso(now)

    # 裁剪旧归档
    keep_after = now - timedelta(days=args.archive_days)
    archive = {
        k: v for k, v in archive.items()
        if (parse_iso(v.get("last_seen_at")) or parse_iso(v.get("published_at")) or now) >= keep_after
    }

    # 24小时窗口
    window_start = now - timedelta(hours=args.window_hours)
    latest_items_all_raw: list[dict[str, Any]] = []
    for record in archive.values():
        if _is_nav_pollution(record):
            continue
        ts = event_time(record)
        if ts and ts >= window_start:
            latest_items_all_raw.append(dict(record))

    # 打分
    scored_all = [add_cross_relevance_fields(item) for item in latest_items_all_raw]
    # 过滤：跨境相关 且 分数达到阈值（is_cross_related 布尔 ≠ 分数达标，
    # 2026-08-04修复：此前只过滤布尔导致0.42/0.49等低分条目混入数据流）
    items_cross = [
        item for item in scored_all
        if item.get("cross_is_related") and item.get("cross_score", 0) >= 0.60
    ]
    # 单源上限：每个源最多贡献 N 条（2026-08-04新增，防聚合站垄断精选）
    # 普通源 8 条；community 类源（知无不言等UGC论坛）限 5 条——求助帖非新闻，控制占比
    items_cross.sort(key=lambda x: x.get("cross_score", 0), reverse=True)
    community_site_ids = {s["site_id"] for s in BUILTIN_SOURCES if s.get("kind") == "community"}
    per_site_count: dict[str, int] = {}
    capped: list[dict[str, Any]] = []
    for item in items_cross:
        sid = str(item.get("site_id") or "unknown")
        limit = 5 if sid in community_site_ids else 8
        if per_site_count.get(sid, 0) >= limit:
            continue
        per_site_count[sid] = per_site_count.get(sid, 0) + 1
        capped.append(item)
    items_cross = capped
    items_all = dedupe_items(scored_all)
    items_cross_deduped = dedupe_items(items_cross)

    # 增强
    items_cross_enriched = enrich_items(items_cross_deduped, title_cache)
    items_all_enriched = enrich_items(items_all, title_cache)
    items_all_raw_enriched = enrich_items(scored_all, title_cache)

    print(f"[INFO] 跨境相关: {len(items_cross_enriched)} 条，全量: {len(items_all_enriched)} 条")

    # 构建输出
    latest_payload = build_latest_payload(
        items_cross_enriched, items_all_enriched, items_all_raw_enriched,
        statuses, now, args.window_hours, len(archive))

    # 分割 slim / all
    slim_payload = dict(latest_payload)
    all_payload = build_all_payload(items_all_enriched, items_all_raw_enriched, slim_payload["generated_at"])
    slim_payload.pop("items_all", None)
    slim_payload.pop("items_all_raw", None)
    slim_payload["all_mode_data_url"] = "data/latest-24h-all.json"

    # 写文件
    latest_path.write_text(json.dumps(slim_payload, ensure_ascii=False, indent=False), encoding="utf-8")
    latest_all_path.write_text(json.dumps(all_payload, ensure_ascii=False, indent=False), encoding="utf-8")
    save_archive(archive, archive_path)
    save_title_cache(title_cache, title_cache_path)

    # 源状态
    failed = [s for s in statuses if not s.get("ok")]
    source_status = {
        "generated_at": iso(now),
        "sites": statuses,
        "failed_sites": failed,
        "successful_sites": len([s for s in statuses if s.get("ok")]),
        "fetched_raw_items": len(raw_items),
        "items_before_topic_filter": len(scored_all),
        "rss_opml": {
            "enabled": bool(args.rss_opml),
            "feed_count": len(rss_feed_statuses),
            "ok_feeds": len([f for f in rss_feed_statuses if f.get("ok")]),
        },
    }
    status_path.write_text(json.dumps(source_status, ensure_ascii=False, indent=False), encoding="utf-8")

    # 政策日历
    policy_calendar = generate_policy_calendar(session, now)
    policy_path.write_text(json.dumps(policy_calendar, ensure_ascii=False, indent=False), encoding="utf-8")

    print(f"[INFO] 输出写入 {output_dir}/")
    print(f"[INFO] 跨境信号: {slim_payload['total_items']} | 全量: {slim_payload['total_items_all_mode']} | 归档: {len(archive)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
