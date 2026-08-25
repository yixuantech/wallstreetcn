#!/usr/bin/env python3
"""M3·3.1 侦察：宏观数据剩余数据源（真实采集，不调AI不推送）

待侦察：
  1. CPI/PPI 报表是否自带"预期值"字段（columns=ALL 看全字段）
  2. 社融/M2 报表名（此前4个候选未中，再试一轮）
  3. 美国宏观数据源（东财外国经济数据页）

用法: NO_PROXY='*' no_proxy='*' PYTHONIOENCODING=utf-8 python tests/recon_macro.py
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests

SAMPLES = Path("research/samples")
SAMPLES.mkdir(parents=True, exist_ok=True)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def probe_dc(report_name: str, label: str, columns: str = "ALL"):
    """探测 datacenter 报表，存样本打印首行字段"""
    try:
        r = requests.get(DC, params={
            "reportName": report_name, "columns": columns,
            "pageSize": 3, "pageNumber": 1, "sortColumns": "REPORT_DATE",
            "sortTypes": "-1", "source": "WEB", "client": "WEB",
        }, headers=HEADERS, timeout=10)
        d = r.json()
    except Exception as e:
        print(f"  ✗ {label}({report_name}): {type(e).__name__}")
        return None
    if not d.get("result") or not d["result"].get("data"):
        print(f"  ✗ {label}({report_name}): 无数据 code={d.get('code')} msg={d.get('message', '')[:60]}")
        return None
    rows = d["result"]["data"]
    path = SAMPLES / f"macro_{label}.json"
    path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    keys = list(rows[0].keys())
    print(f"  ✓ {label}({report_name}): {len(rows)}行 字段{len(keys)}个 → {path.name}")
    print(f"      字段: {keys[:18]}{'...' if len(keys) > 18 else ''}")
    first = {k: rows[0][k] for k in keys[:8]}
    print(f"      首行: {json.dumps(first, ensure_ascii=False)[:220]}")
    return rows


def main():
    print("宏观数据源侦察")
    print("=" * 70)

    print("\n[1] 已知报表的完整字段（找预期值）")
    probe_dc("RPT_ECONOMY_CPI", "cpi_full")
    probe_dc("RPT_ECONOMY_PPI", "ppi_full")

    print("\n[2] 社融/M2 候选报表名")
    for name, label in [
        ("RPT_ECONOMY_SOCIETAL_FINANCE", "社融增量"),
        ("RPT_ECONOMY_SHRZGM", "社融旧名"),
        ("RPT_ECONOMY_M2", "M2"),
        ("RPT_ECONOMY_CURRENCY_SUPPLY", "货币供应"),
        ("RPT_ECONOMY_TOTAL_SOCIETY_FINANCING", "社融v2"),
    ]:
        probe_dc(name, label)

    print("\n[3] 美国宏观数据候选")
    for name, label in [
        ("RPT_ECONOMY_USA_CPI", "美CPI"),
        ("RPT_ECONOMY_FOREIGN", "外国数据"),
        ("RPT_ECONOMY_AMERICA_CPI", "美CPIv2"),
        ("RPT_USMACRO_CPI", "美宏CPI"),
    ]:
        probe_dc(name, label)

    print("\n[4] 美国数据页面直探（外国经济数据页接口）")
    try:
        r = requests.get("https://data.eastmoney.com/cjsj/foreign_0_0.html", headers=HEADERS, timeout=10)
        print(f"  页面可达: {r.status_code}, 长度{len(r.text)}")
    except Exception as e:
        print(f"  ✗ 页面: {e}")


if __name__ == "__main__":
    main()
