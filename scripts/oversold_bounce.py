#!/usr/bin/env python3
"""超跌反弹策略 V1 — 极度恐慌日次日抄底，快速止盈止损。"""
import sys, json, numpy as np
from datetime import datetime
from urllib import request

CAPITAL = 100_000       # 独立资金，不混用
MAX_POSITIONS = 3
POSITION_SIZE = 0.10    # 每只 10%
TAKE_PROFIT = 0.05      # +5% 止盈
HARD_STOP = -0.03       # -3% 止损
MAX_HOLD_DAYS = 5        # 最多持 5 天
START_DATE = "2025-01-02"
END_DATE = "2025-12-31"

SINA_KLINE = "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"

def fetch_kline(symbol):
    try:
        url = f"{SINA_KLINE}?symbol={symbol}&scale=240&ma=no&datalen=500"
        req = request.Request(url, headers={"Referer": "http://finance.sina.com.cn"})
        with request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return sorted([{"date": r["day"], "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]), "volume": float(r["volume"])}
            for r in data], key=lambda x: x["date"]) if data else None
    except: return None

def is_oversold(bars, idx):
    """Check if stock is deeply oversold for bounce potential."""
    closes = np.array([b["close"] for b in bars])
    volumes = np.array([b["volume"] for b in bars])
    n = len(bars)
    if idx < 20: return False, 0
    
    # RSI(14) < 30
    gains = losses = 0
    for i in range(idx-13, idx+1):
        chg = closes[i] - closes[i-1] if i > 0 else 0
        if chg > 0: gains += chg
        else: losses += -chg
    rsi = 100 - 100/(1 + gains/losses) if losses > 0 else 100
    
    # Near Bollinger lower band
    ma20 = np.mean(closes[idx-19:idx+1])
    std20 = np.std(closes[idx-19:idx+1])
    lower_band = ma20 - 2*std20
    near_boll = closes[idx] <= lower_band * 1.05
    
    # Volume capitulation: today's volume > 1.5x avg
    avg_vol = np.mean(volumes[idx-19:idx+1])
    vol_spike = volumes[idx] > avg_vol * 1.5
    
    # 3-day drop > 8%
    drop_3d = (closes[idx] - closes[idx-3]) / closes[idx-3] * 100 if idx >= 3 else 0
    
    score = 0
    if rsi < 30: score += 3
    elif rsi < 35: score += 1
    if near_boll: score += 3
    if vol_spike: score += 2
    if drop_3d < -8: score += 2
    
    return score >= 3, score

def main():
    # Get stock codes
    codes = []
    for prefix in ["sh60","sh68","sz00","sz30","sz002"]:
        url = f"http://money.finance.sina.com.cn/d/api/jsonp.php/IO.XSRV2.Callback19/US_VariationService.getNFLList?page=1&num=2000&sort=code&asc=1&node={prefix}"
        try:
            req = request.Request(url, headers={"Referer": "http://finance.sina.com.cn"})
            with request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("gbk")
            import re
            m = re.search(r'\((\[.*\])\)', raw, re.DOTALL)
            if m:
                for stock in json.loads(m.group(1)):
                    codes.append(stock["code"])
        except: pass
    
    print(f"Stock universe: {len(codes)}", file=sys.stderr)
    sample = sorted(codes)[:500]
    
    # Get SH index (baostock for 2025)
    print("Fetching SH index via baostock...", file=sys.stderr)
    try:
        import baostock as bs, contextlib, io
        with contextlib.redirect_stdout(io.StringIO()): bs.login()
        rs = bs.query_history_k_data_plus('sh.000001','date,close',start_date=START_DATE,end_date=END_DATE,frequency='d',adjustflag='2')
        sh = []
        while rs.error_code=='0' and rs.next():
            r = rs.get_row_data()
            sh.append({"date":r[0],"close":float(r[1])})
        bs.logout()
    except:
        print("Baostock failed, trying Sina fallback", file=sys.stderr)
        sh = fetch_kline("sh000001")
    if not sh: return
    dates = [d["date"] for d in sh if START_DATE <= d["date"] <= END_DATE]
    
    # Find panic days (SH < -3%)
    panic_days = []
    for d in sh:
        if not (START_DATE <= d["date"] <= END_DATE): continue
        idx = next((i for i, sd in enumerate(sh) if sd["date"] == d["date"]), None)
        if idx is None or idx == 0: continue
        chg = (d["close"] - sh[idx-1]["close"]) / sh[idx-1]["close"] * 100
        if chg < -2.0:
            panic_days.append(d["date"])
    
    print(f"Panic days: {len(panic_days)}", file=sys.stderr)
    if not panic_days:
        print("No panic days found. Try different period.")
        return
    
    # Reduced sample for baostock speed
    sample = sorted(codes)[:200]
    
    # Fetch K-lines via baostock
    klines = {}
    for i, c in enumerate(sample):
        if i % 10 == 0: print(f"Fetching {i}/{len(sample)}...", file=sys.stderr)
        try:
            rs = bs.query_history_k_data_plus(f'{c[:2]}.{c[2:]}','date,open,high,low,close,volume',
                start_date=START_DATE,end_date=END_DATE,frequency='d',adjustflag='2')
            data = []
            while rs.error_code=='0' and rs.next():
                r = rs.get_row_data()
                if r[1] == '': continue
                data.append({"date":r[0],"open":float(r[1]),"high":float(r[2]),
                    "low":float(r[3]),"close":float(r[4]),"volume":float(r[5])})
            if data:
                klines[c] = sorted(data, key=lambda x:x["date"])
        except: pass
    bs.logout()
    print(f"K-lines: {len(klines)} stocks", file=sys.stderr)
    
    # Simulate
    cash = CAPITAL
    positions = {}
    trades = []
    daily_values = {}
    
    for pday in panic_days:
        # Buy next day at open
        next_day_idx = next((i for i, d in enumerate(dates) if d > pday), None)
        if next_day_idx is None: continue
        buy_date = dates[next_day_idx]
        
        # Check existing positions: sell if TP/SL/hold expired
        for sym in list(positions.keys()):
            pos = positions[sym]
            bars = klines.get(sym)
            if not bars: continue
            di = next((i for i, b in enumerate(bars) if b["date"] == buy_date), None)
            if di is None: continue
            
            hold_days = len([d for d in dates if pos["buy_date"] < d <= buy_date])
            price = bars[di]["open"]
            pnl = (price - pos["cost"])/pos["cost"]
            
            sell = False; reason = ""
            if pnl >= TAKE_PROFIT: sell = True; reason = "止盈"
            elif pnl <= HARD_STOP: sell = True; reason = "止损"
            elif hold_days >= MAX_HOLD_DAYS: sell = True; reason = "到期"
            
            if sell:
                cash += pos["shares"]*price*0.9975
                trades.append({"date":buy_date,"code":sym,"action":"SELL","price":price,
                    "shares":pos["shares"],"pnl":round(pnl*100,1),"reason":reason})
                del positions[sym]
        
        # Find oversold stocks
        candidates = []
        for c, bars in klines.items():
            di = next((i for i, b in enumerate(bars) if b["date"] == buy_date), None)
            if di is None: continue
            oversold, score = is_oversold(bars, di)
            if oversold and bars[di]["open"] > 0:
                candidates.append((c, bars[di]["open"], score))
        
        candidates.sort(key=lambda x: x[2], reverse=True)
        
        # Buy top oversold stocks
        slots = MAX_POSITIONS - len(positions)
        for code, price, score in candidates[:slots]:
            if price <= 0: continue
            amount = cash * POSITION_SIZE
            shares = int(amount / (price*1.0025) / 100) * 100
            if shares < 100: continue
            cost = shares*price*1.0025
            if cost > cash * 0.15: continue  # Max 15% per buy
            
            cash -= cost
            positions[code] = {"cost": price, "shares": shares, "buy_date": buy_date, "score": score}
            trades.append({"date":buy_date,"code":code,"action":"BUY","price":price,
                "shares":shares,"pnl":0,"reason":"超跌反弹"})
        
        # Record daily value
        tv = sum(klines.get(sym, [{}])[-1].get("close", positions[sym]["cost"])*positions[sym]["shares"] 
                for sym in positions) if positions else 0
        daily_values[buy_date] = cash + tv
    
    # Results
    sells = [t for t in trades if t["action"]=="SELL"]
    buys = [t for t in trades if t["action"]=="BUY"]
    wins = [t for t in sells if t["pnl"] > 0]
    
    print(f"\n{'='*60}")
    print(f"超跌反弹策略 V1 回测: {START_DATE} ~ {END_DATE}")
    print(f"恐慌日: {len(panic_days)} | 总交易: {len(trades)} | 买入: {len(buys)} | 卖出: {len(sells)}")
    if sells:
        wr = len(wins)/len(sells)*100
        avg_win = sum(t["pnl"] for t in wins)/len(wins) if wins else 0
        avg_loss = sum(t["pnl"] for t in sells if t["pnl"]<0)/len([t for t in sells if t["pnl"]<0]) if [t for t in sells if t["pnl"]<0] else 0
        pf = sum(t["pnl"] for t in wins)/sum(abs(t["pnl"]) for t in sells if t["pnl"]<0) if wins and [t for t in sells if t["pnl"]<0] else 0
        final = daily_values[sorted(daily_values.keys())[-1]] if daily_values else CAPITAL
        print(f"胜率: {wr:.0f}% | 盈亏比: {pf:.2f} | 最终: ¥{final:,.0f} ({(final-CAPITAL)/CAPITAL*100:+.1f}%)")
        print(f"平均盈利: +{avg_win:.1f}% | 平均亏损: {avg_loss:.1f}%")
    else:
        print("无卖出交易")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
