# Limitations (honest)

* **No demonstrated edge.** The baseline study (Jul–Oct 2026) shows no statistically significant edge after costs.
  The platform's value today is the disciplined infrastructure to *search* for edge and to refuse trades without it.
* **Binance access.** From this build environment `fapi.binance.com` returned HTTP 451 (restricted location), so
  live market data and signed endpoints were tested against the **testnet** (public endpoints) and with mocked
  transports (signed endpoints). The MILLION-X notes in this repository state that Binance stopped new orders for
  EEA users from 1 July 2026; live trading from Croatia may therefore be impossible. Testnet and paper work
  regardless; the exchange layer is an adapter so another venue can be added.
* **Signed trading was not executed against a real account here** (no keys in this environment). Order placement,
  algo orders, reconciliation and failure handling are tested against the paper exchange and mocked Binance
  responses. Run a TESTNET session (API Connection → testnet keys → Test connection) before anything else.
* **Order-flow live tier** (book/aggTrades features) cannot be backtested from bar data; it is logged in paper/live
  so it can be validated later. Only the bar tier is backtested.
* **The "AI team"** are deterministic, auditable analytical components, not language models.
* **Agents' adaptive optimisation** is deliberately not automatic: the research lab proposes, a human promotes.
  The planned next step (daily self-review with isolated experiments) builds on the Quant Researcher and the lab;
  it must never change live risk limits or enable unvalidated strategies on its own.
* **Phone runtime** depends on Android battery policy and connectivity.
* **Universe history**: backtests use whatever symbols you pass; survivorship bias is controlled only if you pick
  symbols as of the test start.
