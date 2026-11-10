"""측정만 — 저점 주봉 신호 포트폴리오 백테스트(자금 제약·동시 보유·물타기·장기 보유).

사전등록: docs/lowpoint_portfolio_backtest.md §1 — 결과 보고 규칙·값을 바꾸지 않는다.
재사용: 11-10 매도 규칙 모듈(weekly_hits·self_check·WARMUP) → 10-09(load_data·liquid) → harness(CleanView·run_stamp·벤치마크).
실행 시각: harness.check_run_window(("kr",)).
실행: MEAS_CACHE=/tmp/lp_1900.pkl python3 scripts/measurements/2026-11-10_lowpoint_portfolio_backtest.py
      (파일명 날짜는 실행일 — KST — 로 바꾼다. 결과 JSON은 커밋하지 않는다)
"""
import importlib.util
import json
import math
import os
import random
import time
from collections import Counter, defaultdict
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("lp_1110", os.path.join(HERE, "2026-11-10_lowpoint_after5_exit_rules.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)
base, harness, lp = m.base, m.harness, m.lp

# ── 사전등록 값(docs/lowpoint_portfolio_backtest.md §1) ──
PRIMARY_N = 10               # 동시 보유 최대 종목(사용자 "좋아")
VARIANTS_N = (5, 10, 20)     # 민감도(기록만) — 1차 비중 = 1/(2N)
TARGET = 0.05
GAP_SKIP = 0.05
BUY_SLIP = 0.002             # 매수 시가 슬리피지 0.2%(AI 어림값)
SELL_COST = 0.0025           # 매도 금액 0.25%(AI 어림값)
TURNOVER_DAYS = 20           # 신호 우선순위 = 직전 20일 평균 거래대금(사용자 "좋아")
DEDUP_DAYS = 28
START_PAD_DAYS = 7           # 시작일 = 조회 창 시작 + 워밍업 104주 + 7일(모든 종목이 같은 출발선)
SEED = 20261110
OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


def collect_signals(data, now, stats):
    """11-10 측정과 같은 히트·워밍업·stale·4주 중복·저유동성. 반환: [{ticker, base_date, base_price, turnover}]."""
    window_start = pd.Timestamp(now.date()) - pd.Timedelta(days=base.KR_DAYS)
    hits_by_label = defaultdict(list)
    for t, df in data.items():
        truncated = df.index[0] <= window_start + pd.Timedelta(days=30)
        wk_idx = lp.resample_bars(df["Close"], m.TF).index
        for label, bar_date, c0 in m.weekly_hits(df["Close"], now):
            if truncated and wk_idx.get_loc(label) < m.WARMUP_WEEKS:
                stats["hit_warmup_skip"] += 1
                continue
            hits_by_label[label].append({"ticker": t, "label": label, "base_date": pd.Timestamp(bar_date), "base_price": c0})
    week_mode = {}
    for t, df in data.items():
        for lbl, bd in lp.resample_bars(df["Close"], m.TF)["bar_date"].items():
            week_mode.setdefault(lbl, Counter())[pd.Timestamp(bd)] += 1
    hits = []
    for label, hs in hits_by_label.items():
        mode = week_mode[label].most_common(1)[0][0]
        hits += [h for h in hs if h["base_date"] >= mode]
    out, last_kept, views = [], {}, {}
    for h in sorted(hits, key=lambda x: (x["ticker"], x["base_date"])):
        t = h["ticker"]
        if t in last_kept and (h["base_date"] - last_kept[t]).days < DEDUP_DAYS:
            stats["dedup_skip"] += 1
            continue
        last_kept[t] = h["base_date"]
        df = data[t]
        view = views.get(t) or views.setdefault(t, harness.CleanView(df))
        if h["base_date"] not in df.index:
            continue
        jb = df.index.get_loc(h["base_date"])
        if not view.valid(jb):
            continue
        pre = view.prefix(jb)
        if not base.liquid(pre):
            stats["illiquid"] += 1
            continue
        fut = view.future(jb)
        if len(fut) == 0:
            stats["no_future"] += 1
            continue
        tail = pre.iloc[-TURNOVER_DAYS:]
        out.append({**h, "turnover": float((tail["Close"] * tail["Volume"]).mean()), "path": fut})
    return out, window_start


def run_portfolio(signals, start, end, N, stats):
    size_frac = 1.0 / (2 * N)
    by_day = defaultdict(list)
    for s in signals:
        d0 = s["path"].index[0]
        if start <= d0 <= end:
            by_day[d0].append(s)
    days = sorted(set(d for s in signals for d in s["path"].index if start <= d <= end))
    cash, pos = 1.0, {}               # pos[ticker] = {shares, first_px, first_shares, add(px,shares)|None, path, last, entry}
    eq_curve, trades, missed = [], [], Counter()
    last_eq = 1.0
    for d in days:
        # 1) 신규 진입(그날 시가) — 거래대금 큰 순서
        for s in sorted(by_day.get(d, []), key=lambda x: -x["turnover"]):
            t = s["ticker"]
            o = float(s["path"]["Open"].iloc[0])
            if not o > 0 or o >= s["base_price"] * (1 + GAP_SKIP):
                missed["gap_skip"] += 1
                continue
            if t in pos:
                missed["already_held"] += 1
                continue
            if len(pos) >= N:
                missed["slots_full"] += 1
                continue
            budget = last_eq * size_frac
            if cash < budget:
                missed["no_cash"] += 1
                continue
            px = o * (1 + BUY_SLIP)
            sh = budget / px
            cash -= budget
            pos[t] = {"shares": sh, "first_px": px, "first_shares": sh, "add": None, "path": s["path"], "last": o,
                      "entry": d, "adds": 0, "cash_short_adds": 0, "paid": budget}
        # 2) 보유 종목 그날 봉 처리: 익절 → 덜어내기 → 물타기
        for t in list(pos):
            p = pos[t]
            if d not in p["path"].index:
                continue                                   # 그 종목 무거래(정지) — 마지막 종가 유지
            bar = p["path"].loc[d]
            o, h, lo, c = float(bar["Open"]), float(bar["High"]), float(bar["Low"]), float(bar["Close"])
            T = p["first_px"] * (1 + TARGET)
            if h >= T:
                px = max(o, T)
                proceeds = p["shares"] * px * (1 - SELL_COST)
                cash += proceeds
                trades.append({"ticker": t, "entry": str(p["entry"].date()), "exit": str(d.date()),
                               "days": (d - p["entry"]).days, "ret_on_paid": proceeds / p["paid"] - 1, "adds": p["adds"]})
                del pos[t]
                continue
            if p["add"] is not None:
                avg = (p["first_px"] + p["add"][0]) / 2
                if h >= avg:
                    px = max(o, avg)
                    cash += p["add"][1] * px * (1 - SELL_COST)
                    p["shares"] -= p["add"][1]
                    p["add"] = None
                    p["last"] = c
                    continue
            if p["add"] is None and lo <= p["first_px"] * 0.5:
                px = min(o, p["first_px"] * 0.5)
                need = p["first_shares"] * px
                if cash >= need:
                    cash -= need
                    p["shares"] += p["first_shares"]
                    p["add"] = (px, p["first_shares"])
                    p["adds"] += 1
                    p["paid"] += need
                else:
                    p["cash_short_adds"] += 1
                    stats[f"N{N}_add_no_cash"] += 1
            p["last"] = c
        eq = cash + sum(p["shares"] * p["last"] for p in pos.values())
        eq_curve.append((d, eq, len(pos), cash / eq if eq > 0 else 0))
        last_eq = eq
    s = pd.Series([e for _, e, _, _ in eq_curve], index=[d for d, _, _, _ in eq_curve])
    years = (s.index[-1] - s.index[0]).days / 365.25 if len(s) > 1 else float("nan")
    dd = s / s.cummax() - 1
    yearly = {str(y): float(g.iloc[-1] / g.iloc[0] - 1) for y, g in s.groupby(s.index.year)}
    open_pos = [{"ticker": t, "entry": str(p["entry"].date()), "unrealized": p["shares"] * p["last"] / p["paid"] - 1,
                 "days_held": (s.index[-1] - p["entry"]).days, "adds": p["adds"]} for t, p in pos.items()]
    return {"N": N, "size_frac": size_frac, "start": str(s.index[0].date()), "end": str(s.index[-1].date()),
            "final_equity": float(s.iloc[-1]), "cagr": float(s.iloc[-1] ** (1 / years) - 1) if years and years > 0 else None,
            "max_drawdown": float(dd.min()), "max_dd_date": str(dd.idxmin().date()), "yearly": yearly,
            "trades_closed": len(trades), "win_rate_closed": sum(x["ret_on_paid"] > 0 for x in trades) / len(trades) if trades else None,
            "days_closed": m.pct([x["days"] for x in trades], 0.5), "missed": dict(missed),
            "open_at_end": open_pos, "open_count": len(open_pos),
            "avg_slots_used": sum(n for _, _, n, _ in eq_curve) / len(eq_curve), "avg_cash_ratio": sum(r for *_, r in eq_curve) / len(eq_curve),
            "equity_monthly": {str(k.date()): float(v) for k, v in s.resample("ME").last().items()}}


def main():
    run_window = harness.check_run_window(("kr",))
    t0 = time.time()
    now = datetime.now(m.KST)
    blob = base.load_data()
    data = blob["data"]
    stamp = harness.run_stamp(data)
    rng, stats = random.Random(SEED), Counter()
    m.self_check_hits(data, now, rng, stats)
    signals, window_start = collect_signals(data, now, stats)
    start = window_start + pd.Timedelta(weeks=m.WARMUP_WEEKS) + pd.Timedelta(days=START_PAD_DAYS)
    end = max(df.index[-1] for df in data.values())
    stats["signals_after_filters"] = len(signals)
    results = {f"N{n}": run_portfolio(signals, start, end, n, stats) for n in VARIANTS_N}
    bench = None
    try:
        b = harness.fetch_kr_benchmarks(days=base.KR_DAYS)["kosdaq"]
        c = b["Close"] if hasattr(b, "columns") else b
        c = c[(c.index >= start) & (c.index <= end)]
        bench = {"kosdaq_buy_hold": float(c.iloc[-1] / c.iloc[0] - 1),
                 "kosdaq_max_drawdown": float((c / c.cummax() - 1).min())}
    except Exception as e:                                # 벤치마크는 기록용 — 실패를 숨기지 않고 남긴다
        bench = {"error": repr(e)}
    result = {"run_stamp": stamp, "run_window": run_window, "elapsed_s": round(time.time() - t0),
              "params": {"PRIMARY_N": PRIMARY_N, "VARIANTS_N": VARIANTS_N, "BUY_SLIP": BUY_SLIP, "SELL_COST": SELL_COST,
                         "TURNOVER_DAYS": TURNOVER_DAYS, "DEDUP_DAYS": DEDUP_DAYS, "start": str(start.date()), "end": str(end.date())},
              "stats": dict(stats), "benchmark": bench, "portfolio": results,
              "limitations": ["상장폐지 종목 없음(현재 상장 종목만) — 결과가 실제보다 좋게 나올 수 있음"]}
    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    brief = {k: {kk: v for kk, v in r.items() if kk not in ("equity_monthly", "open_at_end")} for k, r in results.items()}
    print(json.dumps({"stats": dict(stats), "benchmark": bench, "portfolio": brief}, ensure_ascii=False, indent=1, default=str), flush=True)
    print(f"[done] {OUT}", flush=True)


if __name__ == "__main__":
    main()
