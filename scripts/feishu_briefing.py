#!/usr/bin/env python3
"""跨境雷达每日简报推送到飞书。供 Hermes cron 调用。"""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BJ_TZ = timezone(timedelta(hours=8))


def load_json(path: Path) -> dict | list | None:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def fmt_time(iso_str: str | None) -> str:
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        dt_bj = dt.astimezone(BJ_TZ)
        return dt_bj.strftime("%m/%d %H:%M")
    except Exception:
        return "—"


def main():
    data_dir = Path(__file__).parent.parent / "data"
    now_bj = datetime.now(BJ_TZ)

    # Load data
    latest = load_json(data_dir / "latest-24h.json")
    policy = load_json(data_dir / "policy-calendar.json")

    if not latest:
        print("⚠️ 跨境雷达数据未加载，请先运行采集脚本。")
        return

    total = latest.get("total_items", 0)
    sites = latest.get("site_count", 0)
    items = latest.get("items", [])

    # Top picks (highest score)
    scored = sorted(items, key=lambda x: x.get("cross_score", 0), reverse=True)

    lines = [
        f"📦 **跨境雷达日报 · {now_bj.strftime('%Y-%m-%d')}**",
        "",
        f"过去24小时 **{total}** 条跨境信号，来自 **{sites}** 个信息源。",
        "",
    ]

    # Top 5 signals
    if scored:
        lines.append("**🔴 今日重点信号**")
        for i, item in enumerate(scored[:5], 1):
            label_map = {
                "policy_update": "📋政策", "fee_logistics": "📦费用",
                "advertising": "📢广告", "listing_product": "🏷️选品",
                "platform_trend": "📈趋势", "seller_action": "⚡紧急",
            }
            label = label_map.get(item.get("cross_label", ""), "📰资讯")
            score = round((item.get("cross_score", 0)) * 100)
            title = item.get("title", "")[:60]
            source = item.get("site_name", "")
            lines.append(f"{i}. {label} [{score}分] {title}")
            lines.append(f"   📎 {source}")
        lines.append("")

    # Upcoming policies
    if policy:
        upcoming = [
            p for p in policy
            if (datetime.strptime(p["effective_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc) > datetime.now(timezone.utc))
        ]
        if upcoming:
            lines.append("**📅 即将生效政策**")
            for p in sorted(upcoming, key=lambda x: x["effective_date"])[:3]:
                days = (datetime.strptime(p["effective_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).days
                lines.append(f"• {p['title']} — {days}天后生效")
            lines.append("")

    lines.append(f"🔗 完整数据：https://Baodan168.github.io/kj-news-radar/")
    lines.append(f"⏰ 更新时间：{latest.get('generated_at', '—')}")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
