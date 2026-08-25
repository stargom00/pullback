"""
추세전환 탭 히트 수 괴리 조사 (2026-08-25, 8/17 세션 미완 항목 ③).

배경: `all_tabs_common_yardstick_investigation.md`의 원 Script A(2026-08-08,
유동성 필터 없음, 스크립트 원본 소실)는 추세전환 일평균 히트 88.4건으로
기록했다. `docs/pullback_stop_width_and_entry_timing.md`의 2026-08-14
재측정(harness 기반, 저유동성 필터 포함, off=60..250 20개 체크포인트)은
50.1건로 나왔다. 사용자가 실제 앱에서 목격한 당일 스캔은 31건 — 88.4도
50.1도 아닌 세 번째 숫자다.

이 스크립트가 답하는 것 둘:
  (a) 최근 30거래일 일별 히트 수 분포 — 31건이 정상 변동 범위 안인지,
      아니면 최근 하락 추세가 있는지. off=0..29(오늘~29거래일 전) 매일.
  (b) 백테스트 히트 정의 vs 실제 앱 스캔 조건 코드 대조(측정이 아니라
      코드 리딩) — app.py `run_scan()`(3817행~)을 직접 읽어 확인한 결과,
      `analyze_turnaround()` 반환 이후 적용되는 후처리 필터는 저유동성
      하드컷(KR 3억원/US $2M, 3868~3873행) **하나뿐**이다. turnaround는
      pullback 전용인 is_super/rs_3m/rs_delta 같은 추가 게이트가 없다
      (3856~3858행 조건문이 mode=='pullback'에만 걸림). 즉 harness.py의
      `passes_liquidity_filter()`가 이미 실제 앱 파이프라인을 완전히
      재현한다 — 코드 레벨에서 "빠진 필터"는 못 찾았다. 남은 설명은
      (a) 원 88.4가 애초에 유동성 필터 없이 부풀려진 수치였다는 것과
      (b) 그날그날의 정상 변동/추세 — 이 스크립트의 (a) 결과로 판별한다.

scanner.py는 전혀 수정하지 않음(요청 사항) — 순수 측정 스크립트.
공통 하네스(harness.py) 재사용, 새 로직 없음(analyze_turnaround 호출 +
저유동성 필터만 매일 반복).

실행: 리포 루트에서
`python3 scripts/measurements/2026-08-25_turnaround_hit_count_gap.py`
(전체 유니버스 fetch 포함 5~7분, 네트워크 필요)
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
from scanner import analyze_turnaround, TURN_CONFIG

# 최근 30거래일 = off 0(오늘)~29(29거래일 전), 매일(간격 1) — 기존 방법론의
# off=60..250·10간격(추세 EV 측정용, 분기~반기 단위 표본)과는 목적이 달라
# 의도적으로 범위·간격을 바꿈. "최근 30거래일 분포"가 요청 사항이라 여기서는
# 오래된 체크포인트를 아예 안 씀.
OFFSETS = harness.checkpoints(0, 29, 1)
MIN_BARS_FLOOR = TURN_CONFIG["min_bars"]


def run(data, bench, out_path=None):
    kospi_close = bench["kospi"]["Close"].dropna() if bench.get("kospi") is not None else None
    kosdaq_close = bench["kosdaq"]["Close"].dropna() if bench.get("kosdaq") is not None else None

    tickers = list(data.keys())
    daily = []  # [{off, date_hint, n_hits, n_kr, n_us}]
    t0 = time.time()
    for oi, off in enumerate(OFFSETS):
        b_kospi = harness.bench_score_at(kospi_close, off)
        b_kosdaq = harness.bench_score_at(kosdaq_close, off)

        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < MIN_BARS_FLOOR:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, b_kospi, b_kosdaq)

        n_kr = n_us = 0
        date_hint = None
        for t, hist in trunc_cache.items():
            ikr = harness.is_kr_ticker(t)
            if date_hint is None:
                try:
                    date_hint = str(hist.index[-1].date())
                except Exception:
                    date_hint = None
            try:
                hit = analyze_turnaround(hist, rs_rank=rs_ranks.get(t), rs_mom=rs_moms.get(t),
                                          cfg=TURN_CONFIG, is_kr=ikr)
            except Exception:
                continue
            if hit is None:
                continue
            if not harness.passes_liquidity_filter(hit, ikr):
                continue
            if ikr:
                n_kr += 1
            else:
                n_us += 1
        daily.append({"off": off, "date_hint": date_hint, "n_hits": n_kr + n_us, "n_kr": n_kr, "n_us": n_us})
        print(f"[TURNAROUND-DAILY] off={off} date~{date_hint} hits={n_kr+n_us} (kr={n_kr},us={n_us}) "
              f"elapsed={time.time()-t0:.0f}s ({oi+1}/{len(OFFSETS)})", flush=True)

    counts = [d["n_hits"] for d in daily]
    n = len(counts)
    mean = sum(counts) / n if n else None
    variance = sum((c - mean) ** 2 for c in counts) / n if n else None
    std = variance ** 0.5 if variance is not None else None
    first_half = counts[: n // 2]
    second_half = counts[n // 2:]
    fh_avg = sum(first_half) / len(first_half) if first_half else None
    sh_avg = sum(second_half) / len(second_half) if second_half else None

    report = {
        "daily": daily,
        "n_days": n,
        "mean": round(mean, 1) if mean is not None else None,
        "std": round(std, 1) if std is not None else None,
        "min": min(counts) if counts else None,
        "max": max(counts) if counts else None,
        # off=0..14(최근 절반, 시간순으로는 더 과거 절반의 반대 — off가 작을수록
        # 최신이므로 first_half=off 0~14=가장 최근, second_half=off 15~29=더 과거)
        "recent_15d_avg": round(fh_avg, 1) if fh_avg is not None else None,
        "older_15d_avg": round(sh_avg, 1) if sh_avg is not None else None,
        "reference_old_script_a_2026_08_08": 88.4,
        "reference_2026_08_14_harness_60_250": 50.1,
    }
    print(f"[TURNAROUND-DAILY SUMMARY] {report['n_days']}일, 평균={report['mean']}, "
          f"표준편차={report['std']}, 범위=[{report['min']},{report['max']}], "
          f"최근15일평균={report['recent_15d_avg']}, 이전15일평균={report['older_15d_avg']}", flush=True)

    if out_path:
        with open(out_path, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"SAVED report to {out_path}", flush=True)
    return report


if __name__ == "__main__":
    data, kr_u, us_u = harness.fetch_universe_data()
    bench = harness.fetch_kr_benchmarks()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "2026-08-25_turnaround_hit_count_gap.results.json")
    run(data, bench, out_path=out)
