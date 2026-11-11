# Data-quality report

## Markets by platform and outcome

| source     |   markets |   events |   yes |   no |   base_rate |
|:-----------|----------:|---------:|------:|-----:|------------:|
| kalshi     |      2718 |     1137 |   861 | 1857 |       0.317 |
| polymarket |      2523 |     1282 |   835 | 1688 |       0.331 |

## Markets by category

| category                |   kalshi |   polymarket |
|:------------------------|---------:|-------------:|
| Crypto                  |      347 |          375 |
| Culture & Entertainment |      342 |          342 |
| Economics & Finance     |      368 |          375 |
| Politics & World        |      345 |          328 |
| Science & Tech          |      361 |          218 |
| Short-dated recurring   |      250 |          364 |
| Sports                  |      341 |          372 |
| Weather & Climate       |      364 |          149 |

## Exclusions by reason (whole sampling frame)

| source     | reason                 |   markets |
|:-----------|:-----------------------|----------:|
| kalshi     | unresolved_or_void     |      1003 |
| kalshi     | no_price_history       |       275 |
| polymarket | ambiguous_resolution   |     18658 |
| polymarket | unresolved_or_disputed |       535 |
| polymarket | not_binary             |       496 |
| polymarket | no_price_history       |       151 |

## Price-history coverage

| source     | price_type   |   markets |    rows |   rows_per_market |
|:-----------|:-------------|----------:|--------:|------------------:|
| kalshi     | last_trade   |      2518 |  187691 |              74.5 |
| kalshi     | mid          |      2625 | 1613654 |             614.7 |
| polymarket | last_trade   |      2450 | 1860555 |             759.4 |
| polymarket | pm_display   |      2469 |  108905 |              44.1 |

## Volume coverage

| source     |   markets |   complete |   capped |
|:-----------|----------:|-----------:|---------:|
| kalshi     |      2718 |       2718 |        0 |
| polymarket |      2523 |       2517 |        6 |

## Horizon coverage preview

| source     | horizon   |   markets |   open_then |   fresh_price |
|:-----------|:----------|----------:|------------:|--------------:|
| kalshi     | T-1d      |      2718 |        1553 |          1110 |
| kalshi     | T-30d     |      2718 |        1087 |           818 |
| kalshi     | T-7d      |      2718 |        1287 |           866 |
| polymarket | T-1d      |      2523 |        1885 |          1808 |
| polymarket | T-30d     |      2523 |         208 |           199 |
| polymarket | T-7d      |      2523 |         913 |           881 |
