#!/usr/bin/env python3
"""周报预览 — 完整管线（真实采集+真实AI），但不推送、不标记已推。

产出 data/reports/YYYY-MM-DD-weekly-preview.md 供人工审阅。
副作用说明：周报原料全部是只读组装（主线/judgments/观察点/日历），
不写任何状态文件，正式周六跑无重复副作用。

用法: NO_PROXY='*' no_proxy='*' PYTHONIOENCODING=utf-8 python tests/preview_weekly.py
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.analyzer import analyze_weekly
from src.engines import (weekly_judgment_stats, format_weekly_judgments,
                         format_weekly_watchpoints, format_next_calendar)
from src.macro_data import next_publish_calendar, cn_release_forecast
from src.storylines import format_weekly_storylines
from src.utils import today_str, monday_of
from src.watchlist import collect_calendar_only
from src import state
from src.utils import CST


def main():
    print("周报预览（不推送）")
    print("=" * 60)

    date_str = datetime.now(CST).strftime("%Y-%m-%d")
    week_start = monday_of(date_str)
    print(f"本周区间: {week_start} ~ {date_str}")

    lines = state.load_storylines().get("lines", [])
    print(f"主线: 共{len(lines)}条（活跃{sum(1 for l in lines if l.get('status') != '终结')}条）")

    rows = state.load_judgments(week_start, date_str)
    stats = weekly_judgment_stats(rows)
    rate_txt = f"记分胜率{stats['rate']}%" if stats["rate"] is not None else "本周无记分"
    print(f"记分: 本周{stats['total']}条流水，{rate_txt}")

    cal_stocks = collect_calendar_only()
    us_cal = next_publish_calendar(14)
    cn_cal = cn_release_forecast(14)
    print(f"日历: 自选股{sum(len(s.calendar_events) for s in cal_stocks)}条 + "
          f"美国{len(us_cal)}条 + 中国惯例{len(cn_cal)}条")

    data_blocks = "\n\n".join([
        format_weekly_storylines(lines, week_start),
        format_weekly_judgments(stats),
        format_weekly_watchpoints(week_start),
        format_next_calendar(cal_stocks, us_cal, cn_cal),
    ])
    print("\n---- 数据块预览 ----\n")
    print(data_blocks)
    print("\n---- 调用AI生成周报 ----\n")
    report = analyze_weekly(data_blocks)

    out = Path("data/reports") / f"{date_str}-weekly-preview.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"\n周报预览已落盘: {out}")
    week_txt = f"{week_start[5:].replace('-', '/')}当周"
    print(f"拟用标题: 📖 AI盘报·周报 | {week_txt} | {rate_txt}")


if __name__ == "__main__":
    main()
