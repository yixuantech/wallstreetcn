#!/usr/bin/env python3
"""手动真实运行一班晚报（演示用）：进程内绕过交易日守卫，其余逻辑 100% 原样。

用途：非交易日需要向用户演示真实管线效果时使用（数据为最近交易日收盘实况，
推送真实发出，状态真实写入）。正式 cron 调度不受影响——守卫只在本进程内失效。

用法: PYTHONIOENCODING=utf-8 python tests/run_real_evening_demo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner

runner.is_trading_day = lambda: True  # 仅本进程内绕过（演示运行）
sys.exit(runner.run("evening"))
