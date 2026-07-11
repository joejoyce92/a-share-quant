# A-Share Quantitative Strategies

双策略 A 股量化交易系统，300k 实盘验证中。

## 策略

| 策略 | 版本 | 18月回测 | 说明 |
|------|------|---------|------|
| 5-Gate | V3 | +53% | 五层硬性筛选 + 三级市场自适应 |
| 烽火 V5 | V5 | +5% | 五因子评分 + 大盘过滤 |

## 脚本

| 文件 | 功能 |
|------|------|
| `intraday_stop.py` | 盘中监控（止损+补仓+熔断+跌停保护） |
| `daily_monitor.py` | 收盘日报（持仓+交易+收益曲线） |
| `pnl_curve.py` | 收益曲线数据记录 |
| `backtest.py` | 5-Gate V3 回测引擎 |
| `baostock_screener.py` | 烽火 V5 实时筛选 |

## 数据源

- Sina Finance（K线）
- Tushare（PE/PB）
- Baostock（历史指数）

## 风控

- 跌停保护：低开 > 8% 立即卖出
- 熔断：连续 3 次止损清仓 5 天
- T+1 合规：当日买入锁定至次日
