#!/usr/bin/env python3
"""M3 预览：数据解读 中国档+美国档（真实采集 + 真实AI，绝不推送、绝不落地登记）

macro_seen.json 先备份、跑完恢复 —— 概念卡登记不提前消耗，首跑仍算首秀。

用法: NO_PROXY='*' no_proxy='*' PYTHONIOENCODING=utf-8 python tests/preview_macro.py
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.analyzer import analyze_macro
from src.engines import macro_compare
from src.macro_data import (collect_cn, collect_us, landed_cn, landed_us,
                            concept_cards, format_macro_prompt,
                            CN_INDICATORS, US_INDICATORS)
from src import state
from src.utils import CST

SEEN = state.STATE_DIR / "macro_seen.json"


def _preview(region: str, edition: str):
    if region == "cn":
        data = collect_cn()
        fresh = landed_cn(data)
        definitions = CN_INDICATORS
    else:
        data = collect_us()
        fresh = landed_us(data)
        definitions = US_INDICATORS
    if not fresh:
        print(f"[Preview] {edition}：本批无落地（静默路径）")
        return

    compares = {k: macro_compare(k, data[k], definitions[k].get("predicate"))
                for k in fresh if data.get(k)}
    concepts = concept_cards(fresh)
    from src.watchlist import load_watchlist
    watch_names = [w.get("name", "") for w in load_watchlist()]
    blocks = format_macro_prompt(fresh, data, compares, definitions, concepts, watch_names)

    print(f"\n{'='*60}\n[Preview] 数据块（{edition}，落地 {fresh}）：\n{blocks}\n{'='*60}\n")
    report = analyze_macro(edition, blocks)

    date_str = datetime.now(CST).strftime("%Y-%m-%d")
    path = Path("data/reports") / f"{date_str}-macro-{region}-preview.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    print(f"[Preview] {edition} 已保存: {path}（未推送）")


def main():
    backup = SEEN.read_text(encoding="utf-8") if SEEN.exists() else None
    try:
        _preview("cn", "中国档")
        _preview("us", "美国档")
    finally:
        # 恢复 macro_seen：预览不算数，真实首跑仍是首秀
        if backup is not None:
            SEEN.write_text(backup, encoding="utf-8")
        elif SEEN.exists():
            SEEN.unlink()
    print("\n[Preview] macro_seen.json 已恢复原状，完成")


if __name__ == "__main__":
    main()
