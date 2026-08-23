"""微信公众号发布模块 — 草稿+发布API封装

接口流程：
1. get_access_token() → 获取并缓存access_token
2. upload_cover_image() → 上传封面图，获取thumb_media_id
3. create_draft() → 新建草稿
4. publish_draft() → 发布草稿（异步，需轮询状态）
"""

import time
import json
import requests
from pathlib import Path

from src.config import Config


class WechatPublisher:
    """微信公众号发布器"""

    BASE_URL = "https://api.weixin.qq.com/cgi-bin"

    def __init__(self, appid: str = "", secret: str = ""):
        self.appid = appid or Config.WECHAT_APPID
        self.secret = secret or Config.WECHAT_APPSECRET
        self._access_token = ""
        self._token_expires_at = 0

    # ── access_token 管理 ──

    def get_access_token(self) -> str:
        """获取access_token，自动缓存和刷新"""
        # 缓存未过期则直接返回
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        print("[WechatPublisher] 获取access_token...")
        url = f"{self.BASE_URL}/token"
        params = {
            "grant_type": "client_credential",
            "appid": self.appid,
            "secret": self.secret,
        }

        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()

        if "access_token" not in data:
            raise ValueError(f"获取access_token失败: {data}")

        self._access_token = data["access_token"]
        # 提前5分钟过期，避免边界问题
        self._token_expires_at = time.time() + data.get("expires_in", 7200) - 300

        print("[WechatPublisher] access_token获取成功")
        return self._access_token

    # ── 图片上传 ──

    def upload_cover_image(self, image_path: str = "") -> str:
        """上传封面图，返回thumb_media_id

        Args:
            image_path: 图片文件路径，默认使用Config中的路径

        Returns:
            thumb_media_id（用于创建草稿时的封面）
        """
        path = image_path or Config.WECHAT_COVER_IMAGE_PATH
        if not path or not Path(path).exists():
            raise FileNotFoundError(f"封面图不存在: {path}")

        token = self.get_access_token()
        url = f"{self.BASE_URL}/material/add_material"
        params = {"access_token": token, "type": "image"}

        print(f"[WechatPublisher] 上传封面图: {path}")
        with open(path, "rb") as f:
            resp = requests.post(
                url,
                params=params,
                files={"media": (Path(path).name, f, "image/jpeg")},
                timeout=30,
            )

        data = resp.json()
        if "media_id" not in data:
            raise ValueError(f"上传封面图失败: {data}")

        print(f"[WechatPublisher] 封面图上传成功, media_id={data['media_id']}")
        return data["media_id"]

    def upload_content_image(self, image_path: str) -> str:
        """上传正文中的图片，返回微信URL

        Args:
            image_path: 图片文件路径

        Returns:
            微信图片URL（用于正文中的<img>标签）
        """
        token = self.get_access_token()
        url = f"{self.BASE_URL}/media/uploadimg"
        params = {"access_token": token}

        with open(image_path, "rb") as f:
            resp = requests.post(
                url,
                params=params,
                files={"media": (Path(image_path).name, f, "image/jpeg")},
                timeout=30,
            )

        data = resp.json()
        if "url" not in data:
            raise ValueError(f"上传正文图片失败: {data}")

        return data["url"]

    # ── 草稿管理 ──

    def create_draft(
        self,
        title: str,
        content: str,
        thumb_media_id: str,
        digest: str = "",
        author: str = "AI财经分析师",
    ) -> str:
        """新建草稿

        Args:
            title: 文章标题
            content: HTML格式正文
            thumb_media_id: 封面图media_id
            digest: 摘要（空则自动截取正文前120字）
            author: 作者名

        Returns:
            草稿的media_id
        """
        token = self.get_access_token()
        url = f"{self.BASE_URL}/draft/add"
        params = {"access_token": token}

        if not digest:
            from src.report_formatter import generate_article_digest
            digest = generate_article_digest(content)

        data = {
            "articles": [
                {
                    "title": title,
                    "author": author,
                    "digest": digest,
                    "content": content,
                    "thumb_media_id": thumb_media_id,
                    "need_open_comment": 1,       # 打开评论
                    "only_fans_can_comment": 0,    # 所有人可评论
                }
            ]
        }

        print(f"[WechatPublisher] 创建草稿: {title}")
        resp = requests.post(url, params=params, json=data, timeout=30)
        result = resp.json()

        if result.get("errcode", 0) != 0:
            raise ValueError(f"创建草稿失败: {result}")

        media_id = result["media_id"]
        print(f"[WechatPublisher] 草稿创建成功, media_id={media_id}")
        return media_id

    # ── 发布 ──

    def publish_draft(self, media_id: str) -> str:
        """发布草稿（异步接口，会轮询发布状态）

        Args:
            media_id: 草稿的media_id

        Returns:
            发布后的article_id（publish_id）
        """
        token = self.get_access_token()
        url = f"{self.BASE_URL}/freepublish/submit"
        params = {"access_token": token}

        print(f"[WechatPublisher] 发布草稿: {media_id}")
        resp = requests.post(url, params=params, json={"media_id": media_id}, timeout=30)
        result = resp.json()

        if result.get("errcode", 0) != 0:
            raise ValueError(f"发布失败: {result}")

        publish_id = result.get("publish_id", "")
        print(f"[WechatPublisher] 发布提交成功, publish_id={publish_id}")

        # 轮询发布状态（最多等30秒）
        for _ in range(6):
            time.sleep(5)
            status = self._check_publish_status(publish_id)
            if status == "success":
                print("[WechatPublisher] 发布成功！")
                return publish_id
            elif status == "fail":
                raise ValueError("发布失败，请检查公众号后台")

        print("[WechatPublisher] 发布状态未确认（可能仍在处理中），请检查公众号后台")
        return publish_id

    def _check_publish_status(self, publish_id: str) -> str:
        """查询发布状态"""
        token = self.get_access_token()
        url = f"{self.BASE_URL}/freepublish/get"
        params = {"access_token": token}

        resp = requests.post(url, params=params, json={"publish_id": publish_id}, timeout=10)
        data = resp.json()

        if data.get("errcode", 0) != 0:
            return "fail"

        status = data.get("publish_status", 0)
        if status == 1:
            return "success"
        elif status == 2:
            return "fail"
        return "pending"

    # ── 一键发布 ──

    def publish_article(
        self,
        title: str,
        html_content: str,
        cover_image_path: str = "",
    ) -> str:
        """一键发布文章到公众号

        串联：上传封面 → 创建草稿 → 发布

        Args:
            title: 文章标题
            html_content: HTML格式正文
            cover_image_path: 封面图路径（默认用Config中的）

        Returns:
            publish_id
        """
        # 1. 上传封面图
        thumb_media_id = self.upload_cover_image(cover_image_path)

        # 2. 创建草稿
        draft_media_id = self.create_draft(title, html_content, thumb_media_id)

        # 3. 发布
        publish_id = self.publish_draft(draft_media_id)

        return publish_id
