"""
Python port of the Resonant app (v8.2) signal engine: only the parts the trading rules need.
Source of truth: AstroEngine.kt. Every formula below mirrors that file line by line so the
server and the phone app produce the same numbers.

Defaults match the app: orb 3 deg, Sd on, Pm on, Mercury/Mars taint on, orb falloff on, London Ascendant.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

R = math.pi / 180.0


def rev(x: float) -> float:
    return ((x % 360.0) + 360.0) % 360.0


def sn(x: float) -> float:
    return math.sin(x * R)


def cs(x: float) -> float:
    return math.cos(x * R)


# name, weight, default polarity class (1 green, 0 neutral, -1 red). Order matters (same as the app).
PLANETS: List[Tuple[str, int, int]] = [
    ("Pluto", 10, -1),
    ("Mercury", 9, 0),
    ("Saturn", 8, -1),
    ("Mars", 7, 0),
    ("Venus", 6, 1),
    ("Moon", 5, 0),
    ("Jupiter", 4, 1),
    ("Sun", 3, 0),
    ("Uranus", 2, 0),
    ("Neptune", 1, 1),
]
NAMES = [p[0] for p in PLANETS]
IDX = {n: i for i, n in enumerate(NAMES)}
WEIGHT = {p[0]: p[1] for p in PLANETS}
CLASS = {p[0]: p[2] for p in PLANETS}

ANGLES = [0, 30, 45, 60, 72, 90, 120, 135, 144, 150, 180]
HARD_ANGLES = {45, 90, 135, 150, 180}
TAINT_ALL = {"Mercury", "Mars"}
CONJ_POS = {"Moon", "Mercury", "Uranus"}
POS_45 = {"Moon", "Mercury", "Sun", "Mars", "Uranus"}

GREEN_CJ = {0: 0.5, 30: 0.5, 45: 0.625, 60: 0.75, 72: 0.25, 90: 0.375, 120: 0.5, 135: 0.25, 144: 0.125, 150: 0.25, 180: 0.125}
NEUTRAL_CJ = {0: 0.5, 30: 0.5, 45: 0.625, 60: 0.75, 72: 0.25, 90: 0.875, 120: 0.5, 135: 0.375, 144: 0.125, 150: 0.25, 180: 0.125}
RED_CJ = {0: 0.5, 30: 0.625, 45: 0.75, 60: 0.375, 72: 0.25, 90: 0.875, 120: 0.25, 135: 0.5, 144: 0.125, 150: 0.375, 180: 0.25}

SD_FALL = 0.875
SD_NEUTRAL = 1.0
SD_DOM = 1.125
SD_FALL_BY_SIGN = [
    {"Moon", "Saturn", "Venus"},
    {"Mars", "Pluto", "Uranus"},
    {"Jupiter"},
    {"Mars", "Mercury", "Saturn"},
    {"Mercury", "Saturn", "Uranus"},
    {"Jupiter", "Neptune"},
    {"Mars", "Pluto", "Sun"},
    {"Moon", "Venus"},
    {"Mercury", "Uranus"},
    {"Jupiter", "Moon", "Neptune", "Venus"},
    {"Pluto", "Sun"},
    {"Mercury"},
]
SD_DOM_BY_SIGN = [
    {"Mars", "Pluto", "Sun"},
    {"Moon", "Venus"},
    {"Mercury", "Saturn", "Uranus"},
    {"Jupiter", "Moon", "Neptune", "Venus"},
    {"Pluto", "Sun"},
    {"Mercury"},
    {"Moon", "Saturn", "Venus"},
    {"Mars", "Pluto", "Uranus"},
    {"Jupiter"},
    {"Mars", "Mercury", "Saturn"},
    {"Jupiter", "Mercury", "Saturn", "Uranus"},
    {"Jupiter", "Moon", "Neptune", "Venus"},
]

PM_DIVISOR = 10.0

EL = {
    "Mercury": [48.3313, 3.24587e-5, 7.0047, 5e-8, 29.1241, 1.01444e-5, 0.387098, 0.0, 0.205635, 5.59e-10, 168.6562, 4.0923344368],
    "Venus": [76.6799, 2.4659e-5, 3.3946, 2.75e-8, 54.891, 1.38374e-5, 0.72333, 0.0, 0.006773, -1.302e-9, 48.0052, 1.6021302244],
    "Mars": [49.5574, 2.11081e-5, 1.8497, -1.78e-8, 286.5016, 2.92961e-5, 1.523688, 0.0, 0.093405, 2.516e-9, 18.6021, 0.5240207766],
    "Jupiter": [100.4542, 2.76854e-5, 1.303, -1.557e-7, 273.8777, 1.64505e-5, 5.20256, 0.0, 0.048498, 4.469e-9, 19.895, 0.0830853001],
    "Saturn": [113.6634, 2.3898e-5, 2.4886, -1.081e-7, 339.3939, 2.97661e-5, 9.55475, 0.0, 0.055546, -9.499e-9, 316.967, 0.0334442282],
    "Uranus": [74.0005, 1.3978e-5, 0.7733, 1.9e-8, 96.6612, 3.0565e-5, 19.18171, -1.55e-8, 0.047318, 7.45e-9, 142.5905, 0.011725806],
    "Neptune": [131.7806, 3.0173e-5, 1.77, -2.55e-7, 272.8461, -6.027e-6, 30.05826, 3.313e-8, 0.008606, 2.15e-9, 260.2471, 0.005995147],
    "Moon": [125.1228, -0.0529538083, 5.1454, 0.0, 318.0634, 0.1643573223, 60.2666, 0.0, 0.0549, 0.0, 115.3654, 13.0649929509],
}

LONDON = (51.5074, -0.1278)


def _orb(name: str, d: float):
    e = EL[name]
    N = e[0] + e[1] * d
    i = e[2] + e[3] * d
    w = e[4] + e[5] * d
    a = e[6] + e[7] * d
    ec = e[8] + e[9] * d
    M = rev(e[10] + e[11] * d)
    E = M + ec / R * sn(M) * (1.0 + ec * cs(M))
    for _ in range(4):
        E -= (E - ec / R * sn(E) - M) / (1.0 - ec * cs(E))
    xv = a * (cs(E) - ec)
    yv = a * math.sqrt(1.0 - ec * ec) * sn(E)
    v = math.atan2(yv, xv) / R
    r = math.hypot(xv, yv)
    u = v + w
    x = r * (cs(N) * cs(u) - sn(N) * sn(u) * cs(i))
    y = r * (sn(N) * cs(u) + cs(N) * sn(u) * cs(i))
    z = r * sn(u) * sn(i)
    return x, y, z, r, M, w, N


def planetary_positions(ms: int) -> List[float]:
    """Geocentric ecliptic longitudes in the order of PLANETS. ms = Unix time in milliseconds."""
    d = ms / 864e5 + 2440587.5 - 2451543.5
    L: Dict[str, float] = {}

    sw = 282.9404 + 4.70935e-5 * d
    se = 0.016709 - 1.151e-9 * d
    sM = rev(356.047 + 0.9856002585 * d)
    sE = sM + se / R * sn(sM) * (1.0 + se * cs(sM))
    sx = cs(sE) - se
    sy = math.sqrt(1.0 - se * se) * sn(sE)
    sl = math.atan2(sy, sx) / R + sw
    sr = math.hypot(sx, sy)
    xs = sr * cs(sl)
    ys = sr * sn(sl)
    L["Sun"] = rev(sl)

    mx, my, mz, mr, mM, mw, mN = _orb("Moon", d)
    Lm = mM + mw + mN
    Ls = sM + sw
    D = Lm - Ls
    F = Lm - mN
    Mm = mM
    L["Moon"] = rev(
        math.atan2(my, mx) / R - 1.274 * sn(Mm - 2 * D) + 0.658 * sn(2 * D)
        - 0.186 * sn(sM) - 0.059 * sn(2 * Mm - 2 * D) - 0.057 * sn(Mm - 2 * D + sM)
        + 0.053 * sn(Mm + 2 * D) + 0.046 * sn(2 * D - sM) + 0.041 * sn(Mm - sM)
        - 0.035 * sn(D) - 0.031 * sn(Mm + sM) - 0.015 * sn(2 * F - 2 * D) + 0.011 * sn(Mm - 4 * D)
    )

    O = {n: _orb(n, d) for n in ["Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune"]}
    Mj, Ms, Mu = O["Jupiter"][4], O["Saturn"][4], O["Uranus"][4]
    P = {
        "Jupiter": -0.332 * sn(2 * Mj - 5 * Ms - 67.6) - 0.056 * sn(2 * Mj - 2 * Ms + 21)
        + 0.042 * sn(3 * Mj - 5 * Ms + 21) - 0.036 * sn(Mj - 2 * Ms) + 0.022 * cs(Mj - Ms)
        + 0.023 * sn(2 * Mj - 3 * Ms + 52) - 0.016 * sn(Mj - 5 * Ms - 69),
        "Saturn": 0.812 * sn(2 * Mj - 5 * Ms - 67.6) - 0.229 * cs(2 * Mj - 4 * Ms - 2)
        + 0.119 * sn(Mj - 2 * Ms - 3) + 0.046 * sn(2 * Mj - 6 * Ms - 69) + 0.014 * sn(Mj - 3 * Ms + 32),
        "Uranus": 0.04 * sn(Ms - 2 * Mu + 6) + 0.035 * sn(Ms - 3 * Mu + 33) - 0.015 * sn(Mj - Mu + 20),
    }
    for n, (ox, oy, oz, orr, _M, _w, _N) in O.items():
        lon = math.atan2(oy, ox) / R + P.get(n, 0.0)
        lat = math.atan2(oz, math.hypot(ox, oy)) / R
        L[n] = rev(math.atan2(orr * cs(lat) * sn(lon) + ys, orr * cs(lat) * cs(lon) + xs) / R)

    Sx = 50.03 + 0.033459652 * d
    Pp = 238.95 + 0.003968789 * d
    pl = (238.9508 + 0.00400703 * d - 19.799 * sn(Pp) + 19.848 * cs(Pp) + 0.897 * sn(2 * Pp)
          - 4.956 * cs(2 * Pp) + 0.61 * sn(3 * Pp) + 1.211 * cs(3 * Pp) - 0.341 * sn(4 * Pp)
          - 0.19 * cs(4 * Pp) + 0.128 * sn(5 * Pp) - 0.034 * cs(5 * Pp) - 0.038 * sn(6 * Pp)
          + 0.031 * cs(6 * Pp) + 0.02 * sn(Sx - Pp) - 0.01 * cs(Sx - Pp))
    pb = (-5.452 * sn(Pp) - 14.974 * cs(Pp) + 3.527 * sn(2 * Pp) + 1.673 * cs(2 * Pp)
          - 1.051 * sn(3 * Pp) + 0.328 * cs(3 * Pp) + 0.179 * sn(4 * Pp) - 0.292 * cs(4 * Pp)
          + 0.019 * sn(5 * Pp) + 0.1 * cs(5 * Pp) - 0.031 * sn(6 * Pp) - 0.026 * cs(6 * Pp) + 0.011 * cs(Sx - Pp))
    pr = (40.72 + 6.68 * sn(Pp) + 6.9 * cs(Pp) - 1.18 * sn(2 * Pp) - 0.03 * cs(2 * Pp)
          + 0.15 * sn(3 * Pp) - 0.14 * cs(3 * Pp))
    L["Pluto"] = rev(math.atan2(pr * cs(pb) * sn(pl) + ys, pr * cs(pb) * cs(pl) + xs) / R)

    return [L[n] for n in NAMES]


def ascendant(ms: int, lat: float = LONDON[0], lon: float = LONDON[1]) -> float:
    jd = ms / 864e5 + 2440587.5
    dd = jd - 2451545.0
    T = dd / 36525.0
    th = rev(280.46061837 + 360.98564736629 * dd + 3.87933e-4 * T * T + lon)
    eps = 23.4393 - 3.563e-7 * (jd - 2451543.5)
    return rev(math.atan2(cs(th), -(sn(th) * cs(eps) + math.tan(lat * R) * sn(eps))) / R)


def sep(a: float, b: float) -> float:
    return abs(((a - b + 540.0) % 360.0) - 180.0)


def cj_for(name: str, angle: int) -> float:
    c = CLASS[name]
    table = GREEN_CJ if c == 1 else RED_CJ if c == -1 else NEUTRAL_CJ
    return table.get(angle, 0.0)


def sign_dignity(name: str, lon: float) -> float:
    sign = int(math.floor(lon / 30.0)) % 12
    if name in SD_DOM_BY_SIGN[sign]:
        return SD_DOM
    if name in SD_FALL_BY_SIGN[sign]:
        return SD_FALL
    return SD_NEUTRAL


def _is_tainted(i: int, longs: List[float], orb: float) -> bool:
    for j in range(len(PLANETS)):
        if j == i:
            continue
        c = PLANETS[j][2]
        angles = ANGLES if c == -1 else HARD_ANGLES if c == 0 else []
        s = sep(longs[i], longs[j])
        if any(abs(s - a) <= orb for a in angles):
            return True
    return False


def tainted_flags(longs: List[float], orb: float) -> List[bool]:
    return [PLANETS[i][2] == 0 and _is_tainted(i, longs, orb) for i in range(len(PLANETS))]


def aspect_polarity(name: str, angle: int, tainted: bool, taint_all_mm: bool = False) -> int:
    c = CLASS[name]
    if c != 0:
        base = c
    elif tainted and taint_all_mm and name in TAINT_ALL:
        base = -1
    elif angle == 0:
        base = 1 if name in CONJ_POS else -1
    elif angle == 45 and name in POS_45:
        base = 1
    elif angle in HARD_ANGLES:
        base = -1
    else:
        base = 1
    return -base if angle in (72, 144) else base


def planet_modifiers(longs: List[float], orb: float) -> List[float]:
    pm = [0.0] * len(PLANETS)
    for i in range(len(PLANETS)):
        for j in range(len(PLANETS)):
            if i == j:
                continue
            s = sep(longs[i], longs[j])
            qn = NAMES[j]
            for A in ANGLES:
                if abs(s - A) <= orb:
                    pm[i] += WEIGHT[qn] * cj_for(qn, A) * aspect_polarity(qn, A, False)
    return pm


def dynamic_weights(longs: List[float], orb: float, use_sd: bool = True, use_pm: bool = True) -> List[float]:
    pm = planet_modifiers(longs, orb) if use_pm else None
    out = []
    for i, (n, w, _c) in enumerate(PLANETS):
        sd = sign_dignity(n, longs[i]) if use_sd else 1.0
        out.append(max(0.0, w * sd + (pm[i] if pm else 0.0) / PM_DIVISOR))
    return out


@dataclass
class Asp:
    planet: str
    angle: int
    orb: float
    polarity: int
    coeff: float
    dyn: float


def ascendant_aspects(asc: float, longs: List[float], orb: float = 3.0, use_sd=True, use_pm=True, taint_all_mm=True) -> List[Asp]:
    tainted = tainted_flags(longs, orb)
    dw = dynamic_weights(longs, orb, use_sd, use_pm)
    out: List[Asp] = []
    for k, (n, _w, _c) in enumerate(PLANETS):
        sp = sep(asc, longs[k])
        for A in ANGLES:
            d = abs(sp - A)
            if d <= orb:
                out.append(Asp(n, A, d, aspect_polarity(n, A, tainted[k], taint_all_mm), cj_for(n, A), dw[k]))
    return out


def score(aspects: List[Asp]) -> float:
    if not aspects:
        return 0.0
    num = sum(a.polarity * a.dyn * a.coeff for a in aspects)
    den = sum(a.dyn for a in aspects)
    return 0.0 if den <= 1e-9 else num / den


def aspect_sum(aspects: List[Asp], orb_falloff: bool = True, orb: float = 3.0) -> float:
    s = 0.0
    for a in aspects:
        of = max(0.0, 1.0 - a.orb / orb) if orb_falloff else 1.0
        s += a.polarity * a.dyn * a.coeff * of
    return s


def best_aspect(a: float, b: float, orb: float = 3.0) -> Tuple[int, float]:
    s = sep(a, b)
    best_a, best_d = -1, float("inf")
    for A in ANGLES:
        d = abs(s - A)
        if d <= orb and d < best_d:
            best_d, best_a = d, A
    return (best_a, best_d) if best_a >= 0 else (-1, 0.0)


PLUTO_ACTORS = ["Moon", "Mercury", "Mars", "Saturn", "Sun"]
SATURN_ACTORS = ["Moon", "Mercury", "Mars", "Pluto", "Sun"]


def actors_lit(longs: List[float], star: str, actors: List[str], orb: float = 3.0) -> List[Tuple[str, int]]:
    """Actors currently in aspect (any angle, orb 3) to the star planet. Same as the strip's lit cells."""
    si = IDX[star]
    out = []
    for a in actors:
        ang, _d = best_aspect(longs[si], longs[IDX[a]], orb)
        if ang >= 0:
            out.append((a, ang))
    return out


def moon_mars(longs: List[float], orb: float = 3.0) -> int:
    """Angle of the Moon-Mars aspect (the background tint) or -1."""
    ang, _d = best_aspect(longs[IDX["Moon"]], longs[IDX["Mars"]], orb)
    return ang


BAR_MS = 300_000


@dataclass
class Bar:
    t: int
    asc: float
    longs: List[float]
    aspects: List[Asp]
    s: float


def make_bar(t: int, lat: float = LONDON[0], lon: float = LONDON[1], **kw) -> Bar:
    longs = planetary_positions(t)
    asc = ascendant(t, lat, lon)
    asp = ascendant_aspects(asc, longs, **kw)
    return Bar(t, asc, longs, asp, score(asp))


def planet_asc_series(bars: List[Bar], planet: str, window: int = 12, orb_falloff: bool = True):
    """Mirror of planetAscendantSeries: detrended value, closest angle, and the held colour angle per bar."""
    n = len(bars)
    raw = [0.0] * n
    ang = [-1] * n
    for i, b in enumerate(bars):
        mine = [a for a in b.aspects if a.planet == planet]
        raw[i] = aspect_sum(mine, orb_falloff)
        if mine:
            ang[i] = min(mine, key=lambda a: a.orb).angle
    detr = []
    for i in range(n):
        lo = max(0, i - window + 1)
        detr.append(raw[i] - sum(raw[lo:i + 1]) / (i - lo + 1))
    return raw, detr, ang


# ---------------------------------------------------------------- heliocentric Sun field (app v7.0)
HELIO_PLANETS: List[Tuple[str, int, int]] = [
    ("Pluto", 10, -1),
    ("Neptune", 3, 1),
    ("Uranus", 5, 0),
    ("Saturn", 9, -1),
    ("Jupiter", 9, 1),
    ("Mars", 7, 0),
    ("Venus", 6, 1),
    ("Mercury", 6, 0),
]


def helio_longitudes(ms: int) -> List[float]:
    """Heliocentric longitudes of HELIO_PLANETS, then the Earth (geocentric Sun + 180) in the last slot."""
    d = ms / 864e5 + 2440587.5 - 2451543.5
    sw = 282.9404 + 4.70935e-5 * d
    se = 0.016709 - 1.151e-9 * d
    sM = rev(356.047 + 0.9856002585 * d)
    sE = sM + se / R * sn(sM) * (1.0 + se * cs(sM))
    sl = math.atan2(math.sqrt(1.0 - se * se) * sn(sE), cs(sE) - se) / R + sw
    O = {n: _orb(n, d) for n in ["Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune"]}
    Mj, Ms, Mu = O["Jupiter"][4], O["Saturn"][4], O["Uranus"][4]
    P = {
        "Jupiter": -0.332 * sn(2 * Mj - 5 * Ms - 67.6) - 0.056 * sn(2 * Mj - 2 * Ms + 21)
        + 0.042 * sn(3 * Mj - 5 * Ms + 21) - 0.036 * sn(Mj - 2 * Ms) + 0.022 * cs(Mj - Ms)
        + 0.023 * sn(2 * Mj - 3 * Ms + 52) - 0.016 * sn(Mj - 5 * Ms - 69),
        "Saturn": 0.812 * sn(2 * Mj - 5 * Ms - 67.6) - 0.229 * cs(2 * Mj - 4 * Ms - 2)
        + 0.119 * sn(Mj - 2 * Ms - 3) + 0.046 * sn(2 * Mj - 6 * Ms - 69) + 0.014 * sn(Mj - 3 * Ms + 32),
        "Uranus": 0.04 * sn(Ms - 2 * Mu + 6) + 0.035 * sn(Ms - 3 * Mu + 33) - 0.015 * sn(Mj - Mu + 20),
    }
    Sx = 50.03 + 0.033459652 * d
    Pp = 238.95 + 0.003968789 * d
    pl = (238.9508 + 0.00400703 * d - 19.799 * sn(Pp) + 19.848 * cs(Pp) + 0.897 * sn(2 * Pp)
          - 4.956 * cs(2 * Pp) + 0.61 * sn(3 * Pp) + 1.211 * cs(3 * Pp) - 0.341 * sn(4 * Pp)
          - 0.19 * cs(4 * Pp) + 0.128 * sn(5 * Pp) - 0.034 * cs(5 * Pp) - 0.038 * sn(6 * Pp)
          + 0.031 * cs(6 * Pp) + 0.02 * sn(Sx - Pp) - 0.01 * cs(Sx - Pp))
    out: List[float] = []
    for n, _w, _c in HELIO_PLANETS:
        if n == "Pluto":
            out.append(rev(pl))
        else:
            out.append(rev(math.atan2(O[n][1], O[n][0]) / R + P.get(n, 0.0)))
    out.append(rev(sl + 180.0))
    return out


def helio_total(ms: int, orb: float = 3.0, use_sd: bool = True, use_pm: bool = True,
                taint_all_mm: bool = True, orb_falloff: bool = True) -> float:
    """Running total of the heliocentric aspects to the Sun (the yellow line of the Helio Sun card)."""
    longs = helio_longitudes(ms)
    earth = longs[len(HELIO_PLANETS)]
    n = len(HELIO_PLANETS)
    tainted = []
    for i in range(n):
        t = False
        if HELIO_PLANETS[i][2] == 0:
            for j in range(n):
                if j == i:
                    continue
                c = HELIO_PLANETS[j][2]
                angles = ANGLES if c == -1 else HARD_ANGLES if c == 0 else []
                s = sep(longs[i], longs[j])
                if any(abs(s - a) <= orb for a in angles):
                    t = True
                    break
        tainted.append(t)
    pm = [0.0] * n
    if use_pm:
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                s = sep(longs[i], longs[j])
                qn = HELIO_PLANETS[j][0]
                for A in ANGLES:
                    if abs(s - A) <= orb:
                        pm[i] += HELIO_PLANETS[j][1] * cj_for(qn, A) * aspect_polarity(qn, A, False)
    total = 0.0
    for k, (name, w, _c) in enumerate(HELIO_PLANETS):
        sd = sign_dignity(name, longs[k]) if use_sd else 1.0
        dyn = max(0.0, w * sd + pm[k] / PM_DIVISOR)
        sp = sep(earth, longs[k])
        for A in ANGLES:
            dd = abs(sp - A)
            if dd <= orb:
                of = max(0.0, 1.0 - dd / orb) if orb_falloff else 1.0
                total += aspect_polarity(name, A, tainted[k], taint_all_mm) * dyn * cj_for(name, A) * of
    return total


def helio_trend(ms: int) -> float:
    """What the Sun-only pane tint uses: this 5-minute bar's total minus the previous bar's. <0 red (falling), >0 green."""
    tb = ms // BAR_MS * BAR_MS
    return helio_total(tb) - helio_total(tb - BAR_MS)


# ---------------------------------------------------------------- Moon gate: transiting Moon in conjunction / hard aspect to Mars, Sun, Jupiter
# "Hard" follows the app's own HARD_ANGLES (45, 90, 135, 150, 180) plus the conjunction (0), as requested.
MOON_GATE_ANGLES = (0, 45, 90, 135, 150, 180)
MOON_GATE_TARGETS = ("Mars", "Sun", "Jupiter")


def moon_hard_aspect(longs: List[float], angles=MOON_GATE_ANGLES, targets=MOON_GATE_TARGETS, orb: float = 3.0) -> bool:
    m = longs[IDX["Moon"]]
    for t in targets:
        s = sep(m, longs[IDX[t]])
        for A in angles:
            if abs(s - A) <= orb:
                return True
    return False
