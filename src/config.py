"""配置管理模块 — 从环境变量或 .env 文件读取配置"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件（优先级：环境变量 > .env > 默认值）
load_dotenv(Path(__file__).parent.parent / ".env")


class Config:
    """全局配置"""

    # DeepSeek API
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"

    # AI 模型
    AI_MODEL: str = os.getenv("AI_MODEL", "deepseek-chat")

    # PushPlus
    PUSHPLUS_TOKEN: str = os.getenv("PUSHPLUS_TOKEN", "")

    # 微信公众号
    WECHAT_APPID: str = os.getenv("WECHAT_APPID", "")
    WECHAT_APPSECRET: str = os.getenv("WECHAT_APPSECRET", "")
    WECHAT_COVER_IMAGE_PATH: str = os.getenv("WECHAT_COVER_IMAGE_PATH", "")

    # 项目路径
    PROJECT_ROOT: Path = Path(__file__).parent.parent
    DATA_DIR: Path = PROJECT_ROOT / "data"
    LOGS_DIR: Path = PROJECT_ROOT / "logs"

    # 华尔街见闻 API
    WSCN_API_BASE: str = "https://api-one-wscn.awtmt.com"
    WSCN_MARKET_API_BASE: str = "https://api-ddc-wscn.awtmt.com"
    WSCN_HEADERS: dict = {
        "Origin": "https://wallstreetcn.com",
        "Referer": "https://wallstreetcn.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    # 请求配置
    REQUEST_TIMEOUT: int = 10
    REQUEST_INTERVAL: float = 3.0
    MAX_RETRIES: int = 3

    # 市场行情产品代码
    MARKET_CODES: list[str] = [
        "DXY.OTC",       # 美元指数
        "EURUSD.OTC",    # 欧元/美元
        "USDJPY.OTC",    # 美元/日元
        "USDCNH.OTC",    # 离岸人民币
        "XAUUSD.OTC",    # 现货黄金
        "USCL.OTC",      # WTI原油
    ]

    # AI 模型参数
    MODEL_CONFIGS: dict = {
        "deepseek-chat": {
            "model": "deepseek-chat",
            "max_tokens": 3000,
            "temperature": 0.3,
        },
        "deepseek-reasoner": {
            "model": "deepseek-reasoner",
            "max_tokens": 4000,
            "temperature": 0.1,
        },
    }

    # 去重文件
    PROCESSED_IDS_FILE: Path = DATA_DIR / "processed_ids.txt"

    @classmethod
    def validate(cls) -> list[str]:
        """检查必填配置，返回缺失项列表"""
        missing = []
        if not cls.DEEPSEEK_API_KEY:
            missing.append("DEEPSEEK_API_KEY")
        if not cls.PUSHPLUS_TOKEN:
            missing.append("PUSHPLUS_TOKEN")
        return missing

    @classmethod
    def is_wechat_configured(cls) -> bool:
        """检查公众号配置是否完整"""
        return bool(cls.WECHAT_APPID and cls.WECHAT_APPSECRET)
