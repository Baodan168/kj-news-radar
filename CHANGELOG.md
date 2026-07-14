# Changelog

## [1.1.0] - 2026-07-13

### Added
- Cross-relevance scoring system with UK/EU market bias
- Amazon UK/EU compliance boost (PPWR, GPSR, EPR detection)
- Non-Amazon platform penalty to reduce irrelevant platform noise
- Source health monitoring dashboard
- Policy calendar with upcoming EU/UK regulation dates
- BrowserAct-based JS rendering for dynamic Chinese news sites
- OPML custom RSS feed support

### Changed
- Relevance threshold calibrated for UK-focused Amazon sellers
- Title translation switched to MyMemory API with rate limiting
- Improved CJK mojibake detection and repair

### Fixed
- Duplicate URL detection across multiple sources
- Timezone handling for international news sources

## [1.0.0] - 2026-06-26

### Added
- Initial release
- 16 built-in news sources (Amazon official, Chinese aggregators, industry blogs)
- Parallel news collection with ThreadPoolExecutor
- 24-hour rolling window news aggregation
- GitHub Actions daily auto-update at 09:00 CST
- Static GitHub Pages deployment
- Source status tracking per collector
- Archive system with 21-day retention window
- English-to-Chinese title translation