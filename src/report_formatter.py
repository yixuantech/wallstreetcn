"""报告格式化模块 — Markdown转微信公众号HTML

公众号正文要求HTML格式，且必须内联CSS（不支持外部样式表）。
不依赖第三方Markdown库，用正则实现转换。
"""

import re


# 公众号内联样式
STYLES = {
    "h2": "font-size:18px;font-weight:bold;color:#1a1a1a;margin:20px 0 10px;padding-bottom:6px;border-bottom:1px solid #eee;",
    "h3": "font-size:16px;font-weight:bold;color:#333;margin:16px 0 8px;",
    "p": "font-size:15px;line-height:1.8;color:#333;margin:10px 0;",
    "table": "border-collapse:collapse;width:100%;margin:12px 0;font-size:14px;",
    "th": "border:1px solid #ddd;padding:8px 10px;background:#f5f5f5;font-weight:bold;text-align:left;",
    "td": "border:1px solid #ddd;padding:8px 10px;text-align:left;",
    "ul": "font-size:15px;line-height:1.8;color:#333;margin:8px 0;padding-left:20px;",
    "li": "margin:4px 0;",
    "blockquote": "border-left:4px solid #ddd;padding:8px 12px;margin:12px 0;background:#f9f9f9;color:#666;font-size:14px;",
    "strong": "color:#1a1a1a;",
    "img": "max-width:100%;height:auto;",
    "hr": "border:none;border-top:1px solid #eee;margin:16px 0;",
}

# 公众号尾部模板
FOOTER_TEMPLATE = """
<hr style="border:none;border-top:1px solid #eee;margin:20px 0;" />
<p style="font-size:13px;color:#999;line-height:1.6;margin:10px 0;">
⚠️ <strong>免责声明</strong>：本报告由AI基于公开信息生成，仅供学习参考，不构成任何投资建议。市场有风险，投资需谨慎。
</p>
<p style="font-size:13px;color:#999;line-height:1.6;margin:10px 0;">
📊 数据来源：华尔街见闻、东方财富 | 🤖 AI分析：DeepSeek
</p>
<p style="font-size:14px;color:#576b95;line-height:1.6;margin:10px 0;text-align:center;">
<strong>👇 关注我，每个交易日盘前收到全球市场分析</strong>
</p>
"""


def markdown_to_wechat_html(md_content: str, add_footer: bool = True) -> str:
    """将Markdown转换为微信公众号HTML

    Args:
        md_content: Markdown格式报告
        add_footer: 是否添加公众号尾部

    Returns:
        公众号兼容的HTML
    """
    html = _md_to_html(md_content)
    html = _inject_styles(html)

    if add_footer:
        html += FOOTER_TEMPLATE

    return html


def _md_to_html(md: str) -> str:
    """Markdown转HTML（覆盖公众号报告用到的语法）"""
    lines = md.split("\n")
    html_parts = []
    in_table = False
    in_ul = False

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 空行
        if not stripped:
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            if in_table:
                html_parts.append("</tbody></table>")
                in_table = False
            i += 1
            continue

        # 水平线
        if stripped in ("---", "***", "___"):
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            if in_table:
                html_parts.append("</tbody></table>")
                in_table = False
            html_parts.append("<hr />")
            i += 1
            continue

        # 标题
        if stripped.startswith("### "):
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            if in_table:
                html_parts.append("</tbody></table>")
                in_table = False
            text = _inline_md(stripped[4:])
            html_parts.append(f"<h3>{text}</h3>")
            i += 1
            continue

        if stripped.startswith("## "):
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            if in_table:
                html_parts.append("</tbody></table>")
                in_table = False
            text = _inline_md(stripped[3:])
            html_parts.append(f"<h2>{text}</h2>")
            i += 1
            continue

        if stripped.startswith("# "):
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            if in_table:
                html_parts.append("</tbody></table>")
                in_table = False
            text = _inline_md(stripped[2:])
            html_parts.append(f"<h1>{text}</h1>")
            i += 1
            continue

        # 表格行
        if stripped.startswith("|") and "|" in stripped[1:]:
            if not in_table:
                # 开始新表格
                html_parts.append("<table>")
                # 第一行是表头
                headers = [c.strip() for c in stripped.split("|")[1:-1]]
                html_parts.append("<thead><tr>")
                for h in headers:
                    html_parts.append(f"<th>{_inline_md(h)}</th>")
                html_parts.append("</tr></thead><tbody>")
                in_table = True
                i += 1
                # 跳过分隔行 |---|---|
                if i < len(lines) and re.match(r'\|[\s\-:]+\|', lines[i].strip()):
                    i += 1
                continue
            else:
                # 表体行
                cells = [c.strip() for c in stripped.split("|")[1:-1]]
                html_parts.append("<tr>")
                for c in cells:
                    html_parts.append(f"<td>{_inline_md(c)}</td>")
                html_parts.append("</tr>")
                i += 1
                continue

        # 分隔行 |---|---|（表格已处理时跳过）
        if in_table and re.match(r'\|[\s\-:]+\|', stripped):
            i += 1
            continue

        # 无序列表
        if stripped.startswith("- ") or stripped.startswith("* "):
            if in_table:
                html_parts.append("</tbody></table>")
                in_table = False
            if not in_ul:
                html_parts.append("<ul>")
                in_ul = True
            text = _inline_md(stripped[2:])
            html_parts.append(f"<li>{text}</li>")
            i += 1
            continue

        # 引用
        if stripped.startswith("> "):
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            if in_table:
                html_parts.append("</tbody></table>")
                in_table = False
            text = _inline_md(stripped[2:])
            html_parts.append(f"<blockquote><p>{text}</p></blockquote>")
            i += 1
            continue

        # 普通段落
        if in_ul:
            html_parts.append("</ul>")
            in_ul = False
        if in_table:
            html_parts.append("</tbody></table>")
            in_table = False
        text = _inline_md(stripped)
        html_parts.append(f"<p>{text}</p>")
        i += 1

    # 关闭未关闭的标签
    if in_ul:
        html_parts.append("</ul>")
    if in_table:
        html_parts.append("</tbody></table>")

    return "\n".join(html_parts)


def _inline_md(text: str) -> str:
    """处理行内Markdown语法：加粗、斜体、行内代码"""
    # 加粗 **text**
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # 斜体 *text*
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    # 行内代码 `code`
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
    return text


def _inject_styles(html: str) -> str:
    """为HTML标签注入内联样式"""
    html = re.sub(r'<h2>', f'<h2 style="{STYLES["h2"]}">', html)
    html = re.sub(r'<h3>', f'<h3 style="{STYLES["h3"]}">', html)
    html = re.sub(r'<p(?!\s+style=)>', f'<p style="{STYLES["p"]}">', html)
    html = re.sub(r'<table>', f'<table style="{STYLES["table"]}">', html)
    html = re.sub(r'<th>', f'<th style="{STYLES["th"]}">', html)
    html = re.sub(r'<td>', f'<td style="{STYLES["td"]}">', html)
    html = re.sub(r'<ul>', f'<ul style="{STYLES["ul"]}">', html)
    html = re.sub(r'<li>', f'<li style="{STYLES["li"]}">', html)
    html = re.sub(r'<blockquote>', f'<blockquote style="{STYLES["blockquote"]}">', html)
    html = re.sub(r'<strong(?!\s+style=)', f'<strong style="{STYLES["strong"]}"', html)
    html = re.sub(r'<img(?!\s+style=)', f'<img style="{STYLES["img"]}"', html)
    html = re.sub(r'<hr\s*/?>', f'<hr style="{STYLES["hr"]}" />', html)
    return html


def generate_article_digest(html_content: str, max_chars: int = 120) -> str:
    """生成文章摘要（用于公众号卡片展示）

    Args:
        html_content: HTML正文
        max_chars: 摘要最大字符数

    Returns:
        纯文本摘要
    """
    text = re.sub(r'<[^>]+>', '', html_content)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "..."
    return text
