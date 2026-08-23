"""推送模块 — PushPlus微信服务号推送"""

import time
import requests

from src.config import Config


class PushPlusPush:
    """PushPlus微信推送"""

    def __init__(self, token: str = ""):
        self.token = token or Config.PUSHPLUS_TOKEN
        self.url = "https://www.pushplus.plus/send"

    def push(self, title: str, content: str, verdict: str = "") -> dict:
        """发送PushPlus消息

        Args:
            title: 消息标题
            content: Markdown格式内容
            verdict: 预判方向（看多/看空/看平），用于标题标记

        Returns:
            PushPlus API响应
        """
        data = {
            "token": self.token,
            "title": title,
            "content": content,
            "template": "markdown",
        }

        print(f"[Pusher] 推送PushPlus消息: {title}")

        for attempt in range(3):
            try:
                resp = requests.post(
                    self.url,
                    json=data,
                    timeout=10,
                )
                result = resp.json()

                if result.get("code") == 200:
                    print("[Pusher] 推送成功")
                    return result
                else:
                    print(f"[Pusher] 推送返回错误: {result}")

            except requests.RequestException as e:
                print(f"[Pusher] 推送失败 (第{attempt + 1}次): {e}")

            if attempt < 2:
                time.sleep(5)

        print("[Pusher] 推送最终失败")
        return {"code": -1, "msg": "推送失败，已重试3次"}
