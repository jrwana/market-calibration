# Data-quality report

## Markets by platform and outcome

| source     |   markets |   events |   yes |   no |   base_rate |
|:-----------|----------:|---------:|------:|-----:|------------:|
| kalshi     |       457 |      195 |   155 |  302 |       0.339 |
| polymarket |       458 |      225 |   121 |  337 |       0.264 |

## Markets by category

| category                |   kalshi |   polymarket |
|:------------------------|---------:|-------------:|
| Crypto                  |       59 |           62 |
| Culture & Entertainment |       59 |           56 |
| Economics & Finance     |       61 |           62 |
| Politics & World        |       56 |           47 |
| Science & Tech          |       61 |           53 |
| Short-dated recurring   |       43 |           59 |
| Sports                  |       56 |           61 |
| Weather & Climate       |       62 |           58 |

## Exclusions by reason (whole sampling frame)

| source     | reason                 |   markets |
|:-----------|:-----------------------|----------:|
| kalshi     | unresolved_or_void     |       110 |
| kalshi     | no_price_history       |        39 |
| polymarket | ambiguous_resolution   |      6367 |
| polymarket | unresolved_or_disputed |       209 |
| polymarket | no_price_history       |        34 |
| polymarket | not_binary             |        21 |

## Price-history coverage

| source     | price_type   |   markets |   rows |   rows_per_market |
|:-----------|:-------------|----------:|-------:|------------------:|
| kalshi     | last_trade   |       413 |  30039 |              72.7 |
| kalshi     | mid          |       442 | 275960 |             624.3 |
| polymarket | last_trade   |       431 | 319017 |             740.2 |
| polymarket | pm_display   |       452 |  19223 |              42.5 |

## Volume coverage

| source     |   markets |   complete |   capped |
|:-----------|----------:|-----------:|---------:|
| kalshi     |       457 |        457 |        0 |
| polymarket |       458 |        457 |        1 |

## Horizon coverage preview

| source     | horizon   |   markets |   open_then |   fresh_price |
|:-----------|:----------|----------:|------------:|--------------:|
| kalshi     | T-1d      |       457 |         274 |           192 |
| kalshi     | T-30d     |       457 |         202 |           152 |
| kalshi     | T-7d      |       457 |         203 |           136 |
| polymarket | T-1d      |       458 |         355 |           343 |
| polymarket | T-30d     |       458 |          32 |            26 |
| polymarket | T-7d      |       458 |         182 |           165 |
