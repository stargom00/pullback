"""v5.324 — 저점 스크린·평가 페이지 US = 배당 미조정(분할만 조정) · US 유니버스 제외 규칙 보강.

사용자 결정: "저점 스크린 US + 평가 페이지 US(월봉 RSI·StochRSI·신고가 하락률 포함)를 배당 미조정(분할만 조정)으로
전환. 5탭 US·눌림목 데이터는 불변(측정 기반) — 테스트로 고정." 근거: 키움 10-02 주봉 US 12종목 대조 — 배당 조정 8/12,
미조정 10/12(JBGS·AVA 맞아짐, BIT 빠짐, 깨지는 일치 0). 유니버스: 2026-09-29 목록에서 기존 규칙을 빠져나간 우선주 4
(CDZIP·MNSBP·WAFDP·LILAP) + 구조화증권 8(GJH·GJO·GJP·GJR·GJS·GJT·JBK·KTN)을 이름 패턴으로 제외.

사보타주 확인(2026-10-05, FAIL 확인 후 원복):
① lowpoint.US_AUTO_ADJUST = True → test_screen_us_fetch_is_splits_only FAIL
② 평가 페이지 US의 auto_adjust 인자 제거 → test_eval_page_us_is_splits_only FAIL
③ us_listings의 pref_depositary 패턴 제거 → test_universe_holes_closed_exactly FAIL
④ newlisting의 auto_adjust=True 명시 제거 → test_newlisting_us_unchanged FAIL
"""
from __future__ import annotations

import inspect
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(ROOT, "scripts", "screens"), os.path.join(ROOT, "scripts", "measurements")):
    sys.path.insert(0, p)
import harness  # noqa: E402
import lowpoint as lp  # noqa: E402
import lowpoint_eval as ev  # noqa: E402
import newlisting as nl  # noqa: E402
import us_listings as ul  # noqa: E402

import app  # noqa: E402

KST = timezone(timedelta(hours=9))


@pytest.fixture
def yf_calls(monkeypatch):
    calls = []
    idx = pd.bdate_range(end="2026-10-02", periods=300)

    def fake(tickers, period="2y", auto_adjust=True):
        calls.append((tuple(tickers), period, auto_adjust))
        c = pd.Series(range(len(idx)), index=idx, dtype=float) + 10
        return {t: pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1000.0}) for t in tickers}
    monkeypatch.setattr(harness, "_fetch_us_batch", fake)
    return calls


def test_basis_includes_us():
    assert lp.CALC_BASIS == app.LOWPOINT_CALC_BASIS == "naver_integrated+wilder_sma+us_splits_only"
    assert lp.US_AUTO_ADJUST is False


def test_screen_us_fetch_is_splits_only(yf_calls):
    data, failed, _ = lp.fetch_us(["AVA", "JBGS"], "week")
    assert yf_calls == [(("AVA", "JBGS"), lp.US_PERIOD["week"], False)] and failed == []
    assert "fetch_us(list(uni), tf)" in inspect.getsource(lp.screen_market)        # 스크린은 기본값(미조정)으로 부른다


def test_eval_page_us_is_splits_only(yf_calls):
    ev.fetch_ohlcv("AVA", "US")
    ev.short_table([{"code": "AVA", "market": "US"}])
    assert yf_calls == [(("AVA",), lp.US_PERIOD["month"], False), (("AVA",), lp.US_PERIOD["month"], False)]


def test_newlisting_us_unchanged():
    """신규상장은 이 결정 범위 밖 — 기존(배당 조정) 값 그대로."""
    assert 'lp.fetch_us(us, "week", auto_adjust=True)' in inspect.getsource(nl)


def test_five_tab_us_data_unchanged():
    """5탭 US·눌림목(측정 기반)은 배당 조정 그대로."""
    src = inspect.getsource(app._fetch_us_batch)
    assert src.count("auto_adjust=True") >= 1 and "auto_adjust=False" not in src
    assert 'history(period="2y", interval="1d", auto_adjust=True)' in open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert inspect.signature(harness._fetch_us_batch).parameters["auto_adjust"].default is True   # 측정 스크립트 기본값 불변
    assert "US_AUTO_ADJUST" not in open(os.path.join(ROOT, "scanner.py"), encoding="utf-8").read()


# ── 유니버스 구멍 ─────────────────────────────────────────────────
def _row(symbol, name):
    return {"symbol": symbol, "name": name, "etf": "N", "test_issue": "N"}


# 2026-09-29 Nasdaq Trader 목록의 실제 이름(캐시에서 복사)
HOLES = {
    "CDZIP": ("Cadiz, Inc. - Depositary Shares", "name:pref_depositary"),
    "MNSBP": ("MainStreet Bancshares, Inc. - Depositary Shares", "name:pref_depositary"),
    "WAFDP": ("WaFd, Inc. - Depositary Shares", "name:pref_depositary"),
    "LILAP": ("Liberty Latin America Ltd. - 9.0% Fixed Rate Cumulative Perpetual Redeemable Series A Pref", None),
    "GJH": ("Synthetic Fixed-Income Securities Inc 6.375% (STRATS) Cl A-1", None),
    "GJS": ("Goldman Sachs Group Securities STRATS Trust for Goldman Sachs Group Securities, Series 2006-2", "name:structured"),
    "JBK": ("Lehman ABS 3.50 3.50% Adjustable Corp Backed Tr Certs GS Cap I", None),
    "KTN": ("Structured Products Corp 8.205% CorTS 8.205% Corporate Backed Trust Securities (CorTS)", None),
}


@pytest.mark.parametrize("sym", sorted(HOLES))
def test_hole_names_excluded(sym):
    name, tag = HOLES[sym]
    why = ul.exclusion_reason(_row(sym, name))
    assert why is not None and (tag is None or why == tag)


@pytest.mark.parametrize("sym,name", [
    ("OMAB", "Grupo Aeroportuario del Centro Norte S.A.B. de C.V. - American Depositary Shares each representing"),
    ("IRS", "IRSA Inversiones Y Representaciones S.A. Global Depositary Shares (Each representing ten shares)"),
    ("VIST", "Vista Energy S.A.B. de C.V. American Depositary Shares, each representing one series A share"),
    ("AVA", "Avista Corporation Common Stock"), ("BIT", "BlackRock Multi-Sector Income Trust Common Shares"),
    ("BATRA", "Atlanta Braves Holdings, Inc. - Series A Common Stock"),
])
def test_adr_and_common_still_kept(sym, name):
    assert ul.exclusion_reason(_row(sym, name)) is None


needs_cache = pytest.mark.skipif(not os.path.exists(ul.CACHE_PATH), reason="US 상장목록 캐시 없음")


@needs_cache
def test_universe_holes_closed_exactly():
    """실제 목록에서 새 패턴이 빼는 것은 정확히 이 12개 — 보통주를 잘못 빼지 않는다."""
    rows = json.load(open(ul.CACHE_PATH, encoding="utf-8"))["rows"]
    new_tags = {"pref_depositary", "pref_abbrev", "coupon_pct", "structured"}
    removed = {r["symbol"] for r in rows
               if (ul.exclusion_reason(r) or "").removeprefix("name:") in new_tags
               and r.get("etf") != "Y" and r.get("test_issue") != "Y"
               and not any(rx.search(r.get("name") or "") for tag, rx in ul._NAME_EXCLUDE if tag not in new_tags)
               and ul._SYMBOL_OK.match(r.get("symbol") or "")}
    assert removed == {"CDZIP", "MNSBP", "WAFDP", "LILAP", "GJH", "GJO", "GJP", "GJR", "GJS", "GJT", "JBK", "KTN"}
