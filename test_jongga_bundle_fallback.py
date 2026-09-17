"""v5.266 — 번들에 없는 종가베팅 후보를 직접 조회로 채운다.

[근본 원인] `_fetch_market_data_inner()`는 **fetch 이전에** 시총 1000억 필터로
유니버스를 자른다. 종가베팅 후보 기준은 **거래대금 상위 100**이라 시총 조건이
없으므로, 거래대금은 터졌지만 시총이 작은 종목(이노메트리 302430.KQ)은
**후보로는 뽑히지만 `bundle["data"]`엔 없다** → `kr_data.get(t)`가 None →
백필이 영원히 스킵됐다(09-09 레코드가 8일 넘게 close_price=null).

[이전 재현이 왜 다 통과했나] 로컬 재현에서 `kr_data`를 **내가 직접 만들어 넣어**
필터 경로를 재현하지 않았다. 그래서 이 테스트는 **프로덕션과 같은 방식으로
번들을 구성**한 뒤(시총 허용목록으로 유니버스를 자른 뒤) 검증한다.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402


def bars(dates, closes, opens=None):
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in dates])
    return pd.DataFrame({"Close": closes, "Open": opens or closes,
                         "High": closes, "Low": closes,
                         "Volume": [100] * len(closes)}, index=idx)


BIG, SMALL = "005930.KS", "302430.KQ"      # 시총 큰 종목 / 시총 필터에 걸리는 후보


@pytest.fixture(autouse=True)
def clear_cache():
    app._JONGGA_BAR_CACHE.clear()
    yield
    app._JONGGA_BAR_CACHE.clear()


@pytest.fixture
def store(monkeypatch):
    state = {}
    monkeypatch.setattr(app, "_load_jongga_forward", lambda: state.get("d", {}))
    monkeypatch.setattr(app, "_save_jongga_forward",
                        lambda d: state.__setitem__("d", d))
    return state


def _rec(**kw):
    base = {"ticker": "X", "name": "X", "snapshot_price": 8000.0,
            "snapshot_source": "eod_fallback", "close_price": None,
            "eod_recorded": False, "next_open_price": None, "next_open_date": None,
            "resolved": False, "gap_snapshot_pct": None, "gap_close_pct": None}
    base.update(kw)
    return base


def _production_bundle(monkeypatch, universe: dict, mcap_allowed: set):
    """**프로덕션과 같은 순서로** 번들을 만든다 — 시총 필터가 fetch 이전에 걸린다.
    (`_fetch_market_data_inner`의 해당 구간을 그대로 옮긴 것)"""
    import naver_kr
    filtered = {t: n for t, n in universe.items()
                if not naver_kr.is_kr(t) or t in mcap_allowed}
    return {t: bars(["2026-09-08", "2026-09-09", "2026-09-10"],
                    [7770.0, 8810.0, 8600.0]) for t in filtered}


def test_mcap_filtered_ticker_is_absent_from_bundle(monkeypatch):
    """전제 확인 — 시총 미달 후보는 번들에 **없다**(이게 버그의 출발점)."""
    data = _production_bundle(monkeypatch, {BIG: "삼성전자", SMALL: "이노메트리"},
                              mcap_allowed={BIG})
    assert BIG in data and SMALL not in data


def test_backfill_uses_direct_fetch_when_absent_from_bundle(store, monkeypatch):
    """핵심 회귀: 번들에 없어도 직접 조회로 채워야 한다."""
    calls = []

    def fake_fetch(ticker, *a, **k):
        calls.append(ticker)
        return bars(["2026-09-08", "2026-09-09", "2026-09-10"], [7770.0, 8810.0, 8600.0])

    monkeypatch.setattr(app.naver_kr, "fetch_history", fake_fetch)
    store["d"] = {"2026-09-09": {SMALL: _rec(ticker=SMALL, name="이노메트리")}}
    kr_data = _production_bundle(monkeypatch, {BIG: "삼성", SMALL: "이노메트리"},
                                 mcap_allowed={BIG})

    app._record_jongga_eod("2026-09-17", kr_data)

    r = store["d"]["2026-09-09"][SMALL]
    assert r["close_price"] == 8810.0, "번들에 없다고 그냥 넘어갔다"
    assert r["eod_recorded"] is True
    assert calls == [SMALL], f"직접 조회가 안 됐거나 과하게 불렸다: {calls}"


def test_bundle_hit_does_not_fetch(store, monkeypatch):
    """번들에 있으면 네트워크를 타면 안 된다."""
    def boom(*a, **k):
        raise AssertionError("번들에 있는데 직접 조회했다")

    monkeypatch.setattr(app.naver_kr, "fetch_history", boom)
    store["d"] = {"2026-09-09": {BIG: _rec(ticker=BIG)}}
    kr_data = _production_bundle(monkeypatch, {BIG: "삼성"}, mcap_allowed={BIG})
    app._record_jongga_eod("2026-09-17", kr_data)
    assert store["d"]["2026-09-09"][BIG]["close_price"] == 8810.0


def test_direct_fetch_goes_through_invalid_bar_filter(store, monkeypatch):
    """직접 조회분도 번들과 같은 후처리(_downcast = _filter_invalid_bars + float32)를
    거쳐야 같은 종가가 나온다."""
    dirty = bars(["2026-09-08", "2026-09-09"], [7770.0, 8810.0])
    dirty.loc[dirty.index[0], ["Open", "High", "Low", "Close"]] = 0.0   # 무효 봉
    monkeypatch.setattr(app.naver_kr, "fetch_history", lambda *a, **k: dirty)
    store["d"] = {"2026-09-09": {SMALL: _rec(ticker=SMALL)}}
    app._record_jongga_eod("2026-09-17", {})
    assert store["d"]["2026-09-09"][SMALL]["close_price"] == 8810.0
    df, src = app._jongga_bars({}, SMALL, [5])
    assert src == "direct"
    assert str(df["Close"].dtype) == "float32", "번들과 dtype이 다르다"


def test_direct_fetch_has_a_cap(store, monkeypatch):
    n = [0]

    def fake(ticker, *a, **k):
        n[0] += 1
        return bars(["2026-09-09"], [10.0])

    monkeypatch.setattr(app.naver_kr, "fetch_history", fake)
    monkeypatch.setattr(app, "_JONGGA_DIRECT_FETCH_MAX", 3)
    store["d"] = {"2026-09-09": {f"{i:06d}.KQ": _rec(ticker=f"{i:06d}.KQ")
                                 for i in range(10)}}
    app._record_jongga_eod("2026-09-17", {})
    assert n[0] == 3, f"상한이 안 걸린다({n[0]}회)"


def test_fetched_bars_are_cached_within_ttl(monkeypatch):
    n = [0]

    def fake(ticker, *a, **k):
        n[0] += 1
        return bars(["2026-09-09"], [10.0])

    monkeypatch.setattr(app.naver_kr, "fetch_history", fake)
    for _ in range(3):
        app._jongga_bars({}, SMALL, [9])
    assert n[0] == 1, f"TTL 안에서 {n[0]}번 받았다"


def test_resolve_gaps_also_falls_back(store, monkeypatch):
    """시가 확정도 같은 문제를 겪었다 — 여기도 폴백이 있어야 한다."""
    monkeypatch.setattr(app.naver_kr, "fetch_history",
                        lambda *a, **k: bars(["2026-09-09", "2026-09-10"],
                                             [8810.0, 8600.0], opens=[8700.0, 8800.0]))
    store["d"] = {"2026-09-09": {SMALL: _rec(ticker=SMALL, close_price=8810.0,
                                             eod_recorded=True)}}
    app._resolve_jongga_gaps({})
    r = store["d"]["2026-09-09"][SMALL]
    assert r["next_open_price"] == 8800.0 and r["resolved"] is True


def test_summary_log_is_emitted_even_when_nothing_filled(store, monkeypatch, capsys):
    """09-17처럼 0건일 때 **침묵하면 '미호출'로 오판**한다."""
    monkeypatch.setattr(app.naver_kr, "fetch_history", lambda *a, **k: None)
    store["d"] = {"2026-09-09": {SMALL: _rec(ticker=SMALL)}}
    app._record_jongga_eod("2026-09-17", {})
    out = capsys.readouterr().out
    assert "EOD 종가 기록 0건" in out, out
    assert "미기록 1건" in out and "실패 1" in out, out


def test_summary_log_reports_sources(store, monkeypatch, capsys):
    monkeypatch.setattr(app.naver_kr, "fetch_history",
                        lambda *a, **k: bars(["2026-09-09"], [8810.0]))
    store["d"] = {"2026-09-09": {BIG: _rec(ticker=BIG), SMALL: _rec(ticker=SMALL)}}
    kr_data = _production_bundle(monkeypatch, {BIG: "삼성", SMALL: "이노"},
                                 mcap_allowed={BIG})
    app._record_jongga_eod("2026-09-17", kr_data)
    out = capsys.readouterr().out
    assert "번들 1" in out and "직접조회 1" in out, out


def test_memory_endpoint_skips_object_walk_by_default():
    import asyncio
    r = asyncio.run(app.debug_memory())
    assert "skipped" in r["live_dataframes"]
    r2 = asyncio.run(app.debug_memory(objects=1))
    assert "count" in r2["live_dataframes"]
