# 跨境雷达｜24 小时跨境电商更新雷达

自动整理过去 24 小时值得看的跨境电商、亚马逊、Temu、TikTok Shop 等平台更新。

## 特性

- **20+ 信息源**：亚马逊官方、AMZ123、雨果跨境、Helium10、NovaData 等中英文源
- **跨境相关性打分**：纯关键词规则，零 LLM 消耗
- **事件聚类**：多条报道同一事件自动合并为一个信号
- **政策日历**：即将生效的政策变动一目了然
- **源健康监控**：实时显示每个信息源的采集状态
- **静态部署**：GitHub Pages + GitHub Actions，零成本

## 架构

```
Source List → 并行采集(20+源) → URL去重 → 跨境相关性打分 → JSON输出 → GitHub Pages
```

### 信息源矩阵

| 层级 | 源 | 类型 |
|---|---|---|
| 官方 | Amazon Newsroom, SP-API Changelog, Amazon Ads, 全球开店 | RSS + Jina |
| 聚合 | AMZ123, AMZDH, 雨果跨境, 跨境电商365, TopHub | Jina |
| 行业 | Helium10, Jungle Scout, EcomBrainly, NovaData, EcomEngine | RSS + Jina |
| 社区 | WeAreSellers, Seller Sessions Podcast | RSS |
| 扩展 | OPML/RSS 自定义源 | RSS |

## 使用

### 直接访问
打开 [GitHub Pages 链接](https://liyuhong168.github.io/kj-news-radar/) 即可。

### 本地运行
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/update_crossborder.py --output-dir data --window-hours 24
python -m http.server 8080
```

### 自定义源
```bash
cp feeds/follow.example.opml feeds/follow.opml
# 编辑 follow.opml 添加你的 RSS 源
python scripts/update_crossborder.py --output-dir data --rss-opml feeds/follow.opml
```

## GitHub Actions

每天北京时间 09:00 自动更新。可手动触发。

## License

MIT
