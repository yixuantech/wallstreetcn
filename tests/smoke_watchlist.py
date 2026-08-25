#!/usr/bin/env python3
"""自选股模块冒烟测试 — 真实采集 + 标签引擎 + 温度计，不调AI不推送

用法: python tests/smoke_watchlist.py
依赖: data/watchlist.json（不存在则测试跳过路径）
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.watchlist import (collect_all, format_watchlist_prompt, label_summary,
                           load_watchlist, LABEL_GREEN, LABEL_RED, LABEL_WHITE,
                           LABEL_YELLOW, _dc)

VALID_LABELS = {LABEL_GREEN, LABEL_YELLOW, LABEL_RED, LABEL_WHITE}


def main():
    # 1. 配置读取
    items = load_watchlist()
    print(f"[1] 配置读取: {len(items)}只自选股")
    assert isinstance(items, list), "配置应为列表"

    # 2. 真实采集 + 打标签
    stocks = collect_all()
    assert len(stocks) == len(items), "采集数量应与配置一致"

    # 3. 标签合法性
    for s in stocks:
        assert s.label in VALID_LABELS, f"{s.name} 标签非法: {s.label}"
        assert s.label_reason, f"{s.name} 缺标签理由"
        print(f"\n[3] {s.name}({s.code})")
        print(f"    行情: {s.price} ({s.chg_pct}%)  流通市值: {s.float_mv}")
        print(f"    标签: {s.label} ｜ {s.label_reason}")
        print(f"    资讯{len(s.news)}条 公告{len(s.announcements)}条 日历{len(s.calendar)}条")
        if s.margin_line:
            print(f"    两融: {s.margin_line}")
        if s.research_line:
            print(f"    研报: {s.research_line[:60]}")
        print(f"    温度计: {s.thermo}")
        print(f"    失败接口: {s.failures or '无'}")

    # 4. 估值接口排序假设校验（watchlist.py 假定首条最新）
    if stocks:
        rows = _dc("RPT_VALUEANALYSIS_DET", stocks[0].code, page_size=5)
        dates = [str(r.get("TRADE_DATE", ""))[:10] for r in rows]
        print(f"\n[4] 估值接口排序校验（{stocks[0].name}）: {dates}")
        assert dates == sorted(dates, reverse=True), "接口非按日期倒序!需修正watchlist.py的latest=pes[0]假设"

    # 5. Prompt拼装 + 标题聚合
    prompt_block = format_watchlist_prompt(stocks)
    print(f"\n[5] Prompt数据块 ({len(prompt_block)}字) 预览前300字:")
    print(prompt_block[:300])
    print(f"\n[6] 标题聚合: {label_summary(stocks)}")

    # 7. 空配置跳过路径
    assert format_watchlist_prompt([]) == "", "空列表应产出空数据块"

    print("\n" + "=" * 50)
    print("✓ 冒烟测试全部通过")


if __name__ == "__main__":
    main()
