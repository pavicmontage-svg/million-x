"""Command line.

  python -m quanttitan serve [--host 127.0.0.1] [--port 8765] [--data data] [--replay BTCUSDT,ETHUSDT]
  python -m quanttitan create-admin USERNAME          (password read from stdin / prompt)
  python -m quanttitan download BTCUSDT,ETHUSDT 2026-07-01 2026-10-31
  python -m quanttitan backtest BTCUSDT,ETHUSDT [--json out.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import sys


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="quanttitan")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default=os.environ.get("QT_HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.environ.get("QT_PORT", 8765)))
    s.add_argument("--data", default=os.environ.get("QT_DATA_DIR", "data"))
    s.add_argument("--replay", default="")
    s.add_argument("--tf", default="3m", help="timeframe of the replay history")
    a = sub.add_parser("create-admin")
    a.add_argument("username")
    a.add_argument("--data", default=os.environ.get("QT_DATA_DIR", "data"))
    d = sub.add_parser("download")
    d.add_argument("symbols"); d.add_argument("start"); d.add_argument("end")
    d.add_argument("--data", default=os.environ.get("QT_DATA_DIR", "data"))
    d.add_argument("--tf", default="3m")
    b = sub.add_parser("backtest")
    b.add_argument("symbols")
    b.add_argument("--data", default=os.environ.get("QT_DATA_DIR", "data"))
    b.add_argument("--json", default="")
    b.add_argument("--tf", default="3m")
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn
        from .server import build
        app, *_ = build(args.data, replay_symbols=[x for x in args.replay.split(",") if x], replay_tf=args.tf)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info", ws="wsproto", proxy_headers=False)
    elif args.cmd == "create-admin":
        from .security.auth import Auth
        from .storage.db import Database
        pw = sys.stdin.readline().strip() if not sys.stdin.isatty() else getpass.getpass("password (≥10 chars): ")
        Auth(Database(f"sqlite:///{os.path.join(args.data, 'quanttitan.db')}")).create_user(args.username, pw)
        print("administrator created")
    elif args.cmd == "download":
        from .data.history import HistoryStore
        hs = HistoryStore(os.path.join(args.data, "history"))
        for sym in args.symbols.split(","):
            c = hs.download(sym, args.tf, dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end))
            print(sym, len(c), "bars")
    elif args.cmd == "backtest":
        from .backtest.engine import Backtester
        from .config import AppConfig, rescale_timeframe
        from .data.history import HistoryStore
        hs = HistoryStore(os.path.join(args.data, "history"))
        syms = args.symbols.split(",")
        data = {x: hs.load(x, args.tf) for x in syms}
        r = Backtester(rescale_timeframe(AppConfig(), args.tf), {k: v for k, v in data.items() if v is not None},
                       funding={x: hs.load_funding(x) for x in syms if hs.load_funding(x) is not None}).run()
        print(json.dumps(r.summary(), indent=1))
        if args.json:
            with open(args.json, "w") as fh:
                json.dump(r.to_dict(), fh, default=str)


if __name__ == "__main__":
    main()
