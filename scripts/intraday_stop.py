#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中监控 V3 — Tencent数据 + 五级恐慌 + 熔断 + 跌停保护 + T+1合规."""
import urllib.request, json, os, sys
from datetime import datetime, date, timedelta

STATE_FILE = os.path.expanduser("~/Desktop/intraday_positions.json")
TRADES_FILE = os.path.expanduser("~/Desktop/trades.json")
CIRCUIT_BREAK = 3
CIRCUIT_DAYS = 5

def load_trades():
    if os.path.exists(TRADES_FILE):
        with open(TRADES_FILE) as f: return json.load(f)
    return []

def save_trade(trade):
    trades = load_trades()
    for t in trades:
        if t["date"]==trade["date"] and t["strategy"]==trade["strategy"] and t["code"]==trade["code"] and t["action"]==trade["action"]:
            return
    trades.append(trade)
    with open(TRADES_FILE,"w") as f: json.dump(trades,f,ensure_ascii=False,indent=2)

INITIAL = {
    "烽火V5": [("603725","天安新材",14.15,13.16,5300,"2026-07-03"),("002050","三花智控",49.82,46.33,1500,"2026-07-03"),("601991","大唐发电",7.81,7.26,9600,"2026-07-03"),("301023","奕帆传动",37.30,34.69,2000,"2026-07-03")],
    "5-Gate": [("603725","天安新材",14.15,13.44,5300,"2026-07-03"),("603638","艾迪精密",28.39,26.97,2600,"2026-07-03"),("601991","大唐发电",7.81,7.42,9600,"2026-07-03"),("301138","华研精机",34.04,32.34,2200,"2026-07-03")],
}

def fetch_tencent(codes):
    """Batch fetch: return {code: {open,now,last_close,name,chg_pct}}."""
    result = {}
    for c in codes:
        try:
            pfx = "sh" if c.startswith("6") else "sz"
            url = f"http://qt.gtimg.cn/q={pfx}{c}"
            req = urllib.request.Request(url, headers={"Referer":"https://finance.qq.com"})
            with urllib.request.urlopen(req, timeout=5) as r:
                f = r.read().decode("gbk").split('"')[1].split("~")
            result[c] = {"now":float(f[3]),"open":float(f[5])if f[5]!=""else float(f[3]),"last_close":float(f[4]),"name":f[1],"chg_pct":float(f[32])if f[32]!=""else 0}
        except: pass
    return result

def get_panic_level():
    try:
        req = urllib.request.Request("http://qt.gtimg.cn/q=sh000001", headers={"Referer":"https://finance.qq.com"})
        with urllib.request.urlopen(req, timeout=5) as r:
            pct = float(r.read().decode("gbk").split('"')[1].split("~")[32])
        if pct > 0.5: return 0
        elif pct > -1.0: return 1
        elif pct > -2.0: return 2
        elif pct > -3.0: return 3
        else: return 4
    except: return 1

PANIC_LABELS = {0:"greed",1:"weak",2:"caution",3:"panic",4:"extreme"}
PANIC_FREEZE = 3
PANIC_HALVE = 4

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            s = json.load(f)
            s.setdefault("circuit_breaker",{"烽火V5":0,"5-Gate":0})
            s.setdefault("circuit_until",{"烽火V5":"","5-Gate":""})
            return s
    return {"烽火V5":[],"5-Gate":[],"cash":{"烽火V5":0,"5-Gate":0},"circuit_breaker":{"烽火V5":0,"5-Gate":0},"circuit_until":{"烽火V5":"","5-Gate":""}}

def save_state(s):
    s["_updated"] = datetime.now().isoformat()
    with open(STATE_FILE,"w") as f: json.dump(s,f,ensure_ascii=False,indent=2)

def in_circuit(s, strategy):
    u = s["circuit_until"].get(strategy,"")
    return u and u >= date.today().isoformat()

def run_screener(exclude_codes, capital):
    script = r"C:\Users\JoeJoyce\AppData\Local\hermes\plugins\stock-analysis-plugin\skills\stock-screener\scripts\baostock_screener.py"
    try:
        cmd = f'"C:/Users/JoeJoyce/AppData/Local/Microsoft/WindowsApps/python3" "{script}" --top 15 --style trend'
        data = json.loads(os.popen(cmd).read())
        picks = []
        for c in data.get("candidates",[]):
            code = c["symbol"]
            if code in exclude_codes or "ST" in c["name"]: continue
            price = c["price"]; amount = capital*0.125
            shares = int(amount/(price*1.0025)/100)*100
            if shares < 100: continue
            picks.append((code,c["name"],price,shares,shares*price*1.0025,c["scores"]["composite"]))
        return picks[:2]
    except: return []

def main():
    now = datetime.now()
    if now.weekday() >= 5: return
    t = now.hour*60+now.minute
    if t < 570 or (690 < t < 780) or t >= 900: return
    # Only write state if this is a real trading hour invocation

    state = load_state()
    today = date.today().isoformat()
    lines = []
    sold = {"烽火V5":0,"5-Gate":0}
    stops = {"烽火V5":0,"5-Gate":0}

    panic = get_panic_level()
    if panic >= PANIC_FREEZE:
        lines.append(f"PANIC L{panic}({PANIC_LABELS[panic]})")

    for st in ["烽火V5","5-Gate"]:
        if not state[st]:
            for code,name,cost,stop,shares,bd in INITIAL[st]:
                state[st].append({"code":code,"name":name,"cost":cost,"stop":stop,"shares":shares,"buy_date":bd})
        state.setdefault("cash",{}).setdefault(st,0)

    # Collect all codes for batch fetch
    all_codes = set()
    for st in ["烽火V5","5-Gate"]:
        for p in state[st]: all_codes.add(p["code"])
    quotes = fetch_tencent(all_codes)

    # Sell loop
    for st in ["烽火V5","5-Gate"]:
        if in_circuit(state,st):
            lines.append(f"CIRCUIT {st} until {state['circuit_until'][st]}")
            continue
        remaining = []
        for pos in state[st]:
            q = quotes.get(pos["code"])
            if not q: remaining.append(pos); continue
            
            now_p = q["now"]; open_p = q["open"]; last_c = q["last_close"]
            cost = pos["cost"]; sh = pos["shares"]
            pnl = (now_p-cost)/cost*100
            can_sell = pos["buy_date"] < today
            limit_down = last_c and (open_p/last_c-1) < -0.08

            if limit_down and can_sell:
                cash = sh*open_p*0.9975
                lines.append(f"LIMITDOWN {st} {pos['code']} {pos.get('name','')} {open_p:.2f} rcv{cash:,.0f}")
                sold[st] += cash; stops[st] += 1
                save_trade({"date":today,"strategy":st,"action":"SELL","code":pos["code"],"name":pos.get("name",""),"price":round(open_p,2),"shares":sh,"amount":round(cash,0),"reason":"limit-down"})
            elif now_p <= pos["stop"] and can_sell:
                cash = sh*now_p*0.9975
                lines.append(f"STOP {st} {pos['code']} {pos.get('name','')} {now_p:.2f} rcv{cash:,.0f}")
                sold[st] += cash; stops[st] += 1
                save_trade({"date":today,"strategy":st,"action":"SELL","code":pos["code"],"name":pos.get("name",""),"price":round(now_p,2),"shares":sh,"amount":round(cash,0),"reason":"stop","pnl_pct":round(pnl,1)})
            elif now_p <= pos["stop"] and not can_sell:
                lines.append(f"LOCK {st} {pos['code']} T+1"); remaining.append(pos)
            elif pnl < -3:
                lines.append(f"WARN {st} {pos['code']} {pos.get('name','')} {now_p:.2f} {pnl:+.1f}%"); remaining.append(pos)
            else:
                remaining.append(pos)
        state[st] = remaining
        state["cash"][st] += sold[st]

    # Circuit breaker
    for st in ["烽火V5","5-Gate"]:
        if in_circuit(state,st): continue
        cb = state["circuit_breaker"].get(st,0) + stops[st]
        if cb >= CIRCUIT_BREAK:
            extra = 0
            for pos in state[st]:
                q = quotes.get(pos["code"],{})
                if q.get("now"): extra += pos["shares"]*q["now"]*0.9975
            state[st] = []
            state["cash"][st] += extra
            state["circuit_breaker"][st] = 0
            state["circuit_until"][st] = (date.today()+timedelta(days=CIRCUIT_DAYS)).isoformat()
            lines.append(f"CIRCUIT {st} {CIRCUIT_BREAK} losses until {state['circuit_until'][st]}")
        else:
            state["circuit_breaker"][st] = cb

    # Extreme panic: halve positions
    if panic >= PANIC_HALVE:
        for st in ["烽火V5","5-Gate"]:
            if in_circuit(state,st): continue
            for pos in list(state[st]):
                if len(state[st]) <= 2: break
                q = quotes.get(pos["code"],{})
                if q.get("now"):
                    state["cash"][st] += pos["shares"]*q["now"]*0.9975
                    lines.append(f"HALVE {st} {pos['code']} extreme panic")
                    state[st].remove(pos)

    # Rebuy
    if panic < PANIC_FREEZE:
        for st in ["烽火V5","5-Gate"]:
            if in_circuit(state,st) or sold[st] <= 0: continue
            total_cash = state["cash"][st]
            if len(state[st]) >= 4: continue
            exclude = [p["code"] for p in state[st]]
            picks = run_screener(exclude, total_cash)
            if picks:
                lines.append(f"REBUY {st} cash{total_cash:,.0f}")
                for code,name,price,shares,cost,score in picks:
                    if len(state[st]) >= 4: break
                    state[st].append({"code":code,"name":name,"cost":price*1.0025,"stop":price*0.95,"shares":shares,"buy_date":today})
                    state["cash"][st] -= cost
                    save_trade({"date":today,"strategy":st,"action":"BUY","code":code,"name":name,"price":round(price,2),"shares":shares,"amount":round(cost,0),"reason":"rebuy"})
                    lines.append(f"  BUY {code} {name} {shares}s {price:.2f}")

    save_state(state)
    if lines:
        for l in lines: print(l)

if __name__ == "__main__":
    main()
