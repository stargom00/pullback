"""KR 종가베팅 매도 타이밍별 EV — 사전등록 실행 (2026-09-10).
사전등록: docs/kr_jongga_betting_backtest.md "사전등록: 종가베팅 매도
타이밍별 EV (2026-09-09)" 절. 조건은 그 절이 정본 — 이 스크립트는 그
설계를 그대로 실행만 한다(조건 변경 금지, 사용자 지시).

【재사용 원칙(README 규칙3) — 재구현 금지】
- 후보 조건(base/candle/volume/position) 판정과 (a)익일시가 갭 계산은
  `2026-08-29_kr_jongga_betting_backtest_extended.py`(모듈 `orig`)의
  `turnover_rank_at()`/`evaluate()`를 그대로 호출해 재사용한다 — 여기서
  다시 구현하지 않는다.
- 유니버스는 `2026-09-01_jongga_universe_v2_revalidation.py`가 확립한
  "정적 KR_UNIVERSE ∪ naver_kr.fetch_top_turnover_v2()" 구성을 그대로
  재사용한다 — 사전등록이 인용하는 "기존 채택값(+0.80%, z=3.54, n=292)"이
  바로 이 유니버스로 만든 수치이기 때문(검증: 결과 JSON의 combo_a가
  n=292/net_mean=0.008022...=0.80%로 이 v2 유니버스에서만 정확히 재현됨,
  구 유니버스(orig 스크립트 자체의 get_universe("kr"))로는 다른 n이 나옴
  — 아래 "조건 정의 관련 발견" 참고).
- (a)익일 시가 갭(`gap_open`)은 `orig.evaluate()`가 계산한 값을 그대로
  재사용한다(재계산 안 함) — 이 스크립트가 새로 계산하는 건 (d)익일
  종가/구간 내 최대낙폭(저가)/갭하락 여부뿐, harness.truncate_at/
  future_after로 얻은 동일한 future 슬라이스에서 뽑는다(재구현 아님,
  같은 재료에서 다른 필드만 추가 추출).

【조건 정의 관련 발견 — 실행 전 반드시 보고】
사전등록 문서(618-614행)는 "base+candle+volume+position+상한가제외
5조건"이라고 쓰고 `2026-08-29_kr_jongga_betting_backtest_extended.py`를
출처로 인용하지만, 그 스크립트의 evaluate()엔 상한가 제외 로직이 전혀
없다(grep 확인, "상한가"/"limit" 매치 0건). "상한가제외"는 실제로는
scanner.py의 프로덕션 JONGGA_CONFIG(analyze_jongga(), upper_limit_pct=
0.30)에만 있는 5번째 조건이고, 이 백테스트 스크립트 계열엔 애초에
구현된 적이 없다. 반면 사전등록이 반복 인용하는 "n=292/+0.80%/z=3.54"는
2026-09-01_jongga_universe_v2_revalidation.results.json에서 정확히
확인되며, 그 값은 orig.evaluate()의 combo_a(base&candle&volume&position,
상한가 필터 없음) 그대로에서 나온다 — 상한가 필터를 새로 추가하면 n이
292에서 달라져 "기존 채택값 재현"이라는 유효성 게이트의 전제 자체가
깨진다. 그래서 이 스크립트는 **상한가 필터를 추가하지 않고 orig.evaluate()
의 combo_a 정의를 문자 그대로 재사용**한다 — "5조건" 문구가 아니라
"n=292 재현"이라는 명시적·검증 가능한 앵커를 따른 것. 이 판단 자체를
1단계 보고에 포함해 사용자 확인을 받는다(조건을 임의로 바꾼 게 아니라
문서 내부 모순 중 코드로 검증 가능한 쪽을 따랐다는 걸 명시).

실행: 리포 루트에서
  python3 scripts/measurements/2026-09-10_jongga_exit_timing.py --stage1
(v2 유니버스 fetch ~60초 + KR 1900일 fetch ~200초+ + 90체크포인트 평가,
2026-09-01 재측정과 동일 규모라 총 10분 내외 예상). fetch한 데이터는
로컬 캐시(.stage1_data_cache.pkl, 커밋 안 함)에 저장 — stage2 실행 시
재fetch 없이 재사용."""
import sys
import os
import json
import time
import argparse
import importlib.util
import pickle
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

import naver_kr
import universe as universe_mod
import harness

_ORIG_PATH = os.path.join("scripts", "measurements",
                           "2026-08-29_kr_jongga_betting_backtest_extended.py")
_spec = importlib.util.spec_from_file_location("jongga_orig", _ORIG_PATH)
orig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(orig)

ROUND_TRIP_COST = 0.003   # 왕복 0.3%, 전 구간 동일(사전등록)
ADOPTED_EV = 0.0080       # 기존 채택값(+0.80%)
ADOPTED_TOL = 0.0015      # ±0.15%p
ADOPTED_N = 292
ADOPTED_Z = 3.54

_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".stage1_data_cache.pkl")


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


def evaluate_exit_timing(data, off, rank_at_off):
    """orig.evaluate()로 조건판정 + (a)gap_open을 그대로 재사용(재구현
    안 함). combo_a(후보 선정 조건, 안 건드림)를 통과한 레코드에 한해
    harness.truncate_at/future_after로 얻은 같은 future 슬라이스에서
    (d)익일종가/구간내 최대낙폭(저가)/갭하락 여부만 신규 추출."""
    base_records = orig.evaluate(data, off, rank_at_off)
    out = []
    for r in base_records:
        if not (r["base"] and r["candle"] and r["volume"] and r["position"]):
            continue
        t = r["ticker"]
        df = data[t]
        hist = harness.truncate_at(df, off)
        future = harness.future_after(df, off)
        if hist.empty or future.empty:
            continue
        close_t = float(hist["Close"].iloc[-1])
        open_t1 = float(future["Open"].iloc[0])
        close_t1 = float(future["Close"].iloc[0])
        low_t1 = float(future["Low"].iloc[0])
        # 룩어헤드 assert(사전등록 명시 요구) — 매도가는 매수 시각 이후
        # 정보만 써야 한다: future의 첫 봉 날짜가 hist의 마지막(매수) 날짜
        # 이후인지 직접 확인.
        assert future.index[0] > hist.index[-1], (
            f"lookahead violation: {t} off={off} hist_last={hist.index[-1]} "
            f"future_first={future.index[0]}"
        )
        gap_close = close_t1 / close_t - 1.0   # (d) 익일 종가
        gap_down = bool(open_t1 < close_t)      # 갭하락 여부
        mdd_d = low_t1 / close_t - 1.0          # (d)구간 내 최대낙폭(저가 기준)
        out.append({
            "ticker": t, "off": off, "half": r["half"],
            "date_t": str(hist.index[-1].date()), "date_t1": str(future.index[0].date()),
            "close_t": close_t, "open_t1": open_t1, "close_t1": close_t1, "low_t1": low_t1,
            "gap_open": r["gap_open"],   # (a) — orig.evaluate() 값 그대로
            "gap_close": gap_close,      # (d) — 신규
            "gap_down": gap_down, "mdd_d": mdd_d,
        })
    return out


def stage1_stats(records, cost=ROUND_TRIP_COST):
    """(a)익일시가 재현 통계 — orig.stats()와 동일 정의(재사용), gap_open만 사용."""
    n = len(records)
    if n == 0:
        return {"n": 0}
    gaps = [r["gap_open"] for r in records]
    mean_gap = sum(gaps) / n
    up_prob = sum(1 for g in gaps if g > 0) / n
    return {"n": n, "mean_gap": mean_gap, "net_mean": mean_gap - cost, "up_prob": up_prob}


def one_sample_like_z(records, base_only_records):
    """orig.mean_gap_zscore()를 그대로 재사용 — base_only(대조군) 대비
    combo_a(gap_open)의 z. 유효성 게이트 보고용(기존 채택 z=3.54와 비교)."""
    z, sig = orig.mean_gap_zscore(base_only_records, records)
    return z, sig


def do_fetch():
    print("[stage1] v2 유니버스(m.stock.naver.com 기반) 생성 중...", flush=True)
    new_dyn, v2_stats = naver_kr.fetch_top_turnover_v2(top_n=1500)
    print(f"[stage1] fetch_top_turnover_v2 stats: {v2_stats}", flush=True)
    new_universe_tickers = {**universe_mod.KR_UNIVERSE, **new_dyn}
    print(f"[stage1] 유니버스(정적+동적v2) 티커 수: {len(new_universe_tickers)}", flush=True)
    data = fetch_universe_history(list(new_universe_tickers.keys()))
    with open(_CACHE_PATH, "wb") as f:
        pickle.dump({"data": data, "v2_stats": v2_stats,
                     "universe_ticker_count": len(new_universe_tickers)}, f)
    print(f"[stage1] fetch 결과 캐시 저장: {_CACHE_PATH}", flush=True)
    return data, v2_stats, len(new_universe_tickers)


def load_or_fetch(force_fetch=False):
    if not force_fetch and os.path.exists(_CACHE_PATH):
        print(f"[stage1] 캐시 발견({_CACHE_PATH}) — 재fetch 없이 재사용", flush=True)
        with open(_CACHE_PATH, "rb") as f:
            c = pickle.load(f)
        return c["data"], c["v2_stats"], c["universe_ticker_count"]
    return do_fetch()


def run_stage1(force_fetch=False):
    _t0 = time.time()
    data, v2_stats, universe_ticker_count = load_or_fetch(force_fetch)

    harness.assert_sufficient_depth(data, orig.OFFSETS)

    print(f"\n체크포인트별 평가 ({len(orig.OFFSETS)}개 지점)", flush=True)
    all_exit_records = []
    all_base_records = []
    for oi, off in enumerate(orig.OFFSETS):
        rank = orig.turnover_rank_at(data, off)
        base_recs = orig.evaluate(data, off, rank)
        all_base_records.extend(base_recs)
        exit_recs = evaluate_exit_timing(data, off, rank)
        all_exit_records.extend(exit_recs)
        if (oi + 1) % 15 == 0 or oi == len(orig.OFFSETS) - 1:
            print(f"[collect] offset {off} 완료 ({oi+1}/{len(orig.OFFSETS)}) "
                  f"combo_a누적={len(all_exit_records)} elapsed={time.time()-_t0:.0f}s", flush=True)

    base_only = [r for r in all_base_records if r["base"]]
    s_a = stage1_stats(all_exit_records)
    z, sig = one_sample_like_z(all_exit_records, base_only)

    gate_diff = None
    gate_pass = False
    if s_a.get("n"):
        gate_diff = s_a["net_mean"] - ADOPTED_EV
        gate_pass = abs(gate_diff) <= ADOPTED_TOL

    report = {
        "stage": 1,
        "universe_ticker_count": universe_ticker_count,
        "fetched_ticker_count": len(data),
        "v2_stats": v2_stats,
        "n_base_only": len(base_only),
        "n_combo_a_records_total": len(all_exit_records),
        "reproduction_a": s_a,
        "z_vs_base": z, "significant_vs_base": sig,
        "adopted_reference": {"net_mean": ADOPTED_EV, "n": ADOPTED_N, "z": ADOPTED_Z, "tol": ADOPTED_TOL},
        "validity_gate": {
            "diff_pp": gate_diff * 100 if gate_diff is not None else None,
            "pass": gate_pass,
        },
        "elapsed_sec": time.time() - _t0,
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "2026-09-10_jongga_exit_timing.stage1.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 70)
    print("【1단계: (a) 익일 시가 재현】")
    print("=" * 70)
    print(f"  n={s_a.get('n')}  평균갭={s_a.get('mean_gap', 0)*100:+.3f}%  "
          f"비용차감후(EV)={s_a.get('net_mean', 0)*100:+.3f}%  갭업확률={s_a.get('up_prob', 0)*100:.1f}%")
    print(f"  base 대비 z={z:.2f}" if z is not None else "  base 대비 z=계산불가")
    print(f"  기존 채택값: EV=+0.80% n={ADOPTED_N} z={ADOPTED_Z}")
    print(f"  괴리 = {gate_diff*100:+.3f}%p (허용 ±0.15%p) -> 유효성 게이트: "
          f"{'통과' if gate_pass else '미달(측정 무효)'}")
    print(f"\n결과 저장: {out_path}")
    print(f"전체 elapsed={time.time()-_t0:.0f}s")

    # stage2에서 재사용할 수 있게 exit_records/base_only도 별도 캐시에 저장
    with open(_CACHE_PATH, "rb") as f:
        cache_obj = pickle.load(f)
    cache_obj["all_exit_records"] = all_exit_records
    cache_obj["all_base_records"] = all_base_records
    with open(_CACHE_PATH, "wb") as f:
        pickle.dump(cache_obj, f)

    return report


# ══════════════════════════════════════════════════════════════════
# 2단계: (d) 익일종가 계산 + 판정 (사용자 승인 후 실행, 2026-09-10)
# ══════════════════════════════════════════════════════════════════
JUDGMENT_MIN_DIFF = 0.0030   # +0.30%p
JUDGMENT_Z = 1.96
JUDGMENT_MIN_N = 100


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def paired_test(records):
    """d_i = gap_close(d) - gap_open(a), 대응표본 양측 z검정. 비용은
    양쪽에 동일하게 적용돼 차이(d_i)에선 상쇄되므로 raw gap으로 계산해도
    net 기준 차이와 값이 같다(사전등록: "비용 왕표 0.3%, 전 구간 동일 적용")."""
    diffs = [r["gap_close"] - r["gap_open"] for r in records]
    n = len(diffs)
    if n < 2:
        return {"n": n, "mean_diff": _mean(diffs), "z": None, "significant": False}
    mean_d = sum(diffs) / n
    var_d = sum((x - mean_d) ** 2 for x in diffs) / (n - 1)
    se = (var_d / n) ** 0.5
    z = mean_d / se if se > 0 else None
    sig = z is not None and abs(z) >= JUDGMENT_Z
    return {"n": n, "mean_diff": mean_d, "std_diff": var_d ** 0.5, "z": z, "significant": sig}


def segment_report(records, cost=ROUND_TRIP_COST):
    """사전등록 "추가 보고 항목" — 구간별 승률/최대낙폭(저가기준, (d)만)/
    갭하락 비율 + 두 구간(a)/(d) 요약 통계."""
    n = len(records)
    if n == 0:
        return {"n": 0}
    a_gaps = [r["gap_open"] for r in records]
    d_gaps = [r["gap_close"] for r in records]
    a_net = [g - cost for g in a_gaps]
    d_net = [g - cost for g in d_gaps]
    mdd_list = [r["mdd_d"] for r in records]
    gap_down_list = [1.0 if r["gap_down"] else 0.0 for r in records]
    return {
        "n": n,
        "a": {"mean_gap": _mean(a_gaps), "net_mean": _mean(a_net),
              "win_rate": sum(1 for x in a_net if x > 0) / n},
        "d": {"mean_gap": _mean(d_gaps), "net_mean": _mean(d_net),
              "win_rate": sum(1 for x in d_net if x > 0) / n},
        "diff_d_minus_a_pp": (_mean(d_net) - _mean(a_net)) * 100,
        "mdd_d_mean_pct": _mean(mdd_list) * 100,
        "mdd_d_worst_pct": min(mdd_list) * 100,
        "gap_down_rate_pct": _mean(gap_down_list) * 100,
    }


def investigate_z_gap(all_exit_records, all_base_records):
    """추가 보고 — 기준(a)의 base 대비 z가 1.80으로 기존 3.54보다 낮은 이유
    (n 감소만으로 설명되는지 vs 표본 구성 변화인지)."""
    base_only = [r for r in all_base_records if r["base"]]
    combo_gaps = [r["gap_open"] for r in all_exit_records]
    base_gaps = [r["gap_open"] for r in base_only]
    n_c, n_b = len(combo_gaps), len(base_gaps)
    m_c, m_b = _mean(combo_gaps), _mean(base_gaps)
    var_c = sum((x - m_c) ** 2 for x in combo_gaps) / (n_c - 1)
    var_b = sum((x - m_b) ** 2 for x in base_gaps) / (n_b - 1)
    se = (var_c / n_c + var_b / n_b) ** 0.5
    z_actual = (m_c - m_b) / se
    # n만 292로 되돌렸을 때(같은 평균/분산 가정) z가 얼마나 바뀌는지 — 감도 확인
    se_n292 = (var_c / 292 + var_b / n_b) ** 0.5
    z_if_n292 = (m_c - m_b) / se_n292
    return {
        "today_combo_a": {"n": n_c, "mean_gap_pct": m_c * 100, "std_pct": var_c ** 0.5 * 100},
        "today_base": {"n": n_b, "mean_gap_pct": m_b * 100, "std_pct": var_b ** 0.5 * 100},
        "reference_2026_09_01": {
            "combo_a": {"n": 292, "mean_gap_pct": 1.1023},
            "base": {"n": 8329, "mean_gap_pct": 0.2135},
            "z": 3.537,
        },
        "z_actual_today": z_actual,
        "z_if_n_restored_to_292_same_mean_var": z_if_n292,
        "gap_vs_base_pp_today": (m_c - m_b) * 100,
        "gap_vs_base_pp_2026_09_01": (1.1023 - 0.2135),
    }


def run_stage2():
    _t0 = time.time()
    if not os.path.exists(_CACHE_PATH):
        raise SystemExit("stage1 캐시가 없습니다 — 먼저 --stage1을 실행할 것")
    with open(_CACHE_PATH, "rb") as f:
        cache_obj = pickle.load(f)
    all_exit_records = cache_obj.get("all_exit_records")
    all_base_records = cache_obj.get("all_base_records")
    if all_exit_records is None or all_base_records is None:
        raise SystemExit("stage1 캐시에 exit_records가 없습니다 — --stage1을 다시 실행할 것")

    # 룩어헤드 assert는 evaluate_exit_timing()에서 레코드 생성 시점에
    # 이미 전부 확인됨(assert가 여기 도달했다는 것 자체가 위반 0건이라는 뜻).

    overall = segment_report(all_exit_records)
    pt_overall = paired_test(all_exit_records)

    earlier = [r for r in all_exit_records if r["half"] == "earlier"]
    recent = [r for r in all_exit_records if r["half"] == "recent"]
    seg_earlier = segment_report(earlier)
    seg_recent = segment_report(recent)
    pt_earlier = paired_test(earlier)
    pt_recent = paired_test(recent)

    z_investigation = investigate_z_gap(all_exit_records, all_base_records)

    # ── 판정(사전등록 그대로, 완화 없음) ──
    cond_diff = overall["diff_d_minus_a_pp"] >= JUDGMENT_MIN_DIFF * 100
    cond_z = pt_overall["significant"]
    half_reproduced = (
        seg_earlier["n"] >= JUDGMENT_MIN_N and seg_recent["n"] >= JUDGMENT_MIN_N and
        seg_earlier["diff_d_minus_a_pp"] >= JUDGMENT_MIN_DIFF * 100 and
        seg_recent["diff_d_minus_a_pp"] >= JUDGMENT_MIN_DIFF * 100
    )
    cond_n = overall["n"] >= JUDGMENT_MIN_N
    passed = cond_diff and cond_z and half_reproduced and cond_n

    report = {
        "stage": 2,
        "overall": overall,
        "paired_test_overall": pt_overall,
        "half_earlier": seg_earlier, "paired_test_earlier": pt_earlier,
        "half_recent": seg_recent, "paired_test_recent": pt_recent,
        "z_gap_investigation": z_investigation,
        "judgment": {
            "cond_diff_ge_030pp": cond_diff,
            "cond_z_ge_196": cond_z,
            "cond_half_reproduced": half_reproduced,
            "cond_n_ge_100": cond_n,
            "passed": passed,
        },
        # 히트별 원자료(사전등록 명시: "재fetch 없이 재확인 가능하게")
        "raw_records": all_exit_records,
        "elapsed_sec": time.time() - _t0,
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "2026-09-10_jongga_exit_timing.stage2.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 70)
    print("【2단계: (d) 익일 종가 — 판정】")
    print("=" * 70)
    print(f"  전체 n={overall['n']}")
    print(f"  (a) 비용차감후 평균={overall['a']['net_mean']*100:+.3f}%  승률={overall['a']['win_rate']*100:.1f}%")
    print(f"  (d) 비용차감후 평균={overall['d']['net_mean']*100:+.3f}%  승률={overall['d']['win_rate']*100:.1f}%")
    print(f"  (d)-(a) = {overall['diff_d_minus_a_pp']:+.3f}%p")
    print(f"  대응표본 z = {pt_overall['z']:.3f}" if pt_overall['z'] is not None else "  z=계산불가")
    print(f"  (d)구간 최대낙폭(저가기준) 평균={overall['mdd_d_mean_pct']:+.3f}%  최악={overall['mdd_d_worst_pct']:+.3f}%")
    print(f"  갭하락 비율={overall['gap_down_rate_pct']:.1f}%")
    print(f"\n  이전 절반: n={seg_earlier['n']} diff={seg_earlier['diff_d_minus_a_pp']:+.3f}%p z={pt_earlier['z']:.3f}"
          if pt_earlier['z'] is not None else f"\n  이전 절반: n={seg_earlier['n']} z=계산불가")
    print(f"  최근 절반: n={seg_recent['n']} diff={seg_recent['diff_d_minus_a_pp']:+.3f}%p z={pt_recent['z']:.3f}"
          if pt_recent['z'] is not None else f"  최근 절반: n={seg_recent['n']} z=계산불가")
    print(f"\n  판정 조건: (d)-(a)>=+0.30%p[{cond_diff}] z>=1.96[{cond_z}] "
          f"시기반분재현[{half_reproduced}] n>=100[{cond_n}]")
    print(f"  ==> {'통과 — (d)익일종가 채택, 별개 전략으로 분리' if passed else '미달 — 현행(a) 유지'}")
    print(f"\n결과 저장: {out_path}")
    print(f"elapsed={time.time()-_t0:.1f}s")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage1", action="store_true", help="1단계: (a) 재현만 계산")
    parser.add_argument("--stage2", action="store_true", help="2단계: (d) 계산 + 판정 (stage1 캐시 재사용)")
    parser.add_argument("--force-fetch", action="store_true", help="캐시 무시하고 재fetch(1단계 전용)")
    args = parser.parse_args()

    if args.stage1:
        run_stage1(force_fetch=args.force_fetch)
    elif args.stage2:
        run_stage2()
    else:
        print("사용법: --stage1 (재fetch+재현) 또는 --stage2 (stage1 캐시로 판정, 재fetch 없음)")
