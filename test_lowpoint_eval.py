"""v5.317 — 저점 탭 "평가": 장기 체크리스트 자동 판정 · 단기 비교 · 저장.

합성 데이터로 각 자동 항목의 경계를 고정하고(네트워크 없음), 저장은 매매 기록과 같은 rev 규칙
(_rev_store_put 공용), 화면 집계·정렬은 production JS를 그대로 node로 실행한다.

사보타주 확인(2026-10-04, FAIL 확인 후 원복):
⑤ (v5.318 후속 (a)) warmed()의 36개월 제거를 없앰(첫 달부터 인정) → 36개월 경계·NKE 시딩 재현 테스트 FAIL
④ (v5.318 후속) 두 항목 분리 후 rise_stoch를 rise_rsi 값으로 덮어 결합처럼 만듦 → test_two_items_judged_separately FAIL
③ (v5.318 원문 기준) 의미있는 상승의 +30% 경계를 '상승이면 O'(rise > 0)로 바꿈 → test_rise_30pct_boundary 등 FAIL
② (v5.318) 서버 허용 키(LP_EVAL_MANUAL_KEYS)에서 ichimoku_cloud 제거 → 키 동기화·호환 테스트 등 3건 FAIL
① lowpoint_eval의 급등 판정을 신규상장 surge_check 호출 대신 사본(rolling 365D 직접 계산)으로 바꿈
   → test_surge_check_is_reused_not_copied FAIL
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "scripts", "screens"))
import lowpoint_eval as ev  # noqa: E402
import newlisting as nl  # noqa: E402

import app  # noqa: E402

KST = timezone(timedelta(hours=9))
SRC = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()


def _item(items, key):
    return next(i for i in items if i["key"] == key)


def _daily(closes, vols=None, start="2020-01-01", opens=None):
    idx = pd.bdate_range(start, periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": opens if opens is not None else c.values, "High": c * 1.01, "Low": c * 0.99,
                         "Close": c, "Volume": vols if vols is not None else [1000.0] * len(c)}, index=idx)


def _months(lows=None, closes=None, opens=None, n=None):
    n = n or len(lows or closes)
    idx = pd.date_range("2020-01-31", periods=n, freq="ME")
    closes = closes or [10.0] * n
    return pd.DataFrame({"Open": opens or closes, "High": [x * 1.1 for x in closes],
                         "Low": lows or [x * 0.9 for x in closes], "Close": closes, "Volume": [1.0] * n}, index=idx)


# ── ① 월봉 개수 ─────────────────────────────────────────────────────
@pytest.mark.parametrize("n,want", [(12, False), (13, True)])
def test_months_more_than_12(n, want):
    items = ev.long_checks(_daily([10.0] * 100), _months(n=n))
    assert _item(items, "months")["result"] is want


# ── ② 신고가 대비 하락 ───────────────────────────────────────────────
@pytest.mark.parametrize("last,want", [(50.0, True), (50.01, False)])
def test_drawdown_from_peak_boundary(last, want):
    items = ev.long_checks(_daily([100.0] + [80.0] * 30 + [last]), _months(n=13))
    assert _item(items, "drawdown")["result"] is want


def test_drawdown_uses_monthly_peak_when_daily_window_is_short():
    """코인처럼 일봉이 짧아도 월봉 최고 종가가 더 높으면 그걸 신고가로 쓴다."""
    m = _months(closes=[300.0] + [100.0] * 12)
    items = ev.long_checks(_daily([100.0] * 30), m)
    assert _item(items, "drawdown")["value"] == -66.67


# ── ③ RSI<30 다음 달 양봉 · ④ StochRSI 0 근접 ───────────────────────
# v5.318 후속: 신호는 데이터 첫 36개월 뒤부터만 인정 — 합성 데이터 앞에 36개월(작게 오르내림)을 붙인다
WARM = [100.0 + (1.0 if i % 2 else 0.0) for i in range(36)]


def _falling_then(next_open, next_close):
    closes = WARM + [100.0 * (0.9 ** i) for i in range(18)]
    opens = closes[:]
    closes.append(next_close)
    opens.append(next_open)
    return _months(closes=closes, opens=opens)


def test_rsi_rebound_requires_bullish_next_month():
    up = _falling_then(next_open=15.0, next_close=17.0)        # RSI<30 다음 달 양봉
    down = _falling_then(next_open=17.0, next_close=15.0)      # 다음 달 음봉
    d = _daily([10.0] * 100)
    assert _item(ev.long_checks(d, up), "rsi_rebound")["result"] is True
    assert _item(ev.long_checks(d, down), "rsi_rebound")["result"] is False


def _rally_then(drops):
    """상승(가끔 조정) 15개월 뒤 drops개월 연속 하락 — RSI가 움직여야 StochRSI가 정의된다
    (단조 하락이면 RSI가 0으로 일정해 0÷0)."""
    c = WARM + [100.0]
    for i in range(1, 16):
        c.append(c[-1] * (1.08 if i % 4 else 0.97))
    for _ in range(drops):
        c.append(c[-1] * 0.85)
    return _months(closes=c)


def test_stoch_zero_detects_bottom_close():
    d = _daily([10.0] * 100)
    assert _item(ev.long_checks(d, _rally_then(8)), "stoch_zero")["result"] is True
    rising = _months(closes=WARM + [100.0 * (1.06 if i % 3 else 0.97) ** i for i in range(30)])
    assert _item(ev.long_checks(d, rising), "stoch_zero")["result"] is False
    assert _item(ev.long_checks(d, _months(n=5)), "stoch_zero")["result"] is None     # 계산 구간 부족 = 미판정


# ── ⑤ 급등 — 신규상장 판정 재사용 ───────────────────────────────────
def test_surge_check_is_reused_not_copied():
    assert ev.nl.surge_check is nl.surge_check
    src = open(ev.__file__, encoding="utf-8").read()
    assert "nl.surge_check(c)" in src
    assert "365D" not in src and ".rolling(f\"{" not in src, "급등 판정 사본"


def test_surge_item_matches_newlisting_result():
    closes = [10.0] * 50 + [50.0] + [40.0] * 20       # 5배 → 급등 전력
    d = _daily(closes)
    ratio, _ = nl.surge_check(d["Close"])
    it = _item(ev.long_checks(d, _months(n=13)), "no_surge")
    assert it["value"] == ratio and it["result"] is (ratio < nl.SURGE_RATIO) and it["result"] is False


# ── ⑥ 저점 높이기 ───────────────────────────────────────────────────
@pytest.mark.parametrize("prior,recent,want", [(10.0, 10.0, True), (10.0, 10.5, True), (10.0, 9.99, False)])
def test_higher_lows_boundary(prior, recent, want):
    m = _months(lows=[prior] * 6 + [recent] * 6, closes=[20.0] * 12)
    assert _item(ev.long_checks(_daily([20.0] * 100), m), "higher_lows")["result"] is want


def test_higher_lows_needs_12_months():
    m = _months(lows=[10.0] * 11, closes=[20.0] * 11)
    assert _item(ev.long_checks(_daily([20.0] * 100), m), "higher_lows")["result"] is None


# ── ⑦ 20일선 · ⑨ 거래대금 ───────────────────────────────────────────
def test_tv_rising():
    d = _daily([10.0] * 79 + [12.0], vols=[100.0] * 60 + [200.0] * 20)
    items = ev.long_checks(d, _months(n=13))
    assert _item(items, "tv_rising")["result"] is True
    d2 = _daily([10.0] * 80, vols=[200.0] * 60 + [100.0] * 20)
    assert _item(ev.long_checks(d2, _months(n=13)), "tv_rising")["result"] is False


# ── ⑧ 매물대(일봉 종가×거래량 근사) ─────────────────────────────────
def test_volume_share_above_boundaries():
    # 현재가 100. 위 +30% 구간 = (100, 130]. 100(자기 자신)은 제외, 130은 포함, 130.01은 제외.
    d = _daily([100.0, 130.0, 130.01, 120.0, 90.0, 100.0], vols=[10.0, 20.0, 30.0, 40.0, 50.0, 50.0])
    assert ev.volume_share_above(d, 30) == round((20 + 40) / 200 * 100, 2)
    assert ev.volume_share_above(_daily([1.0, 1.0], vols=[0.0, 0.0]), 30) is None


@pytest.mark.parametrize("share_vol,want", [(20.0, True), (20.1, False)])
def test_light_overhead_threshold(share_vol, want):
    d = _daily([110.0, 50.0, 100.0], vols=[share_vol, 100.0 - share_vol - 1.0, 1.0])
    it = _item(ev.long_checks(d, _months(n=13)), "light_overhead")
    assert it["result"] is want
    assert ev.VP_UP30_MAX_PCT == 20.0 and ev.STOCH_NEAR_ZERO == 5.0      # 사용자 승인값(2026-10-04)


def test_completed_months_drops_current_month():
    m = _months(n=3)
    m.index = pd.to_datetime(["2026-08-31", "2026-09-30", "2026-10-31"])
    now = datetime(2026, 10, 4, 12, 0, tzinfo=KST)
    assert [d.month for d in ev.completed_months(m, "KR", now).index] == [8, 9]
    up = m.copy()
    up.index = pd.to_datetime(["2026-08-01", "2026-09-01", "2026-10-01"])   # 업비트 월봉 라벨(월초)
    assert [d.month for d in ev.completed_months(up, "UPBIT", now).index] == [8, 9]


# ── 단기 비교 ──────────────────────────────────────────────────────
def test_short_metrics_and_rank():
    d = _daily([100.0] * 20 + [110.0], vols=[10.0] * 16 + [20.0] * 5)
    m = ev.short_metrics(d)
    assert m["ret10_pct"] == 10.0
    assert m["tv5_chg_pct"] == round(((20 * 100 * 4 + 20 * 110) / 5 / (10 * 100) - 1) * 100, 2)
    assert m["atr_pct"] is not None
    rows = ev.rank_by_ret10([{"code": "A", "ret10_pct": 1.0}, {"code": "B", "ret10_pct": 5.0},
                             {"code": "C", "ret10_pct": None}])
    assert {r["code"]: r["rs_rank"] for r in rows} == {"A": 2, "B": 1, "C": None}


# ── 저장(매매 기록과 같은 rev 규칙) ──────────────────────────────────
class _Req:
    def __init__(self, body=None):
        self._body = body
        self.headers = {"user-agent": "pytest"}
        self.client = type("C", (), {"host": "127.0.0.1"})()

    async def json(self):
        return self._body


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LP_EVALS_PATH", str(tmp_path / "evals.json"))
    monkeypatch.setattr(app, "LP_EVALS_DELETE_LOG_PATH", str(tmp_path / "del.log"))
    return tmp_path


REC = {"id": "042000.KQ", "code": "042000.KQ", "name": "카페24", "mkt": "KR", "manual": {}, "memo": "",
       "interest": False, "auto": None, "checked_at": None}


def _put(rec, base):
    r = asyncio.run(app.lp_eval_put(rec["id"], _Req({"record": rec, "base_rev": base})))
    return r.status_code, json.loads(r.body)


def test_eval_save_reload_manual_and_rev(store):
    assert _put(REC, None)[0] == 200
    s, d = _put({**REC, "manual": {"rise_rsi": "O", "dilution": "X"}, "memo": "유증 2025", "interest": True,
                 "checked_at": "2026-10-04", "auto": {"items": [{"key": "months", "result": True}]}}, 1)
    assert s == 200 and d["record"]["rev"] == 2
    lst = json.loads(asyncio.run(app.lp_evals_list()).body)
    rec = lst["evals"][0]
    assert rec["manual"] == {"rise_rsi": "O", "dilution": "X"} and rec["memo"] == "유증 2025" and rec["interest"] is True
    assert [k for k, _ in lst["manual_items"]] == list(app.LP_EVAL_MANUAL_KEYS)
    assert _put({**REC, "memo": "낡은 탭"}, 1)[1]["code"] == "conflict"


def test_one_record_per_ticker_and_validation(store):
    assert _put({**REC, "id": "X"}, None)[0] == 400                          # id ≠ 코드
    assert _put({**REC, "manual": {"rise_rsi": "Y"}}, None)[0] == 400         # O|X|null만
    assert _put({**REC, "manual": {"other": "O"}}, None)[0] == 400            # 정해진 항목만
    assert _put({**REC, "id": "KRW-BTC", "code": "KRW-BTC", "mkt": "KR"}, None)[0] == 400
    assert _put({**REC, "id": "KRW-BTC", "code": "KRW-BTC", "mkt": "UPBIT"}, None)[0] == 200


def test_delete_logs(store):
    _put(REC, None)
    asyncio.run(app.lp_eval_delete("042000.KQ", _Req()))
    assert json.loads((store / "evals.json").read_text()) == []
    assert json.loads((store / "del.log").read_text().strip())["id"] == "042000.KQ"


def test_trades_and_evals_share_one_rev_rule():
    import inspect
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert src.count("def _rev_store_put(") == 1
    for fn, call in ((app.lp_trade_put, "_rev_store_put(trades,"), (app.lp_eval_put, "_rev_store_put(evals,")):
        body = inspect.getsource(fn)
        assert call in body and '"conflict"' not in body and '"gone"' not in body, f"{fn.__name__}: rev 규칙 사본"


# ── 화면(production JS 그대로 실행) ──────────────────────────────────
def _fn(name):
    start = SRC.index(f"function {name}(")
    i = SRC.index("{", SRC.index(")", start))
    dep = 0
    for j in range(i, len(SRC)):
        dep += {"{": 1, "}": -1}.get(SRC[j], 0)
        if dep == 0:
            return SRC[start:j + 1]
    raise AssertionError(name)


def _js(expr, *fns):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    p = subprocess.run(["node", "-e", "\n".join(_fn(f) for f in fns) + f"\nconsole.log(JSON.stringify({expr}));"],
                       capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


MANUAL = [["rise_rsi", "a"], ["long_base", "b"], ["dilution", "c"]]


def test_tally_counts_auto_and_manual():
    rec = {"auto": {"items": [{"result": True}, {"result": True}, {"result": False}, {"result": None}]},
           "manual": {"rise_rsi": "O", "dilution": "X"}}
    assert _js(f"lpeTally({json.dumps(rec)}, {json.dumps(MANUAL)})", "lpeManualEffective", "lpeTally") == {"O": 3, "X": 2, "blank": 2}
    assert _js(f"lpeTally({{}}, {json.dumps(MANUAL)})", "lpeManualEffective", "lpeTally") == {"O": 0, "X": 0, "blank": 3}


def test_manual_cycle():
    assert _js("[lpeCycle(null), lpeCycle('O'), lpeCycle('X')]", "lpeCycle") == ["O", "X", None]


def test_short_table_sort_nulls_last():
    rows = [{"code": "A", "atr_pct": 3.0}, {"code": "B", "atr_pct": None}, {"code": "C", "atr_pct": 5.0}]
    asc = _js(f"lpeSortRows({json.dumps(rows)}, 'atr_pct', 'asc').map(r => r.code)", "lpeSortRows")
    desc = _js(f"lpeSortRows({json.dumps(rows)}, 'atr_pct', 'desc').map(r => r.code)", "lpeSortRows")
    assert asc == ["A", "C", "B"] and desc == ["C", "A", "B"]


def test_ui_states_approximation_and_ai_values():
    body = _fn("renderLowpointEval")
    assert "일봉 종가×거래량 근사" in body and "승인한 기준" in body
    assert "lpSetView('${k}')" in _fn("renderLowpointTrack")
    assert "/api/lowpoint/resolve/" in _fn("lpeAdd"), "저점 매매 기록과 같은 종목 해석을 써야 한다"


def test_inputs_survive_rerender():
    """판정이 끝날 때마다 다시 그려도 입력 중인 종목·메모가 남아야 한다(로컬 화면 확인 중 입력이 지워진 사례)."""
    body = _fn("renderLowpointEval") + _fn("_lpeCard")
    assert 'value="${_escapeHtml(_lpe.draft)}" oninput="_lpe.draft=this.value"' in body
    assert "_lpe.memoDraft[rec.id] ?? rec.memo" in body


def test_version_badge_matches():
    import re
    m = re.search(r'id="verBadge">(v[\d.]+)<', SRC)
    assert m and m.group(1) == app.VERSION


# ── v5.318: 수동 항목 "파란구름(월봉 일목 구름)" 추가 ───────────────
def test_ichimoku_manual_item_added_and_keys_in_sync():
    keys = [k for k, _ in ev.MANUAL_ITEMS]
    assert keys == ["rise_rsi", "rise_stoch", "long_base", "dilution", "ichimoku_cloud"]
    assert tuple(keys) == app.LP_EVAL_MANUAL_KEYS
    assert dict(ev.MANUAL_ITEMS)["ichimoku_cloud"] == "파란구름의 두꺼운 구간을 충분히 지났다(월봉 일목 구름 기준)"


def test_old_record_without_new_key_is_compatible(store):
    """v5.317 레코드(새 키 없음)가 그대로 저장·로드되고, 새 항목은 미표시로 센다. 새 항목 O/X도 저장된다."""
    old = {**REC, "manual": {"rise_rsi": "O", "long_base": "X", "dilution": None}}
    assert _put(old, None)[0] == 200
    lst = json.loads(asyncio.run(app.lp_evals_list()).body)
    rec = lst["evals"][0]
    assert "ichimoku_cloud" not in rec["manual"]
    tally = _js(f"lpeTally({json.dumps(rec)}, {json.dumps(lst['manual_items'])})", "lpeManualEffective", "lpeTally")
    assert tally == {"O": 1, "X": 1, "blank": 3}           # rise_stoch·dilution·ichimoku_cloud 미표시
    s, d = _put({**rec, "manual": {**rec["manual"], "ichimoku_cloud": "O"}}, rec["rev"])
    assert s == 200 and d["record"]["manual"]["ichimoku_cloud"] == "O"
    tally2 = _js(f"lpeTally({json.dumps(d['record'])}, {json.dumps(lst['manual_items'])})", "lpeManualEffective", "lpeTally")
    assert tally2 == {"O": 2, "X": 1, "blank": 2}
    assert _put({**d["record"], "manual": {"ichimoku_cloud": "Z"}}, d["record"]["rev"])[0] == 400



# ── v5.318 원문 기준: ⑦ 월봉 20선 · 의미있는 상승 자동값 · 해당 없음(—) ──
@pytest.mark.parametrize("closes,want", [
    ([10.0] * 19, None),                       # 월봉 19개 → —
    ([10.0] * 19 + [11.0], True),              # 마지막 월봉 종가 > 20개월 평균
    ([10.0] * 20, False),                      # 같으면 위가 아니다(초과만 O)
    ([12.0] * 19 + [10.0], False),
])
def test_above_monthly_ma20(closes, want):
    it = _item(ev.long_checks(_daily([999.0] * 100), _months(closes=closes)), "above_ma20")
    assert it["result"] is want
    assert ev.MA_MONTHS == 20 and "월봉" in it["label"]


def _mask(flags):
    return pd.Series(flags)


@pytest.mark.parametrize("after,want", [(129.9, False), (130.0, True), (150.0, True)])
def test_rise_30pct_boundary(after, want):
    m = _months(closes=[200.0, 100.0, after, 90.0])
    r = ev.rise_after_signal(m, _mask([False, True, False, False]))
    assert r["month"] == "2020-02" and r["result"] is want


def test_rise_counts_only_after_signal_and_uses_last_ended_episode():
    # 신호 전 200은 무시, 진행 중인 마지막 구간(마지막 달 신호)은 빼고 그 전 끝난 구간을 본다
    m = _months(closes=[200.0, 100.0, 120.0, 80.0, 70.0])
    r = ev.rise_after_signal(m, _mask([False, True, False, True, True]))
    assert r["month"] == "2020-02" and r["rise_pct"] == 20.0 and r["result"] is False
    assert ev.rise_after_signal(m, _mask([False] * 5)) is None                  # 신호 없음 → —
    assert ev.rise_after_signal(m, _mask([False, False, False, True, True])) is None   # 진행 중뿐 → —


def test_manual_auto_na_rules():
    short = _months(n=10)
    ma = ev.manual_auto(short)
    assert ma["ichimoku_cloud"]["na"] is True                      # 52개 미만 — 구름 미형성
    assert ma["long_base"]["na"] is True                           # 폭등 없음
    assert ev.manual_auto(_months(n=52))["ichimoku_cloud"]["na"] is False
    assert ev.manual_auto(_months(n=51))["ichimoku_cloud"]["na"] is True


def _eff(rec, key):
    return _js(f"lpeManualEffective({json.dumps(rec)}, '{key}')", "lpeManualEffective")


def test_manual_override_over_auto_value():
    auto = {"manual_auto": {"rise_rsi": {"result": True, "na": False, "detail": "신호월 2024-03 · 이후 최대 +45%"},
                            "rise_stoch": {"result": False, "na": False, "detail": "신호월 2025-01 · 이후 최대 +5%"},
                            "long_base": {"result": None, "na": True, "detail": "폭등 없음"}}}
    e = _eff({"auto": auto, "manual": {}}, "rise_rsi")
    assert e["v"] is True and e["src"] == "auto"
    e = _eff({"auto": auto, "manual": {"rise_rsi": "X"}}, "rise_rsi")       # 항목별 덮어쓰기
    assert e["v"] == "X" and e["src"] == "manual" and "덮어씀" in e["detail"]
    e = _eff({"auto": auto, "manual": {"rise_rsi": "X"}}, "rise_stoch")     # 다른 항목은 그대로 자동
    assert e["v"] is False and e["src"] == "auto"
    e = _eff({"auto": auto, "manual": {"long_base": "O"}}, "long_base")      # 해당 없음이면 수동값 무시
    assert e["v"] is None and e["src"] == "na"


def test_na_is_blank_in_tally():
    rec = {"auto": {"items": [{"result": True}],
                    "manual_auto": {"rise_rsi": {"result": False, "na": False, "detail": ""},
                                    "rise_stoch": {"result": True, "na": False, "detail": ""},
                                    "long_base": {"result": None, "na": True, "detail": ""},
                                    "ichimoku_cloud": {"result": None, "na": True, "detail": ""}}},
           "manual": {"long_base": "O", "ichimoku_cloud": "X", "dilution": "O"}}
    manual = [[k, k] for k in app.LP_EVAL_MANUAL_KEYS]
    # O: items 1 + rise_stoch 자동 O + dilution 1 / X: rise_rsi 자동 X / 미표시: long_base·ichimoku(해당 없음)
    assert _js(f"lpeTally({json.dumps(rec)}, {json.dumps(manual)})", "lpeManualEffective", "lpeTally") == \
        {"O": 3, "X": 1, "blank": 2}


def test_override_saved_and_restored(store):
    rec = {**REC, "auto": {"items": [], "manual_auto": {"rise_rsi": {"result": True, "na": False, "detail": "x"}}},
           "manual": {"rise_rsi": "X"}}
    assert _put(rec, None)[0] == 200
    back = json.loads(asyncio.run(app.lp_evals_list()).body)["evals"][0]
    e = _eff(back, "rise_rsi")
    assert e["v"] == "X" and e["src"] == "manual"
    s, d = _put({**back, "manual": {"rise_rsi": None}}, back["rev"])           # 덮어쓰기 해제 → 자동값으로
    assert s == 200 and _eff(d["record"], "rise_rsi")["src"] == "auto"


def test_evaluate_returns_manual_auto_and_ui_saves_it():
    assert "manual_auto: a.manual_auto" in _fn("lpeEvaluate")
    assert '"manual_auto": manual_auto(months)' in open(ev.__file__, encoding="utf-8").read()



# ── v5.318 후속: 의미있는 상승 두 항목 분리 ─────────────────────────
def test_two_items_judged_separately(monkeypatch):
    seq = iter([{"month": "2016-11", "rise_pct": 256.7, "result": True},     # RSI<30
                {"month": "2026-06", "rise_pct": 1.6, "result": False}])     # StochRSI
    monkeypatch.setattr(ev, "rise_after_signal", lambda m, mask: next(seq))
    ma = ev.manual_auto(_months(n=60))
    assert ma["rise_rsi"]["result"] is True and ma["rise_rsi"]["signal_month"] == "2016-11"
    assert ma["rise_stoch"]["result"] is False and ma["rise_stoch"]["rise_pct"] == 1.6
    assert "신호월 2016-11" in ma["rise_rsi"]["detail"] and "+256.7%" in ma["rise_rsi"]["detail"]
    assert "rise_2x" not in ma, "결합 판정이 남아 있다"
    assert ma["long_base"]["na"] is False                     # 한 항목이라도 O면 폭등 있음


def test_no_signal_is_dash_for_each_item(monkeypatch):
    monkeypatch.setattr(ev, "rise_after_signal", lambda m, mask: None)
    ma = ev.manual_auto(_months(n=60))
    assert ma["rise_rsi"]["result"] is None and ma["rise_stoch"]["result"] is None
    assert ma["long_base"]["na"] is True


def test_legacy_combined_manual_value_is_kept_but_not_counted(store):
    """v5.318 첫 후속의 합산 수동값(rise_2x)이 있는 레코드도 저장·로드되고(400 아님), 집계엔 안 들어간다."""
    old = {**REC, "manual": {"rise_2x": "O", "dilution": "X"},
           "auto": {"items": [], "manual_auto": {"rise_2x": {"result": True, "na": False, "detail": "옛 합산"}}}}
    assert _put(old, None)[0] == 200
    lst = json.loads(asyncio.run(app.lp_evals_list()).body)
    rec = lst["evals"][0]
    assert rec["manual"]["rise_2x"] == "O"
    tally = _js(f"lpeTally({json.dumps(rec)}, {json.dumps(lst['manual_items'])})", "lpeManualEffective", "lpeTally")
    assert tally == {"O": 0, "X": 1, "blank": 4}            # rise_2x(수동 O·자동 O) 둘 다 무시, dilution X만
    s, d = _put({**rec, "memo": "다른 칸 수정"}, rec["rev"])
    assert s == 200
    assert "예전 합산 항목" in _fn("_lpeCard") and "rise_2x" in _fn("_lpeCard")
    assert app.LP_EVAL_LEGACY_MANUAL_KEYS == ("rise_2x",)



# ── v5.318 후속 (a): 데이터 시작 후 36개월 이후 신호만 인정 ───────────────
def test_signal_warmup_reuses_lowpoint_min_bars():
    import lowpoint as lp
    assert ev.SIGNAL_WARMUP_MONTHS == lp.MIN_BARS["month"] == 36


def test_warmup_boundary_36_months():
    flags = [False] * 40
    flags[35] = True                                  # 36번째 월봉(첫 36개월 안) → 무시
    assert not ev.warmed(pd.Series(flags)).any()
    flags[35], flags[36] = False, True                # 37번째 월봉 → 인정
    assert ev.warmed(pd.Series(flags)).tolist()[36] is True
    m = _months(closes=[100.0] * 36 + [100.0, 140.0, 120.0, 110.0])
    sig = pd.Series([False] * 36 + [True, False, False, False])
    r = ev.rise_after_signal(m, ev.warmed(sig))
    assert r["month"] == m.index[36].strftime("%Y-%m") and r["result"] is True
    early = pd.Series([False] * 35 + [True] + [False] * 4)
    assert ev.rise_after_signal(m, ev.warmed(early)) is None


def test_short_history_signals_are_dash():
    """월봉 36개 이하(신규상장·짧은 창)는 신호 항목이 모두 —(가짜 신호 대신 판정 불가)."""
    m = _months(closes=[100.0 * (0.85 ** i) for i in range(36)])     # 내내 하락 — 예전엔 RSI<30 신호가 떴다
    items = ev.long_checks(_daily([10.0] * 100), m)
    assert _item(items, "rsi_rebound")["result"] is None and _item(items, "stoch_zero")["result"] is None
    ma = ev.manual_auto(m)
    assert ma["rise_rsi"]["result"] is None and ma["rise_stoch"]["result"] is None and "신호 미인정" in ma["rise_rsi"]["detail"]


def test_nke_style_seeding_signal_is_gone():
    """NKE 10년 창 재현: 창 2번째 월봉이 하락이라 RSI=0(가짜 RSI<30) → 그 뒤 크게 오른 모양. 예전엔
    'RSI<30 이후 +250% O'가 나왔다. 이제 그 신호는 첫 36개월 안이라 무시되어 —."""
    closes = [60.0, 43.3] + [45.0 + 5.0 * (i % 2) for i in range(60)] + [150.0, 90.0, 60.0]
    m = _months(closes=closes)
    import scanner
    assert float(scanner.rsi(m["Close"], 14).iloc[1]) == 0.0          # 시딩 왜곡 재현
    assert ev.manual_auto(m)["rise_rsi"]["result"] is None
    assert _item(ev.long_checks(_daily([60.0] * 100), m), "rsi_rebound")["value"] == 0


def test_nke_live_fake_2016_11_signal_removed():
    """실데이터(10년 창): 2016-11은 창 2번째 월봉 — 더 이상 RSI 신호월로 잡히지 않는다."""
    import harness
    d = harness._fetch_us_batch(["NKE"], period=ev.lp.US_PERIOD["month"]).get("NKE")
    if d is None or d.empty:
        pytest.skip("yfinance 응답 없음(네트워크) — 원천 자체 실패")
    d = d[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
    m = ev.completed_months(ev.monthly_ohlc(d), "US", datetime.now(KST))
    ma = ev.manual_auto(m)
    assert ma["rise_rsi"]["signal_month"] != "2016-11"
    it = _item(ev.long_checks(d, m), "rsi_rebound")
    assert "2016-12" not in it["detail"]


# ── v5.319: 평가 카드 압축(다른 탭으로 안 새는지) ─────────────────────
def test_compact_rules_are_scoped_to_eval_card():
    import re
    style = SRC[SRC.index("<style>"):SRC.index("</style>")]
    rules = [r for r in re.findall(r"(?m)^[^\n{]*\blpe-[\w-]+[^\n{]*\{", style)]
    assert rules, "압축 규칙이 없다"
    for r in rules:
        sels = [s.strip() for s in r.rstrip("{").split(",")]
        assert all(s.startswith(".lpe-card") for s in sels), f"평가 카드 밖으로 샐 수 있는 선택자: {r}"
    # 공용 표·카드 기본값은 그대로
    assert "table.jr-table td{padding:16px 12px;" in SRC
    css = SRC[SRC.index(".lpe-card{"):SRC.index(".lpe-card .lpe-memo textarea{")]
    assert "#" not in re.sub(r"var\(--[\w-]+\)", "", css).replace("#lowpoint", ""), "하드코딩 색"


def test_eval_card_markup_uses_compact_classes():
    card = _fn("_lpeCard")
    assert 'class="n-card lpe-card"' in card and 'class="jr-table lpt-table lpe-table"' in card
    assert 'class="lpe-detail" title="${_escapeHtml(s)}" onclick="this.classList.toggle(\'open\')"' in card
    assert card.count("${det(") == 2                              # 자동·수동 행 모두 같은 근거 칸
    # 매매 기록 렌더는 압축 클래스를 쓰지 않는다
    assert "lpe-" not in _fn("renderLowpointTrack").split("if (view === 'eval')")[1].split("return;")[1]
