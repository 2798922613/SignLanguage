#!/usr/bin/env python3
"""临时核对脚本：按 gesture-recognizer.js 的口径复算 dataset/video/*.json 的静态特征。
用法: python3 .scratch/an.py 高兴_s01_011 [更多文件...]
"""
import json, math, sys, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FING = [[5, 6, 7, 8], [9, 10, 11, 12], [13, 14, 15, 16], [17, 18, 19, 20]]
TIPS = [4, 8, 12, 16, 20]


def d3(a, b, ar):
    dx = a["x"] - b["x"]; dy = (a["y"] - b["y"]) * ar; dz = a.get("z", 0) - b.get("z", 0)
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def d2(a, b, ar):
    return math.hypot(a["x"] - b["x"], (a["y"] - b["y"]) * ar)


def unit(a, b, ar):
    vx = b["x"] - a["x"]; vy = (b["y"] - a["y"]) * ar; vz = b.get("z", 0) - a.get("z", 0)
    l = math.sqrt(vx * vx + vy * vy + vz * vz) or 1e-9
    return vx / l, vy / l, vz / l


def handfeat(pose, p, ar):
    ls, rs, nose = pose[11], pose[12], pose[0]
    sw = math.hypot(ls["x"] - rs["x"], ls["y"] - rs["y"])
    if sw < 0.01:
        return None
    cx = (ls["x"] + rs["x"]) / 2; cy = (ls["y"] + rs["y"]) / 2
    ext = [d3(p[2], p[4], ar) / max(d3(p[2], p[3], ar) + d3(p[3], p[4], ar), 1e-6)]
    for m, pip, dip, tip in FING:
        arc = d3(p[m], p[pip], ar) + d3(p[pip], p[dip], ar) + d3(p[dip], p[tip], ar)
        ext.append(d3(p[m], p[tip], ar) / max(arc, 1e-6))
    pl = d3(p[0], p[9], ar)
    if pl < 1e-6:
        return None
    ax = p[5]["x"] - p[0]["x"]; ay = (p[5]["y"] - p[0]["y"]) * ar; az = p[5].get("z", 0) - p[0].get("z", 0)
    bx = p[17]["x"] - p[0]["x"]; by = (p[17]["y"] - p[0]["y"]) * ar; bz = p[17].get("z", 0) - p[0].get("z", 0)
    nx = ay * bz - az * by; ny0 = az * bx - ax * bz; nz = ax * by - ay * bx
    nl = math.sqrt(nx * nx + ny0 * ny0 + nz * nz) or 1e-9
    mouth = {"x": (pose[9]["x"] + pose[10]["x"]) / 2, "y": (pose[9]["y"] + pose[10]["y"]) / 2}
    eyeY = (pose[2]["y"] + pose[5]["y"]) / 2
    dx1, dy1, dz1 = unit(p[5], p[8], ar)
    dxT, dyT, dzT = unit(p[2], p[4], ar)
    dxP, dyP, dzP = unit(p[17], p[20], ar)
    palmz = (p[0].get("z", 0) + p[5].get("z", 0) + p[9].get("z", 0) + p[17].get("z", 0)) / 4
    f = {
        "ext": ext, "pl": pl, "sw": sw,
        "hy": -(p[9]["y"] - cy) * ar / sw, "hx": -(p[9]["x"] - cx) / sw,
        "nd": d2(p[9], nose, ar) / sw,
        "eyeH": -(p[9]["y"] - eyeY) * ar / sw,
        "ny": ny0 / nl, "nz": nz / nl, "nxv": nx / nl,
        "thUp": (p[2]["y"] - p[4]["y"]) * ar / sw,
        "thFwd": (p[4].get("z", 0) - palmz) / pl,
        "tiR": d3(p[4], p[8], ar) / pl, "tmR": d3(p[4], p[12], ar) / pl,
        "thAt5": d2(p[4], p[5], ar) / pl,
        "spr": (d3(p[8], p[12], ar) + d3(p[12], p[16], ar) + d3(p[16], p[20], ar)) / (3 * pl),
        "mn4": min(ext[1:5]), "mx3": max(ext[2:5]),
        "mdI": d2(p[8], mouth, ar) / sw, "mdM": d2(p[12], mouth, ar) / sw, "mdT": d2(p[4], mouth, ar) / sw,
        "dx1": dx1, "dy1": dy1, "adz1": abs(dz1), "dz1": dz1,
        "dxT": dxT, "dyT": dyT, "dzT": dzT,
        "dxP": dxP, "dyP": dyP, "dzP": dzP,
        "txh": -(p[8]["x"] - cx) / sw, "tyh": -(p[8]["y"] - cy) * ar / sw,
        "tipY": [-(p[i]["y"] - cy) * ar / sw for i in TIPS],
        "tipX": [-(p[i]["x"] - cx) / sw for i in TIPS],
        "dziR": (p[8].get("z", 0) - p[5].get("z", 0)) / pl,
    }
    return f


def todict(pts):
    out = []
    for q in pts:
        out.append({"x": q[0], "y": q[1], "z": q[2] if len(q) > 2 else 0.0})
    return out


def load(name):
    path = name if name.endswith(".json") else "dataset/video/%s.json" % name
    d = json.load(open(os.path.join(ROOT, path)))
    ar = d["source"]["height"] / d["source"]["width"]
    recs = []
    for f in d["frames"]:
        pose = todict(f["pose"]) if f.get("pose") else None; hs = f.get("hands", [])
        if not pose or not hs:
            continue
        hands = []
        for h in hs:
            hf = handfeat(pose, todict(h["pts"]), ar)
            if hf:
                hf["lab"] = h["label"]
                hands.append(hf)
        if not hands:
            continue
        hands.sort(key=lambda z: -z["hx"])   # hx 大 = 图像左侧（hx = -(x-cx)/sw）
        recs.append({"t": f["t"] / 1000.0, "hands": hands, "npose": True})
    return d, ar, recs


def rng(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return "n/a"
    return "%.2f…%.2f" % (min(vals), max(vals)) if min(vals) != max(vals) else "%.2f" % min(vals)


def still_windows(recs, two_hand, thr=0.25, win=0.40):
    """识别器口径：trailing 400ms 内 hx/hy 漂移 ≤0.25 肩宽（双手词用两手掌心中点）。"""
    pts = []
    for r in recs:
        hs = r["hands"]
        if two_hand and len(hs) < 2:
            pts.append(None); continue
        if two_hand:
            pts.append(((hs[0]["hx"] + hs[1]["hx"]) / 2, (hs[0]["hy"] + hs[1]["hy"]) / 2, r["t"]))
        else:
            pts.append((hs[0]["hx"], hs[0]["hy"], r["t"]))
    ok = [False] * len(recs)
    for i, p in enumerate(pts):
        if not p:
            continue
        j = i
        drift = 0.0
        while j >= 0 and pts[j] and p[2] - pts[j][2] <= win:
            drift = max(drift, math.hypot(p[0] - pts[j][0], p[1] - pts[j][1]))
            j -= 1
        ok[i] = drift <= thr
    segs = []; cur = []
    for i, o in enumerate(ok):
        if o:
            cur.append(i)
        else:
            if cur: segs.append(cur)
            cur = []
    if cur: segs.append(cur)
    segs.sort(key=lambda s: recs[s[-1]]["t"] - recs[s[0]]["t"], reverse=True)
    return segs


def fold_count(series, dead):
    """折返次数：速度符号变化（幅度超过 dead 才计）。"""
    v = [series[i + 1] - series[i] for i in range(len(series) - 1)]
    v = [x for x in v if abs(x) > dead]
    n = 0; last = 0
    for x in v:
        s = 1 if x > 0 else -1
        if last and s != last:
            n += 1
        last = s
    return n


def report(name):
    d, ar, recs = load(name)
    src = d["source"]
    two = sum(1 for r in recs if len(r["hands"]) == 2)
    one = sum(1 for r in recs if len(r["hands"]) == 1)
    nfr = len(d["frames"])
    print("=" * 78)
    print("%s  fps=%.1f  dur=%.2fs  frames=%d  %dx%d mirrored=%s" %
          (name, d["avgFps"], d["durationMs"] / 1000.0, nfr, src["width"], src["height"], src.get("mirrored")))
    print("  有手帧=%d/%d (%.0f%%)  单手=%d  双手=%d  pose=%d/%d" %
          (len(recs), nfr, 100.0 * len(recs) / nfr, one, two,
           sum(1 for f in d["frames"] if f.get("pose")), nfr))
    for tag, want2 in (("双手", True), ("单手", False)):
        if want2 and two < 5:
            continue
        if not want2 and one < 5 and two >= 5:
            continue
        segs = still_windows(recs, want2)
        if not segs:
            print("  [%s] 无静止窗口" % tag); continue
        s = segs[0]
        sub = recs[s[0]:s[-1] + 1]
        print("  [%s] 最长静止 %.2f→%.2fs (%.2fs, %d帧)  其余窗口: %s" %
              (tag, sub[0]["t"], sub[-1]["t"], sub[-1]["t"] - sub[0]["t"], len(sub),
               [(round(recs[x[0]]["t"], 2), round(recs[x[-1]]["t"] - recs[x[0]]["t"], 2)) for x in segs[1:4]]))
        keys = ["hy", "hx", "nd", "eyeH", "ny", "nz", "spr", "mn4", "mx3", "thUp", "thFwd", "thAt5",
                "tiR", "tmR", "mdI", "mdM", "mdT", "dx1", "dy1", "adz1", "dz1", "dxT", "dyT", "dzT",
                "dxP", "dyP", "dzP", "txh", "tyh", "dziR"]
        for hi, h in enumerate(("A(图左)", "B(图右)")):
            sel = [r for r in sub if len(r["hands"]) > hi]
            if not sel:
                continue
            labs = set(r["hands"][hi]["lab"] for r in sel)
            line = []
            for k in keys:
                line.append("%s %s" % (k, rng([r["hands"][hi][k] for r in sel])))
            ext = ["ext%d %s" % (i, rng([r["hands"][hi]["ext"][i] for r in sel])) for i in range(5)]
            tipY = ["tipY%d %s" % (i, rng([r["hands"][hi]["tipY"][i] for r in sel])) for i in range(5)]
            tipX = ["tipX%d %s" % (i, rng([r["hands"][hi]["tipX"][i] for r in sel])) for i in range(5)]
            print("    手%s label=%s" % (h, labs))
            print("      " + " | ".join(ext))
            print("      " + " | ".join(line[:12]))
            print("      " + " | ".join(line[12:22]))
            print("      " + " | ".join(line[22:]))
            print("      " + " | ".join(tipY))
            print("      " + " | ".join(tipX))
        if want2:
            pk = []
            for r in sub:
                if len(r["hands"]) < 2:
                    continue
                a, b = r["hands"][0], r["hands"][1]
                tg = math.hypot(a["txh"] - b["txh"], a["tyh"] - b["tyh"])
                pg = math.hypot(a["hx"] - b["hx"], a["hy"] - b["hy"])
                pk.append({"pMinExt1": min(a["ext"][1], b["ext"][1]), "pMinExt2": min(a["ext"][2], b["ext"][2]),
                           "pMaxMx3": max(a["mx3"], b["mx3"]), "pMinMn4": min(a["mn4"], b["mn4"]),
                           "pMaxHy": max(a["hy"], b["hy"]), "pMinNd": min(a["nd"], b["nd"]),
                           "pDxProd": a["dx1"] * b["dx1"], "pTipGap": tg, "pPalmGap": pg,
                           "pGapR": tg / pg if pg > 1e-6 else 9.9,
                           "pMinSpr": min(a["spr"], b["spr"]), "pMaxSpr": max(a["spr"], b["spr"]),
                           "pMinThFwd": min(a["thFwd"], b["thFwd"]), "pMaxThFwd": max(a["thFwd"], b["thFwd"]),
                           "pMinExt0": min(a["ext"][0], b["ext"][0]), "pMaxExt0": max(a["ext"][0], b["ext"][0]),
                           "pMinExt1": min(a["ext"][1], b["ext"][1]),
                           "pDyProd": a["dy1"] * b["dy1"], "pMinTyh": min(a["tyh"], b["tyh"]),
                           "pMaxTyh": max(a["tyh"], b["tyh"]),
                           "pNyProd": a["ny"] * b["ny"]})
            print("    ‖双手 " + " | ".join("%s %s" % (k, rng([r[k] for r in pk])) for k in pk[0]))
    # 运动次数（全程有手帧）
    for hi, h in enumerate(("A(图左)", "B(图右)")):
        sel = [r for r in recs if len(r["hands"]) > hi]
        if len(sel) < 8:
            continue
        xs = [r["hands"][hi]["hx"] for r in sel]
        ys = [r["hands"][hi]["hy"] for r in sel]
        md = [r["hands"][hi]["mdI"] for r in sel]
        print("  [运动] 手%s 全程 t %.2f→%.2fs  掌心x折返=%d  掌心y折返=%d  mdI折返=%d  x范围 %s  y范围 %s" %
              (h, sel[0]["t"], sel[-1]["t"], fold_count(xs, 0.02), fold_count(ys, 0.02),
               fold_count(md, 0.02), rng(xs), rng(ys)))




def series(name, keys, hi=0, step=2):
    d, ar, recs = load(name)
    print("---- series %s (手%d) ----" % (name, hi))
    print("  t     " + "  ".join("%7s" % k for k in keys))
    for i, r in enumerate(recs):
        if i % step: continue
        if len(r["hands"]) <= hi:
            print("%6.2f  (无手)" % r["t"]); continue
        h = r["hands"][hi]
        vals = []
        for k in keys:
            if k.startswith("ext"):
                vals.append(h["ext"][int(k[3])])
            elif k.startswith("tipY"):
                vals.append(h["tipY"][int(k[4])])
            elif k.startswith("tipX"):
                vals.append(h["tipX"][int(k[4])])
            else:
                vals.append(h.get(k))
        print("%6.2f  " % r["t"] + "  ".join("%7.2f" % v if v is not None else "    n/a" for v in vals))


if "--series" in sys.argv:
    a = sys.argv[1:]
    a.remove("--series")
    spec = a[0].split(",")
    series(spec[0], spec[1].replace("+", "|").split("|"), int(spec[2]) if len(spec) > 2 else 0, int(spec[3]) if len(spec) > 3 else 2)

else:
    for n in sys.argv[1:]:
        report(n)
