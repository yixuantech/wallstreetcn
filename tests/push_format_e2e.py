#!/usr/bin/env python3
"""全部推送场景格式端到端检查 — 渲染预检 + 真实推送（消耗推送配额）

覆盖：7 栏目形态（晨/午/晚/数据解读中美/夜巡/周报）+ 失败心跳。
每个场景先用公众号渲染器预检（块级元素齐全、无残留裸markdown），
再经 PushPlus 真实通道推送（html模板），间隔可调避开限频。

用法:
  PYTHONIOENCODING=utf-8 python tests/push_format_e2e.py           # 预检+真实推送
  PYTHONIOENCODING=utf-8 python tests/push_format_e2e.py --dry     # 仅预检不推送
  PYTHONIOENCODING=utf-8 python tests/push_format_e2e.py --gap 60  # 推送间隔秒数
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.engines import dress_report
from src.pusher import PushPlusPush
from src.report_formatter import markdown_to_wechat_html
from src.utils import today_str

R = Path("data/reports")


def _latest(pattern: str) -> str:
    files = sorted(R.glob(pattern))
    if not files:
        return ""
    return files[-1].read_text(encoding="utf-8")


def _placeholder(kind: str) -> str:
    return (f"### {kind}（占位正文）\n\n"
            f"该栏目为条件触发，尚无历史归档。本条为**骨架通道测试**：正文占位，"
            f"验证打断类尾注形态。\n\n- 列表渲染检查项一\n- 列表渲染检查项二\n")


def build_scenarios() -> list:
    """[(栏目key, 测试标题, markdown正文, 说明)]"""
    return [
        ("morning", "🧪格式·①晨报（全骨架）",
         _latest("????-??-??.md") or _placeholder("晨报"), "必读类"),
        ("noon", "🧪格式·②午间快讯（仅尾注）",
         _latest("*-noon.md") or _placeholder("午间快讯"), "打断类"),
        ("macro_cn", "🧪格式·③数据解读中国档（仅尾注）",
         _latest("*-macro-cn.md") or _placeholder("数据解读"), "打断类"),
        ("macro_us", "🧪格式·④数据解读美国档（仅尾注·占位）",
         _latest("*-macro-us.md") or _placeholder("数据解读·美国档"), "打断类"),
        ("night", "🧪格式·⑤夜巡警报（仅尾注·占位）",
         _latest("*-alert.md") or _latest("*-night.md") or _placeholder("夜巡警报"), "打断类"),
        ("weekly", "🧪格式·⑥周报（全骨架）",
         _latest("*-weekly.md") or _placeholder("周报"), "结算类"),
        ("evening", "🧪格式·⑦晚报（全骨架+今日行）",
         _latest("*-evening.md") or _placeholder("晚报"), "必读类"),
    ]


def heartbeat_scenario() -> tuple:
    """捕获真实 _push_failure_heartbeat 的正文（mock掉pusher，不重复触网），
    再以🧪演练标题真实推送——既验证真实产物格式，又不发假警报。"""
    import runner

    captured = []

    class _Cap:
        def __init__(self, *a, **k):
            pass

        def push(self, title, content, verdict=""):
            captured.append((title, content))
            return {"code": 200}

    real = runner.PushPlusPush
    runner.PushPlusPush = _Cap
    try:
        runner._push_failure_heartbeat("morning", RuntimeError("格式演练——非真实故障"))
    finally:
        runner.PushPlusPush = real
    title, body = captured[0]
    return "🧪演练｜" + title, body


def precheck(name: str, md: str) -> bool:
    """渲染预检：块级元素齐全、无残留裸markdown、块内无裸换行（会被webview吞掉）"""
    import re
    html = markdown_to_wechat_html(md, add_footer=False)
    checks = {
        "有块级元素": "<p" in html or "<h3" in html or "<li" in html,
        "无残留表格分隔线": "|---" not in html and "| ---" not in html,
        "无残留标题井号": "### " not in html,
        "块内无裸换行": re.search(r"<(?:p|li|td|h\d)[^>]*>[^<]*\n", html) is None,
    }
    ok = all(checks.values())
    flag = "✓" if ok else "✗"
    bad = [k for k, v in checks.items() if not v]
    n = {t: html.count(f"<{t}") for t in ("p", "h3", "li", "td")}
    print(f"  [{flag}] {name}: p={n['p']} h3={n['h3']} li={n['li']} td={n['td']}"
          + (f" 未过: {bad}" if bad else ""))
    return ok


def main():
    dry = "--dry" in sys.argv
    gap = 40
    if "--gap" in sys.argv:
        gap = int(sys.argv[sys.argv.index("--gap") + 1])

    scenarios = build_scenarios()
    hb_title, hb_body = heartbeat_scenario()
    scenarios.append(("heartbeat", hb_title, hb_body, "失败心跳"))

    print(f"== 渲染预检（{len(scenarios)} 场景）==")
    all_ok = True
    for key, title, body, kind in scenarios:
        all_ok &= precheck(key, body)
    if not all_ok:
        print("!! 预检未全过，终止推送（--dry 检查内容）")
        return 1
    if dry:
        print("== dry 模式：仅预检，不推送 ==")
        return 0

    print(f"== 真实推送（间隔{gap}s 避限频）==")
    pusher = PushPlusPush()
    results = []
    for i, (key, title, body, kind) in enumerate(scenarios):
        full_title = title if key == "heartbeat" else f"{title} | {today_str()}"
        res = pusher.push(full_title, body)
        code = res.get("code")
        results.append((key, code))
        print(f"  {key}: code={code}" + ("" if code == 200 else f" {res}"))
        if i < len(scenarios) - 1:
            time.sleep(gap)
    ok = sum(1 for _, c in results if c == 200)
    print(f"== {ok}/{len(results)} 推送成功 ==")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
