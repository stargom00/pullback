"""v5.254 섹터/테마 페이지 — 라우트 2개 검증.

핵심 제약 3가지를 테스트가 강제한다:
  1. **캐시만 읽는다** — 두 라우트 어느 쪽도 _fetch_market_data()를 부르면 안 된다
     (콜드 스타트면 수 분 블록 → "첫 로드가 느려지면 안 된다"는 제약 위반).
  2. **콜드 캐시는 명시** — 빈 화면이 아니라 cache_state="cold" + 안내 문구.
  3. **themes_kr.json fail-open이되 조용하지 않게** — 파일 없음/깨짐이면 ok=False와
     사유를 싣고, 업종 층은 영향 없음.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402


def _series(vals):
    idx = pd.date_range("2024-01-01", periods=len(vals), freq="B")
    return pd.DataFrame({"Close": vals, "High": vals, "Low": vals,
                         "Open": vals, "Volume": [1000] * len(vals)}, index=idx)


def _fake_bundle():
    """210봉짜리 최소 bundle — 실제 스캔 없이 조립 로직만 본다."""
    up = list(range(100, 310))          # 우상향
    flat = [200.0] * 210
    return {
        "universe": {"005930.KS": "삼성전자", "000660.KS": "SK하이닉스"},
        "data": {"005930.KS": _series(up), "000660.KS": _series(flat)},
        "rs_ranks": {"005930.KS": 95, "000660.KS": 40},
        "sector_info": {
            "by_ticker": {
                "005930.KS": {"sector": "반도체", "market": "KR", "rank": 1, "total": 2,
                              "sector_rs_pct": 90},
                "000660.KS": {"sector": "반도체", "market": "KR", "rank": 2, "total": 2,
                              "sector_rs_pct": 90},
            },
            "by_sector": {
                "반도체|KR": {"sector": "반도체", "market": "KR", "n": 2, "ret20": 5.5,
                              "ret60": 12.0, "sector_rs_pct": 90, "rs20_pct": 88,
                              "new_high_52w": True, "pct_above_ma50": 100.0,
                              "pct_20d_high": 50.0,
                              "leaders": [{"ticker": "005930.KS", "qualifies": True},
                                          {"ticker": "000660.KS", "qualifies": None}]},
                "반도체|US": {"sector": "반도체", "market": "US", "n": 3, "ret20": 1.0,
                              "ret60": 2.0, "leaders": []},
            },
        },
        "ts": 1757700000, "daykey": "2026-09-11",
    }


@pytest.fixture
def warm_cache(monkeypatch):
    monkeypatch.setitem(app._data_cache, "data:kr", _fake_bundle())
    yield


@pytest.fixture
def no_fetch(monkeypatch):
    """_fetch_market_data를 부르면 즉시 실패시킨다 — 제약 1의 강제 장치."""
    async def boom(*a, **k):
        raise AssertionError("라우트가 _fetch_market_data()를 호출했다 — 캐시만 읽어야 한다")
    monkeypatch.setattr(app, "_fetch_market_data", boom)
    yield


# ── 제약 1: 캐시만 읽는다 ──────────────────────────────────────────────
def test_routes_never_trigger_a_fetch(no_fetch, monkeypatch):
    monkeypatch.setattr(app, "_data_cache", {})
    monkeypatch.setattr(app, "_load_disk_cache", lambda *a, **k: None)
    asyncio.run(app.api_sector_members("kr"))
    asyncio.run(app.api_themes())   # 예외 없이 끝나면 통과


# ── 제약 2: 콜드 캐시 명시 ────────────────────────────────────────────
def test_sector_members_cold_cache_is_explicit(no_fetch, monkeypatch):
    monkeypatch.setattr(app, "_data_cache", {})
    monkeypatch.setattr(app, "_load_disk_cache", lambda *a, **k: None)
    r = asyncio.run(app.api_sector_members("kr"))
    assert r["ok"] is True
    assert r["cache_state"] == "cold"
    assert r["sectors"] == []
    assert r.get("message"), "콜드인데 안내 문구가 없다 — 조용한 빈 화면 금지"


def test_themes_cold_cache_still_lists_names(no_fetch, monkeypatch):
    """캐시가 없어도 테마·종목 목록 자체는 나와야 한다(가격만 None)."""
    monkeypatch.setattr(app, "_data_cache", {})
    monkeypatch.setattr(app, "_load_disk_cache", lambda *a, **k: None)
    r = asyncio.run(app.api_themes())
    assert r["ok"] is True and r["cache_state"] == "cold"
    assert len(r["themes"]) > 0
    row = r["themes"][0]["tickers"][0]
    assert row["name"] and row["price"] is None


# ── 제약 3: themes_kr.json fail-open, 단 시끄럽게 ──────────────────────
def test_themes_missing_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(app, "_THEMES_FILE", str(tmp_path / "nope.json"))
    r = asyncio.run(app.api_themes())
    assert r["ok"] is False and r["themes"] == []
    assert "없" in r["error"]
    assert "themes" in capsys.readouterr().out.lower(), "실패를 로그로 안 남겼다"


def test_themes_broken_json(monkeypatch, tmp_path, capsys):
    p = tmp_path / "themes_kr.json"
    p.write_text('{"themes": {"조선": {"tickers": [', encoding="utf-8")
    monkeypatch.setattr(app, "_THEMES_FILE", str(p))
    r = asyncio.run(app.api_themes())
    assert r["ok"] is False and r["themes"] == []
    assert "실패" in r["error"]
    assert "themes" in capsys.readouterr().out.lower()


def test_sector_layer_unaffected_by_broken_themes(warm_cache, no_fetch, monkeypatch, tmp_path):
    """테마 파일이 깨져도 업종 층은 정상 — 두 층이 독립인지."""
    monkeypatch.setattr(app, "_THEMES_FILE", str(tmp_path / "gone.json"))
    assert asyncio.run(app.api_themes())["ok"] is False
    s = asyncio.run(app.api_sector_members("kr"))
    assert s["ok"] is True and s["cache_state"] == "warm" and len(s["sectors"]) == 1


# ── 조립 로직 ─────────────────────────────────────────────────────────
def test_sector_members_shape_and_market_split(warm_cache, no_fetch):
    r = asyncio.run(app.api_sector_members("kr"))
    assert r["cache_state"] == "warm"
    assert len(r["sectors"]) == 1, "US 섹터가 KR 요청에 섞였다"
    sec = r["sectors"][0]
    assert sec["sector"] == "반도체" and sec["n"] == 2
    assert [m["ticker"] for m in sec["members"]] == ["005930.KS", "000660.KS"], "20일 수익률 내림차순"
    assert sec["leaders"][0]["qualifies"] is True
    assert sec["leaders"][1]["qualifies"] is None, "3상태(null)가 뭉개졌다"
    m = sec["members"][0]
    assert m["name"] == "삼성전자" and m["rs"] == 95 and m["above_ma200"] is True
    assert m["sector_rank"] == 1 and m["sector_total"] == 2


def test_ticker_stats_math():
    b = _fake_bundle()
    st = app._ticker_stats_from_bundle(b, "005930.KS")
    c = b["data"]["005930.KS"]["Close"]
    assert st["price"] == round(float(c.iloc[-1]), 2)
    assert st["ret20"] == round((float(c.iloc[-1]) / float(c.iloc[-21]) - 1) * 100, 2)
    assert st["ret60"] == round((float(c.iloc[-1]) / float(c.iloc[-61]) - 1) * 100, 2)
    # 200일선 위/아래: 우상향은 위, 평탄은 경계라 위가 아님
    assert app._ticker_stats_from_bundle(b, "000660.KS")["above_ma200"] is False


def test_short_history_returns_none_not_crash():
    b = _fake_bundle()
    b["data"]["SHORT.KS"] = _series([10.0] * 5)
    b["universe"]["SHORT.KS"] = "짧은종목"
    st = app._ticker_stats_from_bundle(b, "SHORT.KS")
    assert st["ret20"] is None and st["ret60"] is None and st["above_ma200"] is None
    assert st["price"] == 10.0


def test_theme_summary_and_leader(warm_cache, no_fetch, monkeypatch, tmp_path):
    p = tmp_path / "themes_kr.json"
    p.write_text(json.dumps({"themes": {"반도체": {"tickers": [
        {"t": "005930.KS", "n": "삼성전자"},
        {"t": "000660.KS", "n": "SK하이닉스"},
        {"t": "999999.KQ", "n": "유니버스밖종목"},
    ]}}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(app, "_THEMES_FILE", str(p))
    r = asyncio.run(app.api_themes())
    th = r["themes"][0]
    assert th["n"] == 3 and th["n_priced"] == 2, "유니버스 밖은 가격 집계에서 빠져야 한다"
    # 👑 = RS 1위(삼성전자 95 > SK하이닉스 40)
    crown = [x["ticker"] for x in th["tickers"] if x["theme_leader"]]
    assert crown == ["005930.KS"], crown
    out = [x for x in th["tickers"] if not x["in_universe"]][0]
    assert out["price"] is None and out["name"] == "유니버스밖종목"
    # 200일선 위 비율: 삼성전자만 위 → 50%
    assert th["pct_above_ma200"] == 50.0


def test_theme_median_even_count(warm_cache, no_fetch, monkeypatch, tmp_path):
    """짝수 표본 중앙값이 두 값의 평균인지(홀수 경로만 맞고 짝수가 틀린 버그 방지)."""
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"themes": {"X": {"tickers": [
        {"t": "005930.KS", "n": "a"}, {"t": "000660.KS", "n": "b"}]}}}), encoding="utf-8")
    monkeypatch.setattr(app, "_THEMES_FILE", str(p))
    th = asyncio.run(app.api_themes())["themes"][0]
    rets = sorted(x["ret20"] for x in th["tickers"] if x["ret20"] is not None)
    assert th["median_ret20"] == round((rets[0] + rets[1]) / 2, 2)


def test_real_themes_file_parses():
    """레포에 실제로 들어 있는 themes_kr.json이 라우트를 통과하는가."""
    if not os.path.exists(app._THEMES_FILE):
        pytest.skip("themes_kr.json 없음")
    r = asyncio.run(app.api_themes())
    assert r["ok"] is True, r.get("error")
    assert len(r["themes"]) >= 20
    assert sum(t["n"] for t in r["themes"]) >= 300
