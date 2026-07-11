#!/usr/bin/env python3
"""收益曲线跟踪 — 每日收盘后记录并生成 ASCII 曲线。"""
import json, os
from datetime import date

CURVE_FILE = os.path.expanduser("~/Desktop/pnl_curve.json")

def record(today, fenghuo_total, fivegate_total):
    data = {}
    if os.path.exists(CURVE_FILE):
        with open(CURVE_FILE) as f:
            data = json.load(f)
    
    entry = data.setdefault(today, {"date": today})
    entry["fenghuo"] = fenghuo_total
    entry["fivegate"] = fivegate_total
    
    # Keep last 30 days
    keys = sorted(data.keys())[-30:]
    trimmed = {k: data[k] for k in keys}
    
    with open(CURVE_FILE, "w") as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)

def render_ascii_chart():
    if not os.path.exists(CURVE_FILE):
        return None
    
    with open(CURVE_FILE) as f:
        data = json.load(f)
    
    if len(data) < 2:
        return None
    
    entries = sorted(data.items())
    labels = [e[0][5:] for e in entries]  # MM-DD
    fh_vals = [e[1]["fenghuo"] for e in entries]
    fg_vals = [e[1]["fivegate"] for e in entries]
    
    all_vals = fh_vals + fg_vals
    mn, mx = min(all_vals), max(all_vals)
    rng = mx - mn or 1
    height = 10
    
    lines = []
    lines.append("```")
    lines.append("  收益曲线 (最近 " + str(len(entries)) + " 天)")
    lines.append("  " + "─" * (len(entries) * 3 + 10))
    
    for h in range(height, -1, -1):
        level = mn + rng * h / height
        row = f"  {level:7,.0f} │"
        for i in range(len(entries)):
            fh_norm = int((fh_vals[i] - mn) / rng * height)
            fg_norm = int((fg_vals[i] - mn) / rng * height)
            if fh_norm >= h and fg_norm >= h:
                row += "▒"
            elif fh_norm >= h:
                row += "█"
            elif fg_norm >= h:
                row += "░"
            else:
                row += " "
            row += "  "
        lines.append(row)
    
    # X-axis
    lines.append("  " + " " * 10 + "└" + "─" * (len(entries) * 3))
    x_labels = "             "
    for i, l in enumerate(labels):
        if i % 3 == 0 or i == len(labels) - 1:
            x_labels += l + " "
        else:
            x_labels += "   "
    lines.append(x_labels)
    lines.append("")
    lines.append("  █ 烽火V5   ░ 5-Gate   ▒ 重叠")
    lines.append("```")
    
    return "\n".join(lines)

if __name__ == "__main__":
    # Test: record and render
    import sys
    if len(sys.argv) >= 4:
        record(sys.argv[1], float(sys.argv[2]), float(sys.argv[3]))
        print("recorded")
    else:
        chart = render_ascii_chart()
        if chart:
            print(chart)
        else:
            print("No data")
