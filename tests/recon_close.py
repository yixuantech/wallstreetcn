#!/usr/bin/env python3
"""M1·1.1 侦察：收盘全景接口（真实采集，不调AI不推送）

探测五类数据源，逐个存样本到 research/samples/：
  1. 涨跌家数   push2 指数行情 f104/f105/f106
  2. 板块涨跌   push2 clist fs=m:90+t:2（行业）/ m:90+t:3（概念）
  3. 涨停池     push2ex getTopicZTPool
  4. 跌停池     push2ex getTopicDTPool + 炸板池 getTopicZBPool
  5. 两市量能   沪指+深成指 f6 成交额求和

用法: NO_PROXY='*' no_proxy='*' PYTHONIOENCODING=utf-8 python tests/recon_close.py
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests

from src.utils import CST

SAMPLES = Path("research/samples")
SAMPLES.mkdir(parents=True, exist_ok=True)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
TODAY_COMPACT = datetime.now(CST).strftime("%Y%m%d")

RESULTS = []


def probe(name: str, url: str, params: dict, fields_desc: str, trim=None):
    """探测单个接口：2次重试，存样本，打印摘要"""
    for attempt in range(2):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception as e:
            if attempt == 0:
                time.sleep(2)
                continue
            print(f"  ✗ {name}: 两次尝试失败 — {e}")
            RESULTS.append((name, "FAIL", str(e)))
            return None

    path = SAMPLES / f"close_{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = bool(data.get("data"))
    print(f"  {'✓' if ok else '△'} {name}: {'有数据' if ok else 'data为空'} → {path.name}")
    if ok and trim:
        try:
            print(f"      {trim(data)}")
        except Exception as e:
            print(f"      (摘要提取失败: {e})")
    print(f"      字段说明: {fields_desc}")
    RESULTS.append((name, "OK" if ok else "EMPTY", ""))
    return data


def main():
    print(f"收盘全景接口侦察 — {datetime.now(CST).strftime('%Y-%m-%d %H:%M')} (交易日)")
    print("=" * 70)

    # 1. 涨跌家数（指数行情附加字段）
    print("\n[1] 涨跌家数 — push2 ulist f104上涨/f105下跌/f106平盘")
    probe(
        "breadth",
        "https://push2.eastmoney.com/api/qt/ulist.np/get",
        {
            "fltt": 2, "invt": 2, "np": 1,
            "secids": "1.000001,0.399001,0.399006",
            "fields": "f12,f14,f2,f3,f5,f6,f104,f105,f106",
        },
        "f104=上涨家数 f105=下跌家数 f106=平盘 f5=成交量 f6=成交额",
        trim=lambda d: "; ".join(
            f"{r['f14']}: 涨{r.get('f104')}/跌{r.get('f105')}/平{r.get('f106')} 额{r.get('f6')}"
            for r in d["data"]["diff"] if isinstance(r, dict)
        ),
    )

    # 2. 板块涨跌（行业 top5 + bottom5）
    print("\n[2] 板块涨跌 — push2 clist m:90+t:2行业 / m:90+t:3概念")
    probe(
        "industry_top",
        "https://push2.eastmoney.com/api/qt/clist/get",
        {
            "pn": 1, "pz": 8, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": "f3", "fs": "m:90+t:2+f:!50",
            "fields": "f12,f14,f3,f62,f128,f140",
        },
        "f3=涨跌幅 f62=主力净流入 f128=领涨股 f140=领涨股代码",
        trim=lambda d: " | ".join(f"{r['f14']}{r['f3']}%" for r in d["data"]["diff"]),
    )
    probe(
        "industry_bottom",
        "https://push2.eastmoney.com/api/qt/clist/get",
        {
            "pn": 1, "pz": 8, "po": 0, "np": 1, "fltt": 2, "invt": 2,
            "fid": "f3", "fs": "m:90+t:2+f:!50",
            "fields": "f12,f14,f3,f62,f128,f140",
        },
        "同上，po=0取跌幅榜",
        trim=lambda d: " | ".join(f"{r['f14']}{r['f3']}%" for r in d["data"]["diff"]),
    )

    # 3. 涨停池
    print("\n[3] 涨停池 — push2ex getTopicZTPool")
    probe(
        "zt_pool",
        "https://push2ex.eastmoney.com/getTopicZTPool",
        {
            "ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt",
            "Pageindex": 0, "pagesize": 30, "sort": "fbt:asc", "date": TODAY_COMPACT,
        },
        "data.tc=总数; 池内 c=代码 n=名称 zdp=涨跌幅 fbt=首次封板时间 lbt=最后封板时间 hyb=所属行业",
        trim=lambda d: f"涨停总数={d['data'].get('tc')}，前5: "
                       + " | ".join(r.get("n", "?") for r in d["data"]["pool"][:5]),
    )

    # 4. 跌停池 + 炸板池
    print("\n[4] 跌停池/炸板池 — push2ex getTopicDTPool / getTopicZBPool")
    probe(
        "dt_pool",
        "https://push2ex.eastmoney.com/getTopicDTPool",
        {
            "ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt",
            "Pageindex": 0, "pagesize": 30, "sort": "fund:asc", "date": TODAY_COMPACT,
        },
        "结构同涨停池",
        trim=lambda d: f"跌停总数={d['data'].get('tc')}，前5: "
                       + " | ".join(r.get("n", "?") for r in d["data"].get("pool", [])[:5]),
    )
    probe(
        "zb_pool",
        "https://push2ex.eastmoney.com/getTopicZBPool",
        {
            "ut": "7eea3edcaed7a9cbfc24409ed989", "dpt": "wz.ztzt",
            "Pageindex": 0, "pagesize": 30, "sort": "fbt:asc", "date": TODAY_COMPACT,
        },
        "炸板=曾涨停后开板；总数用于计算炸板率=炸板/(涨停+炸板)",
        trim=lambda d: f"炸板总数={d['data'].get('tc')}，前5: "
                       + " | ".join(r.get("n", "?") for r in d["data"].get("pool", [])[:5]),
    )

    # 5. 北向资金（情绪刻度候选输入；若不可用则剔除）
    print("\n[5] 北向资金 — push2 kline get")
    probe(
        "northbound",
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        {
            "secid": "1.000001", "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
            "klt": 101, "fqt": 1, "end": "20500101", "lmt": 3,
        },
        "日K：f51日期 f52开 f53收 f55成交量 f56成交额 f61换手 f62主力净流入(检查语义)",
        trim=lambda d: "; ".join(k["f51"] + " 额" + k["f56"] for k in d["data"]["klines"][-3:]),
    )

    print("\n" + "=" * 70)
    print("侦察结果汇总:")
    for name, status, err in RESULTS:
        print(f"  {status:6s} {name}" + (f" — {err}" if err else ""))


if __name__ == "__main__":
    main()
