# Data availability probe (2026-11-20T23:42:46Z)

## Daily archives (data.binance.vision), all 10 symbols

| dataset | 2024-01-15 | 2024-03-15 | 2024-06-15 | 2025-01-15 | 2025-06-15 | 2025-12-15 | 2026-03-15 | 2026-06-15 | 2026-11-15 |
|---|---|---|---|---|---|---|---|---|---|
| metrics | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 |
| bookDepth | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 |
| bookTicker | 10/10 | 10/10 | 0/10 | 0/10 | 0/10 | 0/10 | 0/10 | 0/10 | 0/10 |
| aggTrades | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 |
| trades | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 |
| liquidationSnapshot | 0/10 | 0/10 | 0/10 | 0/10 | 0/10 | 0/10 | 0/10 | 0/10 | 0/10 |
| premiumIndexKlines_1m | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 |
| markPriceKlines_1m | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 |
| klines_1m | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 |

## Monthly archives

| dataset | 2024-01 | 2025-01 | 2025-06 | 2026-01 | 2026-06 | 2026-10 |
|---|---|---|---|---|---|---|
| fundingRate | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 |
| aggTrades_monthly | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 |

## File contents (BTCUSDT, newest available sample)

- **metrics**: {"day": "2026-11-15", "status": 200, "zip_bytes": 11753, "header": ["create_time", "symbol", "sum_open_interest", "sum_open_interest_value", "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio", "count_long_short_ratio", "sum_taker_long_short_vol_ratio"], "rows": 288, "first_row": ["2026-11-15 00:25:00", "BTCUSDT", "97815.0480000000000000", "6234799630.0536000000000000", "3.68237600", "1.84025300", "3.08114889", "0.49234300"], "median_step_ms": 3900000}
- **bookDepth**: {"day": "2026-11-15", "status": 200, "zip_bytes": 561631, "header": ["timestamp", "percentage", "depth", "notional"], "rows": 34560, "first_row": ["2026-11-15 00:00:01", "-5.00", "9239.74400000", "580194632.75510000"], "median_step_ms": 30000}
- **bookTicker**: {"day": "2024-03-15", "status": 200, "zip_bytes": 299227972, "header": ["update_id", "best_bid_price", "best_bid_qty", "best_ask_price", "best_ask_qty", "transaction_time", "event_time"], "rows": 26651695, "first_row": ["4183452554160", "71455.50000000", "2.12900000", "71455.60000000", "2.26000000", "1710460800006", "1710460800012"], "median_step_ms": 99}
- **aggTrades**: {"day": "2026-11-15", "status": 200, "zip_bytes": 33441439, "header": ["agg_trade_id", "price", "quantity", "first_trade_id", "last_trade_id", "transact_time", "is_buyer_maker"], "rows": 2790904, "first_row": ["3523878282", "64130.5", "0.005", "8275015668", "8275015668", "1794700800006", "true"], "median_step_ms": 37}
- **trades**: {"day": "2026-11-15", "skipped": "too large to inspect; status/size recorded"}
- **liquidationSnapshot**: {"status": "no sample day available"}
- **premiumIndexKlines_1m**: {"day": "2026-11-15", "status": 200, "zip_bytes": 29251, "header": ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"], "rows": 1440, "first_row": ["1794700800000", "-0.00049629", "-0.00028760", "-0.00054379", "-0.00034433", "0", "1794700859999", "0", "12", "0", "0", "0"], "median_step_ms": 60000}
- **markPriceKlines_1m**: {"day": "2026-11-15", "status": 200, "zip_bytes": 35932, "header": ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"], "rows": 1440, "first_row": ["1794700800000", "64124.10000000", "64125.01326087", "64077.00000000", "64101.70000000", "0", "1794700859999", "0", "60", "0", "0", "0"], "median_step_ms": 60000}
- **klines_1m**: {"day": "2026-11-15", "status": 200, "zip_bytes": 63140, "header": ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"], "rows": 1440, "first_row": ["1794700800000", "64130.50", "64130.60", "64077.00", "64087.60", "234.785", "1794700859999", "15049651.86990", "5734", "78.292", "5018110.23300", "0"], "median_step_ms": 60000}
- **fundingRate**: {"month": "2026-10", "status": 200, "zip_bytes": 943, "header": ["calc_time", "funding_interval_hours", "last_funding_rate"], "rows": 93, "first_row": ["1790812800001", "8", "0.00007981"], "median_step_ms": 28800000}

## REST endpoints (from the CI runner's region)

- **fundingRate**: HTTP 451 — `{
  "code": 0,
  "msg": "Service unavailable from a restricted location according to 'b. Eligibility' in https://www.binance.com/en/terms. Please contact custom`
- **openInterestHist_old**: HTTP 451 — `{
  "code": 0,
  "msg": "Service unavailable from a restricted location according to 'b. Eligibility' in https://www.binance.com/en/terms. Please contact custom`
- **openInterestHist_recent**: HTTP 451 — `{
  "code": 0,
  "msg": "Service unavailable from a restricted location according to 'b. Eligibility' in https://www.binance.com/en/terms. Please contact custom`
- **aggTrades_old**: HTTP 451 — `{
  "code": 0,
  "msg": "Service unavailable from a restricted location according to 'b. Eligibility' in https://www.binance.com/en/terms. Please contact custom`
- **depth**: HTTP 451 — `{
  "code": 0,
  "msg": "Service unavailable from a restricted location according to 'b. Eligibility' in https://www.binance.com/en/terms. Please contact custom`
