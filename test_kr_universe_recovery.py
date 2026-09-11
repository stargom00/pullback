"""KR 유니버스 동적 수집 복구 — load_kr_dynamic() 소스 교체 검증 (v5.246,
사용자 지시 — 긴급). 2026-09-11 naver PC 페이지(finance.naver.com
sise_quant.naver/sise_market_sum.naver) 개편으로 기존 파서(fetch_top_value()
경유)가 200 OK를 받고도 0건을 반환하게 됐고, 이게 19시간+ 아무도 모르게
정적 폴백(254종목, 정상 1505의 17%)으로 이어졌다.

수정: load_kr_dynamic()의 소스를 naver_kr.fetch_top_turnover_v2()(모바일
API, 종가베팅 탭이 이미 쓰던 검증된 경로)로 교체. 실패를 조용히 넘기지
않기 위해 ① 절반 미만 수집 시 경고 로그(임계값은 근거 없는 판단값임을
명시) ② get_kr_universe_info()로 "이번 호출의 소스/건수"를 모듈 상태로
노출 — app.py가 매 스캔 TIMING/{calendar}에 그대로 실어 나른다(별도
테스트 파일에서 그쪽은 검증, 여기는 universe.py 자체만).

레시피: naver_kr.fetch_top_turnover_v2를 monkeypatch로 대체해 실제
네트워크 없이 결정적으로 테스트."""
import sys

import pytest

import universe


@pytest.fixture(autouse=True)
def _isolate_kr_dynamic_cache(monkeypatch, tmp_path):
    """모듈 전역 캐시(_KR_DYNAMIC_CACHE)와 파일 캐시 디렉터리를 테스트마다
    격리 — 이전 테스트의 성공 캐시가 다음 테스트에 새는 걸 방지."""
    monkeypatch.setattr(universe, "_KR_DYNAMIC_CACHE", {})
    monkeypatch.setitem(__import__("os").environ, "JOURNAL_DIR", str(tmp_path))
    # 슬롯키가 매 테스트마다 달라지지 않게 고정(파일 캐시 경로 예측 가능하게) —
    # 실제 값 자체는 중요하지 않고, 테스트 내내 일관되기만 하면 됨.
    monkeypatch.setattr(universe, "_kr_cache_slot", lambda: "test_slot")
    yield


def _patch_turnover_v2(monkeypatch, universe_dict, stats=None):
    default_stats = {"kospi_total": None, "kosdaq_total": None,
                      "kospi_fetched": 0, "kosdaq_fetched": 0,
                      "skipped_etf": 0, "incomplete": False, "errors": []}
    default_stats.update(stats or {})

    def fake_fetch(top_n=1500, page_size=100):
        return dict(universe_dict), default_stats

    monkeypatch.setattr("naver_kr.fetch_top_turnover_v2", fake_fetch)


def _fake_universe(n):
    return {f"{100000 + i:06d}.KS": f"종목{i}" for i in range(n)}


def test_zero_results_triggers_warning_and_static_fallback(monkeypatch, capsys):
    _patch_turnover_v2(monkeypatch, {}, stats={"incomplete": True, "errors": ["KOSPI page=1: Timeout"]})
    out = universe.load_kr_dynamic(top_n=1500)
    assert out == {}
    captured = capsys.readouterr()
    assert "⚠️" in captured.err
    assert "KR 동적 수집 부족" in captured.err
    info = universe.get_kr_universe_info()
    assert info == {"source": "static_fallback", "dynamic_count": 0}


def test_below_half_triggers_warning_but_stays_dynamic(monkeypatch, capsys):
    """절반 미만이지만 0은 아닌 경우 — 경고는 뜨되 source는 여전히
    'dynamic'(완전 실패가 아니라 부분 수집, get_universe()의 병합
    로직이 정적 목록과 합쳐 부분 보강한다)."""
    _patch_turnover_v2(monkeypatch, _fake_universe(600))   # 1500의 40%
    out = universe.load_kr_dynamic(top_n=1500)
    assert len(out) == 600
    captured = capsys.readouterr()
    assert "⚠️" in captured.err
    assert "KR 동적 수집 부족" in captured.err
    info = universe.get_kr_universe_info()
    assert info == {"source": "dynamic", "dynamic_count": 600}


def test_normal_result_no_warning_dynamic_source(monkeypatch, capsys):
    _patch_turnover_v2(monkeypatch, _fake_universe(1499))
    out = universe.load_kr_dynamic(top_n=1500)
    assert len(out) == 1499
    captured = capsys.readouterr()
    assert "⚠️" not in captured.err
    assert "KR 동적 수집 부족" not in captured.err
    info = universe.get_kr_universe_info()
    assert info == {"source": "dynamic", "dynamic_count": 1499}


def test_exception_sets_static_fallback(monkeypatch, capsys):
    def raise_err(top_n=1500, page_size=100):
        raise ConnectionError("network down")
    monkeypatch.setattr("naver_kr.fetch_top_turnover_v2", raise_err)
    out = universe.load_kr_dynamic(top_n=1500)
    assert out == {}
    info = universe.get_kr_universe_info()
    assert info == {"source": "static_fallback", "dynamic_count": 0}


def test_get_universe_kr_falls_back_to_static_when_dynamic_empty(monkeypatch):
    """load_kr_dynamic()이 빈 dict를 반환하면 get_universe("kr")가
    KR_UNIVERSE(정적) 기반으로 폴백하는지 — 실제 병합 로직까지 확인.
    watchlist.txt가 있으면 몇 개 더 얹힐 수 있어 부분집합만 확인(동적
    수집분은 전혀 안 섞여야 함이 핵심)."""
    _patch_turnover_v2(monkeypatch, {})
    result = universe.get_universe("kr")
    for k, v in universe.KR_UNIVERSE.items():
        assert result.get(k) == v
    # 정적 목록 수 근처(watchlist 몇 개 더해질 수 있음)여야지, 동적
    # 1500 규모로 부풀어 있으면 안 됨(= 병합 로직이 잘못 동작한 것).
    assert len(result) < len(universe.KR_UNIVERSE) + 50


def test_get_universe_kr_merges_dynamic_when_available(monkeypatch):
    fake = _fake_universe(1499)
    _patch_turnover_v2(monkeypatch, fake)
    result = universe.get_universe("kr")
    # 동적 결과가 정적 목록 위에 병합되므로 최소 동적 건수만큼은 있어야 함.
    assert len(result) >= len(fake)
    for k in fake:
        assert k in result


def test_get_kr_universe_info_returns_copy():
    """호출부가 반환값을 변형해도 모듈 내부 상태가 오염되지 않아야 함."""
    info = universe.get_kr_universe_info()
    info["source"] = "tampered"
    info2 = universe.get_kr_universe_info()
    assert info2["source"] != "tampered"
