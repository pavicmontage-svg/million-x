"""Command line tools.

  python -m millionx.cli genkey                      # new MX_MASTER_KEY
  python -m millionx.cli pair [--role owner]         # one-time code for the Android app
  python -m millionx.cli download --symbols BTCUSDT,ETHUSDT --start 2023-01 --end 2025-12
  python -m millionx.cli backtest --symbols ETHUSDT --start 2024-01-01 --end 2024-12-31 --equity 40
  python -m millionx.cli walkforward --symbols ETHUSDT,SOLUSDT --start 2023-01-01 --end 2025-12-31 --bots bot2_ema
  python -m millionx.cli feasibility --price 3000 --min-notional 20
  python -m millionx.cli verify-audit
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date

from .backtest import metrics
from .config import load_settings


def _month(s: str) -> date:
    y, m = s.split("-")[:2]
    return date(int(y), int(m), 1)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="millionx")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("genkey")
    p = sub.add_parser("pair")
    p.add_argument("--role", default="owner", choices=["owner", "viewer"])
    d = sub.add_parser("download")
    d.add_argument("--symbols", required=True)
    d.add_argument("--start", required=True)
    d.add_argument("--end", required=True)
    for name in ("backtest", "walkforward"):
        b = sub.add_parser(name)
        b.add_argument("--symbols", required=True)
        b.add_argument("--start", required=True)
        b.add_argument("--end", required=True)
        b.add_argument("--equity", type=float, default=40.0)
        b.add_argument("--bots", default="")
        b.add_argument("--train-days", type=int, default=60)
        b.add_argument("--test-days", type=int, default=20)
        b.add_argument("--out", default="")
    f = sub.add_parser("feasibility")
    f.add_argument("--price", type=float, required=True)
    f.add_argument("--min-notional", type=float, default=5.0)
    f.add_argument("--stop-pct", type=float, default=0.004)
    f.add_argument("--capital-eur", type=float, default=4.0)
    sub.add_parser("verify-audit")
    bk = sub.add_parser("backup")
    bk.add_argument("--dir", default="")
    sub.add_parser("migrate")
    a = ap.parse_args(argv)
    s = load_settings()

    if a.cmd == "genkey":
        from .security import Vault
        print(Vault.generate())
    elif a.cmd == "pair":
        from .security import create_pairing_code
        from .storage import Store
        code = create_pairing_code(Store(s.db_path), a.role)
        print(f"Pairing code ({a.role}, valid 10 min): {code}")
    elif a.cmd == "download":
        from .backtest.data import download_month, months_between
        import requests
        sess = requests.Session()
        for sym in a.symbols.split(","):
            for y, m in months_between(_month(a.start), _month(a.end)):
                p = download_month(sym, y, m, os.path.join(s.data_dir, sym), sess)
                print(sym, y, m, "ok" if p else "not published")
    elif a.cmd in ("backtest", "walkforward"):
        from .backtest.data import load_symbol
        st, en = date.fromisoformat(a.start), date.fromisoformat(a.end)
        data = {x: load_symbol(x, s.data_dir, st, en) for x in a.symbols.split(",")}
        bots = [x for x in a.bots.split(",") if x] or None
        if a.cmd == "backtest":
            from .backtest.engine import BacktestConfig, run
            res = run(data, s, BacktestConfig(start_equity=a.equity, bots=bots))
            out = res.summary()
        else:
            from .backtest.walkforward import walk_forward
            from .strategies import ALL_IDS
            out = {}
            for b in bots or ALL_IDS:
                wf = walk_forward(data, s, b, a.train_days, a.test_days,
                                  progress=lambda bot, k, w: print(f"{bot} window {k}: OOS trades {w['oos_trades']}, "
                                                                   f"expR {w['oos_expectancy_r']}"))
                out[b] = wf.to_dict()
                print(b, "->", wf.activation["status"])
        text = json.dumps(metrics.json_safe(out), indent=2, default=str)
        if a.out:
            with open(a.out, "w") as fh:
                fh.write(text)
        print(text[:4000])
    elif a.cmd == "feasibility":
        from .risk import SymbolRules, feasibility_report
        r = feasibility_report(a.capital_eur * s.eur_usdt, a.price, a.stop_pct,
                               SymbolRules("X", min_notional=a.min_notional, min_qty=0.0, step_size=1e-6),
                               s.risk, s.fees)
        print(json.dumps(r, indent=2))
    elif a.cmd == "backup":
        from .storage import Store
        st = Store(s.db_path)
        print(st.backup(a.dir or os.path.join(os.path.dirname(s.db_path) or ".", "backups")))
    elif a.cmd == "migrate":
        from .storage import Store
        st = Store(s.db_path)
        print("schema version", st.schema_version(), "integrity", st.integrity_ok())
    elif a.cmd == "verify-audit":
        from .storage import Store
        print(Store(s.db_path).verify_audit())


if __name__ == "__main__":
    main()
