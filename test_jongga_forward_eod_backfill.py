"""v5.258 종가베팅 포워드 3중 결함 수정.

[결함 A] _warm_market EOD 분기에서 _record_jongga_eod()가 **폴백 스냅샷보다
  먼저** 호출됐다. 14:40 창을 놓친 날은 그 시점에 레코드가 없어 즉시 return →
  방금 만든 레코드의 close_price가 그날 안 채워짐.
[결함 B] _record_jongga_eod()가 그날 하나만 봐서, 한 번 놓치면 영영 안 채워짐
  (EOD 분기는 _warmed 가드로 하루 1회).
[결함 C] _resolve_jongga_gaps()가 close_price=None인 채 resolved=True를 찍고,
  이후 재방문하지 않아 gap_close_pct가 영영 null → close 기준 표본 0건.

실사고: 2026-09-09 이노메트리 eod_recorded=false / close_price=null.
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
                         "Volume": [1] * len(closes)}, index=idx)


@pytest.fixture
def store(monkeypatch, tmp_path):
    """_load/_save_jongga_forward를 임시 파일로 격리."""
    state = {}

    def _load():
        return state.get("d", {})

    def _save(d):
        state["d"] = d

    monkeypatch.setattr(app, "_load_jongga_forward", _load)
    monkeypatch.setattr(app, "_save_jongga_forward", _save)
    return state


def _rec(**kw):
    base = {"ticker": "X", "name": "X", "snapshot_price": 100.0,
            "snapshot_source": "eod_fallback", "close_price": None,
            "eod_recorded": False, "next_open_price": None, "next_open_date": None,
            "resolved": False, "gap_snapshot_pct": None, "gap_close_pct": None}
    base.update(kw)
    return base


# ── _close_on_date: 날짜를 정확히 맞추는가 ────────────────────────────
def test_close_on_date_exact_match():
    df = bars(["2026-09-08", "2026-09-09", "2026-09-10"], [100.0, 122.5, 112.8])
    assert app._close_on_date(df, "2026-09-09") == 122.5
    assert app._close_on_date(df, "2026-09-10") == 112.8


def test_close_on_date_missing_returns_none_not_last_bar():
    """핵심 — 그 날짜 봉이 없으면 **다른 날 종가를 쓰지 않는다**."""
    df = bars(["2026-09-08", "2026-09-10"], [100.0, 112.8])
    assert app._close_on_date(df, "2026-09-09") is None      # 휴장/누락
    assert app._close_on_date(df, "2026-09-01") is None      # 데이터 시작 이전
    assert app._close_on_date(df, "2026-09-30") is None      # 아직 오지 않은 날


def test_close_on_date_handles_empty_and_none():
    assert app._close_on_date(None, "2026-09-09") is None
    assert app._close_on_date(pd.DataFrame(), "2026-09-09") is None


# ── 결함 B: 백필 ──────────────────────────────────────────────────────
def test_backfills_past_dates(store):
    store["d"] = {
        "2026-09-09": {"457190.KQ": _rec(ticker="457190.KQ", name="이노메트리")},
        "2026-09-11": {"131290.KQ": _rec(ticker="131290.KQ", name="티에스이")},
    }
    kr = {
        "457190.KQ": bars(["2026-09-09", "2026-09-10", "2026-09-11"], [122.5, 110.0, 108.0]),
        "131290.KQ": bars(["2026-09-10", "2026-09-11"], [50.0, 55.5]),
    }
    app._record_jongga_eod("2026-09-11", kr)
    d = store["d"]
    assert d["2026-09-09"]["457190.KQ"]["close_price"] == 122.5, "과거 날짜가 백필 안 됨"
    assert d["2026-09-09"]["457190.KQ"]["eod_recorded"] is True
    assert d["2026-09-11"]["131290.KQ"]["close_price"] == 55.5


def test_backfill_does_not_use_wrong_day_close(store):
    """09-09 봉이 없는데 마지막 봉(09-11)을 쓰면 오염. None 유지여야 한다."""
    store["d"] = {"2026-09-09": {"X": _rec()}}
    app._record_jongga_eod("2026-09-11", {"X": bars(["2026-09-10", "2026-09-11"], [110.0, 108.0])})
    r = store["d"]["2026-09-09"]["X"]
    assert r["close_price"] is None and r["eod_recorded"] is False


def test_already_recorded_is_not_overwritten(store):
    store["d"] = {"2026-09-09": {"X": _rec(close_price=122.5, eod_recorded=True)}}
    app._record_jongga_eod("2026-09-11", {"X": bars(["2026-09-09"], [999.0])})
    assert store["d"]["2026-09-09"]["X"]["close_price"] == 122.5


# ── 결함 C: resolved 재방문 ───────────────────────────────────────────
def test_resolved_without_close_gets_gap_backfilled(store):
    """resolved=True인데 close 기준이 비어 있고 close_price가 채워졌으면 재계산."""
    store["d"] = {"2026-09-09": {"X": _rec(
        close_price=122.5, eod_recorded=True, resolved=True,
        next_open_price=112.8, next_open_date="2026-09-10",
        gap_snapshot_pct=12.5, gap_close_pct=None)}}
    app._resolve_jongga_gaps({})          # kr_data 없어도 저장값만으로 계산 가능해야 한다
    r = store["d"]["2026-09-09"]["X"]
    expected = round((112.8 / 122.5 - 1 - app.JONGGA_FORWARD_COST) * 100, 2)
    assert r["gap_close_pct"] == expected


def test_resolved_revisit_uses_stored_next_open_not_latest_bar(store):
    """재방문 시 현재 마지막 봉으로 시가를 다시 잡으면 훨씬 뒤 날짜가 섞인다."""
    store["d"] = {"2026-09-09": {"X": _rec(
        close_price=100.0, eod_recorded=True, resolved=True,
        next_open_price=90.0, next_open_date="2026-09-10", gap_close_pct=None)}}
    app._resolve_jongga_gaps({"X": bars(["2026-09-11"], [500.0], opens=[500.0])})
    r = store["d"]["2026-09-09"]["X"]
    assert r["next_open_price"] == 90.0, "저장된 시가가 덮어써졌다"
    assert r["gap_close_pct"] == round((90.0 / 100.0 - 1 - app.JONGGA_FORWARD_COST) * 100, 2)


def test_resolved_with_gap_already_set_is_untouched(store):
    store["d"] = {"2026-09-09": {"X": _rec(
        close_price=100.0, resolved=True, next_open_price=90.0, gap_close_pct=-10.3)}}
    app._resolve_jongga_gaps({})
    assert store["d"]["2026-09-09"]["X"]["gap_close_pct"] == -10.3


def test_resolved_without_close_price_stays_none(store):
    """close_price가 여전히 없으면 억지로 만들지 않는다."""
    store["d"] = {"2026-09-09": {"X": _rec(resolved=True, next_open_price=90.0)}}
    app._resolve_jongga_gaps({})
    assert store["d"]["2026-09-09"]["X"]["gap_close_pct"] is None


# ── 결함 A: 순서 (전체 시나리오) ──────────────────────────────────────
def test_missed_window_then_fallback_then_eod_fills_close(store):
    """14:40 창 누락 → 폴백 스냅샷 → EOD 기록 순서로 close_price가 채워진다."""
    daykey = "2026-09-09"
    kr = {"457190.KQ": bars(["2026-09-08", daykey], [100.0, 122.5])}
    # ① 기존 순서대로 EOD 기록이 먼저 (그날 레코드 없음)
    app._record_jongga_eod(daykey, kr)
    assert store.get("d") in (None, {}), "레코드가 없는데 뭔가 썼다"
    # ② 폴백 스냅샷이 레코드를 만든다
    app._record_jongga_snapshot(daykey, [{"ticker": "457190.KQ", "name": "이노메트리",
                                          "close": 122.5}], source="eod_fallback")
    assert store["d"][daykey]["457190.KQ"]["close_price"] is None
    # ③ v5.258이 추가한 재호출
    app._record_jongga_eod(daykey, kr)
    r = store["d"][daykey]["457190.KQ"]
    assert r["close_price"] == 122.5 and r["eod_recorded"] is True


def test_call_site_reruns_eod_after_fallback_snapshot():
    """호출 순서 회귀 방지 — 폴백 스냅샷 뒤에 _record_jongga_eod 재호출이 있어야."""
    src = Path(app.__file__).read_text(encoding="utf-8")
    i = src.index('_record_jongga_snapshot(daykey, fb_result["hits"], source="eod_fallback")')
    after = src[i:i + 800]
    assert "_record_jongga_eod(daykey" in after, "폴백 뒤 EOD 재호출이 없다"


def test_inometry_0909_recovery_path(store):
    """사용자 보고 케이스 재현: eod_recorded=false/close_price=null + resolved.
    백필 → 갭 보정 순서로 close 기준이 복구되는가."""
    store["d"] = {"2026-09-09": {"457190.KQ": _rec(
        ticker="457190.KQ", name="이노메트리", snapshot_price=118.0,
        resolved=True, next_open_price=112.8, next_open_date="2026-09-10",
        gap_snapshot_pct=-4.7)}}
    kr = {"457190.KQ": bars(["2026-09-09", "2026-09-10", "2026-09-11"],
                            [122.5, 110.0, 108.0], opens=[120.0, 112.8, 109.0])}
    app._record_jongga_eod("2026-09-11", kr)
    app._resolve_jongga_gaps(kr)
    r = store["d"]["2026-09-09"]["457190.KQ"]
    assert r["close_price"] == 122.5 and r["eod_recorded"] is True
    assert r["gap_close_pct"] == round((112.8 / 122.5 - 1 - app.JONGGA_FORWARD_COST) * 100, 2)
