#!/usr/bin/env python3
"""事件聚类 / 故事合并 — 把多源重复报道的同一事件聚合成"故事线"。

解决的问题: 管道此前只做 URL 去重, 同一事件被多个源各报一次, 用户在
时间线上看到 N 条重复信号。本模块把「标题相似 + 时间窗口内」的条目
合并为一条故事 (story), 并提供多源信号×时间衰减的热点排名。

合并规则:
- 相似度: 标题分词后的 Jaccard 系数 >= similarity_threshold (默认 0.35)
  * 中文按 2 字符 bigram 分词 (CJK 连续段内滑窗)
  * 英文/数字按词分词 (等价于按空格分词后剥离标点)
  * 中英混排标题: CJK 段各自 bigram + 其余部分按词切分, 不跨段组词
- 时间窗口: 两条目的 event_time (published_at, 缺失回退 first_seen_at)
  间隔 <= window_hours (默认 24h) 才可能连边
- 链式合并: A~B 且 B~C (各边同时满足相似+窗口) => A/B/C 同一故事
  (union-find 单连通关聚类)
- 兜底不变量: 合并完成后若某故事的时间跨度 > window_hours, 按时间顺序
  贪心切分, 保证最终每个故事内条目跨度 <= window_hours

输出故事按 max_score 降序。纯标准库实现, 供 update_crossborder.py
等管道脚本直接 import:

    from stories_merger import merge_stories, rank_hot_topics
    stories = merge_stories(items)          # items: list[dict]
    top = rank_hot_topics(stories, top_n=10)
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

__all__ = ["merge_stories", "rank_hot_topics", "title_similarity"]

# CJK 统一表意文字(含扩展A) + 假名 + 谚文, 覆盖中日韩标题
_CJK_RUN_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]+")
_ASCII_WORD_RE = re.compile(r"[a-z0-9]+")

# 排序哨兵: 无时间戳的条目排在最后
_TIME_MAX = datetime.max.replace(tzinfo=timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    """解析 ISO 时间字符串 (容忍 Z 后缀/naive时间/常见变体), 统一转 UTC。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return None
        dt = None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
                        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
        if dt is None:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _event_time(item: dict[str, Any]) -> datetime | None:
    """条目事件时间: published_at 优先, 缺失回退 first_seen_at。"""
    return _parse_time(item.get("published_at")) or _parse_time(item.get("first_seen_at"))


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _tokenize(title: str) -> frozenset[str]:
    """标题分词: CJK 连续段内 2 字符 bigram; 其余部分按 [a-z0-9]+ 切词。"""
    if not title:
        return frozenset()
    text = str(title).lower()
    tokens: set[str] = set()
    parts: list[str] = []
    pos = 0
    for m in _CJK_RUN_RE.finditer(text):
        parts.append(text[pos:m.start()])
        run = m.group()
        if len(run) == 1:
            tokens.add(run)
        else:
            tokens.update(run[i:i + 2] for i in range(len(run) - 1))
        pos = m.end()
    parts.append(text[pos:])
    for part in parts:
        tokens.update(_ASCII_WORD_RE.findall(part))
    return frozenset(tokens)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / (len(a) + len(b) - inter)


def title_similarity(title_a: str, title_b: str) -> float:
    """两个标题的 Jaccard 相似度 (公开辅助函数, 便于调用方调试阈值)。"""
    return _jaccard(_tokenize(title_a), _tokenize(title_b))


def _score(item: dict[str, Any]) -> float:
    try:
        return float(item.get("cross_score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _source_key(item: dict[str, Any]) -> str:
    return str(item.get("site_id") or item.get("site_name") or "unknown")


def merge_stories(
    items: list[dict[str, Any]],
    similarity_threshold: float = 0.35,
    window_hours: int = 24,
) -> list[dict[str, Any]]:
    """合并相似条目为故事线。返回按 max_score 降序的故事列表。"""
    if not items:
        return []

    n = len(items)
    tokens = [_tokenize(it.get("title") or "") for it in items]
    times = [_event_time(it) for it in items]
    window = timedelta(hours=window_hours)

    # ---- union-find: 相似 且 在窗口内 => 连边; 链式传递 ----
    parent = list(range(n))

    def find(x: int) -> int:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for i in range(n):
        if not tokens[i]:
            continue
        for j in range(i + 1, n):
            if not tokens[j]:
                continue
            if times[i] and times[j] and abs(times[i] - times[j]) > window:
                continue
            if _jaccard(tokens[i], tokens[j]) >= similarity_threshold:
                union(i, j)

    clusters: dict[int, list[int]] = {}
    for idx in range(n):
        clusters.setdefault(find(idx), []).append(idx)

    # ---- 兜底: 故事时间跨度 > window 时按时间贪心切分 ----
    # (链式合并可能让首尾条目间隔超过窗口; 无时间条目并入第一段)
    stories: list[dict[str, Any]] = []
    for members in clusters.values():
        timed = sorted((k for k in members if times[k] is not None),
                       key=lambda k: times[k])
        untimed = [k for k in members if times[k] is None]

        segments: list[list[int]] = []
        cur: list[int] = []
        cur_start: datetime | None = None
        for k in timed:
            t = times[k]
            if cur_start is None or (t - cur_start) <= window:
                cur.append(k)
                if cur_start is None:
                    cur_start = t
            else:
                segments.append(cur)
                cur, cur_start = [k], t
        segments.append(cur)
        segments[0].extend(untimed)

        for seg in segments:
            seg.sort(key=lambda k: (times[k] is None, times[k] or _TIME_MAX))
            seg_items = [items[k] for k in seg]

            # 主条目 = 最长标题(信息量最大); 同长取分高, 再取时间早
            primary = min(seg, key=lambda k: (
                -len(str(items[k].get("title") or "")),
                -_score(items[k]),
                times[k] or _TIME_MAX,
            ))
            primary_item = items[primary]
            primary_title = str(primary_item.get("title") or "")

            # 源去重按 site_id (缺失回退 site_name), 展示名用 site_name
            seen_sources: dict[str, str] = {}
            for k in seg:
                it = items[k]
                key = _source_key(it)
                if key not in seen_sources:
                    seen_sources[key] = str(it.get("site_name") or it.get("site_id") or key)

            seg_times = [times[k] for k in seg if times[k] is not None]
            stories.append({
                "story_id": hashlib.sha1(primary_title.encode("utf-8")).hexdigest()[:12],
                "primary_title": primary_title,
                "primary_url": primary_item.get("url"),
                "primary_site_id": primary_item.get("site_id"),
                "item_count": len(seg),
                "source_count": len(seen_sources),
                "source_names": list(seen_sources.values()),
                "max_score": max(_score(it) for it in seg_items),
                "category": primary_item.get("cross_label"),
                "items": seg_items,
                "earliest_time": _iso(min(seg_times)) if seg_times else None,
                "latest_time": _iso(max(seg_times)) if seg_times else None,
            })

    stories.sort(key=lambda s: (s["max_score"], s["source_count"], s["item_count"]),
                 reverse=True)
    return stories


def rank_hot_topics(
    stories: list[dict[str, Any]],
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """多源信号×时间衰减排名。

    score = source_count * 2 + item_count * 0.5 + max_score * 3 + freshness_bonus
    freshness_bonus (按故事 latest_time 距今): 6h内 +2.0, 24h内 +1.0, 48h内 +0.5。
    返回 top_n 个故事的副本 (不修改入参), 带 rank / hot_score / freshness_bonus。
    """
    if not stories:
        return []
    now = datetime.now(timezone.utc)

    scored: list[dict[str, Any]] = []
    for s in stories:
        source_count = int(s.get("source_count") or 0)
        item_count = int(s.get("item_count") or len(s.get("items") or []))
        try:
            max_score = float(s.get("max_score") or 0.0)
        except (TypeError, ValueError):
            max_score = 0.0

        latest = _parse_time(s.get("latest_time"))
        if latest is not None:
            age_hours = max(0.0, (now - latest).total_seconds() / 3600.0)
            if age_hours <= 6:
                bonus = 2.0
            elif age_hours <= 24:
                bonus = 1.0
            elif age_hours <= 48:
                bonus = 0.5
            else:
                bonus = 0.0
        else:
            age_hours = None
            bonus = 0.0

        hot = source_count * 2 + item_count * 0.5 + max_score * 3 + bonus
        entry = dict(s)
        entry["hot_score"] = hot
        entry["freshness_bonus"] = bonus
        entry["age_hours"] = round(age_hours, 2) if age_hours is not None else None
        scored.append(entry)

    scored.sort(key=lambda d: (d["hot_score"], d.get("max_score") or 0.0), reverse=True)
    top = scored[:max(0, int(top_n))]
    for rank, entry in enumerate(top, start=1):
        entry["rank"] = rank
    return top


if __name__ == "__main__":
    now = datetime.now(timezone.utc)

    def ago(hours: float) -> str:
        return _iso(now - timedelta(hours=hours))

    def at_off(base_hours_ago: float, plus_hours: float) -> str:
        return _iso(now - timedelta(hours=base_hours_ago) + timedelta(hours=plus_hours))

    B = 8.0  # 基准: 8 小时前
    items: list[dict[str, Any]] = [
        # 故事A: 同一事件 3 源 + 1 条 published_at 缺失(回退 first_seen_at)
        {"title": "亚马逊宣布调整美国站FBA仓储费用标准", "site_id": "amz123",
         "site_name": "AMZ123", "url": "https://www.amz123.com/kx/a1",
         "published_at": ago(B), "first_seen_at": ago(B),
         "cross_score": 0.85, "cross_label": "policy_update"},
        {"title": "亚马逊宣布调整美国站FBA仓储费标准", "site_id": "cifnews",
         "site_name": "雨果跨境", "url": "https://www.cifnews.com/article/1001",
         "published_at": at_off(B, 1.0), "first_seen_at": at_off(B, 1.0),
         "cross_score": 0.72, "cross_label": "policy_update"},
        # 注: 不能放在恰好6.0h处 — rank_hot_topics 内部第二次取 now() 会比
        # fixture 的 now 晚几微秒, 精确边界值会跨档(6h+ε → 24h档), 测试必须放档位中间
        {"title": "美国站FBA仓储费用标准调整 亚马逊发布公告", "site_id": "amzdh",
         "site_name": "AMZDH", "url": "https://www.amzdh.com/kjtt/1001.html",
         "published_at": at_off(B, 2.5), "first_seen_at": at_off(B, 2.5),
         "cross_score": 0.66, "cross_label": "policy_update"},
        {"title": "亚马逊美国站FBA仓储费用新标准", "site_id": "wearesellers",
         "site_name": "知无不言", "url": "https://www.wearesellers.com/question/1001",
         "published_at": None, "first_seen_at": at_off(B, 1.0),
         "cross_score": 0.60, "cross_label": "policy_update"},
        # 故事B: 英文标题对 (英文按词分词)
        {"title": "Amazon raises FBA storage fees for US sellers", "site_id": "ecomengine",
         "site_name": "EcomEngine", "url": "https://www.ecomengine.com/blog/fba-fees",
         "published_at": at_off(B, 1.5), "first_seen_at": at_off(B, 1.5),
         "cross_score": 0.78, "cross_label": "platform_trend"},
        {"title": "Amazon announces FBA storage fee increase for US sellers",
         "site_id": "marketplace_pulse", "site_name": "Marketplace Pulse",
         "url": "https://www.marketplacepulse.com/articles/fba-fee-increase",
         "published_at": at_off(B, 2.5), "first_seen_at": at_off(B, 2.5),
         "cross_score": 0.70, "cross_label": "platform_trend"},
        # 故事C: 相似词头但不同事件 (日本站佣金 != 美国站仓储费), 应独立成故事。
        # 注意措辞避开共享骨架"宣布调整…站FBA…标准"——该骨架与条目2的Jaccard
        # 恰好=0.35会触发边界合并; 真实数据里这类"同框架不同市场"确实会被
        # ≥0.35规则合并, 属规格定义的行为。
        {"title": "亚马逊日本站FBA销售佣金费率上调", "site_id": "cifnews",
         "site_name": "雨果跨境", "url": "https://www.cifnews.com/article/1002",
         "published_at": at_off(B, 0.5), "first_seen_at": at_off(B, 0.5),
         "cross_score": 0.64, "cross_label": "policy_update"},
        # 故事D: 与条目1同标题但 50h 前 => 超出时间窗口, 不得并入故事A
        {"title": "亚马逊宣布调整美国站FBA仓储费用标准", "site_id": "amz123",
         "site_name": "AMZ123", "url": "https://www.amz123.com/kx/old",
         "published_at": ago(B + 50), "first_seen_at": ago(B + 50),
         "cross_score": 0.80, "cross_label": "policy_update"},
        # 故事E: 链式合并 — A~B(0.46), B~C(0.44), 但 A~C(0.26)<0.35, 仍应合成一个故事
        {"title": "欧盟GPSR新规12月1日正式生效", "site_id": "amz123",
         "site_name": "AMZ123", "url": "https://www.amz123.com/kx/g1",
         "published_at": at_off(B, 3.0), "first_seen_at": at_off(B, 3.0),
         "cross_score": 0.55, "cross_label": "compliance_deadline"},
        {"title": "GPSR新规12月1日生效卖家需自查", "site_id": "cifnews",
         "site_name": "雨果跨境", "url": "https://www.cifnews.com/article/1003",
         "published_at": at_off(B, 3.5), "first_seen_at": at_off(B, 3.5),
         "cross_score": 0.52, "cross_label": "compliance_deadline"},
        {"title": "卖家注意GPSR新规12月生效 需自查合规材料", "site_id": "ennews",
         "site_name": "亿恩网", "url": "https://www.ennews.com/article/1003",
         "published_at": at_off(B, 4.0), "first_seen_at": at_off(B, 4.0),
         "cross_score": 0.45, "cross_label": "compliance_deadline"},
    ]

    print("== 链式相似度验证 ==")
    s_12 = title_similarity(items[8]["title"], items[9]["title"])
    s_23 = title_similarity(items[9]["title"], items[10]["title"])
    s_13 = title_similarity(items[8]["title"], items[10]["title"])
    print(f"  t1~t2={s_12:.3f} (>=0.35)  t2~t3={s_23:.3f} (>=0.35)  t1~t3={s_13:.3f} (<0.35)")
    assert s_12 >= 0.35 and s_23 >= 0.35 and s_13 < 0.35, "链式测试数据前提不成立"

    print("== merge_stories ==")
    stories = merge_stories(items)
    print(f"  输入 {len(items)} 条 -> 合并为 {len(stories)} 个故事")
    for s in stories:
        print(f"  [{s['story_id']}] {s['item_count']}条/{s['source_count']}源 "
              f"score={s['max_score']:.2f} cat={s['category']} "
              f"src={s['source_names']} | {s['primary_title'][:32]}")

    assert len(stories) == 5, f"应为5个故事, 实得{len(stories)}"

    story_a = next(s for s in stories if s["item_count"] == 4)
    assert story_a["source_count"] == 4, story_a["source_count"]
    assert set(story_a["source_names"]) == {"AMZ123", "雨果跨境", "AMZDH", "知无不言"}
    assert story_a["max_score"] == 0.85
    assert story_a["category"] == "policy_update"
    assert story_a["primary_title"] == "美国站FBA仓储费用标准调整 亚马逊发布公告"  # 最长标题为主
    assert story_a["primary_site_id"] == "amzdh"
    assert story_a["story_id"] == hashlib.sha1(
        story_a["primary_title"].encode("utf-8")).hexdigest()[:12]
    assert story_a["earliest_time"] == ago(B)
    assert story_a["latest_time"] == at_off(B, 2.5)
    assert any(it.get("published_at") is None for it in story_a["items"]), \
        "published_at=None 的条目应经 first_seen_at 并入故事A"

    story_e = next(s for s in stories if s["item_count"] == 3
                   and all("GPSR" in it["title"] for it in s["items"]))
    assert story_e["primary_title"].startswith("卖家注意"), \
        f"主条目应取最长标题, 实得: {story_e['primary_title']}"
    assert story_e["item_count"] == 3 and story_e["source_count"] == 3, "链式合并失败"

    story_b = next(s for s in stories if s["primary_title"].startswith("Amazon"))
    assert story_b["item_count"] == 2, "英文标题对未合并"

    story_d = next(s for s in stories if s["item_count"] == 1
                   and s["primary_title"].startswith("亚马逊宣布调整美国站"))
    assert story_d["primary_url"].endswith("/old"), "超窗口旧条目不应并入故事A"

    story_c = next(s for s in stories if "日本站" in s["primary_title"])
    assert story_c["item_count"] == 1, "不同事件(日本站佣金)不应并入故事A"
    assert title_similarity(items[1]["title"], items[6]["title"]) < 0.35

    assert [s["max_score"] for s in stories] == sorted(
        [s["max_score"] for s in stories], reverse=True), "故事未按 max_score 降序"

    # 空输入 / 单条边界
    assert merge_stories([]) == []
    single = merge_stories([{"title": "单独一条", "site_id": "amz123",
                             "url": "https://x/1", "published_at": ago(1.0),
                             "cross_score": 0.9, "cross_label": "general"}])
    assert len(single) == 1 and single[0]["item_count"] == 1

    print("== rank_hot_topics ==")
    top = rank_hot_topics(stories, top_n=3)
    for s in top:
        print(f"  #{s['rank']} hot={s['hot_score']:.2f} "
              f"(bonus={s['freshness_bonus']}, age={s['age_hours']}h) "
              f"{s['item_count']}条/{s['source_count']}源 | {s['primary_title'][:32]}")
    assert len(top) == 3
    assert [s["rank"] for s in top] == [1, 2, 3]
    # A: 4源*2 + 4条*0.5 + 0.85*3 + 新鲜2.0 = 14.55, 必须第一
    assert top[0]["item_count"] == 4
    assert abs(top[0]["hot_score"] - 14.55) < 1e-9
    # 链式故事E: 3源*2 + 3条*0.5 + 0.55*3 + 2.0 = 11.15 第二; 英文B 9.34 第三
    assert top[1]["item_count"] == 3 and abs(top[1]["hot_score"] - 11.15) < 1e-9
    assert top[2]["item_count"] == 2 and abs(top[2]["hot_score"] - 9.34) < 1e-9
    # 50h 前的单条旧闻(故事D)拿不到新鲜加分, 不进 top3
    assert all("亚马逊宣布调整美国站FBA仓储费用标准" != s["primary_title"]
               or s["item_count"] > 1 for s in top)
    assert rank_hot_topics([], top_n=5) == []
    originals = [dict(s) for s in stories]
    rank_hot_topics(stories)
    assert all(o == n for o, n in zip(originals, stories)), "rank_hot_topics 不应修改入参"

    print("ALL TESTS PASSED")
