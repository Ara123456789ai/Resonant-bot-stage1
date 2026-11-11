# Resonant bot, stage 2: backtester and entry experiments

Tests sell rules on UK Brent Oil history. It does not trade anything.

## Base rules (backtest.py)
- Sell only. Entry the minute these are all true: a Pluto or Saturn aspect to the Ascendant (30/45/60/90/120/135/150) is in orb, that planet's actors strip is amber (2+ actors), Sentiment Index S is below -0.15, and the entry gate is open.
- Entry gate (`--gate`): default `moon` = the transiting Moon is within 3 degrees of a conjunction or hard aspect (45, 90, 135, 150, 180) to Mars, the Sun or Jupiter. `helio` = Sun field falling (the old gate). `none` = no gate. The gate only decides entries; an open trade is never closed by it.
- Signals that stay true for under 5 minutes are skipped.
- Window: weekdays from 04:00 London time to the 22:00 broker close. Last entry 30 minutes before close, flat 5 minutes before close.
- Exit when every allowed Pluto/Saturn aspect has left orb, or SL 500 points, or TP 500 (750 once the Moon-Mars tint is on). One trade per aspect window. Whole lots, loss at the stop at most 10% of the balance. Spread 0.021.

## New: entry_tests.py
Runs every variant in one go (it takes seconds once the astro flags are cached):
1. Entry mode: first minute (reference), 1-minute close below the previous 1-minute low, 5-minute close below the previous 5-minute low. The trade is opened at the close of that bar.
2. For each mode: Pluto only, Saturn only, both (Pluto or Saturn).
3. Each aspect angle on its own, for Pluto only, Saturn only and both.
4. Moon in aspect to the triggering planet (the Moon row of that planet's actors strip) versus not.
Section 4 of the report counts the variants and states how strong a result must be to count. Section 5 repeats the three combined rows with the astro pattern shifted by whole weeks.

## Steps (GitHub Codespaces)
1. Upload these files into your repo and overwrite the old ones.
2. `python3 test_sim.py` and `python3 test_entries.py`. Both should end with "passed".
3. `python3 entry_tests.py --prices download/brentcmdusd-m1-bid-2024-01-01-2026-11-10.csv > entry_report.txt`
   The first run recomputes the astro flags (a few minutes). Open `entry_report.txt` and paste or attach it.
4. Optional, the plain rule with the Moon gate: `python3 backtest.py --prices download/brentcmdusd-m1-bid-2024-01-01-2026-11-10.csv > report_moon.txt`

Options for both scripts: `--gate helio|none`, `--spread`, `--sl`, `--tp`, `--tp-tint`, `--min-run`, `--start`, `--end`.

## Notes
- Dukascopy prices come from a different feed than your Deriv account. Spread, sessions and lot rules come from your Deriv specs.
- The broker clock is assumed to be UTC (your chart showed UTC in winter). Use `--server-offset` if that changes.
- Testing many variants will throw up a few good-looking rows by chance. On random prices the same script also shows rows with t near +3. Judge a row by the rule in section 4, not by how good it looks.
