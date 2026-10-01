"""v5.310 — 저점 매매 기록의 종목 해석 폴백(목록 밖 종목도 저장).

배경: "더본코리아"(475560, KOSPI, 2024-11 상장)를 저점 탭에 기록하려니 실패했다.
원인은 이름 매칭 버그가 아니라 **목록 미포함** — `universe.load_kr_dynamic()`이
거래대금 상위 1,500(`KR_TOP_N`)만 담고 이 종목은 일 거래대금 1~3억으로 순위 밖이다.
저점 탭은 "사용자가 실제 매매한 종목"을 적는 곳이라 스캐너 유니버스와 무관해야 한다.

폴백 규칙: 세 목록(저점 후보·스캐너 유니버스·us_listings)에서 못 찾으면
  · KR 6자리 코드 → naver_kr.fetch_basic()으로 실재·이름·시장 확인 후 저장
  · US 티커 형식 → 일봉이 오는지로 실재 확인 후 저장
  · 둘 다 실패 → 기존 실패 + hint 문구
**이름 입력은 목록 매칭만**(이름→코드 추측 금지, 사용자 지시).

사보타주 확인(2026-10-01, 전부 FAIL 확인 후 원복):
① KR 폴백 분기(`if kr_code: basic = ...`) 제거 → 475560 테스트 2건 FAIL
② `fetch_basic`이 sosok을 안 보고 항상 ".KS" → test_fetch_basic_market_suffix FAIL
③ 이름 입력에도 폴백 적용(이름→코드 추측) → test_name_input_has_no_guessing FAIL
"""
from __future__ import annotations

import json
import os
import re

import pytest
from fastapi.testclient import TestClient

import app
import naver_kr

_ROOT = os.path.dirname(os.path.abspath(__file__))
DOBON = "475560"          # 더본코리아 — KOSPI, 스캐너 유니버스 밖
client = TestClient(app.app)


def _resolve(q: str):
    r = client.get(f"/api/lowpoint/resolve/{q}")
    assert r.status_code == 200, r.text
    return r.json()


# ── 원인(전제) 고정 ─────────────────────────────────────────────────

def test_premise_dobon_is_outside_every_list():
    """이 테스트가 깨지면(= 유니버스에 들어오면) 폴백은 더 이상 이 종목으로 검증되지 않는다."""
    from universe import get_universe
    uni = get_universe(None)
    assert f"{DOBON}.KS" not in uni and f"{DOBON}.KQ" not in uni
    repo = json.load(open(os.path.join(_ROOT, "data", "lowpoint_latest.json"), encoding="utf-8"))
    rows = [r for tf in app.LOWPOINT_TFS for r in (repo.get(tf) or {}).get("rows", [])]
    assert not [r for r in rows if DOBON in str(r.get("code"))]


def test_premise_kr_universe_is_turnover_ranked():
    """빠진 이유가 '거래대금 상위 N' 컷이라는 근거 — 다른 필터가 아니다."""
    import universe as U
    assert U.KR_TOP_N == 1500
    src = open(os.path.join(_ROOT, "universe.py"), encoding="utf-8").read()
    body = src.split("def load_kr_dynamic(")[1].split("\ndef ")[0]
    assert "fetch_top_turnover_v2(top_n)" in body


# ── naver_kr.fetch_basic (실재·이름·시장) ───────────────────────────

@pytest.mark.parametrize("code,name,suffix", [
    (DOBON, "더본코리아", ".KS"),
    ("005930", "삼성전자", ".KS"),
    ("016670", "디모아", ".KQ"),
    ("262840", "아이퀘스트", ".KQ"),
])
def test_fetch_basic_market_suffix(code, name, suffix):
    got = naver_kr.fetch_basic(code)
    if got is None:
        pytest.skip("naver basic API 응답 없음(네트워크/차단)")
    assert got["name"] == name
    assert got["suffix"] == suffix, "sosok(0=KOSPI/1=KOSDAQ) 판정이 틀렸다"
    assert got["market"] == ("KOSPI" if suffix == ".KS" else "KOSDAQ")


def test_fetch_basic_unknown_code_is_none():
    assert naver_kr.fetch_basic("999999") is None


def test_fetch_basic_does_not_guess_market(monkeypatch):
    """sosok도 자동완성도 못 주면 **추측하지 않고 실패**한다(.KS 기본값 금지)."""
    class R:
        status_code = 200

        @staticmethod
        def json():
            return {"stockName": "테스트", "sosok": ""}

    monkeypatch.setattr(naver_kr.requests, "get", lambda *a, **k: R())
    monkeypatch.setattr(naver_kr, "_market_suffix_from_autocomplete", lambda code: None)
    assert naver_kr.fetch_basic("123456") is None


# ── resolve 폴백 ────────────────────────────────────────────────────

def test_kr_code_outside_lists_resolves_via_naver():
    """**skip 조건을 resolve 결과로 판단하면 안 된다** — 폴백 분기를 지워도 not_found가
    되어 skip으로 빠져나가고 사보타주가 통과한다(2026-10-01 실제로 그랬다).
    원천(naver_kr.fetch_basic)을 먼저 직접 찍어 네트워크 가능 여부를 가린 뒤,
    원천이 되는데 resolve가 못 찾으면 **FAIL**로 간다."""
    probe = naver_kr.fetch_basic(DOBON)
    if probe is None:
        pytest.skip("naver basic API 자체가 응답 없음(네트워크/차단) — 우리 코드 판정 불가")
    got = _resolve(DOBON)
    assert got["ok"] is True, f"원천은 되는데 resolve가 못 찾았다(폴백 미작동): {got}"
    assert got["ticker"] == f"{DOBON}.KS"
    assert got["name"] == "더본코리아"
    assert got["mkt"] == "KR"
    assert got["source"] == "naver", "폴백이 아니라 다른 경로로 잡혔다"


def test_unknown_kr_code_fails_with_hint():
    got = _resolve("999999")
    assert got["ok"] is False and got["reason"] == "not_found"
    assert got["kr_code"] is True          # 프론트의 시장 선택 백스톱은 유지
    assert "6자리 코드" in got["hint"] and "티커" in got["hint"]


def test_name_input_has_no_guessing():
    """이름은 목록 매칭만 — 목록 밖 이름은 실패해야 한다(이름→코드 추측 금지)."""
    got = _resolve("더본코리아")
    assert got["ok"] is False, "이름으로 폴백 조회를 하면 안 된다"
    assert got.get("hint")


def test_us_ticker_outside_lists_resolves_via_yahoo(monkeypatch):
    """us_listings에 없는 US 티커도 일봉이 오면 저장 가능해야 한다."""
    monkeypatch.setattr(app, "_us_ticker_name", lambda t: "Fake Corp" if t == "ZZZZ" else None)
    got = _resolve("ZZZZ")
    assert got["ok"] is True and got["source"] == "yahoo"
    assert got["ticker"] == "ZZZZ" and got["name"] == "Fake Corp" and got["mkt"] == "US"


def test_us_ticker_not_existing_fails(monkeypatch):
    monkeypatch.setattr(app, "_us_ticker_name", lambda t: None)
    got = _resolve("ZZZZ")
    assert got["ok"] is False and got.get("hint")


def test_existing_list_tickers_unchanged():
    """목록 안 종목은 기존 경로 그대로 — source가 폴백이 아니어야 한다."""
    got = _resolve("042000")          # 카페24(코스닥) — 저점 후보/유니버스에 있음
    assert got["ok"] is True
    assert got["ticker"].startswith("042000") and got["mkt"] == "KR"
    assert got["source"] in ("lowpoint", "universe"), f"폴백으로 샜다: {got['source']}"


def test_us_name_lookup_requires_real_bars():
    """`_us_ticker_name`은 일봉으로 실재를 판정한다 — info만 보면 없는 티커도 통과한다."""
    src = open(os.path.join(_ROOT, "app.py"), encoding="utf-8").read()
    body = src.split("def _us_ticker_name(")[1].split("\ndef ")[0]
    assert "history(period=" in body and "df.empty" in body


# ── 현재가 갱신(/api/prices)이 폴백 종목에서도 동작 ──────────────────

def test_prices_endpoint_has_no_universe_dependency():
    src = open(os.path.join(_ROOT, "app.py"), encoding="utf-8").read()
    body = src.split('@app.post("/api/prices")')[1].split("\n@app.")[0]
    assert "get_universe" not in body, "현재가 조회가 유니버스에 의존하면 폴백 종목이 갱신 안 된다"
    assert "naver_kr.fetch_history" in body


def test_prices_endpoint_returns_price_for_fallback_ticker():
    r = client.post("/api/prices", json={"tickers": [f"{DOBON}.KS"]})
    assert r.status_code == 200
    price = (r.json().get("prices") or {}).get(f"{DOBON}.KS")
    if price is None:
        pytest.skip("naver 일봉 조회 실패(네트워크)")
    assert price > 0


# ── 프론트 안내 문구 ────────────────────────────────────────────────

def test_frontend_appends_hint_to_error():
    src = open(os.path.join(_ROOT, "static", "index.html"), encoding="utf-8").read()
    i = src.index("종목을 찾지 못했어요")
    tail = src[i:i + 400]
    assert "t.hint" in tail, "서버 hint를 화면에 덧붙이지 않는다"


def test_version_badge_matches():
    src = open(os.path.join(_ROOT, "static", "index.html"), encoding="utf-8").read()
    m = re.search(r'id="verBadge"[^>]*>(v[\d.]+)<', src)
    assert m and m.group(1) == app.VERSION
