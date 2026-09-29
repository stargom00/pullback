"""저점종목 스크린 **전용** US 유니버스(us_listings.py) + harness 단일 티커 버그 수정.

배경: 2026-09-28 조사에서 ZUMZ(시총 ≈$217~269M)가 `universe.get_universe("us")`의
AUTO 스냅샷 시총 $500M+ 필터에서 탈락해 저점 스크린에 안 잡힌 것이 확인됐다. 조건 계산은
정상이었다(직접 투입 시 hit). 스캐너 유니버스는 늘리면 안 되므로 이 스크린만 쓰는
유니버스를 분리했고, 그 분리가 실제로 "소형주 포함 + ETF류 제외"를 하는지 여기서 검증한다.

사보타주 확인(2026-09-29, 둘 다 FAIL 확인 후 원복):
① `us_listings.exclusion_reason`이 항상 None(제외 해제) → test_etf_excluded_* 2건 FAIL
② `harness._fetch_us_batch`의 MultiIndex 처리를 옛 `single` 분기로 되돌림
   → test_fetch_us_batch_single_ticker FAIL
"""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_ROOT, "scripts", "screens"))
sys.path.insert(0, os.path.join(_ROOT, "scripts", "measurements"))
sys.path.insert(0, _ROOT)

import lowpoint as lp          # noqa: E402
import us_listings as ul       # noqa: E402

CACHE = ul.CACHE_PATH
needs_cache = pytest.mark.skipif(
    not os.path.exists(CACHE),
    reason="US 상장목록 캐시 없음(python3 scripts/screens/us_listings.py --refresh 먼저)")


# ── 제외 규칙(순수) ──────────────────────────────────────────────────

def _row(symbol="ZUMZ", name="Zumiez Inc. - Common Stock", etf="N", test="N"):
    return {"symbol": symbol, "name": name, "etf": etf, "test_issue": test}


def test_common_stock_kept():
    assert ul.exclusion_reason(_row()) is None
    assert ul.exclusion_reason(_row("BRK.B", "Berkshire Hathaway Inc. Class B Common Stock")) is None
    # ADR은 보통주에 대한 예탁증서 — 제외 목록에 없다(남긴다)
    assert ul.exclusion_reason(_row("ABEV", "Ambev S.A. American Depositary Shares")) is None


@pytest.mark.parametrize("name,etf,want", [
    ("SPDR S&P 500 ETF Trust", "Y", "etf_flag"),
    ("iPath Series B Bloomberg Exchange Traded Note", "N", "name:note"),
    ("Arbor Realty Trust 6.375% Series D Cumulative Redeemable Preferred Stock", "N", "name:preferred"),
    ("Ares Acquisition Corporation III Units, each consisting of one Class A ordinary share", "N", "name:unit"),
    ("Ares Acquisition Corporation III Redeemable warrants", "N", "name:warrant"),
    ("Some Corp Rights", "N", "name:right"),
    ("Foo 5.5% Subordinated Notes due 2030", "N", "name:note"),
])
def test_non_common_excluded(name, etf, want):
    assert ul.exclusion_reason(_row("XYZ", name, etf=etf)) == want


def test_symbol_suffix_excluded():
    assert ul.exclusion_reason(_row("AACQ+", "Foo Corp")) == "symbol_suffix"
    assert ul.exclusion_reason(_row("ABRpD", "Foo Corp")) == "symbol_suffix"


def test_no_market_cap_or_volume_filter_in_source():
    """시총·거래량 임계값을 이 모듈에 두지 않는다(사용자 지시 — ZUMZ 탈락 원인)."""
    src = open(os.path.join(_ROOT, "scripts", "screens", "us_listings.py"), encoding="utf-8").read()
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    code = code.split('"""', 2)[-1]          # 모듈 docstring 제외
    for bad in ("marketCap", "market_cap", "min_volume", "MIN_MARKET_CAP", "500_000_000"):
        assert bad not in code, f"제외 로직에 {bad} 기준이 들어갔다"


def test_yahoo_symbol_class_separator():
    assert ul.yahoo_symbol("BRK.B") == "BRK-B"
    assert ul.yahoo_symbol("zumz") == "ZUMZ"


# ── 실제 캐시로 확인 ─────────────────────────────────────────────────

@needs_cache
def test_universe_includes_small_cap_zumz():
    uni, st = ul.build_universe()
    assert "ZUMZ" in uni, "시총 필터 없는 유니버스인데 ZUMZ가 없다"
    assert st["kept"] > 4000, f"보통주 {st['kept']}개 — 전체 상장 규모가 아니다"
    assert st["kept"] < st["total"], "제외가 하나도 안 됐다"


@needs_cache
def test_etf_excluded_from_universe():
    uni, st = ul.build_universe()
    for etf in ("SPY", "QQQ", "IWM", "XLF"):
        assert etf not in uni, f"{etf}(ETF)가 유니버스에 들어왔다"
    assert st["excluded_by_reason"].get("etf_flag", 0) > 1000


@needs_cache
def test_etf_excluded_only_by_exclusion_step():
    """사보타주 대칭 확인 — 제외를 끄면 ETF가 실제로 들어온다(즉 걸러주는 주체가 이 단계)."""
    off, _ = ul.build_universe(apply_exclusions=False)
    on, _ = ul.build_universe()
    assert "SPY" in off and "SPY" not in on


@needs_cache
def test_lowpoint_us_universe_uses_listings_not_scanner_universe():
    uni, st = lp.us_universe()
    assert "ZUMZ" in uni
    assert st["cache_path"] == ul.CACHE_PATH
    # 스캐너 공용 유니버스와 **다른** 집합이어야 한다(이게 분리의 목적)
    from universe import get_universe
    scanner_uni = get_universe("us")
    assert "ZUMZ" not in scanner_uni, "스캐너 유니버스가 바뀌었다 — 건드리지 않아야 한다"
    assert len(uni) > len(scanner_uni)


def test_kr_universe_path_untouched():
    """KR 경로는 이번 변경 대상이 아니다 — KIND 소스를 그대로 쓰는지 코드로 확인."""
    src = open(os.path.join(_ROOT, "scripts", "screens", "lowpoint.py"), encoding="utf-8").read()
    assert "KIND_CORPLIST_URL" in src and "def kr_universe(" in src
    assert "us_listings" in src.split("def kr_universe(")[0] or True
    kr_body = src.split("def kr_universe(")[1].split("\ndef ")[0]
    assert "us_listings" not in kr_body, "KR 유니버스가 US 목록 모듈을 참조한다"


# ── harness 단일 티커 버그 ───────────────────────────────────────────

@pytest.mark.skipif(os.environ.get("NO_NETWORK") == "1", reason="네트워크 차단 환경")
def test_fetch_us_batch_single_ticker():
    """티커 1개여도 정상 반환해야 한다(2026-09-28 재현: 빈 dict → 호출부 KeyError).
    배치 경로 결과와 값이 같아야 한다(수정이 배치 동작을 바꾸지 않았다는 확인).

    **빈 결과를 무조건 FAIL로 두면 yfinance rate limit(YFRateLimitError)에 오탐한다**
    — 실제로 5,600종목 스크린 직후 이 테스트가 그렇게 한 번 실패했다. 그래서 먼저
    같은 파라미터로 원천(yf.download)을 직접 찍어, **원천도 비어 있으면 skip**(외부 제한),
    원천엔 데이터가 있는데 `_fetch_us_batch`만 비면 FAIL(우리 버그)로 가른다.
    수정을 되돌렸을 때의 탐지는 네트워크 없는 `test_fetch_us_batch_no_silent_empty_guard`
    가 같이 담당한다(그래서 여기가 skip돼도 사보타주는 잡힌다)."""
    import harness
    import yfinance as yf
    try:
        raw = yf.download(["ZUMZ"], period="1y", interval="1d", auto_adjust=True,
                          group_by="ticker", threads=True, progress=False)
    except Exception as e:
        pytest.skip(f"yfinance 원천 호출 실패(외부 제한 추정): {e}")
    if raw is None or len(raw) == 0:
        pytest.skip("yfinance 원천이 빈 응답 — rate limit/네트워크 제한(우리 코드 판정 불가)")
    one = harness._fetch_us_batch(["ZUMZ"], period="1y")
    assert "ZUMZ" in one and not one["ZUMZ"].empty, "단일 티커가 빈 결과"
    assert {"Open", "High", "Low", "Close", "Volume"} <= set(one["ZUMZ"].columns)
    batch = harness._fetch_us_batch(["ZUMZ", "AAPL"], period="1y")
    if not batch:
        pytest.skip("배치 호출이 빈 응답 — rate limit 추정")
    assert {"ZUMZ", "AAPL"} <= set(batch)
    assert one["ZUMZ"]["Close"].equals(batch["ZUMZ"]["Close"]), "단일/배치 결과 불일치"


def test_fetch_us_batch_no_silent_empty_guard():
    """빈 결과를 방어 코드로 숨기지 않았는지 — 컬럼 구조로 판단하는지 소스 확인."""
    src = open(os.path.join(_ROOT, "scripts", "measurements", "harness.py"),
               encoding="utf-8").read()
    body = src.split("def _fetch_us_batch(")[1].split("\ndef ")[0]
    assert "isinstance(raw.columns, pd.MultiIndex)" in body
    assert "single = len(tickers) == 1" not in body, "티커 개수로 추측하는 옛 분기가 남아있다"


# ── 실행 끝 제외 목록 출력(시장별 meta 모양 차이) ────────────────────

def _res(market, meta, **kw):
    base = {"market": market, "failed": [], "stale": {}, "short": {}, "names": {}, "meta": meta}
    base.update(kw)
    return base


def test_exclusion_lines_kr_shape():
    lines = lp.exclusion_detail_lines(
        _res("kospi", {"admin_excluded": {"000040.KS": "KR모터스"}}), "week")
    assert any("관리종목 제외" in l and "KR모터스" in l for l in lines)
    assert not any("상장목록 제외" in l for l in lines)


def test_exclusion_lines_us_shape_does_not_crash():
    """US meta에는 admin_excluded가 없다 — 무조건 읽으면 KeyError로 죽는다
    (2026-09-29 실제로 CSV 생성 후 비정상 종료했다)."""
    lines = lp.exclusion_detail_lines(
        _res("us", {"excluded_by_reason": {"etf_flag": 5741}, "total": 13280, "kept": 5618},
             failed=["GRMN"], short={"AAC": 5}, names={"GRMN": "Garmin"}), "week")
    assert any("상장목록 제외" in l and "5618" in l for l in lines)
    assert any("조회 실패 1종목" in l for l in lines)
    assert not any("관리종목" in l for l in lines)


def test_exclusion_lines_reports_optional_filter():
    lines = lp.exclusion_detail_lines(
        _res("us", {"excluded_by_reason": {}, "total": 1, "kept": 1},
             opt_dropped={"FOO": "price 1.0"}), "week")
    assert any("옵션 필터 제외 1종목" in l for l in lines)
