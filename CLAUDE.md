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

- GitHub Actions 每天 09:00 自动更新
- 手动触发：Actions → Update News → Run workflow
- GitHub Pages 部署，OA 门户通过 iframe 直链访问

## 操作禁忌

- ❌ 改展示逻辑要同步更新 product-radar 的 iframe 引用
- ❌ 不要直接改 GitHub Pages 产物，改脚本重新生成
- ✅ master 分支是部署分支，推送前确认脚本已跑通

## 当前状态

- 稳定运行，GHA 自动更新
- 约 363 条信号/天，聚类 10+ 跨源事件