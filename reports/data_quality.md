# Data-quality report

## Markets by platform and outcome

| source     |   markets |   events |   yes |   no |   base_rate |
|:-----------|----------:|---------:|------:|-----:|------------:|
| kalshi     |        36 |        9 |    11 |   25 |       0.306 |
| polymarket |        39 |       21 |    11 |   28 |       0.282 |

## Markets by category

| category                |   kalshi |   polymarket |
|:------------------------|---------:|-------------:|
| Crypto                  |        5 |            5 |
| Culture & Entertainment |        5 |            5 |
| Economics & Finance     |        5 |            5 |
| Politics & World        |        5 |            5 |
| Science & Tech          |        5 |            4 |
| Short-dated recurring   |        5 |            5 |
| Sports                  |        1 |            5 |
| Weather & Climate       |        5 |            5 |

## Exclusions by reason (whole sampling frame)

| source   | reason           |   markets |
|:---------|:-----------------|----------:|
| kalshi   | no_price_history |         4 |

## Price-history coverage

| source     | price_type   |   markets |   rows |   rows_per_market |
|:-----------|:-------------|----------:|-------:|------------------:|
| kalshi     | last_trade   |        34 |   1560 |              45.9 |
| kalshi     | mid          |        35 |  12566 |             359   |
| polymarket | last_trade   |        36 |  33797 |             938.8 |
| polymarket | pm_display   |        36 |   1316 |              36.6 |

## Volume coverage

| source     |   markets |   complete |   capped |
|:-----------|----------:|-----------:|---------:|
| kalshi     |        36 |         36 |        0 |
| polymarket |        39 |         39 |        0 |

## Horizon coverage preview

| source     | horizon   |   markets |   open_then |   fresh_price |
|:-----------|:----------|----------:|------------:|--------------:|
| kalshi     | T-1d      |        36 |          25 |            10 |
| kalshi     | T-30d     |        36 |           9 |             7 |
| kalshi     | T-7d      |        36 |          22 |            11 |
| polymarket | T-1d      |        39 |          27 |            25 |
| polymarket | T-30d     |        39 |           6 |             6 |
| polymarket | T-7d      |        39 |          17 |            17 |
