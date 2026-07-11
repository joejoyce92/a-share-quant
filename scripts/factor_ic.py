#!/usr/bin/env python3
"""5-Gate 因子 IC 分析。IC = 因子得分与未来收益的秩相关系数。"""
import sys, json, numpy as np
from urllib import request
from datetime import datetime
from scipy.stats import spearmanr

SINA_KLINE = "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
INITIAL = 300_000
START = "2026-01-02"
END = "2026-07-03"

def fetch_kline(symbol):
    try:
        url = f"{SINA_KLINE}?symbol={symbol}&scale=240&ma=no&datalen=200"
        req = request.Request(url, headers={"Referer": "http://finance.sina.com.cn"})
        with request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return sorted([{"date": r["day"], "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]), "volume": float(r["volume"])}
            for r in data], key=lambda x: x["date"]) if data else None
    except: return None

def compute_factors_at_date(bars, idx):
    """All 5 factor values at date idx."""
    n = len(bars)
    if idx < 30: return None
    closes = np.array([b["close"] for b in bars])
    volumes = np.array([b["volume"] for b in bars])
    
    # Factor 1: 动量 (5-day return)
    mom = (closes[idx] - closes[idx-5]) / closes[idx-5] * 100
    
    # Factor 2: 量比
    avg_vol = np.mean(volumes[idx-19:idx+1])
    vr = volumes[idx] / avg_vol if avg_vol > 0 else 1
    
    # Factor 3: 趋势 (multi-direction check)
    trend = 0
    if closes[idx] > np.mean(closes[idx-19:idx+1]): trend += 1
    if np.mean(closes[idx-19:idx+1]) > np.mean(closes[idx-59:idx+1]) if idx >= 59 else True: trend += 1
    if idx >= 5 and closes[idx] > closes[idx-5]: trend += 1
    trend = trend / 3  # normalize to 0-1
    
    # Factor 4: 涨幅适中 (30-day vs ideal range 2-10%)
    g30 = (closes[idx] - closes[idx-30]) / closes[idx-30] * 100
    if 2 <= g30 <= 10: mod = 1.0
    elif 10 < g30 <= 15: mod = 0.8
    elif 0 <= g30 <= 2: mod = 0.5
    else: mod = 0.2
    
    # Factor 5: PE proxy (cheaper = better)
    pe_proxy = 100 / closes[idx]
    
    return mom, vr, trend, mod, pe_proxy

def main():
    # Get stock codes - use Sina Market Center (same as screener)
    codes = []
    for prefix in ["sh_a","sz_a"]:
        for page in range(1, 30):
            url = f"http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page={page}&num=80&sort=symbol&asc=1&node={prefix}"
            try:
                req = request.Request(url, headers={"Referer": "http://finance.sina.com.cn"})
                with request.urlopen(req, timeout=15) as resp:
                    raw = resp.read().decode("gbk")
                data = json.loads(raw)
                if not data: break
                for stock in data:
                    code = stock.get("code", "") or stock.get("symbol", "")
                    if code and len(code) == 6:
                        codes.append(code)
            except: break
    
    print(f"Stock universe: {len(codes)}")
    
    # Sample: top 100 by code for speed
    sample = sorted(codes)[:100]
    
    # Fetch K-lines
    klines = {}
    for i, c in enumerate(sample):
        if i % 100 == 0: print(f"Fetching {i}/{len(sample)}...", file=sys.stderr)
        data = fetch_kline(c)
        if data: klines[c] = data
    print(f"K-lines: {len(klines)} stocks", file=sys.stderr)
    
    # Get SH index for dates
    sh = fetch_kline("sh000001")
    print(f"SH bars: {len(sh) if sh else 0}", file=sys.stderr)
    if not sh: return
    dates = [d["date"] for d in sh if START <= d["date"] <= END]
    print(f"Dates: {len(dates)}", file=sys.stderr)
    
    # Use every 5th date as rebalance
    rebal = [d for i, d in enumerate(dates) if i % 5 == 0]
    
    # Collect factor scores and forward returns
    factor_data = {"mom": [], "vr": [], "trend": [], "mod": [], "pe": []}
    forward_returns = []
    
    for rd_idx, rd in enumerate(rebal[:-1]):  # exclude last (no forward return)
        if rd_idx % 5 == 0: print(f"  Rebal {rd_idx+1}/{len(rebal)-1} ({rd})", file=sys.stderr)
        
        next_rd = rebal[rd_idx + 1]
        
        for c, bars in klines.items():
            # Find index for this date
            di = next((i for i, b in enumerate(bars) if b["date"] == rd), None)
            if di is None or di < 30: continue
            
            fi = next((i for i, b in enumerate(bars) if b["date"] == next_rd), None)
            if fi is None: continue
            
            factors = compute_factors_at_date(bars, di)
            if factors is None: continue
            
            fwd_ret = (bars[fi]["close"] - bars[di]["close"]) / bars[di]["close"] * 100
            
            mom, vr, trend, mod, pe = factors
            factor_data["mom"].append(mom)
            factor_data["vr"].append(vr)
            factor_data["trend"].append(trend)
            factor_data["mod"].append(mod)
            factor_data["pe"].append(pe)
            forward_returns.append(fwd_ret)
    
    print(f"\nTotal observations: {len(forward_returns)}")
    print(f"\n{'='*60}")
    print(f"{'Factor':<15} {'IC':>8} {'方向':>10} {'p-value':>10}")
    print(f"{'='*60}")
    
    for name, vals in factor_data.items():
        if len(vals) < 100: continue
        ic, pval = spearmanr(vals, forward_returns)
        direction = "正向" if ic > 0 else "反向"
        print(f"{name:<15} {ic:>8.4f} {direction:>10} {pval:>10.4f}")
    
    print(f"{'='*60}")
    print(f"\nIC > 0.03 = 有效因子  |  IC < 0 = 反向，需修正")

if __name__ == "__main__":
    main()
