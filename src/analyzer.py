"""AI分析模块 — 调用DeepSeek API生成结构化报告"""

import re
from openai import OpenAI

from src.config import Config

SYSTEM_PROMPT = """你是一位资深财经分析师，擅长从纷繁的新闻中提炼关键信息、
发现内在逻辑、形成市场判断。你的分析风格：精准、有逻辑、有洞见。"""

ANALYSIS_PROMPT = """请基于以下华尔街见闻早餐FM的内容和实时市场数据，生成今日盘前分析报告。

## 早餐FM原文
{article_text}

## 实时市场数据
{market_data}

## 报告要求

请严格按照以下格式输出：

### 🌍 全球要闻
提取5-8条最重要的全球财经事件，每条用一句话概括，标注影响方向（利好/利空/中性）

### 📊 隔夜市场表现
**必须用表格展示以下所有类别，每个类别不可省略：**

| 类别 | 品种 | 最新价 | 涨跌幅 |
|------|------|--------|--------|
| A股 | 上证/深证/创业板 | | |
| 港股 | 恒生指数/恒生科技 | | |
| 日韩 | 日经225/韩国KOSPI | | |
| 美股 | 道琼斯/纳斯达克/标普500 | | |
| 欧洲 | 富时100/DAX/CAC40 | | |
| A50期货 | 富时中国A50 | | |
| 外汇 | 美元指数/离岸人民币 | | |
| 商品 | 黄金/原油 | | |
| 债市 | 美10年期收益率/中10年期收益率 | | |

以上数据全部来自上方"实时市场数据"部分，必须逐项填入，不得遗漏。
对于显著波动（涨跌幅>1%），在表格后简要说明原因。

### 🧠 内在逻辑分析
分析上述新闻事件之间的关联和传导逻辑，找出市场主线
重点分析：美联储政策预期、地缘政治、中国经济政策三大主线

### 🎯 今日A股预判
给出今日A股的方向性判断（看多/看偏多/看平/看偏空/看空）
列出需要重点关注的板块（2-3个），说明逻辑
提示主要风险点

注意：
- 所有分析必须基于提供的事实，不要编造数据
- 隔夜市场表现表格必须完整，所有类别都要列出
- 对不确定的判断要明确标注
- 保持客观，避免绝对化表述"""


def analyze(article_text: str, market_data: str = "") -> str:
    """调用DeepSeek API生成分析报告

    Args:
        article_text: 早餐FM纯文本正文
        market_data: 格式化的市场行情文本

    Returns:
        Markdown格式的分析报告
    """
    model_config = Config.MODEL_CONFIGS.get(
        Config.AI_MODEL, Config.MODEL_CONFIGS["deepseek-chat"]
    )

    client = OpenAI(
        api_key=Config.DEEPSEEK_API_KEY,
        base_url=Config.DEEPSEEK_BASE_URL,
    )

    prompt = ANALYSIS_PROMPT.format(
        article_text=article_text,
        market_data=market_data or "暂无市场行情数据",
    )

    print(f"[Analyzer] 调用 {model_config['model']} 生成报告...")

    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=model_config["model"],
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=model_config["max_tokens"],
                temperature=model_config["temperature"],
            )
            report = response.choices[0].message.content.strip()
            print(f"[Analyzer] 报告生成完成，长度: {len(report)} 字")
            return report

        except Exception as e:
            if attempt == 0:
                print(f"[Analyzer] API调用失败: {e}, 重试中...")
            else:
                print(f"[Analyzer] API调用再次失败: {e}")
                return _fallback_report(article_text, str(e))


def extract_verdict(report: str) -> str:
    """从报告中提取预判关键词（用于推送标题和卡片颜色）

    Returns:
        预判关键词：看多/看偏多/看平/看偏空/看空
    """
    # 匹配"今日A股预判"板块中的方向性判断
    patterns = [
        r"看多", r"看偏多", r"看平", r"看偏空", r"看空",
    ]
    # 从报告末尾（A股预判板块）开始匹配，优先匹配更具体的词
    # 先匹配双字词（看偏多 > 看多）
    priority_patterns = [r"看偏多", r"看偏空", r"看多", r"看空", r"看平"]

    prediction_section = report
    # 尝试截取预判板块
    pred_match = re.search(r"今日A股预判", report)
    if pred_match:
        prediction_section = report[pred_match.start():]

    for pattern in priority_patterns:
        if re.search(pattern, prediction_section):
            return pattern

    return "看平"


def _fallback_report(article_text: str, error_msg: str) -> str:
    """AI分析失败时的降级报告"""
    # 截取文章前500字作为摘要
    summary = article_text[:500] + "..." if len(article_text) > 500 else article_text
    return f"""### ⚠️ AI分析暂不可用

AI服务调用失败，以下是早餐FM原文摘要：

---

{summary}

---

*错误信息: {error_msg}*

*请稍后重试或手动阅读完整文章。*"""
