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
    return dt.astimezone(UTC)


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
) -> list[RawItem]:
    """Fetch articles from a JS-rendered page using BrowserAct CLI.

    Falls back to empty list if BrowserAct is unavailable or fails.
    """
    session_name = f"fetch-{uuid.uuid4().hex[:8]}"
    items: list[RawItem] = []

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


# ---------------------------------------------------------------------------
# 标题翻译
# ---------------------------------------------------------------------------

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
    """Amazon Newsroom — 官方新闻。"""
    return fetch_rss(session, "https://www.aboutamazon.com/news/feed",
                     "amazon_newsroom", "Amazon Newsroom", "Amazon官方")


def fetch_sp_api_changelog(session: requests.Session, now: datetime) -> list[RawItem]:
    """SP-API 变更日志。"""
    return fetch_rss(session, "https://developer-docs.amazon.com/sp-api/changelog.rss",
                     "sp_api", "SP-API Changelog", "SP-API变更", max_age_hours=168)


def fetch_amazon_ads_blog(session: requests.Session, now: datetime) -> list[RawItem]:
    """Amazon Ads 官方博客。"""
    return fetch_rss(session, "https://advertising.amazon.com/blog/feed",
                     "amazon_ads", "Amazon Ads Blog", "亚马逊广告")


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
            soup = BeautifulSoup(resp.text, "html.parser")
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
    return items[:40]


def fetch_amz123_early(session: requests.Session, now: datetime) -> list[RawItem]:
    """AMZ123 跨境早报 — BrowserAct + HTML fallback."""
    items = fetch_via_browseract(
        url="https://www.amz123.com/t-kuajingzaobao",
        site_id="amz123", site_name="AMZ123", source_label="跨境早报",
        url_pattern="/t-", base_url="https://www.amz123.com",
    )
    if not items:
        try:
            resp = session.get("https://www.amz123.com/t-kuajingzaobao", timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                title = a.get_text(strip=True)
                href = a["href"]
                if len(title) < 8 or not href.startswith(("http", "/")):
                    continue
                if "/t-" not in href and "/article/" not in href:
                    continue
                if not href.startswith("http"):
                    href = urljoin("https://www.amz123.com", href)
                items.append(RawItem(
                    site_id="amz123", site_name="AMZ123", source="跨境早报",
                    title=title, url=normalize_url(href),
                    published_at=None, meta={},
                ))
        except Exception as e:
            print(f"  [WARN] AMZ123 早报 fallback HTML fetch failed: {e}")
    return items[:30]


def fetch_amzdh(session: requests.Session, now: datetime) -> list[RawItem]:
    """AMZDH 跨境头条 — BrowserAct + HTML fallback."""
    items = fetch_via_browseract(
        url="https://www.amzdh.com/kjtt/",
        site_id="amzdh", site_name="AMZDH", source_label="跨境头条",
        url_pattern="/kjtt/", base_url="https://www.amzdh.com",
    )
    if not items:
        try:
            resp = session.get("https://www.amzdh.com/kjtt/", timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                title = a.get_text(strip=True)
                href = a["href"]
                if len(title) < 10 or not href.startswith(("http", "/")):
                    continue
                if "/kjtt/" not in href and "/article/" not in href:
                    continue
                if not href.startswith("http"):
                    href = urljoin("https://www.amzdh.com", href)
                items.append(RawItem(
                    site_id="amzdh", site_name="AMZDH", source="跨境头条",
                    title=title, url=normalize_url(href),
                    published_at=None, meta={},
                ))
        except Exception as e:
            print(f"  [WARN] AMZDH fallback HTML fetch failed: {e}")
    return items[:30]


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
            soup = BeautifulSoup(resp.text, "html.parser")
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
    return items[:30]


def fetch_kjds365(session: requests.Session, now: datetime) -> list[RawItem]:
    """跨境电商365 — BrowserAct + HTML fallback."""
    items = fetch_via_browseract(
        url="https://kjds365.cn/",
        site_id="kjds365", site_name="跨境电商365", source_label="行业资讯",
        url_pattern="kjds365.cn", base_url="https://kjds365.cn",
    )
    if not items:
        try:
            resp = session.get("https://kjds365.cn/", timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                title = a.get_text(strip=True)
                href = a["href"]
                if len(title) < 8 or not href.startswith(("http", "/")):
                    continue
                if not href.startswith("http"):
                    href = urljoin("https://kjds365.cn", href)
                if "kjds365.cn" not in href:
                    continue
                items.append(RawItem(
                    site_id="kjds365", site_name="跨境电商365", source="行业资讯",
                    title=title, url=normalize_url(href),
                    published_at=None, meta={},
                ))
        except Exception as e:
            print(f"  [WARN] kjds365 fallback HTML fetch failed: {e}")
    return items[:20]


def fetch_gs_amazon_cn(session: requests.Session, now: datetime) -> list[RawItem]:
    """亚马逊全球开店中文 — BrowserAct + HTML fallback."""
    items = fetch_via_browseract(
        url="https://gs.amazon.cn/news",
        site_id="gs_amazon", site_name="亚马逊全球开店", source_label="全球开店资讯",
        url_pattern="amazon.cn", base_url="https://gs.amazon.cn",
    )
    if not items:
        try:
            resp = session.get("https://gs.amazon.cn/news", timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                title = a.get_text(strip=True)
                href = a["href"]
                if len(title) < 8 or not href.startswith(("http", "/")):
                    continue
                if not href.startswith("http"):
                    href = urljoin("https://gs.amazon.cn", href)
                if "amazon.cn" not in href:
                    continue
                items.append(RawItem(
                    site_id="gs_amazon", site_name="亚马逊全球开店", source="全球开店资讯",
                    title=title, url=normalize_url(href),
                    published_at=None, meta={},
                ))
        except Exception as e:
            print(f"  [WARN] gs.amazon.cn fallback HTML fetch failed: {e}")
    return items[:30]


# ---------------------------------------------------------------------------
# 英文跨境源
# ---------------------------------------------------------------------------

def fetch_helium10_blog(session: requests.Session, now: datetime) -> list[RawItem]:
    """Helium 10 博客 — 亚马逊卖家工具头部品牌。"""
    return fetch_rss(session, "https://www.helium10.com/blog/feed/",
                     "helium10", "Helium10 Blog", "Helium10")


def fetch_junglescout_blog(session: requests.Session, now: datetime) -> list[RawItem]:
    """Jungle Scout 博客。"""
    return fetch_rss(session, "https://www.junglescout.com/blog/feed/",
                     "junglescout", "Jungle Scout Blog", "Jungle Scout")


def fetch_ecombrainly(session: requests.Session, now: datetime) -> list[RawItem]:
    """EcomBrainly — 亚马逊政策更新，直接HTML解析。"""
    items: list[RawItem] = []
    try:
        resp = session.get("https://ecombrainly.com/amazon-marketplace-policy-updates/", timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            title = a.get_text(strip=True)
            href = a["href"]
            if len(title) < 10 or not href.startswith("http"):
                continue
            if "ecombrainly.com" not in href:
                continue
            items.append(RawItem(
                site_id="ecombrainly", site_name="EcomBrainly", source="政策更新",
                title=title, url=normalize_url(href),
                published_at=None, meta={},
            ))
    except Exception as e:
        print(f"  [WARN] EcomBrainly fetch failed: {e}")
    return items[:25]


def fetch_novadata(session: requests.Session, now: datetime) -> list[RawItem]:
    """NovaData — 卖家新闻，直接HTML解析。"""
    items: list[RawItem] = []
    try:
        resp = session.get("https://novadata.io/resources/news", timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            title = a.get_text(strip=True)
            href = a["href"]
            if len(title) < 10 or not href.startswith("http"):
                continue
            if "novadata.io" not in href:
                continue
            items.append(RawItem(
                site_id="novadata", site_name="NovaData", source="卖家新闻",
                title=title, url=normalize_url(href),
                published_at=None, meta={},
            ))
    except Exception as e:
        print(f"  [WARN] NovaData fetch failed: {e}")
    return items[:25]


def fetch_seller_policy_watch(session: requests.Session, now: datetime) -> list[RawItem]:
    """Seller Policy Watch — 政策变动监控，直接HTML解析。"""
    items: list[RawItem] = []
    try:
        resp = session.get("https://sellerpolicywatch.com/", timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            title = a.get_text(strip=True)
            href = a["href"]
            if len(title) < 10 or not href.startswith("http"):
                continue
            if "sellerpolicywatch.com" not in href:
                continue
            items.append(RawItem(
                site_id="sellerpolicywatch", site_name="Seller Policy Watch", source="政策监控",
                title=title, url=normalize_url(href),
                published_at=None, meta={},
            ))
    except Exception as e:
        print(f"  [WARN] SellerPolicyWatch fetch failed: {e}")
    return items[:20]


def fetch_ecomengine(session: requests.Session, now: datetime) -> list[RawItem]:
    """EcomEngine — 卖家新闻，直接HTML解析。"""
    items: list[RawItem] = []
    try:
        resp = session.get("https://www.ecomengine.com/amazon-seller-news", timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
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


def fetch_ecommercebytes(session: requests.Session, now: datetime) -> list[RawItem]:
    """EcommerceBytes — 电商行业新闻。"""
    return fetch_rss(session, "https://www.ecommercebytes.com/feed/",
                     "ecommercebytes", "EcommerceBytes", "电商行业")


def fetch_practical_ecommerce(session: requests.Session, now: datetime) -> list[RawItem]:
    """Practical Ecommerce。"""
    return fetch_rss(session, "https://pec-ly.com/feed",
                     "practical_ecommerce", "Practical Ecommerce", "电商实践")


def fetch_web_retailer(session: requests.Session, now: datetime) -> list[RawItem]:
    """Web Retailer — 亚马逊/电商深度分析。"""
    return fetch_rss(session, "https://www.webretailer.com/feed/",
                     "web_retailer", "Web Retailer", "电商分析")


def fetch_seller_sessions_podcast(session: requests.Session, now: datetime) -> list[RawItem]:
    """Seller Sessions Podcast — skipped (Buzzsprout RSS unreliable)."""
    return []


def fetch_amazon_seller_podcast(session: requests.Session, now: datetime) -> list[RawItem]:
    """The Amazon Seller Podcast — skipped (Buzzsprout RSS unreliable)."""
    return []


# ---------------------------------------------------------------------------
# 聚合源 (TopHub / Zeli / NewsNow)
# ---------------------------------------------------------------------------

def fetch_tophub_crossborder(session: requests.Session, now: datetime) -> list[RawItem]:
    """TopHub — 跨境电商相关热榜，直接HTML解析。"""
    items: list[RawItem] = []
    try:
        resp = session.get("https://tophub.today/", timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
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
    """Amazon Seller Blog (sell.amazon.com) — 官方卖家公告。"""
    return fetch_via_browseract(
        url="https://sell.amazon.com/blog/announcements",
        site_id="amazon_seller_blog",
        site_name="Amazon卖家博客",
        source_label="官方公告",
        url_pattern="/blog/announcements/",
        base_url="https://sell.amazon.com",
        max_items=25,
    )


def fetch_wearesellers(session: requests.Session, now: datetime) -> list[RawItem]:
    """知无不言论坛 — 中国亚马逊卖家最活跃的社区。"""
    return fetch_via_browseract(
        url="https://www.wearesellers.com/",
        site_id="wearesellers",
        site_name="知无不言",
        source_label="卖家社区",
        url_pattern="/question/",
        base_url="https://www.wearesellers.com",
        max_items=25,
    )


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
            "title": "EU GPSR 通用产品安全法规",
            "effective_date": "2024-12-13",
            "platforms": ["Amazon EU", "eBay EU"],
            "impact_level": "high",
            "description": "所有在欧盟销售的非食品类产品需有欧盟责任人、产品标签和安全信息。"
        },
        {
            "title": "亚马逊FBA配送费含燃油附加费3.5%",
            "effective_date": "2026-05-02",
            "platforms": ["Amazon US"],
            "impact_level": "high",
            "description": "亚马逊在FBA配送费中新增3.5%燃油和物流附加费。"
        },
        {
            "title": "欧盟取消150欧元低值包裹免税",
            "effective_date": "2026-07-01",
            "platforms": ["Amazon EU", "Temu", "Shein", "AliExpress"],
            "impact_level": "high",
            "description": "每件跨境包裹将被征收3欧元关税，影响所有非欧盟来源的小额包裹。"
        },
        {
            "title": "亚马逊Prime Day 2026",
            "effective_date": "2026-06-23",
            "platforms": ["Amazon US", "Amazon EU"],
            "impact_level": "high",
            "description": "Prime Day大促预计销售额263亿美元，需提前备货和优化listing。"
        },
        {
            "title": "亚马逊美站原产地信息补全截止",
            "effective_date": "2026-06-30",
            "platforms": ["Amazon US"],
            "impact_level": "medium",
            "description": "6月30日后未补全原产地信息的ASIN将影响FBA发货。"
        },
        {
            "title": "亚马逊欧洲站FBM直邮规则升级",
            "effective_date": "2026-07-01",
            "platforms": ["Amazon EU"],
            "impact_level": "medium",
            "description": "仅限授权物流渠道，强化IOSS校验。"
        },
        {
            "title": "EU PPWR 包装法规生效",
            "effective_date": "2026-08-12",
            "platforms": ["Amazon EU"],
            "impact_level": "high",
            "description": "未完成包装EPR合规注册的卖家，商品将面临下架。"
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
    {"func": "fetch_amz123_early", "site_id": "amz123", "site_name": "AMZ123早报", "kind": "aggregate"},
    {"func": "fetch_amzdh", "site_id": "amzdh", "site_name": "AMZDH", "kind": "aggregate"},
    {"func": "fetch_cifnews", "site_id": "cifnews", "site_name": "雨果跨境", "kind": "aggregate"},
    {"func": "fetch_kjds365", "site_id": "kjds365", "site_name": "跨境电商365", "kind": "aggregate"},
    {"func": "fetch_helium10_blog", "site_id": "helium10", "site_name": "Helium10", "kind": "industry"},
    {"func": "fetch_junglescout_blog", "site_id": "junglescout", "site_name": "Jungle Scout", "kind": "industry"},
    {"func": "fetch_ecombrainly", "site_id": "ecombrainly", "site_name": "EcomBrainly", "kind": "blogs"},
    {"func": "fetch_novadata", "site_id": "novadata", "site_name": "NovaData", "kind": "blogs"},
    {"func": "fetch_seller_policy_watch", "site_id": "sellerpolicywatch", "site_name": "政策监控", "kind": "official"},
    {"func": "fetch_ecomengine", "site_id": "ecomengine", "site_name": "EcomEngine", "kind": "industry"},
    {"func": "fetch_ecommercebytes", "site_id": "ecommercebytes", "site_name": "EcommerceBytes", "kind": "blogs"},
    {"func": "fetch_practical_ecommerce", "site_id": "practical_ecommerce", "site_name": "Practical Ecommerce", "kind": "blogs"},
    {"func": "fetch_web_retailer", "site_id": "web_retailer", "site_name": "Web Retailer", "kind": "blogs"},
    {"func": "fetch_seller_sessions_podcast", "site_id": "seller_sessions", "site_name": "Seller Sessions", "kind": "media"},
    {"func": "fetch_amazon_seller_podcast", "site_id": "amz_podcast", "site_name": "Amazon Seller Podcast", "kind": "media"},
    {"func": "fetch_tophub_crossborder", "site_id": "tophub", "site_name": "TopHub", "kind": "aggregate"},
    {"func": "fetch_amazon_seller_blog", "site_id": "amazon_seller_blog", "site_name": "Amazon卖家博客", "kind": "official"},
    {"func": "fetch_wearesellers", "site_id": "wearesellers", "site_name": "知无不言", "kind": "community"},
]

FETCH_FUNC_MAP: dict[str, Any] = {
    "fetch_amazon_newsroom": fetch_amazon_newsroom,
    "fetch_sp_api_changelog": fetch_sp_api_changelog,
    "fetch_amazon_ads_blog": fetch_amazon_ads_blog,
    "fetch_gs_amazon_cn": fetch_gs_amazon_cn,
    "fetch_amz123": fetch_amz123,
    "fetch_amz123_early": fetch_amz123_early,
    "fetch_amzdh": fetch_amzdh,
    "fetch_cifnews": fetch_cifnews,
    "fetch_kjds365": fetch_kjds365,
    "fetch_helium10_blog": fetch_helium10_blog,
    "fetch_junglescout_blog": fetch_junglescout_blog,
    "fetch_ecombrainly": fetch_ecombrainly,
    "fetch_novadata": fetch_novadata,
    "fetch_seller_policy_watch": fetch_seller_policy_watch,
    "fetch_ecomengine": fetch_ecomengine,
    "fetch_ecommercebytes": fetch_ecommercebytes,
    "fetch_practical_ecommerce": fetch_practical_ecommerce,
    "fetch_web_retailer": fetch_web_retailer,
    "fetch_seller_sessions_podcast": fetch_seller_sessions_podcast,
    "fetch_amazon_seller_podcast": fetch_amazon_seller_podcast,
    "fetch_tophub_crossborder": fetch_tophub_crossborder,
    "fetch_amazon_seller_blog": fetch_amazon_seller_blog,
    "fetch_wearesellers": fetch_wearesellers,
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
        "cross_relevance_threshold": 0.65,
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
        ts = event_time(record)
        if ts and ts >= window_start:
            latest_items_all_raw.append(dict(record))

    # 打分
    scored_all = [add_cross_relevance_fields(item) for item in latest_items_all_raw]
    items_cross = [item for item in scored_all if item.get("cross_is_related")]
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
