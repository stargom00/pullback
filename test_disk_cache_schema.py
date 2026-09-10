"""디스크 캐시 스키마 검증 — 재발방지 작업 핵심 산출물 (v5.241, 사용자
지시). 프로덕션 500 장애(KeyError: 'n_fetch_failed_us', v5.238 배포
직후) 원인: `_timing` 딕셔너리에 새 필드(v5.235)가 추가됐는데 디스크
캐시 파일명 네임스페이스(`_CACHE_NS`)를 안 올려서, 구형 pkl이 daykey
매칭으로 그대로 로드되며 병합 코드가 없는 키를 읽다가 죽었다.

이 파일이 검증하는 것 두 가지(사용자 지시 — "스키마 상수 어긋남 감지
테스트가 핵심"):
1. `_BUNDLE_SCHEMA_KEYS`/`_TIMING_SCHEMA_KEYS`(app.py)가 실제
   `_fetch_market_data_inner()`가 만드는 bundle/timing과 정확히
   일치하는지 — 어긋나면(누가 `_timing`에 필드를 추가하고 이 상수를
   안 고치면) 이 테스트가 그 자리에서 FAIL해야 한다. 이게 이번 작업의
   실질적 산출물.
2. `_load_disk_cache()`가 스키마 안 맞는(구형) pkl을 실제로 걸러내는지
   — 오늘 사고를 합성 데이터로 재현."""
import pickle

import pytest

import app
from test_fetch_market_data_all_merge import (
    FIXTURE_KR, FIXTURE_US, mocked_env,  # noqa: F401  (fixture 재사용)
)


def test_fresh_bundle_matches_declared_schema(mocked_env):
    """★ 핵심 산출물. get_signal_snapshot·병합이 아니라 "실제로 방금
    계산된" bundle이 app.py가 선언한 스키마 상수와 정확히 일치하는지
    확인한다 — 이게 어긋나면 _load_disk_cache()의 검증 기준 자체가
    실제 코드와 안 맞다는 뜻이라 무의미해진다.

    fetch_failed_sample은 조건부 필드(실패가 있을 때만 존재)라 비교
    전에 제외 — 이 테스트의 fixture(mocked_env)는 fetch가 전부
    성공하므로 애초에 안 생기지만, 명시적으로 처리해 우연에 기대지
    않는다."""
    import asyncio
    bundle = asyncio.run(app._fetch_market_data("kr", wait_for_fresh=True))
    assert bundle is not None

    bundle_keys = set(bundle.keys())
    assert bundle_keys == app._BUNDLE_SCHEMA_KEYS, (
        f"실제 bundle 키 {bundle_keys} != 선언된 _BUNDLE_SCHEMA_KEYS "
        f"{app._BUNDLE_SCHEMA_KEYS} — _fetch_market_data_inner()의 bundle 구성이 "
        f"바뀌었는데 _BUNDLE_SCHEMA_KEYS를 안 고쳤을 가능성"
    )

    timing_keys = set(bundle["timing"].keys()) - {"fetch_failed_sample"}
    assert timing_keys == app._TIMING_SCHEMA_KEYS, (
        f"실제 timing 키 {timing_keys} != 선언된 _TIMING_SCHEMA_KEYS "
        f"{app._TIMING_SCHEMA_KEYS} — _timing 딕셔너리 구성이 바뀌었는데 "
        f"_TIMING_SCHEMA_KEYS를 안 고쳤을 가능성(오늘 사고와 동일 패턴)"
    )


@pytest.fixture
def isolated_disk_cache_dir(monkeypatch, tmp_path):
    """_disk_cache_dir()를 tmp_path로 고정 — 기본값(os.path.dirname(__file__),
    즉 이 레포 루트)으로 실제 pkl 파일을 쓰지 않게 격리."""
    monkeypatch.setattr(app, "_disk_cache_dir", lambda: str(tmp_path))
    return tmp_path


def test_load_disk_cache_rejects_legacy_schema(isolated_disk_cache_dir, monkeypatch):
    """★ 오늘 사고 재현. n_fetch_failed_kr/us가 없는(v5.235 이전 스키마)
    pkl을 실제 프로덕션 파일 구조 그대로 만들어 디스크에 써두고,
    _load_disk_cache()가 이걸 로드하는지/거르는지 확인한다."""
    market, daykey = "us", "2026-09-10"
    monkeypatch.setattr(app, "_universe_sig", lambda m: "u1")
    legacy_bundle = {
        "universe": {}, "data": {}, "data_ts": {}, "rs_ranks": {}, "rs_moms": {},
        "rs3_ranks": {}, "rs_deltas": {}, "sector_info": {"by_ticker": {}, "by_sector": {}},
        "ts": 0.0, "daykey": daykey,
        "timing": {  # 실제 프로덕션 사고 파일과 동일 — 8개 필드만, fail-count 없음
            "market": "us", "n_total": 2120, "n_reused": 2109,
            "n_fetched_kr": 0, "n_fetched_us": 11,
            "kr_sec": 0.0, "us_sec": 0.3, "rs_sec": 2.5,
        },
    }
    path = app._disk_cache_path(market, daykey)
    with open(path, "wb") as f:
        pickle.dump(legacy_bundle, f)

    result = app._load_disk_cache(market, daykey)
    assert result is None, "구형 스키마 pkl이 그대로 로드됨 — 오늘 사고 재현(검증 미작동)"


def test_load_disk_cache_accepts_current_schema(isolated_disk_cache_dir, monkeypatch):
    """대조군 — 정상(현재 스키마) pkl은 그대로 로드돼야 한다(너무
    공격적으로 전부 거부하는 회귀 방지)."""
    market, daykey = "kr", "2026-09-10"
    monkeypatch.setattr(app, "_universe_sig", lambda m: "u1")
    good_bundle = {
        "universe": {}, "data": {}, "data_ts": {}, "rs_ranks": {}, "rs_moms": {},
        "rs3_ranks": {}, "rs_deltas": {}, "sector_info": {"by_ticker": {}, "by_sector": {}},
        "ts": 0.0, "daykey": daykey,
        "timing": {
            "market": "kr", "n_total": 10, "n_reused": 10,
            "n_fetched_kr": 0, "n_fetched_us": 0,
            "kr_sec": 0.0, "us_sec": 0.0, "rs_sec": 0.0,
            "n_fetch_failed_kr": 0, "n_fetch_failed_us": 0,
        },
    }
    path = app._disk_cache_path(market, daykey)
    with open(path, "wb") as f:
        pickle.dump(good_bundle, f)

    result = app._load_disk_cache(market, daykey)
    assert result is not None
    assert result["timing"]["n_total"] == 10


def test_load_disk_cache_missing_file_returns_none(isolated_disk_cache_dir, monkeypatch):
    monkeypatch.setattr(app, "_universe_sig", lambda m: "u1")
    assert app._load_disk_cache("kr", "2099-01-01") is None
