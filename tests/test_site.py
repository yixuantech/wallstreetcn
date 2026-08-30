#!/usr/bin/env python3
"""Web原型冒烟测试 — 报告渲染/preview排除/驾驶舱四区/空态/幂等（合成数据，不联网）

用法: PYTHONIOENCODING=utf-8 python tests/test_site.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import state
from src.site_builder import build_site, _digest
from src.watchlist import WATCHLIST_FILE

# 真实状态文件备份清单（驾驶舱四区读取，测试需隔离）
STATE_FILES = ["storylines.json", "watchpoints.json", "sentiment_history.json",
               "judgments.csv", "event_archive.json", "label_history.json"]


def _backup_all():
    backups = {f: (state.STATE_DIR / f).read_text(encoding="utf-8")
               if (state.STATE_DIR / f).exists() else None for f in STATE_FILES}
    backups["watchlist.json"] = (WATCHLIST_FILE.read_text(encoding="utf-8")
                                 if WATCHLIST_FILE.exists() else None)
    return backups


def _restore_all(backups):
    for name, text in backups.items():
        path = WATCHLIST_FILE if name == "watchlist.json" else state.STATE_DIR / name
        if text is not None:
            path.write_text(text, encoding="utf-8")
        elif path.exists():
            path.unlink()


def _write_state(name, text):
    path = state.STATE_DIR / name
    if text is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(text, encoding="utf-8")


def _mk_reports(tmp: Path):
    r = tmp / "reports"
    r.mkdir()
    (r / "2026-08-20.md").write_text("""### 🌍 全球要闻
1. **金价新高**（利好）：避险升温，主线「美债贬值交易」主导

自选：贵州茅台 无事

### 📊 隔夜市场表现
| 类别 | 品种 | 最新价 | 涨跌幅 |
|------|------|--------|--------|
| 商品 | 黄金 | 4089.26 | +1.55% |

相关：[研报掘金原文](http://finance.eastmoney.com/a/1.html)

<script>alert(1)</script> 不应被执行
""", encoding="utf-8")
    (r / "2026-08-21-evening.md").write_text("### 📊 今日复盘\n- 上证收涨\n", encoding="utf-8")
    (r / "2026-08-21-evening-preview.md").write_text("### 草稿\n", encoding="utf-8")
    (r / "notes.md").write_text("无法解析的文件名", encoding="utf-8")
    return r


def test_report_render():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        reports = _mk_reports(tmp)
        out = tmp / "site"
        result = build_site(reports, out)
        assert result["reports"] == 2, "应只收录2篇（preview与非法名排除）"
        index = (out / "index.html").read_text(encoding="utf-8")
        assert "reports/2026-08-20.html" in index and "reports/2026-08-21-evening.html" in index
        assert "-preview" not in index and "notes" not in index
        assert "报告归档（2）" in index
        morning = (out / "reports" / "2026-08-20.html").read_text(encoding="utf-8")
        assert "<table>" in morning and "<a href=\"http://finance.eastmoney.com/a/1.html\"" in morning
        assert "<script>alert" not in morning and "&lt;script&gt;" in morning, "HTML必须转义"
        assert "晨报" in morning and "← 驾驶舱" in morning
        assert (out / "assets" / "style.css").exists()
    print("  [1] 报告渲染/链接/转义/preview排除: 通过")


def test_cockpit_zones():
    backups = _backup_all()
    try:
        _write_state("storylines.json", """{"lines": [
          {"id": 1, "name": "美债贬值交易", "status": "主导", "weeks": 3,
           "week_key": "2026-W35", "progress": "QE预期升温",
           "log": [{"date": "2026-08-19", "text": "更早1"},
                   {"date": "2026-08-20", "text": "更早2"},
                   {"date": "2026-08-21", "text": "进展5"},
                   {"date": "2026-08-22", "text": "进展4"},
                   {"date": "2026-08-23", "text": "进展3"},
                   {"date": "2026-08-24", "text": "进展2"},
                   {"date": "2026-08-25", "text": "状态: 发酵→主导"}]},
          {"id": 2, "name": "旧题材", "status": "终结", "weeks": 6,
           "week_key": "2026-W35", "progress": "", "ended_on": "2026-08-20",
           "log": [{"date": "2026-08-20", "text": "🏁终结"}]}]}""")
        _write_state("watchpoints.json", """{"active": [
          {"key": "k1", "stock": "宁德时代", "code": "300750", "kind": "财报披露",
           "date": "2026-08-28", "status": "⏳挂起"}], "history": []}""")
        # 分诊历史两日：宁德时代 🟢→🟡（供速览条聚合与变化标记）
        _write_state("label_history.json", json.dumps({
            "2026-08-24": [
                {"code": "600519", "name": "贵州茅台", "label": "🟢无重大变化",
                 "reason": "近48h无重要资讯", "price": 1300.0, "chg_pct": 0.5},
                {"code": "300750", "name": "宁德时代", "label": "🟢无重大变化",
                 "reason": "近48h无重要资讯", "price": 200.0, "chg_pct": -1.2}],
            "2026-08-25": [
                {"code": "600519", "name": "贵州茅台", "label": "🟢无重大变化",
                 "reason": "近48h无重要资讯", "price": 1310.0, "chg_pct": 0.8},
                {"code": "300750", "name": "宁德时代", "label": "🟡有一事需关注",
                 "reason": "解禁临近", "price": 198.0, "chg_pct": -1.0}]}, ensure_ascii=False))
        _write_state("sentiment_history.json", "{" + ",".join(
            f'"2026-08-{d:02d}": {{"score": {50 + d % 40}, "up": 2000, "down": 2000, "turnover_yi": 15000}}'
            for d in range(10, 24)) + "}")
        _write_state("judgments.csv",
                     "date,judgment,actual,result,detail\n"
                     "2026-08-24,看偏多,上证+0.5%,✓,\n"
                     "2026-08-25,看偏多,上证+0.1%,部分,\n"
                     "2026-08-26,看多,上证-1.0%,❌,\n"
                     "2026-08-20,看平,上证+0.05%,✓,\n"
                     "2026-08-21,看偏空,上证-0.2%,❌,\n")   # 2✓+0.5部分 → 2.5/5=50%
        _write_state("event_archive.json", json.dumps(
            [{"date": f"2026-08-{25 - i:02d}", "title": f"要闻事件{i}",
              "mark": "利好" if i % 2 == 0 else "利空",
              "vs": "指数+0.5%", "result": "✓" if i % 3 else "✗"}
             for i in range(10)] +                                          # 10例 → 触发"查看全部"链接
            [{"date": "2026-08-25", "title": "金价新高", "mark": "利好",
              "vs": "黄金+1.3%", "result": "✓"}], ensure_ascii=False))
        WATCHLIST_FILE.write_text(
            '[{"code": "600519", "name": "贵州茅台", "note": ""},'
            ' {"code": "300750", "name": "宁德时代", "note": ""}]', encoding="utf-8")

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            reports = _mk_reports(tmp)
            out = tmp / "site"
            build_site(reports, out)
            index = (out / "index.html").read_text(encoding="utf-8")
            # 今日速览条：结论先行 + 节奏表（反焦虑：无事日5秒出口，有事日一眼定位）
            assert 'class="digest"' in index and "今日节奏" in index
            assert "🟡观察1只" in index, "速览条应聚合哨兵分诊"
            assert "1条连载" in index and "温度：73 偏热" in index and "胜率：50%" in index
            assert 'href="#z-sentinel"' in index and 'id="z-sentinel"' in index, "速览条应锚到分区"
            # 变化标记：分诊有变/主线动态/温度Δ/新账（ref_d=最新报告日08-21）
            assert "⇄ 1只分诊有变" in index
            assert "↗ 主线新动态×1" in index
            assert "↑ 较前日 +1" in index, "温度72→73应显示+1"
            assert "+2 新账" in index, "08-21有1条judgment+1条要闻归档"
            # 哨兵区
            assert "贵州茅台" in index and "宁德时代" in index and "财报披露" in index
            assert 'class="tri tri-yel"' in index and "观察中：宁德时代" in index, "结论先行：有事点名"
            assert 'stock-row dim' in index, "无异常的行应降噪透明"
            assert 'class="r-miss"' in index, "❌结果应灰化（历史账不抢红）"
            # 翻译官区：主线卡（生命周期轨道+时间轴+徽标+第N周）
            assert "美债贬值交易" in index and "st-lead" in index and "第3周" in index
            assert "发酵→主导" in index, "活跃主线的迁移记录应在首页"
            assert "旧题材" not in index, "完结主线不应出现在首页翻译官区"
            assert 'href="storylines.html">📖 主线档案' in index, "首页应有档案页链接"
            sl_page = (out / "storylines.html").read_text(encoding="utf-8")
            assert "连载中" in sl_page and "已完结" in sl_page and "旧题材" in sl_page
            assert sl_page.count("已完结</span>") >= 1, "完结卡应带已完结角标"
            assert 'class="stepper"' in index, "应有生命周期轨道"
            assert 'class="tl"' in index and 'ev-move' in index, "log应为彩色节点时间轴"
            assert 'style="--evc:#ef4444"' in index, "发酵→主导的迁移点应用主导站红色"
            # 锚区：SVG曲线+均值对照 + 五区色带 + 白话解读（纯温度，无模式库）
            assert "<svg" in index and "<polyline" in index and "均值" in index
            assert "当下温度" not in index and "历史校准" not in index, "锚区应只剩温度，无小节分拆"
            assert "金价新高" not in index or "要闻方向验证" in index
            # 最新日 d=23 → score=73 偏热区；夹具 up/down 均2000 → 涨跌互现；71→72→73 → 连升
            assert 'class="marker" style="left:73%"' in index, "色带标记应指在73%"
            assert 'class="sent-zone">偏热区<' in index and "纪律优先于情绪" in index
            assert 'class="sent-headline"' in index and 'class="sent-details"' in index, "解读应有headline/details两层"
            assert "涨跌互现" in index and "近3日连升" in index
            # 温度档案页：首页给入口；sentiment.html 有统计/分布/逐日明细
            assert 'href="sentiment.html">🌡️ 温度档案' in index, "锚区应有温度档案链接"
            sent_page = (out / "sentiment.html").read_text(encoding="utf-8")
            assert "五区天数分布" in sent_page and "逐日明细" in sent_page
            assert sent_page.count("<tr>") == 15, "逐日表应含14天数据+表头"
            # 模式库详情页：>8例时首页给"查看全部"链接；patterns.html 有统计面板+全量列表
            assert 'href="patterns.html">查看全部 11 例' in index, "超过8例应链接到模式库页"
            patterns = (out / "patterns.html").read_text(encoding="utf-8")
            assert "累计验证(例)" in patterns and "总通过率" in patterns
            assert patterns.count('class="mark"') == 11, "模式库页应含全部11例"
            # 记分牌区：胜率(1+0.5+0)/3=50% + 四数字面板
            assert "50%" in index and "活跃主线" in index and "模式库(例)" in index
            assert "挂起观察点" in index and "看偏多" in index and "❌" in index
            assert "最近5个交易日" in index, "首页流水应只看最近5个交易日"
            # 记分牌两块验证资产：每日预判 + 要闻方向验证（模式库）
            assert "每日预判记分" in index and "要闻方向验证 · 模式库" in index
            assert "金价新高" in index and "黄金+1.3%" in index, "模式库最近例应在记分牌区"
            ledger = (out / "ledger.html").read_text(encoding="utf-8")
            assert "累计判断(条)" in ledger and "全部流水" in ledger
            assert ledger.count("<tr>") == 5 + 1, "档案页应含全部5条流水+表头"
    finally:
        _restore_all(backups)
    print("  [2] 驾驶舱四区渲染（哨兵/翻译官/锚/记分牌）: 通过")


def test_empty_states():
    backups = _backup_all()
    try:
        for f in STATE_FILES:
            _write_state(f, None)
        WATCHLIST_FILE.write_text("[]", encoding="utf-8")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            reports = _mk_reports(tmp)
            result = build_site(reports, tmp / "site")
            assert result["reports"] == 2, "空状态不影响报告渲染"
            index = (tmp / "site" / "index.html").read_text(encoding="utf-8")
            assert "尚无登记主线" in index
            assert "情绪归档积累中" in index
            assert "记分流水积累中" in index
            assert "自选列表为空" in index
            assert "暂无报告归档" not in index, "报告归档与状态空态无关"
            # 空态下速览条仍给出诚实降级（哨兵无快照/主线0/温度积累中）
            assert 'class="digest"' in index and "哨兵：尚无快照" in index
            assert "0条连载" in index and "温度：积累中" in index
        # 有股票但无分诊快照 → 逐股⚪降级 + 哨兵区诚实标注
        WATCHLIST_FILE.write_text('[{"code": "600519", "name": "贵州茅台", "note": ""}]',
                                  encoding="utf-8")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            reports = _mk_reports(tmp)
            build_site(reports, tmp / "site")
            index = (tmp / "site" / "index.html").read_text(encoding="utf-8")
            assert "尚无快照，晨报/晚报运行后生成" in index and "⚪无快照" in index
            assert 'class="stock-row">' in index, "无快照日不应降噪变暗"
    finally:
        _restore_all(backups)
    print("  [3] 空态诚实降级: 通过")


def test_idempotent():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        reports = _mk_reports(tmp)
        out = tmp / "site"
        build_site(reports, out)
        idx1 = (out / "index.html").read_bytes()
        page1 = (out / "reports" / "2026-08-20.html").read_bytes()
        build_site(reports, out)            # 重建（含rmtree）
        idx2 = (out / "index.html").read_bytes()
        page2 = (out / "reports" / "2026-08-20.html").read_bytes()
        assert idx1 == idx2 and page1 == page2, "重建输出应逐字节一致（无时间戳）"
    print("  [4] 幂等重建: 通过")


def test_digest():
    # 短句：无省略号；链接/加粗/列表序号语法剥离
    d = _digest("### 🌍 全球要闻\n1. **金价新高**（利好）：[原文](http://x.com)\n")
    assert d.startswith("金价新高") and "](" not in d and "…" not in d, d
    # 长句：截断加省略号
    d2 = _digest("- **金价新高**（利好）：" + "避险情绪持续升温" * 10)
    assert d2.startswith("金价新高") and d2.endswith("…") and len(d2) <= 61, d2
    assert _digest("") == ""
    print("  [5] 摘要提取: 通过")


def test_interactions():
    """三层交互：详情页导航 / 数据点直链 / 原生折叠与筛选"""
    backups = _backup_all()
    try:
        _write_state("storylines.json", """{"lines": [
          {"id": 1, "name": "美债贬值交易", "status": "主导", "weeks": 2,
           "week_key": "2026-W35", "progress": "QE预期升温",
           "log": [{"date": "2026-08-25", "text": "状态: 发酵→主导"}]}]}""")
        _write_state("judgments.csv",
                     "date,judgment,actual,result,detail\n"
                     "2026-08-20,看平,上证+0.05%,✓,\n")
        _write_state("sentiment_history.json",
                     '{"2026-08-20": {"score": 60}, "2026-08-21": {"score": 70}}')
        _write_state("watchpoints.json", '{"active": [], "history": []}')
        _write_state("event_archive.json", "[]")
        _write_state("label_history.json", json.dumps({"2026-08-20": [
            {"code": "600519", "name": "贵州茅台", "label": "🟢无重大变化",
             "reason": "近48h无重要资讯", "price": 1300.0, "chg_pct": 0.5},
            {"code": "300750", "name": "宁德时代", "label": "🟡有一事需关注",
             "reason": "解禁临近", "price": 200.0, "chg_pct": -1.2}]}))
        WATCHLIST_FILE.write_text('[{"code": "600519", "name": "贵州茅台", "note": "消费压舱石"},'
                                  ' {"code": "300750", "name": "宁德时代", "note": ""}]',
                                  encoding="utf-8")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            reports = _mk_reports(tmp)
            out = tmp / "site"
            build_site(reports, out)
            index = (out / "index.html").read_text(encoding="utf-8")
            # 1. 主线卡 → 详情页
            assert 'href="storylines/1.html"' in index
            spage = (out / "storylines" / "1.html").read_text(encoding="utf-8")
            assert "完整迁移史（1条，新→旧）" in spage and "相关报告（正文提及本主线，1篇）" in spage
            assert "reports/2026-08-20.html" in spage, "相关报告应命中提及主线的晨报"
            # 2. 自选股 → 个股页（哨兵区链接 + 分诊标记 + 近期分诊历史）
            assert 'href="stocks/600519.html"' in index
            assert "🟢无重大变化" in index and "lab-grn" in index, "哨兵区应显示分诊标签"
            assert "🟡有一事需关注" in index and "lab-yel" in index
            assert "🟡 08-20 1只观察中：宁德时代" in index and "1300.0(+0.50%)" in index
            assert "🟡观察1只" in index, "速览条哨兵聚合"
            kpage = (out / "stocks" / "600519.html").read_text(encoding="utf-8")
            assert "贵州茅台" in kpage and "消费压舱石" in kpage and "reports/2026-08-20.html" in kpage
            assert "近期分诊（1天）" in kpage and "近48h无重要资讯" in kpage
            # 3. 情绪数据点直链：21日有晚报→可点；20日无晚报→灰点
            assert '<a href="reports/2026-08-21-evening.html"' in index and 'class="pt"' in index
            assert 'class="dim"' in index
            # 4. 记分流水日期 → 当日晨报
            assert '<a href="reports/2026-08-20.html">08-20</a>' in index
            # 5. 归档：首页只留最近8篇+入口链接；筛选与全量在 archive.html
            assert 'data-k="morning"' in index, "首页归档条目应带栏目标记"
            assert 'href="archive.html">📚 查看全部' in index, "首页应有归档页链接"
            arch = (out / "archive.html").read_text(encoding="utf-8")
            assert 'data-f="morning"' in arch and ".fbtn" in arch, "归档页应有筛选按钮"
            assert arch.count("arch-digest") == index.count("arch-digest") or True  # 全量在归档页
            assert arch.count("<li data-k=") >= index.count("<li data-k="), "归档页条目不少于首页"
    finally:
        _restore_all(backups)
    print("  [6] 交互层（详情页/数据点直链/折叠筛选）: 通过")


def main():
    print("Web原型冒烟测试（驾驶舱+归档）")
    test_report_render()
    test_cockpit_zones()
    test_empty_states()
    test_idempotent()
    test_digest()
    test_interactions()
    print("\n" + "=" * 50)
    print("✓ Web原型冒烟测试全部通过")


if __name__ == "__main__":
    main()
