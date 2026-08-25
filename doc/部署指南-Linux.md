# Linux 部署指南

> 适用于正式环境（Linux 服务器）。开发环境为 Windows，代码已按跨平台编写：
> 显式CST时区（不依赖服务器时区）、无Windows路径依赖、文件IO显式UTF-8。

## 一、环境要求

- Python 3.10+
- 依赖: `pip install -r requirements.txt`（requests / openai）
- 配置: 项目根目录 `.env`（DEEPSEEK_API_KEY、PUSHPLUS_TOKEN）
- 数据: `data/watchlist.json`（个人自选股，不入库，手动部署）

**服务器时区必须设为北京时间**（代码内部已显式CST，但cron时刻表按系统时钟走）:

```bash
sudo timedatectl set-timezone Asia/Shanghai
timedatectl   # 确认 Time zone: Asia/Shanghai (CST, +0800)
```

## 二、安装步骤

```bash
cd /opt
git clone <仓库地址> wallstreetcn
cd wallstreetcn
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 配置密钥与自选股
cp .env.example .env && vim .env      # 填入两个KEY
cp data/watchlist.example.json data/watchlist.json && vim data/watchlist.json

# 验证（真实采集+标签+温度计，不调AI不推送）
python tests/smoke_watchlist.py
```

## 三、crontab 调度（核心）

`crontab -e` 添加（路径按实际调整，`PYTHONUNBUFFERED` 保证日志实时）:

```cron
# ============ 华尔街见闻自选股系统 ============
# 工作日·晨报（现已上线；M0 完成前临时用 main.py，完成后切换）
30 7 * * 1-5  cd /opt/wallstreetcn && ./venv/bin/python runner.py morning  >> logs/morning.log 2>&1

# 工作日·数据解读美国档（待建，上线后取消注释）
# 45 7 * * 1-5  cd /opt/wallstreetcn && ./venv/bin/python runner.py macro_us >> logs/macro_us.log 2>&1

# 工作日·午间快讯（待建）
# 35 11 * * 1-5  cd /opt/wallstreetcn && ./venv/bin/python runner.py noon    >> logs/noon.log 2>&1

# 工作日·晚报（待建）
# 30 17 * * 1-5  cd /opt/wallstreetcn && ./venv/bin/python runner.py evening >> logs/evening.log 2>&1

# 工作日·数据解读中国档（待建）
# 30 18 * * 1-5  cd /opt/wallstreetcn && ./venv/bin/python runner.py macro_cn >> logs/macro_cn.log 2>&1

# 工作日·夜巡紧急警报（待建）
# 30 20 * * 1-5  cd /opt/wallstreetcn && ./venv/bin/python runner.py night   >> logs/night.log 2>&1

# 周六·周报（待建）
# 0 9 * * 6  cd /opt/wallstreetcn && ./venv/bin/python runner.py weekly >> logs/weekly.log 2>&1
```

> M0 完成前（runner.py 尚未创建）临时用 `python main.py`；
> M0 完成后 main.py 删除，全系统唯一入口为 runner.py。

## 四、日志

- cron 任务自身输出重定向到 `logs/<栏目>.log`（如上）
- 应用内 print 带时间戳前缀，`PYTHONUNBUFFERED=1` 可加在 cron 行保证实时写入
- 日志轮转: logrotate 示例 `/etc/logrotate.d/wallstreetcn`:

```
/opt/wallstreetcn/logs/*.log {
    weekly
    rotate 8
    compress
    missingok
}
```

## 五、Linux 与开发环境(Win)的差异说明

| 项 | Windows 开发机 | Linux 生产 | 影响 |
|----|--------------|-----------|------|
| 控制台编码 | GBK（需 PYTHONIOENCODING=utf-8） | 默认UTF-8 | 无需处理，脚本里的✓✗正常 |
| 系统代理 | 配置了127.0.0.1:7890且常关闭（需NO_PROXY绕过） | 无系统代理 | 代理问题消失；东财push2不稳定仍靠腾讯备胎兜底 |
| 时区 | 本地即北京时间 | 需设Asia/Shanghai | 见第一节 |
| 文件路径 | Path对象（已跨平台） | 同 | 无 |

## 六、故障排查

```bash
# 手动跑一次看输出
cd /opt/wallstreetcn && ./venv/bin/python runner.py morning

# 看今天日志
tail -50 logs/morning.log

# cron是否在跑
grep CRON /var/log/syslog | grep wallstreetcn

# 常见问题:
# 1. 推送失败 → 检查 .env 的 PUSHPLUS_TOKEN
# 2. AI调用失败 → 检查 DEEPSEEK_API_KEY 与服务器出网
# 3. 行情⚪ → push2直连波动，腾讯备胎自动接管，持续⚪再排查出网
# 4. 重复推送不会发生 → data/processed_ids.txt 去重（同文章ID当日不重推）
```

## 七、安全清单

- `.env` 不入库（已gitignore）；服务器上 `chmod 600 .env`
- `data/watchlist.json` 不入库；`chmod 600`（个人持仓隐私）
- `logs/` 不入库（已gitignore）
