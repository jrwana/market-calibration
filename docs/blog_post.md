# Are prediction markets calibrated? I checked 5,241 resolved markets.

*Draft.*

Ben Recht's recent post, ["No One Buys Corn in the Corn Market"](https://www.argmin.net/p/no-one-buys-corn-in-the-corn-market),
makes a pointed case against Kalshi and Polymarket. Paraphrasing: prediction markets borrowed an
academic argument (markets aggregate dispersed information and improve decisions) to get regulated
as futures exchanges, while functioning as gambling sites. Each layer of "derivatization" moves them
further from anything real. And, most testable of all, they don't produce probabilities in any
meaningful sense.

Most of that argument is about incentives, regulation and what markets are *for*. I can't settle
those with data. But the last claim has a checkable core: **if market prices are probabilities, then
events priced at 70% should happen about 70% of the time.** So I checked.

## What I did

I sampled resolved binary markets from both platforms whose scheduled close fell between November
2024 and November 2026: 2,718 from Kalshi and 2,523 from Polymarket, across eight categories, with
at most three markets per event so a 20-rung threshold ladder can't dominate. For each market I took
the price at 30 days, 7 days and 1 day before its scheduled close, and scored those prices against
what actually happened.

Three choices matter more than they look:

1. **Anchor to the scheduled close, not to resolution.** A "will X happen by December 31" market
   resolves YES the day X happens and NO only at the deadline. Measuring "one day before resolution"
   therefore depends on the answer, and it quietly flatters the market. My snapshot code never sees
   the resolution time, and a property test confirms that changing it changes nothing.
2. **No look-ahead anywhere, including liquidity.** Bucketing markets by *final* volume uses the
   future. I bucket by contracts traded before the snapshot.
3. **Correlated markets get honest error bars.** Ten candidates in one election aren't ten
   independent forecasts. Every confidence interval comes from resampling whole events.

I also excluded markets that didn't resolve cleanly. This turned out to matter more than I expected
on Polymarket: 18,658 markets in the sampling frame resolved 50/50, mostly sports props that pushed
or were voided. They're logged, not scored.

## What I found

**Prices are well calibrated.** Calibration error (ECE) was 0.02–0.04 on both platforms at every
horizon with a decent sample, which means the realized frequency in each price bin was typically within
a few points of the price. Brier skill against "always guess the base rate" ranged from +0.51 to
+0.71. The pattern holds with one market per event and on a fixed population of markets observed at
all three horizons.

**What changes with time is sharpness, not honesty.** Following the same Kalshi markets from 30 days
out to the day before, skill climbs from +0.39 to +0.84 while calibration error barely moves. Thirty
days out, the market is calibrated but cautious. The day before, it's calibrated and confident.

**Thin markets say less, but they don't lie.** Markets with under 1,000 contracts traded before the
snapshot had much lower skill than the busiest ones (+0.50 vs +0.90 on Kalshi the day before), but
similar calibration. Low liquidity shows up as hedged prices near the base rate, not as
systematically wrong ones.

**The classic favorite–longshot bias shows up, and it's small.** Cheap contracts were overpriced:
Kalshi contracts under 10¢ hit about 2 points less often than priced, and Polymarket's 10–40¢ range
ran 4–9 points rich. Favorites were priced about right. Given fees and spreads, that's consistent
with bettors overpaying a little for lottery tickets, not with prices being meaningless.

**Markets crush a no-information LLM.** As a reference point I asked an open-weights model
(gpt-oss-20b, whose training data ends in June 2024, so it can't remember these outcomes) to forecast
1,000 of the same markets from just the question and close date. The market beat it everywhere, by a
wide margin, and averaging the model with the market made things worse. The model was also poorly
calibrated, and occasionally incoherent across rungs of the same ladder. Whatever the market is
doing, it's incorporating information that a well-read generalist with no news doesn't have.

## Where this leaves Recht's argument

**What the data contradicts.** In the narrow statistical sense, these prices *do* behave like
probabilities. That isn't a theoretical claim about aggregation. It's an empirical one: across
thousands of markets, two platforms, eight categories and three horizons, priced-at-*p* events
happened about *p* of the time. "Don't produce probabilities in practice" is hard to sustain as
stated.

**What the data partly supports.** The longshot bias is a real, persistent distortion, and it's the
one you'd predict if many participants are there for the thrill of a cheap ticket, which is
Recht's gambling framing. The skill gradient by liquidity also says a lot of listed markets are too
thin to add much over a base rate.

**What the data can't address.** Calibration is necessary, not sufficient. A market can be perfectly
calibrated and useless for decisions: if it only becomes sharp the day before resolution, nobody had
time to act on it. My 30-day results suggest real but modest information a month out, which is a
weaker claim than "markets improve decisions in business and government." And nothing here touches
whether these platforms should be regulated as futures exchanges or casinos, whether the original
2008 argument was made in good faith, or what derivatization does to the underlying activity. Those
are questions about institutions and incentives. A reliability diagram can't answer them.

The honest summary: **the prices are better probabilities than the "it's just gambling" framing
implies, and that fact alone doesn't vindicate the policy case made for them.**

## Caveats

- Calibration of the *price* isn't a trading edge. Fees and spreads mean small mispricings can't be
  harvested.
- Stratifying by category means the pooled numbers describe my sample's mix, not the platforms'
  volume-weighted activity.
- Event-level clustering doesn't capture correlation *across* events (every Fed-adjacent market
  moving on one CPI print).
- Polymarket publishes no historical order book, so its snapshots use its displayed price; Kalshi's
  use the bid/ask midpoint where the book is tight.
- Only 91 Polymarket events lasted a month, so its 30-day numbers are indicative.

Code, data pipeline, tests and an interactive explorer: [repo README](../README.md).
