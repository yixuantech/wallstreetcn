#!/usr/bin/env python3
"""华尔街见闻早餐FM 自动化报告 — 主入口

流程：获取早餐FM文章 → AI分析 → PushPlus推送
"""

import sys
from datetime import datetime, timezone, timedelta

from src.config import Config
from src.fetcher import get_latest_breakfast, get_all_market_data, format_market_data
from src.analyzer import analyze, extract_verdict
from src.pusher import PushPlusPush
from src.utils import is_already_processed, mark_processed, today_str, is_today, cleanup_old_ids
from src.report_formatter import markdown_to_wechat_html

CST = timezone(timedelta(hours=8))


def run():
    """主流程"""
    print(f"{'='*60}")
    print(f"  华尔街见闻早餐FM 自动化报告")
    print(f"  运行时间: {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    # 1. 配置校验
    missing = Config.validate()
    if missing:
        print(f"[错误] 缺少必填配置: {', '.join(missing)}")
        print("请检查 .env 文件或环境变量设置")
        sys.exit(1)

    # 2. 获取最新早餐FM文章
    article = get_latest_breakfast()
    if not article:
        print("[Main] 今日暂无早餐FM文章，退出")
        return

    # 3. 日期校验（确保是今天的文章）
    if not is_today(article.display_time):
        article_date = datetime.fromtimestamp(article.display_time, tz=CST).strftime('%Y-%m-%d')
        print(f"[Main] 文章日期 {article_date} 非今日，退出")
        return

    # 4. 去重检查
    if is_already_processed(article.id):
        print(f"[Main] 文章 {article.id} 已处理过，退出")
        return

    # 5. 获取市场行情
    market_quotes = get_all_market_data()
    market_data = format_market_data(market_quotes)

    # 6. AI分析生成报告
    report = analyze(article.content_text, market_data)

    # 7. 提取预判关键词
    verdict = extract_verdict(report)
    print(f"[Main] 今日预判: {verdict}")

    # 8. PushPlus推送
    pusher = PushPlusPush()
    title = f"🌅 早餐FM日报 | {today_str()} | {verdict}"
    pusher.push(title, report, verdict)

    # 9. 公众号发布（如果配置了公众号）
    if Config.is_wechat_configured():
        try:
            from src.wechat_publisher import WechatPublisher

            print("[Main] 开始公众号发布...")
            publisher = WechatPublisher()
            html_content = markdown_to_wechat_html(report, add_footer=True)
            publisher.publish_article(title, html_content)
            print("[Main] 公众号发布成功")
        except Exception as e:
            print(f"[Main] 公众号发布失败: {e}")
            # 公众号发布失败不影响主流程

    # 10. 记录已处理
    mark_processed(article.id)

    # 11. 清理过期记录
    cleanup_old_ids()

    print(f"[Main] 完成！")


if __name__ == "__main__":
    run()
