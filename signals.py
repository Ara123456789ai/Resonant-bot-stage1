"""
Trading rules from the user's spec, expressed on top of the Resonant engine port.
Price-independent part: which 5-minute bars qualify for entry, and when the astro hold condition ends.
SL / TP handling is price based and lives in the executor / backtester.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Set

import resonant_engine as E

SELL_ANGLES: Set[int] = {30, 45, 60, 90, 120, 135, 150}
S_RED = -0.15


@dataclass
class RuleConfig:
    sell_angles: Set[int] = field(default_factory=lambda: set(SELL_ANGLES))
    s_red: float = S_RED
    require_amber: bool = True
    amber_matched: bool = True      # Pluto aspect needs the Pluto strip amber, Saturn aspect the Saturn strip
    amber_min: int = 2


@dataclass
class State:
    t: int
    asc: float
    s: float
    pluto_angle: int          # allowed Ascendant aspect of Pluto in orb, or -1
    saturn_angle: int
    pluto_lit: int            # actors in aspect on the Pluto strip
    saturn_lit: int
    moon_mars: int            # Moon-Mars aspect angle (the tint), or -1

    @property
    def active(self) -> bool:
        """Hold condition: any allowed Pluto or Saturn Ascendant aspect still in orb."""
        return self.pluto_angle >= 0 or self.saturn_angle >= 0

    def entry_ok(self, cfg: RuleConfig) -> bool:
        if not self.active or not (self.s < cfg.s_red):
            return False
        if not cfg.require_amber:
            return True
        p = self.pluto_angle >= 0 and self.pluto_lit >= cfg.amber_min
        s = self.saturn_angle >= 0 and self.saturn_lit >= cfg.amber_min
        if cfg.amber_matched:
            return p or s
        return (self.pluto_angle >= 0 or self.saturn_angle >= 0) and (self.pluto_lit >= cfg.amber_min or self.saturn_lit >= cfg.amber_min)

    @property
    def tint(self) -> bool:
        return self.moon_mars >= 0


def evaluate(bar: E.Bar, cfg: RuleConfig) -> State:
    def angle_of(planet: str) -> int:
        mine = [a for a in bar.aspects if a.planet == planet and a.angle in cfg.sell_angles]
        return min(mine, key=lambda a: a.orb).angle if mine else -1

    return State(
        t=bar.t,
        asc=bar.asc,
        s=bar.s,
        pluto_angle=angle_of("Pluto"),
        saturn_angle=angle_of("Saturn"),
        pluto_lit=len(E.actors_lit(bar.longs, "Pluto", E.PLUTO_ACTORS)),
        saturn_lit=len(E.actors_lit(bar.longs, "Saturn", E.SATURN_ACTORS)),
        moon_mars=E.moon_mars(bar.longs),
    )


def helio_falling(ms: int) -> bool:
    """Global gate: the heliocentric Sun field is falling (red tint behind the Sun-only Ascendant pane)."""
    return E.helio_trend(ms) < 0
