"""Historical data download (mocked HTTP) and loading from monthly + daily archives."""
import io
import zipfile
from datetime import date

import pytest

from millionx.backtest import data as D

HEADER = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"


def _zip(rows, header=True):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("k.csv", (HEADER if header else "") + "".join(
            f"{t},{p},{p + 1},{p - 1},{p},10,{t + 59999},100,5,4,40,0\n" for t, p in rows))
    return buf.getvalue()


class Resp:
    def __init__(self, code, content=b""):
        self.status_code, self.content = code, content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_ensure_data_monthly_then_daily_and_load(tmp_path, monkeypatch):
    t_oct = 1_727_740_800_000  # 2024-10-01
    t_nov1 = 1_730_419_200_000  # 2024-11-01
    urls = []

    class Sess:
        def get(self, url, timeout=60):
            urls.append(url)
            if url.endswith("BTCUSDT-1m-2024-10.zip"):
                return Resp(200, _zip([(t_oct, 100.0), (t_oct + 60_000, 101.0)]))
            if url.endswith("BTCUSDT-1m-2024-11-01.zip"):
                return Resp(200, _zip([(t_nov1, 102.0)], header=False))
            if url.endswith("BTCUSDT-1m-2024-11-02.zip"):
                return Resp(200, _zip([(t_nov1 + 86_400_000, 103.0)]))
            return Resp(404)

    import requests
    monkeypatch.setattr(requests, "Session", Sess)
    notes = []
    got = D.ensure_data("BTCUSDT", str(tmp_path), date(2024, 10, 1), date(2024, 11, 30), progress=notes.append,
                        today=date(2024, 11, 3))
    assert got == {"monthly": 1, "daily": 2, "missing": 0}
    assert not any("2024-11-03" in u for u in urls), "today's file is incomplete and never requested"
    assert notes and "downloading BTCUSDT 2024-10" in notes[0]
    b = D.load_symbol("BTCUSDT", str(tmp_path), date(2024, 10, 1), date(2024, 11, 30))
    assert list(b.close) == [100.0, 101.0, 102.0, 103.0]
    assert b.open_time[0] == t_oct and b.taker_buy_volume[0] == 4 and b.quote_volume[0] == 100
    # second run uses the cached files
    urls.clear()
    D.ensure_data("BTCUSDT", str(tmp_path), date(2024, 10, 1), date(2024, 11, 30), today=date(2024, 11, 3))
    assert all("2024-10.zip" not in u for u in urls)


def test_load_symbol_without_data_explains(tmp_path):
    with pytest.raises(FileNotFoundError, match="no historical data"):
        D.load_symbol("ETHUSDT", str(tmp_path), date(2024, 1, 1), date(2024, 1, 31))
