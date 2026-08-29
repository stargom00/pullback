"""
KR 종가베팅 전략 백테스트 — 사전 등록 재설계, 1회만 (2026-08-29, 사용자
지시). 측정 스크립트만 — scanner.py/app.py 미수정. 원측정
(`2026-08-29_kr_jongga_betting_backtest.py`, docs/kr_jongga_betting_
backtest.md)의 진단 2가지를 반영해 재설계했다:
  ① candle 조건이 단독으로는 역효과(갭업확률 41.1%)로 확인됨 → 후보
     조합에 candle 제외 버전을 추가 비교.
  ② 최종조합 n=70으로 검정력 부족(z=1.84, 기준 1.96 근소 미달) →
     체크포인트 밀도를 4.5배로 늘려 n을 확보.

이 재설계는 **1회만** 수행 — 이후 사후 다이빙 방지를 위해 조건을
더 바꾸지 않고 표본(실행일) 축적으로만 재검증한다(사용자 지시).

【① n 확보 — 데이터 기간 가용 최대 확장】
naver_kr.fetch_history()는 요청 기간에 실질 상한이 없는 것으로 이미
확인돼 있고(fetch당 소요시간이 400/730/1095일 전부 동일 — 왕복지연이
지배적, naver_kr.py fetch_history docstring), `docs/abc_doc_style_tab_
investigation.md`에서 1900일(≈1275봉) fetch가 실제로 성공한 전례가
있다(730→1900일 회귀 0건 확인). 이 스크립트도 `days=1900`으로 fetch
— 실측 1274봉 확보(005930.KS로 사전 확인, 2021-06-16~2026-08-28).
체크포인트 범위를 원측정의 60~250(20개)에서 **60~950(90개, 10 간격)**
로 확장 — max offset 950 + lookback 260 = 1210봉 필요, 1274봉 확보분
대비 64봉 여유. 원측정 대비 체크포인트 수 4.5배(20→90).

【② 후보 조합 2개 — 사전 고정】
- **(A) 원조합 재측정**: base+candle+volume+position(원측정과 조건
  정의 동일, 확장된 기간·체크포인트로 재수집만).
- **(B) candle 제외**: base+volume+position — 원측정에서 candle이
  단독 역효과였다는 진단을 반영.
둘 다 같은 evaluate() 수집 결과에서 사후 필터링만 다르게 적용(재수집
2번 안 함 — fetch/평가는 1회, 조합 분리만 2가지).

【조건 정의 — 원측정과 완전 동일, 변경 없음】
- base: 당일(T) 거래대금(종가×거래량) KR 전종목 상위 100 이내
- candle: 당일 등락률 +3%↑ & 종가가 당일 고가 대비 -2% 이내(윗꼬리 짧음)
- volume: 당일 거래량이 직전 20거래일(T 제외) 평균 대비 2배 이상
- position: 종가 > 20일 이동평균 & 52주 고가 대비 -15% 이내
  (`scanner.off_high_pct()` 재사용)
- theme: 이번에도 미포함(money_flow_data 스냅샷 여전히 1일치뿐) —
  판정에서 미달 시 "3~4주 축적 후 재측정" 예약을 docs에 남긴다.

【사전 판정 기준 — 원측정과 동일, 두 조합에 각각 적용】
(a) 비용차감후(왕복 0.3%) 평균갭 +0.5% 이상 AND (b) 이전/최근 절반
(체크포인트 리스트를 정확히 반으로 나눔 — 아래 참고) 둘 다 (a)와 같은
기준 재현 AND (c) 대조군(base) 대비 z>=1.96 유의 우위 — 셋 다 충족하는
조합이 하나라도 있으면 채택(탭 신설 진행). 둘 다 미달이면 보류 확정
+ theme 조건 포함 재측정을 후속 과제로 예약.

실행: 리포 루트에서
  python3 scripts/measurements/2026-08-29_kr_jongga_betting_backtest_extended.py
(원측정 대비 체크포인트 4.5배라 수집 단계가 더 걸림 — fetch 자체는
비슷(요청기간 무관하게 왕복지연 지배적), collect 단계가 90개 지점이라
원측정 대비 몇 배 더 소요 예상)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import naver_kr
from universe import get_universe

import harness
from scanner import off_high_pct

OFFSETS = harness.checkpoints(60, 950, 10)   # 90개 — n 확보 목적 확장(모듈 docstring 참고)
_half_idx = len(OFFSETS) // 2
RECENT_HALF = set(OFFSETS[:_half_idx])       # 작은 offset(최근) 절반
EARLIER_HALF = set(OFFSETS[_half_idx:])      # 큰 offset(이전) 절반
ROUND_TRIP_COST = 0.003
FETCH_DAYS = 1900            # ≈1274봉 실측 확보(005930.KS 사전 확인)
MIN_BARS_AFTER_OFFSET = 260  # 52주 lookback(252) + 여유 8봉


def _fetch_kr_long(ticker):
    try:
        df = naver_kr.fetch_history(ticker, days=FETCH_DAYS)
        if df is None or df.empty:
            return ticker, None
        return ticker, df
    except Exception:
        return ticker, None


def fetch_kr_long_universe(concurrency=10):
    kr_u = get_universe("kr")
    data = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = {ex.submit(_fetch_kr_long, t): t for t in kr_u}
        done = 0
        for fut in as_completed(futs):
            t, df = fut.result()
            if df is not None:
                data[t] = df
            done += 1
            if done % 300 == 0:
                print(f"[fetch] {done}/{len(kr_u)} elapsed={time.time()-t0:.0f}s", flush=True)
    print(f"[fetch] 완료 {len(data)}/{len(kr_u)} elapsed={time.time()-t0:.0f}s", flush=True)
    return data


def turnover_rank_at(data, off):
    turnovers = {}
    for t, df in data.items():
        if len(df) - off < 1:
            continue
        hist = harness.truncate_at(df, off)
        if hist.empty:
            continue
        close = float(hist["Close"].iloc[-1])
        vol = float(hist["Volume"].iloc[-1])
        turnovers[t] = close * vol
    ranked = sorted(turnovers.items(), key=lambda kv: kv[1], reverse=True)
    return {t: i + 1 for i, (t, _) in enumerate(ranked)}


def evaluate(data, off, rank_at_off):
    out = []
    half = "recent" if off in RECENT_HALF else ("earlier" if off in EARLIER_HALF else None)
    for t, df in data.items():
        n = len(df) - off
        if n < MIN_BARS_AFTER_OFFSET:
            continue
        hist = harness.truncate_at(df, off)
        future = harness.future_after(df, off)
        if future.empty:
            continue
        c, h, v = hist["Close"], hist["High"], hist["Volume"]
        close_t = float(c.iloc[-1])
        prev_close = float(c.iloc[-2])
        high_t = float(h.iloc[-1])
        if close_t <= 0 or prev_close <= 0:
            continue

        ret_t = close_t / prev_close - 1.0
        wick_ok = (close_t >= high_t * 0.98) if high_t > 0 else False
        candle_ok = (ret_t >= 0.03) and wick_ok

        vol_t = float(v.iloc[-1])
        vol20 = v.iloc[-21:-1]
        vol20_avg = float(vol20.mean()) if len(vol20) == 20 else None
        volume_ok = bool(vol20_avg is not None and vol20_avg > 0 and vol_t >= vol20_avg * 2)

        ma20 = float(c.iloc[-20:].mean())
        off_high = off_high_pct(c, 252)
        position_ok = (close_t > ma20) and (off_high >= -15.0)

        rank = rank_at_off.get(t)
        base_ok = (rank is not None and rank <= 100)

        open_t1 = float(future["Open"].iloc[0])
        high_t1 = float(future["High"].iloc[0])
        gap_open = open_t1 / close_t - 1.0
        gap_high = high_t1 / close_t - 1.0

        out.append({
            "ticker": t, "off": off, "half": half,
            "base": base_ok, "candle": candle_ok, "volume": volume_ok, "position": position_ok,
            "gap_open": gap_open, "gap_high": gap_high,
        })
    return out


def stats(records, cost=ROUND_TRIP_COST):
    n = len(records)
    if n == 0:
        return {"n": 0}
    gaps = sorted(r["gap_open"] for r in records)
    gaps_high = [r["gap_high"] for r in records]
    mean_gap = sum(gaps) / n
    median_gap = gaps[n // 2] if n % 2 == 1 else (gaps[n // 2 - 1] + gaps[n // 2]) / 2
    up_prob = sum(1 for g in gaps if g > 0) / n
    mean_gap_high = sum(gaps_high) / n
    return {
        "n": n, "mean_gap": mean_gap, "median_gap": median_gap, "up_prob": up_prob,
        "net_mean": mean_gap - cost,
        "mean_gap_high": mean_gap_high, "net_mean_high": mean_gap_high - cost,
    }


def print_stats(label, records):
    s = stats(records)
    if s["n"] == 0:
        print(f"    {label}: n=0")
        return s
    print(f"    {label}: n={s['n']} 평균갭={s['mean_gap']*100:+.2f}% 중앙값={s['median_gap']*100:+.2f}% "
          f"갭업확률={s['up_prob']*100:.1f}% 비용차감후={s['net_mean']*100:+.2f}% "
          f"(고가기준 참고: 평균={s['mean_gap_high']*100:+.2f}% 비용차감후={s['net_mean_high']*100:+.2f}%)")
    return s


def mean_gap_zscore(records_a, records_b):
    ga = [r["gap_open"] for r in records_a]
    gb = [r["gap_open"] for r in records_b]
    na, nb = len(ga), len(gb)
    if na < 2 or nb < 2:
        return None, False
    ma_ = sum(ga) / na
    mb_ = sum(gb) / nb
    va = sum((x - ma_) ** 2 for x in ga) / (na - 1)
    vb = sum((x - mb_) ** 2 for x in gb) / (nb - 1)
    se = (va / na + vb / nb) ** 0.5
    if se == 0:
        return None, False
    z = (mb_ - ma_) / se
    return z, abs(z) >= 1.96


def evaluate_combo(label, combo_records, base_only):
    print(f"\n  === 조합 {label} ===")
    print_stats(f"{label} 전체", combo_records)
    earlier = [r for r in combo_records if r["half"] == "earlier"]
    recent = [r for r in combo_records if r["half"] == "recent"]
    s_earlier = print_stats(f"{label} 이전 절반", earlier)
    s_recent = print_stats(f"{label} 최근 절반", recent)
    earlier_ok = s_earlier.get("n", 0) > 0 and s_earlier.get("net_mean", -1) >= 0.005
    recent_ok = s_recent.get("n", 0) > 0 and s_recent.get("net_mean", -1) >= 0.005
    print(f"    시간반분: 이전 {'재현' if earlier_ok else '미달'} / 최근 {'재현' if recent_ok else '미달'}")

    z, sig = mean_gap_zscore(base_only, combo_records)
    s_base = stats(base_only)
    s_combo = stats(combo_records)
    gap_vs_base = None
    if z is not None:
        gap_vs_base = s_combo["mean_gap"] - s_base["mean_gap"]
        print(f"    base 대비: 격차={gap_vs_base*100:+.2f}%p z={z:.2f} {'유의' if sig else '유의하지 않음'}")
    else:
        print("    base 대비: 표본 부족 — z검정 불가")

    cond_a = s_combo.get("net_mean") is not None and s_combo["net_mean"] >= 0.005
    cond_b = earlier_ok and recent_ok
    cond_c = (z is not None) and sig and (gap_vs_base is not None and gap_vs_base > 0)
    passed = cond_a and cond_b and cond_c
    print(f"    → (a){'충족' if cond_a else '미달'} (b){'충족' if cond_b else '미달'} "
          f"(c){'충족' if cond_c else '미달'}  ==>  {'채택 후보' if passed else '미달'}")
    return passed


if __name__ == "__main__":
    _t0 = time.time()
    data = fetch_kr_long_universe()

    print("\n" + "=" * 70)
    print(f"체크포인트별 조건 평가 + T+1 갭 수집 ({len(OFFSETS)}개 지점)")
    print("=" * 70)
    all_records = []
    for oi, off in enumerate(OFFSETS):
        rank = turnover_rank_at(data, off)
        recs = evaluate(data, off, rank)
        all_records.extend(recs)
        if (oi + 1) % 10 == 0 or oi == len(OFFSETS) - 1:
            n_base = sum(1 for r in all_records if r["base"])
            print(f"[collect] offset {off} 완료 ({oi+1}/{len(OFFSETS)}) 누적={len(all_records)} "
                  f"base누적={n_base} elapsed={time.time()-_t0:.0f}s", flush=True)

    print(f"\n총 평가 레코드 {len(all_records)}건")
    base_only = [r for r in all_records if r["base"]]
    print(f"base 통과 {len(base_only)}건")

    combo_a = [r for r in base_only if r["candle"] and r["volume"] and r["position"]]
    combo_b = [r for r in base_only if r["volume"] and r["position"]]

    print("\n" + "=" * 70)
    print("【사전 등록 후보 조합 비교】")
    print("=" * 70)
    passed_a = evaluate_combo("(A) base+candle+volume+position (원조합)", combo_a, base_only)
    passed_b = evaluate_combo("(B) base+volume+position (candle 제외)", combo_b, base_only)

    print("\n" + "=" * 70)
    print("【최종 판정】")
    print("=" * 70)
    if passed_a or passed_b:
        winners = [lbl for lbl, p in [("A", passed_a), ("B", passed_b)] if p]
        print(f"  채택: 조합 {', '.join(winners)} 기준 충족 — 탭 신설 진행 검토 대상.")
    else:
        print("  둘 다 미달 — 보류 확정.")
        print("  후속 예약: 돈의흐름(money_flow) 테마 데이터 3~4주 축적 후 "
              "theme 조건 포함 재측정 (현재 money_flow_data/kr/ 스냅샷 1일치뿐).")

    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)
