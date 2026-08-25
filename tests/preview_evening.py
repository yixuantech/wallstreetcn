#!/usr/bin/env python3
"""晚报预览 — 完整管线（真实采集+真实AI），但不推送、不标记已推。

产出 data/reports/YYYY-MM-DD-evening-preview.md 供人工审阅。
副作用说明：情绪归档/观察点扫描是真实写入（幂等，正式晚跑重复无害）；
记分流水只在晨报快照存在时写入（今天没跑晨报则跳过）。

用法: NO_PROXY='*' no_proxy='*' PYTHONIOENCODING=utf-8 python tests/preview_evening.py
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.analyzer import analyze_evening
from src.engines import (sentiment_gauge, archive_sentiment, format_sentiment_prompt,
                         scoreboard, format_scoreboard_prompt, scoreboard_summary,
                         update_watchpoints, format_watchpoints_prompt)
from src.market_close import collect_close_panorama, format_close_prompt
from src.utils import today_str, is_trading_day
from src.watchlist import collect_all, format_close_watchlist_prompt
from src import state
from src.utils import CST


def main():
    print("晚报预览（不推送）")
    print("=" * 60)
    if not is_trading_day():
        print("今日非交易日，预览仍继续（数据为上一交易日）")

    today = state.load_today()
    panorama = collect_close_panorama()

    gauge = sentiment_gauge(panorama)
    hist_line = archive_sentiment(gauge, panorama)
    print(f"情绪刻度: {gauge.get('score')}/{gauge.get('label')}  [{gauge.get('bar', '')}]")

    stocks = collect_all()
    sb = scoreboard(today.get("morning", {}), panorama, stocks)
    print(f"记分牌: {scoreboard_summary(sb)}")

    wp = update_watchpoints(stocks)
    print(f"观察点: 挂起{len(wp.get('active', []))} 新增{len(wp.get('added', []))}")

    pushed_urls = set(today.get("morning", {}).get("pushed_event_ids", []))
    if stocks:
        watch_block = ("## 自选股收盘（相对晨报的增量事件）\n\n"
                       + format_close_watchlist_prompt(stocks, pushed_urls))
    else:
        watch_block = "## 自选股收盘\n\n自选列表为空"

    data_blocks = "\n\n".join([
        format_close_prompt(panorama),
        format_sentiment_prompt(gauge, hist_line),
        format_scoreboard_prompt(sb),
        format_watchpoints_prompt(wp),
        watch_block,
    ])
    print("\n---- 数据块预览 ----\n")
    print(data_blocks)
    print("\n---- 调用AI生成晚报 ----\n")
    report = analyze_evening(data_blocks)

    out = Path("data/reports") / f"{datetime.now(CST).strftime('%Y-%m-%d')}-evening-preview.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"\n晚报预览已落盘: {out}")
    score_txt = f"情绪{gauge['score']}·{gauge['label']}" if gauge.get("score") is not None else "情绪缺"
    print(f'拟用标题: 🌙 AI盘报·晚报 | {today_str()} | {scoreboard_summary(sb)} | {score_txt}')


if __name__ == "__main__":
    main()
