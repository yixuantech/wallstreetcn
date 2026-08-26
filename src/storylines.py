"""主线连载状态机 — 市场是一部连载，不是每天互相矛盾的碎片（M4）

设计约定（与预判块同款降级哲学）：
  - AI 只提案（name/status/progress），规则引擎裁决：id分配/周数翻转/记档/清理
  - AI 输出损坏或缺失 → 主线原样保留，晨报照常（降级不阻断）
  - 周数翻转与周六命令解耦：week_key 存 ISO 周，merge 时跨周即 +1
    （哪怕周报一周没跑，下次晨报 merge 也能把周数补上）
"""

from datetime import datetime, timedelta

from src.utils import CST

STATUSES = ["孕育", "发酵", "主导", "退潮", "终结"]
MAX_ACTIVE = 8          # 活跃线数量上限（防AI失控开新线）
KEEP_ENDED_WEEKS = 4    # 终结线保留周数（供周报展示"墓志铭"，之后清理）
LOG_CAP = 20            # 每线log截断条数


def iso_week(d) -> str:
    """日期 → ISO 周键（如 2026-W35）。d 为 date/datetime/'YYYY-MM-DD'"""
    if isinstance(d, str):
        d = datetime.strptime(d, "%Y-%m-%d")
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _clean_proposals(ai_lines) -> list:
    """AI提案清洗：只留 name/status 合法的条目；整体异常返回 []（降级）"""
    if ai_lines is None:
        return []
    if not isinstance(ai_lines, list):
        print("[Storylines] AI主线块格式异常，本次保持原样（降级）")
        return []
    props = []
    for p in ai_lines:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name", "")).strip()
        status = str(p.get("status", "")).strip()
        if name and status in STATUSES:
            props.append({"id": p.get("id"), "name": name,
                          "status": status, "progress": str(p.get("progress", "")).strip()})
    return props


def _find_line(prop: dict, lines: list, used: set):
    """提案 → 目标线匹配：id > 名称精确 > 名称包含（双向）"""
    pid = prop.get("id")
    if isinstance(pid, int):
        for l in lines:
            if l["id"] == pid and l["id"] not in used:
                return l
    for l in lines:
        if l["name"] == prop["name"] and l["id"] not in used:
            return l
    for l in lines:
        if l["id"] not in used and (prop["name"] in l["name"] or l["name"] in prop["name"]):
            return l
    return None


def merge_storylines(old_lines: list, ai_lines, today: str = None):
    """晨报AI提案 × 现有状态 → 新状态 + 变更清单。

    Args:
        old_lines: state.load_storylines()["lines"]
        ai_lines: 晨报 meta JSON 的 "storylines" 数组（None/损坏均降级）
        today: 'YYYY-MM-DD'（默认今天，测试可注入）
    Returns:
        (new_lines, changes)  changes 如 ["#1 状态: 发酵→主导", "✨新主线#4 反内卷"]
    """
    today = today or datetime.now(CST).strftime("%Y-%m-%d")
    week_now = iso_week(today)
    lines = [dict(l) for l in (old_lines or [])]
    changes = []

    # 1. 周数翻转（对所有线生效，与AI是否提及无关）
    for l in lines:
        if l.get("week_key", week_now) != week_now:
            l["weeks"] = max(l.get("weeks", 1), 1) + 1
            l["week_key"] = week_now

    # 2. 合并AI提案
    used = set()
    for prop in _clean_proposals(ai_lines):
        line = _find_line(prop, lines, used)
        if line is None:
            # 新诞生（终结线不新增；活跃线cap）
            if prop["status"] == "终结":
                continue
            active_n = sum(1 for l in lines if l.get("status") != "终结")
            if active_n >= MAX_ACTIVE:
                changes.append(f"⚠️「{prop['name']}」因超出{MAX_ACTIVE}条上限未收录")
                continue
            nid = max((l["id"] for l in lines), default=0) + 1
            line = {"id": nid, "name": prop["name"], "status": prop["status"],
                    "weeks": 1, "week_key": week_now, "updated": today, "log": []}
            lines.append(line)
            changes.append(f"✨新主线#{nid} {prop['name']}")
            used.add(nid)
        else:
            used.add(line["id"])
        # 逐字段裁决 + 记档
        entry = []
        if prop["status"] != line.get("status"):
            entry.append(f"状态: {line.get('status', '—')}→{prop['status']}")
            if prop["status"] == "终结":
                line["ended_on"] = today
                changes.append(f"🏁#{line['id']} {line['name']} 终结")
            else:
                changes.append(f"#{line['id']} 状态: {line.get('status', '—')}→{prop['status']}")
        if isinstance(prop.get("id"), int) and prop["name"] != line["name"]:
            entry.append(f"更名: {line['name']}→{prop['name']}")
            line["name"] = prop["name"]       # id匹配才允许更名（名称匹配时改了就断锚）
        if prop["progress"] and prop["progress"] != line.get("progress"):
            line["progress"] = prop["progress"]
        if entry or prop["progress"]:
            log_text = "；".join(filter(None, entry + [prop["progress"]]))
            line.setdefault("log", []).append({"date": today, "text": log_text[:60]})
        line["status"] = prop["status"]
        line["updated"] = today

    # 3. 终结线清理（保留KEEP_ENDED_WEEKS周供周报展示）
    kept = []
    for l in lines:
        if l.get("status") == "终结" and l.get("ended_on"):
            try:
                ended = datetime.strptime(l["ended_on"], "%Y-%m-%d")
                if (datetime.strptime(today, "%Y-%m-%d") - ended) > timedelta(weeks=KEEP_ENDED_WEEKS):
                    continue
            except ValueError:
                pass
        l["log"] = (l.get("log") or [])[-LOG_CAP:]
        kept.append(l)
    return kept, changes


def format_storylines_prompt(lines: list) -> str:
    """主线状态 → 晨报prompt数据块（AI据此刻画今日地图并回写提案）"""
    active = [l for l in (lines or []) if l.get("status") != "终结"]
    if not active:
        return ("## 主线连载状态（系统维护）\n\n（当前无登记主线——若今日要闻中出现具备"
                "连续多日驱动力的宏观/产业叙事，可开辟新主线并在JSON块中登记）")
    rows = ["| ID | 主线 | 状态 | 周数 | 最近进展 |", "|---|---|---|---|---|"]
    for l in active:
        rows.append(f"| {l['id']} | {l['name']} | {l.get('status', '')} | "
                    f"第{max(l.get('weeks', 1), 1)}周 | {l.get('progress', '')} |")
    return ("## 主线连载状态（系统维护的当前地图，ID与名称尽量沿用）\n\n"
            + "\n".join(rows))


def format_weekly_storylines(lines: list, week_start: str) -> str:
    """主线状态 → 周报数据块（全量线 + 本周log摘要，AI只叙述）"""
    lines = lines or []
    if not lines:
        return "## 主线周演进\n\n⚠️ 尚无登记主线（本周晨报未运行或主线块损坏）"
    out = ["## 主线周演进（状态与周数由规则维护，不可修改）", ""]
    for l in lines:
        week_logs = [g for g in (l.get("log") or []) if g.get("date", "") >= week_start]
        head = (f"- #{l['id']} {l['name']}｜{l.get('status', '')}·第{max(l.get('weeks', 1), 1)}周"
                + ("（本周已终结）" if l.get("status") == "终结" else ""))
        out.append(head)
        if week_logs:
            for g in week_logs[-5:]:
                out.append(f"  - {g['date'][5:]} {g['text']}")
        elif l.get("progress"):
            out.append(f"  - 最近进展：{l['progress']}")
    return "\n".join(out)
