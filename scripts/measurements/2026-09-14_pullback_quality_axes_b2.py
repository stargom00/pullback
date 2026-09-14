"""측정 B-2 — B에서 누락된 3항목만. 사전등록: docs/pullback_quality_axes_b2.md.

  rs_3m     : 체크포인트별 3개월 RS 랭크를 analyze()에 **주입**
  rs_delta  : rs_rank(cp) − rs_rank(cp에서 20봉 뺀 시점). 벤치마크 점수는
              20봉 전 것을 다시 구하지 않고 cp 값을 재사용 — 프로덕션과 동일
              (app.py `_compute_rs_ranks(data_20ago, b_kospi, b_kosdaq, b_us)`).
  tt_pass   : 0~8 정수 카운트(불리언 아님) → 연속값으로 상위/하위 30%

나머지 14개는 재측정하지 않는다. 문턱은 원래 설계 k=17 기준 2.974 그대로.
**n_top 또는 n_bot이 0이면 판정하지 않고 하드 실패**(B의 재발 방지).
"""
import inspect
import json
import os
import pickle
import sys
import time
from collections import defaultdict

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harness
import naver_kr
import app
from scanner import analyze, CONFIG

_b1 = __import__("2026-09-14_pullback_quality_axes")
_m0911 = __import__("2026-09-11_imminent_score_rank_vs_return")

OFFSETS = _b1.OFFSETS
RECENT, OLDER = _b1.RECENT, _b1.OLDER
PROD_WINDOW_DAYS = _b1.PROD_WINDOW_DAYS
MCAP_MIN_EOK = _b1.MCAP_MIN_EOK
MIN_BARS = _b1.MIN_BARS
T_PLUS = _b1.T_PLUS
TOP_PCT = _b1.TOP_PCT
MIN_HITS_FOR_SPLIT = _b1.MIN_HITS_FOR_SPLIT
MEDIAN_GAP_MIN, HALF_N_MIN, GROUP_N_MIN = _b1.MEDIAN_GAP_MIN, _b1.HALF_N_MIN, _b1.GROUP_N_MIN
Z_RAW = _b1.Z_RAW

Z_BONF = _b1.bonferroni_z(17)      # 사전등록 1.3 — 원래 설계 k=17 그대로
RS_DELTA_LOOKBACK = 20             # app.py와 동일 상수
ITEMS_B2 = [("rs_3m", "주입 — 3개월 RS 랭크"),
            ("rs_delta", "주입 — 20봉 전 대비 랭크 변화"),
            ("tt_pass", "0~8 정수 카운트")]

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "2026-09-14_pullback_quality_axes_b2.results.json")


def scan_at(blob, cp, tickers, cal, is_kr, bench_kr, stats):
    data = blob["data"]
    lo_date = cp - pd.Timedelta(days=PROD_WINDOW_DAYS)
    pos = cal.get_loc(cp)
    t20 = cal[pos + T_PLUS] if pos + T_PLUS < len(cal) else None
    assert t20 is None or t20 > cp

    cache_all, cache_m = {}, {}
    for t in tickers:
        df = data.get(t)
        if df is None:
            continue
        tr = df.loc[(df.index >= lo_date) & (df.index <= cp)]
        if tr.empty or tr.index[-1] != cp:
            continue
        cl = harness.clean_at_checkpoint(tr)
        if cl is None or cl.empty or cl.index[-1] != cp or len(cl) < MIN_BARS:
            continue
        assert cl.index.max() == cp, f"lookahead {t}"
        cache_all[t] = cl
        if is_kr:
            m = blob["mcap"].get(t)
            if m is None:
                stats["mcap_unknown"] += 1
            else:
                if m[0] * float(cl["Close"].iloc[-1]) / m[1] < MCAP_MIN_EOK:
                    stats["mcap_dropped"] += 1
                    continue
        cache_m[t] = cl

    bk = bench_kr["kospi"] if is_kr else 0.0
    bq = bench_kr["kosdaq"] if is_kr else 0.0
    rs_m, mom_m = harness.compute_rs_at_checkpoint(cache_m, bk, bq)
    rs_a, mom_a = harness.compute_rs_at_checkpoint(cache_all, bk, bq)

    # ── B-2: 주입할 두 값 ───────────────────────────────────────────
    rs3_m = harness.rank_by_return(cache_m, 63)
    cache_20ago = {t: h.iloc[:-RS_DELTA_LOOKBACK] for t, h in cache_m.items()
                   if len(h) > RS_DELTA_LOOKBACK}
    # 벤치마크는 cp 값을 그대로 재사용 — 프로덕션과 동일(사전등록 1.2)
    rs_20ago, _ = harness.compute_rs_at_checkpoint(cache_20ago, bk, bq)
    rs_delta_m = {t: rs_m[t] - rs_20ago[t] for t in rs_m if t in rs_20ago}

    n_nofilter = 0
    for t, h in cache_all.items():
        r = analyze(h, rs_rank=rs_a.get(t), rs_mom=mom_a.get(t), cfg=CONFIG, is_kr=is_kr)
        if r and not r.get("price_frozen") and harness.passes_liquidity_filter(r, is_kr):
            n_nofilter += 1

    hits = []
    for t, h in cache_m.items():
        r = analyze(h, rs_rank=rs_m.get(t), rs_mom=mom_m.get(t), cfg=CONFIG, is_kr=is_kr,
                    rs_3m=rs3_m.get(t), rs_delta=rs_delta_m.get(t))   # ← B의 누락 지점
        if r is None or r.get("price_frozen") or not harness.passes_liquidity_filter(r, is_kr):
            continue
        raw = data[t]
        c0 = float(raw.at[cp, "Close"])
        if t20 is None or t20 not in raw.index or c0 <= 0:
            stats["no_future"] += 1
            continue
        hits.append({"ticker": t, "off": None,
                     "ret20": (float(raw.at[t20, "Close"]) / c0 - 1) * 100,
                     "rs_3m": r.get("rs_3m"), "rs_delta": r.get("rs_delta"),
                     "tt_pass": r.get("tt_pass")})
    return hits, n_nofilter


def assign_splits_b2(hits_at_cp, stats):
    """3항목 전부 연속값 — 체크포인트별 상위/하위 30%."""
    for key, _note in ITEMS_B2:
        valid = [h for h in hits_at_cp if isinstance(h.get(key), (int, float))
                 and not isinstance(h.get(key), bool)]
        stats[f"none_{key}"] += len(hits_at_cp) - len(valid)
        if len(valid) < MIN_HITS_FOR_SPLIT:
            stats[f"cp_skip_{key}"] += 1
            continue
        k = max(1, int(len(valid) * TOP_PCT))
        order = sorted(valid, key=lambda x: -x[key])
        top = {id(x) for x in order[:k]}
        bot = {id(x) for x in order[-k:]}
        for h in valid:
            h[f"_g_{key}"] = "top" if id(h) in top else ("bot" if id(h) in bot else None)


def main():
    t0 = time.time()
    blob = _b1.load_data()
    data = blob["data"]
    kr_t = [t for t in blob["kr_u"] if t in data]
    us_t = [t for t in blob["us_u"] if t in data]
    cal_kr = _b1.market_calendar(data, kr_t)
    cal_us = _b1.market_calendar(data, us_t)
    stamp = harness.run_stamp(data)
    print(f"[data] kr={len(kr_t)} us={len(us_t)} last_kr={cal_kr[-1].date()} "
          f"last_us={cal_us[-1].date()}", flush=True)
    print(f"[stamp] {stamp['run_at_kst']} KST | [bonf] k=17 -> z >= {Z_BONF}", flush=True)

    stats = defaultdict(int)
    all_hits, gate_n = [], 0
    for i, off in enumerate(OFFSETS):
        for mkt, tickers, cal in (("kr", kr_t, cal_kr), ("us", us_t, cal_us)):
            if off >= len(cal):
                continue
            cp = cal[-1 - off]
            bench = ({k: harness.bench_score_at_date(blob["bench"][k]["Close"], cp)
                      for k in ("kospi", "kosdaq")} if mkt == "kr" else None)
            hits, n_nf = scan_at(blob, cp, tickers, cal, mkt == "kr", bench, stats)
            gate_n += n_nf
            assign_splits_b2(hits, stats)
            for h in hits:
                h["off"] = off
            all_hits.extend(hits)
        if (i + 1) % 30 == 0:
            print(f"[cp] {i+1}/{len(OFFSETS)} hits={len(all_hits)} "
                  f"elapsed={time.time()-t0:.0f}s", flush=True)

    diff = gate_n / 10199 - 1
    gate = {"ref": 10199, "observed_nofilter": gate_n, "diff_pct": round(diff * 100, 2),
            "passed": abs(diff) <= 0.05, "hits_with_mcap_filter": len(all_hits)}
    print(f"[gate] 눌림목 무필터 {gate_n} vs 10199 ({diff*100:+.2f}%) -> "
          f"{'PASS' if gate['passed'] else 'FAIL'}", flush=True)
    if not gate["passed"]:
        json.dump({"gate": gate, "meta": {"run_stamp": stamp}}, open(OUT, "w"),
                  ensure_ascii=False, indent=2, default=str)
        print("[stop] 재현 게이트 실패 — 판정하지 않고 종료", flush=True)
        sys.exit(1)

    rows = [_b1.judge(k_, note, "B", all_hits) for k_, note in ITEMS_B2]

    # 사전등록 1.4 — n=0이면 **하드 실패**(B는 nan으로 조용히 넘어갔다)
    empty = [(r["item"], r["n_top"], r["n_bot"]) for r in rows
             if r["n_top"] == 0 or r["n_bot"] == 0]
    if empty:
        json.dump({"gate": gate, "items": rows, "empty": empty,
                   "meta": {"run_stamp": stamp}}, open(OUT, "w"),
                  ensure_ascii=False, indent=2, default=str)
        raise SystemExit(f"[FAIL] 군이 빈 항목이 있다 — 측정 불가: {empty}\n"
                         f"       (B에서 이 상태를 nan으로 넘겨 결과표에 0으로 남았다)")

    # judge()는 _b1의 Z_BONF(2.974, k=17)를 쓴다 — 사전등록과 같은 값인지 확인
    assert _b1.Z_BONF == Z_BONF, (_b1.Z_BONF, Z_BONF)

    print(f"\n{'항목':12s} {'n_top':>6s} {'n_bot':>6s} {'중앙값차':>9s} {'MWU z':>7s} "
          f"{'1.96':>5s} {'보정':>5s} {'반분':>5s} {'판정':>5s}")
    for r in rows:
        print(f"{r['item']:12s} {r['n_top']:6d} {r['n_bot']:6d} {r['median_gap']:9.3f} "
              f"{r['mwu_z']:7.3f} {'O' if r['sig_raw_1_96'] else '-':>5s} "
              f"{'O' if r['sig_bonf'] else '-':>5s} "
              f"{'O' if r['checks']['half_same_sign'] else '-':>5s} "
              f"{'PASS' if r['passed'] else '-':>5s}", flush=True)

    passed = [r["item"] for r in rows if r["passed"]]
    result = {"meta": {"prereg": "docs/pullback_quality_axes_b2.md", "k_for_threshold": 17,
                       "z_bonferroni": Z_BONF, "z_raw": Z_RAW, "run_stamp": stamp,
                       "rs_delta_lookback": RS_DELTA_LOOKBACK},
              "gate": gate, "n_hits": len(all_hits), "items": rows, "diag": dict(stats),
              "verdict": {"passed_items": passed, "n_passed": len(passed),
                          "sig_raw_only_items": [r["item"] for r in rows
                                                 if r["sig_raw_1_96"] and not r["sig_bonf"]]}}
    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=2, default=str)
    print(f"\n통과 항목: {passed or '없음'}")
    print(f"[done] {time.time()-t0:.0f}s -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
