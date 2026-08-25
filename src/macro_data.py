"""宏观数据层 — 中国档/美国档采集 + 落地检测 + 数据日历（纯事实，无AI）

数据源（均已实测验证）：
  中国  东财 datacenter RPT_ECONOMY_CPI / PPI / PMI / RMB_LOAN / CURRENCY_SUPPLY(M2)
        社融增量报表名未侦察到 → 暂缺（货币面由M2覆盖，推送中如实标注）
  美国  东财 RPT_ECONOMICVALUE_USANEW × INDICATOR_ID（非农/失业率/CPI月率/CPI年率）
        自带 PUBLISH_DATE 发布日历（预期值字段无 → 三对照走"实际vs前值+分位"降级）

落地检测：state/macro_seen.json 记每指标已见 REPORT_DATE；出现更新即数据日。
"""

import time
from typing import Optional

import requests

from src import state

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"
HISTORY_ROWS = 70          # 月频≈5年+，分位窗口


def _dc(report_name: str, extra: dict = None, page_size: int = HISTORY_ROWS) -> Optional[list]:
    """datacenter 拉取（REPORT_DATE 倒序），失败返回 None"""
    params = {
        "reportName": report_name, "columns": "ALL", "pageSize": page_size,
        "pageNumber": 1, "sortColumns": "REPORT_DATE", "sortTypes": -1,
        "source": "WEB", "client": "WEB",
    }
    params.update(extra or {})
    for attempt in range(3):
        try:
            resp = requests.get(DC, params=params, headers=HEADERS, timeout=10)
            resp.raise_for_status()
            rows = (resp.json().get("result") or {}).get("data")
            return rows or None
        except Exception as e:
            if attempt < 2:
                time.sleep(1 + attempt)
            else:
                print(f"[Macro] {report_name} 拉取失败(降级): {e}")
    return None


# ── 指标定义（中国档） ──
# value: 主字段；unit；predicate: 连续性判定向（sign=正负号 / above50=荣枯线）
CN_INDICATORS = {
    "CPI": {
        "label": "CPI同比", "report": "RPT_ECONOMY_CPI", "field": "NATIONAL_SAME",
        "extra_field": ("NATIONAL_SEQUENTIAL", "环比"), "unit": "%", "predicate": "sign",
    },
    "PPI": {
        "label": "PPI同比", "report": "RPT_ECONOMY_PPI", "field": "BASE_SAME",
        "unit": "%", "predicate": "sign",
    },
    "PMI": {
        "label": "制造业PMI", "report": "RPT_ECONOMY_PMI", "field": "MAKE_INDEX",
        "unit": "", "predicate": "above50",
    },
    "信贷": {
        "label": "新增人民币贷款", "report": "RPT_ECONOMY_RMB_LOAN", "field": "RMB_LOAN",
        "extra_field": ("RMB_LOAN_ACCUMULATE", "年内累计"), "unit": "亿元", "predicate": "sign",
    },
    "M2": {
        "label": "M2同比", "report": "RPT_ECONOMY_CURRENCY_SUPPLY", "field": "CURRENCY_SAME",
        "unit": "%", "predicate": "sign",
    },
}

# ── 指标定义（美国档）：INDICATOR_ID 来自 cjsj/foreign 页 pagedata ──
# predicate: sign=正负号有意义(非农增减/CPI环比涨跌)；缺省=不计算连续性
# （失业率/CPI同比恒为正，sign连续是废话，规则层不出无意义事实）
US_INDICATORS = {
    "非农": {"label": "新增非农就业", "id": "EMG00152118", "unit": "千人", "predicate": "sign"},
    "失业率": {"label": "失业率", "id": "EMG00001039", "unit": "%"},
    "CPI月率": {"label": "CPI环比", "id": "EMG00000770", "unit": "%", "predicate": "sign"},
    "CPI年率": {"label": "CPI同比", "id": "EMG00000733", "unit": "%"},
}


def _norm_date(report_date: str) -> str:
    return str(report_date)[:10]


# ── 中国档 ──

def fetch_cn_indicator(key: str) -> Optional[dict]:
    """单个中国指标 → {period, value, extra, history: [(period, value)...]}"""
    cfg = CN_INDICATORS[key]
    rows = _dc(cfg["report"])
    if not rows:
        return None
    latest = rows[0]
    history = [(r.get("TIME", _norm_date(r.get("REPORT_DATE", ""))), r.get(cfg["field"]))
               for r in rows if isinstance(r.get(cfg["field"]), (int, float))]
    out = {
        "period": latest.get("TIME", ""),
        "value": latest.get(cfg["field"]),
        "history": history,
    }
    if cfg.get("extra_field"):
        f, name = cfg["extra_field"]
        out["extra"] = f"{name}{latest.get(f)}{cfg['unit']}"
    return out


def collect_cn() -> dict:
    """中国档全量采集（各指标独立降级）"""
    data = {}
    for key in CN_INDICATORS:
        data[key] = fetch_cn_indicator(key)
        if data[key]:
            print(f"[MacroCN] {key}: {data[key]['period']} = {data[key]['value']}")
    return data


def landed_cn(data: dict) -> list:
    """落地检测：与 macro_seen 对比，返回本批新落地的指标key列表。

    首次运行（seen为空）视为全部落地=栏目首秀，之后仅期次更新才触发。
    """
    seen = state._load_json("macro_seen.json", {"cn": {}, "us": {}, "concepts_shown": []})
    fresh = []
    for key, d in data.items():
        if not d:
            continue
        # 中国报表无独立发布日字段，以period文本代表期次
        if d.get("period") and seen["cn"].get(key) != d["period"]:
            fresh.append(key)
    return fresh


def mark_seen_cn(data: dict, fresh: list) -> None:
    seen = state._load_json("macro_seen.json", {"cn": {}, "us": {}, "concepts_shown": []})
    for key in fresh:
        if data.get(key):
            seen["cn"][key] = data[key]["period"]
    state._save_json("macro_seen.json", seen)


# ── 美国档 ──

def fetch_us_indicator(key: str) -> Optional[dict]:
    """单个美国指标 → {period, publish_date, value, pre_value, history}

    首行可能是未发布期（VALUE=null, PUBLISH_DATE=未来）→ 保留为日历信息，
    history 只收已发布行。
    """
    cfg = US_INDICATORS[key]
    rows = _dc("RPT_ECONOMICVALUE_USANEW",
               extra={"filter": f'(INDICATOR_ID="{cfg["id"]}")'}, page_size=70)
    if not rows:
        return None
    published = [r for r in rows if isinstance(r.get("VALUE"), (int, float))]
    upcoming = next((r for r in rows if r.get("VALUE") is None), None)
    return {
        "period": published[0].get("REPORT_DATE_CH", "") if published else "",
        "value": published[0].get("VALUE") if published else None,
        "pre_value": published[0].get("PRE_VALUE") if published else None,
        "history": [(r.get("REPORT_DATE_CH", ""), r.get("VALUE")) for r in published],
        "next_period": upcoming.get("REPORT_DATE_CH", "") if upcoming else "",
        "next_publish": _norm_date(upcoming.get("PUBLISH_DATE", "")) if upcoming else "",
    }


def collect_us() -> dict:
    data = {}
    for key in US_INDICATORS:
        data[key] = fetch_us_indicator(key)
        if data[key]:
            print(f"[MacroUS] {key}: {data[key]['period']} = {data[key]['value']}"
                  + (f"（下期{data[key]['next_publish']}发布）" if data[key]["next_publish"] else ""))
    return data


def landed_us(data: dict) -> list:
    """美国落地：已发布期次 != 已见期次（API里未发布行VALUE=null天然被排除；
    07:45晨跑时前一晚20:30/21:30发布的行已是已发布态）"""
    seen = state._load_json("macro_seen.json", {"cn": {}, "us": {}, "concepts_shown": []})
    fresh = []
    for key, d in data.items():
        if not d or d["value"] is None:
            continue
        if seen["us"].get(key) != d["period"]:
            fresh.append(key)
    return fresh


def mark_seen_us(data: dict, fresh: list) -> None:
    seen = state._load_json("macro_seen.json", {"cn": {}, "us": {}, "concepts_shown": []})
    for key in fresh:
        if data.get(key):
            seen["us"][key] = data[key]["period"]
    state._save_json("macro_seen.json", seen)


# ── 概念卡（首次遇到展开，之后一句话） ──

MACRO_CONCEPTS = {
    "CPI": {"what": "消费者物价指数，一篮子居民商品和服务价格的变化", "why": "通胀高低直接决定央行货币政策松紧，进而影响利率和股票估值", "watch": "同比2-3%为温和通胀；持续>3%压制降息预期，<1%提示需求偏弱"},
    "PPI": {"what": "生产者物价指数，工厂出厂价格的变化", "why": "上游价格向下游传导，影响企业利润率；也是CPI的先行指标", "watch": "同比转负=工业通缩压力；回升利好周期类企业利润"},
    "PMI": {"what": "采购经理指数，对企业采购经理的调查汇总（50为荣枯线）", "why": "最早的月度景气信号，比GDP快", "watch": ">50=扩张，<50=收缩；连续3个月同向才形成趋势"},
    "信贷": {"what": "新增人民币贷款，当月银行新发放的贷款总额", "why": "实体经济融资需求的温度计，反映企业和居民愿不愿意借钱", "watch": "看结构：企业中长期贷款占比高=投资意愿强；居民贷款弱=地产消费疲软"},
    "M2": {"what": "广义货币供应量，全社会货币总量（现金+存款）", "why": "货币之水，M2增速与名义GDP增速之差影响资产价格", "watch": "M2回升+信贷改善=宽货币传导顺畅；M2高信贷低=资金空转"},
    "社融": {"what": "社会融资规模，实体经济从金融体系获得的融资总额", "why": "比信贷更全面的融资口径（含债券/股票等）", "watch": "数据源建设中，暂缺"},
    "非农": {"what": "美国非农就业人数变化，除农业外新增就业", "why": "美联储最看重的就业指标，直接影响加息/降息路径", "watch": "20万人以上=强劲；连续低于10万=就业降温"},
    "失业率": {"what": "美国失业率，劳动力中无工作占比", "why": "美联储双重使命之一，快速上行=衰退信号", "watch": "4%上下为充分就业；快速攀升0.5个百分点以上值得警惕"},
    "CPI月率": {"what": "美国CPI环比，单月物价变化", "why": "环比是同比的原料，美联储更看核心环比折年", "watch": "环比0.2%折年约2.5%；0.3%以上=通胀粘性"},
    "CPI年率": {"what": "美国CPI同比", "why": "全球资产定价之锚，决定美元利率和全球流动性", "watch": "回落向2%=降息空间打开；反弹=紧缩重启担忧"},
}


def concept_cards(keys: list) -> list:
    """首次遇到的指标返回概念卡，已见过的返回None（并登记）"""
    seen = state._load_json("macro_seen.json", {"cn": {}, "us": {}, "concepts_shown": []})
    cards = []
    changed = False
    for k in keys:
        if k in seen["concepts_shown"]:
            continue
        if k in MACRO_CONCEPTS:
            cards.append((k, MACRO_CONCEPTS[k]))
            seen["concepts_shown"].append(k)
            changed = True
    if changed:
        state._save_json("macro_seen.json", seen)
    return cards


# ── 推送数据块格式化 ──

def _fmt_value(key: str, cfg: dict, cmp_: dict) -> str:
    unit = cfg.get("unit", "")
    parts = [f"{cmp_['period']} {cfg.get('label', key)} **{cmp_['value']}{unit}**"]
    if cmp_.get("pre") is not None:
        parts.append(f"前值{cmp_['pre']}{unit}（{cmp_.get('vs_pre', '—')}）")
    if cmp_.get("percentile") is not None:
        parts.append(f"近5年{cmp_['percentile']}%分位")
    if (cmp_.get("streak") or 0) >= 2:
        parts.append(f"连续{cmp_['streak']}个月同向")
    return " ｜ ".join(parts)


def format_macro_prompt(keys: list, data: dict, compares: dict, definitions: dict,
                        concepts: list, watch_names: list = None) -> str:
    """数据解读数据块（中美通用）。

    Args:
        keys: 本批落地的指标
        compares: {key: macro_compare结果}
        definitions: CN_INDICATORS / US_INDICATORS（取label/unit）
        concepts: concept_cards() 结果（首遇展开）
        watch_names: 自选股名称（联动段原料）
    """
    lines = ["## 今日落地的宏观数据（全部为已核实数值）"]

    for k in keys:
        cfg = definitions.get(k, {})
        lines.append(f"\n### {cfg.get('label', k)}")
        lines.append(_fmt_value(k, cfg, compares[k]))
        d = data.get(k) or {}
        if d.get("extra"):
            lines.append(f"另：{d['extra']}")
        if d.get("next_period"):
            lines.append(f"下期{d['next_period']}预计 {d['next_publish']} 发布")
        if k in dict(concepts):
            c = dict(concepts)[k]
            lines.append(f"\n🃏 概念卡（首次遇到，展开一次）：{c['what']}。为什么重要：{c['why']}。怎么看：{c['watch']}")

    if watch_names:
        lines.append(f"\n## 持有人自选（联动分析原料，名称：{'、'.join(watch_names)}）")

    lines.append("\n（预期值对照：暂无免费数据源，本块只有实际值/前值/分位，AI不得虚构预期值）")
    return "\n".join(lines)
