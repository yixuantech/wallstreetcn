#!/usr/bin/env python3
"""M1 引擎层冒烟测试 — 情绪刻度/记分牌/观察点（合成数据，不联网不推送）

用法: PYTHONIOENCODING=utf-8 python tests/test_m1.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import state
from src.engines import (sentiment_gauge, archive_sentiment, format_sentiment_prompt,
                         scoreboard, format_scoreboard_prompt, scoreboard_summary,
                         update_watchpoints, format_watchpoints_prompt, _score_direction, _match_board)
from src.watchlist import StockInfo


def test_score_direction():
    cases = [
        ("看多", +1.2, "✓"), ("看多", +0.4, "部分"), ("看多", -0.3, "部分"), ("看多", -0.8, "❌"),
        ("看偏多", +0.2, "✓"), ("看偏多", -0.3, "部分"), ("看偏多", -0.8, "❌"),
        ("看平", +0.3, "✓"), ("看平", +0.9, "❌"), ("看平", -0.4, "✓"),
        ("看偏空", -0.2, "✓"), ("看偏空", +0.1, "部分"), ("看偏空", +0.9, "❌"),
        ("看空", -1.5, "✓"), ("看空", -0.4, "部分"), ("看空", +0.2, "部分"), ("看空", +0.7, "❌"),
    ]
    for d, chg, want in cases:
        got = _score_direction(d, chg)
        assert got == want, f"{d} vs {chg:+.1f}%: 期望{want} 得{got}"
    print("  [1] _score_direction 15例: 通过")


def test_match_board():
    by_name = {"白酒Ⅲ": 2.0, "贵金属": -5.2, "半导体": 1.0}
    assert _match_board("白酒", by_name) == 2.0       # 包含匹配
    assert _match_board("贵金属", by_name) == -5.2     # 精确
    assert _match_board("房地产", by_name) is None
    print("  [2] _match_board: 通过")


def test_sentiment():
    p = {"breadth": {"up": 4089, "down": 1202},
         "limits": {"limit_up": 65, "limit_down": 2, "blown": 22},
         "turnover": {"today_yi": 18318, "chg_pct": None},
         "failures": []}
    g = sentiment_gauge(p)
    # 手算：breadth=54.5 limit=94.0 blown=49.4（vol无昨值且无归档→缺席）
    # 加权重归一(.45,.25,.15)/.85 → 0.529*54.5+0.294*94+0.176*49.4 = 65.1 → score=83
    assert g["score"] == 83 and g["label"] == "亢奋", f"得{g}"
    assert len(g["bar"]) == 20 and g["bar"].startswith("▓")
    # 全缺 → None
    g2 = sentiment_gauge({"failures": ["x"]})
    assert g2["score"] is None and g2["label"] == "数据不足"
    # 量价配合：放量+宽度负 → vol为负
    p3 = {"breadth": {"up": 1000, "down": 4000},
          "limits": {"limit_up": 5, "limit_down": 40, "blown": 10},
          "turnover": {"today_yi": 20000, "chg_pct": 25.0}}
    g3 = sentiment_gauge(p3)
    assert g3["parts"]["vol"] < 0, "宽度为负时放量应记负分（恐慌放量）"
    print(f"  [3] sentiment_gauge: 通过（今日样例={g['score']}分 刻度条{g['bar'][:8]}…）")


def test_scoreboard():
    p = {"indices": {"上证指数": {"close": 3889.44, "chg_pct": 0.19},
                     "创业板指": {"close": 3397.52, "chg_pct": -1.00}},
         "boards": {"by_name": {"白酒Ⅲ": 2.0, "贵金属": -5.2}}}
    morning = {"judgment": {
        "direction": "看偏多",
        "sectors": ["白酒", "房地产", "黄金"],
        "news_marks": [
            {"title": "茅台提价", "mark": "利好"},
            {"title": "国际金价大跌", "mark": "利空"},
            {"title": "某政策出台", "mark": "利好"},
        ]}}
    stocks = [StockInfo(code="600519", name="贵州茅台", chg_pct=2.5)]
    sb = scoreboard(morning, p, stocks)
    assert sb["status"] == "ok"
    assert sb["direction"]["result"] == "✓", "看偏多+0.19%方向兑现应为✓"
    assert sb["direction"]["actual"].startswith("上证+0.19%")
    assert sb["sectors"][0]["result"] == "✓" and sb["sectors"][0]["actual"] == "+2.00%"
    assert sb["sectors"][1]["result"] == "—未对上板块"
    assert sb["sectors"][2]["result"] == "—未对上板块", "黄金与贵金属无子串关系，匹配不上是正确行为"
    assert sb["news_marks"][0]["result"] == "✓" and sb["news_marks"][0]["vs"] == "贵州茅台+2.50%"
    assert sb["news_marks"][1]["vs"] == "贵金属-5.20%" and sb["news_marks"][1]["result"] == "✓"  # 金价同义词→贵金属跌,利空✓
    assert "不记分" in sb["news_marks"][2]["result"]
    # 缺快照
    assert scoreboard({}, p, stocks)["status"] == "missing"
    assert scoreboard({"judgment": {}}, p, stocks)["status"] == "missing"
    # 文本块
    txt = format_scoreboard_prompt(sb)
    assert "看偏多" in txt and "✓" in txt
    assert "无预判快照" in format_scoreboard_prompt(scoreboard({}, p, stocks))
    print(f"  [4] scoreboard + 文本块: 通过（{scoreboard_summary(sb)}）")


def test_watchpoints_lifecycle():
    STATE = state.STATE_DIR / "watchpoints.json"
    backup = STATE.read_text(encoding="utf-8") if STATE.exists() else None
    try:
        if STATE.exists():
            STATE.unlink()
        from src.utils import CST
        from datetime import datetime, timedelta
        today = datetime.now(CST).strftime("%Y-%m-%d")
        past = (datetime.now(CST) - timedelta(days=2)).strftime("%Y-%m-%d")

        s = StockInfo(code="600519", name="贵州茅台")
        future = (datetime.now(CST) + timedelta(days=3)).strftime("%Y-%m-%d")
        s.calendar_events = [
            {"date": today, "kind": "财报披露", "text": "今日披露"},
            {"date": future, "kind": "限售解禁", "text": "3天后解禁"},
        ]
        s.announcements = [{"title": "2026年半年度报告", "date": today, "url": "x"}]

        # 预置一条过期观察点（失效路径只作用于先前挂起后过期的，新扫描不收过期事件）
        state.save_watchpoints({"active": [
            {"key": f"600519-{past}-除权除息", "stock": "贵州茅台", "code": "600519",
             "kind": "除权除息", "date": past, "status": "⏳挂起", "created": past},
        ], "history": []})

        # 第一轮：今日节点→兑现（公告命中），预置过期→失效，未来节点→挂起
        # （到期/过期观察点首轮即结算，因为扫描和结算同轮执行）
        wp = update_watchpoints([s])
        statuses = {w["status"] for w in wp["active"]}
        assert statuses == {"⏳挂起"}, f"挂起集: {statuses}"
        assert len(wp["active"]) == 1 and wp["active"][0]["kind"] == "限售解禁"
        hist = state.load_watchpoints()["history"]
        assert {w["status"] for w in hist} == {"✓兑现", "✗失效"}
        assert any(w.get("evidence") == "2026年半年度报告"[:40] for w in hist), "财报兑现应留证据"

        # 幂等：重扫不重建
        wp2 = update_watchpoints([s])
        assert wp2["added"] == []
        assert len([w for w in wp2["active"]]) == len(wp["active"])

        # 无匹配公告的到期节点 → 到期未现
        s2 = StockInfo(code="300750", name="宁德时代")
        s2.calendar_events = [{"date": today, "kind": "除权除息", "text": "今日除权"}]
        s2.announcements = []
        wp3 = update_watchpoints([s2])
        assert any(w["status"] == "⏳到期未现(明日复核)" for w in wp3["active"])
        txt = format_watchpoints_prompt(wp3)
        assert "观察点看板" in txt and "宁德时代·除权除息" in txt
    finally:
        if backup is not None:
            STATE.write_text(backup, encoding="utf-8")
        elif STATE.exists():
            STATE.unlink()
    print("  [5] watchpoints 生命周期(兑现/失效/挂起/幂等/到期未现): 通过")


def test_sentiment_archive():
    STATE = state.STATE_DIR / "sentiment_history.json"
    backup = STATE.read_text(encoding="utf-8") if STATE.exists() else None
    try:
        if STATE.exists():
            STATE.unlink()
        p = {"breadth": {"up": 4089, "down": 1202}, "turnover": {"today_yi": 18318}}
        g = sentiment_gauge(p)
        line = archive_sentiment(g, p)
        assert "积累中（第1天" in line, line
        # 预填10天历史 → 对照生效
        hist = state._load_json("sentiment_history.json", {})
        from datetime import datetime, timedelta
        from src.utils import CST
        for i in range(1, 11):
            d = (datetime.now(CST) - timedelta(days=i)).strftime("%Y-%m-%d")
            hist[d] = {"score": 50, "turnover_yi": 18318}
        state._save_json("sentiment_history.json", hist)
        line2 = archive_sentiment(g, p)
        assert "近20日均值50" in line2 and "高于" in line2, line2
        # _vol_from_history 备源：无kline昨值时用归档昨值
        from src.engines import _vol_from_history
        assert _vol_from_history(18318) == 0.0     # 昨值相同→0%
        assert _vol_from_history(20000) == (20000 / 18318 - 1) * 100
        prompt = format_sentiment_prompt(g, line2)
        assert f"{g['score']}/100" in prompt and "▓" in prompt
    finally:
        if backup is not None:
            STATE.write_text(backup, encoding="utf-8")
        elif STATE.exists():
            STATE.unlink()
    print("  [6] 情绪归档/历史对照/量能备源: 通过")


def main():
    print("M1 引擎层冒烟测试")
    test_score_direction()
    test_match_board()
    test_sentiment()
    test_scoreboard()
    test_watchpoints_lifecycle()
    test_sentiment_archive()
    print("\n" + "=" * 50)
    print("✓ M1 引擎冒烟测试全部通过")


if __name__ == "__main__":
    main()
