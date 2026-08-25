"""自选股模块 — 数据采集 + 状态标签规则引擎 + 预期温度计

设计原则（见 doc/自选股功能调研报告.md 第0章"功能宪法"）：
- 事实归规则，解释归AI，决策归用户
- 标签由规则引擎判定（默认即绿、接口失败标⚪、AI仅有升级权在Prompt层实现）
- 所有数字来自接口实测，任何一层失败只降级不阻断
- 无事则沉默：🟢股票的数据仍采集，但Prompt要求AI三行收工

接口清单（每股9个请求 + 全部股票共享1个批量行情请求）：
  行情     push2 ulist（批量）      资讯     np-listapi
  公告     np-anotice               财报预约 RPT_PUBLIC_BS_APPOIN
  分红     RPT_SHAREBONUS_DET       解禁     RPT_LIFT_STAGE
  两融     RPTA_WEB_RZRQ_GGMX       一致预期 F10 ProfitForecast
  估值历史 RPT_VALUEANALYSIS_DET    研报     reportapi
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests

CST = timezone(timedelta(hours=8))

# 国内直连，绕过可能失效的系统代理（与 research/probe_apis.py 一致）
_SESSION = requests.Session()
_SESSION.trust_env = False
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

WATCHLIST_FILE = Path(__file__).parent.parent / "data" / "watchlist.json"

DC_BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REQUEST_TIMEOUT = 10
# 东财实测无频控（21请求3.7秒），仅加礼貌性间隔
POLITE_INTERVAL = 0.1

# ── 状态标签 ──
LABEL_RED = "🔴重大变化"
LABEL_YELLOW = "🟡有一事需关注"
LABEL_GREEN = "🟢无重大变化"
LABEL_WHITE = "⚪数据缺失"


@dataclass
class StockInfo:
    """单只自选股的聚合信息"""
    code: str
    name: str
    note: str = ""
    # 行情
    price: Optional[float] = None
    chg_pct: Optional[float] = None
    float_mv: Optional[float] = None        # 流通市值（元），解禁占比用
    # 事件数据（[{title, time, url}]）
    news: list = field(default_factory=list)
    announcements: list = field(default_factory=list)
    # 日历（字符串行，规则生成）
    calendar: list = field(default_factory=list)
    # 日历事件结构化镜像（[{date, kind, text}]，观察点状态机用）
    calendar_events: list = field(default_factory=list)
    # 资金
    margin_line: str = ""                   # 融资余额摘要
    # 温度计
    thermo: dict = field(default_factory=dict)
    # 研报
    research_line: str = ""
    # 标签
    label: str = LABEL_GREEN
    label_reason: str = "近48h无重要资讯、无临近节点"
    # 规则引擎中间量
    lift_ratio: Optional[float] = None       # 最近解禁市值/流通市值
    margin_chg: Optional[float] = None       # 融资余额区间变化%
    min_event_days: Optional[int] = None     # 最近日历节点倒计时（天）
    # 失败记录（接口名列表）
    failures: list = field(default_factory=list)


# ═══════════════ 基础请求 ═══════════════

def _get(url: str, params: dict = None) -> dict:
    """GET + JSON解析，失败抛异常（由调用方降级）"""
    resp = _SESSION.get(url, params=params, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _dc(report_name: str, code: str, page_size: int = 50) -> list:
    """datacenter 通用查询。注意：不传 sortColumns（实测传错列名报9501）"""
    data = _get(DC_BASE, params={
        "reportName": report_name, "columns": "ALL",
        "filter": f'(SECURITY_CODE="{code}")', "pageSize": page_size,
        "source": "WEB", "client": "WEB",
    })
    return ((data.get("result") or {}).get("data")) or []


def _safe(fetch_fn, stock: StockInfo, source: str):
    """执行单接口采集，失败记入 failures 并返回 None（降级不阻断）"""
    try:
        time.sleep(POLITE_INTERVAL)
        return fetch_fn(stock)
    except Exception as e:
        print(f"[Watchlist] {stock.code} {source} 获取失败(降级): {type(e).__name__}: {str(e)[:80]}")
        stock.failures.append(source)
        return None


def _secid(code: str) -> str:
    return f"1.{code}" if code.startswith(("6", "9", "5")) else f"0.{code}"


def _mktsuffix(code: str) -> str:
    return ("SH" if code.startswith(("6", "9", "5")) else "SZ") + code


def _parse_dt(s: str) -> Optional[datetime]:
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


# ═══════════════ 各数据源采集 ═══════════════

def fetch_quotes_batch(stocks: list[StockInfo]) -> None:
    """批量行情：全部股票一次请求。东财ulist为主，腾讯行情为备胎。

    东财 push2 实测时通时断（直连随机被重置，且收盘后偶发返回上一交易日
    缓存数据）；腾讯 qt.gtimg.cn 国内直连稳定、带行情时间戳，作兜底。
    """
    try:
        _quotes_eastmoney(stocks)
        if all(s.price is not None for s in stocks):
            return
    except Exception as e:
        print(f"[Watchlist] 东财行情失败: {type(e).__name__}: {str(e)[:80]}")
    try:
        _quotes_tencent(stocks)
    except Exception as e:
        print(f"[Watchlist] 腾讯行情备胎也失败: {type(e).__name__}: {str(e)[:80]}")
        for s in stocks:
            if s.price is None:
                s.failures.append("行情")


def _quotes_eastmoney(stocks: list[StockInfo]) -> None:
    """东财批量行情（裸requests，fetcher同款；trust_env=False的session会被重置）"""
    last_err = None
    for attempt in range(3):
        try:
            secids = ",".join(_secid(s.code) for s in stocks)
            data = requests.get(
                "https://push2.eastmoney.com/api/qt/ulist.np/get",
                params={"fltt": 2, "secids": secids, "fields": "f2,f3,f12,f14,f21"},
                timeout=REQUEST_TIMEOUT,
            ).json()
            by_code = {d["f12"]: d for d in (data.get("data") or {}).get("diff") or []}
            for s in stocks:
                d = by_code.get(s.code)
                if not d:
                    continue
                s.price = d.get("f2")
                s.chg_pct = d.get("f3")
                s.float_mv = d.get("f21")      # 流通市值（元）
            return
        except Exception as e:
            last_err = e
            if attempt < 2:
                print(f"[Watchlist] 东财行情失败(第{attempt + 1}次): {str(e)[:60]}，重试...")
                time.sleep(2)
    raise last_err


def _quotes_tencent(stocks: list[StockInfo]) -> None:
    """腾讯行情备胎。返回GBK文本 v_sh600519="1~名称~代码~现价~昨收~...
    字段索引：3=现价 32=涨跌幅% 44=流通市值(亿)。失败的不覆盖已成功的。
    """
    print("[Watchlist] 启用腾讯行情备胎")
    codes = ",".join(_mktsuffix(s.code).lower() for s in stocks)
    resp = requests.get(f"https://qt.gtimg.cn/q={codes}", timeout=REQUEST_TIMEOUT)
    resp.encoding = "gbk"
    for line in resp.text.strip().split(";"):
        line = line.strip()
        if "=" not in line:
            continue
        parts = line.split("=", 1)[1].strip('"').split("~")
        if len(parts) < 45:
            continue
        code = parts[2]
        for s in stocks:
            if s.code == code and s.price is None:
                try:
                    s.price = float(parts[3])
                    s.chg_pct = float(parts[32])
                    s.float_mv = float(parts[44]) * 1e8   # 亿 → 元
                except (ValueError, IndexError):
                    pass
    for s in stocks:
        if s.price is None:
            s.failures.append("行情")


def fetch_news(s: StockInfo, hours: int = 48) -> list:
    """个股资讯流，标题含股票名/代码且在时间窗内（过滤大盘泛资讯）"""
    data = _get("https://np-listapi.eastmoney.com/comm/web/getListInfo", params={
        "cfh": 1, "client": "web", "mTypeAndCode": _secid(s.code),
        "type": 1, "pageSize": 20,
    })
    items = (data.get("data") or {}).get("list") or []
    cutoff = datetime.now(CST) - timedelta(hours=hours)
    out = []
    for it in items:
        title = it.get("Art_Title", "")
        if s.name not in title and s.code not in title:
            continue                       # 与个股无关的泛资讯
        dt = _parse_dt(it.get("Art_ShowTime", ""))
        if not dt or dt < cutoff.replace(tzinfo=None):
            continue
        out.append({
            "title": title,
            "time": str(it.get("Art_ShowTime", ""))[:16],
            "url": it.get("Art_Url", ""),
        })
    return out[:8]                         # 最多8条，控token


def fetch_announcements(s: StockInfo, days: int = 10) -> list:
    """近10天公告（东财），详情页URL为固定模式拼接（已实测验证）。
    10天：中报/年报发布后资讯余波持续一周以上，7天窗口会把
    刚出窗的定期报告切掉（实测茅台8/15半年报在8/23刚好出窗）"""
    data = _get("https://np-anotice-stock.eastmoney.com/api/security/ann", params={
        "sr": -1, "page_size": 15, "page_index": 1, "ann_type": "A", "stock_list": s.code,
    })
    items = (data.get("data") or {}).get("list") or []
    cutoff = datetime.now(CST).replace(tzinfo=None) - timedelta(days=days)
    out = []
    for it in items:
        dt = _parse_dt(it.get("notice_date", ""))
        if not dt or dt < cutoff:
            continue
        art = it.get("art_code", "")
        out.append({
            "title": it.get("title", ""),
            "date": str(it.get("notice_date", ""))[:10],
            "url": f"https://data.eastmoney.com/notices/detail/{s.code}/{art}.html",
        })
    return out[:8]


def fetch_calendar(s: StockInfo) -> list:
    """未来日历：财报预约(未披露) / 除权除息 / 解禁（180天内）"""
    today = datetime.now(CST).replace(tzinfo=None)
    horizon = today + timedelta(days=180)
    lines = []

    # 财报预约披露
    for row in _dc("RPT_PUBLIC_BS_APPOIN", s.code, page_size=10):
        if str(row.get("IS_PUBLISH")) == "1":
            continue
        dt = _parse_dt(row.get("APPOINT_PUBLISH_DATE", ""))
        if dt and today <= dt <= horizon:
            days = (dt - today).days
            text = f"📅 {dt:%m-%d} {row.get('REPORT_TYPE_NAME','财报')}披露（还有{days}天）"
            lines.append((dt, text))
            s.calendar_events.append({"date": dt.strftime("%Y-%m-%d"), "kind": "财报披露", "text": text})
            break                            # 只取最近一个未披露节点

    # 分红除权除息
    for row in _dc("RPT_SHAREBONUS_DET", s.code, page_size=30):
        dt = _parse_dt(row.get("EX_DIVIDEND_DATE", ""))
        if dt and today <= dt <= horizon:
            days = (dt - today).days
            profile = row.get("IMPL_PLAN_PROFILE") or ""
            text = f"📅 {dt:%m-%d} 除权除息（还有{days}天）{profile[:30]}"
            lines.append((dt, text))
            s.calendar_events.append({"date": dt.strftime("%Y-%m-%d"), "kind": "除权除息", "text": text})
            break

    # 限售解禁
    for row in _dc("RPT_LIFT_STAGE", s.code, page_size=100):
        dt = _parse_dt(row.get("FREE_DATE", ""))
        if dt and today <= dt <= horizon:
            days = (dt - today).days
            mv = row.get("LIFT_MARKET_CAP")
            ratio = ""
            if mv and s.float_mv:
                s.lift_ratio = mv / s.float_mv
                ratio = f"，约占流通市值{s.lift_ratio * 100:.1f}%"
            text = f"📅 {dt:%m-%d} 限售解禁（还有{days}天）{ratio}"
            lines.append((dt, text))
            s.calendar_events.append({"date": dt.strftime("%Y-%m-%d"), "kind": "限售解禁", "text": text})
            break

    lines.sort(key=lambda x: x[0])
    if lines:
        s.min_event_days = (lines[0][0] - today).days
    return [text for _, text in lines[:4]]


def fetch_margin(s: StockInfo) -> str:
    """融资余额及5日变化。

    注意：该报表不传排序时默认从最旧翻页（实测拿到2021年数据），
    必须显式 sortColumns=DATE&sortTypes=-1（第一轮探测已验证此报表支持）。
    返回按日期倒序，首条最新。
    """
    data = _get(DC_BASE, params={
        "reportName": "RPTA_WEB_RZRQ_GGMX", "columns": "ALL",
        "filter": f'(scode="{s.code}")', "pageSize": 6,
        "sortColumns": "DATE", "sortTypes": "-1",
        "source": "WEB", "client": "WEB",
    })
    rows = ((data.get("result") or {}).get("data")) or []
    if not rows:
        return ""
    latest = rows[0]
    line = f"融资余额{latest.get('RZYE', 0) / 1e8:.1f}亿（{str(latest.get('DATE',''))[:10]}）"
    if len(rows) >= 2:
        oldest = rows[-1]
        chg = (latest["RZYE"] - oldest["RZYE"]) / oldest["RZYE"] * 100
        line += f"，近{len(rows)-1}日{'+' if chg >= 0 else ''}{chg:.1f}%"
        s.margin_chg = chg
    return line


def fetch_research(s: StockInfo) -> str:
    """近30天研报（最新2篇：标题/机构/评级/今年EPS预测）"""
    begin = (datetime.now(CST) - timedelta(days=30)).strftime("%Y-%m-%d")
    end = datetime.now(CST).strftime("%Y-%m-%d")
    data = _get("https://reportapi.eastmoney.com/report/list", params={
        "industryCode": "*", "pageSize": 2, "industry": "*", "rating": "*",
        "ratingChange": "*", "beginTime": begin, "endTime": end,
        "pageNo": 1, "qType": 0, "code": s.code,
    })
    items = data.get("data") or []
    parts = []
    for it in items:
        eps = it.get("predictThisYearEps") or ""
        eps_s = f"，今年EPS预测{eps}" if eps else ""
        parts.append(f"《{it.get('title','')}》{it.get('orgSName','')}·{it.get('emRatingName','')}{eps_s}")
    return "；".join(parts)


def fetch_thermometer(s: StockInfo) -> dict:
    """预期温度计：①锚 ②修正 ③对照(留AI引用业绩数据) ④估值分位 + 成色"""
    thermo = {"anchor": None, "revision": None, "percentile": None,
              "quality": "", "matrix": ""}

    # ── ①锚 + ②修正：F10盈利预测聚合 ──
    try:
        data = _get(f"https://emweb.securities.eastmoney.com/PC_HSF10/ProfitForecast/PageAjax",
                    params={"code": _mktsuffix(s.code)})
        year_now = datetime.now(CST).year
        # jgyc: 一行=一个聚合口径含4年（找"近六月平均"行）
        eps_anchor = pe_anchor = None
        for row in (data.get("jgyc") or []):
            if row.get("ORG_NAME_ABBR") == "近六月平均" or len(data.get("jgyc") or []) == 1:
                for i in range(1, 5):
                    if row.get(f"YEAR{i}") == year_now and str(row.get(f"YEAR_MARK{i}")) == "E":
                        eps_anchor = row.get(f"EPS{i}")
                        pe_anchor = row.get(f"PE{i}")
                        break
            if eps_anchor:
                break
        # yctj_list: 按年的本月/上月EPS与样本数
        count = None
        for row in (data.get("yctj_list") or []):
            if row.get("YEAR") == year_now and str(row.get("YEAR_MARK")) == "E":
                count = row.get("EPS_COUNT")
                if row.get("EPS") and row.get("EPS_LASTMONTHS"):
                    rev = (row["EPS"] - row["EPS_LASTMONTHS"]) / row["EPS_LASTMONTHS"] * 100
                    thermo["revision"] = round(rev, 2)
                if eps_anchor is None:
                    eps_anchor = row.get("EPS")
                break
        if eps_anchor:
            thermo["anchor"] = {
                "eps": round(eps_anchor, 2), "pe": round(pe_anchor, 1) if pe_anchor else None,
                "count": count,
            }
            # 成色分级（报告§4.4）
            if count is None or count < 3:
                thermo["quality"] = "低成色（样本不足），仅供参考，禁止定量超/低预期结论"
            elif count < 10:
                thermo["quality"] = "中成色，结论措辞需带'参考'"
            else:
                thermo["quality"] = f"高成色（{int(count)}家机构）"
        else:
            thermo["quality"] = "无锚（该股无机构盈利预测），走免锚模式，禁止编造预期"
    except Exception:
        thermo["quality"] = "预期数据缺失"

    # ── ④估值分位：近5年PE_TTM百分位 ──
    try:
        rows = _dc("RPT_VALUEANALYSIS_DET", s.code, page_size=1300)
        pes = [r["PE_TTM"] for r in rows if r.get("PE_TTM") is not None]
        if len(pes) >= 250 and s.price:
            latest = pes[0]                # 接口按日期倒序，首条最新
            below = sum(1 for p in pes if p <= latest)
            thermo["percentile"] = round(below / len(pes) * 100)
    except Exception:
        pass

    # ── 矩阵落点（②修正 × ④分位，规则判定，报告§4.2） ──
    rev, pct = thermo["revision"], thermo["percentile"]
    if rev is not None and pct is not None:
        flat = abs(rev) < 1.0
        low = pct < 30
        high = pct > 70
        if flat:
            zone = "低分位·预期平稳（便宜但缺催化剂）" if low else \
                   "高分位·预期平稳（已充分定价）" if high else "预期平稳"
        elif rev > 0:
            zone = "上修×低分位=预期差黄金区" if low else \
                   "上修×高分位=预期改善已定价，追高性价比低" if high else "上修中"
        else:
            zone = "下修×低分位=疑似价值陷阱（便宜或因市场提前知道要坏）" if low else \
                   "下修×高分位=戴维斯双杀风险区" if high else "下修中"
        thermo["matrix"] = zone
    return thermo


# ═══════════════ 状态标签规则引擎 ═══════════════

def _apply_label_rules(s: StockInfo) -> None:
    """规则主裁判：数值阈值+关键词触发；默认即绿。

    MVP简化说明（试运行后校准）：
    - 业绩类：预亏/预减/大幅下降标题→🔴；其他业绩预告/快报→🟡
      （"隐含增速vs一致预期偏差>10%"需预告数值，标题未必含数，MVP用关键词近似）
    - 解禁占比需流通市值，行情接口缺f21时跳过比例判断
    """
    # ⚪：事件雷达三源（资讯/公告/行情）任一失败 → 无法确认"有没有事"
    critical = [f for f in s.failures if f in ("资讯", "公告", "行情")]
    if critical:
        s.label = LABEL_WHITE
        s.label_reason = f"{'/'.join(critical)}接口异常，无法确认有无事项，建议自查"
        return

    titles = [n["title"] for n in s.news] + [a["title"] for a in s.announcements]
    joined = " ".join(titles)

    # ── 🔴 规则 ──
    if any(k in joined for k in ("预亏", "预减", "业绩大幅下降", "立案", "退市风险",
                                 "警示函", "暂停上市")):
        s.label, s.label_reason = LABEL_RED, "出现业绩预警/监管风险类事项"
        return
    if any(k in joined for k in ("停牌", "复牌", "并购", "重组", "要约收购", "重大合同")):
        s.label, s.label_reason = LABEL_RED, "出现停复牌/并购重组/重大合同类事项"
        return
    if (s.lift_ratio is not None and s.lift_ratio > 0.03
            and s.min_event_days is not None and s.min_event_days <= 7):
        s.label, s.label_reason = LABEL_RED, "7日内解禁且解禁市值占流通市值超3%"
        return

    # ── 🟡 规则 ──
    if any(k in joined for k in ("业绩预增", "业绩预告", "快报", "业绩说明会",
                                 "股东大会", "分红", "回购", "增持", "减持",
                                 "中标", "签约", "合作协议")):
        s.label = LABEL_YELLOW
        s.label_reason = "有需关注的公告/资讯（业绩或经营类事项）"
        return
    if s.min_event_days is not None and s.min_event_days <= 7:
        s.label = LABEL_YELLOW
        s.label_reason = f"临近重要日历节点（还有{s.min_event_days}天）"
        return
    if s.margin_chg is not None and abs(s.margin_chg) > 5:
        s.label = LABEL_YELLOW
        s.label_reason = f"融资余额近期{'+' if s.margin_chg > 0 else ''}{s.margin_chg:.1f}%异动"
        return
    if s.thermo.get("revision") is not None and abs(s.thermo["revision"]) > 3:
        s.label = LABEL_YELLOW
        s.label_reason = f"一致预期本月修正{s.thermo['revision']:+.1f}%"
        return
    if s.news:
        s.label, s.label_reason = LABEL_YELLOW, f"近48h有{len(s.news)}条相关资讯"
        return

    # ── 🟢 默认 ──
    s.label = LABEL_GREEN
    s.label_reason = "近48h无重要资讯、无临近节点"


# ═══════════════ 对外主入口 ═══════════════

def load_watchlist() -> list[dict]:
    """读取自选股配置；文件缺失/为空/格式错 → 返回[]（功能整体跳过）"""
    if not WATCHLIST_FILE.exists():
        return []
    try:
        items = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
        return [it for it in items if it.get("code")]
    except (json.JSONDecodeError, OSError) as e:
        print(f"[Watchlist] 配置读取失败（跳过自选股功能）: {e}")
        return []


def collect_all() -> list[StockInfo]:
    """采集全部自选股数据并打标签"""
    items = load_watchlist()
    if not items:
        return []
    stocks = [StockInfo(code=str(it["code"]).zfill(6), name=it.get("name", ""),
                        note=it.get("note", "")) for it in items]
    print(f"[Watchlist] 开始采集 {len(stocks)} 只自选股...")

    fetch_quotes_batch(stocks)
    for s in stocks:
        s.news = _safe(fetch_news, s, "资讯") or []
        s.announcements = _safe(fetch_announcements, s, "公告") or []
        s.calendar = _safe(fetch_calendar, s, "日历") or []
        s.margin_line = _safe(fetch_margin, s, "两融") or ""
        s.research_line = _safe(fetch_research, s, "研报") or ""
        s.thermo = _safe(fetch_thermometer, s, "预期") or {}
        _apply_label_rules(s)
        print(f"[Watchlist] {s.name}({s.code}): {s.label}｜{s.label_reason}")
    return stocks


def format_watchlist_prompt(stocks: list[StockInfo]) -> str:
    """拼装给AI的数据块（含标签、链接、温度计、成色）"""
    blocks = []
    for s in stocks:
        lines = [f"#### {s.name}({s.code}) ｜ "
                 f"{f'现价{s.price}({s.chg_pct:+.2f}%)' if s.price else '行情缺失'} ｜ "
                 f"{s.label}｜规则理由:{s.label_reason}"]
        if s.note:
            lines.append(f"用户备注: {s.note}")
        if s.news:
            lines.append("近48h资讯:")
            lines += [f"- [{n['title']}]({n['url']}) ({n['time']})" for n in s.news]
        if s.announcements:
            lines.append("近7天公告:")
            lines += [f"- [{a['title']}]({a['url']}) ({a['date']})" for a in s.announcements]
        if s.calendar:
            lines.append("未来日历:")
            lines += [f"- {c}" for c in s.calendar]
        if s.margin_line:
            lines.append(f"资金: {s.margin_line}")
        if s.research_line:
            lines.append(f"近期研报: {s.research_line}")
        t = s.thermo
        if t:
            parts = []
            if t.get("anchor"):
                a = t["anchor"]
                pe = f"，对应PE约{a['pe']}倍" if a.get("pe") else ""
                parts.append(f"一致预期锚: 今年EPS≈{a['eps']}元{pe}（{t['quality']}）")
            else:
                parts.append(f"预期锚: {t['quality']}")
            if t.get("revision") is not None:
                parts.append(f"预期修正: 本月{t['revision']:+.2f}%")
            if t.get("percentile") is not None:
                parts.append(f"估值分位: PE处近5年{t['percentile']}%分位")
            if t.get("matrix"):
                parts.append(f"矩阵落点: {t['matrix']}")
            if parts:
                lines.append("🌡 " + "；".join(parts))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def format_close_watchlist_prompt(stocks: list[StockInfo], pushed_urls: set) -> str:
    """晚报自选股收盘段：收盘价 + 相对晨报的新增事件（增量，晨报已推的只计数）。

    Args:
        pushed_urls: 今晨已推送的资讯/公告URL集合（today.json morning.pushed_event_ids）
    """
    blocks = []
    for s in stocks:
        price = f"收盘{s.price}({s.chg_pct:+.2f}%)" if s.price is not None else "收盘行情缺失"
        lines = [f"#### {s.name}({s.code}) ｜ {price} ｜ {s.label}"]
        new_items = []
        seen = 0
        for n in s.news:
            if n.get("url") in pushed_urls:
                seen += 1
            else:
                new_items.append(f"- [资讯:{n['title']}]({n['url']}) ({n.get('time', '')})")
        for a in s.announcements:
            if a.get("url") in pushed_urls:
                seen += 1
            else:
                new_items.append(f"- [公告:{a['title']}]({a['url']}) ({a.get('date', '')})")
        if new_items:
            lines.append("今日新增:")
            lines += new_items
        elif seen:
            lines.append(f"今日无新增（晨报已解读{seen}条）")
        else:
            lines.append("今日无事件")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def label_summary(stocks: list[StockInfo]) -> str:
    """推送标题用的标签聚合（如：茅台🟡 宁德🟢）"""
    def short(s):
        for tag, emoji in ((LABEL_RED, "🔴"), (LABEL_YELLOW, "🟡"),
                           (LABEL_GREEN, "🟢"), (LABEL_WHITE, "⚪")):
            if s.label == tag:
                return f"{s.name}{emoji}"
        return s.name
    flagged = [short(s) for s in stocks if s.label != LABEL_GREEN]
    if not flagged:
        return "自选全🟢"
    quiet = len(stocks) - len(flagged)
    return "自选: " + " ".join(flagged) + (f" 其余🟢({quiet})" if quiet else "")
