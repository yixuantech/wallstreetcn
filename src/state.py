"""状态存储 — 系统的海马体（跨时点/跨日共享记忆）

data/state/ 四类文件：
  today.json        当日快照（晨报判断/已推事件ID）→ 午间防重、晚报记分依据
  watchpoints.json  观察点生命周期（挂起⏳/兑现✓/失效✗）→ M1+
  storylines.json   主线连载状态 → M4
  judgments.csv     判断记分流水 → M1+

设计约定：
  - JSON 读写全部 UTF-8，失败返回默认值（降级不阻断推送）
  - today.json 自带日期保护：跨日读取自动重置为空（新的一天新快照）
"""

import csv
import json
from datetime import datetime
from pathlib import Path

from src.utils import CST

STATE_DIR = Path(__file__).parent.parent / "data" / "state"


def _load_json(name: str, default=None):
    """读 JSON，文件不存在或损坏返回 default（降级不抛异常）"""
    path = STATE_DIR / name
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[State] {name} 读取失败(降级为默认): {e}")
        return default


def _save_json(name: str, data) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 当日快照 ──

def load_today() -> dict:
    """读当日快照。跨日自动重置：文件里的日期非今天则返回空白快照。

    结构：
    {
      "date": "2026-08-25",
      "morning": {
        "pushed": true,
        "report_path": "data/reports/2026-08-25.md",
        "judgment": {"direction": "看偏多", "sectors": [...],
                     "news_marks": [{"title": "...", "mark": "利空"}]},
        "pushed_event_ids": [...]     # 已推资讯/公告URL，午间夜巡防重用
      },
      "noon_pushed": false,
      "night_pushed": false
    }
    """
    data = _load_json("today.json")
    today = datetime.now(CST).strftime("%Y-%m-%d")
    if not data or data.get("date") != today:
        return {"date": today, "morning": {}, "noon_pushed": False, "night_pushed": False}
    return data


def save_today(data: dict) -> None:
    _save_json("today.json", data)


# ── 观察点（M1 起使用，接口先立好） ──

def load_watchpoints() -> dict:
    """结构：{"active": [...], "history": [...]}"""
    return _load_json("watchpoints.json", {"active": [], "history": []})


def save_watchpoints(data: dict) -> None:
    _save_json("watchpoints.json", data)


# ── 主线连载（M4 起使用，接口先立好） ──

def load_storylines() -> dict:
    """结构：{"lines": [{"id": 1, "name": "...", "weeks": 9, "status": "...", "log": [...]}]}"""
    return _load_json("storylines.json", {"lines": []})


def save_storylines(data: dict) -> None:
    _save_json("storylines.json", data)


# ── 判断记分流水（M1 起写入，晚报逐条追加） ──

JUDGMENT_FIELDS = ["date", "judgment", "actual", "result", "detail"]


def append_judgment(row: dict) -> None:
    """追加一条判断记录到 judgments.csv（表头自动创建）"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / "judgments.csv"
    is_new = not path.exists()
    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=JUDGMENT_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in JUDGMENT_FIELDS})


def load_judgments(date_from: str, date_to: str) -> list:
    """读 judgments.csv，过滤 [date_from, date_to] 闭区间的流水（周报用）。

    文件不存在/损坏返回 []（降级不阻断）。
    """
    path = STATE_DIR / "judgments.csv"
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return [row for row in csv.DictReader(f)
                    if row.get("date") and date_from <= row["date"] <= date_to]
    except (OSError, csv.Error) as e:
        print(f"[State] judgments.csv 读取失败(降级为空): {e}")
        return []
