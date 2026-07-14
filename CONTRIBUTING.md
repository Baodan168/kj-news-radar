# Contributing to 跨境雷达 (KJ News Radar)

Thank you for considering contributing! This project aims to help cross-border e-commerce sellers stay informed with the latest industry news.

## How to Contribute

### Reporting Issues

- Check if the issue already exists before creating a new one
- Use the issue templates (bug report or feature request)
- Include the data source URL if reporting a broken news source
- Include relevant logs or error messages if applicable

### Suggesting New Data Sources

Cross-border e-commerce news sources are always evolving. To suggest a new source:

1. Open a feature request issue with the source URL
2. Describe the source (official announcement, aggregator, community, etc.)
3. Explain why it's relevant to cross-border e-commerce sellers

### Pull Requests

1. Fork the repository
2. Create a feature branch from `master`
3. Make your changes
4. Test locally: `python scripts/update_crossborder.py --output-dir data --window-hours 24`
5. Submit a pull request with a clear description of what changed and why

### Development Setup

```bash
git clone https://github.com/liyuhong168/kj-news-radar.git
cd kj-news-radar
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/update_crossborder.py --output-dir data --window-hours 24
```

### Code Style

- Python: follow PEP 8
- Keep functions focused and well-documented
- Add docstrings for new functions
- Cross-relevance scoring keywords should be organized by category

## Questions?

Open a discussion or issue. We're happy to help.