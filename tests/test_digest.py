#!/usr/bin/env python3
"""推送骨架冒烟测试 — 四角色速览/账本刻度/骨架拼装/失败心跳（合成数据，不联网）

用法: PYTHONIOENCODING=utf-8 python tests/test_digest.py
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import state
from src.engines import (three_question_digest, ledger_snapshot, dress_report,
                         digest_delta_line)
from src.utils import CST

# 真实状态文件备份清单（速览/刻度读取，测试需隔离）
STATE_FILES = ["storylines.json", "watchpoints.json", "sentiment_history.json",
               "judgments.csv", "event_archive.json", "label_history.json"]


def _backup_all():
    return {f: (state.STATE_DIR / f).read_text(encoding="utf-8")
            if (state.STATE_DIR / f).exists() else None for f in STATE_FILES}


def _restore_all(backups):
    for name, text in backups.items():
        path = state.STATE_DIR / name
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


def _mk_fixtures():
    """合成状态：今日=运行日（动态），前日=固定旧日期。"""
    today = datetime.now(CST).strftime("%Y-%m-%d")
    prev = "2026-08-20"
    _write_state("label_history.json", json.dumps({
        prev: [{"code": "600519", "name": "贵州茅台", "label": "🟢无事",
                "reason": "", "price": 1300, "chg_pct": 0.5},
               {"code": "300750", "name": "宁德时代", "label": "🟡有一事需关注",
                "reason": "业绩预告", "price": 210, "chg_pct": -1.2}],
        today: [{"code": "600519", "name": "贵州茅台", "label": "🟢无事",
                 "reason": "", "price": 1310, "chg_pct": 0.7},
                {"code": "300750", "name": "宁德时代", "label": "🟢无事",
                 "reason": "", "price": 215, "chg_pct": 2.4}],
    }, ensure_ascii=False))
    _write_state("storylines.json", json.dumps({"lines": [
        {"id": 1, "name": "存储扩产", "status": "主导", "weeks": 4,
         "log": [{"date": today, "text": "状态: 发酵→主导"}]},
        {"id": 2, "name": "油运", "status": "发酵", "weeks": 2, "log": []},
    ]}, ensure_ascii=False))
    _write_state("sentiment_history.json", json.dumps({
        prev: {"score": 62}, today: {"score": 69},
    }))
    _write_state("judgments.csv",
                 "date,judgment,actual,result,detail\n"
                 "2026-08-18,看多,+1.2%,✓,板块✓2/3；情绪65\n"
                 "2026-08-19,看平,+0.3%,部分,板块✓1/3；情绪62\n"
                 f"{today},看多,+0.8%,✓,板块✓2/3；情绪69\n")
    _write_state("watchpoints.json", json.dumps({
        "active": [{"stock": "宁德时代", "kind": "业绩预告", "date": "2026-09-01",
                    "status": "⏳", "evidence": ""}],
        "history": [],
    }, ensure_ascii=False))
    _write_state("event_archive.json", json.dumps([
        {"date": "2026-08-18", "mark": "看多", "result": "✓兑现"},
        {"date": today, "mark": "看多", "result": "⏳待验证"},
    ], ensure_ascii=False))
    return today


def test_digest_full():
    """有完整状态：四行速览+今日变化行+账本刻度，数值全部可复算。"""
    backups = _backup_all()
    try:
        today = _mk_fixtures()
        # 胜率：✓+部分+✓ = (1+0.5+1)/3 = 83%
        head = three_question_digest(with_delta=True)
        assert "【今日速览】" in head
        assert f"🛡️ 哨兵：✅ 2只自选巡检通过，无事" in head, head
        assert "🗣️ 主线：存储扩产·主导第4周 ｜ 油运·发酵第2周" in head, head
        assert "🌡️ 温度：69 偏热（较昨+7）" in head, head
        assert "🧾 账本：胜率83%（累计3条）" in head, head
        assert "📅 今日：分诊1只变动 · 主线新动态×1 · +2新账" in head, head
        # 无 delta 参数时不带今日行
        assert "📅 今日" not in three_question_digest()

        foot = ledger_snapshot("morning")
        assert "📌 **账本刻度**：胜率 83%（累计3条）· 主线 2 · 模式库 2例 · 观察点 1" in foot, foot
        assert "✓=1分，部分=0.5分" in foot, foot
        assert "⏰ 下一班：午间快讯 11:35" in foot, foot

        # 必读类=全骨架；打断类=仅尾注；晚报多「今日」行
        m = dress_report("morning", "正文ABC")
        assert m.index("【今日速览】") < m.index("正文ABC") < m.index("账本刻度")
        assert "📅 今日" not in m
        e = dress_report("evening", "正文ABC")
        assert "📅 今日" in e and "下一班：夜巡 20:30" in e
        n = dress_report("noon", "正文ABC")
        assert "【今日速览】" not in n and "账本刻度" in n and "下一班：晚报 17:30" in n
        w = dress_report("weekly", "正文ABC")
        assert "【今日速览】" in w and "下一班：下个工作日晨报" in w
        print("✓ 速览/刻度/骨架拼装（必读全骨架·打断仅尾注）")
    finally:
        _restore_all(backups)


def test_digest_empty():
    """空状态：每行诚实降级，不编数字。"""
    backups = _backup_all()
    try:
        for f in STATE_FILES:
            _write_state(f, None)
        head = three_question_digest()
        assert "尚无分诊快照" in head, head
        assert "暂无连载中的主线" in head, head
        assert "温度：积累中" in head, head
        assert "待开账" in head, head
        foot = ledger_snapshot("night")
        assert "胜率 待开账（累计0条）" in foot and "主线 0" in foot, foot
        assert dress_report("noon", "正文").startswith("正文"), "打断类空态不应带头部"
        print("✓ 空态诚实降级（尚无快照/积累中/待开账）")
    finally:
        _restore_all(backups)


def test_failure_heartbeat():
    """推送班次异常 → 失败心跳推送（标题带班次名/正文带下一班），退出码1。"""
    import runner

    sent = []

    class _FakePusher:
        def __init__(self, *a, **k):
            pass

        def push(self, title, content, verdict=""):
            sent.append((title, content))
            return {"code": 200}

    real_pusher, real_cmd = runner.PushPlusPush, runner.COMMANDS["morning"]

    def _boom():
        raise RuntimeError("LLM接口超时")

    try:
        runner.PushPlusPush = _FakePusher
        runner.COMMANDS["morning"] = _boom
        code = runner.run("morning")
        assert code == 1, code
        assert len(sent) == 1, sent
        title, content = sent[0]
        assert "晨报生成失败" in title, title
        assert "下一班：午间快讯" in content, content
        assert "RuntimeError" in content and "LLM接口超时" in content, content

        # 非推送命令失败 → run 不捕获（原样抛出给调用者看堆栈），且不发心跳
        sent.clear()
        runner.COMMANDS["site"] = _boom
        try:
            runner.run("site")
            raise AssertionError("非推送命令异常应原样抛出")
        except RuntimeError:
            pass
        assert len(sent) == 0, sent
    finally:
        runner.PushPlusPush = real_pusher
        runner.COMMANDS["morning"] = real_cmd
        del runner.COMMANDS["site"]
    print("✓ 失败心跳（班次名/原因/下一班；非推送命令不打扰）")


if __name__ == "__main__":
    test_digest_full()
    test_digest_empty()
    test_failure_heartbeat()
    print("\n✓ 推送骨架冒烟测试全部通过")
