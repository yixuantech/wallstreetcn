#!/usr/bin/env python3
"""驾驶舱演示构建 — 模拟"已运行两周"的丰富状态，产出 site-demo/。

真实状态全部备份→写演示数据→构建→finally恢复；真实 site/ 不受影响。
演示内容：13个交易日情绪曲线、9条记分流水(胜率72%)、10例模式库、
5条主线（含1条终结"墓志铭"）、3个挂起观察点、含🚨警报在内的11篇报告。

用法: PYTHONIOENCODING=utf-8 python tests/preview_site_demo.py [--open]
"""

import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src import state
from src.site_builder import build_site
from src.watchlist import WATCHLIST_FILE

STATE_FILES = ["storylines.json", "watchpoints.json", "sentiment_history.json",
               "judgments.csv", "event_archive.json", "label_history.json"]

# 13个交易日（两周+），情绪分手排：先抑后扬再回踩，曲线有故事
DEMO_DAYS = ["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14",
             "2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21",
             "2026-08-24", "2026-08-25", "2026-08-26"]
DEMO_SCORES = [42, 38, 51, 63, 58, 71, 66, 74, 80, 76, 83, 62, 69]


def _demo_state() -> dict:
    sent = {d: {"score": s, "up": 1500 + s * 30, "down": 3500 - s * 30,
                "turnover_yi": 14000 + s * 60}
            for d, s in zip(DEMO_DAYS, DEMO_SCORES)}

    judgments = ["date,judgment,actual,result,detail",
                 "2026-08-10,看偏多,上证-0.31%,❌,板块✓0/2",
                 "2026-08-11,看偏空,上证-0.42%,✓,情绪38偏冷",
                 "2026-08-12,看平,上证+0.07%,✓,",
                 "2026-08-13,看偏多,上证+0.94%,✓,板块✓2/2",
                 "2026-08-14,看多,上证+0.21%,部分,方向对未过兑现线",
                 "2026-08-17,看偏多,上证+0.62%,✓,",
                 "2026-08-18,看偏多,上证-0.08%,部分,",
                 "2026-08-19,看偏多,上证+0.55%,✓,情绪74偏热",
                 "2026-08-20,看多,上证+1.08%,✓,板块✓2/2",
                 "2026-08-21,看偏空,上证+0.28%,❌,"]  # 手算：6✓+1部分=6.5/9=72%

    archive = []
    samples = [("金价新高", "利好", "黄金+1.3%", "✓"), ("油价大跌", "利空", "原油-2.0%", "✓"),
               ("美联储放鹰", "利空", "贵金属-1.1%", "❌"), ("半导体出口放松", "利好", "半导体+2.4%", "✓"),
               ("地产政策传闻", "利好", "房地产+0.2%", "○平淡"), ("白酒提价", "利好", "白酒+1.8%", "✓"),
               ("新能源补贴退坡", "利空", "光伏-1.5%", "✓"), ("央行降准", "利好", "银行+0.4%", "○平淡"),
               ("算力大单", "利好", "光模块+3.2%", "✓"), ("车企价格战", "利空", "汽车-0.9%", "✓")]
    for i, (t, m, v, r) in enumerate(samples):
        archive.append({"date": DEMO_DAYS[i], "title": t, "mark": m, "vs": v, "result": r})

    storylines = {"lines": [
        {"id": 1, "name": "美债信用与「贬值交易」", "status": "主导", "weeks": 2,
         "week_key": "2026-W35", "updated": "2026-08-26", "progress": "QE预期升温，黄金续创新高",
         "log": [{"date": "2026-08-10", "text": "✨种子主线"},
                 {"date": "2026-08-11", "text": "美债拍卖平淡，关注升温"},
                 {"date": "2026-08-13", "text": "状态: 发酵→主导；拍卖遇冷确认信用担忧"},
                 {"date": "2026-08-15", "text": "美元指数破位，贬值链启动"},
                 {"date": "2026-08-19", "text": "金价新高，贬值交易自我强化"},
                 {"date": "2026-08-24", "text": "长端利率再度跳升"},
                 {"date": "2026-08-26", "text": "联储纪要偏鸽，主线继续主导"}]},
        {"id": 2, "name": "地缘政治风险升级", "status": "发酵", "weeks": 2,
         "week_key": "2026-W35", "updated": "2026-08-25",
         "progress": "海峡局势紧张，能源价格高企推升通胀预期",
         "log": [{"date": "2026-08-10", "text": "✨种子主线"},
                 {"date": "2026-08-18", "text": "油价大涨，传导链获验证"},
                 {"date": "2026-08-25", "text": "谈判反复，仍在发酵"}]},
        {"id": 3, "name": "中国稳增长与AI产业", "status": "发酵", "weeks": 2,
         "week_key": "2026-W35", "updated": "2026-08-26",
         "progress": "算力大单落地，政策以我为主",
         "log": [{"date": "2026-08-10", "text": "✨种子主线"},
                 {"date": "2026-08-24", "text": "算力订单+800亿资本开支"}]},
        {"id": 4, "name": "反内卷", "status": "孕育", "weeks": 1,
         "week_key": "2026-W35", "updated": "2026-08-26",
         "progress": "光伏行业协会再提限产，首次进入要闻",
         "log": [{"date": "2026-08-25", "text": "✨新主线（首提于要闻）"}]},
        {"id": 5, "name": "出口链修复", "status": "终结", "weeks": 3,
         "week_key": "2026-W35", "updated": "2026-08-22", "ended_on": "2026-08-22",
         "progress": "关税谈判破裂，叙事证伪",
         "log": [{"date": "2026-08-08", "text": "✨种子主线"},
                 {"date": "2026-08-15", "text": "状态: 主导→退潮"},
                 {"date": "2026-08-22", "text": "🏁终结：谈判破裂，逻辑证伪"}]}]}

    watchpoints = {"active": [
        {"key": "k1", "stock": "贵州茅台", "code": "600519", "kind": "财报披露",
         "date": "2026-08-30", "status": "⏳挂起", "created": "2026-08-24"},
        {"key": "k2", "stock": "宁德时代", "code": "300750", "kind": "除权除息",
         "date": "2026-09-02", "status": "⏳挂起", "created": "2026-08-25"},
        {"key": "k3", "stock": "宁德时代", "code": "300750", "kind": "限售解禁",
         "date": "2026-09-10", "status": "⏳挂起", "created": "2026-08-20"}],
        "history": [
            {"key": "k0", "stock": "贵州茅台", "code": "600519", "kind": "除权除息",
             "date": "2026-08-25", "status": "✓兑现", "resolved": "2026-08-25",
             "evidence": "2026年中期权益分派实施公告"}]}

    # 分诊快照：茅台以🟢为主（8-19一天🟡），宁德近两日🟡（解禁临近）
    label_hist = {}
    for i, d in enumerate(DEMO_DAYS):
        mt_y = d == "2026-08-19"
        nd_y = d in ("2026-08-25", "2026-08-26")
        label_hist[d] = [
            {"code": "600519", "name": "贵州茅台",
             "label": "🟡有一事需关注" if mt_y else "🟢无重大变化",
             "reason": "午间公告：渠道调研显示动销回暖" if mt_y else "近48h无重要资讯、无临近节点",
             "price": round(1290 + i * 1.7, 2), "chg_pct": round((i % 5 - 2) * 0.4, 2)},
            {"code": "300750", "name": "宁德时代",
             "label": "🟡有一事需关注" if nd_y else "🟢无重大变化",
             "reason": "9-02限售解禁临近（还7天）" if nd_y else "近48h无重要资讯、无临近节点",
             "price": round(220 + i * 0.9, 2), "chg_pct": round((i % 4 - 1.5) * 0.6, 2)},
        ]

    return {
        "sentiment_history.json": sent,
        "judgments.csv": "\n".join(judgments) + "\n",
        "event_archive.json": archive,
        "storylines.json": storylines,
        "watchpoints.json": watchpoints,
        "label_history.json": label_hist,
    }


def _demo_reports(tmp: Path) -> Path:
    """真实报告 + 演示期合成报告 → 临时目录"""
    rdir = tmp / "reports"
    rdir.mkdir()
    for p in (ROOT / "data" / "reports").glob("*.md"):
        if not p.stem.endswith("-preview"):
            shutil.copy(p, rdir / p.name)

    def w(name, text):
        (rdir / name).write_text(text, encoding="utf-8")

    for d in DEMO_DAYS:
        i = DEMO_DAYS.index(d)
        if d.endswith(("17", "24")):  # 周一晨报样例
            pass
        w(f"{d}.md", f"""### 🌍 全球要闻
1. **美联储纪要偏鸽**（利好）：降息预期升温，锚定{DEMO_SCORES[i]}情绪读数
2. **金价新高**（利好）：避险需求推动
3. **关税谈判反复**（利空）：出口链承压

### 📌 主线追踪
#1 美债信用与「贬值交易」｜主导·第{1 + i // 5}周——贬值交易自我强化

### 📊 隔夜市场表现
| 类别 | 品种 | 最新价 | 涨跌幅 |
|------|------|--------|--------|
| 美股 | 纳斯达克 | {20100 + i * 37}.4 | {0.3 + i * 0.05:+.2f}% |
| 商品 | 黄金 | {4000 + i * 67}.26 | +1.55% |

### 🎯 今日A股预判
看偏多，关注黄金、AI算力。

- [相关资讯样例](http://finance.eastmoney.com/a/demo{i}.html)
""")
        w(f"{d}-evening.md", f"""### 📊 今日复盘
- 上证收于{3880 + i * 3}.44（{DEMO_SCORES[i] // 100 + 0.2:+.2f}%），量能1.5万亿

### 🌡️ 情绪刻度
**今日读数：{DEMO_SCORES[i]}/100**

### 🧾 记分牌复盘
- 方向预判「看偏多」 → **✓**

### 🧭 明日观察
- 量能能否维持1.5万亿
""")
    w("2026-08-19-noon.md", """### ⚡ 午间快讯
### 贵州茅台(600519) ｜ 现价1310.5(+1.20%) ｜ 🟡有一事需关注
- [午间公告样例](http://example.com/noon)
💬 异动：渠道调研显示动销回暖
""")
    w("2026-08-20-night.md", """### 🚨 某公司(000000) — 命中「减持」
- [减持公告样例](http://example.com/alert)
⚠️ 严重度：中。明早盯公告原文。
""")
    w("2026-08-20-macro-cn.md", """### 📅 今日落地
- 7月经济数据

### 📊 逐项解读
- 社零同比2.1%，低于前值

### 🧩 和你的自选股有什么关系
- 白酒消费与社零正相关
""")
    w("2026-08-22-weekly.md", """### 📌 主线周演进
#1 美债贬值交易 发酵→主导；#5 出口链修复 🏁终结

### ✅ 记分周汇总
- 本周胜率 **75%**（3/4）

### 🔍 观察点结算
- ✓兑现：茅台·除权除息

### 📅 下周日历
- 08-30 茅台中报披露
""")
    return rdir


def main():
    open_browser = "--open" in sys.argv
    print("驾驶舱演示构建（模拟已运行两周，不污染真实状态）")
    print("=" * 60)

    backups = {f: (state.STATE_DIR / f).read_text(encoding="utf-8")
               if (state.STATE_DIR / f).exists() else None for f in STATE_FILES}
    try:
        demo = _demo_state()
        for name, data in demo.items():
            path = state.STATE_DIR / name
            if isinstance(data, (dict, list)):
                import json
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                path.write_text(data, encoding="utf-8")
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            reports = _demo_reports(Path(td))
            result = build_site(reports, ROOT / "site-demo")
    finally:
        for name, text in backups.items():
            path = state.STATE_DIR / name
            if text is not None:
                path.write_text(text, encoding="utf-8")
            elif path.exists():
                path.unlink()
    print(f"[Demo] 已收录 {result['reports']} 篇报告（真实+合成）")
    print("[Demo] 真实状态已恢复原样（storylines/judgments等不受影响）")
    out = ROOT / "site-demo" / "index.html"
    print(f"[Demo] 查看: {out}")
    if open_browser:
        import subprocess
        subprocess.run(["cmd", "/c", "start", "", str(out)], check=False)


if __name__ == "__main__":
    main()
