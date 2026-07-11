#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日持仓日报 — T+1合规 + 仓位管理 + 完整交易记录。"""
import urllib.request, json, re, os, io, sys
from datetime import datetime, date

# Force UTF-8 for cron (multiple fallback approaches)
sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None
os.environ['PYTHONIOENCODING'] = 'utf-8'

STATE_FILE = os.path.expanduser("~/Desktop/intraday_positions.json")
TRADES_FILE = os.path.expanduser("~/Desktop/trades.json")
INITIAL = 300_000

# 策略配置
STRATEGY_CONFIG = {
    "烽火V5": {"max_positions": 4, "bull_pct": 0.25, "range_pct": 0.25, "hard_stop": -0.07, "init_positions": [
        ("603725","天安新材",14.15,13.16,5300),("002050","三花智控",49.82,46.33,1500),
        ("601991","大唐发电",7.81,7.26,9600),("301023","奕帆传动",37.30,34.69,2000),
    ]},
    "5-Gate": {"max_positions": 4, "bull_pct": 0.25, "range_pct": 0.125, "hard_stop": -0.05, "init_positions": [
        ("603725","天安新材",14.15,13.44,5300),("603638","艾迪精密",28.39,26.97,2600),
        ("601991","大唐发电",7.81,7.42,9600),("301138","华研精机",34.04,32.34,2200),
    ]},
}

def get_price(code):
    try:
        pfx = "sh" if code.startswith("6") else "sz"
        req = urllib.request.Request(f"http://hq.sinajs.cn/list={pfx}{code}", headers={"Referer":"https://finance.sina.com.cn"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return float(r.read().decode("gbk").split('"')[1].split(",")[3])
    except: return None

def load_trades():
    if os.path.exists(TRADES_FILE):
        with open(TRADES_FILE) as f: return json.load(f)
    trades = []
    for s, cfg in STRATEGY_CONFIG.items():
        for code,name,cost,stop,shares in cfg["init_positions"]:
            trades.append({"date":"2026-07-03","strategy":s,"action":"买入","code":code,"name":name,
                "price":cost,"shares":shares,"amount":round(cost*shares*1.0025,0),"reason":"建仓"})
    with open(TRADES_FILE,"w") as f: json.dump(trades,f,ensure_ascii=False,indent=2)
    return trades

def main():
    today = date.today().isoformat()
    now = datetime.now().strftime("%H:%M")
    trades = load_trades()

    # Capture output for file save
    old_stdout = sys.stdout
    sys.stdout = buffer = io.StringIO()

    # SH index
    req = urllib.request.Request("http://hq.sinajs.cn/list=s_sh000001", headers={"Referer":"https://finance.sina.com.cn"})
    with urllib.request.urlopen(req, timeout=5) as r:
        shp, shc, shpct = [float(x) for x in r.read().decode("gbk").split('"')[1].split(",")[1:4]]

    req = urllib.request.Request("https://quotes.sina.cn/cn/api/jsonp_v2.php/data/CN_MarketDataService.getKLineData?symbol=sh000001&scale=240&ma=no&datalen=30", headers={"Referer":"https://finance.sina.com.cn"})
    with urllib.request.urlopen(req, timeout=5) as r:
        m = re.search(r'\((.*)\)', r.read().decode("utf-8"), re.DOTALL)
    ma20 = sum(float(d["close"]) for d in json.loads(m.group(1))[-20:]) / 20 if m else 0
    is_bull = shp > ma20
    regime_label = "Bull" if is_bull else "Range"

    # Load current positions from state file or init
    positions = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state = json.load(f)
            for s in STRATEGY_CONFIG:
                positions[s] = [(p["code"],p.get("name",""),p["cost"],p["stop"],p["shares"],p.get("buy_date","")) for p in state.get(s,[])]
    else:
        positions = {s: [(c,n,co,st,sh,"2026-07-03") for c,n,co,st,sh in cfg["init_positions"]] for s,cfg in STRATEGY_CONFIG.items()}
        state = {"cash": {"烽火V5": 0, "5-Gate": 0}}

    today_trades = [t for t in trades if t["date"]==today]

    # ====== REPORT ======
    print(f"# 📊 持仓日报 {today} {now}")
    print(f"\n## 🏛️ 市场 | 上证 {shp:.0f}({shc:+.0f}/{shpct:+.1f}%) | MA20 {ma20:.0f} | {regime_label}")
    print()

    strat_results = {}
    for s_name, cfg in STRATEGY_CONFIG.items():
        max_pos = cfg["max_positions"]
        pos_pct = cfg["bull_pct"] if is_bull else cfg["range_pct"]
        hard_stop = cfg["hard_stop"]
        stocks = positions[s_name]
        
        tv, rows = 0, []
        for code, name, cost, stop, shares, buy_date in stocks:
            price = get_price(code)
            if price is None: continue
            value = price * shares
            pnl = (price-cost)/cost*100
            tv += value
            pos_ratio = value / INITIAL * 100
            t1_locked = buy_date >= today
            if price <= stop: status = "🚨止损"
            elif t1_locked: status = "🔒T+1"
            elif pnl < hard_stop*100: status = "🔴浮亏"
            elif pnl < 0: status = "🟠微亏"
            elif pnl < 3: status = "🟡持平"
            else: status = "🟢盈利"
            rows.append((code,name,round(cost,2),round(stop,2),round(price,2),shares,round(value,0),round(pnl,1),status,round(pos_ratio,1),buy_date))
        
        cash = state.get("cash", {}).get(s_name, 0)
        total_value = tv + cash
        total_pnl = (total_value-INITIAL)/INITIAL*100
        strat_results[s_name] = (total_value, total_pnl, rows, max_pos, pos_pct, hard_stop)
        n_pos = len(stocks)
        
        pos_ok = "✅" if n_pos <= max_pos else "⚠️超限"
        print(f"### 🔥 {s_name} | ¥{tv:,.0f} ({total_pnl:+.1f}%) | 持仓 {n_pos}/{max_pos} {pos_ok} | 单只 {pos_pct*100:.0f}%")
        print()
        print("| 代码 | 名称 | 成本 | 止损 | 现价 | 股数 | 市值 | 占比 | 盈亏 | 状态 | 买入日 |")
        print("|:-----|:-----|----:|----:|----:|----:|----:|----:|----:|:----|:-----|")
        for r in rows:
            code,name,cost,stop,price,shares,value,pnl,status,ratio,bd = r
            pnl_s = f"+{pnl:.1f}%" if pnl>=0 else f"{pnl:.1f}%"
            print(f"| {code} | {name} | {cost} | {stop} | {price} | {shares} | {value:,.0f} | {ratio:.1f}% | {pnl_s} | {status} | {bd} |")
        # 汇总行
        print(f"| **持仓合计** | | | | | | **{tv:,.0f}** | **{tv/INITIAL*100:.1f}%** | | | |")
        print(f"| **可用资金** | | | | | | **{cash:,.0f}** | **{cash/INITIAL*100:.1f}%** | | | |")
        print(f"| **总资产** | | | | | | **{total_value:,.0f}** | **{total_value/INITIAL*100:.1f}%** | **{total_pnl:+.1f}%** | | |")
        print()

    # ===== 今日交易 =====
    print("---")
    print(f"## 📋 今日交易 ({today})")
    if today_trades:
        print("| 策略 | 操作 | 代码 | 名称 | 价格 | 股数 | 金额 | 原因 |")
        print("|:-----|:--|:-----|:-----|----:|----:|-----:|:-----|")
        for t in today_trades:
            e = "🔴卖出" if t["action"]=="卖出" else "🟢买入"
            print(f"| {t['strategy']} | {e} | {t['code']} | {t['name']} | {t['price']:.2f} | {t['shares']} | {t['amount']:,.0f} | {t['reason']} |")
    else:
        print("无交易")
    print()

    # ===== T+1 合规检查 =====
    print("## 🔒 T+1 合规")
    locked = []
    for s_name, stocks in positions.items():
        for code, name, cost, stop, shares, buy_date in stocks:
            if buy_date >= today:
                locked.append((s_name, code, name, buy_date))
    if locked:
        print("| 策略 | 代码 | 名称 | 买入日 | 明日可卖 |")
        print("|:-----|:-----|:-----|:-----|:-----|")
        for s, c, n, bd in locked:
            next_day = date.fromisoformat(bd).replace(day=date.fromisoformat(bd).day+1).isoformat() if bd.count('-')==2 else bd
            print(f"| {s} | {c} | {n} | {bd} | 🔒 次日 |")
    else:
        print("无 T+1 锁定持仓")
    print()

    # ===== 仓位合规 =====
    print("## 📐 仓位合规")
    print(f"| 策略 | 持仓 | 上限 | 单只目标 | {regime_label} |")
    print("|:-----|:--:|:--:|:-----|:-----|")
    for s_name, cfg in STRATEGY_CONFIG.items():
        n = len(positions[s_name])
        pos_pct = cfg["bull_pct"] if is_bull else cfg["range_pct"]
        ok = "✅" if n <= cfg["max_positions"] else "⚠️"
        print(f"| {s_name} | {n} | {cfg['max_positions']} {ok} | {pos_pct*100:.0f}% |")

    # ===== 交易汇总 =====
    all_dates = sorted(set(t["date"] for t in trades), reverse=True)
    print()
    print("## 📊 策略绩效")
    print("| 策略 | 胜率 | 盈亏比 | 已平仓 | 盈利/亏损 | 平均盈 | 平均亏 |")
    print("|:-----|:---:|:-----:|:-----:|:-----|----:|----:|")
    for strategy in ["烽火V5", "5-Gate"]:
        sells = [t for t in trades if t["strategy"]==strategy and t["action"]=="卖出"]
        wins = [t for t in sells if t.get("pnl_pct",0) > 0]
        losses = [t for t in sells if t.get("pnl_pct",0) < 0]
        n = len(sells)
        wr = len(wins)/n*100 if n else 0
        tg = sum(t.get("pnl_pct",0) for t in wins)
        tl = sum(abs(t.get("pnl_pct",0)) for t in losses)
        pf = tg/tl if tl>0 else 0
        aw = tg/len(wins) if wins else 0
        al = tl/len(losses) if losses else 0
        print(f"| {strategy} | {wr:.0f}% | {pf:.2f} | {n} | {len(wins)}/{len(losses)} | +{aw:.1f}% | -{al:.1f}% |")

    print()
    print("## 📜 交易汇总")
    print("| 日期 | 策略 | 操作 | 代码 | 名称 | 价格 | 股数 | 金额 | 盈亏% | 原因 |")
    print("|:-----|:-----|:--|:-----|:-----|----:|----:|-----:|-----:|:-----|")
    for d in all_dates[:10]:
        for t in [t for t in trades if t["date"]==d]:
            e = "🔴" if t["action"]=="卖出" else "🟢"
            pnl = t.get("pnl_pct", None)
            pnl_s = f"{pnl:+.1f}%" if pnl is not None else "—"
            print(f"| {d} | {t['strategy']} | {e}{t['action']} | {t['code']} | {t['name']} | {t['price']:.2f} | {t['shares']} | {t['amount']:,.0f} | {pnl_s} | {t['reason']} |")

    # ===== 对比 =====
    fh = strat_results["烽火V5"]
    f5 = strat_results["5-Gate"]
    winner = "🔥烽火V5" if fh[1] > f5[1] else "🛡️5-Gate"
    print(f"\n---\n> 🏆 {winner} | 烽火V5 {fh[1]:+.1f}% vs 5-Gate {f5[1]:+.1f}% | 初始 ¥{INITIAL:,}/策略 | ⚠️ 非投资建议")

    # ===== 对比 =====
    fh = strat_results["烽火V5"]
    f5 = strat_results["5-Gate"]
    fh_tv, fh_pnl = fh[0], fh[1]
    fg_tv, fg_pnl = f5[0], f5[1]
    winner = "🔥烽火V5" if fh_pnl > fg_pnl else "🛡️5-Gate"
    print(f"\n---\n> {winner} | FH5 {fh_pnl:+.1f}% vs 5GT {fg_pnl:+.1f}% | initial {INITIAL:,}/strategy")

    # Record daily P&L data point
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("pnl_curve", os.path.expanduser("~/AppData/Local/hermes/scripts/pnl_curve.py"))
        curve = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(curve)
        curve.record(today, fh_tv, fg_tv)
    except Exception as e:
        pass  # Silently skip if curve recording fails
    # Restore stdout and save to file
    sys.stdout = old_stdout
    report = buffer.getvalue()
    
    # Add P&L curve
    curve_path = os.path.join(os.path.dirname(__file__) if '__file__' in dir() else os.path.expanduser("~/AppData/Local/hermes/scripts"), "pnl_curve.py")
    sys.path.insert(0, os.path.dirname(curve_path))
    from pnl_curve import render_ascii_chart
    chart = render_ascii_chart()
    if chart:
        report += "\n\n## 📈 收益曲线\n\n" + chart + "\n"
    
    report_path = os.path.expanduser(f"~/Desktop/持仓日报_{today.replace('-','')}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    # No stderr output to avoid breaking Feishu delivery
    print(f"Daily Report {today}")
    print(f"SH {shp:.0f} {regime_label}")
    print(f"FH5: {fh_tv:,.0f} ({fh_pnl:+.1f}%)")
    print(f"5GT: {fg_tv:,.0f} ({fg_pnl:+.1f}%)")
    print(f"Winner: {winner}")
    print(f"Full report: Desktop")

if __name__ == "__main__":
    main()
