"""2.1 live collector: message parsing, 1-second aggregation, quality events, WS framing (no network)."""
import json
import math
import struct

from millionx.micro import collector as C
from millionx.micro import wsclient as W


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _depth(sym, ev, bid=100.0, ask=100.1, bq=1.0, aq=1.0):
    bids = [[str(bid - i * 0.1), str(bq * (10 if i == 3 else 1))] for i in range(20)]
    asks = [[str(ask + i * 0.1), str(aq)] for i in range(20)]
    return json.dumps({"stream": f"{sym.lower()}@depth20@500ms",
                       "data": {"e": "depthUpdate", "E": ev, "T": ev, "s": sym, "b": bids, "a": asks}})


def _trade(sym, ev, aid, p, q, maker):
    return json.dumps({"stream": f"{sym.lower()}@aggTrade",
                       "data": {"e": "aggTrade", "E": ev, "s": sym, "a": aid, "p": str(p), "q": str(q), "m": maker}})


def test_book_features_walls_impact_removal():
    bids = [[100 - i * 0.1, 10.0 if i == 3 else 1.0] for i in range(20)]
    asks = [[100.1 + i * 0.1, 1.0] for i in range(20)]
    f = C.book_features(bids, asks, impact_notional=150)
    assert abs(f["spread_bps"] - 0.1 / 100.05 * 1e4) < 1e-6
    assert f["bid10"] == 19.0 and f["ask10"] == 10.0 and f["imb10"] > 0
    assert f["bid_wall_px"] == bids[3][0] and math.isnan(f["ask_wall_px"])
    assert f["impact_buy_bps"] > 0
    g = C.book_features([[b[0], b[1] / 2] for b in bids], asks, prev=f)
    assert g["removed_bid10"] == 9.5 and g["removed_ask10"] == 0


def test_aggregator_rows_and_quality(tmp_path):
    clk = Clock(1_000_000)
    db = C.open_db(str(tmp_path / "m.db"))
    ag = C.Aggregator(db, now=clk)
    ag.on_message(_depth("BTCUSDT", 999_900))
    ag.on_message(_trade("BTCUSDT", 999_950, 10, 100.0, 2.0, False))   # aggressive buy
    ag.on_message(_trade("BTCUSDT", 999_960, 11, 100.0, 1.0, True))    # aggressive sell
    ag.on_message(_trade("BTCUSDT", 999_970, 11, 100.0, 1.0, True))    # duplicate -> rejected
    ag.on_message(_trade("BTCUSDT", 999_980, 15, 100.0, 1.0, True))    # gap of 3 ids (kept, logged)
    ag.on_message(json.dumps({"stream": "btcusdt@forceOrder", "data": {"e": "forceOrder", "E": 999_990, "o": {
        "s": "BTCUSDT", "S": "SELL", "q": "0.5", "p": "99", "ap": "99.5", "z": "0.5", "X": "FILLED", "T": 999_990}}}))
    ag.on_message(json.dumps({"stream": "ethusdt@aggTrade", "data": {"e": "aggTrade", "E": 1, "s": "BTCUSDT",
                                                                       "a": 1, "p": "1", "q": "1", "m": False}}))
    ag.on_message(_depth("BTCUSDT", 999_800))                           # out of order -> rejected
    ag.on_message("not json")
    assert ag.flush(1000) == 2
    tr = db.execute("SELECT buy_qty, sell_qty, buy_n, sell_n FROM trades_1s").fetchone()
    assert tr == (2.0, 2.0, 1, 2)
    bk = db.execute("SELECT spread_bps, imb10, updates FROM book_1s").fetchone()
    assert bk[0] > 0 and bk[2] == 1
    liq = db.execute("SELECT side, qty, lower_bound FROM liquidations").fetchone()
    assert liq == ("long_liquidated", 0.5, 1)
    kinds = {r[0] for r in db.execute("SELECT kind FROM quality_events")}
    assert {"duplicate_or_out_of_order", "trade_gap", "symbol_mapping", "out_of_order", "invalid_message"} <= kinds


def test_stale_book_is_not_written(tmp_path):
    clk = Clock(1_000_000)
    db = C.open_db(str(tmp_path / "m.db"))
    ag = C.Aggregator(db, now=clk)
    ag.on_message(_depth("BTCUSDT", 999_900))
    clk.t += 10_000
    assert ag.flush(1010) == 0, "a book older than 3 s is missing data, not a current book"


def test_crossed_book_rejected(tmp_path):
    db = C.open_db(str(tmp_path / "m.db"))
    ag = C.Aggregator(db, now=Clock(1_000_000))
    ag.on_message(_depth("BTCUSDT", 999_900, bid=101.0, ask=100.0))
    assert db.execute("SELECT kind FROM quality_events").fetchone()[0] == "invalid_price"


class FakeSock:
    def __init__(self, data):
        self.data, self.sent = data, b""

    def recv(self, n):
        out, self.data = self.data[:n], self.data[n:]
        return out

    def sendall(self, b):
        self.sent += b

    def close(self):
        pass


def _server_frame(op, payload, fin=True):
    n = len(payload)
    h = bytes([(0x80 if fin else 0) | op])
    h += bytes([n]) if n < 126 else bytes([126]) + struct.pack("!H", n)
    return h + payload


def test_ws_frames_ping_fragments_close():
    big = b"x" * 300
    data = _server_frame(W.OP_PING, b"hi") + _server_frame(W.OP_TEXT, b"hel", fin=False) + \
        _server_frame(W.OP_CONT, b"lo") + _server_frame(W.OP_TEXT, big) + _server_frame(W.OP_CLOSE, b"\x03\xe8")
    ws = W.WebSocket(FakeSock(data))
    assert ws.recv() == "hello"
    assert ws.sock.sent[0] == 0x80 | W.OP_PONG, "ping answered with a masked pong"
    assert ws.recv() == "x" * 300
    try:
        ws.recv()
        raise AssertionError("close must raise")
    except W.WSClosed:
        pass
    f = W.encode_frame(W.OP_TEXT, b"abc", mask_key=b"\x01\x02\x03\x04")
    assert f[1] & 0x80 and bytes(b ^ k for b, k in zip(f[6:], b"\x01\x02\x03\x04")) == b"abc"


def test_streams_for():
    s = C.streams_for(["BTCUSDT"])
    assert s == "btcusdt@depth20@500ms/btcusdt@aggTrade/btcusdt@forceOrder/btcusdt@markPrice@1s"
