#!/usr/bin/env python3
"""生成今日报告预览 — 完整流程但【不推送、不记录已处理ID】

用法: NO_PROXY='*' python tests/preview_report.py
产出: data/reports/YYYY-MM-DD.md（用于检查AI输出格式/内容质量）
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.fetcher import get_latest_breakfast, get_all_market_data, format_market_data
from src.analyzer import analyze
from src.watchlist import collect_all, format_watchlist_prompt


def main():
    article = get_latest_breakfast()
    if not article:
        print("[Preview] 未获取到早餐FM文章")
        return

    market_data = format_market_data(get_all_market_data())
    stocks = collect_all()
    watchlist_data = format_watchlist_prompt(stocks) if stocks else ""

    report = analyze(article.content_text, market_data, watchlist_data)

    out_dir = Path(__file__).parent.parent / "data" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    out.write_text(report, encoding="utf-8")
    print(f"[Preview] 报告已落盘: {out} ({len(report)}字)")


if __name__ == "__main__":
    main()
