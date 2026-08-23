"""数据获取模块 — 华尔街见闻API获取早餐FM文章 + 东方财富API获取全球指数"""

import time
import requests
from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timezone, timedelta

from src.config import Config

# 中国时区
CST = timezone(timedelta(hours=8))


@dataclass
class BreakfastArticle:
    """早餐FM文章"""
    id: int
    title: str
    uri: str
    content_html: str           # 原始HTML正文
    content_text: str           # 纯文本正文（供AI处理）
    display_time: int           # 发布时间戳
    is_priced: bool             # 是否付费
    audio_uri: Optional[str]    # 音频地址


@dataclass
class MarketQuote:
    """市场行情"""
    code: str                   # 产品代码
    name: str                   # 产品名称
    last_px: float              # 最新价
    px_change: float            # 涨跌额
    px_change_rate: float       # 涨跌幅%
    securities_type: str        # 类型


def _wscn_request(url: str, params: dict = None) -> dict:
    """华尔街见闻API请求封装，带重试和间隔控制"""
    for attempt in range(Config.MAX_RETRIES):
        try:
            resp = requests.get(
                url,
                params=params,
                headers=Config.WSCN_HEADERS,
                timeout=Config.REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()

            # 华尔街见闻API返回 code: 20000 表示成功
            if data.get("code") != 20000:
                raise ValueError(f"API返回错误码: {data.get('code')}, 响应: {data}")

            return data.get("data", data)

        except requests.RequestException as e:
            if attempt < Config.MAX_RETRIES - 1:
                wait = 5 * (2 ** attempt)  # 5, 15, 45 秒指数退避
                print(f"  请求失败 (第{attempt + 1}次): {e}, {wait}秒后重试...")
                time.sleep(wait)
            else:
                raise

    return {}


def get_latest_breakfast() -> Optional[BreakfastArticle]:
    """获取最新早餐FM文章（含全文）"""
    # 第1步：从信息流获取最新早餐FM文章ID
    print("[Fetcher] 获取早餐FM文章列表...")
    data = _wscn_request(
        f"{Config.WSCN_API_BASE}/apiv1/content/information-flow",
        params={
            "channel": "breakfast",
            "accept": "article",
            "limit": 5,
            "action": "upglide",
        },
    )

    items = data.get("items", [])
    if not items:
        print("[Fetcher] 未获取到早餐FM文章")
        return None

    # 找到第一篇免费文章
    target = None
    for item in items:
        resource = item.get("resource", {})
        if not resource.get("is_priced", True):
            target = resource
            break

    if not target:
        print("[Fetcher] 未找到免费早餐FM文章（可能全部为付费内容）")
        return None

    article_id = int(target["id"])
    article_title = target.get("title", "")
    display_time = target.get("display_time", 0)

    print(f"[Fetcher] 找到文章: {article_title} (ID: {article_id})")

    # 第2步：获取文章详情（含正文）
    print("[Fetcher] 获取文章详情...")
    detail = _wscn_request(
        f"{Config.WSCN_API_BASE}/apiv1/content/articles/{article_id}",
        params={
            "extract": 0,
            "accept_theme": "theme,premium-theme",
            "remove_disclaimer": 1,
        },
    )

    content_html = detail.get("content", "")
    if not content_html:
        print("[Fetcher] 文章正文为空")
        return None

    # HTML → 纯文本
    from src.utils import html_to_text
    content_text = html_to_text(content_html)

    return BreakfastArticle(
        id=article_id,
        title=article_title,
        uri=f"https://wallstreetcn.com/articles/{article_id}",
        content_html=content_html,
        content_text=content_text,
        display_time=display_time,
        is_priced=detail.get("is_priced", True),
        audio_uri=detail.get("audio_uri"),
    )


def get_fx_commodity_quotes() -> list[MarketQuote]:
    """获取外汇和商品行情（华尔街见闻API）"""
    print("[Fetcher] 获取外汇/商品行情...")
    codes = ",".join(Config.MARKET_CODES)

    data = _wscn_request(
        f"{Config.WSCN_MARKET_API_BASE}/market/real",
        params={
            "prod_code": codes,
            "fields": "prod_name,last_px,px_change,px_change_rate,price_precision,securities_type",
        },
    )

    fields = data.get("fields", [])
    snapshots = data.get("snapshot", {})

    quotes = []
    for code, values in snapshots.items():
        try:
            field_map = dict(zip(fields, values))
            quotes.append(MarketQuote(
                code=code,
                name=field_map.get("prod_name", code),
                last_px=float(field_map.get("last_px", 0)),
                px_change=float(field_map.get("px_change", 0)),
                px_change_rate=float(field_map.get("px_change_rate", 0)),
                securities_type=field_map.get("securities_type", ""),
            ))
        except (ValueError, TypeError) as e:
            print(f"  解析行情数据失败 {code}: {e}")
            continue

    print(f"[Fetcher] 获取到 {len(quotes)} 个外汇/商品行情")
    return quotes


def get_bond_quotes() -> list[MarketQuote]:
    """获取债券收益率行情（华尔街见闻API）"""
    print("[Fetcher] 获取债券收益率...")
    bond_codes = "US10YR.OTC,CN10YR.OTC"

    data = _wscn_request(
        f"{Config.WSCN_MARKET_API_BASE}/market/real",
        params={
            "prod_code": bond_codes,
            "fields": "prod_name,last_px,px_change,px_change_rate,securities_type",
        },
    )

    fields = data.get("fields", [])
    snapshots = data.get("snapshot", {})

    quotes = []
    for code, values in snapshots.items():
        try:
            field_map = dict(zip(fields, values))
            quotes.append(MarketQuote(
                code=code,
                name=field_map.get("prod_name", code),
                last_px=float(field_map.get("last_px", 0)),
                px_change=float(field_map.get("px_change", 0)),
                px_change_rate=float(field_map.get("px_change_rate", 0)),
                securities_type="bond",
            ))
        except (ValueError, TypeError) as e:
            print(f"  解析债券数据失败 {code}: {e}")
            continue

    print(f"[Fetcher] 获取到 {len(quotes)} 个债券收益率")
    return quotes


def get_global_indices() -> list[MarketQuote]:
    """获取全球主要股指行情（东方财富API）"""
    print("[Fetcher] 获取全球股指行情...")

    # 东方财富全球指数 secid 格式: 市场代码.代码
    # 100=全球, 104=期货, 116=港股, 124=恒生科技
    secids = [
        # A股
        "1.000001",     # 上证指数
        "0.399001",     # 深证成指
        "0.399006",     # 创业板指
        # 港股
        "100.HSI",      # 恒生指数
        "124.HSTECH",   # 恒生科技
        # 美股
        "100.DJIA",     # 道琼斯
        "100.NDX",      # 纳斯达克
        "100.SPX",      # 标普500
        # 亚太
        "100.N225",     # 日经225
        "100.KS11",     # 韩国KOSPI
        # 欧洲
        "100.FTSE",     # 英国富时100
        "100.GDAXI",    # 德国DAX30
        "100.FCHI",     # 法国CAC40
        # A50期货
        "100.XIN9",     # 富时中国A50
    ]

    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params = {
        "fltt": 2,
        "secids": ",".join(secids),
        "fields": "f2,f3,f12,f13,f14",
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        diff = data.get("data", {}).get("diff", []) or []
    except Exception as e:
        print(f"[Fetcher] 东方财富API请求失败: {e}")
        return []

    quotes = []
    for item in diff:
        try:
            name = item.get("f14", "")
            price = float(item.get("f2", 0))
            chg_pct = float(item.get("f3", 0))
            code = item.get("f12", "")
            mkt = item.get("f13", 0)

            # 确定类型
            if mkt in [1, 0]:
                stype = "a股"
            elif mkt == 100 and code == "XIN9":
                stype = "futures"
            elif mkt in [100, 116, 124]:
                stype = "index"
            elif mkt == 104:
                stype = "futures"
            else:
                stype = "index"

            quotes.append(MarketQuote(
                code=code,
                name=name,
                last_px=price,
                px_change=0,              # 东方财富API不直接返回涨跌额
                px_change_rate=chg_pct,
                securities_type=stype,
            ))
        except (ValueError, TypeError) as e:
            print(f"  解析全球指数数据失败: {e}")
            continue

    print(f"[Fetcher] 获取到 {len(quotes)} 个全球指数行情")
    return quotes


def get_all_market_data() -> list[MarketQuote]:
    """汇总所有市场行情数据"""
    quotes = []

    # 1. 全球股指（东方财富）
    quotes.extend(get_global_indices())

    # 2. 外汇 + 商品（华尔街见闻）
    quotes.extend(get_fx_commodity_quotes())

    # 3. 债券收益率（华尔街见闻）
    quotes.extend(get_bond_quotes())

    return quotes


def format_market_data(quotes: list[MarketQuote]) -> str:
    """将市场行情按板块分组格式化（供AI分析使用）"""
    if not quotes:
        return "暂无市场行情数据"

    # 按类型分组
    groups = {
        "a股": [],
        "index": [],       # 全球股指
        "futures": [],     # 期货
        "forex": [],       # 外汇
        "commodity": [],   # 商品
        "bond": [],        # 债券
    }
    for q in quotes:
        g = groups.get(q.securities_type, groups["index"])
        g.append(q)

    lines = []

    # A股
    if groups["a股"]:
        lines.append("### A股收盘")
        lines.append("")
        for q in groups["a股"]:
            direction = _arrow(q.px_change_rate)
            lines.append(f"- **{q.name}**: {q.last_px:.2f}  {direction} {abs(q.px_change_rate):.2f}%")

    # 全球股指
    global_indices = groups["index"] + groups["futures"]
    if global_indices:
        lines.append("")
        lines.append("### 全球股指")
        lines.append("")
        # 分亚盘/欧盘/美盘
        asia = [q for q in global_indices if q.name in ("恒生指数", "恒生科技指数", "日经225", "韩国KOSPI", "富时中国A50")]
        europe = [q for q in global_indices if q.name in ("英国富时100", "德国DAX30", "法国CAC40")]
        us = [q for q in global_indices if q.name in ("道琼斯", "纳斯达克", "标普500")]

        if asia:
            lines.append("**亚太:**")
            for q in asia:
                direction = _arrow(q.px_change_rate)
                lines.append(f"- {q.name}: {q.last_px:.2f}  {direction} {abs(q.px_change_rate):.2f}%")
        if europe:
            lines.append("**欧洲:**")
            for q in europe:
                direction = _arrow(q.px_change_rate)
                lines.append(f"- {q.name}: {q.last_px:.2f}  {direction} {abs(q.px_change_rate):.2f}%")
        if us:
            lines.append("**美股:**")
            for q in us:
                direction = _arrow(q.px_change_rate)
                lines.append(f"- {q.name}: {q.last_px:.2f}  {direction} {abs(q.px_change_rate):.2f}%")

    # 外汇
    if groups["forex"]:
        lines.append("")
        lines.append("### 外汇")
        lines.append("")
        for q in groups["forex"]:
            direction = _arrow(q.px_change_rate)
            lines.append(f"- **{q.name}**: {q.last_px:.4f}  {direction} {abs(q.px_change_rate):.2f}%")

    # 商品
    if groups["commodity"]:
        lines.append("")
        lines.append("### 大宗商品")
        lines.append("")
        for q in groups["commodity"]:
            direction = _arrow(q.px_change_rate)
            lines.append(f"- **{q.name}**: {q.last_px:.2f}  {direction} {abs(q.px_change_rate):.2f}%")

    # 债券
    if groups["bond"]:
        lines.append("")
        lines.append("### 债券收益率")
        lines.append("")
        for q in groups["bond"]:
            direction = _arrow(q.px_change_rate)
            unit = "%"  # 收益率本身就是百分比
            lines.append(f"- **{q.name}**: {q.last_px:.3f}{unit}  {direction} {abs(q.px_change_rate):.2f}%")

    return "\n".join(lines)


def _arrow(rate: float) -> str:
    """涨跌箭头"""
    if rate > 0:
        return "↑"
    elif rate < 0:
        return "↓"
    return "→"
