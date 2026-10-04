"""v5.321 — 저점 스크린·평가의 KR 가격 = KRX 정규장 기준(B안 하이브리드), 나머지 KR 경로는 naver 통합 시세 그대로.

배경(2026-10-04 조사): naver 일봉 종가는 애프터마켓(통합 시세) 마지막 체결가다. 인바이오젠(101140.KS) 10-02는
naver 5,230 / 정규장 4,820 — 주봉 A조건(직전 주 종가 < 최근 주 종가)이 뒤집혀 잘못 히트했다. naver에는 정규장
전용 옵션이 없어(공개 엔드포인트 3종·파라미터 11종 시도) yfinance(auto_adjust=False)를 쓴다. 표본 10종목의
yfinance 종가는 naver가 주는 KRX 공식 "전일 종가"와 10/10 일치했다. yfinance KR 과거 일봉은 하루 튐·수정주가
차이가 있어 사용자 확정 B안: NXT 개장(2025-03-04) 이전 naver + 이후 yfinance, 이음새 비율 검증(±0.5%) →
재조정 또는 경고 플래그(조용히 섞지 않는다).

실데이터 픽스처 test_fixtures/kr_seam_closes.json(2026-10-04 수집, 2024-11-01~2025-05-30 naver·yfinance 종가):
일양약품 007570.KS(비율 1.1187 → 재조정), 제이케이시냅스 060230.KQ(1.0488 → 재조정), 인바이오젠 101140.KS(1.0 → 그대로).

사보타주 확인(2026-10-04, FAIL 확인 후 원복):
① screen_market의 KR 조회를 fetch_kr_regular → fetch_kr(naver)로 되돌림 → test_kr_screen_uses_regular_close_not_after FAIL
② fetch_kr_regular_frames의 auto_adjust=False를 지움 → test_regular_fetch_requests_unadjusted_close FAIL
③ splice_regular의 재조정을 끔(rescaled 분기에서 pre를 그대로 이음) → 이음새 RSI 연속성(합성·실데이터) FAIL
④ 재조정 불가 분기가 경고 없이 status ok를 돌려줌 → test_unstable_ratio_is_flagged_not_mixed_silently FAIL
⑤ lpDisplayName이 market만 보게(평가 카드·단기 비교는 mkt) → test_display_name_us_ticker_kr_name FAIL
"""
from __future__ import annotations

import inspect
import json
import os
import shutil
import subprocess
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

import app  # noqa: E402

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 3, 9, 0, tzinfo=KST)


def _weekly_series(last_close: float) -> pd.Series:
    """주봉 하나 = 금요일 일봉 하나. 81주 무작위 보행 뒤 7주 연속 −4.5% → 1봉 전에서 RSI가 30을 막 하향돌파
    (RSI[2]=31.89 ≥ 30, RSI[1]=29.46 < 30 — B 통과, 0봉과 무관). 1봉 전 종가 5,391.4 — 0봉 종가만 기준별로
    다르게 넣으면 A(1봉전 < 0봉전)만 갈린다: 정규장 5,300(A 거짓) / 애프터 5,450(A 참). 인바이오젠 10-02 모양."""
    import numpy as np
    rng = np.random.default_rng(7)
    w = [10000.0]
    for _ in range(80):
        w.append(w[-1] * (1 + rng.normal(0.004, 0.04)))
    w += [w[-1] * (0.955 ** j) for j in range(1, 8)]
    w.append(last_close)
    fridays = pd.date_range(end="2026-10-02", periods=len(w), freq="W-FRI")
    return pd.Series(w, index=fridays)


def test_basis_constants_in_sync():
    assert lp.KR_PRICE_BASIS == app.LOWPOINT_KR_PRICE_BASIS == "krx_regular"


def test_kr_screen_uses_regular_close_not_after(monkeypatch):
    """같은 날 정규장(4,820)과 애프터(5,230)가 다를 때 — 스크린은 정규장 값으로 판정해야 한다.
    naver 경로(fetch_kr)가 불리면 실패."""
    monkeypatch.setattr(lp, "kr_universe", lambda board: ({"101140.KS": "인바이오젠"}, {}))
    monkeypatch.setattr(lp, "fetch_kr", lambda *a, **k: (_ for _ in ()).throw(AssertionError("naver 통합 시세 사용")))
    seen = {}

    def regular(tickers, tf):
        seen["called"] = (tuple(tickers), tf)
        return {"101140.KS": _weekly_series(5300.0)}, [], {"101140.KS": {"status": "ok", "ratio": 1.0}}
    monkeypatch.setattr(lp, "fetch_kr_regular", regular)
    res = lp.screen_market("kospi", "week", NOW)
    assert seen["called"] == (("101140.KS",), "week")
    ev_regular = lp.evaluate(_weekly_series(5300.0), "week", "kr", NOW)
    ev_after = lp.evaluate(_weekly_series(5450.0), "week", "kr", NOW)
    assert ev_after["status"] == "hit", "전제: 애프터 값이면 히트(인바이오젠과 같은 모양)"
    assert ev_regular["status"] == "no", "정규장 값이면 A가 거짓이라 히트 아님"
    assert res["rows"] == [], "스크린이 정규장이 아닌 값으로 판정했다"


def _ohlcv(idx, close):
    c = pd.Series(close, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1000.0}, index=idx)


@pytest.fixture
def fake_sources(monkeypatch):
    """naver(2024-06~2026-10, 값 ×1.25 — 수정주가 어긋남) + yfinance(같은 기간, 기준값). 호출 기록."""
    calls = {"naver": [], "yf": []}
    idx = pd.bdate_range("2024-06-03", "2026-10-02")
    base = pd.Series(range(len(idx)), index=idx, dtype=float) * 0.5 + 1000

    def nv(t, days=730):
        calls["naver"].append((t, days))
        return _ohlcv(idx, base * 1.25)

    def yf(tickers, period="2y", auto_adjust=True):
        calls["yf"].append((tuple(tickers), period, auto_adjust))
        return {t: _ohlcv(idx, base) for t in tickers}
    import naver_kr
    monkeypatch.setattr(naver_kr, "fetch_history", nv)
    monkeypatch.setattr(harness, "_fetch_us_batch", yf)
    return calls, base


def test_regular_fetch_requests_unadjusted_close(fake_sources):
    calls, base = fake_sources
    data, failed, flags = lp.fetch_kr_regular(["005930.KS", "042000.KQ"], "month")
    assert calls["yf"] == [(("005930.KS", "042000.KQ"), lp.US_PERIOD["month"], False)]
    assert sorted(calls["naver"]) == [("005930.KS", lp.KR_DAYS["month"]), ("042000.KQ", lp.KR_DAYS["month"])]
    assert set(data) == {"005930.KS", "042000.KQ"} and failed == []
    assert flags["005930.KS"]["status"] == "rescaled" and flags["005930.KS"]["ratio"] == 1.25
    # 재조정 후 이은 시리즈 = yfinance 기준값 그대로(경계 앞도 뒤도)
    pd.testing.assert_series_equal(data["005930.KS"], base, check_names=False, check_freq=False)


def test_eval_page_kr_uses_same_hybrid(fake_sources, monkeypatch):
    calls, base = fake_sources
    daily, monthly, note = ev.fetch_ohlcv_noted("101140.KS", "KR")
    assert calls["naver"] and calls["yf"][0][2] is False
    assert note == lp.price_note({"status": "rescaled", "ratio": 1.25}) and "1.25" in note
    assert float(daily["Close"].iloc[0]) == pytest.approx(float(base.iloc[0]))
    assert float(daily["Volume"].iloc[0]) == pytest.approx(1250.0)       # 거래량은 비율만큼 곱한다(거래대금 보존)
    rows = ev.short_table([{"code": "101140.KS", "market": "KOSPI"}, {"code": "NKE", "market": "US"}])
    by = {r["code"]: r for r in rows}
    assert by["101140.KS"]["price_note"] and by["NKE"]["price_note"] is None
    assert (("NKE",), lp.US_PERIOD["month"], True) in calls["yf"]
    assert all(a is False for t, p, a in calls["yf"] if t[0].endswith((".KS", ".KQ")))
    assert "lp.fetch_kr_regular_frames" in inspect.getsource(ev.fetch_ohlcv_noted)
    assert "lp.fetch_kr_regular_frames" in inspect.getsource(ev.short_table)


def test_eval_result_carries_price_note(fake_sources):
    res = ev.evaluate("101140.KS", "KR", datetime(2026, 10, 4, 12, 0, tzinfo=KST))
    assert res["ok"] and "재조정" in res["price_note"]


# ── 이음새(2025-03-04) ────────────────────────────────────────────
def _seam_case(nv_mult, pre_noise=None, n_pre=60, n_post=60):
    idx = pd.bdate_range(end=lp.NXT_START - pd.Timedelta(days=1), periods=n_pre).append(
        pd.bdate_range(start=lp.NXT_START, periods=n_post))
    import numpy as np
    rng = np.random.default_rng(3)
    true = pd.Series(1000 * np.cumprod(1 + rng.normal(0, 0.02, len(idx))), index=idx)
    nv = true[true.index < lp.NXT_START] * nv_mult
    if pre_noise is not None:
        nv = nv * pre_noise(nv.index)
    return true, nv.to_frame("Close"), true.to_frame("Close")


@pytest.mark.parametrize("mult,status", [(1.0, "ok"), (1.004, "ok"), (1.25, "rescaled"), (0.8, "rescaled"), (1.006, "rescaled")])
def test_splice_status_by_ratio(mult, status):
    true, nv, rg = _seam_case(mult)
    out, info = lp.splice_regular(nv, rg)
    assert info["status"] == status and info["ratio"] == pytest.approx(mult, abs=1e-4)
    assert out.index.is_monotonic_increasing and not out.index.duplicated().any()
    assert out.index[0] == nv.index[0] and out.index[-1] == rg.index[-1]
    assert (out.index < lp.NXT_START).sum() == len(nv)                  # 경계 이전은 naver 날짜


def test_post_seam_values_are_regular_close():
    """경계부터는 yfinance(정규장) 값 — naver 값이 섞이면 안 된다."""
    true, nv, rg = _seam_case(1.0)
    nv_post = pd.DataFrame({"Close": 99999.0}, index=rg.index[rg.index >= lp.NXT_START])
    out, _ = lp.splice_regular(pd.concat([nv, nv_post]), rg)
    post = out[out.index >= lp.NXT_START]["Close"]
    pd.testing.assert_series_equal(post, true[true.index >= lp.NXT_START], check_names=False, check_freq=False)


@pytest.mark.parametrize("mult", [1.0, 1.1187, 1.25])
def test_rsi_continuous_across_seam_synthetic(mult):
    """경계 전후 14봉 창 — 이은 시리즈의 RSI가 끊김 없는 원래 시리즈의 RSI와 같다(인위적 점프 없음)."""
    import scanner
    true, nv, rg = _seam_case(mult)
    out, _ = lp.splice_regular(nv, rg)
    i = out.index.get_indexer([lp.NXT_START])[0]
    r_out, r_true = scanner.rsi(out["Close"], 14), scanner.rsi(true, 14)
    assert (r_out - r_true).iloc[i - 14:i + 14].abs().max() < 1e-6
    rets = out["Close"].pct_change().iloc[i - 14:i + 14]
    assert rets.abs().max() == pytest.approx(true.pct_change().iloc[i - 14:i + 14].abs().max())
    if mult != 1.0:   # 전제: 재조정 없이 그냥 이었으면 경계에서 점프가 난다(이 테스트가 탐지력이 있다)
        naive = pd.concat([nv["Close"], rg["Close"][rg.index >= lp.NXT_START]])
        assert abs(naive.iloc[i] / naive.iloc[i - 1] - true.iloc[i] / true.iloc[i - 1]) > 0.05
        assert (scanner.rsi(naive, 14) - r_true).iloc[i:i + 14].abs().max() > 3


SEAM = json.load(open(os.path.join(ROOT, "test_fixtures", "kr_seam_closes.json"), encoding="utf-8"))


def _fixture(t):
    f = lambda d: pd.DataFrame({"Close": pd.Series(d, dtype=float)}).set_axis(pd.to_datetime(list(d)), axis=0)
    return f(SEAM[t]["naver"]), f(SEAM[t]["regular"])


@pytest.mark.parametrize("t,status,ratio", [("007570.KS", "rescaled", 1.1187), ("060230.KQ", "rescaled", 1.0488),
                                            ("101140.KS", "ok", 1.0)])
def test_rsi_continuous_across_seam_real(t, status, ratio):
    """실데이터: 이은 시리즈의 경계일 수익률 = naver 자체 시리즈의 경계일 수익률(±0.1%p), RSI는 경계 ±14봉에서
    naver 자체 RSI와 0.5 이내(naver는 경계 앞뒤 한 소스라 이음새가 없다 — 경계 직후엔 애프터 차이만 남는다)."""
    import scanner
    nv, rg = _fixture(t)
    out, info = lp.splice_regular(nv, rg)
    assert info["status"] == status and info["ratio"] == pytest.approx(ratio, abs=1e-4)
    i = out.index.get_indexer([lp.NXT_START])[0]
    j = nv.index.get_indexer([lp.NXT_START])[0]
    r_sp = out["Close"].iloc[i] / out["Close"].iloc[i - 1] - 1
    r_nv = nv["Close"].iloc[j] / nv["Close"].iloc[j - 1] - 1
    assert abs(r_sp - r_nv) < 0.001
    d = (scanner.rsi(out["Close"], 14) - scanner.rsi(nv["Close"], 14).reindex(out.index)).iloc[i - 14:i + 14].abs()
    assert d.max() < 0.5
    if status == "rescaled":   # 전제: 재조정 없이 이으면 경계에서 비율만큼 점프한다
        naive = pd.concat([nv["Close"][nv.index < lp.NXT_START], rg["Close"][rg.index >= lp.NXT_START]])
        assert abs(naive.iloc[i] / naive.iloc[i - 1] - 1 - r_nv) > (ratio - 1) * 0.8


def test_unstable_ratio_is_flagged_not_mixed_silently():
    """경계 앞 창에서 비율이 바뀌면(한 배수로 재조정할 근거가 없다) 그대로 잇되 unverified 경고."""
    true, nv, rg = _seam_case(1.0, pre_noise=lambda idx: [1.0] * (len(idx) - 10) + [1.2] * 10)
    out, info = lp.splice_regular(nv, rg)
    assert info["status"] == "unverified" and "일정하지 않음" in info["why"]
    assert "이음새 미검증" in lp.price_note(info)


def test_insufficient_overlap_is_flagged():
    true, nv, rg = _seam_case(1.0)
    rg_short = rg[rg.index >= lp.NXT_START - pd.Timedelta(days=4)]       # 경계 앞 공통일 2일뿐
    out, info = lp.splice_regular(nv, rg_short)
    assert info["status"] == "unverified" and "공통 거래일" in info["why"] and lp.price_note(info)


def test_listed_after_seam_and_missing_regular():
    true, nv, rg = _seam_case(1.0)
    post_nv = rg[rg.index >= lp.NXT_START] * 1.3
    out, info = lp.splice_regular(post_nv, rg[rg.index >= lp.NXT_START])
    assert info["status"] == "regular_only" and lp.price_note(info) is None
    assert float(out["Close"].iloc[-1]) == pytest.approx(float(true.iloc[-1]))   # 정규장 값
    out, info = lp.splice_regular(nv, None)
    assert info["status"] == "no_regular" and "naver 통합 시세" in lp.price_note(info)
    assert lp.splice_regular(None, None)[0] is None


def test_seam_flags_reach_rows_and_publish(monkeypatch):
    monkeypatch.setattr(lp, "kr_universe", lambda board: ({"007570.KS": "일양약품", "005930.KS": "삼성전자"}, {}))
    monkeypatch.setattr(lp, "fetch_kr_regular", lambda tickers, tf: (
        {"007570.KS": _weekly_series(5450.0), "005930.KS": _weekly_series(5450.0)}, [],
        {"007570.KS": {"status": "rescaled", "ratio": 1.1187}, "005930.KS": {"status": "ok", "ratio": 1.0}}))
    res = lp.screen_market("kospi", "week", NOW)
    notes = {r["코드"]: r["데이터경고"] for r in res["rows"]}
    assert notes == {"007570.KS": "수정주가 재조정 ×1.1187(naver↔정규장 이음새)", "005930.KS": None}
    assert res["seam_counts"] == {"rescaled": 1, "ok": 1}
    entry = lp.publish_entry([res], "week", {"kospi": pd.Timestamp("2026-10-02")}, {})
    assert {r["code"]: r["price_note"] for r in entry["rows"]} == {"007570.KS": notes["007570.KS"], "005930.KS": None}
    assert entry["excluded_counts"]["KOSPI"]["seam"] == {"rescaled": 1, "ok": 1}
    lines = "\n".join(lp.exclusion_detail_lines(res, "week"))
    assert "이음새(2025-03-04) 상태" in lines and "007570.KS(일양약품)" in lines


def test_other_kr_paths_still_use_naver_integrated_price():
    """5탭 스캐너·현재가·종가베팅·신규상장은 naver 통합 시세 그대로(측정 기반 데이터 정의 보존)."""
    assert "naver_kr.fetch(ticker)" in inspect.getsource(app._fetch)                     # 스캐너 일봉
    assert "naver_kr.fetch_history(tk, days=10)" in inspect.getsource(app.batch_prices)   # 현재가 갱신
    assert "naver_kr.fetch_history(ticker)" in inspect.getsource(app._jongga_bars)        # 종가베팅 보충
    assert "lp.fetch_kr(kr, \"week\")" in inspect.getsource(nl._attach_closes)            # 신규상장 종가
    assert "def fetch_kr(" in inspect.getsource(lp) and "naver_kr.fetch_history" in inspect.getsource(lp.fetch_kr)


# ── 서버: 가격 기준이 바뀐 결과는 창 안에서 다시 돈다 ──────────────────────
@pytest.mark.parametrize("state_basis,now,due", [
    (None, "2026-10-04 12:00", "2026-10-02"),            # 예전(naver) 기준 결과 · 창 안 → 다시
    ("krx_regular", "2026-10-04 12:00", None),           # 이미 새 기준 → 안 돈다
    (None, "2026-10-05 09:01", None),                    # 창(토 09:00 + 48시간) 밖 → 다음 예약 때
])
def test_week_rerun_when_basis_changed(state_basis, now, due):
    st = {"week": {"target": "2026-10-02", "status": "ok", "attempts": 1, **({"basis": state_basis} if state_basis else {})}}
    t = datetime.fromisoformat(now).replace(tzinfo=KST)
    assert app._lowpoint_due("week", t, st) == due


def test_newlisting_not_affected_by_basis():
    st = {"month": {"target": "2026-09-30", "status": "ok", "attempts": 1, "basis": "krx_regular"},
          "newlisting": {"target": "2026-09-30", "status": "ok", "attempts": 1}}
    assert app._lowpoint_due("newlisting", datetime(2026, 10, 1, 9, 0, tzinfo=KST), st) is None


# ── UI: 미국 종목은 티커, KR 이름 그대로 · 경고 표시 ──────────────────────
SRC = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()


def _fn(name):
    start = SRC.index(f"function {name}(")
    i = SRC.index("{", SRC.index(")", start))
    d = 0
    for j in range(i, len(SRC)):
        d += {"{": 1, "}": -1}.get(SRC[j], 0)
        if d == 0:
            return SRC[start:j + 1]
    raise AssertionError(name)


def _js(expr):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    pre = "const _escapeHtml = s => String(s);\n" + _fn("lpDisplayName") + "\n" + _fn("lpPriceNoteMark")
    p = subprocess.run(["node", "-e", pre + f"\nconsole.log(JSON.stringify({expr}));"], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_display_name_us_ticker_kr_name():
    rows = [{"market": "US", "code": "HRTX", "name": "Heron Therapeutics, Inc. - Common Stock"},
            {"mkt": "US", "code": "NKE", "name": "Nike, Inc."},
            {"market": "KOSDAQ", "code": "101140.KS", "name": "인바이오젠"},
            {"mkt": "KR", "code": "005930.KS", "name": "삼성전자"},
            {"mkt": "UPBIT", "code": "KRW-XRP", "name": "엑스알피(리플)"},
            {"market": "KOSPI", "code": "000001.KS", "name": ""}]
    assert _js(f"{json.dumps(rows)}.map(lpDisplayName)") == ["HRTX", "NKE", "인바이오젠", "삼성전자", "엑스알피(리플)", "000001.KS"]


def test_price_note_mark():
    assert _js("[lpPriceNoteMark(null), lpPriceNoteMark('x')]") == ["", ' <span class="st-dot st-bad" style="font-size:11px" title="데이터 경고 — x">⚠</span>']


def test_display_name_used_in_all_lowpoint_surfaces():
    """홈 칩 · 평가 히트 칩(추가 전/후) · 평가 카드 · 단기 비교 — 5곳."""
    assert SRC.count("lpDisplayName(r)") + SRC.count("lpDisplayName(rec)") == 6      # 정의 1 + 호출 5
    assert "const name = lpDisplayName(r);" in _fn("_lowpointRowHtml")
    assert _fn("renderLowpointEval").count("lpDisplayName(r)") == 2
    assert "lpDisplayName(rec)" in _fn("_lpeCard") and "lpDisplayName(r)" in _fn("_lpeShortHtml")
    assert "_escapeHtml(r.name || r.code)} ✓" not in SRC and "+ ${_escapeHtml(r.name || r.code)}" not in SRC


def test_price_note_shown_on_every_surface():
    assert "r.price_note" in _fn("_lowpointRowHtml")
    assert _fn("renderLowpointEval").count("r.price_note") >= 2
    assert _fn("_lpeCard").count("rec.auto.price_note") >= 2
    assert "lpPriceNoteMark(r.price_note)" in _fn("_lpeShortHtml")
    assert "price_note: a.price_note || null" in _fn("lpeEvaluate")
