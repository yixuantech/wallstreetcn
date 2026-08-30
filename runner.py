#!/usr/bin/env python3
"""AI盘报 — 统一入口

用法: python runner.py <命令>

  morning    晨报（盘前预判，07:30）
  noon       午间快讯（🟡以上增量才推，11:35）
  evening    晚报（盘后复盘+情绪刻度+判断记分牌，17:30）
  macro_cn   数据解读·中国档（数据落地日18:30推送）
  macro_us   数据解读·美国档（美国数据落地次晨07:45推送）
  night      夜巡（🔴关键词命中才推紧急警报，20:30）
  weekly     周报（主线周演进+记分胜率+观察点结算+下周日历，周六09:00）
  site       全量重建静态站（四角色驾驶舱+报告归档；每次推送后也会自动重建）
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.config import Config
from src.fetcher import get_latest_breakfast, get_all_market_data, format_market_data
from src.analyzer import (analyze, analyze_evening, analyze_noon, analyze_alert,
                          analyze_macro, analyze_weekly, extract_verdict, split_meta)
from src.market_close import collect_close_panorama, format_close_prompt, _tencent_index
from src.macro_data import (collect_cn, collect_us, landed_cn, landed_us,
                            mark_seen_cn, mark_seen_us, concept_cards,
                            format_macro_prompt, next_publish_calendar,
                            cn_release_forecast, CN_INDICATORS, US_INDICATORS)
from src.engines import (sentiment_gauge, archive_sentiment, format_sentiment_prompt,
                         scoreboard, format_scoreboard_prompt, scoreboard_summary,
                         update_watchpoints, format_watchpoints_prompt,
                         scan_new_events, scan_red_alerts, filter_flagged,
                         format_noon_prompt, format_alert_prompt, macro_compare,
                         archive_pattern_events, format_pattern_bank,
                         weekly_judgment_stats, format_weekly_judgments,
                         format_weekly_watchpoints, format_next_calendar,
                         dress_report, NEXT_SHIFT)
from src.pusher import PushPlusPush
from src.utils import (is_already_processed, mark_processed, today_str,
                       is_today, cleanup_old_ids, is_trading_day, monday_of)
from src.watchlist import (collect_all, format_watchlist_prompt,
                           format_close_watchlist_prompt, label_summary,
                           collect_calendar_only, LABEL_RED, LABEL_YELLOW)
from src.storylines import (merge_storylines, format_storylines_prompt,
                            format_weekly_storylines)
from src import state
from src.utils import CST


def cmd_morning():
    """晨报：文章→行情→自选股→AI→剥离预判块→落盘→推送→状态快照"""
    print(f"{'='*60}")
    print(f"  AI盘报 · 晨报")
    print(f"  运行时间: {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    # 0. 交易日判断（假日静默，省一次API调用）
    if not is_trading_day():
        print("[Morning] 今日非交易日，退出")
        return

    # 1. 配置校验
    missing = Config.validate()
    if missing:
        print(f"[错误] 缺少必填配置: {', '.join(missing)}")
        sys.exit(1)

    # 2. 获取最新早餐FM文章
    article = get_latest_breakfast()
    if not article:
        print("[Morning] 今日暂无早餐FM文章，退出")
        return

    # 3. 日期校验
    if not is_today(article.display_time):
        print("[Morning] 文章非今日，退出")
        return

    # 4. 去重检查
    if is_already_processed(article.id):
        print(f"[Morning] 文章 {article.id} 已处理过，退出")
        return

    # 5. 市场行情
    market_data = format_market_data(get_all_market_data())

    # 5.5 自选股数据
    watchlist_stocks = collect_all()
    watchlist_data = format_watchlist_prompt(watchlist_stocks) if watchlist_stocks else ""
    state.save_label_snapshot(watchlist_stocks)   # 晨巡分诊快照（站点哨兵区/个股页）

    # 5.6 主线连载状态 + 历史模式库（M4：主线JSON进出晨报）
    storyline_lines = state.load_storylines().get("lines", [])
    storylines_block = format_storylines_prompt(storyline_lines)
    pattern_bank = format_pattern_bank()

    # 6. AI分析（含机器可读预判块）
    raw_report = analyze(article.content_text, market_data, watchlist_data,
                         storylines_block, pattern_bank)

    # 6.5 剥离预判块 → 干净报告 + 判断快照
    report, meta = split_meta(raw_report)
    verdict = extract_verdict(report)
    print(f"[Morning] 今日预判: {verdict}"
          + (f"（快照: {meta['direction']}）" if meta else "（预判块缺失，晚报记分将跳过）"))

    # 6.6 主线合并（AI提案 → 规则裁决；预判块损坏则保持原样）
    if meta is not None:
        new_lines, sl_changes = merge_storylines(storyline_lines, meta.get("storylines"))
        state.save_storylines({"lines": new_lines})
        active_n = sum(1 for l in new_lines if l.get("status") != "终结")
        note = f"，变更: {'；'.join(sl_changes)}" if sl_changes else ""
        print(f"[Morning] 主线合并完成（活跃{active_n}条{note}）")

    # 7. 报告落盘（地基：晚报记分与周报的原材料；骨架=速览头+账本尾，规则直出随文档归档）
    report = dress_report("morning", report)
    report_dir = Path("data/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{datetime.now(CST).strftime('%Y-%m-%d')}.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"[Morning] 报告已落盘: {report_path}")

    # 8. PushPlus推送
    pusher = PushPlusPush()
    title = f"🌅 AI盘报·晨报 | {today_str()} | {verdict}"
    if watchlist_stocks:
        title += f" | {label_summary(watchlist_stocks)}"
    pusher.push(title, report, verdict)
    _rebuild_site()

    # 9. 当日快照存档（午间/夜巡防重 + 晚报记分依据）
    event_ids = []
    for s in watchlist_stocks:
        event_ids += [n.get("url", "") for n in s.news]
        event_ids += [a.get("url", "") for a in s.announcements]
    today = state.load_today()
    today["morning"] = {
        "pushed": True,
        "report_path": str(report_path),
        "judgment": meta or {},
    }
    # 顶层统一事件ID池：晨报初始化，午间/晚报/夜巡各自追加（防重唯一真源）
    today["pushed_event_ids"] = [u for u in event_ids if u]
    state.save_today(today)
    print("[Morning] 当日快照已存档 → data/state/today.json")

    # 10. 公众号发布（如配置）
    if Config.is_wechat_configured():
        try:
            from src.report_formatter import markdown_to_wechat_html
            from src.wechat_publisher import WechatPublisher
            print("[Morning] 开始公众号发布...")
            publisher = WechatPublisher()
            html_content = markdown_to_wechat_html(report, add_footer=True)
            publisher.publish_article(title, html_content)
            print("[Morning] 公众号发布成功")
        except Exception as e:
            print(f"[Morning] 公众号发布失败: {e}")

    # 11. 记录已处理 + 清理
    mark_processed(article.id)
    cleanup_old_ids()

    print("[Morning] 完成！")


def cmd_evening():
    """晚报：收盘采集→情绪刻度→记分牌→观察点→AI复盘→推送→状态归档"""
    print(f"{'='*60}")
    print(f"  AI盘报 · 晚报")
    print(f"  运行时间: {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    if not is_trading_day():
        print("[Evening] 今日非交易日，退出")
        return

    missing = Config.validate()
    if missing:
        print(f"[错误] 缺少必填配置: {', '.join(missing)}")
        sys.exit(1)

    today = state.load_today()
    if today.get("evening", {}).get("pushed"):
        print("[Evening] 今日晚报已推送过，退出")
        return

    # 1. 收盘全景（纯事实）
    panorama = collect_close_panorama()

    # 2. 情绪刻度 + 历史归档（跑满10天起有均值对照）
    gauge = sentiment_gauge(panorama)
    hist_line = archive_sentiment(gauge, panorama)
    print(f"[Evening] 情绪刻度: {gauge.get('score')}/{gauge.get('label', '—')}")

    # 3. 自选股再采集（收盘价+当日新公告，也是观察点兑现检测的输入）
    stocks = collect_all()
    state.save_label_snapshot(stocks)             # 收盘分诊快照（覆盖晨巡版）

    # 4. 判断记分牌（今晨快照 vs 实际收盘）
    sb = scoreboard(today.get("morning", {}), panorama, stocks)
    if sb["status"] == "ok" and sb["direction"]:
        d = sb["direction"]
        sec_hits = sum(1 for x in sb["sectors"] if x["result"] == "✓")
        state.append_judgment({
            "date": datetime.now(CST).strftime("%Y-%m-%d"),
            "judgment": d["judgment"], "actual": d["actual"], "result": d["result"],
            "detail": f"板块✓{sec_hits}/{len(sb['sectors'])}；情绪{gauge.get('score')}",
        })
    print(f"[Evening] 记分牌: {scoreboard_summary(sb)}")

    # 4.5 事件归档（M4历史模式锚原材料：只归档对上品种的条目，周报/晨报模式库复用）
    archived = archive_pattern_events(sb)
    if archived:
        print(f"[Evening] 模式库归档{archived}条")

    # 5. 观察点状态机（挂起/兑现/失效）
    wp = update_watchpoints(stocks)

    # 6. 数据块 → AI晚报（只解释，不判定）
    pushed_urls = set(_pushed_event_ids(today))
    if stocks:
        watch_block = ("## 自选股收盘（相对晨报的增量事件）\n\n"
                       + format_close_watchlist_prompt(stocks, pushed_urls))
    else:
        watch_block = "## 自选股收盘\n\n自选列表为空"
    data_blocks = "\n\n".join([
        format_close_prompt(panorama),
        format_sentiment_prompt(gauge, hist_line),
        format_scoreboard_prompt(sb),
        format_watchpoints_prompt(wp),
        watch_block,
    ])
    report = analyze_evening(data_blocks)

    # 7. 落盘 + 推送（骨架=速览头含「今日变化」行+账本尾；分诊/记分/归档均已入库，速览读到的即今日最新）
    report = dress_report("evening", report)
    date_str = datetime.now(CST).strftime("%Y-%m-%d")
    report_path = Path("data/reports") / f"{date_str}-evening.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"[Evening] 报告已落盘: {report_path}")

    score_txt = f"情绪{gauge['score']}·{gauge['label']}" if gauge.get("score") is not None else "情绪缺"
    title = f"🌙 AI盘报·晚报 | {today_str()} | {scoreboard_summary(sb)} | {score_txt}"
    PushPlusPush().push(title, report)
    _rebuild_site()

    # 8. 快照 + 晚间新增事件回写ID池（夜巡防重）
    today["evening"] = {
        "pushed": True,
        "report_path": str(report_path),
        "sentiment": gauge.get("score"),
        "sentiment_label": gauge.get("label"),
        "scoreboard": scoreboard_summary(sb),
    }
    _extend_pushed_ids(today, stocks)
    state.save_today(today)
    print("[Evening] 完成！")


def _pushed_event_ids(today: dict) -> list:
    """已推事件URL池（顶层新位置优先，兼容晨报旧位置的当日文件）"""
    return today.get("pushed_event_ids") or today.get("morning", {}).get("pushed_event_ids", [])


def _extend_pushed_ids(today: dict, stocks) -> list:
    """把本次采集到、尚未记录的事件URL并入ID池，返回新增部分"""
    known = set(_pushed_event_ids(today))
    fresh = []
    for s in stocks:
        for n in s.news:
            if n.get("url") and n["url"] not in known:
                fresh.append(n["url"])
        for a in s.announcements:
            if a.get("url") and a["url"] not in known:
                fresh.append(a["url"])
    if fresh:
        today["pushed_event_ids"] = list(known) + fresh
    return fresh


def cmd_noon():
    """午间快讯：增量采集 → 仅🟡以上新增事件才推（三件套护栏），否则静默"""
    print(f"{'='*60}")
    print(f"  AI盘报 · 午间快讯")
    print(f"  运行时间: {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    if not is_trading_day():
        print("[Noon] 今日非交易日，退出")
        return

    missing = Config.validate()
    if missing:
        print(f"[错误] 缺少必填配置: {', '.join(missing)}")
        sys.exit(1)

    today = state.load_today()
    if today.get("noon_pushed"):
        print("[Noon] 今日午间快讯已处理过，退出")
        return

    stocks = collect_all()
    new_events = scan_new_events(stocks, set(_pushed_event_ids(today)))
    # 三件套护栏：只推规则引擎定级🟡/🔴的股票；🟢有新增不推（分诊不推送）
    flagged = filter_flagged(new_events)
    dropped = len(new_events) - len(flagged)

    if not flagged:
        today["noon_pushed"] = True
        state.save_today(today)
        print(f"[Noon] 无🟡以上新增事件，静默（🟢新增{dropped}条不推送）")
        return

    # 午间指数速览（腾讯实时，盘中最稳的一路）
    indices_line = []
    for name, code in (("上证指数", "sh000001"), ("创业板指", "sz399006")):
        q = _tencent_index(code)
        if q:
            indices_line.append(f"{name} {q['close']} ({q['chg_pct']:+.2f}%)")
    indices_txt = " ｜ ".join(indices_line) or "指数行情缺失"

    report = analyze_noon(indices_txt, format_noon_prompt(flagged))
    report = dress_report("noon", report)   # 打断类：仅账本尾注，警报本身即头版

    date_str = datetime.now(CST).strftime("%Y-%m-%d")
    report_path = Path("data/reports") / f"{date_str}-noon.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    names = " ".join(s.name for s, _ in flagged)
    title = f"⚡ AI盘报·午间快讯 | {today_str()} | {names}"
    PushPlusPush().push(title, report)
    _rebuild_site()

    # 只回收已推送的事件ID（🟢新增留给晚报收盘段展示）
    today["noon_pushed"] = True
    known = set(_pushed_event_ids(today))
    for s, items in flagged:
        known |= {it["url"] for it in items if it.get("url")}
    today["pushed_event_ids"] = list(known)
    state.save_today(today)
    print(f"[Noon] 已推送 {len(flagged)} 只股票的快讯（🟢新增{dropped}条留给晚报）")


def cmd_night():
    """夜巡：晚间新增事件 × 🔴关键词 → 仅命中才推紧急警报，否则静默"""
    print(f"{'='*60}")
    print(f"  AI盘报 · 夜巡")
    print(f"  运行时间: {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    if not is_trading_day():
        print("[Night] 今日非交易日，退出")
        return

    missing = Config.validate()
    if missing:
        print(f"[错误] 缺少必填配置: {', '.join(missing)}")
        sys.exit(1)

    today = state.load_today()
    if today.get("night_pushed"):
        print("[Night] 今夜已巡检过，退出")
        return

    stocks = collect_all()
    new_events = scan_new_events(stocks, set(_pushed_event_ids(today)))
    alerts = scan_red_alerts(new_events)

    if not alerts:
        today["night_pushed"] = True
        state.save_today(today)
        print(f"[Night] 扫描{sum(len(items) for _, items in new_events)}条新增，"
              f"无🔴关键词命中，静默")
        return

    report = analyze_alert(format_alert_prompt(alerts))
    report = dress_report("night", report)  # 打断类：仅账本尾注

    date_str = datetime.now(CST).strftime("%Y-%m-%d")
    report_path = Path("data/reports") / f"{date_str}-night.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    names = " ".join(s.name for s, _, _ in alerts)
    title = f"🚨 AI盘报·紧急警报 | {today_str()} | {names}"
    PushPlusPush().push(title, report)
    _rebuild_site()

    today["night_pushed"] = True
    known = set(_pushed_event_ids(today))
    known |= {it["url"] for _, it, _ in alerts if it.get("url")}
    today["pushed_event_ids"] = list(known)
    state.save_today(today)
    print(f"[Night] 🔴命中{len(alerts)}条，已推送紧急警报")


def cmd_macro_cn():
    """数据解读·中国档：18:30，仅数据落地日推送（落地检测=期次对比）"""
    _cmd_macro("中国档", "cn")


def cmd_macro_us():
    """数据解读·美国档：07:45，仅美国数据落地后次日晨推送"""
    _cmd_macro("美国档", "us")


def _cmd_macro(edition: str, region: str):
    print(f"{'='*60}")
    print(f"  AI盘报 · 数据解读·{edition}")
    print(f"  运行时间: {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    missing = Config.validate()
    if missing:
        print(f"[错误] 缺少必填配置: {', '.join(missing)}")
        sys.exit(1)

    # 中国档看交易日（宏观数据多随工作日发布）；美国档周末也可能有落地的补推
    if region == "cn" and not is_trading_day():
        print(f"[Macro-{edition}] 今日非交易日，退出")
        return

    today = state.load_today()
    flag = f"macro_{region}_pushed"
    if today.get(flag):
        print(f"[Macro-{edition}] 今日已处理过，退出")
        return

    # 采集 + 落地检测
    if region == "cn":
        data = collect_cn()
        fresh = landed_cn(data)
        definitions = CN_INDICATORS
    else:
        data = collect_us()
        fresh = landed_us(data)
        definitions = US_INDICATORS

    if not fresh:
        today[flag] = True
        state.save_today(today)
        print(f"[Macro-{edition}] 无数据落地，静默")
        return

    # 三对照 + 概念卡 + 数据块
    compares = {k: macro_compare(k, data[k], definitions[k].get("predicate"))
                for k in fresh if data.get(k)}
    concepts = concept_cards(fresh)
    watch_names = [s for s in _watch_names()]
    blocks = format_macro_prompt(fresh, data, compares, definitions, concepts, watch_names)

    report = analyze_macro(edition, blocks)
    report = dress_report(f"macro_{region}", report)   # 打断类：仅账本尾注

    date_str = datetime.now(CST).strftime("%Y-%m-%d")
    report_path = Path("data/reports") / f"{date_str}-macro-{region}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    names = "、".join(definitions[k]["label"] for k in fresh)
    title = f"📊 AI盘报·数据解读{edition} | {today_str()} | {names}"
    PushPlusPush().push(title, report)
    _rebuild_site()

    # 落地登记（推送成功后才标记已见，防丢期次）
    if region == "cn":
        mark_seen_cn(data, fresh)
    else:
        mark_seen_us(data, fresh)
    today[flag] = True
    state.save_today(today)
    print(f"[Macro-{edition}] 已推送（落地：{names}）")


def _watch_names() -> list:
    """自选股名称（联动段原料；读配置文件，不做全量采集）"""
    from src.watchlist import load_watchlist
    return [w.get("name", "") for w in load_watchlist()]


def _rebuild_site():
    """推送成功后重建静态站（表达层原型：驾驶舱+归档）。失败只告警，不影响推送。"""
    try:
        from src.site_builder import build_site
        result = build_site()
        print(f"[Site] 静态站已重建（{result['reports']}篇报告 → {result['out']}）")
    except Exception as e:
        print(f"[Site] 静态站重建失败(不影响推送): {e}")


def cmd_site():
    """全量重建静态站：四角色驾驶舱 + 报告归档（本地浏览/原型审阅入口）"""
    print(f"{'='*60}")
    print(f"  AI盘报 · 静态站重建")
    print(f"  运行时间: {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    from src.site_builder import build_site
    result = build_site()
    print(f"[Site] 已收录 {result['reports']} 篇报告")
    print(f"[Site] 输出目录: {result['out']}")
    print(f"[Site] 本地查看: 浏览器打开 {result['out']}\\index.html"
          f"（或 cd site && python -m http.server 8080）")


def cmd_weekly():
    """周报：主线周演进+记分周汇总+观察点结算+下周日历（周六09:00，非交易日也运行）"""
    print(f"{'='*60}")
    print(f"  AI盘报 · 周报")
    print(f"  运行时间: {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    missing = Config.validate()
    if missing:
        print(f"[错误] 缺少必填配置: {', '.join(missing)}")
        sys.exit(1)

    today = state.load_today()
    if today.get("weekly_pushed"):
        print("[Weekly] 今日周报已推送过，退出")
        return

    date_str = datetime.now(CST).strftime("%Y-%m-%d")
    week_start = monday_of(date_str)

    # 1. 原料组装（全规则层，各块独立降级；周六只做轻量日历采集，不碰行情）
    lines = state.load_storylines().get("lines", [])
    stats = weekly_judgment_stats(state.load_judgments(week_start, date_str))
    cal_stocks = collect_calendar_only()
    us_cal = next_publish_calendar(14)
    cn_cal = cn_release_forecast(14)
    data_blocks = "\n\n".join([
        format_weekly_storylines(lines, week_start),
        format_weekly_judgments(stats),
        format_weekly_watchpoints(week_start),
        format_next_calendar(cal_stocks, us_cal, cn_cal),
    ])

    # 2. AI周报（只叙述不判定）
    report = analyze_weekly(data_blocks)

    # 3. 落盘 + 推送（结算类：全骨架——速览头与账本结算正文呼应）
    report = dress_report("weekly", report)
    report_path = Path("data/reports") / f"{date_str}-weekly.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"[Weekly] 报告已落盘: {report_path}")

    rate_txt = f"记分胜率{stats['rate']}%" if stats["rate"] is not None else "本周无记分"
    week_txt = f"{week_start[5:].replace('-', '/')}当周"
    title = f"📖 AI盘报·周报 | {week_txt} | {rate_txt}"
    PushPlusPush().push(title, report)
    _rebuild_site()

    today["weekly_pushed"] = True
    state.save_today(today)
    print("[Weekly] 完成！")


def _not_built(name: str):
    def cmd():
        print(f"[{name}] 该栏目待建（见 doc/实现规划.md），今日不执行")
    return cmd


COMMANDS = {
    "morning": cmd_morning,
    "noon": cmd_noon,
    "evening": cmd_evening,
    "macro_cn": cmd_macro_cn,
    "macro_us": cmd_macro_us,
    "night": cmd_night,
    "weekly": cmd_weekly,
    "site": cmd_site,
}

# 推送班次（生成失败需失败心跳）；site 等工具命令失败只走日志
_PUSH_CMDS = {"morning", "noon", "evening", "macro_cn", "macro_us", "night", "weekly"}

_SHIFT_NAME = {"morning": "🌅晨报", "noon": "⚡午间快讯", "evening": "🌙晚报",
               "macro_cn": "📊数据解读(中国)", "macro_us": "📊数据解读(美国)",
               "night": "🚨夜巡", "weekly": "📖周报"}


def _push_failure_heartbeat(cmd: str, err: Exception) -> None:
    """失败心跳：推送班次生成失败必须出声（宪法 §2「沉默=无事」不可被故障占用）。

    用户收不到推送时无法区分"没事"与"系统挂了"——本通知把后者显式说出来，
    并给出下一班恢复时间。推送通道本身也挂时只留日志（无法触达属物理极限）。
    """
    name = _SHIFT_NAME.get(cmd, cmd)
    next_shift = NEXT_SHIFT.get(cmd, "见栏目时刻表")
    title = f"⚠️ AI盘报·{name}生成失败 | {today_str()}"
    body = (f"**{name}生成失败**（{datetime.now(CST).strftime('%H:%M')}）\n\n"
            f"原因：`{type(err).__name__}: {str(err)[:120]}`\n\n"
            f"本班未发出，哨兵分诊暂停一班。\n\n"
            f"⏰ **下一班：{next_shift}** 自动恢复；若连续失败请查看服务器日志。")
    try:
        result = PushPlusPush().push(title, body)
        if result.get("code") != 200:
            print(f"[Heartbeat] 失败通知未送达: {result}")
    except Exception as pe:  # 心跳自身失败绝不再抛（不能掩盖原始异常）
        print(f"[Heartbeat] 失败通知推送异常(无法触达): {pe}")


def run(cmd: str) -> int:
    """命令分发入口。Returns: 0成功 / 1班次失败(已发失败心跳) / 2用法错误"""
    if cmd not in COMMANDS:
        print("AI盘报 — 用法: python runner.py <morning|noon|evening|macro_cn|macro_us|night|weekly|site>")
        return 2
    if cmd not in _PUSH_CMDS:
        COMMANDS[cmd]()
        return 0
    try:
        COMMANDS[cmd]()
    except Exception as e:
        import traceback
        print(f"[Runner] {cmd} 执行失败: {e}", file=sys.stderr)
        traceback.print_exc()
        _push_failure_heartbeat(cmd, e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else ""))
