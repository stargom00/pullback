"""측정만 — 저점 포트폴리오에서 물타기 몫(현금 예비) 줄이기.

사전등록: docs/lowpoint_portfolio_cash_reserve.md §1 — 결과 보고 규칙·값을 바꾸지 않는다.
재사용: 11-10 포트폴리오 백테스트(collect_signals · run_portfolio) — 신호·체결·익절·물타기·덜어내기 규칙은 그 스크립트 그대로.
         이 스크립트는 (동시 종목 수 N, 1차 비중 f) 조합만 바꾼다.
실행 시각: harness.check_run_window(("kr",)).
실행: MEAS_CACHE=/tmp/lp_1900.pkl python3 scripts/measurements/2026-11-11_lowpoint_portfolio_cash_reserve.py
      (파일명 날짜는 실행일 — KST — 로 바꾼다. 결과 JSON은 커밋하지 않는다)
"""
import importlib.util
import json
import os
import random
import time
from collections import Counter
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("lp_pf", os.path.join(HERE, "2026-11-10_lowpoint_portfolio_backtest.py"))
pf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pf)
m, base, harness = pf.m, pf.base, pf.harness

# ── 사전등록 값(docs/lowpoint_portfolio_cash_reserve.md §1) ──
# (이름, 동시 종목 N, 1차 비중 f) — 주식 최대 투입 = N × f, 물타기 몫 = 1 − N × f
VARIANTS = [
    ("A_base_N10_f5", 10, 0.05),     # 11-10 기본 재현: 투입 50% / 예비 50%
    ("B_N14_f5", 14, 0.05),          # 자리 늘리기: 70% / 30%
    ("C_N20_f5", 20, 0.05),          # 자리 늘리기: 100% / 0%
    ("D_N10_f7.5", 10, 0.075),       # 한 번에 크게: 75% / 25%
    ("E_N10_f10", 10, 0.10),         # 한 번에 크게: 100% / 0%
]
OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))


def main():
    run_window = harness.check_run_window(("kr",))
    t0 = time.time()
    now = datetime.now(m.KST)
    blob = base.load_data()
    data = blob["data"]
    stamp = harness.run_stamp(data)
    rng, stats = random.Random(pf.SEED), Counter()
    m.self_check_hits(data, now, rng, stats)
    signals, window_start = pf.collect_signals(data, now, stats)
    start = window_start + pd.Timedelta(weeks=m.WARMUP_WEEKS) + pd.Timedelta(days=pf.START_PAD_DAYS)
    end = max(df.index[-1] for df in data.values())
    stats["signals_after_filters"] = len(signals)
    results = {}
    for name, n, f in VARIANTS:
        r = pf.run_portfolio(signals, start, end, n, stats, size_frac=f, tag=name)
        r["max_invest_frac"] = n * f
        results[name] = r
    # 재현 확인: A는 11-10 기본(N10, 1/(2N)=5%)과 같은 규칙 — 그 실행의 연복리 +4.4%·최종 1.145와 맞아야 한다(데이터 캐시가 같을 때).
    result = {"run_stamp": stamp, "run_window": run_window, "elapsed_s": round(time.time() - t0),
              "params": {"VARIANTS": VARIANTS, "start": str(start.date()), "end": str(end.date())},
              "stats": dict(stats), "portfolio": results,
              "limitations": ["상장폐지 종목 없음(현재 상장 종목만) — 물타기 몫 0 변형일수록 결과가 실제보다 좋게 나올 수 있음",
                              "같은 데이터의 반복 측정 — 표본 밖 확인 아님"]}
    json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    brief = {k: {kk: v for kk, v in r.items() if kk not in ("equity_monthly", "open_at_end")} for k, r in results.items()}
    print(json.dumps({"stats": dict(stats), "portfolio": brief}, ensure_ascii=False, indent=1, default=str), flush=True)
    print(f"[done] {OUT}", flush=True)


if __name__ == "__main__":
    main()
