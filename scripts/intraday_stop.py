#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中监控 V2 — 跌停保护 + 熔断 + 恐慌过滤。"""
import urllib.request, json, os, sys
from datetime import datetime, date, timedelta

STATE_FILE = os.path.expanduser("~/Desktop/intraday_positions.json")
TRADES_FILE = os.path.expanduser("~/Desktop/trades.json")
CIRCUIT_BREAK = 3
CIRCUIT_DAYS = 5
LIMIT_DOWN_PCT = -0.08
PANIC_THRESHOLD = -2.0  # SH跌超2%视为恐慌

def load_trades():
    if os.path.exists(TRADES_FILE):
        with open(TRADES_FILE) as f: return json.load(f)
    return []

def save_trade(trade):
    trades = load_trades()
    for t in trades:
        if (t["date"]==trade["date"] and t["strategy"]==trade["strategy"] 
            and t["code"]==trade["code"] and t["action"]==trade["action"]):
            return
    trades.append(trade)
    with open(TRADES_FILE,"w") as f: json.dump(trades,f,ensure_ascii=False,indent=2)

INITIAL = {
    "烽火V5": [("603725","天安新材",14.15,13.16,5300,"2026-07-03"),
               ("002050","三花智控",49.82,46.33,1500,"2026-07-03"),
               ("601991","大唐发电",7.81,7.26,9600,"2026-07-03"),
               ("301023","奕帆传动",37.30,34.69,2000,"2026-07-03")],
    "5-Gate": [("603725","天安新材",14.15,13.44,5300,"2026-07-03"),
               ("603638","艾迪精密",28.39,26.97,2600,"2026-07-03"),
               ("601991","大唐发电",7.81,7.42,9600,"2026-07-03"),
               ("301138","华研精机",34.04,32.34,2200,"2026-07-03")]}

def get_price_info(code):
    """Tencent API: returns (open, now, last_close). Batch-fetches all in one call."""
    return get_price_batch(code)[1:4]

def get_price_batch(code):
    """Returns (now, open, last_close, name) from Tencent. All stocks cached per run."""
    global _price_cache
    if '_price_cache' not in globals() or not hasattr(get_price_batch, 'cache'):
        get_price_batch.cache = {}
    if code in get_price_batch.cache:
        return get_price_batch.cache[code]
    try:
        pfx = "sh" if code.startswith("6") else "sz"
        url = f"http://qt.gtimg.cn/q={pfx}{code}"
        req = urllib.request.Request(url, headers={"Referer":"https://finance.qq.com"})
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read().decode("gbk")
        f = raw.split('"')[1].split("~")
        result = (float(f[3]), float(f[5]) if f[5] else float(f[3]), float(f[4]), f[1])
        get_price_batch.cache[code] = result
        return result
    except:
        empty = (None, None, None, "")
        get_price_batch.cache[code] = empty
        return empty

def is_panic():
    """SH down > 2% via Tencent API."""
    try:
        req = urllib.request.Request("http://qt.gtimg.cn/q=sh000001", 
                                      headers={"Referer":"https://finance.qq.com"})
        with urllib.request.urlopen(req, timeout=5) as r:
            f = r.read().decode("gbk").split('"')[1].split("~")
        pct = float(f[32])  # change %
        return pct < -2.0
    except: return False

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state = json.load(f)
            state.setdefault("circuit_breaker", {"烽火V5":0,"5-Gate":0})
            state.setdefault("circuit_until", {"烽火V5":"","5-Gate":""})
            return state
    return {"烽火V5":[],"5-Gate":[],"cash":{"烽火V5":0,"5-Gate":0},
            "circuit_breaker":{"烽火V5":0,"5-Gate":0},
            "circuit_until":{"烽火V5":"","5-Gate":""}}

def save_state(s):
    s["_updated"] = datetime.now().isoformat()
    with open(STATE_FILE, "w") as f: json.dump(s, f, ensure_ascii=False, indent=2)

def run_screener(exclude_codes, capital):
    script = r"C:\Users\JoeJoyce\AppData\Local\hermes\plugins\stock-analysis-plugin\skills\stock-screener\scripts\baostock_screener.py"
    try:
        cmd = f'"C:/Users/JoeJoyce/AppData/Local/Microsoft/WindowsApps/python3" "{script}" --top 15 --style trend'
        result = os.popen(cmd).read()
        data = json.loads(result)
        picks = []
        for c in data.get("candidates", []):
            code = c["symbol"]
            if code in exclude_codes or "ST" in c["name"]: continue
            price = c["price"]; amount = capital * 0.125
            shares = int(amount/(price*1.0025)/100)*100
            if shares < 100: continue
            cost = shares*price*1.0025
            picks.append((code, c["name"], price, shares, cost, c["scores"]["composite"]))
        return picks[:2]
    except: return []

def in_circuit(state, strategy):
    until = state["circuit_until"].get(strategy, "")
    return until and until >= date.today().isoformat()

def main():
    now = datetime.now()
    if now.weekday() >= 5: return
    h, m = now.hour, now.minute
    t = h*60+m
    if t < 570 or (690 < t < 780) or t >= 900: return
    
    state = load_state()
    today = date.today().isoformat()
    alerts = []
    sold_cash = {"烽火V5":0,"5-Gate":0}
    new_stops = {"烽火V5":0,"5-Gate":0}
    panic = is_panic()
    
    if panic: alerts.append("PANIC! SH跌超2% freeze")

    for strategy in ["烽火V5","5-Gate"]:
        if not state[strategy]:
            for code, name, cost, stop, shares, buy_date in INITIAL[strategy]:
                state[strategy].append({"code":code,"name":name,"cost":cost,"stop":stop,
                                         "shares":shares,"buy_date":buy_date})
        state.setdefault("cash",{}).setdefault(strategy,0)

    for strategy in ["烽火V5","5-Gate"]:
        if in_circuit(state, strategy):
            alerts.append(f"XX  {strategy} CIRCUIT until{state['circuit_until'][strategy]}")
            continue
        
        remaining = []
        for pos in state[strategy]:
            code = pos["code"]
            open_p, now_p, last_close = get_price_info(code)
            if now_p is None: remaining.append(pos); continue
            
            pnl = (now_p - pos["cost"])/pos["cost"]*100
            can_sell = pos["buy_date"] < today
            limit_down = open_p and last_close and (open_p/last_close-1) < LIMIT_DOWN_PCT
            
            if limit_down and can_sell:
                cash = pos["shares"]*open_p*0.9975
                alerts.append(f"XX  {strategy} {code} {pos.get('name','')} LIMIT-DOWN SELL! {open_p:.2f} ¥{cash:,.0f}")
                sold_cash[strategy] += cash; new_stops[strategy] += 1
                save_trade({"date":today,"strategy":strategy,"action":"卖出","code":code,
                    "name":pos.get("name",""),"price":round(open_p,2),"shares":pos["shares"],
                    "amount":round(cash,0),"reason":"跌停保护"})
            elif now_p <= pos["stop"] and can_sell:
                cash = pos["shares"]*now_p*0.9975
                alerts.append(f"🚨 {strategy} {code} {pos.get('name','')} ¥{now_p:.2f} 止损 ¥{cash:,.0f}")
                sold_cash[strategy] += cash; new_stops[strategy] += 1
                save_trade({"date":today,"strategy":strategy,"action":"卖出","code":code,
                    "name":pos.get("name",""),"price":round(now_p,2),"shares":pos["shares"],
                    "amount":round(cash,0),"reason":"止损","pnl_pct":round(pnl,1)})
            elif now_p <= pos["stop"] and not can_sell:
                alerts.append(f"🔒 {strategy} {code} T+1 LOCKED"); remaining.append(pos)
            elif pnl < -3:
                alerts.append(f"!!  {strategy} {code} ¥{now_p:.2f} {pnl:+.1f}%"); remaining.append(pos)
            else:
                remaining.append(pos)
        
        state[strategy] = remaining
        state["cash"][strategy] += sold_cash[strategy]

    # 熔断检查
    for strategy in ["烽火V5","5-Gate"]:
        if in_circuit(state, strategy): continue
        cb = state["circuit_breaker"].get(strategy,0) + new_stops[strategy]
        if cb >= CIRCUIT_BREAK:
            extra_cash = 0
            for pos in state[strategy]:
                _, now_p, _ = get_price_info(pos["code"])
                if now_p: extra_cash += pos["shares"]*now_p*0.9975
            state[strategy] = []; state["cash"][strategy] += extra_cash
            state["circuit_breaker"][strategy] = 0
            state["circuit_until"][strategy] = (today+timedelta(days=CIRCUIT_DAYS)).isoformat()
            alerts.append(f"XX  {strategy} CIRCUIT BREAK! {CIRCUIT_BREAK}losses, liquidate clear until{state['circuit_until'][strategy]}")
        else:
            state["circuit_breaker"][strategy] = cb

    # REBUY（非熔断+非恐慌）
    for strategy in ["烽火V5","5-Gate"]:
        if in_circuit(state, strategy) or panic: continue
        if sold_cash[strategy] <= 0: continue
        total_cash = state["cash"][strategy]
        if len(state[strategy]) >= 4: continue
        exclude = [p["code"] for p in state[strategy]]
        picks = run_screener(exclude, total_cash)
        if picks:
            alerts.append(f">>  {strategy} REBUY ¥{total_cash:,.0f}:")
            for code, name, price, shares, cost, score in picks:
                if len(state[strategy]) >= 4: break
                state[strategy].append({"code":code,"name":name,"cost":price*1.0025,
                    "stop":price*0.95,"shares":shares,"buy_date":today})
                state["cash"][strategy] -= cost
                save_trade({"date":today,"strategy":strategy,"action":"买入","code":code,
                    "name":name,"price":round(price,2),"shares":shares,"amount":round(cost,0),"reason":"REBUY"})
                alerts.append(f"  OK  {code} {name} {shares}s ¥{price:.2f}")

    save_state(state)
    if alerts:
        for a in alerts: print(a)

if __name__ == "__main__":
    main()
