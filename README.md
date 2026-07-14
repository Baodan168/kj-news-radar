# 跨境雷达 — 24小时跨境电商更新雷达

[![GitHub](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/)
[![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-auto--update-brightgreen)](https://github.com/liyuhong168/kj-news-radar/actions)

**跨境雷达 (KJ News Radar)** 自动聚合过去 24 小时值得看的跨境电商、亚马逊、Temu、TikTok Shop 等平台更新，专为跨境电商卖家设计。

> 🎯 聚焦 Amazon UK/EU 卖家视角，智能过滤噪音，只看与你业务相关的信息。

---

## 特性

- **20+ 信息源** — 亚马逊官方、AMZ123、雨果跨境、Helium10、NovaData、ChannelX 等中英文源
- **智能跨境相关性打分** — 纯关键词规则，零 LLM 消耗，UK/EU 市场加权
- **事件聚类** — 多条报道同一事件自动合并为一个信号，减少重复阅读
- **政策日历** — 即将生效的 EU/UK 政策变动一目了然（GPSR、PPWR 等）
- **源健康监控** — 实时显示每个信息源的采集状态
- **零成本部署** — GitHub Actions + GitHub Pages，无需服务器

### 打分权重

| 维度 | 加权 | 说明 |
|------|------|------|
| Amazon UK 相关 | +0.12 | 英国站卖家最优先 |
| Amazon 全球 | +0.08 | 亚马逊生态通用 |
| EU/UK 合规 | +0.05 | 政策法规类 |
| 跨境新闻 | 基准 | 一般跨境资讯 |
| 非 Amazon 平台 | -0.15 | 非目标市场 |

---

## 快速开始

### 直接访问

打开 [kj-news-radar](https://liyuhong168.github.io/kj-news-radar/) 即可查看最新资讯。

### 本地运行

```bash
git clone https://github.com/liyuhong168/kj-news-radar.git
cd kj-news-radar
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/update_crossborder.py --output-dir data --window-hours 24
python -m http.server 8080
```

访问 `http://localhost:8080` 查看本地版。

### 自定义 RSS 源

```bash
cp feeds/follow.example.opml feeds/follow.opml
# 编辑 follow.opml 添加你的 RSS 源
python scripts/update_crossborder.py --output-dir data --rss-opml feeds/follow.opml
```

---

## 架构

```
Source List (20+ 源)
    │
    ▼
并行采集 (ThreadPoolExecutor, max_workers=8)
    │
    ▼
URL 去重 & 归一化
    │
    ▼
跨境相关性打分 (cross_relevance.py)
    │  ├─ 关键词匹配 (Amazon/UK/EU/合规/物流等)
    │  ├─ 平台加权 (Amazon UK +0.12, 非 Amazon -0.15)
    │  └─ 噪音过滤 (国内电商/娱乐/促销)
    │
    ▼
英文标题翻译 (MyMemory API, 限速 1req/s)
    │
    ▼
JSON 输出 → GitHub Pages
```

### 信息源矩阵

| 层级 | 源 | 类型 |
|------|----|------|
| 官方 | Amazon Newsroom, SP-API Changelog, Amazon Ads, 全球开店 | RSS + BrowserAct |
| 聚合 | AMZ123, AMZDH, 雨果跨境, 跨境电商365, TopHub | BrowserAct + HTML |
| 行业 | Helium10, Jungle Scout, EcomBrainly, NovaData, EcomEngine | RSS + HTML |
| 社区 | WeAreSellers, Seller Sessions Podcast | RSS |
| 扩展 | OPML/RSS 自定义源 | RSS |

---

## 输出文件

| 文件 | 说明 |
|------|------|
| `latest-24h.json` | 仅跨境相关条目（精简版） |
| `latest-24h-all.json` | 所有条目（完整版） |
| `archive.json` | 21 天归档窗口 |
| `source-status.json` | 各源采集状态 |
| `policy-calendar.json` | EU/UK 政策日历 |
| `title-zh-cache.json` | 英文标题翻译缓存 |

---

## GitHub Actions

每天北京时间 09:00 自动更新，也可手动触发：

1. 打开仓库 Actions 页面
2. 选择 `Update News` workflow
3. 点击 `Run workflow`

---

## 为什么选择跨境雷达？

- **专为 Amazon UK 卖家设计** — 打分系统优先展示英国站相关资讯
- **零运营成本** — 配置好后自动运行，无需手动维护
- **多源交叉验证** — 同一事件多条来源，减少信息偏差
- **政策合规提醒** — 内置 EU/UK 政策日历，不错过重要合规节点

---

## 贡献

欢迎贡献新的数据源或改进！详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 安全策略

报告安全漏洞请参阅 [SECURITY.md](SECURITY.md)。

---

## License

[MIT](LICENSE) © 2026 Li Yuhong