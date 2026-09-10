"""_fetch_market_data(market="all") 리팩터 — 1단계 (v5.237, 사용자 지시).

배경: `docs/`에 적히지 않은(이 세션 조사, "다른 방 담당"이라 docs/는 안 건드림)
확정된 원인 — `cache_key = f"data:{market}"`(app.py)가 "data:kr"과 "data:all"을
서로 다른 키로 취급해서, `_market_fetch_locks`도 market 문자열별로 별개
`asyncio.Lock()`을 만든다. "all"은 KR+US 상위집합인데 캐시·락이 이걸 몰라서
스케줄러의 'kr' 워밍과 사용자 요청의 'all' 워밍이 서로를 인지 못 하고 같은
KR 1505종목을 동시에 중복 fetch한 게 실측(2026-09-10)으로 확인됐다(app.py
v4.48.1 주석이 경고하는 것과 동일한 "콜드 스캔 중복 → 메모리 2~3배 → OOM"
패턴).

리팩터 방향: "data:all"이라는 캐시 슬롯 자체를 없애고, market="all" 요청은
_fetch_market_data("kr", ...) + _fetch_market_data("us", ...)를 각각 호출해
병합한다 — 그러면 기존 cache_key별 락이 자동으로 "all"과 "kr"을 같은 자원
(cache_key="data:kr")으로 인식해 직렬화한다. 새 락 메커니즘을 만들지 않는다.

이 파일은 "1단계: 테스트부터" — 리팩터 이전 코드에서 먼저 실행해 (a) 현재
market="all" 번들의 구조/값이 kr+us를 따로 fetch해 병합한 것과 수학적으로
동일함을 확인(병합 가능성 자체의 증거), (b) 실제 버그(동시 fetch 시 같은
티커가 두 번 fetch됨)가 재현되는지 확인한다. 그 다음 리팩터를 적용하고 같은
테스트가 여전히 (a)는 통과, (b)는 이제 통과(중복 없음)로 바뀌는지 검증한다.

네트워크 의존 없음: test_trace_parity.py가 이미 쓰는 고정 픽스처
(test_fixtures/sample_tickers.pkl, KR 23 + US 15, 2026-08-07 종가까지 300봉,
결정론적)를 그대로 재사용해 naver/yfinance 대신 _fetch/_fetch_us_batch/
_benchmark_rs_scores/_get_earnings_safe를 monkeypatch로 대체한다."""
import asyncio
import pickle
import time
from pathlib import Path

import pytest

import app

FIXTURE_PATH = Path(__file__).parent / "test_fixtures" / "sample_tickers.pkl"
with open(FIXTURE_PATH, "rb") as f:
    FIXTURE: dict = pickle.load(f)

FIXTURE_KR = [t for t in FIXTURE if t.endswith((".KS", ".KQ"))]
FIXTURE_US = [t for t in FIXTURE if not t.endswith((".KS", ".KQ"))]

BUNDLE_KEYS = {"universe", "data", "data_ts", "rs_ranks", "rs_moms",
               "rs3_ranks", "rs_deltas", "sector_info", "ts", "daykey", "timing"}


def _fake_get_universe(market):
    kr = {t: t for t in FIXTURE_KR}
    us = {t: t for t in FIXTURE_US}
    if market == "kr":
        return dict(kr)
    if market == "us":
        return dict(us)
    return {**kr, **us}


def _make_fetch(call_log: list):
    def _fetch(ticker):
        call_log.append(ticker)
        df = FIXTURE.get(ticker)
        return df.copy() if df is not None else None
    return _fetch


def _make_fetch_us_batch(call_log: list):
    def _fetch_us_batch(tickers):
        call_log.extend(tickers)
        return {t: FIXTURE[t].copy() for t in tickers if t in FIXTURE}
    return _fetch_us_batch


def _fake_benchmark_rs_scores():
    # 벤치마크 상수는 같은 시장 안에서 전 종목에 균일하게 적용돼 순위엔
    # 영향이 없다(_compute_rs_ranks 독스트링) — 0.0 고정으로 테스트 단순화.
    return {"us": 0.0, "kospi": 0.0, "kosdaq": 0.0}


async def _fake_get_earnings_safe(ticker):
    return {}


# v5.241(사용자 지시 — 재발방지 작업 [4]): 엔드포인트 스모크 테스트
# (test_endpoints_smoke.py)가 이 fixture를 그대로 재사용(import)할 수
# 있게 market_gate/get_positions/jongga_candidates 가짜 응답을 추가.
# get_calendar()가 실제로 느려지는 지점은 이 셋이 아니라 그 뒤(포지션이
# 있으면 걸리는 실적 D-3 조회) — get_positions()를 빈 포지션으로 고정하면
# 그 경로 자체가 안 걸려서(코드 확인 + 직접 실행 확인) 추가 mock 없이도
# get_calendar()가 수 ms 안에 끝난다.
from fastapi.responses import JSONResponse


async def _fake_market_gate():
    return JSONResponse({"ok": True, "gate_kr": "neutral", "gate_us": "neutral",
                          "suggest": "neutral", "why": ""})


async def _fake_get_positions():
    return JSONResponse({"synced_at": None, "stale": False, "positions": [], "summary": None,
                          "sync_error": None, "sync_enabled": False})


async def _fake_jongga_candidates():
    return JSONResponse({"ok": True, "count": 0, "date": None, "hits": []})


@pytest.fixture
def mocked_env(monkeypatch):
    """네트워크 전부 차단 + 전역 캐시 상태 초기화. call_log에 실제 _fetch/
    _fetch_us_batch에 들어간 티커를 순서대로 기록(중복 fetch 검증용).

    _confirmed_daykey를 항상 None으로 고정하는 이유(중요, 실제로 겪은
    테스트 격리 버그): 이걸 안 하면 "1) 장마감 후 디스크 캐시 우선"
    분기가 실제 현재 시각(실행할 때마다 달라짐) + 이 저장소에 남아있을
    수 있는 실제 디스크 캐시 파일(datacache_*.pkl — 이 세션에서 라이브
    진단할 때 실제로 생겼었음)을 그대로 타서, mock이 아니라 진짜
    캐시된(또는 그날 실행 시점 실제 네트워크) 데이터를 돌려준다 —
    "us만 따로 fetch"할 때만 disk daykey가 마침 확정돼 있어서 mock이
    조용히 무시되고 실데이터(다른 행 수)가 섞이는 걸 직접 겪었다. 항상
    daykey=None으로 고정하면 순수 장중 TTL 경로(디스크 무관)로만 가서
    실행 시각·로컬 디스크 상태와 완전히 무관한 결정론적 테스트가 된다.

    v5.241: market_gate/get_positions/jongga_candidates도 여기서 같이
    막는다 — get_calendar() 스모크 테스트가 이 fixture를 그대로
    재사용하기 위함(이 세 개는 이 파일의 기존 3개 테스트와는 무관 —
    그 테스트들은 get_calendar()를 안 불러서 아무 영향 없음, 추가만
    했지 기존 동작은 안 바꿈)."""
    call_log: list = []
    monkeypatch.setattr(app, "get_universe", _fake_get_universe)
    monkeypatch.setattr(app, "_fetch", _make_fetch(call_log))
    monkeypatch.setattr(app, "_fetch_us_batch", _make_fetch_us_batch(call_log))
    monkeypatch.setattr(app, "_benchmark_rs_scores", _fake_benchmark_rs_scores)
    monkeypatch.setattr(app, "_get_earnings_safe", _fake_get_earnings_safe)
    monkeypatch.setattr(app, "_confirmed_daykey", lambda market: None)
    # v5.231이 만든 시총 허용목록도 fail-open(빈 set)이 되도록 초기화 —
    # 실제 프로덕션 상태와 무관하게 매 테스트가 같은 조건에서 시작하게.
    monkeypatch.setattr(app, "_mcap_allowed_cache", {})
    # 전역 캐시/락 상태 초기화 — 테스트 간 오염 방지(모듈 전역 dict라
    # 그대로 두면 이전 테스트의 _data_cache["data:kr"]가 남아 재사용이
    # 섞여버린다).
    monkeypatch.setattr(app, "_data_cache", {})
    monkeypatch.setattr(app, "_market_fetch_locks", {})
    monkeypatch.setattr(app, "_market_refreshing", {})
    monkeypatch.setattr(app, "market_gate", _fake_market_gate)
    monkeypatch.setattr(app, "get_positions", _fake_get_positions)
    monkeypatch.setattr(app, "jongga_candidates", _fake_jongga_candidates)
    return call_log


def _assert_bundle_shape(bundle):
    assert set(bundle.keys()) == BUNDLE_KEYS, f"번들 키 불일치: {set(bundle.keys())}"
    assert isinstance(bundle["universe"], dict)
    assert isinstance(bundle["data"], dict)
    assert isinstance(bundle["data_ts"], dict)
    assert isinstance(bundle["rs_ranks"], dict)
    assert isinstance(bundle["rs_moms"], dict)
    assert isinstance(bundle["rs3_ranks"], dict)
    assert isinstance(bundle["rs_deltas"], dict)
    assert isinstance(bundle["sector_info"], dict)
    assert set(bundle["sector_info"].keys()) == {"by_ticker", "by_sector"}
    assert isinstance(bundle["ts"], float)
    assert bundle["daykey"] is None or isinstance(bundle["daykey"], str)
    assert isinstance(bundle["timing"], dict)
    for k in ("market", "n_total", "n_reused", "n_fetched_kr", "n_fetched_us",
              "kr_sec", "us_sec", "rs_sec", "n_fetch_failed_kr", "n_fetch_failed_us"):
        assert k in bundle["timing"], f"timing에 {k} 없음"


def test_all_bundle_structure(mocked_env):
    """market="all" 번들이 문서화된 11개 키를 그대로 갖는지 — 8개 호출부
    (run_scan/refresh_market/get_calendar/api_sectors/lookup_ticker/
    debug_ticker/watch_leader_check/get_positions) 전부 이 모양을 기대한다."""
    bundle = asyncio.run(app._fetch_market_data("all", wait_for_fresh=True))
    _assert_bundle_shape(bundle)
    assert set(bundle["data"].keys()) == set(FIXTURE_KR) | set(FIXTURE_US)
    assert set(bundle["universe"].keys()) == set(FIXTURE_KR) | set(FIXTURE_US)


def test_all_bundle_matches_kr_us_merge(mocked_env):
    """market="all"의 rs_ranks/rs_moms/rs3_ranks/rs_deltas/sector_info가
    "kr 따로 fetch + us 따로 fetch 후 병합"과 값이 완전히 같은지 — 이게
    맞아야 1단계 리팩터(all을 kr+us 병합으로 대체)가 결과를 안 바꾼다고
    보장할 수 있다. _compute_rs_ranks/sector_snapshot.compute가 이미
    market별로 내부 분리 계산하므로(코드 확인 완료) 이론상 같아야 하는데,
    여기서 실제로 실행해 증명한다."""
    all_bundle = asyncio.run(app._fetch_market_data("all", wait_for_fresh=True))

    # 전역 상태를 다시 초기화(all fetch가 이미 채워놨으므로) 하고 kr/us를
    # 각각 콜드 상태에서 따로 받는다 — "따로 fetch해서 병합"을 그대로 재현.
    app._data_cache.clear()
    app._market_fetch_locks.clear()
    app._market_refreshing.clear()
    kr_bundle = asyncio.run(app._fetch_market_data("kr", wait_for_fresh=True))
    us_bundle = asyncio.run(app._fetch_market_data("us", wait_for_fresh=True))

    for key in ("rs_ranks", "rs_moms", "rs3_ranks", "rs_deltas"):
        merged = {**kr_bundle[key], **us_bundle[key]}
        assert merged == all_bundle[key], f"{key} 불일치 — all과 kr+us 병합이 다름"

    merged_by_ticker = {**kr_bundle["sector_info"]["by_ticker"], **us_bundle["sector_info"]["by_ticker"]}
    assert merged_by_ticker == all_bundle["sector_info"]["by_ticker"]
    merged_by_sector = {**kr_bundle["sector_info"]["by_sector"], **us_bundle["sector_info"]["by_sector"]}
    assert merged_by_sector == all_bundle["sector_info"]["by_sector"]

    merged_data_keys = set(kr_bundle["data"].keys()) | set(us_bundle["data"].keys())
    assert merged_data_keys == set(all_bundle["data"].keys())


def test_concurrent_kr_and_all_fetch_deduplicates(mocked_env):
    """★ 이번 작업의 핵심 산출물. 콜드 상태에서 _fetch_market_data("kr")과
    _fetch_market_data("all")을 asyncio.gather로 동시 실행 — 실측(2026-09-10
    14:26:27/14:27:01, 34초 간격 동시 실행)을 그대로 재현한다. 같은 KR
    티커가 두 번 fetch되면(call_log에 두 번 등장) FAIL — 리팩터 전 코드는
    cache_key가 "data:kr"/"data:all"로 갈려 있어 이 테스트가 반드시 FAIL해야
    한다(FAIL 안 하면 이 테스트 자체가 버그를 못 잡는 잘못 짜인 테스트)."""
    call_log = mocked_env

    async def _run():
        return await asyncio.gather(
            app._fetch_market_data("kr", wait_for_fresh=True),
            app._fetch_market_data("all", wait_for_fresh=True),
        )

    asyncio.run(_run())

    dup = {t: call_log.count(t) for t in FIXTURE_KR if call_log.count(t) > 1}
    assert not dup, (
        f"KR 티커가 두 번 이상 fetch됨(중복 fetch, 실사고 재현): {dup}\n"
        f"전체 fetch 로그 길이: {len(call_log)} (기대: KR {len(FIXTURE_KR)}건, 중복 없으면 정확히 {len(FIXTURE_KR)})"
    )


def test_all_merge_survives_legacy_timing_schema(mocked_env):
    """★ 긴급 수정 회귀 방지(v5.240, 프로덕션 500 장애: GET /api/scan?
    market=all&mode=pullback → KeyError: 'n_fetch_failed_us', 20초마다
    반복). 원인: _fetch_market_data_all()의 병합 코드가
    kr_t["n_fetch_failed_kr"]/us_t["n_fetch_failed_us"]를 대괄호로 직접
    읽어서, 이 필드가 생기기 전(v5.235 이전) 코드가 저장해둔 디스크
    캐시(datacache_*.pkl, Railway 볼륨에 영구 보존 — 배포해도 안
    지워짐)를 오늘 daykey로 그대로 읽어올 때 죽었다. 실제 프로덕션
    datacache_rs6_us_u2120_2026-09-10.pkl의 timing을 그대로 재현
    (n_fetch_failed_kr/us 필드 자체가 없는 8개 필드짜리 구형 스키마).

    이 테스트가 처음부터 존재했다면 막을 수 있었던 사고다 — 기존
    test_all_bundle_structure는 "새로 계산된" bundle의 timing 키
    존재만 검증해서, "kr/us 서브 bundle 중 하나가 구형 스키마(디스크
    캐시)일 때 병합이 죽는지"는 아예 검증 범위 밖이었다(전부 매번
    fresh mock 계산이라 이 케이스 자체가 생성될 일이 없었음)."""
    now_ts = time.time()

    def _minimal_bundle(market, tickers, legacy_schema):
        timing = {
            "market": market, "n_total": len(tickers), "n_reused": 0,
            "n_fetched_kr": len(tickers) if market == "kr" else 0,
            "n_fetched_us": len(tickers) if market == "us" else 0,
            "kr_sec": 0.0, "us_sec": 0.0, "rs_sec": 0.0,
        }
        if not legacy_schema:
            timing["n_fetch_failed_kr"] = 0
            timing["n_fetch_failed_us"] = 0
        return {
            "universe": {t: t for t in tickers},
            "data": {t: FIXTURE[t] for t in tickers},
            "data_ts": {t: now_ts for t in tickers},
            "rs_ranks": {t: 50 for t in tickers}, "rs_moms": {t: 0 for t in tickers},
            "rs3_ranks": {t: 50 for t in tickers}, "rs_deltas": {t: 0 for t in tickers},
            "sector_info": {"by_ticker": {}, "by_sector": {}},
            "ts": now_ts, "daykey": None, "timing": timing,
        }

    app._data_cache["data:kr"] = _minimal_bundle("kr", FIXTURE_KR, legacy_schema=False)
    # v5.235 이전 스키마 재현 — 실제 프로덕션 사고를 일으킨 그 파일과
    # 동일하게 n_fetch_failed_kr/us가 아예 없음.
    app._data_cache["data:us"] = _minimal_bundle("us", FIXTURE_US, legacy_schema=True)

    all_bundle = asyncio.run(app._fetch_market_data("all", wait_for_fresh=False))
    assert all_bundle is not None, "구형 스키마 서브 bundle 때문에 병합 자체가 실패함(None)"
    assert all_bundle["timing"]["n_fetch_failed_kr"] == 0
    assert all_bundle["timing"]["n_fetch_failed_us"] == 0, (
        "구형(pre-v5.235) 디스크 캐시 스키마의 us 서브 bundle과 병합할 때 "
        "KeyError 없이 기본값(0)으로 채워져야 한다 — 실제 프로덕션 장애 재현"
    )
