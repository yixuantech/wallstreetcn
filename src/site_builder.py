"""静态站点生成器 — 四角色驾驶舱 + 报告归档浏览（纯展示层，零新依赖）

产品定位（doc/产品设计.md §7 已识别候选）：
  服务翻译官+记分牌的回看需求 + 表达层原型实验场（四感中"从容感/成长感"的显形实验）。
  数据全部来自 state/*.json 与 data/reports/*.md，构建时读取，产物为 site/ 静态文件。

交互设计（静态站三层交互，无JS框架）：
  1. 详情页导航：主线卡→storylines/{id}.html；自选股→stocks/{code}.html（零JS）
  2. 数据点直链：情绪曲线每个点→当日晚报；记分流水日期→当日晨报（SVG/表格内<a>）
  3. 原生折叠：主线迁移史 <details> 展开；归档栏目筛选（15行原生JS）

设计约定：
  - 增量数据缺失 → 诚实空态"积累中"（不硬凑，与产品哲学一致）
  - -preview.md 为草稿，不上站（站点永远只反映已推送内容）
  - 隐私默认安全：纯本地文件，外网暴露由部署侧决定（见部署指南）
  - 全量重建幂等：页面不含构建时间戳（内容确定性，可 diff 可校验）
"""

import html
import re
from pathlib import Path

from src import state
from src.engines import weekly_judgment_stats
from src.report_formatter import _md_to_html
from src.watchlist import load_watchlist

# 后缀 → (栏目名, 徽标, 同日排序, 筛选键)
REPORT_TYPES = {
    "":         ("晨报", "🌅", 0, "morning"),
    "noon":     ("午间快讯", "⚡", 1, "noon"),
    "evening":  ("晚报", "🌙", 2, "evening"),
    "macro-cn": ("数据解读·中国", "📊", 3, "macro"),
    "macro-us": ("数据解读·美国", "📊", 4, "macro"),
    "night":    ("紧急警报", "🚨", 5, "night"),
    "alert":    ("紧急警报", "🚨", 5, "night"),      # 预览脚本历史命名兼容
    "weekly":   ("周报", "📖", 6, "weekly"),
}

_STATUS_CLASS = {"孕育": "st-bud", "发酵": "st-brew", "主导": "st-lead",
                 "退潮": "st-ebb", "终结": "st-end"}

_ROOT = Path(__file__).parent.parent


def build_site(reports_dir: Path = None, out_dir: Path = None) -> dict:
    """全量重建静态站（在位覆盖：先写新页，再清孤儿页——浏览器占用文件时不致命）。

    Returns: {"reports": 篇数, "out": 输出目录}
    """
    reports_dir = Path(reports_dir) if reports_dir else _ROOT / "data" / "reports"
    out_dir = Path(out_dir) if out_dir else _ROOT / "site"

    reports = _scan_reports(reports_dir)
    stems = {r["path"].stem for r in reports}
    pages = {}                                          # 相对路径 → 内容
    for r in reports:
        pages[f"reports/{r['path'].stem}.html"] = _render_report_page(r)

    # 详情页：主线 + 个股（零JS真实导航）
    for l in state.load_storylines().get("lines", []):
        pages[f"storylines/{l['id']}.html"] = _render_story_page(l, reports)
    for w in load_watchlist():
        pages[f"stocks/{str(w.get('code', '')).zfill(6)}.html"] = _render_stock_page(w, reports)

    pages["index.html"] = _render_index(reports, stems)
    pages["archive.html"] = _render_archive_page(reports)
    pages["storylines.html"] = _render_storylines_page()
    pages["patterns.html"] = _render_patterns_page()
    pages["sentiment.html"] = _render_sentiment_page()
    pages["ledger.html"] = _render_ledger_page(stems)
    pages["assets/style.css"] = _STYLE_CSS

    for rel, content in pages.items():
        p = out_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    # 清孤儿页（改名/删除的报告残留）；被浏览器占用则告警跳过，下次重建再清
    removed = 0
    for p in out_dir.rglob("*.html"):
        if p.relative_to(out_dir).as_posix() not in pages:
            try:
                p.unlink()
                removed += 1
            except OSError:
                print(f"[Site] 旧页面被占用，本次跳过清理: {p.name}")
    for d in sorted((x for x in out_dir.rglob("*") if x.is_dir()),
                    key=lambda x: -len(x.parts)):        # 深度优先，清空目录
        try:
            d.rmdir()
        except OSError:
            pass
    if removed:
        print(f"[Site] 已清理 {removed} 个过期页面")
    return {"reports": len(reports), "out": str(out_dir)}


# ── 报告扫描与渲染 ──

def _scan_reports(reports_dir: Path) -> list:
    out = []
    for p in reports_dir.glob("*.md"):
        if p.stem.endswith("-preview"):
            continue
        m = re.match(r"^(\d{4}-\d{2}-\d{2})(?:-([a-z0-9-]+))?$", p.stem)
        if not m:
            print(f"[Site] 跳过无法解析的文件: {p.name}")
            continue
        suffix = m.group(2) or ""
        meta = REPORT_TYPES.get(suffix)
        if meta is None:
            print(f"[Site] 跳过未知栏目后缀: {p.name}")
            continue
        out.append({"date": m.group(1), "suffix": suffix, "label": meta[0],
                    "emoji": meta[1], "order": meta[2], "fkey": meta[3], "path": p})
    out.sort(key=lambda r: r["order"])                    # 同日内 晨报→周报
    out.sort(key=lambda r: r["date"], reverse=True)       # 日期倒序（稳定排序保留栏内序）
    return out


def _md_to_web_html(md: str) -> str:
    """报告md → 网页HTML：转义 → 链接可点击 → 复用结构转换（不用公众号内联样式）"""
    text = html.escape(md, quote=False)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
                  lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener">{m.group(1)}</a>',
                  text)
    return _md_to_html(text)


def _digest(md: str, n: int = 60) -> str:
    """报告md → 首段摘要（归档列表用）：剥离标题/列表/加粗/链接语法"""
    for line in md.splitlines():
        s = line.strip()
        s = re.sub(r"^(#{1,6}\s*|[-*+>]\s*|\d+\.\s*)+", "", s)
        s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
        s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s).strip("` ")
        if len(s) >= 10:
            return s[:n] + ("…" if len(s) > n else "")
    return ""


def _page_head(title: str, back_depth: int = 1) -> str:
    back = "../" * back_depth + "index.html"
    return (f'<header class="page-top"><a href="{back}">← 驾驶舱</a>'
            f'<span class="page-title">{title}</span></header>')


def _render_report_page(r: dict) -> str:
    md = r["path"].read_text(encoding="utf-8")
    title = f"{r['date']} · {r['label']}"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} | AI盘报</title>
<link rel="stylesheet" href="../assets/style.css">
</head>
<body>
{_page_head(f"{r['emoji']} {html.escape(title)}")}
<main class="report">
{_md_to_web_html(md)}
</main>
<footer class="page-foot">
⚠️ 免责声明：本报告由AI基于公开信息生成，仅供学习参考，不构成任何投资建议。
数据来源：华尔街见闻、东方财富 ｜ AI分析：DeepSeek
</footer>
</body>
</html>"""


def _related_reports(reports: list, keyword: str, limit: int = 30) -> list:
    """正文含关键词的报告（详情页"相关报告"用）"""
    out = []
    for r in reports:
        if keyword in r["path"].read_text(encoding="utf-8"):
            out.append(r)
    return out[:limit]


def _report_links(reports: list, prefix="") -> str:
    items = "".join(
        f'<li><a href="{prefix}reports/{r["path"].stem}.html">'
        f'<span class="badge-col">{r["emoji"]} {r["label"]}</span>'
        f'<span class="arch-date">{r["date"][5:]}</span></a></li>'
        for r in reports)
    return f'<ul class="archive compact">{items or "<li class=muted>无相关报告</li>"}</ul>'


# ── 详情页：主线 ──

def _render_story_page(l: dict, reports: list) -> str:
    cls = _STATUS_CLASS.get(l.get("status", ""), "")
    logs = l.get("log") or []
    shown = list(reversed(logs))
    related = _related_reports(reports, l.get("name", ""))
    head = (f'<div class="story-head"><span class="badge {cls}">{html.escape(l.get("status", ""))}</span>'
            f'<strong>#{l.get("id")} {html.escape(l.get("name", ""))}</strong>'
            f'<span class="weeks">第{max(l.get("weeks", 1), 1)}周</span></div>')
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>主线 #{l.get("id")} {html.escape(l.get("name", ""))} | AI盘报</title>
<link rel="stylesheet" href="../assets/style.css">
</head>
<body>
{_page_head(f"📌 主线 #{l.get('id')} {html.escape(l.get('name', ''))}")}
<main class="report detail">
{head}
{_stepper_html(l, shown_logs=shown)}
<p class="progress">{html.escape(l.get("progress", "") or "—")}</p>
<h3>完整迁移史（{len(logs)}条，新→旧）</h3>
{_timeline_html(shown, sid=l.get('id', '')) or '<p class="muted">暂无记录</p>'}
<h3>相关报告（正文提及本主线，{len(related)}篇）</h3>
{_report_links(related)}
</main>
<footer class="page-foot">⚠️ 本页由归档自动聚合，仅供参考，不构成投资建议</footer>
</body>
</html>"""


# ── 详情页：个股 ──

def _render_stock_page(w: dict, reports: list) -> str:
    name, code = w.get("name", ""), str(w.get("code", "")).zfill(6)
    short = re.sub(r"^(中国|贵州|北京|上海|深圳|江苏|浙江|山东|四川|河南|湖北|湖南|"
                   r"广东|福建|安徽|河北)", "", name) or name
    hits = _related_reports(reports, name)
    if name != short:                                  # 全名/简称双检索（报告惯用简称）
        hits += _related_reports(reports, short, 60)
    uniq, seen = [], set()
    for r in hits:
        if r["path"].stem not in seen:
            seen.add(r["path"].stem)
            uniq.append(r)
    uniq.sort(key=lambda r: (r["date"], r["order"]), reverse=True)
    wps = [x for x in state.load_watchpoints().get("active", [])
           if x.get("code") == code]
    wp_html = "".join(
        f'<li><span class="wp-kind">{html.escape(x.get("kind", ""))}</span>'
        f'{html.escape(x.get("stock", ""))}（节点 {x.get("date", "")[5:]}）· {html.escape(x.get("status", ""))}</li>'
        for x in wps)
    lhist = state.load_label_history(code)

    def _px(h: dict) -> str:
        if h.get("price") is not None and h.get("chg_pct") is not None:
            return f"{h['price']}({h['chg_pct']:+.2f}%)"
        return ""

    lrows = "".join(
        f'<tr><td>{h["date"][5:]}</td>'
        f'<td><span class="lab {_lab_cls(h.get("label", ""))}">{html.escape(h.get("label", ""))}</span></td>'
        f'<td>{_px(h)}</td>'
        f'<td>{html.escape(h.get("reason", ""))}</td></tr>'
        for h in lhist)
    note = f'<p class="muted">关注理由：{html.escape(w.get("note", "") or "—")}</p>' if w.get("note") else ""
    ltable = (f'<h3>近期分诊（{len(lhist)}天）</h3>'
              '<table class="judgments"><tr><th>日期</th><th>标签</th><th>收盘</th><th>规则理由</th></tr>'
              f"{lrows}</table>") if lhist else ""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(name)}({code}) | AI盘报</title>
<link rel="stylesheet" href="../assets/style.css">
</head>
<body>
{_page_head(f"🔍 {html.escape(name)}({code})")}
<main class="report detail">
{note}
{ltable}
<h3>挂起观察点（{len(wps)}）</h3>
<ul class="watchpoints">{wp_html or '<li class="muted">无挂起项</li>'}</ul>
<h3>相关报告（正文提及，{len(uniq)}篇）</h3>
{_report_links(uniq)}
</main>
<footer class="page-foot">⚠️ 本页由归档自动聚合；实时分诊标签见当日推送</footer>
</body>
</html>"""


# ── 驾驶舱四区 ──

def _lab_cls(label: str) -> str:
    if label.startswith("🔴"):
        return "lab-red"
    if label.startswith("🟡"):
        return "lab-yel"
    if label.startswith("🟢"):
        return "lab-grn"
    return "lab-gray"


def _zone_sentinel() -> str:
    watch = [w for w in load_watchlist() if w.get("name")]
    if not watch:
        return '<p class="muted">（自选列表为空）</p>'
    day, labels = state.load_latest_labels()
    rows, flagged = [], []                             # flagged: (名字, 标签) 🔴/🟡
    for w in watch:
        code = str(w.get("code", "")).zfill(6)
        name = w.get("name", "")
        e = labels.get(code)
        if e:
            lab = e.get("label", "") or "⚪无快照"
            px = (f'{e.get("price")}({e.get("chg_pct"):+.2f}%)'
                  if e.get("price") is not None and e.get("chg_pct") is not None else "")
            why = e.get("reason", "")
        else:
            lab, px, why = "⚪无快照", "", "当日未采集"
        if lab.startswith(("🔴", "🟡")):
            flagged.append((name, lab))
        rows.append((name, code, lab, px, why))
    # 结论先行：有事点名、无事盖章——99%的日子扫一眼就能走
    if not day:
        head = '<p class="muted">自选分诊（尚无快照，晨报/晚报运行后生成）：</p>'
        calm = False
    elif flagged:
        has_red = any(l.startswith("🔴") for _, l in flagged)
        names = [n for n, _ in flagged]
        shown = "、".join(names[:3]) + (f" 等{len(names)}只" if len(names) > 3 else "")
        icon, kind = ("🔴", "需注意") if has_red else ("🟡", "观察中")
        head = (f'<p class="tri {"tri-red" if has_red else "tri-yel"}">'
                f'{icon} {day[5:]} {len(names)}只{kind}：{html.escape(shown)}'
                f'<span class="tri-tip">点击个股看详情</span></p>')
        calm = False
    else:
        head = (f'<p class="tri tri-grn">✅ {day[5:]} 巡检通过：无🔴/🟡异常信号'
                f'<span class="tri-tip">点击个股看详情</span></p>')
        calm = True
    flag_names = {n for n, _ in flagged}
    items = "".join(
        f'<li class="stock-row{" dim" if day and (calm or name not in flag_names) else ""}">'
        f'<a class="stock-link" href="stocks/{code}.html">'
        f'{html.escape(name)}</a><span class="lab {_lab_cls(lab)}">{html.escape(lab)}</span>'
        f'<span class="px">{px}</span><span class="why">{html.escape(why)}</span></li>'
        for name, code, lab, px, why in rows)
    active = state.load_watchpoints().get("active", [])
    wp_items = "".join(
        f'<li><span class="wp-kind">{html.escape(w.get("kind", ""))}</span>'
        f'<a href="stocks/{w.get("code", "")}.html">{html.escape(w.get("stock", ""))}</a>'
        f'（节点 {w.get("date", "")[5:]}）</li>'
        for w in sorted(active, key=lambda x: x.get("date", ""))[:8])
    return f"""
{head}
<ul class="stock-list">{items}</ul>
<h3>挂起观察点（{len(active)}）</h3>
<ul class="watchpoints">{wp_items or '<li class="muted">无挂起项</li>'}</ul>"""


_STAGES = ["孕育", "发酵", "主导", "退潮", "终结"]
_STAGE_COLOR = {"孕育": "#94a3b8", "发酵": "#f59e0b", "主导": "#ef4444",
                "退潮": "#3b82f6", "终结": "#475569"}


def _stage_idx(s: str) -> int:
    return _STAGES.index(s) if s in _STAGES else 0


def _max_stage(line: dict) -> int:
    """历史上到达过的最远阶段（含回退前的位置，用于点亮走过的路）"""
    cur = _stage_idx(line.get("status", "孕育"))
    for g in line.get("log") or []:
        m = re.search(r"状态:\s*(\S+?)→(\S+)", g.get("text", ""))
        if m and m.group(2) in _STAGES:
            cur = max(cur, _stage_idx(m.group(2)))
    return cur


def _stepper_html(line: dict, shown_logs: list | None = None) -> str:
    """生命周期轨道：走过的站点点亮，当前站高亮环。

    每个 done/cur 站点同时作为"被定位目标"和"跳向时间轴"的锚点：
      - 轨道站带 id="step-{主线ID}-{站名}"，供时间轴反向跳转
      - 轨道站带 href="#st-{主线ID}-{站名}-{序号}"，可跳到 shown_logs 里的对应条目
    shown_logs 与调用处的时间轴保持一致（首页取最近 5 条，详情页取完整）。
    """
    sid = line.get("id", "")
    cur, far = _stage_idx(line.get("status", "孕育")), _max_stage(line)
    steps = []
    # 基于实际渲染的那批 logs 建立锚点，避免目标不存在
    logs = list(shown_logs) if shown_logs is not None else list(reversed(line.get("log") or []))
    stage_seq = {}
    birth_stage = None
    for seq, g in enumerate(logs):
        text = g.get("text", "")
        if "状态:" in text:
            m = re.search(r"状态:\s*\S+?→([^；;，,\s]+)", text)
            dest = m.group(1) if m else ""
            if dest in _STAGES:
                stage_seq.setdefault(dest, seq)
        elif "✨" in text and birth_stage is None:
            birth_stage = line.get("status", "孕育")
            stage_seq.setdefault(birth_stage, seq)
    for i, st in enumerate(_STAGES):
        cls = "cur" if i == cur else ("done" if i <= far else "")
        color = _STAGE_COLOR[st]
        anchor_id = f'id="step-{sid}-{st}"'
        if cls and st in stage_seq:
            href = f'href="#st-{sid}-{st}-{stage_seq[st]}"'
            steps.append(f'<a class="step {cls}" style="--c:{color}" {anchor_id} {href}><i></i>{st}</a>')
        else:
            steps.append(f'<span class="step {cls}" style="--c:{color}" {anchor_id}><i></i>{st}</span>')
    return f'<div class="stepper">{"".join(steps)}</div>'


def _timeline_html(logs: list, sid: str = "") -> str:
    """log条目 → 带连线与彩色节点的时间轴。

    节点配色与生命周期轨道严格对应：
      状态迁移节点 → 用"到达站"的颜色（如 发酵→主导=红点，与轨道主导站同色）
      ✨诞生=青绿 / 🏁终结=终结站深灰 / 普通进展=灰（不对应任何站）

    状态迁移条目自带 id="st-{主线ID}-{到达站}-{序号}"，与轨道节点共享锚点。
    """
    items = []
    for seq, g in enumerate(logs):                # 新→旧，与卡片一致；seq 与 stepper 查找顺序一致
        text = g.get("text", "")
        style = ""
        anchor = ""
        text_html = html.escape(text)
        ev = ""
        if "✨" in text:
            ev = "ev-birth"
        elif "🏁" in text:
            ev = "ev-end"
        elif "状态:" in text:
            ev = "ev-move"
            m = re.search(r"状态:\s*\S+?→([^；;，,\s]+)", text)   # 到达站（截到首个分隔符）
            dest = m.group(1) if m else ""
            if dest in _STAGE_COLOR:              # 到达站着色：与轨道节点一一对应
                style = f' style="--evc:{_STAGE_COLOR[dest]}"'
            if sid and dest in _STAGES:
                anchor = f' id="st-{sid}-{dest}-{seq}"'
                # 迁移条目本身也是跳回轨道站的链接
                text_html = (f'<a href="#step-{sid}-{dest}" class="tl-link">'
                             f'{html.escape(text)}</a>')
        items.append(f'<li class="{ev}"{style}{anchor}><span class="d">{g.get("date", "")[5:]}</span>'
                     f'<span class="t">{text_html}</span></li>')
    return f'<ul class="tl">{"".join(items)}</ul>'


def _log_items(logs: list, with_year: bool = False) -> str:
    """log条目 → <li>HTML"""
    return "".join(
        f'<li><span class="d">{g.get("date", "")[5:] if not with_year else g.get("date", "")}</span>'
        f' {html.escape(g.get("text", ""))}</li>'
        for g in reversed(logs))


def _story_card(l: dict) -> str:
    """单张主线卡（首页与档案页共用）"""
    cls = _STATUS_CLASS.get(l.get("status", ""), "")
    logs = l.get("log") or []
    shown_logs = list(reversed(logs[-5:])) if logs else []
    more = len(logs) - 5
    extra = (f'<details><summary>展开更早迁移史（{more}条）</summary>'
             f'<ul class="story-log">{_log_items(logs[:-5])}</ul></details>'
             ) if more > 0 else ""
    ended_cls = " ended" if l.get("status") == "终结" else ""
    ended_flag = '<span class="ended-flag">已完结</span>' if ended_cls else ""
    return f"""
<div class="story{ended_cls}">
  {ended_flag}<a class="story-head" href="storylines/{l['id']}.html">
    <span class="badge {cls}">{html.escape(l.get("status", ""))}</span>
    <strong>#{l.get("id")} {html.escape(l.get("name", ""))}</strong>
    <span class="weeks">第{max(l.get("weeks", 1), 1)}周 →</span>
  </a>
  {_stepper_html(l, shown_logs=shown_logs)}
  <p class="progress">{html.escape(l.get("progress", "") or "—")}</p>
  {_timeline_html(shown_logs, sid=l.get('id', ''))}
  {extra}
</div>"""


def _zone_translator() -> str:
    """首页翻译官区：只展示连载中的主线；完结主线收进档案页"""
    lines = state.load_storylines().get("lines", [])
    if not lines:
        return '<p class="muted">尚无登记主线（晨报运行后开始连载）</p>'
    active = [l for l in lines if l.get("status") != "终结"]
    ended = [l for l in lines if l.get("status") == "终结"]
    cards = "".join(_story_card(l) for l in active) or \
        '<p class="muted">当前无连载中的主线（市场叙事切换中）</p>'
    archive_link = (f'<a class="pat-all" href="storylines.html">'
                    f'📖 主线档案：全部{len(lines)}条（含已完结{len(ended)}条）→</a>'
                    ) if ended or len(lines) > len(active) else (
                    f'<a class="pat-all" href="storylines.html">📖 主线档案 · 全部{len(lines)}条 →</a>')
    return cards + archive_link


# 情绪五区定义（色带+解读共用）：(下界,上界,区名,白话含义,用色)
_SENT_ZONES = [
    (0, 20, "冰点", "恐慌极端——历史上此处恐慌割肉的赔率最差，想动之前先看数据", "#1d4ed8"),
    (20, 40, "偏冷", "偏弱但非极端——正常应对，无需恐慌加码", "#60a5fa"),
    (40, 60, "中性", "市场没表态——此区读数信息量最低，别过度解读", "#9ca3af"),
    (60, 80, "偏热", "偏强但非极端——正常应对，纪律优先于情绪", "#f59e0b"),
    (80, 101, "亢奋", "情绪透支——此区追高的历史赔率差，冲动之前先冷静", "#ef4444"),
]


def _sentiment_bar(score: int) -> str:
    """0-100五区色带 + 当前位置标记（标记嵌在色带正上方，无间隙）"""
    segs = "".join(
        f'<span class="seg" style="flex:{hi - lo};background:{c}" title="{n}区 {lo}-{hi - 1}"></span>'
        for lo, hi, n, _, c in _SENT_ZONES)
    return (f'<div class="sent-bar">'
            f'<div class="marker" style="left:{score}%"><b>{score}</b><i></i></div>'
            f'<div class="segs">{segs}</div>'
            f'</div>'
            f'<div class="zone-labels"><span>0</span><span>20</span><span>40</span>'
            f'<span>60</span><span>80</span><span>100</span></div>')


def _sentiment_reading(entries: list, hist: dict) -> str:
    """情绪读数的白话解读：诊断卡（区名+建议）+ 带类型标签的指标行"""
    latest_d, latest_s = entries[-1]
    zone = next(z for z in _SENT_ZONES if z[0] <= latest_s < z[1])
    rows = []                                            # (标签, 内容)
    b = (hist.get(latest_d) or {})
    up, down = b.get("up"), b.get("down")
    if up and down:
        ratio = up / max(down, 1)
        shape = "明显普涨" if ratio >= 1.5 else ("明显普跌" if ratio <= 0.67 else "涨跌互现")
        rows.append(("宽度", f"上涨{up}家 / 下跌{down}家，{shape}"))
    scores = [s for _, s in entries]
    n_avg = min(len(scores), 20)
    if len(scores) >= 10:
        avg = sum(scores[-20:]) / n_avg
        delta = latest_s - avg
        if delta > 3:
            rows.append(("位置", f"高于近{n_avg}日均值 <b>{avg:.0f}</b> 达 {delta:.0f} 分，偏热一侧"))
        elif delta < -3:
            rows.append(("位置", f"低于近{n_avg}日均值 <b>{avg:.0f}</b> 达 {-delta:.0f} 分，偏冷一侧"))
        else:
            rows.append(("位置", f"与近{n_avg}日均值 <b>{avg:.0f}</b> 基本持平"))
    if len(scores) >= 3:
        a, c = scores[-2], scores[-3]
        trend = "连升 ↑" if latest_s > a > c else ("连降 ↓" if latest_s < a < c else "反复震荡 ↔")
        rows.append(("趋势", f"近3日{trend}"))
    details = "".join(f'<li><span class="tag">{t}</span>{c}</li>' for t, c in rows)
    return (f'<div class="sent-reading" style="--zc:{zone[4]}">'
            f'<div class="sent-headline">'
            f'<span class="sent-zone">{zone[2]}区</span>'
            f'<span class="sent-hint">{zone[3]}</span></div>'
            f'<ul class="sent-details">{details}</ul>'
            f'</div>')


def _zone_name(score: int) -> str:
    return "冰点" if score < 20 else "偏冷" if score < 40 else \
           "中性" if score < 60 else "偏热" if score < 80 else "亢奋"


def _zone_anchor(stems: set) -> str:
    hist = state._load_json("sentiment_history.json", {})
    entries = sorted((d, v.get("score")) for d, v in hist.items() if v.get("score") is not None)
    if not entries:
        return '<p class="muted">情绪归档积累中（晚报运行后每日记录）</p>'
    latest_d, latest_s = entries[-1]
    scores = [s for _, s in entries]
    if len(scores) < 10:
        compare = f"积累中（第{len(scores)}天，满10天起提供均值对照）"
    else:
        avg = sum(scores[-20:]) / min(len(scores), 20)
        compare = f"近{min(len(scores), 20)}日均值 {avg:.0f}"
    svg = _sparkline_svg(entries[-30:], stems)      # 首页曲线只取最近30点，避免越挤越密
    label = _zone_name(latest_s)
    zc = next(z for z in _SENT_ZONES if z[0] <= latest_s < z[1])[4]
    return f"""
<div class="sent-head">
  <span class="big-number">{latest_s}<span class="unit">/100</span></span>
  <span class="sent-sub"><b class="label" style="color:{zc}">{label}</b>　{latest_d[5:]}{compare}　<span class="hint">点击曲线看当日晚报 · 温度描述状态，非操作信号</span></span>
</div>
{_sentiment_bar(latest_s)}
{svg or ''}
{_sentiment_reading(entries, hist)}
<a class="pat-all" href="sentiment.html">🌡️ 温度档案 · 逐日明细（{len(entries)}天） →</a>"""


def _render_sentiment_page() -> str:
    """温度档案页：全历史曲线 + 五区天数分布 + 逐日明细"""
    hist = state._load_json("sentiment_history.json", {})
    entries = sorted((d, v.get("score")) for d, v in hist.items() if v.get("score") is not None)
    if not entries:
        return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>温度档案 | AI盘报</title><link rel="stylesheet" href="assets/style.css"></head>
<body>{_page_head("🌡️ 温度档案")}
<main class="report detail"><p class="muted">情绪归档积累中（晚报运行后每日记录）</p></main>
<footer class="page-foot">⚠️ 本页由归档自动聚合，仅供参考，不构成投资建议</footer></body></html>"""

    scores = [s for _, s in entries]
    hi = max(entries, key=lambda t: t[1])
    lo = min(entries, key=lambda t: t[1])
    # 五区天数分布
    zone_days = {z[2]: 0 for z in _SENT_ZONES}
    for _, s in entries:
        zone_days[_zone_name(s)] += 1
    dist_bar = "".join(
        f'<div class="zrow"><span class="zname" style="color:{z[4]}">{z[2]}</span>'
        f'<span class="zbar"><i style="width:{zone_days[z[2]] / len(entries) * 100:.0f}%;background:{z[4]}"></i></span>'
        f'<span class="zn">{zone_days[z[2]]}天</span></div>'
        for z in _SENT_ZONES)
    most = max(zone_days, key=zone_days.get)
    # 全历史曲线（首页只显示30点，这里全量）
    svg = _sparkline_svg(entries, set()) or ""
    # 逐日明细表（新→旧）
    rows = "".join(
        f'<tr><td>{d}</td><td><b style="color:{next(z[4] for z in _SENT_ZONES if z[0] <= s < z[1])}">{s}</b></td>'
        f'<td>{_zone_name(s)}</td>'
        f'<td>{(hist.get(d) or {}).get("up") or "—"}</td><td>{(hist.get(d) or {}).get("down") or "—"}</td></tr>'
        for d, s in reversed(entries))
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>温度档案 · 逐日情绪明细 | AI盘报</title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
{_page_head("🌡️ 温度档案 · 逐日情绪明细")}
<main class="report detail">
<p class="muted">市场每天的温度记录。看两个东西：当前位置在历史什么分位；极端温度之后市场通常怎么走。</p>
<div class="tiles">
  <div class="tile"><span class="num">{len(entries)}</span><span class="cap">累计记录(天)</span></div>
  <div class="tile"><span class="num">{hi[1]}</span><span class="cap">最高 {hi[0][5:]}</span></div>
  <div class="tile"><span class="num">{lo[1]}</span><span class="cap">最低 {lo[0][5:]}</span></div>
  <div class="tile"><span class="num">{most}</span><span class="cap">最常驻区</span></div>
</div>
<h3>全历史曲线（{entries[0][0][5:]} ~ {entries[-1][0][5:]}）</h3>
{svg}
<h3>五区天数分布</h3>
<div class="zdist">{dist_bar}</div>
<h3>逐日明细（{len(entries)}天，新→旧）</h3>
<table class="judgments"><tr><th>日期</th><th>读数</th><th>区</th><th>上涨家数</th><th>下跌家数</th></tr>{rows}</table>
</main>
<footer class="page-foot">⚠️ 本页由归档自动聚合，仅供参考，不构成投资建议</footer>
</body>
</html>"""


def _render_storylines_page() -> str:
    """主线档案页：连载中一组 + 已完结一组（完整市场叙事史）"""
    lines = state.load_storylines().get("lines", [])
    active = [l for l in lines if l.get("status") != "终结"]
    ended = [l for l in lines if l.get("status") == "终结"]
    active_html = ("".join(_story_card(l) for l in active)
                   or '<p class="muted">当前无连载中的主线</p>')
    ended_html = ("".join(_story_card(l) for l in ended)
                  or '<p class="muted">尚无完结主线</p>')
    total_weeks = sum(max(l.get("weeks", 1), 1) for l in lines)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>主线档案 · 市场叙事史 | AI盘报</title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
{_page_head("📖 主线档案 · 市场叙事史")}
<main class="report detail">
<div class="tiles">
  <div class="tile"><span class="num">{len(lines)}</span><span class="cap">累计主线</span></div>
  <div class="tile"><span class="num">{len(active)}</span><span class="cap">连载中</span></div>
  <div class="tile"><span class="num">{len(ended)}</span><span class="cap">已完结</span></div>
  <div class="tile"><span class="num">{total_weeks}</span><span class="cap">累计周数</span></div>
</div>
<h3>连载中（{len(active)}）</h3>
{active_html}
<h3>已完结（{len(ended)}）——它们如何走完一生</h3>
{ended_html}
</main>
<footer class="page-foot">⚠️ 本页由归档自动聚合，仅供参考，不构成投资建议</footer>
</body>
</html>"""


def _render_patterns_page() -> str:
    """模式库完整页：统计面板（按方向分组胜率）+ 全量验证列表"""
    archive = state._load_json("event_archive.json", [])
    n_hit = sum(1 for e in archive if "✓" in str(e.get("result", "")))
    rate = f"{round(n_hit / len(archive) * 100)}%" if archive else "—"

    # 按方向（看多/看空/…）分组统计：各方向自己的胜率
    groups = {}
    for e in archive:
        mark = e.get("mark", "未标注")
        g = groups.setdefault(mark, {"total": 0, "hit": 0})
        g["total"] += 1
        if "✓" in str(e.get("result", "")):
            g["hit"] += 1
    grp_tiles = "".join(
        f'<div class="tile"><span class="num">{g["hit"]}/{g["total"]}</span>'
        f'<span class="cap">{html.escape(k)}验证通过</span></div>'
        for k, g in sorted(groups.items()))

    rows = "".join(
        f'<li><span class="d">{e.get("date", "")}</span>'
        f'「{html.escape(e.get("title", ""))}」<span class="mark">{html.escape(e.get("mark", ""))}</span>'
        f'→ {html.escape(e.get("vs", ""))} <b class="res {_res_cls(str(e.get("result", "")))}">'
        f'{html.escape(e.get("result", ""))}</b></li>'
        for e in reversed(archive))
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>模式库 · 全部验证记录 | AI盘报</title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
{_page_head("🧪 模式库 · 全部验证记录")}
<main class="report detail">
<p class="muted">每条要闻的方向判断，次日用实际行情打分。积累越厚，"看到消息就想动"的直觉越有参照。</p>
<div class="tiles">
  <div class="tile"><span class="num">{len(archive)}</span><span class="cap">累计验证(例)</span></div>
  <div class="tile"><span class="num">{n_hit}</span><span class="cap">验证通过(例)</span></div>
  <div class="tile"><span class="num">{rate}</span><span class="cap">总通过率</span></div>
</div>
<div class="tiles">{grp_tiles}</div>
<h3>全部记录（{len(archive)}例，新→旧）</h3>
<ul class="pattern-list">{rows}</ul>
</main>
<footer class="page-foot">⚠️ 本页由归档自动聚合，仅供参考，不构成投资建议</footer>
</body>
</html>"""


def _sparkline_svg(entries: list, stems: set = None, w: int = 560, h: int = 150) -> str:
    """[(date, score)] → 内联SVG折线（面积渐变填充+可见数据点；点可点击直达当日晚报）。"""
    stems = stems or set()
    n = len(entries)
    if n < 2:
        return ""
    pad, mid = 12, 50
    x = lambda i: pad + i * (w - 2 * pad) / (n - 1)          # noqa: E731
    y = lambda s: pad + (100 - s) * (h - 2 * pad) / 100      # noqa: E731
    pts = " ".join(f"{x(i):.1f},{y(s):.1f}" for i, (_, s) in enumerate(entries))
    area = f"{x(0):.1f},{h - pad} {pts} {x(n - 1):.1f},{h - pad}"
    lx, ly = x(n - 1), y(entries[-1][1])
    dots = []
    for i, (d, s) in enumerate(entries):
        if f"{d}-evening" in stems:
            dots.append(f'<a href="reports/{d}-evening.html" class="pt" aria-label="{d} 情绪{s}">'
                        f'<circle cx="{x(i):.1f}" cy="{y(s):.1f}" r="9"/></a>')
        else:
            dots.append(f'<circle cx="{x(i):.1f}" cy="{y(s):.1f}" r="2.5" class="dim"/>')
    return f"""<svg viewBox="0 0 {w} {h}" class="spark" role="img" aria-label="情绪刻度曲线（数据点可点击）">
<defs><linearGradient id="sgrad" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#f59e0b" stop-opacity="0.28"/>
<stop offset="1" stop-color="#f59e0b" stop-opacity="0.02"/>
</linearGradient></defs>
<line x1="{pad}" y1="{y(mid):.1f}" x2="{w - pad}" y2="{y(mid):.1f}" class="mid"/>
<polygon points="{area}" fill="url(#sgrad)"/>
<polyline points="{pts}" class="line"/>
{"".join(dots)}
<circle cx="{lx:.1f}" cy="{ly:.1f}" r="4" class="dot"/>
<text x="{pad}" y="{h - 2}" class="tick">{entries[0][0][5:]}</text>
<text x="{w - pad}" y="{h - 2}" text-anchor="end" class="tick">{entries[-1][0][5:]}</text>
</svg>"""


def _res_cls(text: str) -> str:
    """验证结果着色纪律：✓绿、部分琥珀、✗/❌灰——历史账不是当日警报，不抢红色"""
    if "✓" in text:
        return "r-ok"
    if "✗" in text or "❌" in text:
        return "r-miss"
    return "r-part"


def _judgment_rows(rows: list, stems: set, limit_days: int | None = None) -> list[str]:
    """流水行 → <tr>；limit_days=N 只取最近N个交易日（按出现过的日期去重）"""
    ordered = sorted(rows, key=lambda r: r.get("date", ""), reverse=True)
    if limit_days is not None:
        dates = []
        for r in ordered:
            if r.get("date", "") not in dates:
                dates.append(r.get("date", ""))
        keep = set(dates[:limit_days])
        ordered = [r for r in ordered if r.get("date", "") in keep]
    trs = []
    for r in ordered:
        d = r.get("date", "")
        link = (f'<a href="reports/{d}.html">{d[5:]}</a>' if d in stems else d[5:])
        res = r.get("result", "")
        trs.append(f"<tr><td>{link}</td><td>{html.escape(r.get('judgment', ''))}</td>"
                   f"<td>{html.escape(r.get('actual', ''))}</td>"
                   f"<td><span class=\"{_res_cls(res)}\">{html.escape(res)}</span></td></tr>")
    return trs


def _pattern_block() -> str:
    """要闻方向验证（模式库）摘要块——记分牌区第二块验证资产"""
    archive = state._load_json("event_archive.json", [])
    if not archive:
        return ('<div class="anchor-sub">🧪 要闻方向验证 · 模式库</div>'
                '<p class="muted">模式库积累中（晚报运行后每日归档要闻方向验证）</p>')
    n_hit = sum(1 for e in archive if "✓" in str(e.get("result", "")))
    rate = round(n_hit / len(archive) * 100)
    rows = "".join(
        f'<li><span class="d">{str(e.get("date", ""))[5:]}</span>'
        f'「{html.escape(e.get("title", ""))}」<span class="mark">{html.escape(e.get("mark", ""))}</span>'
        f'→ {html.escape(e.get("vs", ""))} <b class="res {_res_cls(str(e.get("result", "")))}">'
        f'{html.escape(e.get("result", ""))}</b></li>'
        for e in reversed(archive[-8:]))
    all_link = (f'<a class="pat-all" href="patterns.html">查看全部 {len(archive)} 例 →</a>'
                if len(archive) > 8 else "")
    return (f'<div class="anchor-sub">🧪 要闻方向验证 · 模式库'
            f'<span class="anchor-note">每条要闻的方向判断，次日用实际行情打分——校准"看到消息就想动"的直觉</span></div>'
            f'<p class="muted">已积累<b>{len(archive)}</b>例，验证通过{n_hit}例（{rate}%）：最近8例</p>'
            f'<ul class="pattern-list">{rows}</ul>{all_link}')


def _zone_ledger(stems: set) -> str:
    rows = state.load_judgments("0000-01-01", "9999-12-31")
    stats = weekly_judgment_stats(rows)
    n_lines = sum(1 for l in state.load_storylines().get("lines", [])
                  if l.get("status") != "终结")
    n_archive = len(state._load_json("event_archive.json", []))
    n_active_wp = len(state.load_watchpoints().get("active", []))

    rate = f"{stats['rate']}%" if stats["rate"] is not None else "—"
    tiles = f"""
<div class="tiles">
  <div class="tile"><span class="num">{rate}</span><span class="cap">判断胜率</span></div>
  <div class="tile"><span class="num">{n_lines}</span><span class="cap">活跃主线</span></div>
  <div class="tile"><span class="num">{n_archive}</span><span class="cap">模式库(例)</span></div>
  <div class="tile"><span class="num">{n_active_wp}</span><span class="cap">挂起观察点</span></div>
</div>"""
    # 第一块：每日预判记分
    if not rows:
        judge_html = ('<div class="anchor-sub">📋 每日预判记分</div>'
                      '<p class="muted">记分流水积累中（首个晨报预判快照日开账）</p>')
    else:
        trs = _judgment_rows(rows, stems, limit_days=5)   # 首页只看最近5个交易日
        n_days = len({r.get("date", "") for r in rows})
        all_link = (f'<a class="pat-all" href="ledger.html">🧾 查看全部记分流水（{len(rows)}条） →</a>'
                    if n_days > 5 else "")
        judge_html = (f'<div class="anchor-sub">📋 每日预判记分'
                      f'<span class="anchor-note">晨报对当日大盘的方向预判，收盘后对照实际打分</span></div>'
                      f'<p class="muted">得分{stats["score"]:g}/{stats["total"]}条'
                      f'（✓=1分，部分=0.5分）· 最近5个交易日（日期可点回晨报）：</p>'
                      f'<table class="judgments"><tr><th>日期</th><th>预判</th><th>实际</th><th>结果</th></tr>{"".join(trs)}</table>'
                      f'{all_link}')
    # 第二块：要闻方向验证（模式库）
    return tiles + judge_html + _pattern_block()


def _render_ledger_page(stems: set) -> str:
    """记分流水完整页：全量judgments + 统计"""
    rows = state.load_judgments("0000-01-01", "9999-12-31")
    stats = weekly_judgment_stats(rows)
    rate = f"{stats['rate']}%" if stats["rate"] is not None else "—"
    trs = _judgment_rows(rows, stems)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>记分流水 · 全部判断 | AI盘报</title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
{_page_head("🧾 记分流水 · 全部判断记录")}
<main class="report detail">
<p class="muted">每个交易日的预判 vs 实际。对错都留痕——这是系统对自己的记分，不是给你的荐股记录。</p>
<div class="tiles">
  <div class="tile"><span class="num">{len(rows)}</span><span class="cap">累计判断(条)</span></div>
  <div class="tile"><span class="num">{stats['score']:g}/{stats['total']}</span><span class="cap">累计得分</span></div>
  <div class="tile"><span class="num">{rate}</span><span class="cap">胜率</span></div>
</div>
<h3>全部流水（{len(rows)}条，新→旧，日期可点回晨报）</h3>
<table class="judgments"><tr><th>日期</th><th>预判</th><th>实际</th><th>结果</th></tr>{''.join(trs)}</table>
</main>
<footer class="page-foot">⚠️ 本页由归档自动聚合，仅供参考，不构成投资建议</footer>
</body>
</html>"""


# ── 首页 ──

_FILTER_JS = """
var fbtns=document.querySelectorAll('.fbtn');
fbtns.forEach(function(b){b.addEventListener('click',function(){
  fbtns.forEach(function(x){x.classList.remove('on')});b.classList.add('on');
  var k=b.dataset.f;
  document.querySelectorAll('.archive li[data-k]').forEach(function(li){
    li.style.display=(k==='all'||li.dataset.k===k)?'':'none';});
});});
"""


_ARCH_ORDER = ["all", "morning", "noon", "evening", "macro", "night", "weekly"]
_ARCH_NAMES = {"all": "全部", "morning": "🌅晨报", "noon": "⚡快讯", "evening": "🌙晚报",
               "macro": "📊数据", "night": "🚨警报", "weekly": "📖周报"}


def _arch_items(reports: list) -> str:
    return "".join(
        f'<li data-k="{r["fkey"]}"><a href="reports/{r["path"].stem}.html">'
        f'<span class="badge-col">{r["emoji"]} {r["label"]}</span>'
        f'<span class="arch-date">{r["date"][5:]}</span>'
        f'<span class="arch-digest">{html.escape(_digest(r["path"].read_text(encoding="utf-8")))}</span></a></li>'
        for r in reports)


def _arch_filters(reports: list) -> str:
    counts = {}
    for r in reports:
        counts[r["fkey"]] = counts.get(r["fkey"], 0) + 1
    return "".join(
        f'<button class="fbtn{" on" if k == "all" else ""}" data-f="{k}">'
        f'{_ARCH_NAMES[k]}{"" if k == "all" else f" {counts.get(k, 0)}"}</button>'
        for k in _ARCH_ORDER if k == "all" or counts.get(k))


_DIGEST_SCHED = "今日节奏：晨7:30 · 午11:35 · 晚17:30 · 夜巡20:30"


def _digest_strip() -> str:
    """今日速览条：一行结论+节奏表——无事日5秒出口，有事日一眼定位；节奏表给"离开"一个许可"""
    _, labels = state.load_latest_labels()
    labs = [(labels.get(str(w.get("code", "")).zfill(6)) or {}).get("label", "")
            for w in load_watchlist() if w.get("name")]
    n_red = sum(1 for x in labs if x.startswith("🔴"))
    n_yel = sum(1 for x in labs if x.startswith("🟡"))
    if not labels:
        sent = '<a class="dg dg-gray" href="#z-sentinel">🛡️ 哨兵：尚无快照</a>'
    elif n_red:
        sent = f'<a class="dg dg-red" href="#z-sentinel">🛡️ 哨兵：🔴需注意{n_red}只</a>'
    elif n_yel:
        sent = f'<a class="dg dg-yel" href="#z-sentinel">🛡️ 哨兵：🟡观察{n_yel}只</a>'
    else:
        sent = '<a class="dg dg-grn" href="#z-sentinel">🛡️ 哨兵：✅今日无事</a>'
    n_act = sum(1 for l in state.load_storylines().get("lines", []) if l.get("status") != "终结")
    trans = f'<a class="dg" href="#z-translator">🗣️ 主线：{n_act}条连载</a>'
    ent = sorted((d, v.get("score")) for d, v in
                 state._load_json("sentiment_history.json", {}).items() if v.get("score") is not None)
    if ent:
        anchor = f'<a class="dg" href="#z-anchor">🌡️ 温度：{ent[-1][1]} {_zone_name(ent[-1][1])}</a>'
    else:
        anchor = '<a class="dg" href="#z-anchor">🌡️ 温度：积累中</a>'
    st = weekly_judgment_stats(state.load_judgments("0000-01-01", "9999-12-31"))
    rate = f"{st['rate']}%" if st["rate"] is not None else "—"
    ledger = f'<a class="dg" href="#z-ledger">🧾 胜率：{rate}</a>'
    sched = (f'<span class="dg dg-sched" title="周六09:00周报 · 午间快讯仅🟡以上推送 · 夜巡仅🔴">'
             f'⏰ {_DIGEST_SCHED}</span>')
    return f'<div class="digest">{sent}{trans}{anchor}{ledger}{sched}</div>'


def _zdelta(text: str, on: bool = False) -> str:
    """分区标题右侧的变化徽标：无变化=安静灰，有变化=本区色——人对变化敏感，对存量无感"""
    if not text:
        return ""
    return f'<span class="zdelta{" on" if on else ""}">{text}</span>'


def _delta_sentinel() -> str:
    data = state._load_json("label_history.json", {})
    days = sorted(data.keys())
    if len(days) < 2:
        return ""
    cur = {e.get("code"): e.get("label", "") for e in data[days[-1]]}
    prev = {e.get("code"): e.get("label", "") for e in data[days[-2]]}
    n = sum(1 for c, l in cur.items() if l and prev.get(c) not in ("", None, l))
    return _zdelta(f"⇄ {n}只分诊有变", on=True) if n else _zdelta("↔ 分诊无变化")


def _delta_translator(ref_d: str) -> str:
    if not ref_d:
        return ""
    logs = [g for l in state.load_storylines().get("lines", [])
            for g in (l.get("log") or []) if str(g.get("date", "")) == ref_d]
    return _zdelta(f"↗ 主线新动态×{len(logs)}", on=True) if logs else _zdelta("↔ 主线无新动态")


def _delta_anchor() -> str:
    ent = sorted((d, v.get("score")) for d, v in
                 state._load_json("sentiment_history.json", {}).items() if v.get("score") is not None)
    if len(ent) < 2:
        return ""
    d = ent[-1][1] - ent[-2][1]
    if not d:
        return _zdelta("↔ 与前日持平")
    return _zdelta(f"{'↑' if d > 0 else '↓'} 较前日 {'+' if d > 0 else ''}{d}", on=True)


def _delta_ledger(ref_d: str) -> str:
    if not ref_d:
        return ""
    n = sum(1 for r in state.load_judgments("0000-01-01", "9999-12-31") if r.get("date") == ref_d)
    n += sum(1 for e in state._load_json("event_archive.json", [])
             if str(e.get("date", "")) == ref_d)
    return _zdelta(f"+{n} 新账", on=True) if n else _zdelta("↔ 无新账")


def _render_index(reports: list, stems: set) -> str:
    # 归档区：首页只留最近8篇（筛选与全量在 archive.html）
    recent = reports[:8]
    archive_html = _arch_items(recent) or '<li class="muted">暂无报告归档</li>'
    all_link = (f'<a class="pat-all" href="archive.html">📚 查看全部 {len(reports)} 篇归档'
                f'（可按栏目筛选） →</a>')
    latest = f"最新内容：{reports[0]['date']}" if reports else "暂无归档"
    ref_d = reports[0]["date"] if reports else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI盘报 · 状态驾驶舱</title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
<header class="top">
  <h1>AI盘报 · 状态驾驶舱</h1><span class="sub">{latest}</span>
</header>
<main>
{_digest_strip()}
<section class="zone z-sentinel" id="z-sentinel"><h2>🛡️ 哨兵 · 我的票有事吗{_delta_sentinel()}</h2>{_zone_sentinel()}</section>
<section class="zone z-translator" id="z-translator"><h2>🗣️ 翻译官 · 市场在讲什么故事{_delta_translator(ref_d)}</h2>{_zone_translator()}</section>
<section class="zone z-anchor" id="z-anchor"><h2>⚓ 锚 · 现在什么温度{_delta_anchor()}</h2>{_zone_anchor(stems)}</section>
<section class="zone z-ledger" id="z-ledger"><h2>🧾 记分牌 · 我的判断几斤几两{_delta_ledger(ref_d)}</h2>{_zone_ledger(stems)}</section>
  <section class="zone z-archive"><h2>📰 报告归档（{len(reports)}）· 最近8篇</h2>
    <ul class="archive">{archive_html}</ul>
    {all_link}
  </section>
</main>
<footer class="page-foot">
⚠️ 免责声明：本报告由AI基于公开信息生成，仅供学习参考，不构成任何投资建议。只报道，不操盘。
</footer>
</body>
</html>"""


def _render_archive_page(reports: list) -> str:
    """归档完整页：全量列表 + 栏目筛选"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>报告归档 · 全部{len(reports)}篇 | AI盘报</title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
{_page_head(f"📰 报告归档 · 全部{len(reports)}篇")}
<main class="report detail">
<div class="filters">{_arch_filters(reports)}</div>
<ul class="archive">{_arch_items(reports) or '<li class="muted">暂无报告归档</li>'}</ul>
</main>
<footer class="page-foot">⚠️ 本页由归档自动聚合，仅供参考，不构成投资建议</footer>
<script>{_FILTER_JS}</script>
</body>
</html>"""


_STYLE_CSS = """
:root { --ink:#1e293b; --soft:#475569; --muted:#64748b; --line:#e2e8f0;
        --line2:#f1f5f9; --bg:#f4f5f7; --card:#ffffff; }
* { box-sizing:border-box; }
body { margin:0; font-family:system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
       color:var(--ink); background:var(--bg); font-size:14.5px; line-height:1.75; }
a { color:inherit; }

/* ── 页头 ── */
.top { display:flex; align-items:baseline; gap:12px; padding:20px 20px 6px; flex-wrap:wrap; }
.top h1 { font-size:19px; margin:0; letter-spacing:.5px; }
.sub { color:var(--muted); font-size:12.5px; }

/* ── 分区卡片 ── */
main { padding:6px 12px 28px; max-width:860px; margin:0 auto; }
.zone { background:var(--card); border:1px solid var(--line); border-radius:14px;
        padding:16px 18px 14px; margin:14px 0; scroll-margin-top:12px;
        box-shadow:0 1px 3px rgba(15,23,42,.04); }
.zone h2 { font-size:15px; font-weight:600; margin:0 0 12px; padding-bottom:9px;
           border-bottom:1px solid var(--line2); letter-spacing:.3px; color:var(--ink);
           display:flex; align-items:center; gap:10px; }
.z-sentinel  { border-left:3px solid #38bdf8; --zc:#38bdf8; }
.z-translator{ border-left:3px solid #a78bfa; --zc:#a78bfa; }
.z-anchor    { border-left:3px solid #fbbf24; --zc:#f59e0b; }
.z-ledger    { border-left:3px solid #34d399; --zc:#34d399; }
.z-archive   { border-left:3px solid #cbd5e1; }

/* ── 今日速览条：结论先行，5秒出口 ── */
.digest { display:flex; flex-wrap:wrap; align-items:center; gap:7px; margin:12px 0 0; }
.dg { font-size:12px; color:var(--soft); background:#f6f8fa; border:1px solid var(--line2);
      border-radius:999px; padding:4px 11px; text-decoration:none; white-space:nowrap; }
.dg:hover { border-color:#94a3b8; }
.dg-grn { background:#ecfdf5; border-color:#bbf7d0; color:#15803d; font-weight:600; }
.dg-yel { background:#fffbeb; border-color:#fde68a; color:#b45309; font-weight:600; }
.dg-red { background:#fef2f2; border-color:#fecaca; color:#b91c1c; font-weight:700; }
.dg-gray { color:var(--muted); }
.dg-sched { margin-left:auto; background:none; border:none; color:#94a3b8;
            font-size:11.5px; padding:4px 0; cursor:default; }

/* 分区变化徽标 */
.zdelta { margin-left:auto; flex:0 0 auto; font-size:11px; font-weight:500; color:#94a3b8;
          background:#f6f8fa; border-radius:999px; padding:2px 9px; white-space:nowrap; }
.zdelta.on { color:var(--zc); font-weight:600;
             background:color-mix(in srgb, var(--zc) 10%, #ffffff); }
.muted { color:var(--muted); font-size:12.5px; }
h3 { font-size:13.5px; margin:14px 0 6px; color:var(--soft); font-weight:600; }

/* ── 哨兵 ── */
.tri { margin:0 0 8px; padding:6px 11px; border-radius:9px; font-size:13px; font-weight:600; }
.tri-grn { background:#ecfdf5; color:#15803d; }
.tri-yel { background:#fffbeb; color:#b45309; }
.tri-red { background:#fef2f2; color:#b91c1c; }
.tri .tri-tip { float:right; font-weight:400; font-size:11.5px; opacity:.7; }
.stock-list { list-style:none; margin:2px 0 6px; padding:0; }
.stock-row { display:flex; align-items:center; gap:8px; flex-wrap:wrap;
             padding:7px 0; border-bottom:1px solid var(--line2); }
.stock-row:last-child { border-bottom:none; }
.stock-row.dim { opacity:.55; }            /* 无事降噪：注意力只给例外 */
.stock-row.dim:hover { opacity:1; }
.stock-link { text-decoration:none; color:#0369a1; font-weight:600; font-size:14.5px; }
.lab { display:inline-block; border-radius:999px; padding:1px 9px; font-size:11.5px; white-space:nowrap; }
.lab-red { background:#fee2e2; color:#b91c1c; }
.lab-yel { background:#fef3c7; color:#b45309; }
.lab-grn { background:#dcfce7; color:#15803d; }
.lab-gray{ background:#f1f5f9; color:#64748b; }
.px { font-size:12.5px; color:var(--soft); white-space:nowrap; font-variant-numeric:tabular-nums; }
.why { font-size:11.5px; color:var(--muted); flex:1; min-width:140px; }
.watchpoints { margin:4px 0; padding-left:18px; }
.watchpoints li { margin:3px 0; font-size:13.5px; }
.watchpoints a { color:#0369a1; text-decoration:none; }
.wp-kind { display:inline-block; background:#f1f5f9; border-radius:6px;
           padding:0 6px; margin-right:6px; font-size:11.5px; color:var(--muted); }

/* ── 主线卡片 ── */
.story { border:1px solid var(--line2); border-radius:11px; padding:11px 14px; margin:10px 0;
         background:#fcfdfe; }
.story-head { display:flex; align-items:center; gap:8px; text-decoration:none; color:inherit; }
.story-head .badge { flex:0 0 auto; }
.story-head strong { font-size:14.5px; flex:1 1 auto; min-width:0; }
.story-head:hover strong { color:#7c3aed; }
.weeks { color:var(--muted); font-size:12px; white-space:nowrap; }

/* 生命周期轨道 */
.stepper { display:flex; margin:10px 0 4px; }
.step { flex:1; position:relative; text-align:center; font-size:9.5px; color:#94a3b8;
         text-decoration:none; }
.step i { display:block; width:9px; height:9px; border-radius:50%; margin:0 auto 4px;
          background:#e2e8f0; border:2px solid #e2e8f0; position:relative; z-index:1; }
.step[href^="#st-"] { cursor:pointer; text-decoration:underline; text-decoration-style:dotted;
                          text-decoration-color:var(--c); text-underline-offset:3px; }
.step[href^="#st-"]:hover { opacity:.85; }
.step[href^="#st-"]:hover i { transform:scale(1.15); }
.step:target i { animation:pulse-ring .9s ease; }
@keyframes pulse-ring {
  0%   { box-shadow:0 0 0 3px #fff, 0 0 0 5px var(--c), 0 0 0 8px #fde68a; }
  50%  { box-shadow:0 0 0 3px #fff, 0 0 0 5px var(--c), 0 0 0 14px #fde68a; }
  100% { box-shadow:0 0 0 3px #fff, 0 0 0 5px var(--c), 0 0 0 8px #fde68a; }
}
.step::before { content:""; position:absolute; top:7px; right:50%; width:100%; height:2px;
                background:#e2e8f0; }
.step:first-child::before { display:none; }
.step.done { color:var(--soft); }
.step.done i, .step.done::before, .step.cur::before
  { background:var(--c); border-color:var(--c); }
.step.cur { color:var(--c); font-weight:700; font-size:11px; }
.step.cur i { width:13px; height:13px; margin-bottom:2px; background:var(--c); border-color:var(--c);
              box-shadow:0 0 0 3px #fff, 0 0 0 5px var(--c); }

/* 终结卡：走完的氛围 */
.story.ended { position:relative; background:#f8fafc; opacity:.82; }
.story.ended .badge, .story.ended .step.done i, .story.ended .step.done::before
  { filter:saturate(.35); }
.ended-flag { position:absolute; top:10px; right:12px; z-index:2;
              background:#475569; color:#fff; font-size:10px; border-radius:6px;
              padding:1px 8px; letter-spacing:1px; }

/* 迁移时间轴 */
.tl { list-style:none; margin:6px 0 0; padding:0; position:relative; }
.tl::before { content:""; position:absolute; left:8px; top:8px; bottom:8px; width:2px;
              background:#e2e8f0; border-radius:2px; }
.tl li { position:relative; padding:3px 0 3px 26px; font-size:12px; color:var(--soft);
         scroll-margin-top:14px; transition:background .15s; }
.tl li:target { background:#fff7ed; border-radius:6px; }
.tl li .tl-link { color:inherit; text-decoration:none; border-bottom:1px dashed #94a3b8; }
.tl li .tl-link:hover { border-bottom-style:solid; color:#7c3aed; }
.step:target i { box-shadow:0 0 0 3px #fff, 0 0 0 5px var(--c), 0 0 0 8px #fde68a !important; }
.tl li::after { content:""; position:absolute; left:7px; top:0; bottom:0; width:3px;
                background:var(--evc, transparent); opacity:.3; border-radius:2px; }
.tl li::before { content:""; position:absolute; left:3px; top:8px; width:8px; height:8px;
                 border-radius:50%; background:#cbd5e1; border:2px solid #fff;
                 box-shadow:0 0 0 1.5px #cbd5e1; z-index:1; }
.tl li.ev-birth { --evc:#0d9488; }
.tl li.ev-end   { --evc:#475569; }
.tl li.ev-birth::before { background:#0d9488; box-shadow:0 0 0 1.5px #0d9488; }
.tl li.ev-move::before  { background:var(--evc, #f59e0b); box-shadow:0 0 0 1.5px var(--evc, #f59e0b); }
.tl li.ev-end::before   { background:#475569; box-shadow:0 0 0 1.5px #475569; }
.tl .d { display:inline-block; min-width:44px; color:#94a3b8; font-size:11px;
         font-variant-numeric:tabular-nums; }
.progress { margin:7px 0 4px; font-size:13.5px; color:var(--soft); }
.story-log { margin:2px 0; padding-left:2px; color:var(--muted); font-size:12px; list-style:none; }
.story-log .d { display:inline-block; min-width:44px; font-variant-numeric:tabular-nums; }
.story-log.full li { margin:4px 0; }
details { margin:5px 0 0; font-size:12px; color:var(--muted); }
details summary { cursor:pointer; display:flex; align-items:center; gap:4px;
                  padding:3px 0; list-style:none; }
details summary::-webkit-details-marker { display:none; }
details summary::before { content:"▸"; font-size:11px; color:#94a3b8;
                          transition:transform .15s; }
details[open] summary::before { transform:rotate(90deg); }
.badge { display:inline-block; border-radius:999px; padding:1px 10px; font-size:11.5px; color:#fff; }
.st-bud { background:#94a3b8; } .st-brew { background:#f59e0b; }
.st-lead { background:#ef4444; } .st-ebb { background:#3b82f6; }
.st-end { background:#475569; }

/* ── 锚：情绪 ── */
.sent-head { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; margin:2px 0 4px; }
.big-number { font-size:34px; font-weight:700; line-height:1.1;
              font-variant-numeric:tabular-nums; letter-spacing:-.5px; }
.big-number .unit { font-size:13px; font-weight:400; color:var(--muted); margin-left:2px; }
.sent-sub { font-size:12.5px; color:var(--muted); }
.sent-sub .label { font-size:15px; color:#b45309; margin-right:2px; }
.sent-sub .hint { color:#94a3b8; }
.sent-bar { position:relative; padding-top:20px; margin:2px 0 0; }
.segs { display:flex; height:9px; border-radius:999px; overflow:hidden; }
.seg { height:100%; }
.marker { position:absolute; top:0; transform:translateX(-50%);
          display:flex; flex-direction:column; align-items:center; line-height:1; }
.marker b { font-size:12px; font-weight:700; color:#334155; }   /* 中性墨：位置指示，非警报 */
.marker i { display:block; width:0; height:0; margin-top:1px; font-style:normal;
            border:4.5px solid transparent; border-top-color:#334155; border-bottom-width:2.5px; }
.zone-labels { display:flex; justify-content:space-between; padding:3px 2px 0;
               font-size:10px; color:#94a3b8; font-variant-numeric:tabular-nums; }
.spark { width:100%; height:auto; display:block; margin:6px 0 2px; }
.spark .line { fill:none; stroke:#f59e0b; stroke-width:2.2; stroke-linejoin:round; }
.spark .mid { stroke:#dbe2ea; stroke-dasharray:4 4; }
.spark .dot { fill:#d97706; }
.spark .dim { fill:#b6c2d2; }
.spark .pt circle { fill:transparent; }
.spark .pt:hover circle { fill:#d97706; opacity:.4; cursor:pointer; }
.spark .tick { font-size:10px; fill:#94a3b8; }
.sent-reading { margin:12px 0 0; background:#fbfcfe; border:1px solid var(--line2);
                border-radius:11px; padding:12px 14px 10px; }
.sent-headline { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap;
                 padding-bottom:9px; margin-bottom:8px;
                 border-bottom:1px dashed color-mix(in srgb, var(--zc) 35%, var(--line2)); }
.sent-zone { flex:0 0 auto; font-size:17px; font-weight:800; color:var(--zc);
             letter-spacing:.5px; }
.sent-hint { font-size:13px; color:var(--soft); line-height:1.6; }
.sent-details { list-style:none; margin:0; padding:0; font-size:12.5px; color:var(--soft); }
.sent-details li { display:flex; align-items:baseline; gap:10px; padding:5.5px 0; }
.sent-details li + li { border-top:1px solid color-mix(in srgb, #fbfcfe 60%, var(--line2)); }
.sent-details .tag { flex:0 0 auto; font-size:11px; color:var(--zc);
                     background:color-mix(in srgb, var(--zc) 10%, #ffffff);
                     border-radius:5px; padding:1px 7px; font-weight:600; }
.sent-details b { color:var(--ink); font-variant-numeric:tabular-nums; }
.pattern-list { list-style:none; margin:2px 0; padding:0; font-size:13px; }
.pat-all { display:inline-block; margin-top:7px; font-size:12.5px; color:#2563eb;
           text-decoration:none; }
.pat-all:hover { text-decoration:underline; }
.zdist { margin:4px 0 8px; }
.zrow { display:flex; align-items:center; gap:8px; margin:4px 0; font-size:12.5px; }
.zrow .zname { flex:0 0 34px; font-weight:600; }
.zrow .zbar { flex:1; height:10px; background:var(--line2); border-radius:999px; overflow:hidden; }
.zrow .zbar i { display:block; height:100%; border-radius:999px; }
.zrow .zn { flex:0 0 44px; color:var(--muted); text-align:right; font-variant-numeric:tabular-nums; }
.anchor-sub { font-size:13px; font-weight:600; color:var(--soft); margin:14px 0 6px;
              padding-bottom:5px; border-bottom:1px dashed var(--line); }
.anchor-sub .anchor-note { font-weight:400; font-size:11.5px; color:var(--muted); margin-left:10px; }
.pattern-list li { padding:5px 0; border-bottom:1px solid var(--line2); }
.pattern-list li:last-child { border-bottom:none; }
.pattern-list .d { display:inline-block; min-width:44px; color:var(--muted); font-size:11.5px;
                   font-variant-numeric:tabular-nums; }
.pattern-list .mark { display:inline-block; background:#f1f5f9; border-radius:6px;
                      padding:0 6px; margin:0 4px; font-size:11px; color:var(--muted); }
.pattern-list .res { font-weight:600; }

/* ── 记分牌 ── */
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(118px,1fr)); gap:9px; margin:6px 0 10px; }
.tile { background:#f8fafc; border:1px solid var(--line2); border-radius:11px;
        text-align:center; padding:11px 4px 9px; }
.tile .num { display:block; font-size:23px; font-weight:700; line-height:1.2;
             font-variant-numeric:tabular-nums; }
.tile .cap { font-size:11.5px; color:var(--muted); }
.judgments { width:100%; border-collapse:collapse; font-size:12.5px; }
.judgments th, .judgments td { border:1px solid var(--line2); padding:5px 8px; text-align:left; }
.judgments th { background:#f8fafc; font-weight:600; color:var(--soft); }
.judgments a { color:#2563eb; text-decoration:none; }

/* 验证结果着色纪律：✓绿、部分琥珀、✗灰（历史账不抢红） */
.r-ok { color:#15803d; font-weight:600; }
.r-part { color:#b45309; }
.r-miss { color:#94a3b8; filter:grayscale(1); }

/* ── 归档 ── */
.filters { margin:4px 0 10px; display:flex; flex-wrap:wrap; gap:6px; }
.fbtn { border:1px solid var(--line); background:#f8fafb; border-radius:999px;
        padding:3px 12px; font-size:12px; cursor:pointer; color:var(--soft); }
.fbtn:hover { border-color:#94a3b8; }
.fbtn.on { background:#1e293b; color:#fff; border-color:#1e293b; }
.archive { list-style:none; margin:0; padding:0; }
.archive.compact li { border-bottom:none; padding:2px 6px; }
.archive a { display:flex; gap:10px; align-items:baseline; padding:9px 6px;
             border-bottom:1px solid var(--line2); text-decoration:none; color:var(--ink); }
.archive.compact a { padding:3px 6px; border-bottom:none; }
.archive a:hover { background:#f8fafc; }
.badge-col { flex:0 0 auto; font-size:12.5px; }
.arch-date { flex:0 0 auto; color:var(--muted); font-size:12px; font-variant-numeric:tabular-nums; }
.arch-digest { color:var(--muted); font-size:12px; overflow:hidden;
               text-overflow:ellipsis; white-space:nowrap; }

/* ── 报告/详情页 ── */
.page-top { display:flex; align-items:center; gap:14px; padding:13px 20px; background:var(--card);
            border-bottom:1px solid var(--line); }
.page-top a { text-decoration:none; color:#2563eb; font-size:13.5px; }
.page-title { font-size:14.5px; font-weight:600; }
.report { max-width:760px; margin:14px auto; padding:18px 22px; background:var(--card);
          border:1px solid var(--line); border-radius:14px; }
.report h2 { font-size:17px; border-bottom:1px solid var(--line2); padding-bottom:8px; }
.report h3 { font-size:15.5px; margin:16px 0 6px; }
.report table { border-collapse:collapse; width:100%; margin:10px 0; font-size:13px; }
.report th, .report td { border:1px solid var(--line); padding:6px 9px; text-align:left; }
.report th { background:#f8fafc; }
.report a { color:#2563eb; }
.page-foot { text-align:center; color:var(--muted); font-size:11.5px; padding:16px 20px 24px; }
"""
