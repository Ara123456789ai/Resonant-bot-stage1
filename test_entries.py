"""Tests for the bar-close entry modes in entry_tests.py. Run: python3 test_entries.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import entry_tests as T

N = 80
P = dict(spread=0.021, point=0.001, sl=500, tp=500, tp_tint=750, min_run=5)


def arrays(patch=None):
    o = [100.0] * N; h = [100.0] * N; l = [100.0] * N; c = [100.0] * N
    for k, (oo, hh, ll, cc) in (patch or {}).items():
        o[k], h[k], l[k], c[k] = oo, hh, ll, cc
    return o, h, l, c


def go(mode, patch=None, el_from=10, el_to=50, act_to=60):
    o, h, l, c = arrays(patch)
    ok = bytearray([1] * N); tint = bytes(N); can = bytearray([1] * N); flat = bytes(N)
    act = bytes(1 if 5 <= k <= act_to else 0 for k in range(N))
    el = bytes(1 if el_from <= k <= el_to else 0 for k in range(N))
    return T.run_variant(mode, act, el, (o, h, l, c, ok, tint, can, flat, P))


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (" " + extra if extra else ""))
    if not cond:
        sys.exit(1)


# base: enters at the first eligible minute at the open
t = go("base")
check("base enters at the first minute", len(t) == 1 and t[0][0] == 10, str(t))

# c1: waits for a 1-minute close below the previous 1-minute low; enters at that close
t = go("c1", {15: (100.0, 100.0, 99.8, 99.85)})
check("c1 enters at the close of the first bar closing below the previous low",
      len(t) == 1 and t[0][0] == 15 and t[0][2] == "ASPECT" and abs(t[0][3] - (99.85 - 100.021)) < 1e-9, str(t))
check("c1 does nothing when no bar closes below the previous low", go("c1") == [])

# c5: a 5-minute bar (minutes 20-24) closing below the previous 5-minute bar's low (minutes 15-19)
t = go("c5", {22: (100.0, 100.0, 99.7, 99.8), 24: (99.8, 99.8, 99.75, 99.75)})
check("c5 enters at the close of the 5-minute bar", len(t) == 1 and t[0][0] == 24 and abs(t[0][3] - (99.75 - 100.021)) < 1e-9, str(t))
check("c5 does nothing when no 5-minute bar closes below the previous low", go("c5") == [])

# the 5-minute signal filter: an eligible run of 4 minutes is skipped in every mode
for m in ("base", "c1", "c5"):
    check(f"{m}: 4 minute signal skipped", go(m, {12: (100, 100, 99.8, 99.85), 14: (100, 100, 99.6, 99.7)}, el_from=10, el_to=13) == [])

# stop loss right after a confirmed entry, then no second trade in the same aspect window
t = go("c1", {15: (100.0, 100.0, 99.8, 99.85), 20: (100.0, 100.5, 100.0, 100.0), 30: (100.0, 100.0, 99.5, 99.6)})
check("c1 stop loss at -500 pts and one trade per window", len(t) == 1 and t[0][2] == "SL" and abs(t[0][3] + 0.5) < 1e-9, str(t))
print("all entry-mode tests passed")
