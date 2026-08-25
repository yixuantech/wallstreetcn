#!/usr/bin/env python3
"""M3 冒烟测试 — 分位/连续月数/三对照/落地检测/概念卡（合成数据，不联网不推送）

用法: PYTHONIOENCODING=utf-8 python tests/test_m3.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import state
from src.engines import percentile_of, streak_of, macro_compare
from src.macro_data import landed_cn, landed_us, mark_seen_cn, mark_seen_us, concept_cards, format_macro_prompt


def test_percentile():
    history = [("p1", 1.0), ("p2", 2.0), ("p3", 3.0), ("p4", 4.0)]
    assert percentile_of(2.5, history) == 50.0      # 高于2个低于1个
    assert percentile_of(5.0, history) == 100.0
    assert percentile_of(0.0, history) == 0.0
    assert percentile_of(2.0, history) == 37.5       # 低于1个+等于1个/2 = 0.375
    assert percentile_of(None, history) is None
    assert percentile_of(1.0, []) is None
    print("  [1] percentile_of 边界: 通过")


def test_streak():
    hist = [("m4", -0.5), ("m3", -0.3), ("m2", 0.2), ("m1", 0.4)]
    assert streak_of(hist, "sign") == 2              # 最近两月为负
    assert streak_of(hist[:2], "sign") == 2
    hist_pmi = [("m3", 49.2), ("m2", 50.1), ("m1", 51.0)]
    assert streak_of(hist_pmi, "above50") == 0      # 最近一月49.2<50，连续0个月扩张
    assert streak_of(hist_pmi[1:], "above50") == 2
    print("  [2] streak_of: 通过")


def test_macro_compare():
    d = {"period": "2026年07月份", "value": 3.5, "pre_value": None,
         "history": [("2026年07月份", 3.5), ("2026年06月份", 3.0), ("2026年05月份", 2.8)]}
    c = macro_compare("PPI", d, "sign")
    assert c["pre"] == 3.0 and c["vs_pre"] == "高于前值"
    assert c["percentile"] == 100.0                   # 3.5最高
    assert c["streak"] == 3
    # 美国自带pre_value优先
    d2 = {"period": "2026年08月", "value": 20, "pre_value": -23,
          "history": [("2026年08月", 20), ("2026年07月", -23), ("2026年06月", 63)]}
    c2 = macro_compare("非农", d2, "sign")
    assert c2["pre"] == -23 and c2["vs_pre"] == "高于前值"
    # 恒为正指标（失业率/CPI同比）predicate=None → 不计算连续性
    d3 = {"period": "2026年08月", "value": 4.1, "pre_value": 4.2,
          "history": [("2026年08月", 4.1), ("2026年07月", 4.2)]}
    c3 = macro_compare("失业率", d3, None)
    assert c3["streak"] is None and c3["percentile"] == 0.0
    print("  [3] macro_compare 中美口径: 通过")


def test_landed_and_concepts():
    S = state.STATE_DIR / "macro_seen.json"
    backup = S.read_text(encoding="utf-8") if S.exists() else None
    try:
        if S.exists():
            S.unlink()
        data = {"CPI": {"period": "2026年07月份", "value": 0.5, "history": []},
                "PPI": {"period": "2026年07月份", "value": 3.5, "history": []}}
        # 首次全落地
        fresh = landed_cn(data)
        assert set(fresh) == {"CPI", "PPI"}
        # 概念卡首遇
        cards = concept_cards(fresh)
        assert {k for k, _ in cards} == {"CPI", "PPI"}
        # 已见后不再落地、不再出卡
        mark_seen_cn(data, fresh)
        assert landed_cn(data) == []
        assert concept_cards(["CPI"]) == []
        # 期次更新再落地
        data["CPI"]["period"] = "2026年08月份"
        assert landed_cn(data) == ["CPI"]
        # 美国档：value=None(未发布)不落地
        us = {"非农": {"period": "2026年08月", "value": None, "pre_value": -23, "history": []}}
        assert landed_us(us) == []
        us["非农"]["value"] = 15
        assert landed_us(us) == ["非农"]
        mark_seen_us(us, ["非农"])
        assert landed_us(us) == []
        # 格式化
        from src.macro_data import CN_INDICATORS
        data["CPI"]["history"] = [("2026年07月份", 0.5), ("2026年06月份", 0.3)]
        cmps = {"CPI": macro_compare("CPI", data["CPI"], "sign")}
        txt = format_macro_prompt(["CPI"], data, cmps, CN_INDICATORS, [], ["贵州茅台"])
        assert "CPI同比 **0.5%**" in txt and "前值0.3%" in txt and "贵州茅台" in txt
        assert "不得虚构预期值" in txt
    finally:
        if backup is not None:
            S.write_text(backup, encoding="utf-8")
        elif S.exists():
            S.unlink()
    print("  [4] 落地检测/概念卡/格式化: 通过")


def main():
    print("M3 冒烟测试（数据解读）")
    test_percentile()
    test_streak()
    test_macro_compare()
    test_landed_and_concepts()
    print("\n" + "=" * 50)
    print("✓ M3 冒烟测试全部通过")


if __name__ == "__main__":
    main()
