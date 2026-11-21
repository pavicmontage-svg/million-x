"""TITAN command line.

  python -m titan.cli init          create tables, default paper portfolios, research watchlist
  python -m titan.cli pair          print a one-time pairing code for the Android app (15 min)
  python -m titan.cli genkey        generate a master encryption key
  python -m titan.cli api           run the API (uvicorn)
  python -m titan.cli engine        run the copy/execution engine
  python -m titan.cli verify-audit  verify the audit hash chain
  python -m titan.cli binance-check probe Binance demo connectivity from this server
"""
from __future__ import annotations

import json
import sys


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    cmd = argv[0] if argv else "help"
    if cmd == "genkey":
        from .security import generate_master_key
        print(generate_master_key())
        return 0
    from .db import init_db, session_scope
    if cmd == "init":
        init_db()
        from .discovery.service import seed_watchlist
        from .services.venues import ensure_default_portfolios
        with session_scope() as s:
            ps = ensure_default_portfolios(s)
            n = seed_watchlist(s)
        print(f"ok: {len(ps)} portfolios, {n} watchlist traders added")
    elif cmd == "pair":
        init_db()
        from .security import create_pairing_code
        with session_scope() as s:
            print(create_pairing_code(s))
    elif cmd == "api":
        import uvicorn
        uvicorn.run("titan.api.app:app", host="0.0.0.0", port=8080, proxy_headers=True, forwarded_allow_ips="*")
    elif cmd == "engine":
        from .runtime import Runtime
        Runtime().run()
    elif cmd == "verify-audit":
        from .audit import verify_chain
        with session_scope() as s:
            print(json.dumps(verify_chain(s)))
    elif cmd == "binance-check":
        from .adapters.base import AdapterUnavailable
        from .adapters.binance.client import BinanceClient
        for product in ("spot", "usdm", "coinm"):
            c = BinanceClient(product, "demo")
            try:
                c.ping()
                print(product, "demo reachable:", c.base, "offset_ms", c.sync_time())
            except AdapterUnavailable as e:
                print(product, "demo NOT reachable:", c.base, "-", e)
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
