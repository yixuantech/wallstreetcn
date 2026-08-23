#!/usr/bin/env python3
"""自选股功能数据源探测脚本 — 逐一实测候选API的可用性

用法: python research/probe_apis.py [股票代码]
默认测试标的: 600519 (贵州茅台, 上交所 -> secid=1.600519)
深交所代码前缀为 0/3 -> secid=0.xxxxxx

输出: 终端打印每个接口的探测结果摘要；完整JSON存 research/samples/
"""

import json
import sys
import time
from pathlib import Path

import requests

# 国内直连，绕过可能失效的系统代理
session = requests.Session()
session.trust_env = False

CODE = sys.argv[1] if len(sys.argv) > 1 else "600519"
SECID = f"1.{CODE}" if CODE.startswith(("6", "9", "5")) else f"0.{CODE}"
SAMPLES_DIR = Path(__file__).parent / "samples"
SAMPLES_DIR.mkdir(exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
WSCN_HEADERS = {
    "Origin": "https://wallstreetcn.com",
    "Referer": "https://wallstreetcn.com/",
    "User-Agent": UA["User-Agent"],
}


def probe(name: str, url: str, params=None, headers=None, method="GET",
          data=None, json_body=None, extract=None):
    """探测单个接口，打印摘要，保存完整响应"""
    print(f"\n{'─'*70}")
    print(f"[{name}] {method} {url.split('?')[0]}")
    try:
        resp = session.request(
            method, url, params=params, headers=headers or UA,
            data=data, json=json_body, timeout=10,
        )
        print(f"  HTTP {resp.status_code}")
        resp.raise_for_status()

        # jsonp 响应去掉回调包裹
        text = resp.text
        if params and str(params).find("cb=") != -1 or (isinstance(params, dict) and "cb" in params):
            import re
            m = re.search(r"\((.*)\)\s*;?\s*$", text, re.S)
            if m:
                text = m.group(1)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            print(f"  ✗ 非JSON响应: {text[:120]}")
            return None

        # 保存完整样本
        fname = SAMPLES_DIR / f"{name}.json"
        fname.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        # 提取摘要
        if extract:
            summary = extract(payload)
            if summary:
                print(f"  ✓ {summary}")
            else:
                print(f"  ⚠ 响应OK但提取结果为空，结构见 {fname.name}")
        else:
            print(f"  ✓ 响应OK，结构见 {fname.name}")
        return payload

    except Exception as e:
        print(f"  ✗ 失败: {type(e).__name__}: {str(e)[:150]}")
        return None


def brief(items, count=2, maxlen=150):
    """生成前N条记录的摘要预览"""
    if not items:
        return None
    lines = [f"共{len(items)}条, 前{min(count, len(items))}条预览:"]
    for it in items[:count]:
        s = json.dumps(it, ensure_ascii=False)
        lines.append("    " + s[:maxlen])
    return "\n    ".join(lines)


# ═══════════════ A. 个股资讯 ═══════════════

def probe_news():
    # A1. 东方财富个股资讯流（股吧/资讯 tab 用的接口）
    probe("A1_东财个股资讯流",
          "https://np-listapi.eastmoney.com/comm/web/getListInfo",
          params={"cfh": 1, "client": "web", "mTypeAndCode": SECID,
                  "type": 1, "pageSize": 20, "fields": "code,name,showTime,title,mediaName,summary,url,uniqueUrl"},
          extract=lambda p: brief((p.get("data") or {}).get("list") or []))

    # A2. 东方财富搜索API（关键词搜资讯，可搜公司名）
    param = {
        "uid": "", "keyword": "贵州茅台", "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {
            "searchScope": "default", "sort": "time",
            "pageIndex": 1, "pageSize": 10,
            "preTag": "", "postTag": ""}},
    }
    probe("A2_东财搜索资讯",
          "https://search-api-web.eastmoney.com/search/jsonp",
          params={"cb": "jQuery", "param": json.dumps(param, ensure_ascii=False)},
          extract=lambda p: brief(
              (p.get("result") or {}).get("cmsArticleWebOld") or []))

    # A3. 华尔街见闻站内搜索（候选端点1: 通用搜索）
    probe("A3a_见闻搜索_article",
          "https://api-one-wscn.awtmt.com/apiv1/search/article",
          params={"keyword": "贵州茅台", "limit": 10}, headers=WSCN_HEADERS,
          extract=lambda p: brief(p.get("data", {}).get("items") or p.get("data") or []))

    # A4. 华尔街见闻信息流（candidate: global频道按关键词）
    probe("A3b_见闻搜索_information",
          "https://api-one-wscn.awtmt.com/apiv1/search/information-flow",
          params={"keyword": "贵州茅台", "accept": "article", "limit": 10},
          headers=WSCN_HEADERS,
          extract=lambda p: brief(p.get("data", {}).get("items") or []))


# ═══════════════ B. 日历节点 ═══════════════

def dc_params(report_name, filter_expr, sort_col, page_size=5):
    """datacenter-web 通用参数"""
    return {
        "reportName": report_name, "columns": "ALL",
        "filter": filter_expr, "pageSize": page_size,
        "sortColumns": sort_col, "sortTypes": "-1",
        "source": "WEB", "client": "WEB",
    }


def probe_calendar():
    base = "https://datacenter-web.eastmoney.com/api/data/v1/get"

    # B1. 财报预约披露日期
    probe("B1_财报预约披露", base,
          params=dc_params("RPT_PUBLIC_BS_APPOIN", f'(SECURITY_CODE="{CODE}")', "REPORT_DATE"),
          extract=lambda p: brief(((p.get("result") or {}).get("data")) or []))

    # B2. 分红送配（含除权除息日）
    probe("B2_分红送配", base,
          params=dc_params("RPT_SHAREBONUS_DET", f'(SECURITY_CODE="{CODE}")', "REPORT_DATE"),
          extract=lambda p: brief(((p.get("result") or {}).get("data")) or []))

    # B3. 限售解禁
    probe("B3_限售解禁", base,
          params=dc_params("RPT_LIFT_STAGE", f'(SECURITY_CODE="{CODE}")', "FREE_DATE"),
          extract=lambda p: brief(((p.get("result") or {}).get("data")) or []))

    # B4. 公司大事/股东大会 — F10 接口
    probe("B4_F10分红融资", "https://emweb.securities.eastmoney.com/PC_HSF10/BonusFinancing/PageAjax",
          params={"code": f"SH{CODE}" if SECID.startswith("1") else f"SZ{CODE}"},
          extract=lambda p: "keys: " + ",".join(list(p.keys())[:15]))


# ═══════════════ C. 其他重要事项 ═══════════════

def probe_others():
    base = "https://datacenter-web.eastmoney.com/api/data/v1/get"

    # C1. 公司公告（东方财富公告接口）
    probe("C1_东财公告列表",
          "https://np-anotice-stock.eastmoney.com/api/security/ann",
          params={"sr": -1, "page_size": 10, "page_index": 1,
                  "ann_type": "A", "stock_list": CODE},
          extract=lambda p: brief((p.get("data") or {}).get("list") or []))

    # C2. 巨潮资讯公告（官方源，POST）
    probe("C2_巨潮公告",
          "http://www.cninfo.com.cn/new/hisAnnouncement/query",
          method="POST",
          data={"pageNum": 1, "pageSize": 10, "column": "sse",
                "tabName": "fulltext", "stock": f"{CODE},gssh0{CODE}",
                "searchkey": "", "secid": "", "category": "",
                "trade": "", "seDate": "", "sortName": "", "sortType": "",
                "isHLtitle": "true"},
          extract=lambda p: brief((p.get("announcements")) or [], maxlen=200))

    # C3. 股东增减持
    probe("C3_股东增减持", base,
          params=dc_params("RPT_SHARE_HOLDER_INCREASE", f'(SECURITY_CODE="{CODE}")', "CHANGE_DATE"),
          extract=lambda p: brief(((p.get("result") or {}).get("data")) or []))

    # C4. 回购
    probe("C4_回购", base,
          params=dc_params("RPT_REPURCHASE", f'(SECURITY_CODE="{CODE}")', "UPDATE_DATE"),
          extract=lambda p: brief(((p.get("result") or {}).get("data")) or []))

    # C5. 龙虎榜
    probe("C5_龙虎榜", base,
          params=dc_params("RPT_DAILYBILLBOARD_DETAILSNEW", f'(SECURITY_CODE="{CODE}")', "TRADE_DATE"),
          extract=lambda p: brief(((p.get("result") or {}).get("data")) or []))

    # C6. 大宗交易
    probe("C6_大宗交易", base,
          params=dc_params("RPT_DATA_BLOCKTRADE", f'(SECURITY_CODE="{CODE}")', "TRADE_DATE"),
          extract=lambda p: brief(((p.get("result") or {}).get("data")) or []))

    # C7. 融资融券明细
    probe("C7_融资融券", base,
          params=dc_params("RPTA_WEB_RZRQ_GGMX", f'(scode="{CODE}")', "DATE"),
          extract=lambda p: brief(((p.get("result") or {}).get("data")) or []))

    # C8. 北向资金持股
    probe("C8_北向持股", base,
          params=dc_params("RPT_MUTUAL_HOLDSTOCKNORTH_STA", f'(SECURITY_CODE="{CODE}")', "HOLD_DATE"),
          extract=lambda p: brief(((p.get("result") or {}).get("data")) or []))

    # C9. 机构调研
    probe("C9_机构调研", base,
          params=dc_params("RPT_ORG_SURVEYNEW", f'(SECURITY_CODE="{CODE}")', "RECEIVE_START_DATE"),
          extract=lambda p: brief(((p.get("result") or {}).get("data")) or [], maxlen=200))

    # C10. 研报评级（含分析师评级和盈利预测）
    probe("C10_研报评级",
          "https://reportapi.eastmoney.com/report/list",
          params={"industryCode": "*", "pageSize": 5, "industry": "*", "rating": "*",
                  "ratingChange": "*", "beginTime": "2026-05-01", "endTime": "2026-08-23",
                  "pageNo": 1, "fields": "", "qType": 0, "orgCode": "", "code": CODE},
          extract=lambda p: brief(p.get("data") or []))

    # C11. 盈利预测（F10）
    probe("C11_盈利预测",
          "https://emweb.securities.eastmoney.com/PC_HSF10/ProfitForecast/PageAjax",
          params={"code": f"SH{CODE}" if SECID.startswith("1") else f"SZ{CODE}"},
          extract=lambda p: "keys: " + ",".join(list(p.keys())[:15]))


if __name__ == "__main__":
    print(f"探测标的: {CODE} (secid={SECID})")
    t0 = time.time()
    probe_news()
    probe_calendar()
    probe_others()
    print(f"\n{'═'*70}")
    print(f"探测完成，耗时 {time.time()-t0:.1f}秒，样本保存于 {SAMPLES_DIR}")
