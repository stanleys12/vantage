# Vantage — a daily, evidence-first stock & ETF research desk

Vantage screens the S&P 500 + 32 major ETFs every day, deep-dives the shortlist with fundamentals,
insider activity, analyst drift, guru 13F positions, news and social sentiment, has a reasoning
layer decide independently, and publishes a ranked brief with theses, holding horizons, honest
scenario ranges — and a **live scorecard of its own picks against SPY**.

That last part is the whole point. Nothing on the market shows you its own track record. Vantage does,
because the only honest answer to "does this work?" is measured, not promised.

## What it will and will not do (read this first)

- **It will not predict returns.** No system does. What it does is tilt toward the only inputs with
  decades of evidence behind them (momentum, quality, value, low-vol, insider buying, trend), which
  historically earn a *modest* premium — a few percent a year, not 50%.
- **The "expected excess return" is deliberately small** (typically +1 to +5%/yr). It is derived from
  long-run factor premia × this name's exposure. If a product shows you +40% "expected", it is lying.
- **P(beat SPY) sits around 50–60% and that is correct.** A 3%/yr edge on a 50%-volatility stock over
  six months is nearly a coin flip — volatility swamps the edge on any *single* name. Edges show up in
  the *average* over many picks, which is what the scorecard measures.
- **Scenario ranges are wide on purpose.** Bear/base/bull = base ± one standard deviation over the
  holding period (a ~68% band). A semiconductor at 60% vol really can be −45% or +65% in six months.
- **News, social and guru 13Fs are context, not signal.** Their combined weight is capped at 3%, and the
  reasoning layer's adjustment is bounded to ±15 conviction points. The desk is informed by the crowd
  and the famous investors; it is not led by them. 13F positions are up to 4.5 months stale by law and
  are labelled as such everywhere.
- **The scorecard is the verdict.** After ~30 scored picks it will tell you, with a t-statistic, whether
  the desk is adding value, doing nothing, or hurting you. Believe it over the theses.

## How it works

```
535 names ──stage 1 (price only)──▶ 50 core + 15 low-vol sleeve + 5 ETFs
                                          │  fundamentals · insiders · analysts · news · StockTwits · 13F match
                                          ▼
                                   stage 2 composite ──▶ evidence conviction (0-100)
                                          │
                              reasoning layer (headless Claude, top-25 shortlist):
                              independent thesis · 5 investor lenses · key risk ·
                              "what would change my mind" · bounded ±15 adjustment
                                          ▼
                       final ranking ──▶ record picks ──▶ score all past picks vs SPY ──▶ brief + dashboard
```

**Factor weights** (`vantage/config.py`): momentum 28%, quality 20%, value 12%, trend 10%, insider 10%,
low-vol 8%, analyst revision 5%, catalyst 4%, news+social context 3% (capped).

**Driver & horizon**: each pick is labelled by its most *distinctive* exposure (momentum → 3-9 months,
quality/value → 1-3 years, insider → 6-12 months, catalyst → through the event), which sets the holding
period and the scenario math.

**Investor lenses**: the reasoning layer looks at every shortlisted name through Buffett, Ackman, Burry,
Druckenmiller and Damodaran framings (persona reasoning), *and* separately reports what those managers
actually hold from their real 13F filings. The two are kept distinct on purpose.

## Data (all free, no API keys)

| source | used for | freshness |
|---|---|---|
| yfinance | prices (2y daily + **1-minute intraday**), fundamentals, insider transactions, analyst recs, earnings dates, macro (VIX, 10y, dollar, oil, gold, credit) | minutes |
| Google News / Yahoo / CNBC RSS | company + market headlines (72h window, deduped, weak sentiment) | minutes |
| SEC EDGAR | 13F-HR holdings for 9 tracked managers, latest vs prior quarter (new / adds / trims / exits) | quarterly (stale by law) |
| StockTwits | bullish/bearish tagged message ratio | minutes |
| datasets/s-and-p-500-companies | universe constituents | weekly |

Reddit and X are *not* used: Reddit blocks unauthenticated access and X's API is paid. StockTwits is the
social layer. If you want more, add a key-based source in `vantage/social.py`.

"Up to the minute" is honest here: prices and headlines are minutes old and the dashboard refreshes
live prices on demand. But freshness is not edge — anything in a headline is in the price within seconds.
The desk uses fresh data to *describe* the world accurately, not to front-run it.

## Run it

```bash
cd ~/vantage
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt   # once
./.venv/bin/python run_daily.py            # full run: ~3-6 min (gurus + LLM reasoning)
./.venv/bin/python run_daily.py --fast     # ~30s: no LLM, no 13F (smoke test / rule-based brief)
./.venv/bin/python app.py                  # dashboard → http://127.0.0.1:8866
./.venv/bin/python tests.py                # unit tests
```

Outputs: `reports/YYYY-MM-DD.md` + `.json` (and `latest.*`), `data/picks.jsonl` (every pick ever),
`data/scorecard.json`, `reports/run.log`.

Scheduled (launchd, `launchd/`): weekdays **6:20am PT** (pre-open brief with overnight news) and
**1:20pm PT** (post-close refresh). Dashboard runs as a keep-alive agent. The plists hardcode the
author's paths (launchd does not expand `~` or variables), so rewrite them for your checkout when
installing. launchd will not start a job whose log directory is missing, and `reports/` is not in the
repo, so create it first:

```bash
cd ~/vantage
mkdir -p reports
for p in launchd/*.plist; do
  sed -e "s#/Users/stanleyshen/vantage#$PWD#g" -e "s#/Users/stanleyshen#$HOME#g" "$p" \
    > ~/Library/LaunchAgents/"${p:t}"
  launchctl load ~/Library/LaunchAgents/"${p:t}"
done
```

The reasoning layer uses the Claude Code CLI headlessly (`claude -p`, no tools). One full run costs
roughly **$1.5–2 on your CLI default model** (~$35–45/month at one full run per weekday); set
`VANTAGE_MODEL=sonnet` in the launchd plist to cut that ~5×. Unset uses your CLI default. If the CLI is
missing the desk degrades to rule-based text. The 1:20pm refresh runs `--fast` (no LLM, no re-record).
SEC asks for a contact in the User-Agent — put your name/email in `USER_AGENT_CONTACT` in `config.py`.

## The learning loop (and why it is deliberately slow)

Every run, `learn.py` joins the pick log with scored outcomes and:

1. **Attributes** results by driver, conviction bucket, verdict, regime and rank, and runs a strongly
   regularized (ridge) regression of realized excess on the factor exposures → *realized factor efficacy*.
2. **Adapts factor weights** toward what actually paid, by shrinkage: `new = (1−λ)·prior + λ·realized`,
   with λ ≤ 0.30 (reached only after ~120 independent pick-dates), every weight floored at 25% and capped
   at 200% of its prior. Learned weights live in `data/learned.json` and are used by the screen, the
   composite and the horizon logic on the next run.
3. **Gates the reasoning layer**: measures whether names it pushed up beat names it pushed down; if that
   is significantly negative it shrinks the adjustment cap from ±15 to ±5.
4. **Checks calibration**: if higher conviction does not mean higher excess, it cools the conviction scale.
5. **Writes `data/lessons.md`**, a self-review (aggregate stats + the last dozen resolved calls with the
   original thesis, flip condition and what actually happened) that is fed back into the reasoning prompt.

**Nothing moves until 60 scored picks over 20 independent dates.** Same-day picks are one correlated
observation, so every t-statistic here is *clustered by date*; the naive per-pick t overstates by a lot.
The loop also refuses to learn from a positive mean that disappears when the top 5% of winners are
removed — that is a fat tail, not a tilt. These gates exist because a learner on noisy market data that
adapts fast mostly learns luck and gets whipsawed; your own 30-day sprint demonstrated that 248 times.

## The historical replay (`replay.py`) — read the caveats

`replay.py` re-runs only the **price screen** (momentum/trend/low-vol) every 10 trading days over the
last 15 months and scores it against SPY. It cannot replay fundamentals, insiders, news, 13Fs or the
reasoning layer (no point-in-time source in free data), and it uses *today's* S&P 500 list for past
dates (survivorship bias, which inflates momentum). First result, 2025-07 → 2026-09:

| | naive view | honest view |
|---|---|---|
| 1-month excess | mean +3.2%, t = 3.1 | median −0.2%, hit 49%, cluster t = 2.2, **+0.3% without the top 5% of picks** |
| where it came from | "the screen works" | 5 names (LITE, MRNA, MU, WDC, LRCX) = 52% of all positive excess; 65 names across 372 picks |

The screen caught one memory-chip mania. That is the momentum profile: most picks lose, a few carry
everything, and it reverses violently at the end of a run. The system's own verdict is "do not size up
on it." Live scoring of the full desk (fundamentals + insiders + reasoning) is the only test that counts.

## The weekly tactical sleeve (`vantage/weekly.py`) — paper-first, and why

Requested: picks to buy and sell within a week, a bit more risk, "majority should be wins." The honest
position: one week is the *worst* horizon for every signal with real evidence. The desk's own 15-month
replay scored the price screen at one week at **hit 47%, median −0.38%, cluster t 0.37 — no edge**, and
weekly turnover adds spread, slippage and short-term tax. "Majority wins" cannot be backed with evidence.

So the sleeve exists to *test* the claim rather than assert it. Each day it scores the deep-dive set on the
only short-horizon signals with any support:

- **post-earnings drift** — reported ≤15 days ago with an EPS surprise > +5% and price confirming
  (misses > −5% are penalized: negative drift)
- **reversal guard** — names up > 12% last week are penalized; quality names down > 10% get a small
  bounce credit
- **trend intact, not extended** and **analyst revisions rising**; fresh positive headlines as a tiebreaker
- **earnings within 5 days** → labelled `event` (fat tail both ways), capped at 2 per day
- **volatility cap**: anything with a 5-day σ above 12% is excluded and listed — a ±50% week is a lottery
  ticket, not a trade

Up to 6 names/day, logged as `sleeve: weekly`, scored **separately** at the 1-week horizon on the scorecard
(it never feeds the learning loop, which is core-only). Sell after 5 trading days regardless. Do not put
real money on it until the weekly scorecard shows an edge over 20+ independent weeks — and expect it not to.

## The paper portfolio (`vantage/paper.py`) — dollars verified by real order records

Runs the desk's picks in a **dedicated Alpaca paper account** under rules frozen on 2026-10-02 (v1):

| sleeve | capital | rule |
|---|---|---|
| core | 40% | first run of each week: hold the top five Buy-rated picks at 8% each; sell names that left, buy the new ones |
| weekly | 30% | buy each weekly pick at 1%; sell every lot after five trading days, no matter what |
| SPY | 30% | bought once on day one and held: the yardstick for the other two |

No margin (a buy that would take cash negative is skipped and logged). Market day orders. Trades only on the
recorded morning run; the afternoon refresh just reconciles fills and marks to market. The endpoint is hard-wired
to Alpaca's paper host, and the module refuses to start on an account that already holds positions.

Setup: create a **new** Alpaca paper account, then `cp .env.example .env`, fill in `VANTAGE_ALPACA_KEY` and
`VANTAGE_ALPACA_SECRET`, `chmod 600 .env`. The next morning run starts it. Ledger: `data/paper_ledger.json`.

What it proves: dollars, sizing, turnover, cash drag, real fills. What it does not: statistical edge. It is the
same picks on the same days as the scorecard, and paper fills are optimistic.

## Charts

Click any core or weekly pick on the dashboard to open its price chart. One range control (1D / 1W / 1M / 1Y / 5Y)
above the tables applies to every open chart and is remembered. Each chart shows the price and change in text,
a marker where the desk first picked the name, a crosshair readout (mouse or arrow keys), and a data-table view.
`/?open=TICKER` deep-links to a pick. Data: `/api/chart?ticker=&range=` (yfinance, cached 1–60 min by range).

## Reading the scorecard

- **hit rate**: % of picks that beat SPY over the horizon. 50% = coin flip.
- **mean excess**: average pick return minus SPY. This is the number that matters.
- **t-stat**: mean excess / its standard error. |t| > 2 with n ≥ 30 is the first moment the result is
  distinguishable from noise. Before that, everything is provisional — including good-looking streaks.
- **top-5 daily portfolio**: what holding each day's top five, rebalanced daily, would have done vs SPY.

## Layout

```
vantage/
  config.py    universe, weights, gurus, lenses, horizons, premia, disclaimer
  universe.py  S&P 500 (cached) + ETFs
  data.py      yfinance: prices, intraday, fundamentals, insiders, analysts, macro
  news.py      RSS aggregation, dedupe, weak sentiment
  edgar.py     SEC 13F parsing, quarter-over-quarter diff, issuer→ticker matching
  social.py    StockTwits
  factors.py   stage-1 screen, stage-2 composite, contributions
  rank.py      driver, horizon, expected excess, scenarios, P(beat SPY), risk flags
  reason.py    headless-Claude reasoning with bounded adjustment; rule-based fallback
  track.py     pick log + forward-return scorecard vs SPY
  report.py    markdown + JSON brief
run_daily.py   orchestrator          app.py  dashboard        tests.py  unit tests
```

_Not financial advice. Vantage is a research tool that shows its work and its track record._
