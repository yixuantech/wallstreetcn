#!/usr/bin/env python3
"""M2 冒烟测试 — 增量扫描/🔴关键词/触发护栏（合成注入，不调AI不推送）

用法: PYTHONIOENCODING=utf-8 python tests/test_m2.py
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import state
from src.engines import (scan_new_events, scan_red_alerts, filter_flagged,
                         format_noon_prompt, format_alert_prompt, RED_KEYWORDS)
from src.utils import CST
from src.watchlist import StockInfo, LABEL_RED, LABEL_YELLOW, LABEL_GREEN

TODAY = datetime.now(CST).strftime("%Y-%m-%d")


def _mk_stock(code, name, label, news=None, anns=None):
    s = StockInfo(code=code, name=name, label=label, label_reason="测试注入")
    s.price, s.chg_pct = 100.0, -3.5
    s.news = news or []
    s.announcements = anns or []
    return s


def test_scan_new_events():
    pushed = {"http://old-news", "http://old-ann"}
    today_news = {"title": "今日资讯", "time": f"{TODAY} 10:30", "url": "http://new-news"}
    yday_news = {"title": "昨日资讯", "time": f"2020-01-01 10:00", "url": "http://yday-news"}
    today_ann = {"title": "今日公告", "date": TODAY, "url": "http://new-ann"}
    yday_ann = {"title": "昨日公告", "date": "2020-01-01", "url": "http://yday-ann"}
    s = _mk_stock("600519", "贵州茅台", LABEL_YELLOW,
                  news=[today_news, yday_news, {"title": "已推", "time": f"{TODAY} 08:00", "url": "http://old-news"}],
                  anns=[today_ann, yday_ann, {"title": "已推公告", "date": TODAY, "url": "http://old-ann"}])
    result = scan_new_events([s], pushed)
    assert len(result) == 1
    stock, items = result[0]
    urls = {it["url"] for it in items}
    assert urls == {"http://new-news", "http://new-ann"}, f"增量={urls}"
    types = {it["type"] for it in items}
    assert types == {"资讯", "公告"}
    # 无新增股票不出现
    s2 = _mk_stock("300750", "宁德时代", LABEL_GREEN, news=[], anns=[])
    assert scan_new_events([s2], pushed) == []
    # today_only=False 时昨日事件也进增量
    result2 = scan_new_events([s], pushed, today_only=False)
    assert len(result2[0][1]) == 4
    print("  [1] scan_new_events 防重/当日过滤/无新增: 通过")


def test_scan_red_alerts():
    s = _mk_stock("600519", "贵州茅台", LABEL_RED, anns=[
        {"title": "关于公司收到中国证监会立案调查通知的公告", "date": TODAY, "url": "http://li"},
        {"title": "2026年半年度报告", "date": TODAY, "url": "http://report"},
        {"title": "控股股东减持股份计划公告", "date": TODAY, "url": "http://jc"},
    ])
    new_events = scan_new_events([s], set())
    alerts = scan_red_alerts(new_events)
    hits = {(it["url"], kw) for _, it, kw in alerts}
    assert ("http://li", "立案") in hits
    assert ("http://jc", "减持") in hits
    assert "http://report" not in {it["url"] for _, it, _ in alerts}, "半年报不应命中"
    print(f"  [2] scan_red_alerts 立案/减持命中、报告不误伤: 通过（关键词表{len(RED_KEYWORDS)}个）")


def test_filter_flagged():
    yellow = _mk_stock("1", "A", LABEL_YELLOW, news=[{"title": "t", "time": f"{TODAY} 10:00", "url": "u1"}])
    green = _mk_stock("2", "B", LABEL_GREEN, news=[{"title": "t", "time": f"{TODAY} 10:00", "url": "u2"}])
    red = _mk_stock("3", "C", LABEL_RED, news=[{"title": "t", "time": f"{TODAY} 10:00", "url": "u3"}])
    new_events = scan_new_events([yellow, green, red], set())
    flagged = filter_flagged(new_events)
    assert {s.name for s, _ in flagged} == {"A", "C"}, "🟢必须被护栏拦下"
    assert filter_flagged([]) == []
    print("  [3] filter_flagged 三件套护栏: 通过")


def test_prompt_blocks():
    s = _mk_stock("600519", "贵州茅台", LABEL_YELLOW, anns=[
        {"title": "减持公告", "date": TODAY, "url": "http://x"}])
    new_events = scan_new_events([s], set())
    noon_txt = format_noon_prompt(new_events)
    assert "贵州茅台(600519)" in noon_txt and "规则理由" in noon_txt and "http://x" in noon_txt
    alerts = scan_red_alerts(new_events)
    alert_txt = format_alert_prompt(alerts)
    assert "命中关键词「减持」" in alert_txt
    print("  [4] 快讯/警报数据块渲染: 通过")


def main():
    print("M2 冒烟测试（午间快讯+夜巡）")
    test_scan_new_events()
    test_scan_red_alerts()
    test_filter_flagged()
    test_prompt_blocks()
    print("\n" + "=" * 50)
    print("✓ M2 冒烟测试全部通过")


if __name__ == "__main__":
    main()
