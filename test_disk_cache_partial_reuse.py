"""v5.286 — 디스크 캐시 파일명에서 u{N} 제거 + 교집합 부분 재사용.

[확정 원인] 09-25 기동 로그의 /data 상태가 결정적이었다:
`datacache_rs9_kr_u1504_2026-09-23.pkl`(09-23 EOD 저장)과
`datacache_rs9_kr_u1505_2026-09-23.pkl`(09-24 아침 콜드 저장)이 **같은 날짜로
나란히** 있었다. 09-24 아침 스캔은 u1505 이름을 찾다가 바로 옆의 u1504 파일을
보지도 못하고 miss → 1,505종목 전량 콜드(24분). KR 유니버스는 거래대금 상위
1500 ∪ 정적 254 ∪ 워치리스트라 **하루 1종목만 달라져도** 파일명이 바뀌어
전체 캐시가 무효화되는 구조였다.

[지금] 이름은 NS·market·daykey만. 유니버스 차이는 교집합 재사용 +
차집합만 fetch로 흡수한다.
"""
import asyncio
import time
from pathlib import Path

import pytest

import app
from test_fetch_market_data_all_merge import (  # noqa: F401
    FIXTURE, FIXTURE_KR, mocked_env,
)

ROOT = Path(__file__).resolve().parent
DAYKEY = "2026-09-23"


def _saved_bundle(tickers, tmp_path):
    """프로덕션과 같은 함수로 디스크 캐시를 만든다(직접 파일을 빚지 않는다 —
    CLAUDE.md '입력 생성 경로부터 프로덕션과 같게')."""
    bundle = {k: {} for k in app._BUNDLE_SCHEMA_KEYS}
    bundle["timing"] = {k: 0 for k in app._TIMING_SCHEMA_KEYS}
    bundle["daykey"] = DAYKEY
    bundle["ts"] = time.time()
    bundle["data"] = {t: FIXTURE[t].copy() for t in tickers}
    # 디스크 데이터는 "오래된" 시각이어야 한다 — REUSE_TTL로 걸러지면
    # 부분 재사용이 아니라 전량 재fetch가 된다(그걸 잡는 게 이 테스트다).
    bundle["data_ts"] = {t: 0 for t in tickers}
    app._save_disk_cache("kr", DAYKEY, bundle)
    return bundle


@pytest.fixture
def kr_env(mocked_env, monkeypatch, tmp_path):  # noqa: F811
    """확정 daykey(장마감 후) 상태로 고정 — 디스크 캐시 경로를 타게."""
    monkeypatch.setattr(app, "_confirmed_daykey", lambda market: DAYKEY if market == "kr" else None)
    return mocked_env   # call_log


def _fetched_kr(call_log):
    return [t for t in call_log if t.endswith((".KS", ".KQ"))]


def test_added_ticker_reuses_the_rest_and_fetches_only_the_new_one(kr_env, monkeypatch, capsys):
    """유니버스 1종목 **추가** — 나머지는 전부 재사용, 새 것만 fetch."""
    old = FIXTURE_KR[:-1]
    _saved_bundle(old, None)
    kr_env.clear()
    bundle = asyncio.run(app._fetch_market_data("kr", wait_for_fresh=True))
    assert _fetched_kr(kr_env) == [FIXTURE_KR[-1]], _fetched_kr(kr_env)
    assert bundle["timing"]["n_reused"] == len(old)
    assert set(bundle["data"]) == set(FIXTURE_KR)
    out = capsys.readouterr().out
    assert f"hit datacache_rs9_kr_{DAYKEY}.pkl 재사용 {len(old)} · 신규 fetch 1 · 제외 0" in out, out


def test_removed_ticker_reuses_everything_and_fetches_nothing(kr_env, monkeypatch, capsys):
    """유니버스 1종목 **제거** — 재사용만, fetch 0. 파일에만 있는 종목은 버린다."""
    dropped = FIXTURE_KR[-1]
    _saved_bundle(FIXTURE_KR, None)
    kr_env.clear()
    monkeypatch.setattr(app, "get_universe",
                        lambda m: {t: t for t in FIXTURE_KR if t != dropped} if m == "kr" else {})
    bundle = asyncio.run(app._fetch_market_data("kr", wait_for_fresh=True))
    assert _fetched_kr(kr_env) == []
    assert bundle["timing"]["n_reused"] == len(FIXTURE_KR) - 1
    assert dropped not in bundle["data"]
    out = capsys.readouterr().out
    assert f"재사용 {len(FIXTURE_KR) - 1} · 신규 fetch 0 · 제외 1" in out, out


def test_identical_universe_is_still_a_whole_bundle_hit(kr_env, capsys):
    """완전 일치면 예전처럼 번들을 그대로 승격한다(RS 재계산도 없음)."""
    saved = _saved_bundle(FIXTURE_KR, None)
    kr_env.clear()
    bundle = asyncio.run(app._fetch_market_data("kr", wait_for_fresh=True))
    assert _fetched_kr(kr_env) == []
    assert bundle["ts"] == saved["ts"], "같은 번들이 그대로 올라와야 한다"
    assert "재사용 %d · 신규 fetch 0 · 제외 0" % len(FIXTURE_KR) in capsys.readouterr().out


def test_new_tickers_go_through_the_same_quality_checks(kr_env, monkeypatch):
    """신규 fetch 종목도 기존 품질검사(_filter_invalid_bars)를 거쳐야 한다 —
    전용 경로를 따로 만들면 여기서 조용히 빠진다."""
    new_t = FIXTURE_KR[-1]
    _saved_bundle(FIXTURE_KR[:-1], None)
    kr_env.clear()
    real_fetch = app._fetch

    def counting_fetch(ticker, stats_sink=None):
        df = real_fetch(ticker, stats_sink)
        if ticker == new_t and stats_sink is not None:
            # 실제 _downcast()가 넣는 것과 같은 모양(list of dict)
            stats_sink.append({"ticker": ticker, "n_dropped": 3, "gap_truncated": False})
        return df
    monkeypatch.setattr(app, "_fetch", counting_fetch)
    bundle = asyncio.run(app._fetch_market_data("kr", wait_for_fresh=True))
    # stats_sink가 실제로 전달됐다는 것 = 신규 종목이 품질검사 경로를 탔다는 것
    assert bundle["timing"]["n_invalid_bars_dropped_kr"] == 3, bundle["timing"]


def test_legacy_u_name_is_read_during_the_transition(kr_env, tmp_path, capsys):
    """전환기: 새 이름이 없고 구 u{N} 파일만 있으면 그걸 읽어 부분 재사용."""
    _saved_bundle(FIXTURE_KR[:-1], None)
    new_name = tmp_path / f"datacache_rs9_kr_{DAYKEY}.pkl"
    legacy = tmp_path / f"datacache_rs9_kr_u1504_{DAYKEY}.pkl"
    new_name.rename(legacy)
    kr_env.clear()
    bundle = asyncio.run(app._fetch_market_data("kr", wait_for_fresh=True))
    out = capsys.readouterr().out
    assert "구 이름 파일 사용(전환기)" in out and legacy.name in out, out
    assert _fetched_kr(kr_env) == [FIXTURE_KR[-1]]
    assert bundle["timing"]["n_reused"] == len(FIXTURE_KR) - 1


def test_two_files_for_one_day_cannot_survive_a_save(kr_env, tmp_path):
    """09-24 사고의 볼륨 상태(u1504·u1505 공존)가 다시 생기면 안 된다 —
    저장은 방금 쓴 그 파일 하나만 남긴다."""
    _saved_bundle(FIXTURE_KR[:-1], None)
    (tmp_path / f"datacache_rs9_kr_{DAYKEY}.pkl").rename(tmp_path / f"datacache_rs9_kr_u1504_{DAYKEY}.pkl")
    kr_env.clear()
    asyncio.run(app._fetch_market_data("kr", wait_for_fresh=True))
    left = sorted(f.name for f in tmp_path.glob("datacache_*_kr_*.pkl"))
    assert left == [f"datacache_rs9_kr_{DAYKEY}.pkl"], left


def test_filename_has_no_universe_signature():
    assert "u" not in Path(app._disk_cache_path("kr", DAYKEY)).name.replace("datacache_", "")
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    body = src[src.index("def _disk_cache_path("):src.index("def _legacy_disk_cache_path(")]
    assert "_universe_sig" not in body, "파일명에 유니버스 크기가 다시 들어갔다"
