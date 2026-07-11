#!/usr/bin/env python3
"""5-Gate V3 dual-mode: compares close-price vs next-open execution in same run."""
import sys, json, time
from datetime import datetime, timedelta
from urllib import request
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np

INITIAL_CAPITAL = 100_000
MAX_POSITIONS = 4
POSITION_SIZE = 0.25
MAX_HOLD_DAYS = 30
START_DATE = "2025-01-02"
END_DATE = "2025-12-31"
SLIPPAGE = 0.002
COMMISSION = 0.0003
HARD_STOP = -0.05
BREAKEVEN_ACTIVATE = 0.05
POSITION_STOP_HALVE = -0.10
POSITION_STOP_LIQUIDATE = -0.15
TAKE_PROFIT_1 = 0.15; TAKE_PROFIT_2 = 0.25; TAKE_PROFIT_3 = 0.40
TRAILING_STOP = 0.08; TRAILING_ACTIVATE = 0.03
V5_TOP_N = 30

SINA_KLINE = "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
SINA_MARKET = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"

TECH_KW = ["半导体","芯片","集成电路","微电子","光刻","封装","晶圆","存储","功率","IGBT","SiC","GaN",
    "人工智能","AI","智能","算法","机器学习","深度学习","大模型","AIGC","驾驶","机器人",
    "软件","信息","数据","云","计算","数字","智慧","互联","网络","安全","系统","科技","技术","智控",
    "通信","光通信","光模块","光纤","5G","6G","连接器","传输","宽带",
    "电子","消费电子","面板","显示","触控","声学","摄像头","光学","激光","精密",
    "光伏","储能","锂电","电池","充电","新能源","氢能","风电","核电","智能电网",
    "航天","航空","军工","国防","兵器","雷达","导航",
    "生物","医药","医疗","基因","创新药","器械"]

def _is_tech(name):
    for kw in TECH_KW:
        if kw in name: return True
    return False

def fetch_index_kline():
    """Use baostock for historical index data."""
    try:
        import baostock as bs, contextlib, io
        with contextlib.redirect_stdout(io.StringIO()): bs.login()
        rs = bs.query_history_k_data_plus('sh.000001','date,close',start_date=START_DATE,end_date=END_DATE,frequency='d',adjustflag='2')
        data = {}
        while rs.error_code=='0' and rs.next():
            r = rs.get_row_data()
            data[r[0]] = float(r[1])
        bs.logout()
        return data if data else None
    except Exception: return None

def compute_ma(index_data,date,all_dates,window):
    if date not in index_data: return None
    idx=all_dates.index(date) if date in all_dates else -1
    if idx<window-1: return None
    closes=[index_data[d] for d in all_dates[idx-window+1:idx+1] if d in index_data]
    return sum(closes)/len(closes) if len(closes)>=window*0.7 else None

def get_all_codes():
    codes=[]
    for node in ["sh_a","sz_a"]:
        prefix="sh" if node=="sh_a" else "sz"; page=1
        while True:
            url=f"{SINA_MARKET}?page={page}&num=100&sort=symbol&asc=1&node={node}"
            req=request.Request(url,headers={"Referer":"http://finance.sina.com.cn"})
            try:
                with request.urlopen(req,timeout=20) as resp: data=json.loads(resp.read().decode("utf-8"))
            except Exception: break
            if not data: break
            for s in data: codes.append((f"{prefix}{s['code']}",s["name"]))
            if len(data)<100: break
            page+=1; time.sleep(0.15)
    return codes

def fetch_kline(symbol):
    try:
        url=f"{SINA_KLINE}?symbol={symbol}&scale=240&ma=no&datalen=500"
        req=request.Request(url,headers={"Referer":"http://finance.sina.com.cn"})
        with request.urlopen(req,timeout=15) as resp: data=json.loads(resp.read().decode("utf-8"))
        if not data: return None
        return sorted([{"date":r["day"],"open":float(r["open"]),"high":float(r["high"]),
            "low":float(r["low"]),"close":float(r["close"]),"volume":float(r["volume"])} for r in data],
            key=lambda x:x["date"])
    except Exception: return None

def compute_gate_indicators(bars):
    n=len(bars)
    if n<60: return None
    closes=np.array([b["close"] for b in bars])
    volumes=np.array([b["volume"] for b in bars])
    ma20=np.zeros(n); ma60=np.zeros(n)
    for i in range(n):
        s20=max(0,i-19); s60=max(0,i-59)
        ma20[i]=closes[s20:i+1].mean()
        ma60[i]=closes[s60:i+1].mean() if i>=59 else closes[i]
    gain30=np.zeros(n)
    for i in range(30,n): gain30[i]=(closes[i]-closes[i-30])/closes[i-30]*100
    gain5=np.zeros(n)
    for i in range(5,n): gain5[i]=(closes[i]-closes[i-5])/closes[i-5]*100
    vr=np.ones(n)
    for i in range(20,n):
        avg=volumes[i-19:i+1].mean()
        vr[i]=volumes[i]/avg if avg>0 else 1.0
    vr3=np.ones(n)
    for i in range(3,n): vr3[i]=min(vr[i],vr[i-1],vr[i-2])
    # RSI(14)
    rsi=np.full(n, 50.0)
    for i in range(14,n):
        gains=losses=0
        for j in range(i-13,i+1):
            chg=closes[j]-closes[j-1] if j>0 else 0
            if chg>0: gains+=chg
            else: losses+=-chg
        avg_gain=gains/14; avg_loss=losses/14
        rsi[i]=100-100/(1+avg_gain/avg_loss) if avg_loss>0 else 100
    return closes,ma20,ma60,gain30,gain5,vr,vr3,rsi

def gate5_score_v2(bars,idx,inds,pe_val,is_tech,market_regime):
    closes,ma20,ma60,gain30,gain5,vr,vr3,rsi=inds
    price=closes[idx]
    # V4: RSI > 70 → overheating, reject
    if rsi[idx] > 70: return None
    # V4: volume divergence - price up but vol below 20d avg (vr<1.0)
    if gain5[idx] > 3 and vr[idx] < 1.0: return None
    if market_regime=="bull": gate1_max=50; gate4_min=0.7
    elif market_regime=="bear": gate1_max=20; gate4_min=0.9
    else: gate1_max=20; gate4_min=0.85
    if gain30[idx]>gate1_max: return None
    if price<ma20[idx]: return None
    if idx>=61 and ma60[idx]<ma60[idx-20]: return None
    if is_tech and pe_val: pe_max=200
    else: pe_max=100
    if pe_val is not None and pe_val>pe_max: return None
    if vr3[idx]<gate4_min: return None
    g5=gain5[idx]; mom=min(20,max(0,g5/10*20))
    v=vr[idx]; vol_s=min(20,max(5,v/3*20))
    trend_s=0
    if idx>=5 and closes[idx]>ma20[idx] and ma20[idx]>closes[idx-5]: trend_s+=5
    if idx>=10 and closes[idx]>ma20[idx] and ma20[idx]>closes[max(0,idx-10)]: trend_s+=5
    if ma20[idx]>ma60[idx]: trend_s+=5
    trend_s+=min(5,int(gain5[idx]/5*5)) if gain5[idx]>0 else 0
    trend_s=min(20,trend_s)
    g30=gain30[idx]
    if 2<=g30<=10: gm=15
    elif 10<g30<=15: gm=12
    elif 0<=g30<2: gm=8
    elif 15<g30<=gate1_max: gm=5
    else: gm=0
    if pe_val and pe_val>0:
        if pe_val<15: pe_s=15
        elif pe_val<30: pe_s=12
        elif pe_val<50: pe_s=8
        elif pe_val<=pe_max: pe_s=4
        else: pe_s=0
    else: pe_s=8
    tech=10 if is_tech else 0
    return mom+vol_s+trend_s+gm+pe_s+tech

def _mm(vals):
    arr=np.array(vals,dtype=float); mn,mx=arr.min(),arr.max()
    return np.full_like(arr,50.0) if mx==mn else ((arr-mn)/(mx-mn)*100).clip(0,100)

def v5_metrics(bars,idx):
    if idx<1: return None
    prev,cur=bars[idx-1]["close"],bars[idx]
    if prev<=0 or cur["close"]<=0: return None
    chg=(cur["close"]-prev)/prev*100
    h,l,c,o,v=cur["high"],cur["low"],cur["close"],cur["open"],cur["volume"]
    rng=max(h-l,0.01)
    return {"close":c,"change_pct":chg,"volume":v,"day_pos":((c-l)/rng),"gap":((o-prev)/prev*100),
        "upper_shadow":(h-max(c,o))/rng,"body_pct":abs(c-o)/rng}

def v5_score(day_data):
    stocks=list(day_data.keys()); metrics=[day_data[s] for s in stocks]
    chg=np.array([m["change_pct"] for m in metrics]); vol=np.array([m["volume"] for m in metrics])
    dp=np.array([m["day_pos"] for m in metrics]); gp=np.array([m["gap"] for m in metrics])
    us=np.array([m["upper_shadow"] for m in metrics]); bp=np.array([m["body_pct"] for m in metrics])
    momentum=_mm(chg); liquidity=_mm(vol); trend=_mm(dp*0.5+gp.clip(-5,10)/10*0.5*100)
    sweet=100-np.abs(chg-5)/5*100; sweet=sweet.clip(0,100); sweet=np.where(chg<=8,sweet,sweet*0.5)
    breakout=0.5*sweet+0.3*(1-us)*100+0.2*bp*100
    composite=0.25*momentum+0.30*liquidity+0.20*trend+0.25*breakout
    return sorted([(s,round(float(composite[i]),1),day_data[s]["close"]) for i,s in enumerate(stocks)],
        key=lambda x:x[1],reverse=True)

def main():
    print("Step 1: SH index...",file=sys.stderr)
    index_data=fetch_index_kline()

    print("Step 2: Codes...",file=sys.stderr)
    all_codes=get_all_codes()
    print(f"  {len(all_codes)} stocks",file=sys.stderr)

    print("Step 3: K-line...",file=sys.stderr)
    all_kline={}; t0=time.time(); done=0
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures={ex.submit(fetch_kline,code):(code,name) for code,name in all_codes}
        for fut in as_completed(futures):
            done+=1; c,n=futures[fut]; b=fut.result()
            if b: all_kline[(c,n)]=b
            if done%500==0: print(f"  {done}/{len(all_codes)} | {time.time()-t0:.0f}s",file=sys.stderr)
    print(f"  Done: {len(all_kline)} in {time.time()-t0:.0f}s",file=sys.stderr)

    print("Step 4: Gate indicators...",file=sys.stderr)
    gate_inds={}; tech_flags={}
    for (code,name),bars in all_kline.items():
        inds=compute_gate_indicators(bars)
        if inds: gate_inds[code]=inds; tech_flags[code]=_is_tech(name)
    print(f"  {len(gate_inds)} stocks",file=sys.stderr)

    all_dates=sorted(set(d for bars in all_kline.values() for b in bars for d in [b["date"]]))
    tdates=[d for d in all_dates if START_DATE<=d<=END_DATE]

    market_buy=set(); hold=set(); liq=set(); regime={}
    if index_data:
        for d in tdates:
            ma20=compute_ma(index_data,d,all_dates,20); ma50=compute_ma(index_data,d,all_dates,50)
            sh=index_data.get(d,0)
            if ma20 and sh>ma20: market_buy.add(d); regime[d]="bull"
            elif ma50 and sh<ma50: liq.add(d); regime[d]="bear"
            else: hold.add(d); regime[d]="range"

    rebal=tdates[::5]
    if tdates[-1] not in rebal: rebal.append(tdates[-1])

    def next_open(code,date_str):
        bars=next((b for (c,_),b in all_kline.items() if c==code),None)
        if not bars: return None
        bi=next((i for i,b in enumerate(bars) if b["date"]==date_str),None)
        if bi is None or bi+1>=len(bars): return None
        return bars[bi+1]["open"]

    cn={code:name for (code,name),_ in all_kline.items()}
    pe_data={}
    dim={d:i for i,d in enumerate(tdates)}

    # TWO parallel portfolios
    cash_close=INITIAL_CAPITAL; pos_close={}
    cash_open=INITIAL_CAPITAL; pos_open={}
    vals_close=[]; vals_open=[]

    for wn,rd in enumerate(rebal):
        di=dim[rd]; mr=regime.get(rd,"range")
        can_buy=rd in market_buy or rd in hold
        must_liq=rd in liq
        pos_frac=POSITION_SIZE if mr=="bull" else POSITION_SIZE*0.5

        # Process exits for both portfolios (same triggers, different execution prices)
        for sym in list(pos_close.keys()):
            pos=pos_close[sym]; pos2=pos_open.get(sym)
            bars=next((b for (c,_),b in all_kline.items() if c==sym),None)
            if not bars: continue
            bi=next((i for i,b in enumerate(bars) if b["date"]==rd),None)
            if bi is None: continue
            cp=bars[bi]["close"]; pnl=(cp-pos["avg_cost"])/pos["avg_cost"]
            if cp>pos["highest_close"]: pos["highest_close"]=cp
            er=None
            if pnl<=HARD_STOP: er="stop"
            elif pnl>=BREAKEVEN_ACTIVATE and not pos.get("ba"): pos["ba"]=True
            elif pos.get("ba") and pnl<=0: er="stop"
            elif pnl<=POSITION_STOP_HALVE and not pos.get("halved"): er="half"
            elif pnl>=TRAILING_ACTIVATE:
                if (pos["highest_close"]-cp)/pos["highest_close"]>=TRAILING_STOP: er="trail"
            if not er:
                if pnl>=TAKE_PROFIT_3: er="tp3"
                elif pnl>=TAKE_PROFIT_2 and pos.get("shares",0)>pos.get("is",0)*0.5: er="half"
                elif pnl>=TAKE_PROFIT_1 and not pos.get("tp1"): er="tp1"
            if di-pos.get("bdi",0)>=MAX_HOLD_DAYS: er="expire"
            if must_liq and not er: er="liq"

            if er:
                # Close-price execution
                cash_close+=pos["shares"]*cp*(1-SLIPPAGE-COMMISSION)
                del pos_close[sym]
                # Next-open execution
                no=next_open(sym,rd) or cp
                cash_open+=pos2["shares"]*no*(1-SLIPPAGE-COMMISSION)
                del pos_open[sym]

        # Enter new positions (same selection, different execution prices)
        if can_buy and len(pos_close)<MAX_POSITIONS and cash_close>INITIAL_CAPITAL*0.05:
            dd={}
            for (code,_),bars in all_kline.items():
                bi=next((i for i,b in enumerate(bars) if b["date"]==rd),None)
                if bi is None: continue
                m=v5_metrics(bars,bi)
                if m and 2<=m["change_pct"]<=9.8: dd[code]=m
            v5_ranked=v5_score(dd)[:V5_TOP_N]
            confirmed=[]
            for code,v5s,close in v5_ranked:
                if code not in gate_inds: continue
                bars_sym=next((b for (c,_),b in all_kline.items() if c==code),None)
                bi=next((i for i,b in enumerate(bars_sym) if b["date"]==rd),None)
                g5s=gate5_score_v2(bars_sym,bi,gate_inds[code],None,tech_flags.get(code,False),mr)
                if g5s: confirmed.append((code,v5s,g5s,(v5s+g5s)/2,close))
            confirmed.sort(key=lambda x:x[3],reverse=True)

            for code,v5s,g5s,combined,close in confirmed[:MAX_POSITIONS-len(pos_close)]:
                ba=cash_close*pos_frac
                if ba<1000: break
                # Close-price entry
                pc=close*(1+SLIPPAGE+COMMISSION)
                sc=int(ba/pc)
                if sc>=100 and sc*pc<=cash_close:
                    cash_close-=sc*pc; pos_close[code]={"shares":sc,"is":sc,"avg_cost":pc,"highest_close":close,"bdi":di}
                # Next-open entry
                no=next_open(code,rd) or close
                po=no*(1+SLIPPAGE+COMMISSION)
                so=int(ba/po)
                if so>=100 and so*po<=cash_open:
                    cash_open-=so*po; pos_open[code]={"shares":so,"is":so,"avg_cost":po,"highest_close":no,"bdi":di}

        tv_close=cash_close
        for sym,pos in pos_close.items():
            for (code,_),bars in all_kline.items():
                if code==sym:
                    bar=next((b for b in bars if b["date"]==rd),None)
                    if bar: tv_close+=pos["shares"]*bar["close"]
                    break
        tv_open=cash_open
        for sym,pos in pos_open.items():
            for (code,_),bars in all_kline.items():
                if code==sym:
                    bar=next((b for b in bars if b["date"]==rd),None)
                    if bar: tv_open+=pos["shares"]*bar["close"]
                    break
        vals_close.append(tv_close); vals_open.append(tv_open)
        pc=(tv_close-INITIAL_CAPITAL)/INITIAL_CAPITAL*100
        po=(tv_open-INITIAL_CAPITAL)/INITIAL_CAPITAL*100
        print(f"  W{wn+1:2d} close:¥{tv_close:,.0f}({pc:+.1f}%) open:¥{tv_open:,.0f}({po:+.1f}%)",file=sys.stderr)

    fd=tdates[-1]
    for sym,pos in list(pos_close.items()):
        for (code,_),bars in all_kline.items():
            if code==sym:
                bar=next((b for b in bars if b["date"]==fd),None)
                if bar: cash_close+=pos["shares"]*bar["close"]*(1-SLIPPAGE-COMMISSION)
                break
    for sym,pos in list(pos_open.items()):
        for (code,_),bars in all_kline.items():
            if code==sym:
                bar=next((b for b in bars if b["date"]==fd),None)
                if bar: cash_open+=pos["shares"]*bar["close"]*(1-SLIPPAGE-COMMISSION)
                break

    tr_close=(cash_close-INITIAL_CAPITAL)/INITIAL_CAPITAL*100
    tr_open=(cash_open-INITIAL_CAPITAL)/INITIAL_CAPITAL*100

    print(f"\n{'='*60}",file=sys.stderr)
    print(f"收盘价成交: {tr_close:+.2f}%",file=sys.stderr)
    print(f"次日开盘成交: {tr_open:+.2f}%",file=sys.stderr)
    print(f"Reality gap impact: {tr_open-tr_close:+.2f}%",file=sys.stderr)
    print(f"{'='*60}",file=sys.stderr)

if __name__=="__main__": main()
