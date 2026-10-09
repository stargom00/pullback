"""상장 후 하락 종목의 회복 패턴 — 탐색(판정 없음).

문서: docs/ipo_decline_recovery_exploration.md. 등록부: "탐색(판정 없음)". **채택 판정이 아니라 패턴 서술**이다.
⚠️ 생존편향: 실행일(2026-10-09) KIND 상장·비관리 종목만 — 상장폐지된 종목은 없다(회복 못 하고 사라진 종목이 빠져 회복 쪽으로 기운다).

탐색/확인 분할: 종목 코드(6자리) sha256의 첫 바이트가 짝수면 탐색군, 홀수면 확인군. **확인군은 가격 데이터를 열지 않는다**(목록만 docs/에 저장).
데이터: 600일선 측정과 같은 3,000일 캐시(KIND 2,475종목, naver 일봉 2018-07-23~)·harness.CleanView 정제·harness 표준 저유동성 컷.
유니버스: KIND 상장일 ≥ 2018-07-01, 캐시의 정제 첫 봉 = 상장일(상장 첫날부터 있음), 스팩(회사명 "스팩"/"기업인수목적")·리츠(회사명 "리츠"/"REIT")
제외. 관리종목은 캐시 유니버스에서 이미 빠져 있다(KIND adminissue, 2026-10-09 스냅샷).

패턴(사후 정보 사용, 서술 1): 상장 초기 20거래일 제외. 저점 t = 21번째 봉 이후 최저 종가, 상장 고점 p = 21번째 봉 ~ t 전 최고 종가.
하락 = 저점 ≤ 고점 × 0.6. 회복 = t 이후 종가 ≥ 저점 × 1.5 도달(회복군) / 데이터 끝까지 미도달이고 t 이후 250봉 이상(미회복군) / 그 밖(판정 보류).
진입 신호(서술 2): 신호 판정은 그 봉까지의 데이터만 쓴다(이동평균·running 고점/저점). 종목당 **저점(사후) 이후** 첫 1회, 신호 다음 날 시가 진입.
실행: MEAS_CACHE=<3,000일 캐시.pkl> python3 scripts/measurements/2026-10-09_ipo_decline_recovery_exploration.py
"""
import csv
import hashlib
import importlib.util
import json
import os
import sys
import time
from collections import Counter, defaultdict
from statistics import median

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


line = _load("ma_line_ipo", "2026-10-09_ma600_bull_candle_breakout.py")     # load_data(3,000일)·장대양봉(bc)
harness, prev, lp, bc = line.harness, line.prev, line.prev.lp, line.bc
import abc_screener  # noqa: E402  — import만

LIST_FROM = "2018-07-01"         # 원문 "상장일이 2018-07-01 이후"
SKIP_FIRST = 20                  # "상장 초기 20거래일은 제외"
DECLINE = 0.6                    # "저점이 상장 고점 대비 −40% 이하"
RECOVER = 1.5                    # "저점 × 1.5 도달"
POST_MIN = 250                   # "저점 이후 데이터가 250봉 미만이면 판정 보류"
PRE_VOL_N, PRE_VOL_BASE = 60, 250          # "저점 직전 60봉 거래량(저점 전 250봉 평균 대비)"
BAND = 0.05                      # "저점 ±5% 안에서 보낸 날 수"
BAND_WIN = 250                   # 횡보 길이 창(저점 ±250봉) — AI 판단, 문서 §1-구현
REBOUND_LOOKBACK = 60            # "직전 반등 고점" = 저점 전 60봉 최고 종가 — AI 판단
VOL_SPIKE, VOL_BASE = 2.0, 250   # "거래량이 250봉 평균의 2배"
PRE_A_DROP, PRE_A_WIN, PRE_A_BAND = 0.5, 60, 1.15   # (a) "고점 대비 −50% 이하, 최근 60봉 종가가 최저 × 1.15 안"
HORIZONS = (63, 126, 250)
UP, DOWN = 1.30, 0.80            # "+30% 먼저 vs −20% 먼저"
SIGNALS = ("a_pre_base", "b_ma99_cross", "c_ma20_50_cross", "d_rebound_high", "e_ma200_cross")
RUN_MARKETS = ["KR"]
CONFIRM_CSV = os.path.join(ROOT, "docs", "ipo_decline_recovery_confirm_set.csv")
OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


# ── 유니버스 ─────────────────────────────────────────────────────────
def kind_listing() -> dict:
    """KIND corpList(공개 목록) → {ticker: {name, listed, sector}}. lowpoint.kr_universe와 같은 다운로드·같은 접미사."""
    import requests
    out = {}
    for board in ("kospi", "kosdaq"):
        corp_mt, _, suffix, min_n = lp.KR_BOARDS[board]
        r = requests.get(lp.KIND_CORPLIST_URL, params={"method": "download", "marketType": corp_mt}, headers=lp._KIND_HEADERS, timeout=30)
        r.raise_for_status()
        r.encoding = "euc-kr"
        t = lp._read_kind_table(r.text)
        assert len(t) >= min_n, f"KIND {board} {len(t)}건 — 비정상"
        for _, row in t.iterrows():
            code = str(row["종목코드"]).strip().zfill(6)
            out[f"{code}{suffix}"] = {"name": str(row["회사명"]), "listed": str(row["상장일"])[:10], "sector": str(row["업종"])}
    return out


def is_explore(ticker: str) -> bool:
    """종목 코드 해시 분할 — sha256(6자리 코드)의 첫 바이트가 짝수면 탐색군."""
    return hashlib.sha256(ticker.split(".")[0].encode()).digest()[0] % 2 == 0


def excluded_kind(name: str) -> "str | None":
    if "스팩" in name or "기업인수목적" in name:
        return "spac"
    if "리츠" in name or "REIT" in name.upper():
        return "reit"
    return None


# ── 패턴(서술 1, 사후) ────────────────────────────────────────────────
def pattern(s: pd.DataFrame) -> "dict | None":
    """s = 상장 첫날부터의 정제 일봉. 하락 아니면 None."""
    c = s["Close"].astype(float).values
    if len(c) <= SKIP_FIRST + 1:
        return None
    t = SKIP_FIRST + int(np.argmin(c[SKIP_FIRST:]))
    if t == SKIP_FIRST:
        return {"declined": False}
    p = SKIP_FIRST + int(np.argmax(c[SKIP_FIRST:t]))
    if not c[t] <= c[p] * DECLINE:
        return {"declined": False}
    rec = next((i for i in range(t + 1, len(c)) if c[i] >= c[t] * RECOVER), None)
    group = "recovered" if rec is not None else ("unrecovered" if len(c) - 1 - t >= POST_MIN else "pending")
    return {"declined": True, "p": p, "t": t, "rec": rec, "group": group}


def _first(cond, start):
    """cond: bool 배열. start 이후 처음 참인 위치."""
    idx = np.flatnonzero(cond[start:])
    return int(start + idx[0]) if len(idx) else None


def describe(s: pd.DataFrame, pt: dict) -> dict:
    """서술 1 항목(저점 기준 일수, 봉 단위). 이동평균은 그 봉까지의 값(rolling)."""
    c = s["Close"].astype(float)
    v = s["Volume"].astype(float)
    o = s["Open"].astype(float)
    p, t, rec = pt["p"], pt["t"], pt["rec"]
    cv = c.values
    ma = {n: c.rolling(n).mean().values for n in (20, 50, 99, 200)}
    out = {"drop_pct": (cv[t] / cv[p] - 1) * 100, "decline_bars": t - p}
    lo = max(0, t - PRE_VOL_BASE)
    base = v.values[lo:t].mean() if t > lo else float("nan")
    pre = v.values[max(0, t - PRE_VOL_N):t].mean() if t > 0 else float("nan")
    out["pre60_vol_ratio"] = pre / base if base and base > 0 else None
    w0, w1 = max(SKIP_FIRST, t - BAND_WIN), min(len(cv), t + BAND_WIN + 1)
    out["band_days"] = int(((cv[w0:w1] >= cv[t] * (1 - BAND)) & (cv[w0:w1] <= cv[t] * (1 + BAND))).sum())

    def rel(i):
        return None if i is None else i - t
    for n in (20, 50, 99, 200):
        out[f"above_ma{n}"] = rel(_first(np.nan_to_num(cv > ma[n], nan=False) & ~np.isnan(ma[n]), t + 1))
    x = (ma[20] > ma[50]) & ~np.isnan(ma[50])
    cross = np.zeros(len(cv), bool)
    cross[1:] = x[1:] & ~x[:-1]
    out["ma20_50_cross"] = rel(_first(cross, t + 1))
    rh = cv[max(p, t - REBOUND_LOOKBACK):t].max()
    out["rebound_high_break"] = rel(_first(cv > rh, t + 1))
    bull = np.zeros(len(cv), bool)
    for i in range(1, len(cv)):
        bull[i] = bc.is_bull_candle(cv[i - 1], o.values[i], cv[i])
    out["first_bull15"] = rel(_first(bull, t + 1))
    vb = v.rolling(VOL_BASE).mean().shift(1).values
    out["vol_spike2x"] = rel(_first(np.nan_to_num(v.values > VOL_SPIKE * vb, nan=False) & ~np.isnan(vb), t + 1))
    first_a = None
    for i in range(max(t + 1, abc_screener._min_bars(abc_screener.ABC_CONFIG) - 1), len(cv)):
        if abc_screener.analyze_abc(s.iloc[:i + 1])["verdict"] == "ABC":
            first_a = i
            break
    out["abc_first_A"] = rel(first_a)
    out["to_recovery"] = rel(rec)
    return out


# ── 진입 신호(서술 2) — 그 봉까지의 데이터만 ─────────────────────────────
def signal_series(s: pd.DataFrame) -> dict:
    """각 신호의 봉별 참/거짓. 모든 값은 i까지의 데이터로만 정해진다(rolling·누적 최고/최저·과거 창) — test가 미래 봉을 바꿔 확인."""
    c = s["Close"].astype(float).values
    n = len(c)
    cs = pd.Series(c)
    ma = {k: cs.rolling(k).mean().values for k in (20, 50, 99, 200)}
    out = {k: np.zeros(n, bool) for k in SIGNALS}
    run_hi = run_lo = None
    lo_i = None
    for i in range(SKIP_FIRST, n):
        run_hi = c[i] if run_hi is None else max(run_hi, c[i])       # 21번째 봉부터 그날까지 최고(상장 고점, running)
        if run_lo is None or c[i] < run_lo:
            run_lo, lo_i = c[i], i                                     # 그날까지 최저 종가와 그 위치(running)
        if i >= SKIP_FIRST + PRE_A_WIN:
            w = c[i - PRE_A_WIN + 1:i + 1]
            out["a_pre_base"][i] = run_lo <= run_hi * (1 - PRE_A_DROP) and w.max() <= run_lo * PRE_A_BAND
        for k, name in ((99, "b_ma99_cross"), (200, "e_ma200_cross")):
            if not np.isnan(ma[k][i - 1]):
                out[name][i] = c[i - 1] <= ma[k][i - 1] and c[i] > ma[k][i]
        if not np.isnan(ma[50][i - 1]):
            out["c_ma20_50_cross"][i] = ma[20][i - 1] <= ma[50][i - 1] and ma[20][i] > ma[50][i]
        if lo_i is not None and i > lo_i:
            rh = c[max(SKIP_FIRST, lo_i - REBOUND_LOOKBACK):lo_i].max() if lo_i > SKIP_FIRST else None
            out["d_rebound_high"][i] = rh is not None and c[i - 1] <= rh < c[i]
    return out


def forward(s: pd.DataFrame, i_sig: int) -> dict:
    """신호 다음 날 시가 진입 → 63·126·250봉 뒤 종가 수익률(진입봉 = 1번째), 250봉 안 최대 상승·하락, +30%/−20% 먼저."""
    if i_sig + 1 >= len(s):
        return {"no_next_bar": True}
    e = float(s["Open"].iloc[i_sig + 1])
    f = s.iloc[i_sig + 1:]
    out = {"entry": e, "entry_date": str(f.index[0].date())}
    for h in HORIZONS:
        out[f"r{h}"] = (float(f["Close"].iloc[h - 1]) / e - 1) * 100 if len(f) >= h else None
    w = f.iloc[:HORIZONS[-1]]
    out["max_up_250"] = (float(w["High"].max()) / e - 1) * 100
    out["max_dn_250"] = (float(w["Low"].min()) / e - 1) * 100
    out["full_250"] = len(f) >= HORIZONS[-1]
    first = "neither"
    for _, row in w.iterrows():
        dn, up = row["Low"] <= e * DOWN, row["High"] >= e * UP
        if dn:
            first = "down20"              # 같은 날 둘 다면 보수적으로 −20% 먼저
            break
        if up:
            first = "up30"
            break
    out["first_hit"] = first
    return out


def _q(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "median": median(xs), "p25": xs[len(xs) // 4], "p75": xs[len(xs) * 3 // 4]}


def main():
    run_window = harness.check_run_window(RUN_MARKETS)
    t0 = time.time()
    kind = kind_listing()
    blob = line.load_data()
    data = blob["data"]
    stamp = harness.run_stamp(data)
    stats = Counter()

    # 유니버스·분할(확인군은 가격을 보지 않는다 — 상장일·첫 봉 날짜 대조도 탐색군에서만)
    cands = {}
    for t, info in kind.items():
        if info["listed"] < LIST_FROM:
            continue
        stats["kind_listed_since"] += 1
        if excluded_kind(info["name"]):
            stats[f"excluded_{excluded_kind(info['name'])}"] += 1
            continue
        if t not in data:
            stats["not_in_cache"] += 1               # 관리종목(캐시 유니버스에서 제외) 등
            continue
        cands[t] = info
    explore = sorted(t for t in cands if is_explore(t))
    confirm = sorted(t for t in cands if not is_explore(t))
    with open(CONFIRM_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "name", "listed"])
        for t in confirm:
            w.writerow([t, cands[t]["name"], cands[t]["listed"]])
    stats["candidates"], stats["explore"], stats["confirm"] = len(cands), len(explore), len(confirm)

    used, rows = set(), []
    for t in explore:
        assert is_explore(t)
        v = harness.CleanView(data[t])
        s = v.full
        if not len(s) or str(s.index[0].date()) != cands[t]["listed"]:
            stats["first_bar_not_listing_day"] += 1      # 이전상장·캐시 시작 이후 상장 아님·장기 정지 절단 등
            continue
        stats["explore_used"] += 1
        used.add(t)
        pt = pattern(s)
        if not pt or not pt["declined"]:
            stats["not_declined"] += 1
            continue
        stats[f"group_{pt['group']}"] += 1
        row = {"ticker": t, "listed": cands[t]["listed"], **pt, "trough_date": str(s.index[pt["t"]].date()),
               "peak_date": str(s.index[pt["p"]].date()), **describe(s, pt)}
        sig = signal_series(s)
        row["signals"] = {}
        for k in SIGNALS:
            i = _first(sig[k], pt["t"] + 1)                # 저점(사후) 이후 첫 1회
            if i is None:
                continue
            if not prev.liquid(s.iloc[:i + 1]):
                row["signals"][k] = {"illiquid": True, "day": i - pt["t"]}
                continue
            row["signals"][k] = {"day": i - pt["t"], "date": str(s.index[i].date()), **forward(s, i)}
        row["signals"]["best_trough"] = {"day": 0, **forward(s, pt["t"])}      # 이론상 최선(사후 저점 다음 날 시가) — 실현 불가
        rows.append(row)
    assert not (used & set(confirm)), "확인군 종목이 집계에 들어갔다"
    assert all(is_explore(r["ticker"]) for r in rows)

    # 서술 1
    keys = ["drop_pct", "decline_bars", "pre60_vol_ratio", "band_days", "above_ma20", "above_ma50", "above_ma99", "above_ma200",
            "ma20_50_cross", "rebound_high_break", "first_bull15", "vol_spike2x", "abc_first_A", "to_recovery"]
    desc = {g: {k: _q([r.get(k) for r in rows if r["group"] == g]) for k in keys} for g in ("recovered", "unrecovered", "pending")}
    # 서술 2(보류 포함 하락 종목 전체)
    sig_tab = {}
    for k in list(SIGNALS) + ["best_trough"]:
        ss = [r["signals"][k] for r in rows if k in r["signals"] and not r["signals"][k].get("illiquid") and not r["signals"][k].get("no_next_bar")]
        fh = Counter(x["first_hit"] for x in ss)
        sig_tab[k] = {"n": len(ss), "n_illiquid": sum(1 for r in rows if r["signals"].get(k, {}).get("illiquid")),
                      **{f"r{h}": _q([x[f"r{h}"] for x in ss]) for h in HORIZONS},
                      "max_up_250": _q([x["max_up_250"] for x in ss if x["full_250"]]),
                      "max_dn_250": _q([x["max_dn_250"] for x in ss if x["full_250"]]),
                      "first_up30": fh["up30"], "first_down20": fh["down20"], "first_neither": fh["neither"],
                      "day_after_trough_recovered": _q([r["signals"][k]["day"] for r in rows if r["group"] == "recovered" and k in r["signals"]]),
                      "by_group_n": {g: sum(1 for r in rows if r["group"] == g and k in r["signals"]) for g in ("recovered", "unrecovered", "pending")}}
    result = {"run_stamp": stamp, "run_window": run_window, "fetched_at": blob.get("fetched_at"), "elapsed_s": round(time.time() - t0),
              "params": {k: globals()[k] for k in ("LIST_FROM", "SKIP_FIRST", "DECLINE", "RECOVER", "POST_MIN", "BAND", "BAND_WIN",
                                                   "REBOUND_LOOKBACK", "VOL_SPIKE", "PRE_A_DROP", "PRE_A_WIN", "PRE_A_BAND", "UP", "DOWN")},
              "stats": dict(stats), "describe": desc, "signals": sig_tab, "rows": rows}
    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=1, default=str), flush=True)
    print(f"[done] {OUT}", flush=True)


if __name__ == "__main__":
    main()
