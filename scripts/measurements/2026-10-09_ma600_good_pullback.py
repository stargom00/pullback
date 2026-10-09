"""600일선 기준봉(장대양봉) 뒤 "좋은 되돌림" 확인봉 → 다음 날 시가 진입 EV (A 돌파 기준봉 vs C 선 아래 기준봉).

사전등록: docs/ma600_good_pullback.md (짝: docs/ma99_good_pullback.md — 이 모듈을 import해 선 길이만 99로).
**⚠️ ④ LTF 가격 반응은 과거 분봉이 없어(naver 분봉 최근 6거래일만 보존) 일봉 반전봉으로 대체했다.**
a732920(600·99 돌파 장대양봉 다음 날 시가 진입, 기각)의 진입 설계 오류("다음 날 시가")를 바로잡는 측정 — 같은 데이터 일곱 번째 검정,
Bonferroni 7(z ≥ 2.69).

재사용(사본 금지): 데이터·유니버스·정제·저유동성·A/C 분류·MA·청산 봉 찾기·D+63 구간 = 600일선 돌파 스크립트(`load_data`·`classify`·
`ma_at`·`exit_index`·`d63_buckets`·`_dist`), 다음 날 시가 진입 레이스 = 지지 확인 스크립트(`race_open`), 매물대 = 운영 ABC
`abc_screener.supply_profile`(v5.331, ABC_CONFIG 기본값 a_lookback 250·supply_profile_bins 10 — import만, 수정 안 함).

기준봉(①): 장대양봉(종가 ≥ 전일 종가 × 1.15 그리고 종가 > 시가). A = 전일 종가 < 선, 당일 종가 > 선. C = 전일 종가 < 선, 당일 종가 ≤ 선.
매물대(②): 기준봉 **전일까지** 정제 데이터로 supply_profile(종가, 거래량, last = 전일 종가) — 전일 종가 위쪽 최대 거래량 구간.
  조건 = 어느 날 저가 ≤ 매물대 상단, 그리고 그때까지 종가 < 매물대 하단인 날이 없음.
이평선(③): 어느 날 봉의 저가~고가 범위가 [min(MA10, MA50), max(MA10, MA50)] 구간과 겹침(사용자 승인 해석 6 — 50일선 아래꼬리 + 종가 회복도
  닿음), 그리고 그때까지 종가 < MA50인 날이 없음.
매물대가 통째로 기준봉 종가 위(하단 > 기준봉 종가)면 미진입 사유 "위 매물 잔존"(`overhead_supply`)으로 따로 센다(사용자 승인 해석 4).
눌림: 기준봉 다음 날부터 종가 < 기준봉 종가인 날 1일 이상.
확인봉(④ 대체): 눌림 1일 이상·②·③ 충족이 **앞선 봉에서** 끝난 뒤 나온, 양봉이면서 종가 > 전일 고가인 첫 봉.
기한: 기준봉 다음 날부터 20거래일(유효봉). 그 안에 ②·③ 종가 이탈이 먼저 나오거나 확인봉이 없으면 미진입.
진입 = 확인봉 다음 유효봉 시가, 손절 = 기준봉 다음 날 ~ 확인봉 최저 저가(저가 터치 −1R), +2R / 60봉. 진입가 ≤ 손절가면 제외.
판정: A EV ≥ 0.15R · A−C z ≥ 2.69 · 반분 양쪽(A ≥ 0.15R·A > C) · A·C nv ≥ 100(아니면 판정 불가).
실행 시각: harness.check_run_window(["KR"]). 실행: MEAS_CACHE=<3,000일 캐시.pkl> python3 scripts/measurements/2026-10-09_ma600_good_pullback.py
"""
import importlib.util
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from statistics import median

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


line = _load("ma_line_bull_candle_gp", "2026-10-09_ma600_bull_candle_breakout.py")    # 데이터·A/C·MA·청산 봉·D+63 구간
bcs = _load("bull_candle_support_gp", "2026-10-09_bull_candle_support_entry.py")      # race_open(다음 날 시가 진입)
harness, prev = line.harness, line.prev
import abc_screener  # noqa: E402  — 매물대(v5.331) import만

# ── 사전등록 원문의 값 ────────────────────────────────────────────────
MA_N = 600                       # 선(99일선 스크립트가 99로 바꾼다)
MA_FAST, MA_SLOW = 10, 50        # "③ 주요 이평선 10~50"
WINDOW = 20                      # "기한: 기준봉 다음 날부터 20거래일"
EV_MIN = 0.15
Z_MIN = 2.69                     # "같은 데이터로 일곱 번째 검정이므로 Bonferroni 7을 적용한다. z ≥ 2.69"
N_MIN = 100
D_FWD = 63
RUN_MARKETS = ["KR"]
SEED = 20261009
CHECK_N = 3                      # 수기 대조 건수(원문 "수기 3건")

OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


def supply_zone(p_prev):
    """기준봉 전일까지 정제 데이터 → 운영 ABC 매물대(전일 종가 위 최대 거래량 구간) {lo, hi, ...} | None."""
    return abc_screener.supply_profile(p_prev["Close"], p_prev["Volume"], float(p_prev["Close"].iloc[-1]))["zone"]


def find_entry(view, k_bull: int, zone) -> dict:
    """기준봉(원본 k_bull) 뒤 20유효거래일 안 확인봉 탐색. 반환 {"reason", "k_confirm", "day", "stop", ...}.
    reason: confirmed · no_zone · overhead_supply(매물대 하단 > 기준봉 종가 — 위 매물 잔존) · break_zone(종가 < 매물대 하단) · break_ma50(종가 < MA50) · break_both ·
            no_pullback · fail_zone_only(②만 실패) · fail_ma_only(③만 실패) · fail_both · no_confirm_bar(②·③·눌림 다 됐는데 확인봉 없음) ·
            window_incomplete(데이터 끝).
    각 봉은 그 봉까지 정제한 df에서 판정(무효봉 건너뜀, 거래일로 안 셈). 이탈 판정을 먼저 본다(확인봉 자신도 이탈하면 안 된다).
    눌림·②·③ 충족 표시는 그 봉을 본 **뒤**에 켠다 — 같은 봉은 확인봉이 될 수 없다(충족 "뒤에 나온" 봉)."""
    if zone is None:
        return {"reason": "no_zone"}
    b_close = float(view.prefix(k_bull)["Close"].iloc[-1])
    if zone["lo"] > b_close:
        return {"reason": "overhead_supply"}        # 기준봉이 매물대를 못 뚫었다 — "이탈"과 따로 센다(해석 4)
    pulled = z_touch = m_touch = False
    min_low = None
    day = 0
    for k in range(k_bull + 1, len(view.raw)):
        if not view.valid(k):
            continue
        day += 1
        if day > WINDOW:
            break
        p = view.prefix(k)
        o, h, lo, c = (float(p[x].iloc[-1]) for x in ("Open", "High", "Low", "Close"))
        ma_f, ma_s = line.ma_at(p, 0, MA_FAST), line.ma_at(p, 0, MA_SLOW)
        min_low = lo if min_low is None else min(min_low, lo)
        bz, bm = c < zone["lo"], c < ma_s
        if bz or bm:
            return {"reason": "break_both" if bz and bm else ("break_zone" if bz else "break_ma50"), "day": day}
        if pulled and z_touch and m_touch and c > o and c > float(p["High"].iloc[-2]):
            return {"reason": "confirmed", "k_confirm": k, "day": day, "stop": min_low}
        if c < b_close:
            pulled = True
        if lo <= zone["hi"]:
            z_touch = True
        if lo <= max(ma_f, ma_s) and h >= min(ma_f, ma_s):
            m_touch = True                             # 봉 범위가 10~50일선 구간과 겹침(해석 6)
    if day < WINDOW:
        return {"reason": "window_incomplete"}
    if not pulled:
        return {"reason": "no_pullback"}
    if z_touch and m_touch:
        return {"reason": "no_confirm_bar"}
    if z_touch:
        return {"reason": "fail_ma_only"}          # ②는 됐고 ③만 실패
    if m_touch:
        return {"reason": "fail_zone_only"}        # ③은 됐고 ②만 실패
    return {"reason": "fail_both"}


def forward_from_entry(view, k_confirm: int, entry: float) -> dict:
    """기록 전용: 진입봉(확인봉 다음 유효봉)을 1번째로 세어 63번째 유효봉 종가의 진입가 대비 %, 그 63봉 최고 고가·최저 저가 %."""
    fut = view.future(k_confirm)
    if len(fut) < D_FWD:
        return {"d63_incomplete": True}
    w = fut.iloc[:D_FWD]
    return {"d63_incomplete": False, "d63_pct": (float(w["Close"].iloc[-1]) / entry - 1) * 100,
            "d63_max_pct": (float(w["High"].max()) / entry - 1) * 100, "d63_min_pct": (float(w["Low"].min()) / entry - 1) * 100}


def _in_any(k, spans):
    return any(a <= k <= b for a, b in spans)


def scan_ticker(t, view, stats: Counter) -> list:
    """기준봉 → 확인봉 → 진입. 겹침(a732920과 같은 뜻): 이미 잡은 포지션 기간(진입~청산) 안에 기준봉이 있거나 진입봉이 들어가면 건너뛴다."""
    assert line.MIN_BARS == MA_N + 1, "선 모듈의 후보 거름(MIN_BARS)이 이 측정의 선 길이와 다르다"
    out, spans = [], []
    for k in line.candidate_bars(view):
        if not view.valid(k):
            continue
        p = view.prefix(k)
        if len(p) < MA_N + 1:
            continue
        g = line.classify(p, MA_N)
        if g is None:
            continue
        stats[f"{g}_signal"] += 1
        if _in_any(k, spans):
            stats[f"{g}_skipped_in_position"] += 1
            continue
        zone = supply_zone(p.iloc[:-1])
        fe = find_entry(view, k, zone)
        stats[f"{g}_{fe['reason']}"] += 1
        if fe["reason"] != "confirmed":
            continue
        kc = fe["k_confirm"]
        fut = view.future(kc)
        if len(fut) and _in_any(int(view.raw.index.get_loc(fut.index[0])), spans):
            stats[f"{g}_skipped_in_position"] += 1
            continue
        a = bcs.race_open(view, kc, fe["stop"], stats, g)
        if not a:
            continue
        x = line.exit_index(a["entry"], a["stop"], fut, a["outcome"])
        spans.append((int(view.raw.index.get_loc(fut.index[0])), int(view.raw.index.get_loc(fut.index[x]))))
        out.append({"ticker": t, "group": g, "bull_date": str(p.index[-1].date()), "confirm_date": str(view.raw.index[kc].date()),
                    "days_bull_to_confirm": fe["day"], "zone_lo": zone["lo"], "zone_hi": zone["hi"],
                    **a, **forward_from_entry(view, kc, a["entry"])})
    return out


def d63_summary(rows) -> dict:
    done = [r for r in rows if not r["d63_incomplete"]]
    xs = sorted(r["d63_pct"] for r in done)
    out = {"n": len(rows), "n_complete": len(done), "n_incomplete": len(rows) - len(done)}
    if xs:
        out.update({"up_rate": sum(1 for x in xs if x > 0) / len(xs), "median": median(xs), "p25": xs[len(xs) // 4],
                    "p75": xs[len(xs) * 3 // 4], "buckets": line.d63_buckets(xs),
                    "max_pct_median": median(r["d63_max_pct"] for r in done), "min_pct_median": median(r["d63_min_pct"] for r in done)})
    return out


def hand_checks(rows, views, rng) -> dict:
    """실행 중 수기 대조(하드 실패): 진입 이벤트 3건의 D+63(파이썬 루프), 확인봉 시점 MA10·MA50 3건(파이썬 합)."""
    pick = rng.sample([r for r in rows if not r["d63_incomplete"]], min(CHECK_N, len(rows)))
    out = []
    for r in pick:
        v = views[r["ticker"]]
        kc = v.raw.index.get_loc(pd.Timestamp(r["confirm_date"]))
        fut = v.future(kc)
        closes = [float(x) for x in fut["Close"].tolist()[:D_FWD]]
        hand = (closes[-1] / r["entry"] - 1) * 100
        assert abs(hand - r["d63_pct"]) < 1e-9, f"D+63 대조 실패 {r['ticker']}"
        p = v.prefix(kc)
        cs = [float(x) for x in p["Close"].tolist()]
        h10, h50 = sum(cs[-MA_FAST:]) / MA_FAST, sum(cs[-MA_SLOW:]) / MA_SLOW
        assert abs(h10 - line.ma_at(p, 0, MA_FAST)) <= abs(h10) * 1e-9 and abs(h50 - line.ma_at(p, 0, MA_SLOW)) <= abs(h50) * 1e-9
        out.append({"ticker": r["ticker"], "confirm_date": r["confirm_date"], "entry": r["entry"], "d63_close": closes[-1],
                    "d63_pct": hand, "ma10": h10, "ma50": h50})
    return out


def main():
    run_window = harness.check_run_window(RUN_MARKETS)
    t0 = time.time()
    blob = line.load_data()
    data = blob["data"]
    assert max(len(df) for df in data.values()) > 1900, "3,000일 데이터가 아니다"
    stamp = harness.run_stamp(data)
    print(f"[stamp] {stamp}", flush=True)
    stats = Counter()
    views = {t: harness.CleanView(df) for t, df in data.items()}
    rows = []
    for i, (t, v) in enumerate(sorted(views.items()), 1):
        rows.extend(scan_ticker(t, v, stats))
        if i % 500 == 0:
            print(f"[scan] {i}/{len(views)} rows={len(rows)} {time.time() - t0:.0f}s", flush=True)
    a_rows = [r for r in rows if r["group"] == "A"]
    c_rows = [r for r in rows if r["group"] == "C"]
    sum_a, sum_c = prev.summarize(a_rows), prev.summarize(c_rows)
    z, _ = harness.ev_gap_zscore(sum_c, sum_a) if a_rows and c_rows else (None, False)
    split = sorted(r["date"] for r in a_rows)[len(a_rows) // 2] if a_rows else None
    halves = ({"early": {"A": prev.summarize([r for r in a_rows if r["date"] < split]), "C": prev.summarize([r for r in c_rows if r["date"] < split])},
               "late": {"A": prev.summarize([r for r in a_rows if r["date"] >= split]), "C": prev.summarize([r for r in c_rows if r["date"] >= split])}}
              if split else None)
    if (sum_a["nv"] or 0) < N_MIN or (sum_c["nv"] or 0) < N_MIN or not halves:
        verdict, checks = "판정 불가", None
    else:
        checks = {"ev_a_ge_min": sum_a["ev_R"] is not None and sum_a["ev_R"] >= EV_MIN,
                  "z_ge_min": z is not None and z >= Z_MIN,
                  "halves_hold": all(v["A"]["ev_R"] is not None and v["C"]["ev_R"] is not None and v["A"]["ev_R"] >= EV_MIN
                                     and v["A"]["ev_R"] > v["C"]["ev_R"] for v in halves.values())}
        verdict = "통과" if all(checks.values()) else "기각"
    hc = hand_checks(rows, views, random.Random(SEED)) if rows else []
    reasons = {g: {k.split("_", 1)[1]: n for k, n in sorted(stats.items()) if k.startswith(g + "_")} for g in ("A", "C")}
    result = {
        "run_stamp": stamp, "run_window": run_window, "fetched_at": blob.get("fetched_at"),
        "universe": len(blob.get("names") or {}), "fetched": len(data), "failed": len(blob.get("failed") or []),
        "elapsed_s": round(time.time() - t0),
        "params": {"MA_N": MA_N, "MA_FAST": MA_FAST, "MA_SLOW": MA_SLOW, "WINDOW": WINDOW, "EV_MIN": EV_MIN, "Z_MIN": Z_MIN,
                   "N_MIN": N_MIN, "MAX_BARS": prev.MAX_BARS, "D_FWD": D_FWD, "DATA_DAYS": line.DATA_DAYS,
                   "supply": {"fn": "abc_screener.supply_profile", "a_lookback": abc_screener.ABC_CONFIG["a_lookback"],
                              "supply_profile_bins": abc_screener.ABC_CONFIG["supply_profile_bins"]}},
        "stats": dict(stats), "reasons_by_group": reasons,
        "A": sum_a, "C": sum_c, "z_A_vs_C": z, "half_split_date": split, "halves": halves, "verdict": verdict, "checks": checks,
        "hand_checks": hc,
        "record_only_d63": {g: d63_summary([r for r in rows if r["group"] == g]) for g in ("A", "C")},
        "record_only_days_bull_to_confirm": {g: line._dist([r["days_bull_to_confirm"] for r in rows if r["group"] == g]) for g in ("A", "C")},
        "record_only_risk_pct": {g: line._dist([r["risk_pct"] for r in rows if r["group"] == g]) for g in ("A", "C")},
        "rows": rows,
    }
    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=1, default=str), flush=True)
    print(f"[done] {OUT}", flush=True)


if __name__ == "__main__":
    main()
