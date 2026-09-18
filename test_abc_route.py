"""v5.267 — /api/abc 라우트와 실적 축.

이 파일이 지키는 두 가지:

1. **셀 포맷** — `_parse_kr_mobile`은 값을 `{"value": 2131.0, "est": false}`로 준다.
   dict를 그대로 비교하면 `TypeError: '>' not supported between 'dict' and 'int'`가
   났다(작성 중 실제로 발생). 그리고 `est=True`는 **추정치**라 실적이 아니다.
2. **판정 불가 ≠ 미달** — naver 모바일은 분기를 6개만 준다. "최근 4분기 YoY"에는
   8분기가 필요해 실제로 계산 가능한 건 2분기뿐이다. 이걸 "YoY+ 1/4분기 미달"로
   뭉개면 **없는 근거로 등급을 깎는다**. 분모(rev_yoy_of)가 기준에 못 미치면
   감점하지 않고, 대신 이유를 화면까지 내보낸다.
"""
import asyncio
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import abc_screener  # noqa: E402
import app  # noqa: E402

CFG = abc_screener.ABC_CONFIG


def cell(v, est=False):
    return {"value": float(v), "est": est}


def _parsed(rev, eps, n_annual=3):
    """`_parse_kr_mobile` 반환 형태 — 앞쪽 n_annual개가 연간, 뒤가 분기."""
    return {"n_annual": n_annual,
            "revenue": [cell(0)] * n_annual + rev,
            "eps": [cell(0)] * n_annual + eps}


@pytest.fixture
def stub(monkeypatch):
    """실적 조회 2회(annual/quarter)를 가로채고 파싱 결과만 주입한다."""
    def _install(parsed):
        monkeypatch.setattr(app.earnings_mod, "_fetch_kr_finance",
                            lambda *a, **k: {"stub": True})
        monkeypatch.setattr(app.earnings_mod, "_parse_kr_mobile",
                            lambda *a, **k: parsed)
    return _install


# ══════════════════════════════════════════════════════════════
# 1. 셀 언랩
# ══════════════════════════════════════════════════════════════

def test_dict_cells_do_not_raise_and_are_unwrapped(stub):
    """dict 셀을 숫자로 못 풀면 예외가 reason에 실려 나온다(조용한 0 아님)."""
    rev = [cell(100), cell(110), cell(120), cell(130), cell(140), cell(150),
           cell(160), cell(170)]
    eps = [cell(10)] * 8
    stub(_parsed(rev, eps))
    r = app._abc_quarterly_axes("005930.KS")
    assert "TypeError" not in (r["reason"] or ""), r
    assert r["rev_yoy_pos"] == 4 and r["rev_yoy_of"] == 4
    assert r["eps_pos_q"] == 2


def test_estimated_quarters_are_dropped(stub):
    """est=True는 **아직 안 나온 분기**다. 실적으로 세면 안 된다."""
    real = [cell(100), cell(110), cell(120), cell(130), cell(140)]
    with_est = real + [cell(999, est=True)]
    stub(_parsed(with_est, [cell(1)] * 5 + [cell(999, est=True)]))
    r = app._abc_quarterly_axes("005930.KS")
    # 실적 분기는 5개 → YoY 가능 1분기(140 vs 100)
    assert r["rev_yoy_of"] == 1, r
    assert r["rev_yoy_pos"] == 1

    stub(_parsed(real, [cell(1)] * 5))
    same = app._abc_quarterly_axes("005930.KS")
    assert same == r, "추정치가 결과를 바꿨다"


def test_missing_table_is_reported_not_guessed(stub):
    stub({"n_annual": 0, "revenue": [], "eps": []})
    r = app._abc_quarterly_axes("005930.KS")
    assert r["rev_yoy_pos"] is None and r["reason"] == "실적 표 없음"


def test_fetch_failure_surfaces_the_exception(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("naver 개편")
    monkeypatch.setattr(app.earnings_mod, "_fetch_kr_finance", boom)
    r = app._abc_quarterly_axes("005930.KS")
    assert "naver 개편" in r["reason"] and r["rev_yoy_pos"] is None


# ══════════════════════════════════════════════════════════════
# 2. 판정 불가 ≠ 미달
# ══════════════════════════════════════════════════════════════

def test_short_history_is_not_counted_as_a_failure(stub):
    """6분기(=YoY 2분기)뿐이면 매출 축은 감점하지 않는다."""
    rev = [cell(100), cell(100), cell(100), cell(100), cell(90), cell(90)]
    stub(_parsed(rev, [cell(5)] * 6))
    r = app._abc_quarterly_axes("005930.KS")
    assert r["rev_yoy_of"] < CFG["rev_yoy_min_quarters"]
    assert "판정 불가" in r["reason"]

    comp = abc_screener.company_axis(500, r["rev_yoy_pos"], r["eps_pos_q"],
                                     False, rev_yoy_of=r["rev_yoy_of"])
    assert not any("매출" in f for f in comp["fails"]), comp["fails"]


def test_full_history_below_threshold_still_fails():
    """분모가 충분한데 미달이면 **감점해야 한다** — 위 완화가 축을 죽이면 안 된다."""
    comp = abc_screener.company_axis(500, 1, 2, False,
                                     rev_yoy_of=CFG["rev_yoy_window"])
    assert any("매출" in f for f in comp["fails"]), comp["fails"]


def test_reason_is_carried_to_the_client():
    """이유가 응답에 없으면 화면에서 '왜 통과했는지' 알 수 없다."""
    src = Path(app.__file__).read_text(encoding="utf-8")
    i = src.index('@app.get("/api/abc")')
    body = src[i:src.index('@app.get("/api/debug/memory")')]
    assert '"fin_reason": f["reason"]' in body
    assert '"rev_yoy_of"' in body


# ══════════════════════════════════════════════════════════════
# 3. 플래그 파일
# ══════════════════════════════════════════════════════════════

def test_missing_flags_file_means_all_false(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "_ABC_FLAGS_FILE", str(tmp_path / "nope.json"))
    assert app._load_abc_flags() == {}


def test_broken_flags_file_logs_and_continues(monkeypatch, tmp_path, capsys):
    p = tmp_path / "abc_flags.json"
    p.write_text("{ broken", encoding="utf-8")
    monkeypatch.setattr(app, "_ABC_FLAGS_FILE", str(p))
    assert app._load_abc_flags() == {}
    assert "abc_flags.json 파싱 실패" in capsys.readouterr().out


def test_real_flags_file_parses():
    """레포에 든 파일이 실제로 읽히는가(형식이 어긋나면 조용히 빈 dict가 된다)."""
    doc = app._load_abc_flags()
    assert isinstance(doc, dict)


# ══════════════════════════════════════════════════════════════
# 4. 라우트
# ══════════════════════════════════════════════════════════════

def test_cold_cache_says_so_and_does_not_fetch(monkeypatch):
    monkeypatch.setattr(app, "_peek_market_bundle", lambda m: None)
    monkeypatch.setattr(app.naver_kr, "fetch_history",
                        lambda *a, **k: pytest.fail("콜드인데 네트워크를 탔다"))
    r = asyncio.run(app.api_abc())
    assert r["cache_state"] == "cold" and r["hits"] == []


def _synth(kind="abc"):
    """abc_screener 테스트와 같은 모양의 합성 봉.

    v5.268: 봉 수가 기준선 기간(600)보다 짧으면 전부 "MA600 불가"로 빠져
    이 테스트가 아무것도 검증하지 않게 된다 — 기간을 따라가게 만든다."""
    import abc_screener as _A
    import numpy as np
    span, lead = 45, 20
    flat = _A._min_bars() + 100
    base = [100.0] * flat
    lo = 100.0 * (1 - 0.45)
    down = list(np.linspace(100.0, lo, span // 2))
    up = list(np.linspace(lo, 100.0, span - span // 2))
    tail = [101.0] * lead
    c = base + down + up + tail
    idx = pd.bdate_range("2019-01-02", periods=len(c))
    return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c,
                         "Volume": [1_000_000.0] * len(c)}, index=idx)


def test_route_never_fetches_bars(monkeypatch):
    """캐시만 읽는다 — 스캔 밖에서 유니버스를 다시 긁으면 안 된다."""
    monkeypatch.setattr(app.naver_kr, "fetch_history",
                        lambda *a, **k: pytest.fail("번들 캐시만 봐야 한다"))
    monkeypatch.setattr(app, "_peek_market_bundle", lambda m: {
        "data": {"005930.KS": _synth()}, "universe": {"005930.KS": "합성"},
        "ts": 1_758_000_000})
    monkeypatch.setattr(app, "_abc_quarterly_axes", lambda t: {
        "rev_yoy_pos": None, "rev_yoy_of": 0, "eps_pos_q": None, "reason": None})
    r = asyncio.run(app.api_abc())
    assert r["cache_state"] == "warm"
    assert set(r["counts"]) == {"ABC", "다른 셋업", "ABC 아님", "MA600 불가"}
    assert sum(r["counts"].values()) == 1, r["counts"]
    assert r["counts"]["MA600 불가"] == 0, "600봉 넘는데 불가로 셌다"
    assert r["ma_label"] == "MA600"


def test_us_tickers_are_excluded(monkeypatch):
    monkeypatch.setattr(app, "_peek_market_bundle", lambda m: {
        "data": {"AAPL": _synth()}, "universe": {"AAPL": "Apple"}, "ts": 1})
    r = asyncio.run(app.api_abc())
    assert sum(r["counts"].values()) == 0, "KR 전용인데 US가 들어왔다"


def test_short_history_is_counted_not_graded(monkeypatch):
    """v5.268: 봉 부족 종목은 등급에서 빠지되 **카운트에는 남는다**."""
    short = _synth().iloc[-100:]
    monkeypatch.setattr(app, "_peek_market_bundle", lambda m: {
        "data": {"005930.KS": short}, "universe": {"005930.KS": "짧음"}, "ts": 1})
    r = asyncio.run(app.api_abc())
    assert r["counts"]["MA600 불가"] == 1, r["counts"]
    assert r["hits"] == [], "등급이 매겨졌다"


def test_config_is_exposed_for_the_ui():
    """화면이 '초기 임의값'을 보여주려면 값 자체가 응답에 있어야 한다."""
    src = Path(app.__file__).read_text(encoding="utf-8")
    i = src.index('@app.get("/api/abc")')
    assert '"config": abc_screener.ABC_CONFIG' in src[i:i + 4000]
