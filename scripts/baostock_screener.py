#!/usr/bin/env python3
"""烽火 V5 实时选股 — 新浪OHLCV + Tushare PE/PB + 板块分类 + 五因子评分."""
import sys, json, argparse, time, re, os
from datetime import datetime
from urllib import request
import numpy as np
import pandas as pd

SINA_API = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"

SECTOR_RULES = [
    ("银行/金融", ["银行","保险","证券","信托","金融","期货"]),
    ("房地产", ["地产","房产","置业","园区","物业","城建"]),
    ("医药/医疗", ["医药","药业","制药","生物","医疗","基因","疫苗","诊断","器械","健康"]),
    ("半导体/芯片", ["芯片","半导体","微电子","晶圆","光刻","硅","封装","集成电路","存储"]),
    ("新能源", ["新能源","锂电","光伏","风电","储能","电池","太阳能","氢能","核能","充电桩","光电"]),
    ("汽车产业链", ["汽车","汽配","轮胎","驱动","变速","传动","底盘","车联","智驾","自动驾","零部件"]),
    ("消费/食品", ["食品","饮料","酒","乳业","肉","零食","调味","粮油","糖","奶","啤酒","白酒"]),
    ("电子/元器件", ["电子","电器","电气","传感","连接器","电容","电阻","电感","线缆","光纤","光学","镜头"]),
    ("软件/通信", ["软件","通信","通讯","信息","数据","网络","云","互联","数字","智能","算力"]),
    ("化工/材料", ["化工","化学","材料","新材","高分子","塑料","橡胶","涂料","玻纤","碳纤维","稀土","石墨"]),
    ("机械/装备", ["机械","装备","设备","机器","机床","工具","模具","轴承","泵","阀","压缩"]),
    ("电力/能源", ["电力","电网","发电","水电","热电","煤电","燃气","石油","石化","煤炭"]),
    ("军工/航天", ["军工","航天","航空","兵器","船舶","雷达","导航","卫星","导弹","舰船"]),
    ("建筑/建材", ["建筑","建材","水泥","玻璃","钢构","装修","装饰","幕墙","管道"]),
    ("传媒/娱乐", ["传媒","影视","游戏","动漫","广告","出版","文化","体育","旅游","演艺"]),
    ("农业/畜牧", ["农业","种业","畜牧","饲料","农药","化肥","渔业","林业","养殖","粮食"]),
    ("环保/公用", ["环保","水务","节能","减排","固废","污水","环卫","供热","燃气供应","自来水"]),
    ("纺织/服装", ["纺织","服装","家纺","化纤","面料","皮革","鞋","服饰"]),
    ("钢铁/有色", ["钢铁","钢","有色","铜","铝","锌","锡","镍","黄金","白银","冶炼"]),
    ("交通/物流", ["交通","物流","铁路","公路","港口","机场","高速","运输","快递","供应链"]),
]
SECTOR_OVERRIDES = {
    "比亚迪":"汽车产业链","宁德时代":"新能源","海康威视":"电子/元器件",
    "美的集团":"消费/食品","格力电器":"消费/食品","立讯精密":"电子/元器件",
    "药明康德":"医药/医疗","迈瑞医疗":"医药/医疗","中芯国际":"半导体/芯片",
    "韦尔股份":"半导体/芯片","北方华创":"半导体/芯片","中微公司":"半导体/芯片",
}

def classify_sector(name):
    if name in SECTOR_OVERRIDES: return SECTOR_OVERRIDES[name]
    for sector, keywords in SECTOR_RULES:
        for kw in keywords:
            if kw in name: return sector
    return "综合/其他"

def fetch_sina():
    stocks = []
    for node in ["sh_a","sz_a"]:
        page = 1
        while True:
            url = f"{SINA_API}?page={page}&num=100&sort=symbol&asc=1&node={node}"
            req = request.Request(url, headers={"Referer":"http://finance.sina.com.cn"})
            try:
                with request.urlopen(req, timeout=20) as resp: data = json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                print(f"  Sina {node} p{page} failed: {e}", file=sys.stderr); break
            if not data: break
            for s in data:
                stocks.append({
                    "code": s["code"], "name": s["name"],
                    "price": float(s["trade"]) if s.get("trade") else None,
                    "change_pct": float(s["changepercent"]) if s.get("changepercent") else None,
                    "volume": int(s["volume"]) if s.get("volume") else None,
                    "turnover_rate": float(s["turnoverratio"]) if s.get("turnoverratio") and s["turnoverratio"]!="" else None,
                    "pe": float(s["per"]) if s.get("per") and s["per"]!="" else None,
                    "pb": float(s["pb"]) if s.get("pb") and s["pb"]!="" else None,
                    "market_cap": float(s["mktcap"]) if s.get("mktcap") and s["mktcap"]!="" else None,
                    "high": float(s["high"]) if s.get("high") else None,
                    "low": float(s["low"]) if s.get("low") else None,
                    "open": float(s["open"]) if s.get("open") else None,
                    "prev_close": float(s["settlement"]) if s.get("settlement") else None,
                })
            print(f"  Sina {node} p{page}: +{len(data)} (total {len(stocks)})", file=sys.stderr)
            if len(data) < 100: break
            page += 1; time.sleep(0.15)
    return stocks

def enrich_tushare(stocks):
    """Enrich with PE/PB from tushare daily_basic. Only if token is set."""
    token = os.environ.get("TUSHARE_TOKEN","")
    if not token:
        print("  Tushare: no token, using Sina PE/PB", file=sys.stderr)
        return stocks
    try:
        import tushare as ts
        ts.set_token(token)
        pro = ts.pro_api()
        today = datetime.now().strftime("%Y%m%d")
        df = pro.daily_basic(trade_date=today, fields='ts_code,pe,pb,total_mv,turnover_rate')
        print(f"  Tushare: {len(df)} stocks with PE/PB", file=sys.stderr)
        # Build lookup: code -> (pe,pb,mkt_cap,turnover)
        lookup = {}
        for _, r in df.iterrows():
            code = r['ts_code'].replace('.SH','').replace('.SZ','')
            pe = float(r['pe']) if pd.notna(r['pe']) and r['pe']>0 else None
            pb = float(r['pb']) if pd.notna(r['pb']) and r['pb']>0 else None
            mc = float(r['total_mv']) if pd.notna(r['total_mv']) and r['total_mv']>0 else None
            tr = float(r['turnover_rate']) if pd.notna(r['turnover_rate']) and r['turnover_rate']>0 else None
            lookup[code] = (pe, pb, mc, tr)
        # Merge
        enriched = 0
        for s in stocks:
            if s['code'] in lookup:
                pe, pb, mc, tr = lookup[s['code']]
                if pe: s['pe'] = pe
                if pb: s['pb'] = pb
                if mc: s['market_cap'] = mc / 1e4  # tushare gives 元, convert to 万元 like Sina
                if tr: s['turnover_rate'] = tr
                enriched += 1
        print(f"  Tushare: enriched {enriched}/{len(stocks)} stocks", file=sys.stderr)
    except Exception as e:
        print(f"  Tushare failed: {e}, using Sina PE/PB", file=sys.stderr)
    return stocks

def _mm(s):
    mn, mx = s.min(), s.max()
    return pd.Series(50.0, index=s.index) if mx==mn else ((s-mn)/(mx-mn)*100).clip(0,100)

def compute_sector_flow(df):
    ss = df.groupby("sector").agg(
        sector_avg_chg=("change_pct","mean"),
        sector_avg_turnover=("turnover_rate","mean"),
        sector_total_volume=("volume","sum"),
        sector_count=("code","count"),
    ).reset_index()
    ss = ss[ss["sector_count"] >= 5]
    ss["sector_heat"] = _mm(ss["sector_avg_chg"])*0.5 + _mm(ss["sector_avg_turnover"])*0.3 + _mm(ss["sector_total_volume"])*0.2
    return ss

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--style", default="trend", choices=["trend","value","growth"])
    args = parser.parse_args()

    print("Step 1: Sina OHLCV...", file=sys.stderr)
    t0 = time.time()
    data = fetch_sina()
    print(f"  {len(data)} stocks in {time.time()-t0:.0f}s", file=sys.stderr)

    print("Step 2: Tushare PE/PB...", file=sys.stderr)
    data = enrich_tushare(data)

    df = pd.DataFrame(data)
    total = len(df)

    print("Classifying sectors...", file=sys.stderr)
    df["sector"] = df["name"].apply(classify_sector)

    # Filters
    df = df[df["price"].notna() & (df["price"]>0) & (df["price"]>1)]
    df = df[df["change_pct"].notna() & (df["change_pct"]>-5) & (df["change_pct"]<9.8) & (df["change_pct"]>=2)]
    df = df[df["volume"].notna() & (df["volume"]>0)]
    df = df[df["turnover_rate"].notna() & (df["turnover_rate"]>0.5) & (df["turnover_rate"]<25)]
    df = df[df["pe"].notna() & (df["pe"]>0) & (df["pe"]<200)]
    df = df[df["pb"].notna() & (df["pb"]>0) & (df["pb"]<50)]
    df = df[df["name"].apply(lambda n: "退" not in n)]
    print(f"  After filters: {len(df)}/{total}", file=sys.stderr)

    if df.empty:
        json.dump({"error":"All filtered"}, sys.stdout, ensure_ascii=False); return

    df = df.copy()
    ss = compute_sector_flow(df)
    hot = ss.sort_values("sector_heat", ascending=False).head(8)
    print("  Hottest sectors:", file=sys.stderr)
    for _, s in hot.iterrows():
        print(f"    {s['sector']}: heat={s['sector_heat']:.1f} (avg_chg={s['sector_avg_chg']:.2f}%, {int(s['sector_count'])} stocks)", file=sys.stderr)

    df = df.merge(ss[["sector","sector_heat"]], on="sector", how="left")
    df["sector_heat"] = df["sector_heat"].fillna(30)

    # Factor scores
    if args.style == "trend":
        df["momentum"] = _mm(df["change_pct"])
        df["liquidity"] = (_mm(df["turnover_rate"]) + _mm(df["volume"].astype(float))) / 2
        df["day_pos"] = ((df["price"]-df["low"])/(df["high"]-df["low"]+0.01)).clip(0,1)*100
        df["gap"] = ((df["open"]-df["prev_close"])/df["prev_close"]*100).clip(-5,10)
        df["trend_strength"] = _mm(df["day_pos"].fillna(50)*0.5 + df["gap"].fillna(0).clip(0,10)*0.5)
        df["sector_strength"] = _mm(df["sector_heat"])
        sweet = 100 - np.abs(df["change_pct"]-5)/5*100
        sweet = sweet.clip(0,100)
        sweet = sweet.where(df["change_pct"]<=8, sweet*0.5)
        rng = (df["high"]-df["low"]).abs()+0.01
        us = ((df["high"]-df[["price","open"]].max(axis=1))/rng).clip(0,1)
        body = (df["price"]-df["open"]).abs()/rng
        df["breakout_quality"] = 0.5*sweet + 0.3*(1-us)*100 + 0.2*body.clip(0,1)*100
        df["composite"] = 0.20*df["momentum"]+0.25*df["liquidity"]+0.20*df["trend_strength"]+0.15*df["sector_strength"]+0.20*df["breakout_quality"]
    elif args.style == "value":
        df["value_score"] = ((100-_mm(df["pe"]))+(100-_mm(df["pb"])))/2
        df["liquidity"] = _mm(df["turnover_rate"])
        df["sector_strength"] = _mm(df["sector_heat"])
        df["composite"] = 0.5*df["value_score"]+0.3*df["liquidity"]+0.2*df["sector_strength"]
    elif args.style == "growth":
        df["momentum"] = _mm(df["change_pct"])
        df["liquidity"] = _mm(df["turnover_rate"])
        df["value"] = (100-_mm(df["pe"]))*0.5+(100-_mm(df["pb"]))*0.5
        df["sector_strength"] = _mm(df["sector_heat"])
        df["composite"] = 0.35*df["momentum"]+0.25*df["liquidity"]+0.2*df["value"]+0.20*df["sector_strength"]

    df = df.sort_values("composite", ascending=False).head(args.top)
    style_names = {"trend":"趋势突破型","value":"价值型","growth":"成长型"}

    cands = []
    for rank, (_, r) in enumerate(df.iterrows(), 1):
        e = {"rank":rank,"symbol":str(r["code"]),"name":str(r["name"]),"sector":str(r["sector"]),
            "price":round(float(r["price"]),2),"change_pct":round(float(r["change_pct"]),2),
            "pe":round(float(r["pe"]),2),"pb":round(float(r["pb"]),2),
            "turnover_rate":round(float(r["turnover_rate"]),2)}
        cap = r.get("market_cap")
        if cap and cap>0:
            cy = cap/1e4
            e["market_cap"] = f"{cy/10000:.1f}万亿" if cy>=10000 else f"{cy:.0f}亿"
        e["scores"] = {"momentum":round(float(r.get("momentum",0)),1),
            "liquidity":round(float(r.get("liquidity",0)),1),
            "trend":round(float(r.get("trend_strength",0)),1),
            "sector":round(float(r.get("sector_strength",0)),1),
            "breakout":round(float(r.get("breakout_quality",0)),1),
            "composite":round(float(r["composite"]),1)}
        cands.append(e)

    tsl = [{"sector":s["sector"],"avg_change":round(float(s["sector_avg_chg"]),2),
        "avg_turnover":round(float(s["sector_avg_turnover"]),2),
        "stock_count":int(s["sector_count"]),"heat":round(float(s["sector_heat"]),1)} for _,s in hot.iterrows()]

    json.dump({"market":"A","strategy":style_names[args.style],
        "data_source":"新浪+Tushare","universe":"全A股",
        "total_stocks":total,"filtered_count":len(df),"returned_count":len(cands),
        "hot_sectors":tsl,"candidates":cands}, sys.stdout, ensure_ascii=False, default=str)
    print()

if __name__ == "__main__": main()
