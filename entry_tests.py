#!/usr/bin/env python3
"""
Entry-rule experiments on top of backtest.py (same prices, spread, sizing windows, exits).

Entry modes (all sells, all inside the astro window, one trade per aspect window, signals under 5 minutes skipped):
  base : enter at the first minute the astro conditions are met (the rule already tested)
  c1   : enter at the close of the first 1-minute bar that closes below the previous 1-minute bar's low
  c5   : enter at the close of the first 5-minute bar that closes below the previous 5-minute bar's low

Universes: Pluto only, Saturn only, both (Pluto or Saturn, the combined rule).
Splits   : every allowed aspect angle on its own (30/45/60/90/120/135/150), and the Moon in or out of aspect
           to the triggering planet (the Moon row of that planet's actors strip).
Entry gate (--gate): by default entries are placed only while the transiting Moon is within 3 degrees of a conjunction or
hard aspect (45/90/135/150/180) to Mars, the Sun or Jupiter. --gate helio uses the falling Sun field instead, --gate none turns it off.
Exit: the universe's aspect ends, SL 500, TP 500 (750 with the Moon-Mars tint), flat before the close.
"""
import argparse, bisect, csv, os, pickle, re, statistics, sys, time
from datetime import datetime, timezone
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resonant_engine as E
import signals as SG
import backtest as B

MIN, DAY = B.MIN, B.DAY
SELL = [30, 45, 60, 90, 120, 135, 150]
NONE = 255
FIELDS = ["pa", "sa", "pamb", "samb", "sred", "tint", "fall", "mp", "ms", "mhard"]


# ------------------------------------------------------------------ astro flags (richer than backtest.py)
def _chunk(args):
    a, b = args
    cols = {k: bytearray() for k in FIELDS}
    cur_tb, cur_fall = None, 0
    t = a
    while t < b:
        bar = E.make_bar(t)
        for planet, key_a, key_amb, key_m, actors in (("Pluto", "pa", "pamb", "mp", E.PLUTO_ACTORS),
                                                     ("Saturn", "sa", "samb", "ms", E.SATURN_ACTORS)):
            mine = [x for x in bar.aspects if x.planet == planet and x.angle in SELL]
            cols[key_a].append(SELL.index(min(mine, key=lambda x: x.orb).angle) if mine else NONE)
            lit = E.actors_lit(bar.longs, planet, actors)
            cols[key_amb].append(1 if len(lit) >= 2 else 0)
            cols[key_m].append(1 if any(n == "Moon" for n, _ in lit) else 0)
        cols["sred"].append(1 if bar.s < SG.S_RED else 0)
        cols["tint"].append(1 if E.moon_mars(bar.longs) >= 0 else 0)
        cols["mhard"].append(1 if E.moon_hard_aspect(bar.longs) else 0)
        tb = t // E.BAR_MS * E.BAR_MS
        if tb != cur_tb:
            cur_tb = tb
            cur_fall = 1 if SG.helio_falling(tb) else 0
        cols["fall"].append(cur_fall)
        t += MIN
    return [bytes(cols[k]) for k in FIELDS]


def build_astro(start_ms, end_ms, cache):
    if cache and os.path.exists(cache):
        with open(cache, "rb") as f:
            d = pickle.load(f)
        if d.get("v") == 3 and d["start"] == start_ms and d["end"] == end_ms:
            print(f"astro flags loaded from {cache}", file=sys.stderr)
            return d
    jobs = [(t, min(t + 2 * DAY, end_ms)) for t in range(start_ms, end_ms, 2 * DAY)]
    t0 = time.time()
    with Pool() as p:
        parts = p.map(_chunk, jobs)
    d = {"v": 3, "start": start_ms, "end": end_ms}
    for i, k in enumerate(FIELDS):
        d[k] = b"".join(pt[i] for pt in parts)
    print(f"astro flags built in {time.time()-t0:.0f}s", file=sys.stderr)
    if cache:
        with open(cache, "wb") as f:
            pickle.dump(d, f)
    return d


# ------------------------------------------------------------------ boolean arrays as big integers (fast & | ^)
class Flags:
    def __init__(self, A):
        self.n = len(A["pa"])
        I = lambda b: int.from_bytes(b, "big")
        self.ones = I(b"\x01" * self.n)
        any_t = bytes([1]) * 255 + bytes([0])
        self.pa_any = I(A["pa"].translate(any_t))
        self.sa_any = I(A["sa"].translate(any_t))
        eq = lambda f: bytes(1 if v == f else 0 for v in range(256))
        self.pa_eq = {i: I(A["pa"].translate(eq(i))) for i in range(len(SELL))}
        self.sa_eq = {i: I(A["sa"].translate(eq(i))) for i in range(len(SELL))}
        self.pamb, self.samb = I(A["pamb"]), I(A["samb"])
        self.sred, self.fall = I(A["sred"]), I(A["fall"])
        self.mp, self.ms = I(A["mp"]), I(A["ms"])
        self.mhard = I(A["mhard"])

    def rotate(self, A, r):
        """Flags of the astro arrays shifted by r minutes (whole weeks), for the random-timing test."""
        sh = lambda b: (b[-r:] + b[:-r]) if r else b
        return Flags({k: (sh(A[k]) if k in FIELDS else A[k]) for k in A})

    def universe(self, kind, F=None, moon=None, gate=None):
        """gate is a flag mask (as a big integer) that must be on at entry, or None for no gate."""
        n = self.n
        PA = self.pa_any if F is None else self.pa_eq[F]
        SA = self.sa_any if F is None else self.sa_eq[F]
        g = self.ones if gate is None else gate
        mp = self.ones if moon is None else (self.mp if moon else self.mp ^ self.ones)
        ms = self.ones if moon is None else (self.ms if moon else self.ms ^ self.ones)
        if kind == "pluto":
            act, el = PA, PA & self.pamb & mp & self.sred & g
        elif kind == "saturn":
            act, el = SA, SA & self.samb & ms & self.sred & g
        else:
            act = PA | SA
            el = ((PA & self.pamb & mp) | (SA & self.samb & ms)) & self.sred & g
        return act.to_bytes(n, "big"), el.to_bytes(n, "big")


# ------------------------------------------------------------------ one trade after entry
def manage(j0, e0, big, active, o, h, l, c, tint, flat, p):
    n = len(o)
    sp, pt = p["spread"], p["point"]
    sl = e0 + p["sl"] * pt
    tp_d, tpt_d = p["tp"] * pt, p["tp_tint"] * pt
    j = j0
    while True:
        if tint[j]:
            big = True
        tp = e0 - (tpt_d if big else tp_d)
        if h[j] + sp >= sl:
            fill = max(sl, o[j] + sp) if j > j0 else sl
            return j, "SL", e0 - fill, big
        if l[j] + sp <= tp:
            fill = min(tp, o[j] + sp) if j > j0 else tp
            return j, "TP", e0 - fill, big
        nx = j + 1
        if nx >= n:
            return j, "END", e0 - (c[j] + sp), big
        if flat[nx]:
            return nx, "FLAT", e0 - (o[nx] + sp), big
        if not active[nx]:
            return nx, "ASPECT", e0 - (o[nx] + sp), big
        j = nx


def run_variant(mode, act_b, el_b, ctx):
    o, h, l, c, ok, tint, can, flat, p = ctx
    n = len(o)
    spans = [(m.start(), m.end()) for m in re.finditer(rb"\x01+", el_b) if m.end() - m.start() >= p["min_run"]]
    eps = [m.start() for m in re.finditer(rb"\x01+", act_b)]
    trades, last_ep, free = [], -1, 0
    for a, b in spans:
        for k in range(max(a, free), b):
            if not can[k] or k < 10 or k >= n - 3:
                continue
            ep = bisect.bisect_right(eps, k) - 1
            if ep == last_ep:
                break
            if mode == "base":
                if ok[k] != 1:
                    continue
                j0, e0 = k, o[k]
            elif mode == "c1":
                if ok[k] != 1 or ok[k - 1] == 0 or not (c[k] < l[k - 1]):
                    continue
                j0, e0 = k + 1, c[k]
            else:  # c5: last minute of a 5-minute bar aligned to the UTC clock
                if k % 5 != 4 or ok[k] == 0 or not (c[k] < min(l[k - 9:k - 4])):
                    continue
                j0, e0 = k + 1, c[k]
            jx, reason, pnl, big = manage(j0, e0, bool(tint[k]), act_b, o, h, l, c, tint, flat, p)
            trades.append((k, jx, reason, pnl, big))
            last_ep, free = ep, jx + 1
            break
    return trades


# ------------------------------------------------------------------ statistics
def stats(trades, lo, p):
    n = len(trades)
    if n == 0:
        return dict(n=0, win=0.0, mean=0.0, t=0.0, m_ins=None, m_out=None)
    pts = [t[3] / p["point"] for t in trades]
    mean = statistics.mean(pts)
    sd = statistics.pstdev(pts) if n > 1 else 0.0
    spread_pts = p["spread"] / p["point"]
    t = (mean + spread_pts) / (sd / n ** 0.5) if sd > 0 else 0.0     # t of the gross edge (before the spread)
    yr = [datetime.fromtimestamp((lo + tr[0] * MIN) / 1000, tz=timezone.utc).year for tr in trades]
    ins = [x for x, y in zip(pts, yr) if y <= 2025]
    out = [x for x, y in zip(pts, yr) if y >= 2026]
    return dict(n=n, win=100 * sum(1 for x in pts if x > 0) / n, mean=mean, t=t,
                m_ins=statistics.mean(ins) if len(ins) >= 20 else None,
                m_out=statistics.mean(out) if len(out) >= 20 else None)


def fmt(s):
    if s["n"] == 0:
        return "   -"
    return f"{s['n']:5d} {s['mean']:+7.1f} ({s['t']:+5.1f})"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prices", required=True)
    ap.add_argument("--cache", default="astro2_cache.pkl")
    ap.add_argument("--start"); ap.add_argument("--end")
    ap.add_argument("--spread", type=float, default=0.021)
    ap.add_argument("--point", type=float, default=0.001)
    ap.add_argument("--sl", type=float, default=500); ap.add_argument("--tp", type=float, default=500)
    ap.add_argument("--tp-tint", type=float, default=750)
    ap.add_argument("--min-run", type=int, default=5)
    ap.add_argument("--window-start", default="04:00")
    ap.add_argument("--close-hour", type=int, default=22)
    ap.add_argument("--last-entry-margin", type=int, default=30)
    ap.add_argument("--flatten-margin", type=int, default=5)
    ap.add_argument("--server-offset", type=float, default=0.0)
    ap.add_argument("--price-offset", type=float, default=0.0)
    ap.add_argument("--gate", choices=["moon", "helio", "none"], default="moon",
                    help="entry gate: moon = Moon in conjunction/hard aspect to Mars, Sun or Jupiter (default); helio = Sun field falling; none")
    ap.add_argument("--null", type=int, default=20, help="weekly shifts for the random-timing test of the three combined rows")
    ap.add_argument("--out", default="entry_tests.csv")
    a = ap.parse_args()

    wh, wm = (int(x) for x in a.window_start.split(":"))
    p = dict(spread=a.spread, point=a.point, sl=a.sl, tp=a.tp, tp_tint=a.tp_tint, min_run=a.min_run,
             window_start=(wh, wm), close_hour=a.close_hour, last_entry_margin=a.last_entry_margin,
             flatten_margin=a.flatten_margin, server_offset=a.server_offset)
    pr = B.load_prices(a.prices, a.price_offset)
    ts = sorted(pr)
    lo = max(ts[0], int(datetime.fromisoformat(a.start).replace(tzinfo=timezone.utc).timestamp() * 1000)) if a.start else ts[0]
    hi = min(ts[-1], int(datetime.fromisoformat(a.end).replace(tzinfo=timezone.utc).timestamp() * 1000)) if a.end else ts[-1]
    lo, hi = lo // DAY * DAY, (hi // DAY + 1) * DAY
    n = (hi - lo) // MIN
    print(f"prices: {len(pr)} minute bars; testing {n} minutes", file=sys.stderr)
    A = build_astro(lo, hi, a.cache)
    o, h, l, c, ok = B.align_prices(pr, lo, n)
    can, flat = B.make_windows(lo, n, p)
    tint = A["tint"]
    ctx = (o, h, l, c, ok, tint, can, flat, p)
    F = Flags(A)
    gate = {"moon": F.mhard, "helio": F.fall, "none": None}[a.gate]
    gate_name = {"moon": "Moon hard aspect to Mars/Sun/Jupiter", "helio": "Sun field falling", "none": "no gate"}[a.gate]
    print(f"entry gate: {gate_name}", file=sys.stderr)

    modes =[("base", "enter at the first minute (reference)"),
             ("c1", "1-minute close below the previous 1-minute low"),
             ("c5", "5-minute close below the previous 5-minute low")]
    kinds = [("pluto", "Pluto only"), ("saturn", "Saturn only"), ("both", "Both (Pluto or Saturn)")]
    rows, results = [], {}

    def go(mode, kind, angle=None, moon=None):
        act, el = F.universe(kind, angle, moon, gate)
        tr = run_variant(mode, act, el, ctx)
        s = stats(tr, lo, p)
        rows.append((mode, kind, "all" if angle is None else SELL[angle], {None: "any", True: "moon in aspect", False: "moon not in aspect"}[moon],
                     s["n"], round(s["win"], 1), round(s["mean"], 1), round(s["t"], 2)))
        return s

    t0 = time.time()
    for mode, _ in modes:
        for kind, _ in kinds:
            results[(mode, kind, None, None)] = go(mode, kind)
    print(f"entry gate: {gate_name}")
    print("\n=== 1. HEADLINE: net result per trade in points after the 21-point spread (t = gross edge before the spread) ===")
    print(f"{'entry mode':50s} {'rule':24s}    n    mean   (t)     win%   2024-25   2026")
    for mode, mname in modes:
        for kind, kname in kinds:
            s = results[(mode, kind, None, None)]
            ins = "   -  " if s["m_ins"] is None else f"{s['m_ins']:+6.1f}"
            out = "   -  " if s["m_out"] is None else f"{s['m_out']:+6.1f}"
            print(f"{mname:50s} {kname:24s} {fmt(s)}  {s['win']:5.1f}  {ins}  {out}")
        print()

    print("=== 2. EACH ASPECT ANGLE ON ITS OWN: n, mean points, (t of gross edge) ===")
    for mode, mname in modes:
        print(f"\n-- {mname}")
        print(f"{'angle':>6s}   {'Pluto only':^22s}  {'Saturn only':^22s}  {'Both':^22s}")
        for i, ang in enumerate(SELL):
            line = f"{ang:>5d}° "
            for kind, _ in kinds:
                s = go(mode, kind, i)
                results[(mode, kind, ang, None)] = s
                line += "  " + fmt(s).rjust(22)
            print(line)

    print("\n=== 3. MOON IN ASPECT to the triggering planet (the Moon row of its actors strip) vs not ===")
    print(f"{'entry mode':50s} {'rule':24s}   Moon in aspect          Moon not in aspect")
    for mode, mname in modes:
        for kind, kname in kinds:
            s1 = go(mode, kind, None, True)
            s0 = go(mode, kind, None, False)
            results[(mode, kind, None, True)], results[(mode, kind, None, False)] = s1, s0
            print(f"{mname:50s} {kname:24s} {fmt(s1):>22s}   {fmt(s0):>22s}")
        print()

    m = len([r for r in rows])
    from statistics import NormalDist
    z = NormalDist().inv_cdf(1 - 0.025 / max(1, m))
    print(f"=== 4. HOW MUCH TO TRUST ANY SINGLE ROW ===")
    print(f"{m} variants were tested, so by chance alone a few will look good. A row only counts as a real edge if its gross t "
          f"is above {z:.1f}, AND its net mean is above zero, AND it holds in both 2024-25 and 2026.")
    good = [r for r in rows if r[4] >= 100 and r[7] >= z and r[6] > 0]
    print("rows that pass:", "none" if not good else "")
    for r in good:
        print("   ", r)
    top = sorted([r for r in rows if r[4] >= 100], key=lambda r: -r[6])[:6]
    print("best six by net mean (n >= 100):")
    for r in top:
        print(f"    {r[0]:4s} {r[1]:6s} angle {str(r[2]):>4s} {r[3]:20s} n={r[4]:5d} mean={r[6]:+7.1f} t={r[7]:+5.2f}")
    pos = sum(1 for r in rows if r[4] >= 100 and r[6] > 0)
    print(f"variants with n >= 100 and a positive net mean: {pos} of {sum(1 for r in rows if r[4] >= 100)}")

    if a.null > 0:
        print("\n=== 5. RANDOM-TIMING TEST for the three combined (Pluto or Saturn) rows: astro pattern shifted by whole weeks ===")
        shifts = [s for w in range(1, a.null // 2 + 2) for s in (w, -w)][:a.null]
        for mode, mname in modes:
            real = results[(mode, "both", None, None)]["mean"]
            null = []
            for w in shifts:
                Fs = F.rotate(A, w * 7 * 1440)
                gs = {"moon": Fs.mhard, "helio": Fs.fall, "none": None}[a.gate]
                act, el = Fs.universe("both", None, None, gs)
                tr = run_variant(mode, act, el, (o, h, l, c, ok, tint, can, flat, p))
                if tr:
                    null.append(statistics.mean(x[3] for x in tr) / p["point"])
            if null:
                print(f"{mname:50s} real {real:+6.1f}; shifted runs mean {statistics.mean(null):+6.1f}, best {max(null):+6.1f}, "
                      f"worst {min(null):+6.1f}; real beats {100*sum(1 for x in null if x < real)/len(null):.0f}% of them")

    with open(a.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["mode", "rule", "angle", "moon", "n", "win_pct", "net_mean_pts", "gross_t"])
        w.writerows(rows)
    print(f"\nall rows written to {a.out}   (took {time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
