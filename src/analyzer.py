"""AI分析模块 — 调用DeepSeek API生成结构化报告"""

import json
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

WATCHLIST_PROMPT = """
## 自选股数据（{n}只，标签由规则引擎判定，你没有降级权）
{watchlist_block}

## 「🔍 自选股观察」板块输出要求（追加在报告最后）

⚠️ 格式铁律（微信渲染会把仅隔单个换行的文字挤成一行）：
- 任何相邻两行文字之间必须空一行（即按一次回车两次）
- 每只股票标题用 ### 三级标题（与其他板块一致），禁止 ####
- 严格按下方模板逐行输出，一行一项，禁止把多项合并成连续段落

各标签模板：

🟢股票（无事则沉默，以下即全部内容，禁止展开分析）：
### {{名称}}({{代码}}) ｜ 现价{{X}}({{涨跌幅}}%) ｜ 🟢无重大变化

📌 一句话：{{资金与预期一句话}}

📅 下一节点：{{日历节点，无则写"无已登记节点"}}

⚪股票（禁止臆测）：
### {{名称}}({{代码}}) ｜ 数据异常 ｜ ⚪数据缺失

⚠️ 数据异常：{{哪些接口失败}}，建议自查

📌 仍可用数据：{{温度计/研报一句话}}

🔴🟡股票（标题行后，对每条重要资讯/公告按此模板逐条解读）：
### {{名称}}({{代码}}) ｜ 现价{{X}}({{涨跌幅}}%) ｜ {{标签}}

- [{{资讯标题}}]({{原文链接}})

【{{分类}} · 利好/利空/中性 · 高/中/低强度 · 脉冲型/持续型】

💬 说人话：{{专业术语翻译成通俗因果，如"主动调整"=主动少发货清库存}}

🔗 逻辑链：事件→业务→财务→估值，短期{{影响}}，长期{{影响}}

⚓ 对照预期：{{超/低/符合预期；无锚写"该股无机构预期锚，无法定量对照"}}

该股全部条目解读完后，最后一项：

🧭 小结：{{对持有逻辑的一句话影响+下一步盯什么}}

内容铁律：
1. 所有数字必须来自上方数据，禁止编造或自行推算
2. 禁止给出买卖指令（只说"盯什么"）
3. 不确定必须标注"不确定"
4. 升级权：发现规则未覆盖的重要事项可将🟢升级为🟡并说明理由；禁止降级任何标签
5. 温度计数值与矩阵落点只解释含义，禁止修改；无锚股票禁止定量"超预期/低于预期"结论"""

META_PROMPT = """

## 机器可读预判块（必须输出，用户不可见）
在报告全部正文输出完毕后，最后另起一块输出以下JSON（ fenced code block，语言标记json），
用于系统次日验证预判，不会推送给用户：
{"direction": "看多|看偏多|看平|看偏空|看空", "sectors": ["重点关注板块1", "板块2"],
 "news_marks": [{"title": "要闻关键词(10字内)", "mark": "利好|利空|中性"}]}
要求：direction 与「今日A股预判」板块一致；sectors 为该板块列出的关注板块；
news_marks 列出「全球要闻」中方向标注明确的主要条目（不超过8条）。只输出这一个JSON块。"""

EVENING_PROMPT = """你是AI盘报的晚报编辑。以下数据块全部为系统规则引擎核实的收盘事实
（收盘全景/情绪刻度/判断记分牌/自选股），你的职责是解释和串联，不是重新判定。

{data_blocks}

## 晚报输出要求（严格控制篇幅，全文不超过1200字）

⚠️ 格式铁律（微信渲染会把仅隔单个换行的文字挤成一行）：
- 任何相邻两行文字之间必须空一行
- 板块标题用 ### 三级标题，禁止 ####
- 表格仅用于指数收盘，其余一律逐行要点

### 📊 今日复盘
- 指数表现用表格（指数/收盘/涨跌幅），随后2-4条要点：宽度与指数的关系、
  板块轮动特征、量能含义。只解释数据块已有事实，不引入外部信息。

### 🌡️ 情绪刻度
- 引用刻度数值与刻度条原样展示，2-3句解释今日读数意味着什么
  （如宽度强但权重弱=赚指数不赚钱的反向情形）。数值禁止修改。

### 🧾 记分牌复盘
- 逐条复述记分牌结果（✓/部分/❌原样），对每条给一句归因
  （对在哪里/错在哪里）。归因只基于今日数据块，不臆测。
- 若记分牌缺失，如实说明"今日晨报快照缺失，不记分"。

### 🔍 自选股收盘
- 每只股票标题用 ### 三级标题（与其他板块一致，禁止####）：
  `### {名称}({代码}) ｜ 收盘{X}({涨跌幅}%) ｜ {标签}`
- 标题下新增事件逐条（无新增则一句话收工）
- 禁止买卖指令，只说"明日盯什么"

### 🧭 明日观察
- 3条以内：从今日结构推出的观察点（量能/板块/事件日历），写清"盯什么、
  什么信号算兑现"。

内容铁律：
1. 所有数字来自数据块，禁止编造
2. 情绪分值、记分✓/❌、观察点状态是规则判定，你只能解释不能修改
3. 复盘语言克制，避免"主力""洗盘"等无法证实的黑话；不确定就说不确定
4. 不给买卖指令，不做次日涨跌预测（"明日观察"是观察点不是预测）"""


def split_meta(report: str):
    """剥离报告末尾的机器可读预判块。

    Returns:
        (干净报告, meta字典)。无块或JSON损坏时 meta=None、报告原样返回（降级不阻断）。
    """
    matches = list(re.finditer(r"```json\s*(\{.*?\})\s*```", report, re.S))
    if not matches:
        return report, None
    last = matches[-1]                    # 只取最后一个（正文里偶有示例块时不误伤）
    try:
        meta = json.loads(last.group(1))
    except json.JSONDecodeError:
        print("[Analyzer] 预判块JSON解析失败，跳过记分（报告不受影响）")
        return report, None
    clean = (report[:last.start()] + report[last.end():]).rstrip()
    return clean, meta


def _call_llm(prompt: str) -> str:
    """共用LLM调用：2次尝试，失败返回空串（调用方自行降级）"""
    model_config = Config.MODEL_CONFIGS.get(
        Config.AI_MODEL, Config.MODEL_CONFIGS["deepseek-chat"]
    )
    client = OpenAI(
        api_key=Config.DEEPSEEK_API_KEY,
        base_url=Config.DEEPSEEK_BASE_URL,
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
                return ""
    return ""


def analyze(article_text: str, market_data: str = "", watchlist_data: str = "") -> str:
    """调用DeepSeek API生成晨报分析报告

    Args:
        article_text: 早餐FM纯文本正文
        market_data: 格式化的市场行情文本
        watchlist_data: 自选股数据块（空则报告不含自选股板块）

    Returns:
        Markdown格式的分析报告
    """
    prompt = ANALYSIS_PROMPT.format(
        article_text=article_text,
        market_data=market_data or "暂无市场行情数据",
    )
    if watchlist_data:
        # 数一下股票只数（数据块以 #### 分隔）
        n = watchlist_data.count("#### ")
        prompt += WATCHLIST_PROMPT.format(n=n, watchlist_block=watchlist_data)

    # 机器可读预判块：晚报记分牌的数据源（用户不可见，代码剥离）
    prompt += META_PROMPT

    report = _call_llm(prompt)
    return report or _fallback_report(article_text, "LLM连续调用失败")


NOON_PROMPT = """你是AI盘报的午间值班编辑。以下为今晨推送后新出现的自选股事件（规则引擎已分诊，
仅🟡以上股票才会出现在这里），你的任务是给每条事件配齐"三件套"，让读者下午知道盯什么。

## 午间指数速览（规则采集）
{indices_line}

## 新增事件（已分诊，标签不可修改）
{data_blocks}

## 快讯输出要求（全文不超过600字）

⚠️ 格式铁律：相邻两行文字之间必须空一行；股票标题用 ### 三级标题，禁止####。

每只股票一块，每条新增事件一组三件套：

### {名称}({{代码}}) ｜ 现价{{X}}({{涨跌幅}}%) ｜ {{标签}}

- [{{事件标题}}]({{链接}})

💬 异动：一句话说清发生了什么（通俗因果，不堆术语）

🧭 下午盯什么：具体信号（价格/成交量/板块/后续公告），写清"看到什么算兑现"

内容铁律：
1. 禁止裸价格播报——只报与事件相关的变动
2. 禁止买卖指令；定级（标签/理由）是规则判定，只能引用不能改
3. 数字来自数据块，不确定就说不确定"""

ALERT_PROMPT = """你是AI盘报的夜巡警报编辑。晚间扫描在自选股公告/资讯中命中了🔴级关键词
（立案/停牌/预亏/重组/退市/减持），需要立刻向持有人解释严重程度和明早要做什么。
宁可严肃，不可淡化；但禁止恐慌化渲染。

## 命中事件（规则判定🔴，不可降级）
{data_blocks}

## 警报输出要求（全文不超过500字）

⚠️ 格式铁律：相邻两行文字之间必须空一行；标题用 ### 三级标题。

### 🚨 {名称}({{代码}}) — 命中「{{关键词}}」

- [{{标题}}]({{链接}})

💬 说人话：这个公告/事件通常意味着什么（基于标题能确定的部分），

不确定的部分明确说"需明早看公告原文核实"

⚠️ 严重度：高/中/低 + 一句理由（仅基于标题与关键词，禁止臆测细节）

🧭 明早盯什么：需要核实的具体事项 + 该事项影响持有逻辑的哪一环

内容铁律：禁止买卖指令；禁止编造公告细节；这是提醒不是结论。
（铁律是对你的要求，不要出现在输出正文中）"""


def analyze_noon(indices_line: str, data_blocks: str) -> str:
    """午间快讯：新增事件 → 三件套快讯（几百字级）"""
    prompt = (NOON_PROMPT.replace("{indices_line}", indices_line)
              .replace("{data_blocks}", data_blocks))
    report = _call_llm(prompt)
    return report or (
        "### ⚡ 午间快讯（AI解读暂不可用）\n\n"
        "规则引擎检测到新增事件（见下），AI解读失败，链接供直接阅读：\n\n"
        + data_blocks
    )


def analyze_alert(data_blocks: str) -> str:
    """夜巡紧急警报：🔴命中 → 短警报"""
    report = _call_llm(ALERT_PROMPT.replace("{data_blocks}", data_blocks))
    return report or (
        "### 🚨 夜巡警报（AI解读暂不可用）\n\n"
        "规则引擎命中🔴关键词，请立即自查以下公告原文：\n\n"
        + data_blocks
    )


MACRO_PROMPT = """你是AI盘报的宏观经济编辑，本期是「数据解读·{edition}」。
以下数据块全部为系统核实的官方数据（实际值/前值/5年分位/连续月数），你的职责是
把这些数字翻译成持有人能懂、能用的话。

{data_blocks}

## 输出要求（全文不超过800字）

⚠️ 格式铁律：相邻两行文字之间必须空一行；板块标题用 ### 三级标题，禁止####。

### 📅 今日落地
- 一句话列出本期落地的指标

### 📊 逐项解读
- 每个指标一小段：数值与方向 → 处于什么位置（分位/连续性意味着什么）→
  一句"所以呢"（对经济状态的含义）。数字原样引用，禁止修改或虚构预期值。

### 🧩 和你的自选股有什么关系
- 从指标到板块到个股的传导逻辑（最多3条），具体点名数据块给出的自选股名称。
- 传导不确定的环节明说"不确定"；禁止给买卖指令。

### 📌 下一个数据日
- 引用数据块中的下期发布信息；没有就写"待下期数据落地时解读"。

内容铁律：
1. 所有数字来自数据块，禁止编造，禁止虚构"市场预期"（数据源无预期值）
2. 分位/连续月数是规则计算结果，只能解释不能改
3. 语言说人话，术语第一次出现时顺带一句通俗解释
（铁律是对你的要求，不要出现在输出正文中）"""


def analyze_macro(edition: str, data_blocks: str) -> str:
    """数据解读（中国档/美国档共用模板）"""
    report = _call_llm(MACRO_PROMPT.replace("{edition}", edition)
                       .replace("{data_blocks}", data_blocks))
    return report or (
        f"### 📊 数据解读·{edition}（AI解读暂不可用）\n\n"
        "规则引擎数据如下，可先行阅读，稍后重跑对应命令补解读：\n\n"
        + data_blocks
    )


def analyze_evening(data_blocks: str) -> str:
    """晚报：规则引擎产出的收盘事实块 → 复盘报告（AI只解释不判定）"""
    # 模板含示例花括号（{名称}等），用replace注入而非format
    report = _call_llm(EVENING_PROMPT.replace("{data_blocks}", data_blocks))
    return report or (
        "### ⚠️ 晚报AI分析暂不可用\n\n"
        "规则引擎数据已归档（情绪刻度/记分牌/观察点不受影响），"
        "明晨晨报照常。可稍后重跑 `python runner.py evening`。"
    )


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
