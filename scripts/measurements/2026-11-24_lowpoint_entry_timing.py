"""저점 신호 뒤 "막 출발하는 자리" 진입 5가지 vs 신호 직후 진입 — H1 판정 측정. 사전등록: docs/lowpoint_entry_timing.md §1.

사용자 2026-11-23: "저점 종목을 매수하는데, 조건은 상장 후 1년이 지난 종목 중 하락후 양봉이 뜨면 진입이야. 나는 조금더 타이트하게
저점 종목중 막 상승을 시작하려는 그때 사고싶어. 조건이 뭘까?" → (후보 A·B·C 제안) → "돌려보자. 이평선 돌파도 넣어. 역배열에서
정배열로 바뀌는순간이라던지, 600일선 강하게 돌파 라던지".

입력: 11-10 시스템 기대값 스크립트의 결과 JSON(trades = 신호 직후 진입 기준선, 매매 정의의 유일한 원본) + 같은 캐시(MEAS_CACHE).
재사용(사본 없음): system_sim2·GAP_SKIP·HOLD_MAX(11-10 모듈) · divergence.bull_div · abc_screener._find_gate_break·ABC_CONFIG ·
lowpoint.rsi_wilder_sma · harness.CleanView/check_run_window/run_stamp.
실행: MEAS_CACHE=<lp_1900.pkl> python3 scripts/measurements/2026-11-24_lowpoint_entry_timing.py <system_expectancy.results.json>
      DRY=1 이면 실행 시각 검사 없이 앞 40종목의 트리거 건수만 센다(결과 지표는 계산하지 않음 — 코드 점검용).
"""
import importlib.util
import json
import math
import os
import pickle
import statistics
import sys
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts", "screens"))
import harness  # noqa: E402
import divergence  # noqa: E402
import lowpoint as lp  # noqa: E402
import abc_screener  # noqa: E402

_spec = importlib.util.spec_from_file_location("sx", os.path.join(HERE, "2026-11-10_lowpoint_system_expectancy_listing.py"))
sx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sx)
system_sim2, GAP_SKIP, HOLD_MAX = sx.system_sim2, sx.GAP_SKIP, sx.HOLD_MAX

# ── 사전등록 값(§1) — 전부 AI(Claude) 어림값, 측정·웹 근거 없음(E만 ABC 탭 강돌파 정의를 그대로 씀) ──
WINDOW = 120          # 신호 뒤 트리거를 찾는 거래일
LISTED_MIN = 250      # 상장 후 1년(사용자 조건) = 신호일까지 정제 일봉 250봉 이상
FAST_DAYS = 20
Z_MIN = 2.58          # Bonferroni 5(양측 0.01)
N_MIN = 100
PIV = 3               # A 저점 = 앞뒤 3봉 최저 종가(다이버전스 칩과 같은 K)
L1_BACK = 20          # A 1차 저점 = 신호일 포함 직전 20봉 ~ 2차 저점 5봉 전까지 최저 종가
C_MIN_DAYS, C_VOL_MAX = 13, 2.0
OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))
CANDS = ("A_higher_low", "B_divergence", "C_quiet_ma20", "D_ma_align", "E_ma600_strong")


def sma(a, n, t):
    return float(a[t - n + 1:t + 1].mean()) if t + 1 >= n else math.nan


def triggers(p, rsi, jb):
    """p = 정제 일봉(인덱스 0..), jb = 신호 봉 위치. 신호 다음 봉부터 WINDOW봉 안에서 후보별 첫 트리거 위치(없으면 None).
    t에서의 판정은 p.iloc[:t+1]만 본다(루프 안 모든 계산이 t 이하 인덱스만 읽는다 — 아래 assert로 고정)."""
    c, o, v = p["Close"].to_numpy(float), p["Open"].to_numpy(float), p["Volume"].to_numpy(float)
    n = len(p)
    out = dict.fromkeys(CANDS)
    base_date = p.index[jb]
    d_ok_at_base = sma(c, 5, jb) < sma(c, 20, jb) < sma(c, 60, jb)
    if not d_ok_at_base:
        out["D_ma_align"] = "excluded"
    last = min(n - 1, jb + WINDOW)
    for t in range(jb + 1, last + 1):
        bull = c[t] > o[t]
        # A 저점 높이기 + 양봉
        if out["A_higher_low"] is None and bull:
            for q in range(jb + 1, t - PIV + 1):
                lo_q = c[q - PIV:q + PIV + 1]
                if c[q] != lo_q.min():
                    continue
                l1 = c[max(0, jb - L1_BACK + 1):q - 4].min()
                if c[q] > l1 and c[q + 1:t + 1].min() >= c[q]:
                    out["A_higher_low"] = t
                    break
        # B 다이버전스(확정 저점, 신호 뒤) + 양봉
        if out["B_divergence"] is None and bull:
            d = divergence.bull_div(p["Close"].iloc[:t + 1], "day", rsi=rsi)
            if d and d["b_confirmed"] and pd.Timestamp(d["b_date"]) > base_date:
                out["B_divergence"] = t
        # C 조용한 20일선 돌파
        if out["C_quiet_ma20"] is None and t >= jb + C_MIN_DAYS and t >= 21:
            m20, m20p = sma(c, 20, t), sma(c, 20, t - 1)
            if c[t] > m20 and c[t - 1] <= m20p and v[t] < C_VOL_MAX * v[t - 20:t].mean():
                out["C_quiet_ma20"] = t
        # D 역배열(5<20<60, 신호일) → 정배열(5>20>60) 바뀌는 날
        if out["D_ma_align"] is None:
            now_ok = sma(c, 5, t) > sma(c, 20, t) > sma(c, 60, t)
            prev_ok = sma(c, 5, t - 1) > sma(c, 20, t - 1) > sma(c, 60, t - 1)
            if now_ok and not prev_ok:
                out["D_ma_align"] = t
        # E 600일선 강돌파(ABC 탭 정의 그대로: 첫 종가 돌파 · 당일 +7% · 거래량 50일 평균 2배)
        if out["E_ma600_strong"] is None and t + 1 > abc_screener.ABC_CONFIG["gate_ma_period"]:
            g = abc_screener._find_gate_break(p["Close"].iloc[:t + 1], p["Volume"].iloc[:t + 1], abc_screener.ABC_CONFIG)
            if g and g["bars_ago"] == 0 and g["strong"]:
                out["E_ma600_strong"] = t
    return out


def pz(d):
    if len(d) < 2:
        return None
    sd = statistics.stdev(d)
    return (statistics.mean(d) / (sd / math.sqrt(len(d)))) if sd > 0 else None


def fast(s):
    return s["exit"] == "target" and s["days"] <= FAST_DAYS


def judge(rows):
    """rows: [{base_date, base: system, cand: system}] — 같은 신호의 짝 비교."""
    n = len(rows)
    if n < 2:
        return {"n": n, "verdict": "판단 불가(표본 부족)"}
    d1 = [int(fast(r["cand"])) - int(fast(r["base"])) for r in rows]
    d2 = [r["cand"]["ret"] - r["base"]["ret"] for r in rows]
    d3 = [int(r["base"]["exit"] == "cap") - int(r["cand"]["exit"] == "cap") for r in rows]
    split = sorted(r["base_date"] for r in rows)[n // 2]
    halves = {}
    for nm, sel in (("early", lambda r: r["base_date"] < split), ("late", lambda r: r["base_date"] >= split)):
        dd = [int(fast(r["cand"])) - int(fast(r["base"])) for r in rows if sel(r)]
        halves[nm] = {"n": len(dd), "O1_diff": (sum(dd) / len(dd)) if dd else None}
    z1, z2, z3 = pz(d1), pz(d2), pz(d3)
    pos = all(h["O1_diff"] is not None and h["O1_diff"] > 0 for h in halves.values())
    neg = all(h["O1_diff"] is not None and h["O1_diff"] < 0 for h in halves.values())
    if n < N_MIN:
        verdict = "판단 불가(표본 부족)"
    elif z1 is not None and z1 >= Z_MIN and pos:
        verdict = "채택"
    elif z1 is not None and z1 <= -Z_MIN and neg:
        verdict = "역방향"
    else:
        verdict = "기각"
    tdays = lambda key: [r[key]["days"] for r in rows if r[key]["exit"] == "target"]
    return {"n": n, "verdict": verdict,
            "fast_base": sum(fast(r["base"]) for r in rows) / n, "fast_cand": sum(fast(r["cand"]) for r in rows) / n,
            "O1_diff_pp": statistics.mean(d1) * 100, "O1_z": z1,
            "ret_base": statistics.mean(r["base"]["ret"] for r in rows), "ret_cand": statistics.mean(r["cand"]["ret"] for r in rows),
            "O2_diff_pp": statistics.mean(d2), "O2_z": z2, "O2_flag": bool(z2 is not None and abs(z2) >= Z_MIN),
            "cap_base": sum(r["base"]["exit"] == "cap" for r in rows) / n, "cap_cand": sum(r["cand"]["exit"] == "cap" for r in rows) / n,
            "O3_z_base_minus_cand_cap": z3, "O3_flag": bool(z3 is not None and abs(z3) >= Z_MIN),
            "halves": halves, "split": str(split.date()),
            "target_days_median_base": statistics.median(tdays("base")) if tdays("base") else None,
            "target_days_median_cand": statistics.median(tdays("cand")) if tdays("cand") else None,
            "entry_vs_base_buy_median_pct": statistics.median(r["entry_vs_base_pct"] for r in rows),
            "wait_days_median": statistics.median(r["wait"] for r in rows)}


def main():
    dry = os.environ.get("DRY") == "1"
    run_window = "DRY" if dry else harness.check_run_window(("kr",))
    src = json.load(open(sys.argv[1]))
    trades = src["trades"]
    if not trades:
        raise SystemExit("매매 0건")
    data = pickle.load(open(os.environ["MEAS_CACHE"], "rb"))["data"]
    by_t = defaultdict(list)
    for x in trades:
        by_t[x["ticker"]].append(x)
    tickers = sorted(by_t)[:40] if dry else sorted(by_t)
    stats, rows = Counter(), defaultdict(list)
    used = set()          # 같은 종목·같은 후보·같은 트리거 날은 한 번만(가까운 신호 둘이 같은 날을 가리키면 앞 신호만)
    for tk in tickers:
        view = harness.CleanView(data[tk])
        for x in by_t[tk]:
            stats["trades"] += 1
            buy_date = pd.Timestamp(x["buy_date"])
            raw = view.raw
            jraw = raw.index.get_loc(buy_date) - 1
            while jraw >= 0 and not view.valid(jraw):
                jraw -= 1
            p_all = view.full if not view.dirty else None
            # 신호 봉까지 + 이후 WINDOW 봉: 판정은 각 t의 접두만 본다. 무효봉 없는 종목은 전체 정제의 접두 = 접두 정제.
            if view.dirty:
                stats["dirty_skip"] += 1          # 무효봉 있는 종목은 날마다 정제가 달라 이 측정에서 뺀다(수 기록)
                continue
            p = p_all
            jb = p.index.get_loc(raw.index[jraw])
            assert p.index[jb] < buy_date
            if jb + 1 < LISTED_MIN:
                stats["listed_lt_1y"] += 1
                continue
            if len(p) - 1 - jb < WINDOW + 1 + HOLD_MAX:
                stats["future_short"] += 1      # 트리거 창 + 보유 250봉이 안 되는 최근 신호는 모든 후보에서 뺀다(시기 치우침 방지)
                continue
            stats["sample"] += 1
            rsi = lp.rsi_wilder_sma(p["Close"])
            trig = triggers(p, rsi, jb)
            for k, t in trig.items():
                if t == "excluded":
                    stats[f"{k}_excluded"] += 1
                    continue
                if t is None:
                    stats[f"{k}_none"] += 1
                    continue
                if (tk, k, t) in used:
                    stats[f"{k}_dup_trigger"] += 1
                    continue
                used.add((tk, k, t))
                stats[f"{k}_hit"] += 1
                if dry:
                    continue
                entry = float(p["Open"].iloc[t + 1])
                if not entry > 0 or entry >= float(p["Close"].iloc[t]) * (1 + GAP_SKIP):
                    stats[f"{k}_gap_skip"] += 1
                    continue
                path = p.iloc[t + 1:t + 1 + HOLD_MAX]
                assert len(path) == HOLD_MAX and path.index[0] > p.index[t]
                rows[k].append({"ticker": tk, "base_date": p.index[jb], "base": x["system"], "cand": system_sim2(entry, path),
                                "wait": t - jb, "entry_vs_base_pct": (entry / x["buy"] - 1) * 100,
                                "trigger_date": str(p.index[t].date())})
    if dry:
        print(dict(stats))
        return
    assert stats["sample"] > 0, "표본 0건"
    res = {"run_window": run_window, "run_stamp": harness.run_stamp(data), "source_stamp": src.get("run_stamp"),
           "stats": dict(stats), "results": {k: judge(rows[k]) for k in CANDS},
           "trades": {k: [{kk: (str(vv.date()) if isinstance(vv, pd.Timestamp) else vv) for kk, vv in r.items()} for r in rows[k]] for k in CANDS}}
    json.dump(res, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: v for k, v in res.items() if k != "trades"}, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
