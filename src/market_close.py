"""收盘全景采集 — 晚报数据层（纯事实，无AI）

一次 collect_close_panorama() 返回：
  indices   上证/深成/创业板 收盘价+涨跌幅（东财日K主源，腾讯备胎）
  breadth   涨跌家数（push2ex 涨跌分布直方图求和）
  limits    涨停/跌停/炸板家数（push2ex 三池）
  boards    行业板块全量涨跌（push2 clist，含top/bottom与名称索引）
  turnover  两市成交额+环比（沪+深日K成交额求和）

任何子源失败只置 None（engines 逐项降级），不抛异常不阻断晚报。
"""

import time
from datetime import datetime
from typing import Optional

import requests

from src.utils import CST

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# push2 即时站在本机直连常被重置，delay站（15分钟延迟）稳定。
# 晚报采集的全是收盘后历史数据，延迟无影响 → delay为主源、即时站为备源。
PUSH2_HOSTS = ["push2delay.eastmoney.com", "push2.eastmoney.com"]
RETRIES = 3


def _get(url: str, params: dict, name: str = "") -> Optional[dict]:
    for attempt in range(RETRIES):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt < RETRIES - 1:
                time.sleep(1 + attempt)
            else:
                print(f"[MarketClose] 请求失败(降级): {name or url} — {e}")
    return None


def _get_push2(path: str, params: dict, name: str = "") -> Optional[dict]:
    """push2 家族双host轮询（delay主源→即时站备源），各带一次重试"""
    for host in PUSH2_HOSTS:
        data = _get(f"https://{host}{path}", params, f"{name}@{host.split('.')[0]}")
        if data is not None:
            return data
    return None


# ── 指数收盘 + 量能（东财日K，一次拿到收盘价/涨跌幅/成交额/昨值） ──

_KLINE_SECTORS = [("1.000001", "上证指数"), ("0.399001", "深证成指"), ("0.399006", "创业板指")]


def _kline(secid: str) -> Optional[list[dict]]:
    """近2根日K：[{date, close, chg_pct, amount}]（amount单位:元）

    注意：东财kline字段按编号升序返回（f51,f53,f57,f59），不按请求顺序。
    """
    data = _get(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        {
            "secid": secid,
            "fields1": "f1,f2,f3",
            "fields2": "f51,f53,f57,f59",   # 日期,收盘,成交额,涨跌幅%（返回按此序）
            "klt": 101, "fqt": 1, "end": "20500101", "lmt": 2,
        },
        f"kline[{secid}]",
    )
    if not data or not data.get("data") or not data["data"].get("klines"):
        return None
    rows = []
    for line in data["data"]["klines"]:
        parts = line.split(",")
        try:
            rows.append({"date": parts[0], "close": float(parts[1]),
                         "amount": float(parts[2]), "chg_pct": float(parts[3])})
        except (ValueError, IndexError):
            continue
    return rows or None


def _tencent_index(code: str) -> Optional[dict]:
    """腾讯简版指数：收盘价/涨跌幅/成交额备胎（日K失败时保住收盘与量能）"""
    try:
        resp = requests.get(f"https://qt.gtimg.cn/q=s_{code}", headers=HEADERS, timeout=10)
        resp.encoding = "gbk"
        parts = resp.text.split("~")
        # 1~名称~代码~现价~涨跌额~涨跌幅%~成交量(手)~成交额(万元)
        return {"close": float(parts[3]), "chg_pct": float(parts[5]),
                "amount": float(parts[7]) * 1e4}   # 万→元
    except (IndexError, ValueError, requests.RequestException) as e:
        print(f"[MarketClose] 腾讯指数备胎失败({code}): {e}")
        return None


def _collect_indices() -> dict:
    """上证/深成/创业板收盘。主源日K，备胎腾讯；全失败该指数缺席。"""
    indices = {}
    for secid, name in _KLINE_SECTORS:
        rows = _kline(secid)
        if rows:
            indices[name] = {"close": rows[-1]["close"], "chg_pct": rows[-1]["chg_pct"],
                             "amount": rows[-1]["amount"], "prev_amount": rows[-2]["amount"] if len(rows) > 1 else None,
                             "source": "eastmoney"}
        else:
            tencent_code = {"上证指数": "sh000001", "深证成指": "sz399001", "创业板指": "sz399006"}[name]
            fallback = _tencent_index(tencent_code)
            if fallback:
                fallback.update({"prev_amount": None, "source": "tencent"})
                indices[name] = fallback
    return indices


# ── 涨跌家数（涨跌分布直方图） ──

def _collect_breadth() -> Optional[dict]:
    data = _get(
        "https://push2ex.eastmoney.com/getTopicZDFenBu",
        {"ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt"},
    )
    fenbu = (data or {}).get("data", {}).get("fenbu")
    if not fenbu:
        return None
    up = down = flat = 0
    for bucket in fenbu:
        for key, count in bucket.items():
            k = int(key)
            if k > 0:
                up += count
            elif k < 0:
                down += count
            else:
                flat += count
    return {"up": up, "down": down, "flat": flat}


# ── 涨停/跌停/炸板池 ──

def _pool_count(pool: str) -> Optional[int]:
    data = _get(
        f"https://push2ex.eastmoney.com/getTopic{pool}",
        {
            "ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt",
            "Pageindex": 0, "pagesize": 1, "sort": "fbt:asc",
            "date": datetime.now(CST).strftime("%Y%m%d"),
        },
    )
    d = (data or {}).get("data")
    if d is None:
        return None
    return d.get("tc", len(d.get("pool", [])))


def _collect_limits() -> dict:
    zt = _pool_count("ZTPool")
    dt = _pool_count("DTPool")
    zb = _pool_count("ZBPool")
    return {"limit_up": zt, "limit_down": dt, "blown": zb}


# ── 行业板块全量 ──

def _collect_boards() -> Optional[dict]:
    """全部行业板块（东财一二三级共~500个，翻页取全量，pz上限100）。

    粒度到三级对晚报有利：领涨领跌更具体，自选股板块对照匹配面更全。
    返回 {all: 按涨跌幅降序, by_name: {名称: 涨跌幅}}
    """
    all_boards = []
    for page in range(1, 6):                      # 5页×100 覆盖~500
        data = _get_push2(
            "/api/qt/clist/get",
            {
                "pn": page, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                "fid": "f3", "fs": "m:90+t:2+f:!50",
                "fields": "f12,f14,f3,f128",
            },
            f"行业板块p{page}",
        )
        diff = (data or {}).get("data", {}).get("diff") if data else None
        if not diff:
            break
        all_boards += [{"name": r.get("f14"), "chg_pct": r.get("f3"), "leader": r.get("f128", "")}
                       for r in diff if isinstance(r.get("f3"), (int, float))]
        if len(diff) < 100:
            break
    if not all_boards:
        return None
    all_boards = [b for b in all_boards if b["name"]]        # 去掉无名单元
    seen = set()
    unique = [b for b in all_boards if not (b["name"] in seen or seen.add(b["name"]))]
    unique.sort(key=lambda b: b["chg_pct"], reverse=True)
    return {"all": unique, "by_name": {b["name"]: b["chg_pct"] for b in unique}}


# ── 汇总入口 ──

def collect_close_panorama() -> dict:
    """收盘全景。缺哪路数据对应字段为 None，failures 记录降级明细。"""
    print("[MarketClose] 采集收盘全景...")
    failures = []

    indices = _collect_indices()
    if not indices:
        failures.append("指数收盘")

    breadth = _collect_breadth()
    if not breadth:
        failures.append("涨跌家数")

    limits = _collect_limits()
    if limits["limit_up"] is None:
        failures.append("涨停池")

    boards = _collect_boards()
    if not boards:
        failures.append("行业板块")

    # 两市成交额 = 沪+深（东财日K与腾讯备胎都带当日额；昨值仅日K有→环比可能缺）
    turnover = None
    sh, sz = indices.get("上证指数"), indices.get("深证成指")
    if sh and sz and sh.get("amount") and sz.get("amount"):
        today_amt = sh["amount"] + sz["amount"]
        prev_amt = None
        if sh.get("prev_amount") and sz.get("prev_amount"):
            prev_amt = sh["prev_amount"] + sz["prev_amount"]
        turnover = {
            "today_yi": round(today_amt / 1e8, 0),                        # 亿元
            "prev_yi": round(prev_amt / 1e8, 0) if prev_amt else None,
            "chg_pct": round((today_amt / prev_amt - 1) * 100, 1) if prev_amt else None,
        }

    if failures:
        print(f"[MarketClose] 降级: {', '.join(failures)}")
    print(f"[MarketClose] 指数{len(indices)}/3 涨跌{'✓' if breadth else '✗'} "
          f"涨停{limits['limit_up']} 两市{turnover['today_yi'] if turnover else '?'}亿")
    return {
        "date": datetime.now(CST).strftime("%Y-%m-%d"),
        "indices": indices,
        "breadth": breadth,
        "limits": limits,
        "boards": boards,
        "turnover": turnover,
        "failures": failures,
    }


def format_close_prompt(p: dict) -> str:
    """收盘全景 → AI prompt数据块（纯事实文本）"""
    lines = ["## 收盘全景（全部为已核实数据）"]

    lines.append("\n### 指数收盘")
    for name, q in p["indices"].items():
        lines.append(f"- {name}: {q['close']} ({q['chg_pct']:+.2f}%)")

    if p["breadth"]:
        b = p["breadth"]
        lines.append(f"\n### 市场宽度\n- 上涨{b['up']}家 / 下跌{b['down']}家 / 平盘{b['flat']}家")

    lim = p["limits"]
    if lim["limit_up"] is not None:
        lines.append(f"\n### 涨跌停\n- 涨停{lim['limit_up']}家 / 跌停{lim['limit_down']}家 / 炸板{lim['blown']}家")

    if p["turnover"]:
        t = p["turnover"]
        chg = f"，较昨日{t['chg_pct']:+.1f}%" if t["chg_pct"] is not None else ""
        lines.append(f"\n### 两市量能\n- 成交额{t['today_yi']:.0f}亿元{chg}")

    if p["boards"]:
        boards = p["boards"]["all"]
        top = "、".join(f"{b['name']}{b['chg_pct']:+.2f}%" for b in boards[:5])
        bottom = "、".join(f"{b['name']}{b['chg_pct']:+.2f}%" for b in boards[-5:])
        lines.append(f"\n### 行业板块\n- 领涨: {top}\n- 领跌: {bottom}")

    if p["failures"]:
        lines.append(f"\n⚠️ 数据降级: {', '.join(p['failures'])}（该部分请勿评述）")

    return "\n".join(lines)
