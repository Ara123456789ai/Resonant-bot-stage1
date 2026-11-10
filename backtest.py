#!/usr/bin/env python3
"""
Backtester for the Resonant SELL rules on UK Brent Oil (Deriv MT5).

Rules (from the user's spec):
  entry  : a Pluto or Saturn Ascendant aspect (30/45/60/90/120/135/150) is in orb, that planet's actors strip is amber
           (2+ actors in aspect), and Sentiment Index S < -0.15. Entry is at the exact minute the conditions are met,
           but only if they will stay true for at least --min-run minutes (the astro is computable ahead of time).
  window : weekdays, from 04:00 London time until the market close (22:00 broker time), last entry and flatten margins apply.
  exits  : all allowed Pluto/Saturn Ascendant aspects have left orb, or SL 500 points, or TP 500 points
           (750 once the Moon-Mars tint is on, also if it switches on mid-trade; kept for the rest of the trade).
  size   : whole lots, loss at the stop <= 10% of the balance (1 lot = 1 barrel, 1 point = 0.001).
  one trade per aspect window (no re-entry after an exit in the same window).
  gate   : global condition, entries only while the heliocentric Sun field is falling (red tint behind the Sun-only
           Ascendant pane: the field's running total is lower than on the previous 5-minute bar). It gates entries only;
           an open trade is not closed when the field turns up.

Prices: M1 bid OHLC as CSV (dukascopy-node or an MT5 export). Spread is applied on the ask side.
"""
import argparse, csv, os, pickle, statistics, sys, time
from datetime import datetime, timezone
from multiprocessing import Pool
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resonant_engine as E
import signals as SG

MIN = 60_000
DAY = 86_400_000
LONDON = ZoneInfo("Europe/London")
CFG_A = SG.RuleConfig(require_amber=True)
CFG_N = SG.RuleConfig(require_amber=False)


# ------------------------------------------------------------------ astro minute flags
def _chunk(args):
    a, b = args
    act, ea, en, ti, who, fall = bytearray(), bytearray(), bytearray(), bytearray(), bytearray(), bytearray()
    t = a
    cur_tb, cur_fall = None, 0
    while t < b:
        st = SG.evaluate(E.make_bar(t), CFG_A)
        act.append(1 if st.active else 0)
        ea.append(1 if st.entry_ok(CFG_A) else 0)
        en.append(1 if st.entry_ok(CFG_N) else 0)
        ti.append(1 if st.tint else 0)
        who.append((1 if st.pluto_angle >= 0 else 0) | (2 if st.saturn_angle >= 0 else 0))
        tb = t // E.BAR_MS * E.BAR_MS            # the Sun-pane tint is constant within a 5-minute bar
        if tb != cur_tb:
            cur_tb = tb
            cur_fall = 1 if SG.helio_falling(tb) else 0
        fall.append(cur_fall)
        t += MIN
    return act, ea, en, ti, who, fall


def build_astro(start_ms, end_ms, cache=None):
    if cache and os.path.exists(cache):
        with open(cache, "rb") as f:
            d = pickle.load(f)
        if d["start"] == start_ms and d["end"] == end_ms:
            print(f"astro flags loaded from {cache}", file=sys.stderr)
            return d
    jobs = [(t, min(t + 2 * DAY, end_ms)) for t in range(start_ms, end_ms, 2 * DAY)]
    t0 = time.time()
    with Pool() as p:
        parts = p.map(_chunk, jobs)
    d = {"start": start_ms, "end": end_ms}
    for i, key in enumerate(["active", "elig_a", "elig_n", "tint", "who", "fall"]):
        buf = bytearray()
        for part in parts:
            buf += part[i]
        d[key] = bytes(buf)
    print(f"astro flags for {len(d['active'])} minutes built in {time.time()-t0:.0f}s", file=sys.stderr)
    if cache:
        with open(cache, "wb") as f:
            pickle.dump(d, f)
    return d


def derive(active, elig):
    n = len(active)
    epi = [-1] * n
    cur, prev = -1, 0
    for k in range(n):
        a = active[k]
        if a:
            if not prev:
                cur += 1
            epi[k] = cur
        prev = a
    run = [0] * (n + 1)
    for k in range(n - 1, -1, -1):
        run[k] = run[k + 1] + 1 if elig[k] else 0
    return epi, run


# ------------------------------------------------------------------ prices
def _parse_time(tok, tz_offset_h):
    tok = tok.strip()
    try:
        v = float(tok)
        if v > 1e11:
            return int(v)
        if v > 1e8:
            return int(v * 1000)
    except ValueError:
        pass
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(tok, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000) - int(tz_offset_h * 3600_000)
        except ValueError:
            continue
    dt = datetime.fromisoformat(tok.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000) - int(tz_offset_h * 3600_000)
    return int(dt.timestamp() * 1000)


def load_prices(path, tz_offset_h=0.0):
    """Return {minute_ms: (o,h,l,c)} of bid prices. Handles dukascopy-node CSV and MT5 exports (tab separated)."""
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        head = f.readline()
        delim = "\t" if "\t" in head else ","
        names = [h.strip().strip("<>").lower() for h in head.strip().split(delim)]
        rdr = csv.reader(f, delimiter=delim)
        ix = {n: i for i, n in enumerate(names)}
        tcol = next((c for c in ("timestamp", "time", "datetime", "date") if c in ix), None)
        if "date" in ix and "time" in ix:
            def get_t(r): return _parse_time(r[ix["date"]] + " " + r[ix["time"]], tz_offset_h)
        elif tcol:
            def get_t(r): return _parse_time(r[ix[tcol]], tz_offset_h)
        else:
            raise SystemExit(f"cannot find a time column in {names}")
        for r in rdr:
            if not r:
                continue
            try:
                t = get_t(r) // MIN * MIN
                out[t] = (float(r[ix["open"]]), float(r[ix["high"]]), float(r[ix["low"]]), float(r[ix["close"]]))
            except (ValueError, IndexError, KeyError):
                continue
    return out


def align_prices(pr, start_ms, n):
    o = [0.0] * n; h = [0.0] * n; l = [0.0] * n; c = [0.0] * n; ok = bytearray(n)
    last = None
    for k in range(n):
        b = pr.get(start_ms + k * MIN)
        if b:
            o[k], h[k], l[k], c[k] = b
            ok[k] = 1
            last = b[3]
        elif last is not None:
            o[k] = h[k] = l[k] = c[k] = last
            ok[k] = 2          # forward filled
    return o, h, l, c, ok


# ------------------------------------------------------------------ trading windows
def make_windows(t0, n, p):
    can, flat = bytearray(n), bytearray(n)
    off = int(p["server_offset"] * 3600_000)
    wh, wm = p["window_start"]
    close_min = p["close_hour"] * 60
    last_entry = close_min - p["last_entry_margin"]
    flat_from = close_min - p["flatten_margin"]
    start_cache = {}
    for k in range(n):
        tt = t0 + k * MIN
        day = tt // DAY
        s = start_cache.get(day)
        if s is None:
            dt = datetime.fromtimestamp(day * 86400 + 43200, tz=LONDON)
            s = wh * 60 + wm - int(dt.utcoffset().total_seconds() // 60)
            start_cache[day] = s
        lb = tt + off
        mod_b = (lb % DAY) // MIN
        wd = ((lb // DAY) + 3) % 7
        mod_u = (tt % DAY) // MIN
        if wd <= 4 and mod_u >= s and mod_b < last_entry:
            can[k] = 1
        if mod_b >= flat_from:
            flat[k] = 1
    return can, flat


# ------------------------------------------------------------------ simulation
def simulate(o, h, l, c, ok, active, elig, tint, who, epi, run, can, flat, p, side="sell"):
    n = len(o)
    sp = p["spread"]
    pt = p["point"]
    sl_d = p["sl"] * pt
    tp_d = p["tp"] * pt
    tpt_d = p["tp_tint"] * pt
    min_run = p["min_run"]
    trades = []
    last_epi = -1
    k = 0
    while k < n - 2:
        if not (can[k] and ok[k] == 1 and elig[k] and run[k] >= min_run and epi[k] != last_epi):
            k += 1
            continue
        sell = side == "sell"
        e0 = o[k] if sell else o[k] + sp
        big = bool(tint[k])
        sl = e0 + sl_d if sell else e0 - sl_d
        reason, fill, jx = None, None, None
        j = k
        while True:
            if tint[j]:
                big = True
            tp = (e0 - (tpt_d if big else tp_d)) if sell else (e0 + (tpt_d if big else tp_d))
            if sell:
                adverse, favour = h[j] + sp, l[j] + sp
                sl_hit, tp_hit = adverse >= sl, favour <= tp
            else:
                adverse, favour = l[j], h[j]
                sl_hit, tp_hit = adverse <= sl, favour >= tp
            if sl_hit:
                gap = (o[j] + sp if sell else o[j]) if j > k else None
                fill = sl if gap is None else (max(sl, gap) if sell else min(sl, gap))
                reason, jx = "SL", j
                break
            if tp_hit:
                gap = (o[j] + sp if sell else o[j]) if j > k else None
                fill = tp if gap is None else (min(tp, gap) if sell else max(tp, gap))
                reason, jx = "TP", j
                break
            nx = j + 1
            if nx >= n:
                fill, reason, jx = (c[j] + sp if sell else c[j]), "END", j
                break
            if flat[nx]:
                fill, reason, jx = (o[nx] + sp if sell else o[nx]), "FLAT", nx
                break
            if not active[nx]:
                fill, reason, jx = (o[nx] + sp if sell else o[nx]), "ASPECT", nx
                break
            j = nx
        pnl = (e0 - fill) if sell else (fill - e0)
        trades.append({"k": k, "kx": jx, "reason": reason, "pnl": pnl, "tint": bool(tint[k]), "big": big, "who": who[k]})
        last_epi = epi[k]
        k = jx + 1 if reason in ("SL", "TP") else max(jx, k + 1)
    return trades


# ------------------------------------------------------------------ reporting
def summarise(trades, pt, label):
    if not trades:
        return f"{label}: no trades"
    pts = [t["pnl"] / pt for t in trades]
    wins = [x for x in pts if x > 0]
    losses = [x for x in pts if x <= 0]
    pf = (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else float("inf")
    eq, peak, dd = 0.0, 0.0, 0.0
    for x in pts:
        eq += x
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    holds = [t["kx"] - t["k"] for t in trades]
    s = [f"{label}: {len(trades)} trades, win rate {100*len(wins)/len(pts):.1f}%, expectancy {statistics.mean(pts):+.1f} pts/trade "
         f"(avg win {statistics.mean(wins) if wins else 0:.0f}, avg loss {statistics.mean(losses) if losses else 0:.0f}), "
         f"profit factor {pf:.2f}, total {sum(pts):+.0f} pts, max drawdown {dd:.0f} pts, median hold {statistics.median(holds)} min"]
    for r in ("TP", "SL", "ASPECT", "FLAT", "END"):
        sub = [t["pnl"] / pt for t in trades if t["reason"] == r]
        if sub:
            s.append(f"    exit {r:6s}: {len(sub):5d} trades ({100*len(sub)/len(pts):4.1f}%), avg {statistics.mean(sub):+7.1f} pts")
    for name, f in (("tint on at entry", lambda t: t["tint"]), ("tint off at entry", lambda t: not t["tint"]),
                    ("Pluto aspect", lambda t: t["who"] & 1), ("Saturn aspect", lambda t: t["who"] & 2)):
        sub = [t["pnl"] / pt for t in trades if f(t)]
        if sub:
            s.append(f"    {name:18s}: {len(sub):5d} trades, avg {statistics.mean(sub):+7.1f} pts, win {100*sum(1 for x in sub if x>0)/len(sub):.1f}%")
    return "\n".join(s)


def equity_curve(trades, t0, p):
    bal = p["balance"]
    sl_usd = p["sl"] * p["point"] * p["contract"]
    off = int(p["server_offset"] * 3600_000)
    day, day_start, day_pnl, n_day = None, bal, 0.0, 0
    peak, dd, taken, skipped, wins = bal, 0.0, 0, 0, 0
    for tr in trades:
        d = (t0 + tr["k"] * MIN + off) // DAY
        if d != day:
            day, day_start, day_pnl, n_day = d, bal, 0.0, 0
        lots = int(p["risk"] * bal / sl_usd + 1e-9)
        lots = min(lots, 1000)
        if lots < 1 or n_day >= p["max_per_day"] or day_pnl <= -p["daily_cap"] * day_start:
            skipped += 1
            continue
        pnl = tr["pnl"] * lots * p["contract"]
        bal += pnl
        day_pnl += pnl
        n_day += 1
        taken += 1
        wins += pnl > 0
        peak = max(peak, bal)
        dd = max(dd, (peak - bal) / peak if peak > 0 else 0)
        if bal <= 0:
            break
    return (f"equity from ${p['balance']:.2f}: ends ${bal:.2f}, {taken} trades taken ({skipped} skipped by size/daily limits), "
            f"max drawdown {100*dd:.0f}%")


def rotate(lst, r):
    r %= len(lst)
    return lst[-r:] + lst[:-r] if r else lst


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prices", required=True, help="M1 bid OHLC csv")
    ap.add_argument("--cache", default="astro_cache.pkl")
    ap.add_argument("--start"); ap.add_argument("--end")
    ap.add_argument("--spread", type=float, default=0.021)
    ap.add_argument("--point", type=float, default=0.001)
    ap.add_argument("--contract", type=float, default=1.0)
    ap.add_argument("--sl", type=float, default=500); ap.add_argument("--tp", type=float, default=500)
    ap.add_argument("--tp-tint", type=float, default=750)
    ap.add_argument("--min-run", type=int, default=5)
    ap.add_argument("--window-start", default="04:00", help="London local time")
    ap.add_argument("--close-hour", type=int, default=22, help="broker time")
    ap.add_argument("--last-entry-margin", type=int, default=30, help="minutes before close")
    ap.add_argument("--flatten-margin", type=int, default=5, help="minutes before close")
    ap.add_argument("--server-offset", type=float, default=0.0, help="broker time minus UTC, hours (the app showed UTC in winter)")
    ap.add_argument("--price-offset", type=float, default=0.0, help="timestamps in the csv are this many hours ahead of UTC")
    ap.add_argument("--balance", type=float, default=50.0)
    ap.add_argument("--risk", type=float, default=0.10)
    ap.add_argument("--daily-cap", type=float, default=0.20, help="stop for the day after losing this fraction of the day's opening balance")
    ap.add_argument("--max-per-day", type=int, default=15)
    ap.add_argument("--no-amber", action="store_true")
    ap.add_argument("--no-helio", action="store_true", help="switch the heliocentric Sun-field gate off")
    ap.add_argument("--null-shifts", type=int, default=40, help="number of weekly time-shifts for the random-timing test")
    ap.add_argument("--trades-out", default="trades.csv")
    a = ap.parse_args()

    wh, wm = (int(x) for x in a.window_start.split(":"))
    p = dict(spread=a.spread, point=a.point, contract=a.contract, sl=a.sl, tp=a.tp, tp_tint=a.tp_tint, min_run=a.min_run,
             window_start=(wh, wm), close_hour=a.close_hour, last_entry_margin=a.last_entry_margin,
             flatten_margin=a.flatten_margin, server_offset=a.server_offset, balance=a.balance, risk=a.risk,
             daily_cap=a.daily_cap, max_per_day=a.max_per_day)

    pr = load_prices(a.prices, a.price_offset)
    if not pr:
        raise SystemExit("no price rows read")
    ts = sorted(pr)
    lo = max(ts[0], int(datetime.fromisoformat(a.start).replace(tzinfo=timezone.utc).timestamp() * 1000)) if a.start else ts[0]
    hi = min(ts[-1], int(datetime.fromisoformat(a.end).replace(tzinfo=timezone.utc).timestamp() * 1000)) if a.end else ts[-1]
    lo, hi = lo // DAY * DAY, (hi // DAY + 1) * DAY
    n = (hi - lo) // MIN
    print(f"prices: {len(pr)} minute bars, {datetime.fromtimestamp(ts[0]/1000, tz=timezone.utc):%Y-%m-%d} to "
          f"{datetime.fromtimestamp(ts[-1]/1000, tz=timezone.utc):%Y-%m-%d}; testing {n} minutes", file=sys.stderr)

    astro = build_astro(lo, hi, a.cache)
    o, h, l, c, ok = align_prices(pr, lo, n)
    active = astro["active"]
    base = astro["elig_n"] if a.no_amber else astro["elig_a"]
    tint, who, fall = astro["tint"], astro["who"], astro["fall"]
    print(f"Sun field falling (red tint) in {100*sum(fall)/len(fall):.1f}% of minutes", file=sys.stderr)
    elig = bytes(x & y for x, y in zip(base, fall)) if not a.no_helio else base      # global condition: only while the field falls
    epi, run = derive(active, elig)
    can, flat = make_windows(lo, n, p)

    sell = simulate(o, h, l, c, ok, active, elig, tint, who, epi, run, can, flat, p, "sell")
    buy = simulate(o, h, l, c, ok, active, elig, tint, who, epi, run, can, flat, p, "buy")
    if not a.no_helio:
        epi0, run0 = derive(active, base)
        ungated = simulate(o, h, l, c, ok, active, base, tint, who, epi0, run0, can, flat, p, "sell")
        print("\n=== for comparison: the same rules WITHOUT the Sun-field gate ===")
        print(summarise(ungated, a.point, "ungated sell"))
    print("\n=== SELL (the strategy, Sun field must be falling) ===" if not a.no_helio else "\n=== SELL (the strategy) ===")
    print(summarise(sell, a.point, "sell"))
    print(equity_curve(sell, lo, p))
    print("\n=== diagnostic: the same entry times traded as BUY ===")
    print(summarise(buy, a.point, "buy"))

    if a.null_shifts > 0 and sell:
        real = statistics.mean(t["pnl"] for t in sell) / a.point
        nulls = []
        shifts = [s for w in range(1, a.null_shifts // 2 + 2) for s in (w, -w)][:a.null_shifts]
        for w in shifts:
            r = w * 7 * 1440
            act_s, tint_s, who_s = rotate(list(active), r), rotate(list(tint), r), rotate(list(who), r)
            el_s = rotate(list(elig), r)
            ep_s, run_s = derive(act_s, el_s)
            tr = simulate(o, h, l, c, ok, act_s, el_s, tint_s, who_s, ep_s, run_s, can, flat, p, "sell")
            if tr:
                nulls.append(statistics.mean(t["pnl"] for t in tr) / a.point)
        if nulls:
            better = sum(1 for x in nulls if x >= real)
            print(f"\n=== random-timing test: the same rules with the astro pattern shifted by whole weeks ===")
            print(f"real expectancy {real:+.1f} pts/trade; {len(nulls)} shifted runs: mean {statistics.mean(nulls):+.1f}, "
                  f"best {max(nulls):+.1f}, worst {min(nulls):+.1f}; real result beats {100*(1-better/len(nulls)):.0f}% of them")

    with open(a.trades_out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["entry_utc", "exit_utc", "reason", "points", "tint_at_entry", "tp_raised", "pluto", "saturn"])
        for t in sell:
            w.writerow([datetime.fromtimestamp((lo + t["k"] * MIN) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                        datetime.fromtimestamp((lo + t["kx"] * MIN) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                        t["reason"], round(t["pnl"] / a.point, 1), int(t["tint"]), int(t["big"]), t["who"] & 1, (t["who"] >> 1) & 1])
    print(f"\ntrade list written to {a.trades_out}")


if __name__ == "__main__":
    main()
