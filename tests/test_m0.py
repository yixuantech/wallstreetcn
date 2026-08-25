#!/usr/bin/env python3
"""M0 地基层冒烟测试 — state/split_meta/交易日/runner命令表（不调AI不推送）

用法: PYTHONIOENCODING=utf-8 python tests/test_m0.py
"""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import state
from src.analyzer import split_meta
from src.utils import is_trading_day


def test_split_meta():
    """预判块剥离：正常剥离/无块/坏JSON三态"""
    # 1. 正常：报告带尾块
    report = "### 🎯 今日A股预判\n\n看偏多\n\n```json\n{\"direction\": \"看偏多\", \"sectors\": [\"黄金\"], \"news_marks\": [{\"title\": \"美加关税\", \"mark\": \"利空\"}]}\n```"
    clean, meta = split_meta(report)
    assert meta and meta["direction"] == "看偏多" and meta["sectors"] == ["黄金"]
    assert "```json" not in clean and "看偏多" in clean, "剥离后正文应保留预判文字、无JSON块"
    # 2. 正文含示例块 + 尾部真块：只取最后一个
    report2 = "示例：```json\n{\"direction\": \"占位\"}\n```\n\n正文。\n\n```json\n{\"direction\": \"看空\"}\n```"
    clean2, meta2 = split_meta(report2)
    assert meta2["direction"] == "看空" and "正文" in clean2
    # 3. 无块
    clean3, meta3 = split_meta("纯报告无块")
    assert meta3 is None and clean3 == "纯报告无块"
    # 4. 坏JSON
    clean4, meta4 = split_meta("报告\n\n```json\n{坏的}\n```")
    assert meta4 is None, "坏JSON应降级返回None不抛异常"
    print("  [1] split_meta: 4态全部通过")


def test_state_today():
    """today.json 往返 + 跨日重置"""
    STATE = state.STATE_DIR / "today.json"
    backup = STATE.read_text(encoding="utf-8") if STATE.exists() else None
    try:
        # 写昨天日期 → 读回应重置为今天空白
        state._save_json("today.json", {"date": "2000-01-01", "morning": {"pushed": True}})
        t = state.load_today()
        assert t["morning"] == {} and t["date"] != "2000-01-01", "跨日应自动重置"
        # 正常往返
        t["morning"] = {"pushed": True, "judgment": {"direction": "看偏多"},
                        "pushed_event_ids": ["http://a", "http://b"]}
        state.save_today(t)
        t2 = state.load_today()
        assert t2["morning"]["pushed"] is True and len(t2["morning"]["pushed_event_ids"]) == 2
        # 损坏文件降级
        STATE.write_text("{broken", encoding="utf-8")
        t3 = state.load_today()
        assert t3["morning"] == {}, "损坏文件应降级返回空白而非崩溃"
    finally:
        if backup is not None:
            STATE.write_text(backup, encoding="utf-8")
        elif STATE.exists():
            STATE.unlink()
    print("  [2] today.json 往返/跨日重置/损坏降级: 通过")


def test_state_judgment():
    """judgments.csv 追加"""
    CSV = state.STATE_DIR / "judgments.csv"
    backup = CSV.read_text(encoding="utf-8") if CSV.exists() else None
    try:
        state.append_judgment({"date": "2026-08-24", "judgment": "看偏多",
                               "actual": "沪指-0.59%", "result": "错", "detail": "白酒✓"})
        content = CSV.read_text(encoding="utf-8")
        assert "看偏多" in content and "date,judgment" in content, "表头+行应写入"
    finally:
        if backup is not None:
            CSV.write_text(backup, encoding="utf-8")
        elif CSV.exists():
            CSV.unlink()
    print("  [3] judgments.csv 追加: 通过")


def test_trading_day():
    assert is_trading_day(date(2026, 8, 22)) is False, "周六非交易日"
    assert is_trading_day(date(2026, 8, 23)) is False, "周日非交易日"
    assert is_trading_day(date(2026, 8, 24)) is True, "周一交易日"
    print("  [4] is_trading_day 周末/工作日: 通过")


def test_runner_commands():
    import runner
    for cmd in ["morning", "noon", "evening", "macro_cn", "macro_us", "night", "weekly"]:
        assert cmd in runner.COMMANDS, f"缺命令 {cmd}"
        assert callable(runner.COMMANDS[cmd])
    print("  [5] runner 七命令注册: 通过")


def main():
    print("M0 地基层冒烟测试")
    test_split_meta()
    test_state_today()
    test_state_judgment()
    test_trading_day()
    test_runner_commands()
    print("\n" + "=" * 50)
    print("✓ M0 冒烟测试全部通过")


if __name__ == "__main__":
    main()
