#!/usr/bin/env python3
"""M2 注入式预览 — 模拟午间触发与夜巡🔴命中（真实采集+真实AI，不推送不写状态）

向真实采集结果注入两条模拟事件，验证完整触发链路：
  午间：🟡股票新增公告 → 三件套快讯
  夜巡：🔴立案公告 → 紧急警报
产出 data/reports/YYYY-MM-DD-noon-preview.md / -alert-preview.md 供人工审阅。

用法: NO_PROXY='*' no_proxy='*' PYTHONIOENCODING=utf-8 python tests/preview_noon_alert.py
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.analyzer import analyze_noon, analyze_alert
from src.engines import (scan_new_events, scan_red_alerts, filter_flagged,
                         format_noon_prompt, format_alert_prompt)
from src.market_close import _tencent_index
from src.utils import today_str, CST
from src.watchlist import collect_all, LABEL_RED

TODAY = datetime.now(CST).strftime("%Y-%m-%d")


def main():
    print("M2 注入式预览（不推送）")
    print("=" * 60)

    stocks = collect_all()
    if not stocks:
        print("自选为空，退出")
        return
    target = stocks[0]

    # ── 注入1：🟡新增公告（午间触发路径）──
    target.announcements.insert(0, {
        "title": f"关于投资建设新一代生产线项目的公告（注入测试·模拟）",
        "date": TODAY,
        "url": "https://example.com/injected-noon",
    })
    new_events = scan_new_events(stocks, set())          # 空池=全部视为新增
    flagged = filter_flagged(new_events)
    print(f"午间触发: {len(flagged)}/{len(new_events)} 只股票通过护栏")
    if not flagged:
        print("⚠️ 注入股票未过护栏（标签非🟡/🔴），检查注入方式")
        return

    indices_line = []
    for name, code in (("上证指数", "sh000001"), ("创业板指", "sz399006")):
        q = _tencent_index(code)
        if q:
            indices_line.append(f"{name} {q['close']} ({q['chg_pct']:+.2f}%)")
    indices_txt = " ｜ ".join(indices_line) or "指数行情缺失"

    noon_report = analyze_noon(indices_txt, format_noon_prompt(flagged))
    out1 = Path("data/reports") / f"{TODAY}-noon-preview.md"
    out1.write_text(noon_report, encoding="utf-8")
    print(f"午间快讯预览: {out1}  拟标题: ⚡ AI盘报·午间快讯 | {today_str()} | {target.name}")

    # ── 注入2：🔴立案公告（夜巡触发路径）──
    red = collect_all()[0]
    red.label = LABEL_RED
    red.announcements.insert(0, {
        "title": f"关于公司收到中国证监会立案调查通知的公告（注入测试·模拟）",
        "date": TODAY,
        "url": "https://example.com/injected-alert",
    })
    alerts = scan_red_alerts(scan_new_events([red], set()))
    print(f"夜巡触发: {len(alerts)} 条🔴命中")
    if not alerts:
        print("⚠️ 立案公告未命中关键词，检查关键词表")
        return
    alert_report = analyze_alert(format_alert_prompt(alerts))
    out2 = Path("data/reports") / f"{TODAY}-alert-preview.md"
    out2.write_text(alert_report, encoding="utf-8")
    print(f"夜巡警报预览: {out2}  拟标题: 🚨 AI盘报·紧急警报 | {today_str()} | {red.name}")


if __name__ == "__main__":
    main()
