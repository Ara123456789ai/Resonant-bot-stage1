# Resonant bot, stage 1: backtester

Tests the sell rules on UK Brent Oil history. It does not trade anything.

## Rules it tests
- Sell only. Entry the minute these are all true: a Pluto or Saturn aspect to the Ascendant (30/45/60/90/120/135/150) is in orb, that planet's actors strip is amber (2+ actors), Sentiment Index S is below -0.15, and the heliocentric Sun field is falling (red tint behind the Sun-only Ascendant pane).
- Signals that stay true for less than 5 minutes are skipped (the astro is known ahead of time, so entry is still at the exact minute the conditions start).
- Trading window: weekdays from 04:00 London time until the 22:00 broker close. Last entry 30 minutes before close, everything flat 5 minutes before close.
- Exit when every allowed Pluto/Saturn aspect has left orb, or SL 500 points, or TP 500 points (750 once the Moon-Mars tint is on, even if it switches on mid-trade).
- One trade per aspect window. Whole lots, loss at the stop at most 10% of the balance (1 lot = 1 barrel, 1 point = 0.001, so lots = balance / 5). Spread 0.021.
- The Sun-field gate only decides entries. An open trade is not closed when the field turns up.

## Steps (GitHub Codespaces)
1. Make a private GitHub repo and upload these files (Add file, Upload files). Open it in a Codespace.
2. Download free Brent 1-minute history (Dukascopy bid prices):
   `npx dukascopy-node -i brentcmdusd -from 2024-01-01 -to 2026-11-10 -t m1 -f csv`
   It writes a `download` folder. Run `ls download` to see the file name.
3. Check the mechanics: `python3 test_sim.py` (should end with "all mechanics tests passed").
4. Run the backtest: `python3 backtest.py --prices download/<that file>.csv`
   The first run spends a few minutes computing the astro flags and saves them to `astro_cache.pkl`. Later runs are fast.
5. Send me the printed report (a screenshot is fine). `trades.csv` lists every trade.

## Useful options
`--no-helio` switches the Sun-field gate off, `--no-amber` drops the amber filter, `--min-run 1` drops the 5-minute filter,
`--spread`, `--sl`, `--tp`, `--tp-tint`, `--balance`, `--start 2025-01-01`, `--end 2026-06-30`.

## Notes
- Dukascopy prices come from a different feed than your Deriv account. Spread, swap, sessions and lot rules come from your Deriv specs.
- The broker clock is assumed to be UTC (your chart showed UTC in winter). If Deriv shifts its server clock in summer, use `--server-offset`.
- The report has three parts: the strategy, the same entry times traded as buys (a sanity check), and a random-timing test where the astro pattern is shifted by whole weeks. A real edge should beat most shifted runs.
