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


# ══════════════════════════════════════════════════════════════════════
# v5.259 — P1 메타 / P2·P3 파일 기준 게이트 / P4 source 분리
# ══════════════════════════════════════════════════════════════════════
def test_zero_hits_still_writes_meta(store):
    """P1 핵심: 0건이어도 날짜 키+메타가 남아야 '누락'과 '진짜 0건'이 갈린다."""
    app._record_jongga_snapshot("2026-09-10", [], source="intraday")
    d = store["d"]
    assert "2026-09-10" in d, "0건이라고 날짜 키가 아예 안 생겼다(v5.258 이전 동작)"
    meta = d["2026-09-10"]["_meta"]["intraday"]
    assert meta["n"] == 0 and meta["ran_at"]
    assert list(app._iter_day_records(d["2026-09-10"])) == [], "메타가 티커로 새어나갔다"


def test_meta_records_each_source_separately(store):
    app._record_jongga_snapshot("2026-09-10", [], source="intraday")
    app._record_jongga_snapshot("2026-09-10", [{"ticker": "X.KS", "name": "X", "close": 10}],
                                source="eod_fallback")
    meta = store["d"]["2026-09-10"]["_meta"]
    assert meta["intraday"]["n"] == 0
    assert meta["eod_fallback"]["n"] == 1
    assert [t for t, _ in app._iter_day_records(store["d"]["2026-09-10"])] == ["X.KS"]


def test_meta_keeps_first_run_time_and_counts_runs(store):
    app._record_jongga_snapshot("2026-09-10", [], source="intraday")
    first = store["d"]["2026-09-10"]["_meta"]["intraday"]["first_ran_at"]
    app._record_jongga_snapshot("2026-09-10", [], source="intraday")
    m = store["d"]["2026-09-10"]["_meta"]["intraday"]
    assert m["first_ran_at"] == first and m["runs"] == 2


# ── P2·P3: 폴백 게이트가 파일 기준인가 ────────────────────────────────
def test_fallback_needed_when_nothing_recorded(store):
    store["d"] = {}
    assert app._jongga_needs_eod_fallback("2026-09-10") is True


def test_fallback_still_needed_after_zero_hit_intraday(store):
    """v5.258까지의 치명적 경로 — 0건 장중 스캔이 폴백을 껐다."""
    app._record_jongga_snapshot("2026-09-10", [], source="intraday")
    assert app._jongga_needs_eod_fallback("2026-09-10") is True


def test_fallback_still_needed_after_successful_intraday(store):
    """장중 스캔이 성공해도 확정 종가 기준 보강은 한 번 돈다(설계 합의)."""
    app._record_jongga_snapshot("2026-09-10", [{"ticker": "X.KS", "name": "X", "close": 10}],
                                source="intraday")
    assert app._jongga_needs_eod_fallback("2026-09-10") is True


def test_fallback_not_repeated_once_done(store):
    """P3: 재시작으로 인메모리 플래그가 리셋돼도 두 번 돌지 않는다."""
    app._record_jongga_snapshot("2026-09-10", [], source="eod_fallback")
    app._jongga_snapshot_date = None          # 프로세스 재시작 시뮬
    assert app._jongga_needs_eod_fallback("2026-09-10") is False


def test_legacy_day_without_meta_is_unknown_not_zero(store):
    """v5.259 이전 레코드는 메타가 없다 — '0건이었다'로 단정하면 안 된다."""
    store["d"] = {"2026-09-09": {"457190.KQ": _rec(ticker="457190.KQ")}}
    assert app._day_meta(store["d"], "2026-09-09") == {}
    assert app._jongga_needs_eod_fallback("2026-09-09") is True


def test_call_site_uses_file_based_gate():
    """회귀 방지 — 인메모리 플래그로 되돌아가면 P2가 부활한다."""
    src = Path(app.__file__).read_text(encoding="utf-8")
    # 앵커는 **실제 호출부**여야 한다 — 'source="eod_fallback"'만 찾으면
    # _record_jongga_snapshot의 docstring 첫 등장에 걸린다(실제로 걸렸다).
    i = src.index('_record_jongga_snapshot(daykey, fb_result["hits"], source="eod_fallback")')
    before = src[max(0, i - 2500):i]
    assert "_jongga_needs_eod_fallback(daykey)" in before, "폴백 게이트가 파일 기준이 아니다"
    # 모듈 docstring(= 변경 이력)에는 옛 게이트가 **인용문으로** 남아 있다.
    # 파일 전체를 훑으면 그 인용에 걸린다(실제로 걸렸다 — v5.259 changelog).
    # 실행되는 코드 영역만 본다.
    code = src[src.index('"""', src.index('"""') + 3) + 3:]
    assert "if _jongga_snapshot_date != daykey:" not in code, "인메모리 게이트가 되살아났다"


# ── 병합 정책: 선착순 유지, 폴백은 없는 티커만 추가 ───────────────────
def test_fallback_does_not_overwrite_intraday_record(store):
    app._record_jongga_snapshot("2026-09-10", [{"ticker": "A.KS", "name": "A", "close": 100}],
                                source="intraday")
    app._record_jongga_snapshot("2026-09-10", [
        {"ticker": "A.KS", "name": "A", "close": 130},      # 같은 티커, 확정 종가
        {"ticker": "B.KS", "name": "B", "close": 50},       # 신규
    ], source="eod_fallback")
    day = store["d"]["2026-09-10"]
    assert day["A.KS"]["snapshot_price"] == 100, "14:40 기록이 덮어써졌다"
    assert day["A.KS"]["snapshot_source"] == "intraday"
    assert day["B.KS"]["snapshot_price"] == 50
    assert day["B.KS"]["snapshot_source"] == "eod_fallback"


# ── P4: source별 분리 집계 ────────────────────────────────────────────
def test_stats_split_snapshot_basis_by_source(store, monkeypatch):
    store["d"] = {"2026-09-10": {
        "A.KS": _rec(ticker="A.KS", snapshot_source="intraday", resolved=True,
                     gap_snapshot_pct=2.0, gap_close_pct=1.0),
        "B.KS": _rec(ticker="B.KS", snapshot_source="eod_fallback", resolved=True,
                     gap_snapshot_pct=-4.0, gap_close_pct=-1.0),
        "_meta": {"intraday": {"n": 1}},
    }}
    s = app._jongga_forward_stats()
    assert s["total_resolved"] == 2, "메타가 레코드로 세어졌다"
    assert s["snapshot_basis_by_source"]["intraday"]["n"] == 1
    assert s["snapshot_basis_by_source"]["intraday"]["mean_gap_pct"] == 2.0
    assert s["snapshot_basis_by_source"]["eod_fallback"]["mean_gap_pct"] == -4.0
    assert s["close_basis"]["n"] == 2, "close 기준은 균일하므로 합쳐서 센다"
    assert s["close_basis_source_mix"] == {"intraday": 1, "eod_fallback": 1}


# ── v5.262: days_meta 노출 ─────────────────────────────────────────────
def test_days_meta_exposes_zero_hit_days(store):
    """레코드 0건인 날짜도 응답에 보여야 한다 — v5.259가 파일엔 남겼지만
    API로는 '그날이 아예 없음'과 구분되지 않았다(09-10·09-11 조사에서 막힌 지점)."""
    store["d"] = {
        "2026-09-10": {"_meta": {"intraday": {"n": 0, "ran_at": "x"},
                                 "eod_fallback": {"n": 0, "ran_at": "y"}}},
        "2026-09-14": {"_meta": {"intraday": {"n": 3, "ran_at": "z"}},
                       "X.KS": _rec(ticker="X.KS", resolved=True, gap_close_pct=1.0,
                                    close_price=100.0)},
    }
    s = app._jongga_forward_stats()
    assert "2026-09-10" in s["days_meta"], "레코드 0건인 날짜가 빠졌다"
    assert s["days_meta"]["2026-09-10"]["intraday"]["n"] == 0
    assert s["days_meta"]["2026-09-14"]["intraday"]["n"] == 3
    assert s["total_resolved"] == 1, "_meta가 레코드로 세어졌다"


def test_days_meta_is_empty_dict_for_legacy_days(store):
    """v5.259 이전 레코드는 _meta가 없다 — 키는 있되 빈 dict."""
    store["d"] = {"2026-09-09": {"A.KS": _rec(ticker="A.KS")}}
    assert app._jongga_forward_stats()["days_meta"] == {"2026-09-09": {}}


def test_existing_keys_unchanged(store):
    """기존 소비처가 읽던 키가 그대로 있어야 한다(무영향 보장)."""
    store["d"] = {"2026-09-14": {"X.KS": _rec(ticker="X.KS", resolved=True,
                                              gap_snapshot_pct=1.0, gap_close_pct=2.0,
                                              close_price=100.0)}}
    s = app._jongga_forward_stats()
    for k in ("total_resolved", "snapshot_basis", "close_basis", "recent",
              "backtest_reference", "snapshot_basis_by_source"):
        assert k in s, f"기존 키 {k}가 사라졌다"
