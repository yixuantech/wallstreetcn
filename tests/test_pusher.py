#!/usr/bin/env python3
"""Pusher 冒烟测试 — html模板/md→HTML转换/重试不炸（mock网络，不联网）

用法: PYTHONIOENCODING=utf-8 python tests/test_pusher.py
背景：PushPlus markdown 模板不认 \n 换行（官方要求 <br/>），曾导致全部推送
段落/表格粘连——本测试锁定"必须走 html 模板 + HTML渲染"的修复。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.pusher import PushPlusPush


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_push_uses_html_template():
    """push() 必须发送 html 模板 + 渲染后的 HTML（换行修复不被回退）"""
    captured = {}

    def _fake_post(url, json=None, timeout=None):
        captured.update(json or {})
        return _FakeResp({"code": 200, "msg": "ok"})

    import src.pusher as pusher_mod
    real_post = pusher_mod.requests.post
    pusher_mod.requests.post = _fake_post
    try:
        p = PushPlusPush(token="test-token")
        md = ("### 📊 标题\n\n| 指数 | 涨跌 |\n|------|------|\n| 上证 | +0.2% |\n\n"
              "**【今日速览】**\n\n🛡️ 哨兵：无事\n\n- 第一条\n- 第二条\n")
        result = p.push("测试标题", md)
        assert result.get("code") == 200
        assert captured["template"] == "html", captured.get("template")
        assert captured["title"] == "测试标题"
        content = captured["content"]
        # 结构化换行：段落/表头/列表全部成为块级元素，不依赖 \n
        assert "<h3" in content and "<td" in content and "<li" in content, content[:200]
        assert "🛡️ 哨兵：无事" in content
        assert "<script>" not in content
        print("✓ html模板 + md→HTML块级渲染（段落/表格/列表）")
    finally:
        pusher_mod.requests.post = real_post


def test_push_retry_degrades():
    """网络连续失败 → 返回 code=-1 不抛异常（失败心跳路径依赖此行为）"""
    import src.pusher as pusher_mod
    real_post, real_sleep = pusher_mod.requests.post, pusher_mod.time.sleep
    pusher_mod.requests.post = lambda *a, **k: (_ for _ in ()).throw(
        pusher_mod.requests.RequestException("网络不通"))
    pusher_mod.time.sleep = lambda s: None
    try:
        result = PushPlusPush(token="t").push("标题", "正文")
        assert result.get("code") == -1
        print("✓ 网络失败降级返回（不抛异常，可被失败心跳捕获）")
    finally:
        pusher_mod.requests.post = real_post
        pusher_mod.time.sleep = real_sleep


if __name__ == "__main__":
    test_push_uses_html_template()
    test_push_retry_degrades()
    print("\n✓ Pusher 冒烟测试全部通过")
