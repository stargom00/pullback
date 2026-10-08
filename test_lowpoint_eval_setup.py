"""v5.344 — 저점 평가: 희석 방향 반전 이관 + 유형별 체크리스트(바닥형/눌림형) + 관찰 중 "무효 참고".

사용자 지시 요지:
1) "평가 체크리스트 '희석 이력(유증·CB)'만 O = 나쁜 조건이라 합계 방향이 반대. 집계 오류." → "희석 이력 없음"(O = 없음),
   기존 레코드 값 반전 이관(O↔X, 미표시 그대로), 앱 정상 경로, 전후 목록 로그.
2) "평가 체크리스트는 '긴 하락 끝 바닥'용이라 우상향 속 조정(눌림형)에는 맞지 않음 … v5.343의 유형(바닥형/눌림형)에 맞춰
   체크리스트를 둘로 나눈다. 새 숫자 임계값 금지." 눌림형 ④ 직전 저점 = 최근 250봉(ABC A 기간) 최고 종가일 직전 250봉 최저 종가.
3) 관찰 중 "무효 참고"(표시 전용): 바닥형 = ABC A 저점, 눌림형 = ④ 직전 저점(같은 함수), 판정 불가 = —.

사보타주 확인(2026-10-09, 전부 FAIL 확인 후 원복):
① 이관에서 반전 누락(옛 값을 그대로 복사) → test_dilution_migration_flips_and_is_idempotent · test_dilution_tally_direction FAIL
② 직전 저점 구간을 고점 이후로 잡음 → test_prior_low_window_fixed · test_prior_low_real_peg FAIL
②' (구간 정의 변경 후) 구간을 직전 250봉으로 되돌림 → test_prior_low_window_fixed · test_prior_low_real_peg ·
    test_peg_pullback_and_hidip_bottom_checklists · test_watch_ref_low_bottom_and_pullback FAIL
③ (무효 참고) 바닥형에 A 저점 대신 직전 저점 → test_watch_ref_low_bottom_and_pullback FAIL
"""
from __future__ import annotations

import asyncio
import gzip
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
import lowpoint_eval as ev  # noqa: E402
import lowpoint_watch as w  # noqa: E402

import app  # noqa: E402

KST = timezone(timedelta(hours=9))
SRC = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
FX = json.load(gzip.open(os.path.join(ROOT, "test_fixtures", "lowpoint_setup_20261009.json.gz"), "rt", encoding="utf-8"))["tickers"]


def _df(code):
    t = FX[code]
    return pd.DataFrame({k: [float("nan") if v is None else v for v in t[k]] for k in ("Open", "High", "Low", "Close", "Volume")},
                        index=pd.to_datetime(t["dates"]))


def _fn(name):
    start = SRC.index(f"function {name}(")
    i = SRC.index("{", SRC.index(")", start))
    d = 0
    for j in range(i, len(SRC)):
        d += {"{": 1, "}": -1}.get(SRC[j], 0)
        if d == 0:
            return SRC[start:j + 1]
    raise AssertionError(name)


def _js(expr, fns):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    p = subprocess.run(["node", "-e", "\n".join(_fn(f) for f in fns) + f"\nconsole.log(JSON.stringify({expr}));"],
                       capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


TALLY = ("lpeManualEffective", "lpeSetupOf", "lpeChecklist", "lpeTally")


# ── 수정 1: 희석 방향 ──────────────────────────────────────────────
def test_label_and_all_other_items_are_good_when_O():
    assert dict(ev.MANUAL_ITEMS)["no_dilution"] == "희석 이력 없음(유증·CB)" and "dilution" not in dict(ev.MANUAL_ITEMS)
    assert ev.ITEM_TIPS["rise_rsi"] == ev.ITEM_TIPS["rise_stoch"] == "바닥 신호 뒤 실제 반등(+30%)이 나왔는지. 월봉 기준."
    assert "no_dilution" in ev.manual_auto(None) and "dilution" not in ev.manual_auto(None)


def _evals(recs):
    app._rec_list_write(app.LP_EVALS_PATH, recs)


@pytest.fixture
def evals_store(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "LP_EVALS_PATH", str(tmp_path / "lowpoint_evals.json"))
    monkeypatch.setattr(app, "LP_EVALS_DELETE_LOG_PATH", str(tmp_path / "lowpoint_evals_deletions.log"))
    return tmp_path


def _rec(rid, manual, rev=1):
    return {"id": rid, "code": rid, "name": rid, "mkt": "KR", "manual": manual, "memo": "", "interest": False, "auto": None,
            "rev": rev, "updated_at": "2026-10-01T00:00:00+09:00"}


def test_dilution_migration_flips_and_is_idempotent(evals_store, capsys):
    _evals([_rec("A", {"dilution": "O", "rise_rsi": "O"}), _rec("B", {"dilution": "X"}), _rec("C", {"dilution": None}),
            _rec("D", {"long_base": "X"}), _rec("E", {"dilution": "O", "no_dilution": "O"})])
    out = app._lp_eval_migrate_dilution()
    by = {r["id"]: r for r in app._rec_list_load(app.LP_EVALS_PATH)}
    assert by["A"]["manual"] == {"rise_rsi": "O", "no_dilution": "X"} and by["A"]["rev"] == 2
    assert by["B"]["manual"] == {"no_dilution": "O"} and by["C"]["manual"] == {"no_dilution": None}
    assert by["D"]["manual"] == {"long_base": "X"} and by["D"]["rev"] == 1                     # 옛 키 없는 레코드는 그대로
    assert by["E"]["manual"] == {"no_dilution": "O"}                                            # 새 키가 이미 있으면 그 값 유지
    log = capsys.readouterr().out
    assert "희석 방향 이관(희석 이력 → 희석 이력 없음, O↔X) 3건: A O→X, B X→O, C 미표시→미표시" in log
    assert "옛 값만 지움 1건: E(옛 O 버림 · 새 O 유지)" in log
    assert app._lp_eval_migrate_dilution() == {"moved": [], "kept": []}                        # 멱등 — 다시 뒤집지 않는다
    assert "이관(희석 이력 → 희석 이력 없음, O↔X) 0건: 없음" in capsys.readouterr().out


def test_dilution_tally_direction(evals_store):
    """희석 '있음'(옛 O)은 합계에서 X로 센다 — 이관 전엔 O로 세져 합계가 거꾸로였다."""
    _evals([_rec("A", {"dilution": "O"})])
    before = _js(f"lpeTally({json.dumps(_rec('A', {'dilution': 'O'}))}, {json.dumps([['dilution', 'old']])})", TALLY)
    app._lp_eval_migrate_dilution()
    rec = app._rec_list_load(app.LP_EVALS_PATH)[0]
    after = _js(f"lpeTally({json.dumps(rec)}, {json.dumps([[k, k] for k, _ in ev.MANUAL_ITEMS])})", TALLY)
    assert before == {"O": 1, "X": 0, "blank": 0}                                               # 옛 집계: 나쁜 조건이 O
    assert after["O"] == 0 and after["X"] == 1                                                  # 이관 후: X(나쁨)


def test_old_dilution_key_rejected_on_put(evals_store):
    class R:
        def __init__(self, b): self._b = b
        async def json(self): return self._b
    rec = {**_rec("A", {"dilution": "O"}), "rev": None}
    r = asyncio.run(app.lp_eval_put("A", R({"record": rec, "base_rev": None})))
    assert r.status_code == 400 and "희석 이력 없음" in json.loads(r.body)["error"]


# ── 수정 2: 유형별 체크리스트 ────────────────────────────────────────
def _evaluate(code, mkt, monkeypatch):
    d = _df(code)
    monkeypatch.setattr(ev, "fetch_ohlcv", lambda c, m: (d, ev.monthly_ohlc(d)))
    return ev.evaluate(code, mkt, datetime(2026, 10, 9, 7, 0, tzinfo=KST))


def test_peg_pullback_and_hidip_bottom_checklists(monkeypatch):
    peg = _evaluate("PEG", "US", monkeypatch)
    hi = _evaluate("365590.KQ", "KR", monkeypatch)
    assert peg["setup"]["setup_type"] == "pullback" and hi["setup"]["setup_type"] == "bottom"
    assert [i["key"] for i in peg["pullback_items"]] == [k for k, _ in ev.PULLBACK_ITEMS]
    assert [i["key"] for i in hi["items"]] == [k for k, _ in ev.LONG_ITEMS]                    # 바닥형 체크리스트 그대로
    pl = next(i for i in peg["pullback_items"] if i["key"] == "above_prior_low")
    assert pl["result"] is False and pl["prior_low"]["low_close"] == pytest.approx(77.43, abs=0.01)   # 71.8 < 직전 저점
    # 재사용 항목은 바닥형 결과와 같은 값(사본 아님)
    base = {i["key"]: i for i in peg["items"]}
    for k in ("above_ma20", "higher_lows", "light_overhead"):
        it = next(i for i in peg["pullback_items"] if i["key"] == k)
        assert (it["result"], it["value"], it["detail"]) == (base[k]["result"], base[k]["value"], base[k]["detail"])
    # 화면: 저장된 auto로 유형에 맞는 체크리스트·수동 목록을 고른다
    recs = {c: {"auto": {"items": r["items"], "pullback_items": r["pullback_items"], "setup": r["setup"], "manual_auto": r["manual_auto"]},
                "manual": {}} for c, r in (("PEG", peg), ("HI", hi))}
    got = _js(f"""(() => {{ const M = {json.dumps(ev.MANUAL_ITEMS)}, P = {json.dumps(ev.PULLBACK_MANUAL_ITEMS)}, R = {json.dumps(recs)};
        return ['PEG','HI'].map(c => {{ const cl = lpeChecklist(R[c], M, P); return [cl.setup.eff, cl.autoItems.map(i => i.key), cl.manual.map(m => m[0])]; }}); }})()""",
              TALLY)
    assert got[0] == ["pullback", [k for k, _ in ev.PULLBACK_ITEMS], ["no_dilution", "not_loss"]]
    assert got[1] == ["bottom", [k for k, _ in ev.LONG_ITEMS], [k for k, _ in ev.MANUAL_ITEMS]]


def test_prior_low_real_peg():
    pl = ev.prior_low(_df("PEG")["Close"])
    # 고점 2026-02-17 이후 162봉 → 직전 162봉 중 최저 종가 2026-01-08 77.43(v5.344 구간 = ⑤와 같은 정의)
    assert (pl["peak_date"], pl["low_date"], pl["window_bars"]) == ("2026-02-17", "2026-01-08", 162)
    assert pl["peak_close"] == pytest.approx(86.95, abs=0.01) and pl["low_close"] == pytest.approx(77.43, abs=0.01)


def test_prior_low_window_fixed():
    """고점 = 최근 250봉 최고 종가일, 구간 = 고점 직전 '고점 이후 봉 수'와 같은 길이(고점일 제외 — ⑤와 같은 정의).
    고점 이후 더 낮은 종가·구간 밖(옛 250봉 구간 안이라도)의 더 낮은 종가는 안 본다."""
    idx = pd.bdate_range("2023-01-02", periods=700)
    c = pd.Series(100.0, index=idx)
    c.iloc[600] = 200.0                               # 최근 250봉(450~699) 최고 → 고점, 이후 99봉(601~699)
    c.iloc[550] = 90.0                                # 직전 99봉(501~599) 안 최저
    c.iloc[450] = 10.0                                # 구간 밖 — 옛 정의(직전 250봉 350~599)라면 잡혔을 값
    c.iloc[650] = 50.0                                # 고점 이후 — 안 본다
    pl = ev.prior_low(c)
    assert (pl["peak_date"], pl["low_date"], pl["low_close"], pl["window_bars"]) == (str(idx[600].date()), str(idx[550].date()), 90.0, 99)
    assert ev._a_lookback() == 250
    assert ev.prior_low(c.iloc[560:]) is None                                                   # 고점 앞 40봉 < 이후 99봉 — 짧게 대신 안 함
    assert ev.prior_low(c.iloc[501:])["window_bars"] == 99                                      # 고점 앞 정확히 99봉 — 판정
    assert ev.prior_low(c.iloc[502:]) is None                                                   # 고점 앞 98봉 — 1봉 모자람
    assert ev.prior_low(c.iloc[:601]) is None                                                   # 고점이 마지막 봉 — 이후 0봉
    assert ev.prior_low(pd.Series([5.0, 4.0], index=idx[:2])) is None


def _months(closes):
    idx = pd.date_range("2020-01-31", periods=len(closes), freq="ME")
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes}, index=idx)


def test_pullback_ma20_rising_and_vol_contracting():
    up = _months([float(i) for i in range(1, 31)])                                            # 꾸준히 상승 → 20선 상승
    flat = _months([10.0] * 30)
    idx = pd.bdate_range("2025-01-01", periods=60)
    close = pd.Series([100.0] * 60, index=idx); close.iloc[40] = 150.0                         # 고점 40번째
    vol = pd.Series([100.0] * 60, index=idx); vol.iloc[41:] = 50.0                              # 고점 이후 19봉 평균 50 < 직전 19봉 100
    daily = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": vol})
    a = {i["key"]: i for i in ev.pullback_checks(daily, up)}
    b = {i["key"]: i for i in ev.pullback_checks(daily, flat)}
    assert a["ma20_rising"]["result"] is True and b["ma20_rising"]["result"] is False          # 같으면 상승 아님(>)
    assert a["vol_contracting"]["result"] is True and "이후 19봉 평균 / 직전 19봉" in a["vol_contracting"]["detail"]
    daily2 = daily.copy(); daily2.loc[daily2.index[41:], "Volume"] = 100.0
    assert {i["key"]: i for i in ev.pullback_checks(daily2, up)}["vol_contracting"]["result"] is False   # 같으면 감소 아님(<)
    short = _months([1.0] * 25)
    assert {i["key"]: i for i in ev.pullback_checks(daily, short)}["ma20_rising"]["result"] is None      # 20+6개월 필요


def test_manual_setup_switch_saved_and_changes_checklist(evals_store):
    class R:
        def __init__(self, b): self._b = b
        async def json(self): return self._b
    auto = {"items": [{"key": "months", "result": True}], "pullback_items": [{"key": "ma20_rising", "result": False}],
            "setup": {"setup_type": "bottom"}, "manual_auto": {}}
    rec = {**_rec("A", {}), "auto": auto}
    rec.pop("rev"); rec.pop("updated_at")
    s1 = json.loads(asyncio.run(app.lp_eval_put("A", R({"record": rec, "base_rev": None}))).body)
    s2 = json.loads(asyncio.run(app.lp_eval_put("A", R({"record": {**s1["record"], "setup_override": "pullback"},
                                                       "base_rev": s1["record"]["rev"]}))).body)
    saved = app._rec_list_load(app.LP_EVALS_PATH)[0]
    assert s2["ok"] and saved["setup_override"] == "pullback" and saved["rev"] == 2
    bad = asyncio.run(app.lp_eval_put("A", R({"record": {**saved, "setup_override": "box"}, "base_rev": 2})))
    assert bad.status_code == 400
    M, P = [[k, k] for k, _ in ev.MANUAL_ITEMS], [[k, k] for k, _ in ev.PULLBACK_MANUAL_ITEMS]
    got = _js(f"[lpeTally({json.dumps(s1['record'])}, {json.dumps(M)}, {json.dumps(P)}), lpeTally({json.dumps(saved)}, {json.dumps(M)}, {json.dumps(P)}),"
              f" lpeSetupOf({json.dumps(saved)}), lpeSetupOf({{auto: {{setup: {{setup_type: 'unknown'}}}}}}), lpeSetupOf({{auto: {{items: []}}}})]", TALLY)
    assert got[0] == {"O": 1, "X": 0, "blank": len(M)} and got[1] == {"O": 0, "X": 1, "blank": len(P)}
    assert got[2]["eff"] == "pullback" and got[2]["override"] == "pullback"
    assert got[3]["eff"] == "bottom" and got[3]["unknown"] and got[3]["judged"]                # 판정 불가 → 바닥형 체크리스트
    assert got[4]["unknown"] and not got[4]["judged"]                                          # 옛 레코드(유형 없음) → 재평가 안내
    card = _fn("_lpeCard")
    assert "유형 판정 불가" in card and "lpeSetSetup(" in card and "tip(it.key)" in card and "tip(k)" in card


# ── 수정 3: 관찰 중 무효 참고 ─────────────────────────────────────────
def test_watch_ref_low_bottom_and_pullback():
    hi = w.setup_type(_df("365590.KQ"), "2026-10-08")
    peg = w.setup_type(_df("PEG"), "2026-10-08")
    assert (hi["setup_ref_low"], hi["setup_ref_basis"], hi["setup_ref_date"]) == (1178.0, "A 저점", "2026-09-30")
    assert hi["setup_ref_low"] == hi["setup_a"]["low"]                                         # ABC A 저점 그대로
    want = ev.prior_low(app._downcast(_df("PEG"))["Close"])
    assert (peg["setup_ref_low"], peg["setup_ref_basis"], peg["setup_ref_date"]) == (want["low_close"], "직전 저점", "2026-01-08")
    unk = w.setup_type(_df("407400.KQ").iloc[-300:], "2026-10-08")
    assert unk["setup_type"] == "unknown" and unk["setup_ref_low"] is None
    got = _js("[lpwRefLow({setup_type:'bottom', setup_ref_low:1178, setup_ref_basis:'A 저점', last_close:1462}),"
              " lpwRefLow({setup_type:'unknown', setup_ref_low:5, last_close:4}), lpwRefLow({setup_type:'pullback', setup_ref_low:77.13, last_close:null})]",
              ("lpwRefLow",))
    assert got[0]["v"] == 1178 and round(got[0]["dist"], 2) == round((1178 / 1462 - 1) * 100, 2) and got[1] is None
    assert got[2]["dist"] is None
    body = _fn("renderLowpointWatch")
    assert body.count("lpwRefLow(r)") == 1 and ">무효 참고</th>" in body


def test_stage_logic_untouched_by_ref_low():
    import inspect
    for f in (w.stage_info, w.judge_kr_regular, w.reach_info):
        assert "setup_ref" not in inspect.getsource(f) and "prior_low" not in inspect.getsource(f)
