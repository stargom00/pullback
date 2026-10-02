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

[v5.314 결정화] 처음엔 "더본코리아가 실제 유니버스 밖"이라는 **실데이터**를 전제로 썼는데,
2026-10-03 거래대금 순위가 바뀌어 더본이 KR 유니버스(상위 1,500) 안으로 들어오자 3건이 실패했다
(수정 전 HEAD에서도 동일 — 코드 회귀가 아니라 전제 붕괴). 그래서 resolve 경로 테스트는
`isolated` 픽스처로 **세 목록(저점 결과 파일·스캐너 유니버스·US 상장목록)을 테스트 안에서 주입**하고
원천(naver_kr.fetch_basic)도 mock한다 — 가짜 코드 FAKE가 확실히 목록 밖이고, 실행 시점의 실제
유니버스·네트워크와 무관하게 통과/실패가 갈린다. naver 원천 자체(fetch_basic의 sosok 판정)는
별도 네트워크 테스트로 남긴다.

사보타주 확인(2026-10-01, 전부 FAIL 확인 후 원복):
① KR 폴백 분기(`if kr_code: basic = ...`) 제거 → 475560 테스트 2건 FAIL
   (v5.314 재확인 2026-10-03: 결정화 후에도 test_kr_code_outside_lists_resolves_via_naver FAIL)
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
DOBON = "475560"          # 더본코리아 — naver 원천 테스트용 실종목(유니버스 포함 여부와 무관하게 씀)
FAKE = "990001"           # 결정적 테스트용 가짜 코드 — 주입한 목록 어디에도 없다
client = TestClient(app.app)

# 주입하는 스캐너 유니버스 — 목록 안 종목(카페24)만 있고 FAKE는 없다
FAKE_UNIVERSE = {"042000.KQ": "카페24", "005930.KS": "삼성전자"}


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """resolve가 보는 세 목록과 naver 원천을 전부 테스트가 정한 값으로 고정한다.
    반환: fetch_basic 호출 기록(list)."""
    lp_file = tmp_path / "lowpoint_latest.json"
    lp_file.write_text(json.dumps({"week": {"rows": []}, "month": {"rows": []}}), encoding="utf-8")
    monkeypatch.setattr(app, "LOWPOINT_DATA_PATH", str(lp_file))
    monkeypatch.setattr(app, "LOWPOINT_LATEST_PATH", str(lp_file))
    monkeypatch.setattr(app, "LOWPOINT_US_LISTINGS_PATH", str(tmp_path / "no_us_listings.json"))
    monkeypatch.setattr(app, "get_universe", lambda market=None: dict(FAKE_UNIVERSE))
    calls = []

    def fake_basic(code):
        calls.append(code)
        if code == FAKE:
            return {"code": FAKE, "suffix": ".KS", "name": "가짜코리아", "market": "KOSPI", "halted": False}
        return None
    monkeypatch.setattr(naver_kr, "fetch_basic", fake_basic)
    return calls


def _resolve(q: str):
    r = client.get(f"/api/lowpoint/resolve/{q}")
    assert r.status_code == 200, r.text
    return r.json()


# ── 원인(전제) 고정 ─────────────────────────────────────────────────

def test_premise_fake_code_is_outside_every_injected_list(isolated):
    """전제를 실데이터가 아니라 주입값으로 고정 — FAKE는 세 목록 어디에도 없고,
    주입한 유니버스가 실제로 resolve에 쓰인다(목록 안 종목은 universe 경로로 잡힌다)."""
    assert f"{FAKE}.KS" not in FAKE_UNIVERSE and f"{FAKE}.KQ" not in FAKE_UNIVERSE
    got = _resolve("042000")
    assert got["ok"] and got["source"] == "universe" and got["ticker"] == "042000.KQ"
    assert isolated == [], "목록 안 종목인데 naver 원천을 불렀다"


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

def test_kr_code_outside_lists_resolves_via_naver(isolated):
    """목록 밖 KR 코드 → naver 원천 폴백. skip 없이 결정적(목록·원천 전부 주입) —
    v5.310 첫 버전은 skip 조건 때문에 사보타주가 통과했고, 둘째 버전은 실데이터 전제가 깨졌다."""
    got = _resolve(FAKE)
    assert got["ok"] is True, f"목록 밖 코드인데 폴백이 안 됐다: {got}"
    assert got["ticker"] == f"{FAKE}.KS" and got["name"] == "가짜코리아" and got["mkt"] == "KR"
    assert got["source"] == "naver", "폴백이 아니라 다른 경로로 잡혔다"
    assert isolated == [FAKE]


def test_unknown_kr_code_fails_with_hint(isolated):
    got = _resolve("999999")
    assert got["ok"] is False and got["reason"] == "not_found"
    assert got["kr_code"] is True          # 프론트의 시장 선택 백스톱은 유지
    assert "6자리 코드" in got["hint"] and "티커" in got["hint"]


def test_name_input_has_no_guessing(isolated, monkeypatch):
    """이름은 목록 매칭만 — 목록 밖 이름은 실패해야 한다(이름→코드 추측 금지).
    원천 mock은 **무엇을 물어도 성공**하게 바꿔 둔다 — 추측 조회가 생기면 ok가 되어 바로 잡힌다."""
    monkeypatch.setattr(naver_kr, "fetch_basic", lambda code: (isolated.append(code) or
                        {"code": code, "suffix": ".KS", "name": "가짜코리아", "market": "KOSPI", "halted": False}))
    monkeypatch.setattr(app, "_us_ticker_name", lambda t: (isolated.append(t) or "Guess Corp"))
    got = _resolve("가짜코리아")
    assert got["ok"] is False, "이름으로 폴백 조회를 하면 안 된다"
    assert got.get("hint") and isolated == [], f"이름 입력으로 원천을 불렀다: {isolated}"


def test_us_ticker_outside_lists_resolves_via_yahoo(isolated, monkeypatch):
    """us_listings에 없는 US 티커도 일봉이 오면 저장 가능해야 한다."""
    monkeypatch.setattr(app, "_us_ticker_name", lambda t: "Fake Corp" if t == "ZZZZ" else None)
    got = _resolve("ZZZZ")
    assert got["ok"] is True and got["source"] == "yahoo"
    assert got["ticker"] == "ZZZZ" and got["name"] == "Fake Corp" and got["mkt"] == "US"


def test_us_ticker_not_existing_fails(isolated, monkeypatch):
    monkeypatch.setattr(app, "_us_ticker_name", lambda t: None)
    got = _resolve("ZZZZ")
    assert got["ok"] is False and got.get("hint")


def test_existing_list_tickers_unchanged(isolated):
    """목록 안 종목은 기존 경로 그대로 — source가 폴백이 아니어야 한다."""
    got = _resolve("042000")          # 카페24(코스닥) — 주입한 유니버스에 있음
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
