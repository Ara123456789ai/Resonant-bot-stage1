"""Mechanics tests for backtest.simulate on hand-made price paths. Run: python3 test_sim.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as B

P = dict(spread=0.021, point=0.001, sl=500, tp=500, tp_tint=750, min_run=5)
N = 40


def mk(price_fn=None, active_to=30, elig_from=2, elig_to=30, tint_from=None, flat_from=None):
    o = [100.0] * N; h = [100.0] * N; l = [100.0] * N; c = [100.0] * N
    if price_fn:
        for k in range(N):
            o[k], h[k], l[k], c[k] = price_fn(k)
    ok = bytearray([1] * N)
    active = bytearray(1 if k <= active_to else 0 for k in range(N))
    elig = bytearray(1 if elig_from <= k <= elig_to else 0 for k in range(N))
    tint = bytearray(1 if (tint_from is not None and k >= tint_from) else 0 for k in range(N))
    who = bytearray([1] * N)
    flat = bytearray(1 if (flat_from is not None and k >= flat_from) else 0 for k in range(N))
    can = bytearray([1] * N)
    epi, run = B.derive(active, elig)
    return B.simulate(o, h, l, c, ok, active, elig, tint, who, epi, run, can, flat, P, "sell")


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (" " + extra if extra else ""))
    if not cond:
        sys.exit(1)


# a) stop loss: ask high reaches entry+0.5 -> loss exactly 0.5, and no re-entry in the same aspect window
t = mk(lambda k: (100, 100.495, 100, 100) if k == 8 else (100, 100, 100, 100))
check("SL exit at -500 pts", len(t) == 1 and t[0]["reason"] == "SL" and abs(t[0]["pnl"] + 0.5) < 1e-9, str(t[:1]))
check("one trade per aspect window", len(t) == 1)

# b) take profit: ask low reaches entry-0.5 -> +0.5
t = mk(lambda k: (100, 100, 99.478, 99.6) if k == 8 else (100, 100, 100, 100))
check("TP exit at +500 pts", len(t) == 1 and t[0]["reason"] == "TP" and abs(t[0]["pnl"] - 0.5) < 1e-9, str(t[:1]))

# c) aspect exit: the aspect window ends at minute 20 -> closed at that open on the ask
t = mk(lambda k: (99.9, 99.9, 99.9, 99.9) if k >= 20 else (100, 100, 100, 100), active_to=19, elig_to=19)
check("aspect-end exit", len(t) == 1 and t[0]["reason"] == "ASPECT" and t[0]["kx"] == 20 and abs(t[0]["pnl"] - (100 - (99.9 + 0.021))) < 1e-9, str(t[:1]))

# d) tint raises the TP to 750: a 600 pt drop does not close the trade when the tint switches on mid-trade
drop = lambda k: (100, 100, 99.38, 99.4) if k == 12 else (100, 100, 100, 100)
t = mk(drop, tint_from=None)
check("600 pt drop hits the 500 pt TP without tint", t and t[0]["reason"] == "TP")
t = mk(drop, tint_from=6)
check("600 pt drop does not hit the raised 750 pt TP", t and t[0]["reason"] == "ASPECT" and t[0]["big"], str(t[:1]))
drop2 = lambda k: (100, 100, 99.2, 99.4) if k == 12 else (100, 100, 100, 100)
t = mk(drop2, tint_from=6)
check("800 pt drop hits the raised TP at +750", t and t[0]["reason"] == "TP" and abs(t[0]["pnl"] - 0.75) < 1e-9, str(t[:1]))

# e) short-lived signals are filtered: an eligible run of 4 minutes is skipped, 5 minutes is taken
check("4 minute signal skipped", mk(elig_from=10, elig_to=13) == [])
check("5 minute signal taken", len(mk(elig_from=10, elig_to=14)) == 1)

# f) flatten before the close
t = mk(flat_from=15)
check("flatten exit", len(t) == 1 and t[0]["reason"] == "FLAT" and t[0]["kx"] == 15, str(t[:1]))

# g) entry happens at the first minute the conditions hold
t = mk(elig_from=7, elig_to=30)
check("entry at the exact first minute", t and t[0]["k"] == 7)
print("all mechanics tests passed")
