"""工具函数 — HTML清洗、日期处理、去重管理"""

import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from bs4 import BeautifulSoup

from src.config import Config

# 中国时区
CST = timezone(timedelta(hours=8))


def html_to_text(html: str) -> str:
    """将HTML转为纯文本，保留段落结构

    Args:
        html: 原始HTML内容

    Returns:
        清洗后的纯文本
    """
    if not html:
        return ""

    soup = BeautifulSoup(html, "html.parser")

    # 移除script和style标签
    for tag in soup(["script", "style"]):
        tag.decompose()

    # 处理常见块级标签，添加换行
    for tag in soup(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "br"]):
        tag.insert_before("\n")
        if tag.name != "br":
            tag.insert_after("\n")

    text = soup.get_text()

    # 清理多余空白
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    return text


def today_str() -> str:
    """返回今天日期字符串（中国时区）"""
    return datetime.now(CST).strftime("%Y年%m月%d日")


def timestamp_to_date(ts: int) -> str:
    """Unix时间戳转日期字符串"""
    return datetime.fromtimestamp(ts, tz=CST).strftime("%Y年%m月%d日")


def is_today(timestamp: int) -> bool:
    """判断时间戳是否是今天（中国时区）"""
    ts_date = datetime.fromtimestamp(timestamp, tz=CST).date()
    today_date = datetime.now(CST).date()
    return ts_date == today_date


# ── 去重管理 ──

def get_processed_ids() -> set[int]:
    """读取已处理的文章ID集合"""
    filepath = Config.PROCESSED_IDS_FILE
    if not filepath.exists():
        return set()

    ids = set()
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.isdigit():
                ids.add(int(line))
    return ids


def is_already_processed(article_id: int) -> bool:
    """检查文章是否已处理过"""
    return article_id in get_processed_ids()


def mark_processed(article_id: int) -> None:
    """记录已处理的文章ID"""
    filepath = Config.PROCESSED_IDS_FILE
    filepath.parent.mkdir(parents=True, exist_ok=True)

    with open(filepath, "a", encoding="utf-8") as f:
        f.write(f"{article_id}\n")

    print(f"[Utils] 已记录文章ID: {article_id}")


def cleanup_old_ids(max_lines: int = 100) -> None:
    """清理已处理ID记录，只保留最近N条"""
    filepath = Config.PROCESSED_IDS_FILE
    if not filepath.exists():
        return

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if len(lines) <= max_lines:
        return

    # 保留最后max_lines条
    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(lines[-max_lines:])

    print(f"[Utils] 清理过期ID记录，保留最近{max_lines}条")
