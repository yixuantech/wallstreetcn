#!/usr/bin/env python3
"""M4 冒烟测试 — 主线状态机/模式锚归档/周胜率/下周日历（合成数据，不联网不推送）

用法: PYTHONIOENCODING=utf-8 python tests/test_m4.py
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import state
from src.storylines import (iso_week, merge_storylines, format_storylines_prompt,
                            format_weekly_storylines)
from src.engines import (archive_pattern_events, format_pattern_bank,
                         weekly_judgment_stats, format_weekly_judgments,
                         format_weekly_watchpoints, format_next_calendar)
from src.macro_data import cn_release_forecast, next_publish_calendar
from src.utils import monday_of
from src.watchlist import StockInfo

W35_TUE = "2026-08-25"      # 2026-W35 周二
W36_MON = "2026-08-31"      # 2026-W36 周一（跨周翻转用）
WEEK_START = "2026-08-24"   # 本周一


def _mk_line(id=1, name="美债信用与贬值交易", status="发酵", weeks=1,
             week_key="2026-W35", **kw):
    line = {"id": id, "name": name, "status": status, "weeks": weeks,
            "week_key": week_key, "updated": W35_TUE,
            "progress": kw.pop("progress", ""), "log": kw.pop("log", [])}
    line.update(kw)
    return line


def test_week_helpers():
    assert iso_week("2026-08-26") == "2026-W35", "8-26 应为W35"
    assert iso_week(W36_MON) == "2026-W36", "8-31 应为W36"
    assert iso_week(datetime(2026, 8, 24)) == "2026-W35", "date对象也应支持"
    assert monday_of("2026-08-26") == WEEK_START, "周三的周一应为8-24"
    assert monday_of("2026-08-29") == WEEK_START, "周六的周一应为8-24"
    assert monday_of(W36_MON) == W36_MON, "周一的周一是自身"
    print("  [1] iso_week/monday_of: 通过")


def test_merge_basic():
    # 状态迁移 + 进展更新记档
    old = [_mk_line(id=1, name="主线A", status="发酵")]
    ai = [{"id": 1, "name": "主线A", "status": "主导", "progress": "今日催化X"}]
    lines, changes = merge_storylines(old, ai, today=W35_TUE)
    assert lines[0]["status"] == "主导" and lines[0]["weeks"] == 1, "同周weeks不变"
    assert lines[0]["progress"] == "今日催化X"
    assert lines[0]["log"][-1] == {"date": W35_TUE, "text": "状态: 发酵→主导；今日催化X"}
    assert changes == ["#1 状态: 发酵→主导"] or any("发酵→主导" in c for c in changes)
    # AI未提及的旧线保留
    old2 = [_mk_line(id=1, name="主线A"), _mk_line(id=2, name="主线B")]
    lines2, _ = merge_storylines(old2, ai, today=W35_TUE)
    assert [l["name"] for l in lines2] == ["主线A", "主线B"], "未提及≠终结"
    print("  [2] 状态迁移/记档/未提及保留: 通过")


def test_merge_week_bump_and_birth():
    # 跨ISO周：W35→W36 周数+1（不依赖周六命令运行）
    old = [_mk_line(id=1, name="主线A", weeks=3, week_key="2026-W35")]
    lines, _ = merge_storylines(old, [], today=W36_MON)
    assert lines[0]["weeks"] == 4 and lines[0]["week_key"] == "2026-W36", "跨周应+1"
    # 无AI输入也翻转（merge即结算）
    lines2, _ = merge_storylines(old, None, today=W36_MON)
    assert lines2[0]["weeks"] == 4, "AI缺勤不影响周数翻转"
    # 新诞生：无id提案 → 分配新id，weeks=1
    ai = [{"name": "反内卷", "status": "孕育", "progress": "首提"}]
    lines3, changes3 = merge_storylines(old, ai, today=W36_MON)
    newborn = [l for l in lines3 if l["name"] == "反内卷"]
    assert len(newborn) == 1 and newborn[0]["id"] == 2 and newborn[0]["weeks"] == 1
    assert any("新主线#2" in c for c in changes3), "新诞生应记入changes"
    # 名称包含匹配（双向）
    old4 = [_mk_line(id=7, name="美债信用")]
    ai4 = [{"name": "美债信用与贬值交易", "status": "发酵", "progress": "p"}]
    lines4, _ = merge_storylines(old4, ai4, today=W35_TUE)
    assert len(lines4) == 1 and lines4[0]["id"] == 7, "包含匹配应命中而非新开线"
    print("  [3] 跨周+1/新诞生/名称包含匹配: 通过")


def test_merge_degradation_and_caps():
    old = [_mk_line(id=1, name="主线A")]
    # AI输出损坏 → 原样返回
    for bad in ("bad", 123, {"a": 1}, [{"name": "X", "status": "爆火"}]):
        lines, changes = merge_storylines(old, bad, today=W35_TUE)
        assert lines == old and changes == [], f"异常输入{bad!r}应降级保持原样"
    # 活跃线cap：已有8条，新提案只收1条后拒绝
    old8 = [_mk_line(id=i, name=f"线{i}") for i in range(1, 9)]
    ai = [{"name": "新1", "status": "孕育", "progress": "x"},
          {"name": "新2", "status": "孕育", "progress": "x"}]
    lines, changes = merge_storylines(old8, ai, today=W35_TUE)
    assert len([l for l in lines if l["status"] != "终结"]) == 8, "cap应为8"
    assert any("上限" in c for c in changes)
    print("  [4] 损坏降级/活跃线cap: 通过")


def test_merge_ending_and_cleanup():
    # 终结：ended_on记录 + changes标记
    old = [_mk_line(id=1, name="主线A")]
    ai = [{"id": 1, "name": "主线A", "status": "终结", "progress": "叙事走完"}]
    lines, changes = merge_storylines(old, ai, today=W35_TUE)
    assert lines[0]["status"] == "终结" and lines[0]["ended_on"] == W35_TUE
    assert any("终结" in c for c in changes)
    # 终结4周内保留
    lines2, _ = merge_storylines(lines, [], today="2026-09-15")
    assert len(lines2) == 1, "终结4周内应保留"
    # 超4周清理
    lines3, _ = merge_storylines(lines, [], today="2026-09-30")
    assert lines3 == [], "终结超4周应清理"
    # 终结线不因新提案复活（新提案status=终结直接忽略）
    old4 = [{"id": 1, "name": "已终", "status": "终结", "weeks": 5,
             "week_key": "2026-W35", "ended_on": W35_TUE, "log": []}]
    lines4, changes4 = merge_storylines(old4, [{"name": "全新", "status": "终结"}], today=W35_TUE)
    assert len(lines4) == 1 and lines4[0]["name"] == "已终"
    print("  [5] 终结记录/保留期/清理: 通过")


def test_format_storylines():
    lines = [_mk_line(id=1, name="主线A", status="主导", weeks=3,
                      progress="进展X",
                      log=[{"date": W35_TUE, "text": "状态: 发酵→主导"}])]
    prompt = format_storylines_prompt(lines)
    assert "主线A" in prompt and "第3周" in prompt and "进展X" in prompt
    assert "ID" in prompt, "应含表头"
    empty = format_storylines_prompt([])
    assert "无登记主线" in empty
    weekly = format_weekly_storylines(lines, WEEK_START)
    assert "发酵→主导" in weekly and "第3周" in weekly, "周报块应含本周log"
    weekly_none = format_weekly_storylines([], WEEK_START)
    assert "尚无登记主线" in weekly_none
    print("  [6] 主线prompt/周报块格式化: 通过")


def test_pattern_archive():
    A = state.STATE_DIR / "event_archive.json"
    backup = A.read_text(encoding="utf-8") if A.exists() else None
    try:
        if A.exists():
            A.unlink()
        sb = {"status": "ok", "news_marks": [
            {"title": "金价新高", "mark": "利好", "vs": "黄金+1.3%", "result": "✓"},
            {"title": "油价大跌", "mark": "利空", "vs": "原油-2.0%", "result": "❌"},
            {"title": "对不上", "mark": "利好", "result": "—未对上品种，不记分"},   # 无vs不入档
        ]}
        assert archive_pattern_events(sb) == 2, "只归档有vs/result的条目"
        bank = format_pattern_bank()
        assert "金价新高" in bank and "黄金+1.3%" in bank and "对不上" not in bank
        # 缺失/无条目降级
        assert archive_pattern_events({"status": "missing"}) == 0
        assert archive_pattern_events({"status": "ok", "news_marks": []}) == 0
        # cap=150：灌160条只留150
        state._save_json("event_archive.json",
                         [{"date": "2026-08-01", "title": f"t{i}", "mark": "利好",
                           "vs": "x", "result": "✓"} for i in range(160)])
        sb2 = {"status": "ok", "news_marks": [
            {"title": "新条", "mark": "利好", "vs": "y", "result": "部分"}]}
        archive_pattern_events(sb2)
        assert len(state._load_json("event_archive.json", [])) == 150
    finally:
        if backup is not None:
            A.write_text(backup, encoding="utf-8")
        elif A.exists():
            A.unlink()
    print("  [7] 模式锚归档/格式化/cap150: 通过")


def test_weekly_stats_and_judgments():
    # 胜率：✓=1分，部分=0.5分，其余0分
    rows = [{"date": W35_TUE, "judgment": "看偏多", "actual": "上证+0.19%", "result": "✓"},
            {"date": "2026-08-26", "judgment": "看偏多", "actual": "上证+0.02%", "result": "部分"},
            {"date": "2026-08-27", "judgment": "看多", "actual": "上证-1.2%", "result": "❌"}]
    s = weekly_judgment_stats(rows)
    assert s["total"] == 3 and s["score"] == 1.5 and s["rate"] == 50, "1+0.5+0=1.5/3=50%"
    assert weekly_judgment_stats([]) == {"total": 0, "score": 0.0, "rate": None, "rows": []}
    # 周区间过滤往返（真实judgments.csv，备份恢复）
    J = state.STATE_DIR / "judgments.csv"
    backup = J.read_text(encoding="utf-8") if J.exists() else None
    try:
        if J.exists():
            J.unlink()
        state.append_judgment({"date": WEEK_START, "judgment": "看平",
                               "actual": "上证+0.10%", "result": "✓", "detail": "周一"})
        state.append_judgment({"date": "2026-08-28", "judgment": "看偏空",
                               "actual": "上证-0.30%", "result": "部分", "detail": "周五"})
        state.append_judgment({"date": "2026-08-17", "judgment": "看多",
                               "actual": "上证+1.0%", "result": "✓", "detail": "上周不入本周边界"})
        got = state.load_judgments(WEEK_START, "2026-08-29")
        assert [r["date"] for r in got] == [WEEK_START, "2026-08-28"], "闭区间过滤，上周剔除"
        txt = format_weekly_judgments(weekly_judgment_stats(got))
        assert "胜率 **75%**" in txt and "看偏空" in txt, "1.5/2=75%"
        assert "无记分流水" in format_weekly_judgments(weekly_judgment_stats([]))
    finally:
        if backup is not None:
            J.write_text(backup, encoding="utf-8")
        elif J.exists():
            J.unlink()
    print("  [8] 周胜率/judgments区间过滤/格式化: 通过")


def test_weekly_blocks():
    # 观察点周结算（真实watchpoints.json，备份恢复）
    W = state.STATE_DIR / "watchpoints.json"
    backup = W.read_text(encoding="utf-8") if W.exists() else None
    try:
        if W.exists():
            W.unlink()
        state.save_watchpoints({
            "active": [{"key": "k2", "stock": "宁德时代", "code": "300750",
                        "kind": "财报披露", "date": "2026-08-28", "status": "⏳挂起"}],
            "history": [{"key": "k1", "stock": "贵州茅台", "code": "600519",
                         "kind": "除权除息", "date": "2026-08-25", "status": "✓兑现",
                         "resolved": W35_TUE, "evidence": "权益分派公告"},
                        {"key": "k0", "stock": "旧事", "code": "000001",
                         "kind": "财报披露", "date": "2026-08-10", "status": "✗失效",
                         "resolved": "2026-08-11", "evidence": ""}],
        })
        txt = format_weekly_watchpoints(WEEK_START)
        assert "✓兑现 贵州茅台·除权除息" in txt and "旧事" not in txt, "只结本周resolved"
        assert "仍挂起" in txt and "宁德时代" in txt
    finally:
        if backup is not None:
            W.write_text(backup, encoding="utf-8")
        elif W.exists():
            W.unlink()
    # 下次日历：自选股事件 + 美/中宏观
    s = StockInfo(code="600519", name="贵州茅台")
    s.calendar_events = [{"date": "2026-08-30", "kind": "中报披露", "text": "📅 08-30 中报披露"}]
    cal = format_next_calendar([s], [("2026-09-02", "🇺🇸非农发布")],
                               [("2026-09-09", "🇨🇳CPI/PPI（预计）")])
    assert "贵州茅台·中报披露" in cal and "非农" in cal and "CPI" in cal
    # 中国惯例窗口推算（可注入today，确定性）
    cn = cn_release_forecast(14, today="2026-09-01")
    assert [d for d, _ in cn] == ["2026-09-09", "2026-09-13"], "9/1起14天：CPI(9日)+金融(13日)"
    cn2 = cn_release_forecast(14, today="2026-09-20")
    assert [d for d, _ in cn2] == ["2026-09-30"], "9/20起14天：仅月末PMI"
    # 美国发布日历（注入数据，不联网）：窗口内命中/窗口外剔除
    from src.utils import CST
    today = datetime.now(CST)
    fake = {"非农": {"next_publish": (today + timedelta(days=5)).strftime("%Y-%m-%d")},
            "失业率": None,
            "CPI月率": {"next_publish": ""},
            "CPI年率": {"next_publish": (today + timedelta(days=30)).strftime("%Y-%m-%d")}}
    got = next_publish_calendar(14, data=fake)
    assert len(got) == 1 and "非农" in got[0][1], "只留14天窗口内的发布日"
    assert "无已登记节点" in format_next_calendar([], [], [])
    print("  [9] 观察点周结算/下周日历/中 US 日历推算: 通过")


def main():
    print("M4 冒烟测试（主线追踪+周报）")
    test_week_helpers()
    test_merge_basic()
    test_merge_week_bump_and_birth()
    test_merge_degradation_and_caps()
    test_merge_ending_and_cleanup()
    test_format_storylines()
    test_pattern_archive()
    test_weekly_stats_and_judgments()
    test_weekly_blocks()
    print("\n" + "=" * 50)
    print("✓ M4 冒烟测试全部通过")


if __name__ == "__main__":
    main()
