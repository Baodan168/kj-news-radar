# 跨境雷达 (KJ News Radar)

## 一句话定位

24h 跨境电商资讯聚合器，20+ 源自动采集 → 打分 → 聚类 → 展示，专为 Amazon UK 卖家设计。零 LLM 消耗，纯关键词规则打分。

## 怎么跑起来

```bash
cd /home/lee/kj-news-radar
source .venv/bin/activate

# 本地更新
python scripts/update_crossborder.py --output-dir data --window-hours 24

# 本地预览
python3 -m http.server 8080
```

## 关键文件

| 文件 | 作用 |
|------|------|
| `scripts/update_crossborder.py` | 主采集脚本 |
| `data/latest-24h.json` | 跨境相关条目（精简版） |
| `data/latest-24h-all.json` | 所有条目（完整版） |
| `data/archive.json` | 21 天归档窗口 |
| `data/source-status.json` | 各源采集状态 |
| `index.html` | 展示页面 |
| `assets/styles.css` | 样式 |

## 架构

```
20+ 源 → 并行采集 → URL 去重 → 打分 → 翻译 → JSON → GitHub Pages
```

## 打分权重

| 维度 | 加权 | 说明 |
|------|------|------|
| Amazon UK | +0.12 | 最高优先级 |
| Amazon 全球 | +0.08 | 亚马逊生态 |
| EU/UK 合规 | +0.05 | 政策法规 |
| 跨境新闻 | 基准 | 一般资讯 |
| 非 Amazon | -0.15 | 非目标市场 |

## 部署

- **本地 cron 采集**：Hermes cron 每天 08:40 跑 `~/.hermes/scripts/kj_local_update.py`
  → 本地跑 `update_crossborder.py`（CN 网络直连 CN 源）→ GitHub Contents API 推送数据 → 触发 Pages 重建
- **不要用 GHA schedule 采集**：GHA 美国 runner IP 被 CN 源 CDN 拦截/冻结，
  采集量从 250+ 掉到 3-5 条（2026-09-01~13 事故）。workflow 的 `schedule` 已注释禁用，仅保留 `workflow_dispatch` 作手动兜底
- git push 被 GFW 阻断 → 用 GitHub Contents API 推（见 `~/.hermes/scripts/api_commit.py` 同款方式）
- GitHub Pages 部署，OA 门户通过 iframe 直链访问

## 操作禁忌

- ❌ 改展示逻辑要同步更新 product-radar 的 iframe 引用
- ❌ 不要直接改 GitHub Pages 产物，改脚本重新生成
- ✅ master 分支是部署分支，推送前确认脚本已跑通

## 当前状态

- 本地 cron 采集（每天 08:40），管道正常：采集 → API 推送 → Pages 重建
- ⚠️ **ennews（亿恩网）2026-09-24 起停更**：feed 最新文章停在 9/24，站点首页被滑块验证码拦截（HTTP 200 但返回人机验证页），RSS 端点仍可访问。连续 2 轮 0 条会触发健康告警，属真实停更而非采集 bug。恢复探测：直接 curl feed 看最新文章日期
- parse_iso 修复（2026-09-27）：ennews 式异常 pubDate（"39, 24 Sep 2026 17:34:00"）重建时保留时分秒，晚间文章不再被提前老化 24h
- 周末采集量自然偏低（约为工作日 1/3，各源发文少），raw 40 条左右属正常；工作日基线 raw ≈ 140
- 改 JS/CSS 后必须 bump `index.html` 里的 `?v=N`（当前 app.js?v=15）

## v2 架构（2026-09-27 全面对标升级，参考 aihot/ai-news-radar/ai-safety-radar）

| 新增产物 | 说明 |
|---|---|
| `data/stories-merged.json` | 事件聚类：标题 Jaccard 相似度(中文bigram/英文分词)≥0.35 + 24h窗口 → 故事线 |
| `data/hot-topics.json` | 今日热点榜 Top10：多源信号×时间衰减（source_count×2 + item_count×0.5 + max_score×3 + freshness_bonus） |
| `data/weekly-rollup.json` | 7天周报：日增量/源贡献/label分布/UK相关数/高分信号TOP5（archive记录无打分字段，现算cross_relevance） |
| `data/feed.xml` | RSS 2.0 订阅输出（跨境信号前50条，含label+评分） |

- `cross_why` 字段：每条信号一句话"为什么值得关注"，纯规则生成（cross_relevance.py 的 REASON_RULES，13条优先级规则），零 LLM
- `source_tiers` 字段：源分级（1=亚马逊官方/2=行业聚合/3=社区UGC），在 latest-24h.json payload
- 72h 回填归位（display_time）：发布后72h内收录的按收录时间算"今天"（慢推源友好），超72h归位原文发布日。24h窗口过滤用它，archive清洗仍用 event_time
- 前端：卡片理由行 + 今日热点榜区块（数据缺失自动隐藏）+ app.js?v=15
- 部署注意：`~/.hermes/scripts/kj_local_update.py` 的 DATA_FILES 已含4个新产物，cron 会自动推送