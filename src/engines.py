"""规则引擎层 — 情绪刻度 / 判断记分牌 / 观察点状态机（纯规则，无AI）

设计约定：
  - 输入全部来自已核实的采集数据（market_close/watchlist/state）
  - 数字代码算话：刻度、✓/❌、⏳/✓/✗ 全部由规则判定，AI只解释不判定
  - 任一输入缺失只降级该项（权重重归一化 / 该条不记分），不阻断整体
"""

from datetime import datetime, timedelta

from src import state
from src.utils import CST

TODAY = lambda: datetime.now(CST).strftime("%Y-%m-%d")  # noqa: E731


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


# ═══════════════ 情绪刻度 ═══════════════

# 各输入权重（可用项之间重归一化）
_W = {"breadth": 0.45, "limit": 0.25, "blown": 0.15, "vol": 0.15}


def sentiment_gauge(p: dict) -> dict:
    """收盘全景 → 0-100情绪分 + 刻度条 + 历史对照。

    构成（均为规则计算）：
      breadth  (上涨-下跌)/(上涨+下跌)      → ±100
      limit    (涨停-跌停)/(涨停+跌停)       → ±100
      blown    炸板率反向（0%炸板=+100）    → ±100
      vol      量能环比，方向随宽度正负放大  → ±100
    score = 50 + 0.5 × 加权和（权重和=1 → score∈[0,100]）
    """
    parts, used_w = {}, 0.0

    b = p.get("breadth")
    if b and (b.get("up", 0) + b.get("down", 0)) > 0:
        parts["breadth"] = (b["up"] - b["down"]) / (b["up"] + b["down"]) * 100
        used_w += _W["breadth"]

    lim = p.get("limits") or {}
    lu, ld = lim.get("limit_up"), lim.get("limit_down")
    if lu is not None and ld is not None:
        parts["limit"] = (lu - ld) / max(lu + ld, 1) * 100
        used_w += _W["limit"]

    blown = lim.get("blown")
    if blown is not None and lu is not None and (blown + lu) > 0:
        parts["blown"] = (1 - 2 * blown / (blown + lu)) * 100
        used_w += _W["blown"]

    t = p.get("turnover") or {}
    chg = t.get("chg_pct")
    if chg is None:                            # 采集层无昨值 → 查昨日归档
        chg = _vol_from_history(t.get("today_yi"))
    if chg is not None:
        direction = 1 if parts.get("breadth", 0) >= 0 else -1   # 量价配合：宽度定方向
        parts["vol"] = _clip(chg / 20, -1, 1) * 100 * direction
        used_w += _W["vol"]

    if not parts:
        return {"score": None, "label": "数据不足", "bar": "",
                "degraded": list(p.get("failures", []))}

    weighted = sum(_W[k] * v for k, v in parts.items()) / used_w
    score = int(_clip(round(50 + weighted / 2), 0, 100))
    label = ("冰点" if score < 20 else "偏冷" if score < 40 else
             "中性" if score < 60 else "偏热" if score < 80 else "亢奋")
    n = round(score / 5)
    return {
        "score": score,
        "label": label,
        "bar": "▓" * n + "░" * (20 - n),
        "parts": {k: round(v, 1) for k, v in parts.items()},
        "degraded": list(p.get("failures", [])),
    }


# ── 情绪历史（自积累，跑满10天启用对照，之前显示"积累中"） ──

def archive_sentiment(gauge: dict, p: dict) -> str:
    """归档当日情绪 → 返回对照文案（今日分 vs 近期均值）"""
    hist = state._load_json("sentiment_history.json", {})
    hist[TODAY()] = {
        "score": gauge.get("score"),
        "up": (p.get("breadth") or {}).get("up"),
        "down": (p.get("breadth") or {}).get("down"),
        "turnover_yi": (p.get("turnover") or {}).get("today_yi"),
    }
    # 只保留最近90天
    keys = sorted(hist.keys())[-90:]
    hist = {k: hist[k] for k in keys}
    state._save_json("sentiment_history.json", hist)

    scores = [v["score"] for d, v in hist.items() if v.get("score") is not None and d != TODAY()]
    if gauge.get("score") is None:
        return "今日数据不足，不记刻度"
    if len(scores) < 10:
        return f"情绪历史积累中（第{len(scores) + 1}天，满10天起提供均值对照）"
    avg = sum(scores[-20:]) / min(len(scores), 20)
    delta = gauge["score"] - avg
    return f"近20日均值{avg:.0f}，今日{'高于' if delta > 3 else '低于' if delta < -3 else '持平于'}均值（{delta:+.0f}）"


def _vol_from_history(today_yi) -> float | None:
    """量能环比的备源：昨日归档成交额"""
    if not today_yi:
        return None
    yesterday = (datetime.now(CST) - timedelta(days=1)).strftime("%Y-%m-%d")
    hist = state._load_json("sentiment_history.json", {})
    prev = hist.get(yesterday, {}).get("turnover_yi")
    if prev:
        return (today_yi / prev - 1) * 100
    return None


def format_sentiment_prompt(gauge: dict, history_line: str) -> str:
    """情绪刻度 → AI prompt数据块"""
    if gauge.get("score") is None:
        return "## 情绪刻度\n\n⚠️ 今日输入数据不足，无刻度（如实说明，不猜测）"
    parts = gauge.get("parts", {})
    part_desc = {
        "breadth": "宽度", "limit": "涨跌停", "blown": "封板质量", "vol": "量能配合",
    }
    detail = "、".join(f"{part_desc[k]}{v:+.0f}" for k, v in parts.items())
    return (
        f"## 情绪刻度（规则计算，数值不可修改）\n\n"
        f"**今日读数：{gauge['score']}/100（{gauge['label']}）**\n\n"
        f"`{gauge['bar']}`\n\n"
        f"分项：{detail}\n\n"
        f"历史对照：{history_line}"
    )


# ═══════════════ 判断记分牌 ═══════════════

def _score_direction(direction: str, chg: float) -> str:
    """晨报方向 vs 上证实际涨跌幅 → ✓/部分/❌

    统一规则：
      方向对（过该档位的兑现线）      → ✓
      方向错但仍落在±0.5%持平区       → 部分（市场未表态，错得有限）
      方向错且越过持平区              → ❌
    兑现线：看多≥+1%，看偏多≥0，看偏空≤0，看空≤-1%，看平|chg|≤0.5。
    """
    if direction == "看平":
        return "✓" if abs(chg) <= 0.5 else "❌"
    bullish = direction in ("看多", "看偏多")
    strong = direction in ("看多", "看空")
    ok_line = (1.0 if bullish else -1.0) if strong else 0.0
    correct = chg >= ok_line if bullish else chg <= ok_line
    if correct:
        return "✓"
    return "部分" if abs(chg) <= 0.5 else "❌"


def _match_board(name: str, by_name: dict) -> float | None:
    """板块名 → 全行业板块索引匹配（精确 > 包含 > 被包含），返回涨跌幅"""
    if not by_name:
        return None
    if name in by_name:
        return by_name[name]
    for bname, chg in by_name.items():
        if name in bname or bname in name:
            return chg
    return None


# 要闻关键词 → 板块候选（晨报要闻惯用语 vs 东财板块名的同义桥，按需扩充）
_NEWS_SYNONYMS = {
    "金价": ["黄金", "贵金属"], "黄金": ["黄金", "贵金属"],
    "油价": ["原油", "石油", "油气"], "原油": ["原油", "石油", "油气"],
    "白酒": ["白酒"], "半导体": ["半导体", "芯片"], "芯片": ["半导体", "芯片"],
    "锂": ["锂", "能源金属"], "地产": ["房地产", "房产开发"], "银行": ["银行"],
    "光伏": ["光伏设备"], "风电": ["风电设备"], "医药": ["医药", "化学制剂"],
}

# 常见地域前缀（全名→简称剥离用，如"贵州茅台"→"茅台"）
_REGION_PREFIXES = ("中国", "贵州", "北京", "上海", "深圳", "江苏", "浙江", "山东",
                    "四川", "河南", "湖北", "湖南", "广东", "福建", "安徽", "河北",
                    "山西", "陕西", "云南", "广西", "江西", "辽宁", "吉林", "黑龙江",
                    "甘肃", "青海", "海南", "内蒙古", "宁夏", "新疆", "西藏", "天津", "重庆")


def _short_name(name: str) -> str:
    """股票全名 → 常用简称（剥离地域前缀）"""
    for p in _REGION_PREFIXES:
        if name.startswith(p) and len(name) > len(p):
            return name[len(p):]
    return name


def _match_title_instrument(title: str, stock_by_name: dict, by_name: dict):
    """要闻标题 → 对得上的品种（自选股简称 > 同义词板块 > 标题原文板块）。

    Returns: (匹配名, 涨跌幅) 或 (None, None)。对不上不记分（诚实降级）。
    """
    for name, chg_pct in stock_by_name.items():
        if not name or chg_pct is None:
            continue
        if name in title or _short_name(name) in title:
            return name, chg_pct
    for keyword, candidates in _NEWS_SYNONYMS.items():
        if keyword not in title:
            continue
        for cand in candidates:
            chg = _match_board(cand, by_name)
            if chg is not None:
                return cand, chg
    chg = _match_board(title, by_name)
    if chg is not None:
        return "板块", chg
    return None, None


def scoreboard(morning: dict, p: dict, stocks: list) -> dict:
    """晨报预判 vs 收盘实际 → 逐条记分。

    三层：
      direction  晨报方向 vs 上证涨跌幅（创业板并列展示）
      sectors    关注板块 vs 板块实际涨跌（>+0.5%兑现 / ±0.5%平淡 / <-0.5%落空）
      news_marks 要闻方向 vs 能对上的品种（板块/自选股），对不上的不记分
    """
    out = {"status": "ok", "direction": None, "sectors": [], "news_marks": []}
    judgment = (morning or {}).get("judgment") or {}
    if not judgment or not judgment.get("direction"):
        out["status"] = "missing"
        return out

    sh = p.get("indices", {}).get("上证指数", {})
    cyb = p.get("indices", {}).get("创业板指", {})
    chg = sh.get("chg_pct")
    if chg is None:
        out["status"] = "no_close"
        return out

    direction = judgment.get("direction", "看平")
    out["direction"] = {
        "judgment": direction,
        "actual": f"上证{chg:+.2f}%" + (f" / 创业板{cyb['chg_pct']:+.2f}%" if cyb.get("chg_pct") is not None else ""),
        "result": _score_direction(direction, chg),
    }

    by_name = (p.get("boards") or {}).get("by_name") or {}
    for sec in judgment.get("sectors", []):
        actual = _match_board(sec, by_name)
        if actual is None:
            out["sectors"].append({"name": sec, "actual": None, "result": "—未对上板块"})
        else:
            result = "✓" if actual > 0.5 else ("❌" if actual < -0.5 else "○平淡")
            out["sectors"].append({"name": sec, "actual": f"{actual:+.2f}%", "result": result})

    stock_by_name = {s.name: (s.chg_pct if s.chg_pct is not None else None) for s in stocks}
    for m in judgment.get("news_marks", [])[:8]:
        title, mark = m.get("title", ""), m.get("mark", "")
        if mark == "中性" or not title:
            out["news_marks"].append({"title": title, "result": "—中性不记分"})
            continue
        matched_name, actual = _match_title_instrument(title, stock_by_name, by_name)
        if matched_name is None:
            out["news_marks"].append({"title": title, "mark": mark, "result": "—未对上品种，不记分"})
            continue
        bullish = (mark == "利好")
        moved = actual > 0.5 if bullish else actual < -0.5
        opposite = actual < -0.5 if bullish else actual > 0.5
        result = "✓" if moved else ("❌" if opposite else "○平淡")
        out["news_marks"].append({"title": title, "mark": mark, "vs": f"{matched_name}{actual:+.2f}%",
                                  "result": result})
    return out


def format_scoreboard_prompt(sb: dict) -> str:
    """记分牌 → AI prompt数据块"""
    if sb["status"] != "ok":
        note = {"missing": "今日晨报无预判快照（JSON块缺失或晨报未推送）",
                "no_close": "今日未取到指数收盘数据"}[sb["status"]]
        return f"## 判断记分牌\n\n⚠️ {note}，今日不记分。复盘照常，但请如实说明无记分。"

    lines = ["## 判断记分牌（✓/❌由规则判定，不可修改）", ""]
    d = sb["direction"]
    lines.append(f"- 方向预判「{d['judgment']}」 vs 实际{d['actual']} → **{d['result']}**")
    if sb["sectors"]:
        for s in sb["sectors"]:
            actual = s["actual"] if s["actual"] else ""
            lines.append(f"- 关注板块「{s['name']}」{actual} → {s['result']}")
    if sb["news_marks"]:
        for m in sb["news_marks"]:
            vs = f"（{m['vs']}）" if m.get("vs") else ""
            mark = f"[{m.get('mark', '')}]" if m.get("mark") else ""
            lines.append(f"- 要闻「{m['title']}」{mark}{vs} → {m['result']}")
    return "\n".join(lines)


def scoreboard_summary(sb: dict) -> str:
    """记分牌 → 推送标题短语（如 '记分✓' / '记分部分' / '无快照'）"""
    if sb["status"] != "ok":
        return "无记分"
    total, hit = 0, 0
    if sb["direction"]:
        total += 1
        if sb["direction"]["result"] == "✓":
            hit += 1
    for s in sb["sectors"]:
        total += 1
        hit += 1 if s["result"] == "✓" else 0
    if total == 0:
        return "无记分"
    if hit == total:
        return f"记分✓{hit}/{total}"
    if hit == 0:
        return f"记分❌0/{total}"
    return f"记分{hit}/{total}"


# ═══════════════ 宏观三对照（数据解读栏目用） ═══════════════

def percentile_of(latest: float, history: list) -> float | None:
    """最新值在历史序列（含自身）中的分位（0-100）。

    history: [(period, value)...] 不含最新期亦可（历史即对照集）。
    """
    values = [v for _, v in history if isinstance(v, (int, float))]
    if not values or latest is None:
        return None
    below = sum(1 for v in values if v < latest)
    equal = sum(1 for v in values if v == latest)
    return round((below + equal / 2) / len(values) * 100, 1)


def streak_of(history: list, predicate: str) -> int:
    """从最新期往前数连续满足判定的月数。

    predicate: sign=与最新值同号；above50=值>50；below50=值<50。
    返回含最新期的连续月数（不足2个月返回实际月数）。
    """
    values = [v for _, v in history if isinstance(v, (int, float))]
    if not values:
        return 0
    latest = values[0]
    if predicate == "above50":
        ok = lambda v: v > 50      # noqa: E731
    elif predicate == "below50":
        ok = lambda v: v < 50      # noqa: E731
    else:                          # sign
        ok = lambda v: (v >= 0) == (latest >= 0)
    if not ok(latest):             # 最新期本身不满足（如PMI跌破荣枯线）→连续0个月
        return 0
    n = 1
    for v in values[1:]:
        if ok(v):
            n += 1
        else:
            break
    return n


def macro_compare(key: str, d: dict, predicate: str) -> dict:
    """三对照计算：实际 vs 前值 + 5年分位 + 连续月数（预期值数据源暂缺）。

    d: fetch_cn_indicator / fetch_us_indicator 的返回值
    """
    value = d.get("value")
    history = d.get("history") or []
    pre = None
    if len(history) >= 2 and isinstance(history[1][1], (int, float)):
        pre = history[1][1]
    pre_value = d.get("pre_value")            # 美国API自带；None时回退history[1]
    out = {
        "period": d.get("period", ""),
        "value": value,
        "pre": pre_value if pre_value is not None else pre,
        "percentile": percentile_of(value, [h for h in history[1:]] or history),
        "streak": streak_of(history, predicate) if predicate else None,
    }
    # 方向判定（规则口径，供AI引用）
    if value is not None and out["pre"] is not None:
        out["vs_pre"] = "高于前值" if value > out["pre"] else ("低于前值" if value < out["pre"] else "持平")
    return out


# ═══════════════ 增量事件扫描（午间/夜巡共用） ═══════════════

# 夜巡🔴关键词（命中即推紧急警报；从历史暴雷样例校准，可调）
RED_KEYWORDS = ["立案", "停牌", "预亏", "重组", "退市", "减持"]


def scan_new_events(stocks: list, pushed_urls: set, today_only: bool = True) -> list:
    """增量事件：采集结果 − 已推ID池。

    Args:
        pushed_urls: today.json pushed_event_ids 集合
        today_only: 只留今日事件（午间/夜巡语义）；False留给晚报展示昨日尾差

    Returns:
        [(StockInfo, [{type: 资讯|公告, title, url, ...}])] 只返回有新增的股票
    """
    today_dashed = TODAY()
    out = []
    for s in stocks:
        items = []
        for n in getattr(s, "news", []):
            if not n.get("url") or n["url"] in pushed_urls:
                continue
            if today_only and not str(n.get("time", "")).startswith(today_dashed):
                continue
            items.append({**n, "type": "资讯"})
        for a in getattr(s, "announcements", []):
            if not a.get("url") or a["url"] in pushed_urls:
                continue
            if today_only and a.get("date") != today_dashed:
                continue
            items.append({**a, "type": "公告"})
        if items:
            out.append((s, items))
    return out


def scan_red_alerts(new_events: list) -> list:
    """夜巡：新增事件标题 × 🔴关键词 → 命中清单

    Args:
        new_events: scan_new_events 的返回值
    Returns:
        [(StockInfo, item, 命中关键词)]
    """
    alerts = []
    for s, items in new_events:
        for it in items:
            title = it.get("title", "")
            for kw in RED_KEYWORDS:
                if kw in title:
                    alerts.append((s, it, kw))
                    break
    return alerts


def filter_flagged(new_events: list) -> list:
    """午间触发护栏：只有规则引擎定级🟡/🔴的股票可推（🟢有新增不推=分诊不推送）"""
    from src.watchlist import LABEL_RED, LABEL_YELLOW
    return [(s, items) for s, items in new_events
            if s.label in (LABEL_RED, LABEL_YELLOW)]


def format_noon_prompt(flagged: list) -> str:
    """午间快讯数据块：仅🟡/🔴股票的新增事件（三件套原料）"""
    from src.watchlist import LABEL_RED, LABEL_YELLOW
    lines = []
    for s, items in flagged:
        price = f"现价{s.price}({s.chg_pct:+.2f}%)" if s.price is not None else "行情缺失"
        lines.append(f"#### {s.name}({s.code}) ｜ {price} ｜ {s.label}｜规则理由:{s.label_reason}")
        for it in items:
            lines.append(f"- [{it['type']}:{it['title']}]({it['url']}) ({it.get('time') or it.get('date', '')})")
    return "\n\n".join(lines)


def format_alert_prompt(alerts: list) -> str:
    """夜巡警报数据块：命中的🔴事件"""
    lines = []
    for s, it, kw in alerts:
        lines.append(f"#### {s.name}({s.code}) ｜ 命中关键词「{kw}」 ｜ {s.label}")
        lines.append(f"- [{it['type']}:{it['title']}]({it['url']}) ({it.get('time') or it.get('date', '')})")
        price = f"现价{s.price}({s.chg_pct:+.2f}%)" if s.price is not None else ""
        if price:
            lines.append(f"- 收盘参考: {price}")
    return "\n\n".join(lines)


# ═══════════════ 观察点状态机 ═══════════════

# 兑现信号：节点当日公告标题包含的关键词（按kind）
_FULFILL_KEYWORDS = {
    "财报披露": ["报告", "季报", "年报", "业绩", "利润", "营收"],
    "除权除息": ["除权", "分红", "派息", "权益分派"],
    "限售解禁": [],                          # 解禁无公告，只能观察量价，不判兑现
}


def update_watchpoints(stocks: list) -> dict:
    """日历节点 → 观察点生命周期。

    ⏳挂起(未到期) → ✓兑现(节点当日出现匹配公告) / ⏳到期未现(次日晨报复核)
                   → ✗失效(过期仍无信号，移入history)
    幂等：key = 代码-日期-kind，重复扫描不重建。
    """
    today = TODAY()
    data = state.load_watchpoints()
    active, history = data.get("active", []), data.get("history", [])
    # 去重含history：已兑现/失效的节点当日扫描不得复活（同轮结算后再次调用场景）
    existing_keys = {w["key"] for w in active} | {w["key"] for w in history}
    added = []

    # 1. 扫描挂起：未来7天内的日历节点
    horizon = (datetime.now(CST) + timedelta(days=7)).strftime("%Y-%m-%d")
    for s in stocks:
        for ev in getattr(s, "calendar_events", []):
            if not (today <= ev["date"] <= horizon):
                continue
            key = f"{s.code}-{ev['date']}-{ev['kind']}"
            if key in existing_keys:
                continue
            active.append({"key": key, "stock": s.name, "code": s.code,
                           "kind": ev["kind"], "date": ev["date"],
                           "status": "⏳挂起", "created": today})
            existing_keys.add(key)
            added.append(f"{s.name}·{ev['kind']}({ev['date'][5:]})")

    # 2. 结算：到期/过期
    stock_map = {s.code: s for s in stocks}
    still_active = []
    for w in active:
        s = stock_map.get(w["code"])
        if w["date"] == today and s is not None:
            keywords = _FULFILL_KEYWORDS.get(w["kind"], [])
            hit = next((a["title"] for a in s.announcements
                        if a.get("date") == today and any(k in a["title"] for k in keywords)), None)
            if hit:
                w.update({"status": "✓兑现", "resolved": today, "evidence": hit[:40]})
                history.append(w)
                print(f"[Watchpoint] 兑现: {w['stock']} {w['kind']} — {hit[:40]}")
                continue
            w["status"] = "⏳到期未现(明日复核)"
        elif w["date"] < today:
            w.update({"status": "✗失效", "resolved": today, "evidence": "过期未见信号"})
            history.append(w)
            print(f"[Watchpoint] 失效: {w['stock']} {w['kind']} {w['date']}")
            continue
        still_active.append(w)

    # 3. 落盘（history截断100条）
    state.save_watchpoints({"active": still_active, "history": history[-100:]})
    return {"added": added, "active": still_active}


def format_watchpoints_prompt(wp: dict) -> str:
    """观察点 → AI prompt数据块"""
    lines = ["## 观察点看板（状态由规则维护，不可修改）"]
    active = wp.get("active", [])
    if not active:
        lines.append("\n- 当前无挂起观察点")
        return "\n".join(lines)
    for w in active[:10]:
        lines.append(f"- {w['status']} {w['stock']}·{w['kind']}（{w['date'][5:]}）")
    if wp.get("added"):
        lines.append(f"\n今日新增：{'、'.join(wp['added'][:5])}")
    return "\n".join(lines)
