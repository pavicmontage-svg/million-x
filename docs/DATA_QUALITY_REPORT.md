# DATA QUALITY REPORT — MillionX 2.1 (arhiva, 2024-12-01 – 2026-06-30)

*Automatski generirano iz CI izlaza (`micro-research run 44841732631`). Selekcijsko razdoblje: discovery 2025-01 – 2025-09 (samo smjer), validacija 2025-10 – 2026-06. Završni holdout (2026-07 – 2026-10) nije preuzet. Brojevi nisu ručno upisani.*

Provjere po izvoru i instrumentu (Data Quality Engine, `micro/quality.py`): praznine, duplikati, redoslijed, nevaljale vrijednosti, abnormalni skokovi, pokrivenost; za aggTrades i konzistentnost s kline taker volumenom.

| instrument | izvor | status | pokrivenost | praznine | nedostaje točaka | duplikati | van reda | nevaljalo | abnormalno | nedostaje dana |
|---|---|---|---|---|---|---|---|---|---|---|
| ADAUSDT | klines | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 634 | 0 |
| ADAUSDT | open_interest | DEGRADED | 1.0000 | 1 | 3 | 0 | 2063 | 45 | 3510 | 0 |
| ADAUSDT | order_book_depth | OK | 0.9692 | 28882 | 57744 | 0 | 0 | 0 | 0 | 0 |
| ADAUSDT | premium | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ADAUSDT | mark | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 636 | 0 |
| ADAUSDT | funding | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ADAUSDT | agg_trades | OK | 1.0000 | 1 | 19 | 0 | 0 | 0 | 0 | 0 |
| AVAXUSDT | klines | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 461 | 0 |
| AVAXUSDT | open_interest | DEGRADED | 1.0000 | 1 | 3 | 0 | 2073 | 24 | 2637 | 0 |
| AVAXUSDT | order_book_depth | OK | 0.9692 | 28882 | 57742 | 0 | 0 | 0 | 0 | 0 |
| AVAXUSDT | premium | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| AVAXUSDT | mark | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 418 | 0 |
| AVAXUSDT | funding | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| AVAXUSDT | agg_trades | OK | 1.0000 | 1 | 19 | 0 | 0 | 0 | 0 | 0 |
| BNBUSDT | klines | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 898 | 0 |
| BNBUSDT | open_interest | DEGRADED | 1.0000 | 1 | 3 | 0 | 2014 | 24 | 4856 | 0 |
| BNBUSDT | order_book_depth | OK | 0.9692 | 28882 | 57742 | 0 | 0 | 0 | 0 | 0 |
| BNBUSDT | premium | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| BNBUSDT | mark | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 889 | 0 |
| BNBUSDT | funding | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| BNBUSDT | agg_trades | OK | 1.0000 | 1 | 19 | 0 | 0 | 0 | 0 | 0 |
| BTCUSDT | klines | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 809 | 0 |
| BTCUSDT | open_interest | DEGRADED | 1.0000 | 1 | 3 | 0 | 2034 | 25 | 4791 | 0 |
| BTCUSDT | order_book_depth | OK | 0.9692 | 28882 | 57743 | 0 | 0 | 0 | 0 | 0 |
| BTCUSDT | premium | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| BTCUSDT | mark | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 780 | 0 |
| BTCUSDT | funding | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| BTCUSDT | agg_trades | OK | 1.0000 | 1 | 19 | 0 | 0 | 0 | 0 | 0 |
| DOGEUSDT | klines | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 665 | 0 |
| DOGEUSDT | open_interest | DEGRADED | 1.0000 | 1 | 3 | 0 | 2028 | 40 | 3961 | 0 |
| DOGEUSDT | order_book_depth | OK | 0.9690 | 28873 | 58061 | 0 | 0 | 0 | 0 | 0 |
| DOGEUSDT | premium | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| DOGEUSDT | mark | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 620 | 0 |
| DOGEUSDT | funding | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| DOGEUSDT | agg_trades | OK | 1.0000 | 1 | 19 | 0 | 0 | 0 | 0 | 0 |
| ETHUSDT | klines | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 822 | 0 |
| ETHUSDT | open_interest | DEGRADED | 1.0000 | 1 | 3 | 0 | 2054 | 24 | 4674 | 0 |
| ETHUSDT | order_book_depth | OK | 0.9692 | 28882 | 57743 | 0 | 0 | 0 | 0 | 0 |
| ETHUSDT | premium | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ETHUSDT | mark | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 801 | 0 |
| ETHUSDT | funding | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ETHUSDT | agg_trades | OK | 1.0000 | 1 | 19 | 0 | 0 | 0 | 0 | 0 |
| LINKUSDT | klines | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 528 | 0 |
| LINKUSDT | open_interest | DEGRADED | 1.0000 | 1 | 3 | 0 | 2064 | 45 | 3099 | 0 |
| LINKUSDT | order_book_depth | OK | 0.9692 | 28882 | 57743 | 0 | 0 | 0 | 0 | 0 |
| LINKUSDT | premium | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| LINKUSDT | mark | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 429 | 0 |
| LINKUSDT | funding | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| LINKUSDT | agg_trades | OK | 1.0000 | 1 | 19 | 0 | 0 | 0 | 0 | 0 |
| LTCUSDT | klines | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 777 | 0 |
| LTCUSDT | open_interest | DEGRADED | 1.0000 | 1 | 3 | 0 | 2049 | 25 | 3887 | 0 |
| LTCUSDT | order_book_depth | OK | 0.9692 | 28882 | 57742 | 0 | 0 | 0 | 0 | 0 |
| LTCUSDT | premium | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| LTCUSDT | mark | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 763 | 0 |
| LTCUSDT | funding | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| LTCUSDT | agg_trades | OK | 1.0000 | 2 | 20 | 0 | 0 | 0 | 0 | 0 |
| SOLUSDT | klines | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 685 | 0 |
| SOLUSDT | open_interest | DEGRADED | 1.0000 | 1 | 3 | 0 | 2054 | 24 | 3415 | 0 |
| SOLUSDT | order_book_depth | OK | 0.9675 | 28849 | 60564 | 0 | 0 | 0 | 0 | 1 |
| SOLUSDT | premium | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SOLUSDT | mark | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 644 | 0 |
| SOLUSDT | funding | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SOLUSDT | agg_trades | OK | 1.0000 | 1 | 19 | 0 | 0 | 0 | 0 | 0 |
| XRPUSDT | klines | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 1224 | 0 |
| XRPUSDT | open_interest | DEGRADED | 1.0000 | 1 | 3 | 0 | 2071 | 25 | 3298 | 0 |
| XRPUSDT | order_book_depth | OK | 0.9675 | 28849 | 60566 | 0 | 0 | 0 | 0 | 1 |
| XRPUSDT | premium | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| XRPUSDT | mark | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 1174 | 0 |
| XRPUSDT | funding | OK | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| XRPUSDT | agg_trades | OK | 1.0000 | 1 | 19 | 0 | 0 | 0 | 0 | 0 |

## aggTrades vs kline taker-buy volumen (ista minuta)

| instrument | uspoređeno minuta | medijan relativne razlike | udio unutar 1 % |
|---|---|---|---|
| ADAUSDT | 830861 | 0.000000 | 0.9950 |
| AVAXUSDT | 830861 | 0.000000 | 0.9968 |
| BNBUSDT | 830861 | 0.000000 | 0.9963 |
| BTCUSDT | 830861 | 0.000000 | 0.9927 |
| DOGEUSDT | 830861 | 0.000000 | 0.9948 |
| ETHUSDT | 830861 | 0.000000 | 0.9925 |
| LINKUSDT | 830861 | 0.000000 | 0.9966 |
| LTCUSDT | 830860 | 0.000000 | 0.9977 |
| SOLUSDT | 830861 | 0.000000 | 0.9923 |
| XRPUSDT | 830861 | 0.000000 | 0.9928 |

## Mapiranje simbola

Nema redova tuđeg simbola u datotekama.

## Funding intervali (sati) viđeni u arhivi

- ADAUSDT: [8.0] (1731 namirenja)
- AVAXUSDT: [8.0] (1731 namirenja)
- BNBUSDT: [8.0] (1731 namirenja)
- BTCUSDT: [8.0] (1731 namirenja)
- DOGEUSDT: [8.0] (1731 namirenja)
- ETHUSDT: [8.0] (1731 namirenja)
- LINKUSDT: [8.0] (1731 namirenja)
- LTCUSDT: [8.0] (1731 namirenja)
- SOLUSDT: [8.0] (1731 namirenja)
- XRPUSDT: [8.0] (1731 namirenja)
