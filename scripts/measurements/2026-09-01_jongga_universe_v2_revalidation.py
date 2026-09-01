"""
종가베팅 백테스트 — 유니버스 오염 수정본으로 재측정 (2026-09-01, 사용자
지시). 배경: `get_universe("kr")`가 91% 시가총액 폴백으로 채워지고
있음이 확인돼(naver_kr.fetch_top_value()의 sise_quant.naver 페이지네이션
버그, 별도 조사) 원측정(z=4.28, n=276, 2026-08-29)의 "base(거래대금
상위100)" 조건이 실제로는 "시총상위 유니버스 내 거래대금 상위100"이었을
가능성이 제기됐다. `naver_kr.fetch_top_turnover_v2()`(m.stock.naver.com
기반, 별도 신설, fetch_top_value 미변경)로 만든 진짜 거래대금 상위
유니버스로 동일 방법론을 재실행해 비교한다.

**재사용 원칙(README 규칙3)**: 원측정 스크립트
(`2026-08-29_kr_jongga_betting_backtest_extended.py`)를 모듈로 import해
`turnover_rank_at()`/`evaluate()`/`stats()`/`mean_gap_zscore()`/`OFFSETS`
(=checkpoints(60,950,10), 90개, 이미 규칙9 충족)를 그대로 재사용한다 —
새로 구현 안 함. 바뀌는 건 오직 어떤 티커 집합의 데이터를 이 함수들에
먹이느냐(유니버스 소스)뿐.

**신규 상장주(60거래일 미만) 처리 확인**: `evaluate()`의
`MIN_BARS_AFTER_OFFSET=260`(52주 lookback+여유) 게이트가 있어, 절대
보유기간이 260봉 미만인 종목은 어떤 체크포인트에서도 `evaluate()`를
통과해 히트가 될 수 없다(코드 확인, 부분데이터로 계산되는 경우 없음 —
`n = len(df) - off; if n < 260: continue`로 완전 스킵). 다만
`turnover_rank_at()`의 조건은 훨씬 느슨해서(`len(df)-off >= 1`) 짧은
역사의 종목도 그날 거래대금이 크면 top-100 순위 계산에는 참여해
**다른 종목을 순위 밖으로 밀어낼 수 있다** — 이 간접효과가 실제로
결과를 바꾸는지 "전체" vs "60거래일 미만 제외" 두 변형을 직접 비교해
확인한다(사용자 지시).

실행: 리포 루트에서
`python3 scripts/measurements/2026-09-01_jongga_universe_v2_revalidation.py`
(신규 유니버스 fetch ~60초 + KR 1900일 fetch ~200초 + 90체크포인트×2변형
평가, 총 10분 내외 예상).
"""
import sys
import os
import json
import time
import importlib.util
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import naver_kr
import universe as universe_mod

_ORIG_PATH = os.path.join("scripts", "measurements",
                           "2026-08-29_kr_jongga_betting_backtest_extended.py")
_spec = importlib.util.spec_from_file_location("jongga_orig", _ORIG_PATH)
orig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(orig)

SHORT_HISTORY_MIN_BARS = 60   # 사용자 지시 — "데이터 60거래일 미만" 그대로


def fetch_universe_history(tickers, concurrency=10):
    data = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = {ex.submit(orig._fetch_kr_long, t): t for t in tickers}
        done = 0
        for fut in as_completed(futs):
            t, df = fut.result()
            if df is not None:
                data[t] = df
            done += 1
            if done % 300 == 0:
                print(f"[fetch] {done}/{len(tickers)} elapsed={time.time()-t0:.0f}s", flush=True)
    print(f"[fetch] 완료 {len(data)}/{len(tickers)} elapsed={time.time()-t0:.0f}s", flush=True)
    return data


def run_variant(label, data):
    t0 = time.time()
    all_records = []
    for oi, off in enumerate(orig.OFFSETS):
        rank = orig.turnover_rank_at(data, off)
        recs = orig.evaluate(data, off, rank)
        all_records.extend(recs)
    base_only = [r for r in all_records if r["base"]]
    combo_a = [r for r in base_only if r["candle"] and r["volume"] and r["position"]]

    s_combo = orig.stats(combo_a)
    s_base = orig.stats(base_only)
    z, sig = orig.mean_gap_zscore(base_only, combo_a)
    gap_vs_base = (s_combo["mean_gap"] - s_base["mean_gap"]) if (s_combo.get("n") and s_base.get("n")) else None

    # 시간 반분(원측정과 동일 판정 기준)
    earlier = [r for r in combo_a if r["half"] == "earlier"]
    recent = [r for r in combo_a if r["half"] == "recent"]
    s_earlier, s_recent = orig.stats(earlier), orig.stats(recent)

    print(f"[{label}] n_records={len(all_records)} n_base={len(base_only)} n_comboA={len(combo_a)} "
          f"elapsed={time.time()-t0:.0f}s", flush=True)

    return {
        "label": label,
        "n_records_total": len(all_records),
        "n_base": len(base_only),
        "combo_a": s_combo,
        "base": s_base,
        "z_vs_base": z, "significant": sig, "gap_vs_base": gap_vs_base,
        "half_earlier": s_earlier, "half_recent": s_recent,
    }


if __name__ == "__main__":
    _t0 = time.time()

    print("[main] 신규 유니버스(m.stock.naver.com 기반) 생성 중...", flush=True)
    new_dyn, v2_stats = naver_kr.fetch_top_turnover_v2(top_n=1500)
    print(f"[main] fetch_top_turnover_v2 stats: {v2_stats}", flush=True)

    # get_universe()와 동일 병합 패턴(정적 KR_UNIVERSE ∪ 동적) — 바뀌는 건
    # 동적 소스뿐, 정적 리스트는 그대로 유지해 통제된 비교가 되게 한다.
    new_universe_tickers = {**universe_mod.KR_UNIVERSE, **new_dyn}
    print(f"[main] 신규 유니버스(정적+동적v2) 티커 수: {len(new_universe_tickers)}", flush=True)

    data_full = fetch_universe_history(list(new_universe_tickers.keys()))

    data_excl_short = {t: df for t, df in data_full.items() if len(df) >= SHORT_HISTORY_MIN_BARS}
    n_excluded = len(data_full) - len(data_excl_short)
    print(f"[main] {SHORT_HISTORY_MIN_BARS}거래일 미만 제외: {n_excluded}종목 "
          f"(fetch성공 {len(data_full)} -> 평가대상 {len(data_excl_short)})", flush=True)

    result_full = run_variant("신규유니버스_전체", data_full)
    result_excl = run_variant(f"신규유니버스_{SHORT_HISTORY_MIN_BARS}일미만제외", data_excl_short)

    report = {
        "fetch_top_turnover_v2_stats": v2_stats,
        "new_universe_ticker_count": len(new_universe_tickers),
        "fetched_count": len(data_full),
        "excluded_short_history_count": n_excluded,
        "short_history_threshold_bars": SHORT_HISTORY_MIN_BARS,
        "result_full": result_full,
        "result_excl_short": result_excl,
        "original_measurement_reference": {
            "note": "재실행 안 함 — docs/kr_jongga_betting_backtest.md 인용",
            "z_vs_base": 4.28, "n_combo_a": 276, "net_mean_pct": 1.22,
        },
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "2026-09-01_jongga_universe_v2_revalidation.results.json")
    with open(out_path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"SAVED report to {out_path}", flush=True)
    print(f"[main] 전체 완료 elapsed={time.time()-_t0:.0f}s", flush=True)
