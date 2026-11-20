# CONFIGURATION PARAMETERS (1.9.0)

Svi limiti rizika mijenjaju se u aplikaciji (Risk → Limits ili profil); server ih provjerava
prema tvrdim granicama (`governance.BOUNDS`, za PAPER/DEMO `SIM_BOUNDS`).

## Runtime (Admin)
| Parametar | Vrijednosti | Zadano |
|---|---|---|
| mode | paper / demo / live | paper |
| execution | manual / auto | manual |
| capital_mode | pro (500 EUR) / multi (40) / experimental (4) | pro |
| timeframe | 0 (bot default), 5, 15, 30, 60, 240 min | 0 |
| brain_interval_min | ≥ 15 | 60 (bez AI-ja pravila svakih 10 min) |

## Rizik (profil Standard = specifikacija vlasnika)
| Parametar | Zadano | Granica LIVE | Granica PAPER/DEMO |
|---|---|---|---|
| risk_per_trade | 0.005 | 0.0005–0.02 | 0.0005–0.05 |
| max_daily_loss (udio) | 0.02 | 0.005–0.10 | 0.005–0.50 |
| max_daily_loss_eur | 10 | 0–1e6 | 0–1e6 |
| max_total_drawdown | 0.20 | 0.02–0.35 | 0.02–0.90 |
| max_open_positions | 2 | 1–10 | 1–10 |
| max_leverage | 20 | 1–20 | 1–50 |
| leverage_tiers | 5,10,15,20 | uzlazno, ≤ max | uzlazno, ≤ max |
| min_liq_buffer | 3.0 | 2–20 | 2–20 |
| max_consecutive_losses / cooldown_minutes | 4 / 60 | 1–20 / 5–1440 | 1–50 / 5–1440 |
| max_total_exposure | 3.0 | 0.1–5 | 0.1–50 |
| max_fee_to_risk | 0.35 | 0.05–0.5 | isto |
| min_reward_risk | 1.2 | 1–5 | isto |
| max_spread_bps / min_depth_usdt | 3 / 50 000 | 0.5–20 / ≥ 1 000 | isto |
| max_price_drift_bps / max_slippage_bps | 10 / 15 | 1–50 | isto |
| max_orders_per_minute / hour | 3 / 30 | 1–10 / 1–120 | isto |
| target_fee_share (minimalni stop) | 0.25 → stop ≥ 0,42 % | — | — |

## AI desk (AI Command Center)
| Parametar | Zadano |
|---|---|
| trader_enabled / supervisor_enabled | true / true (aktivni samo kad je barem jedan provider konfiguriran) |
| trader_interval_min | 5 |
| trader_min_confidence | 0.6 |
| trader_max_trades_per_run | 2 |
| provider: enabled, model (prazno = auto), timeout_s | true, auto, 45 |

## Okruženje (VPS, `.env`)
`MX_MODE`, `MX_EXECUTION`, `MX_CAPITAL_MODE`, `MX_MASTER_KEY` ili `MX_MASTER_KEY_FILE`, `MX_DB_PATH`, `MX_DATA_DIR`,
`MX_API_HOST`, `MX_API_PORT`, `MX_DOMAIN`, `MX_BOOTSTRAP_CODE`, `MX_VOLUME_URL` (rangiranje po stvarnom volumenu),
`MX_MARKET_DATA_URL`, `MX_LLM_PRICES` (opcionalno, cijene po modelu za izračun troška).
