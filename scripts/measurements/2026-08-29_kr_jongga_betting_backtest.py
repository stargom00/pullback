"""
KR 종가베팅 전략 백테스트 — 탭 신설 전 검증 (2026-08-29, 사용자 지시).
측정 스크립트만 — scanner.py/app.py 미수정. 공통 하네스(harness.py)
재사용 가능한 부분(checkpoints/truncate_at/future_after/off_high_pct)은
그대로 가져다 쓰되, "종가베팅"(T일 종가매수→T+1시가매도) 자체는
scanner.py에 대응하는 기존 함수가 전혀 없는 신규 전략이라 조건 계산·
수익률 계산 로직은 새로 작성했다 — 재구현 금지 원칙은 "이미 있는
production 함수를 다시 만들지 말라"는 뜻이지, 원래 없던 신규 백테스트
로직 자체를 막는 게 아니다.

【전략 정의】
T일 종가 매수 → T+1일 시초가 매도(기본). 보조로 T+1일 고가 도달
시나리오도 병기 — 단, 이 레포가 가진 건 일봉(daily OHLC)뿐이라 "오전
고가"가 아니라 **T+1 하루 전체의 고가**로 근사한다(장중 타임스탬프
데이터 없음 — 이 한계를 그대로 명시).

【후보 조건】
- base: 당일(T) 거래대금(종가×거래량) KR 전종목 상위 100 이내
- candle: 당일 등락률 +3%↑ & 종가가 당일 고가 대비 -2% 이내(윗꼬리 짧음)
- volume: 당일 거래량이 직전 20거래일(T 제외) 평균 대비 2배 이상
- position: 종가 > 20일 이동평균 & 52주 고가 대비 -15% 이내
  (off_high_pct >= -15, scanner.off_high_pct() 재사용 — 재구현 금지)
- theme(당일 강세 테마 소속): **측정 불가로 제외**. money_flow_data/kr/
  에 스냅샷이 2026-08-26 단 하루치만 존재(2026-08-29 확인) — 사용자가
  명시한 "데이터 있는 기간만" 조건을 적용하면 usable 기간이 사실상
  0일이라 백테스트 자체가 성립하지 않는다. 조건에서 완전히 뺐다(추정치
  로 채우지 않음 — CLAUDE.md "백테스트 없는 승률 주장 금지" 원칙).

【데이터 소스 — harness 기본값과 다른 이유】
harness.fetch_universe_data()의 KR fetch(`naver_kr.fetch()`)는 730
calendar일(≈490 거래일)만 받아온다. 이 백테스트는 off=250(최대
체크포인트)에 52주(252봉) lookback을 얹으므로 502봉 이상이 필요해
harness 기본값으로는 표본이 크게 줄어든다 — 이 스크립트만
`naver_kr.fetch_history(ticker, days=1100)`(≈3년, ≈730거래일)로 직접
fetch한다(harness.py 확장 안 하고 스크립트 로컬 헬퍼로 처리 — 이
백테스트 전용 요구사항이라 공용 함수에 넣지 않음).

【측정】
1. 조건 단독(base/candle/volume/position 각각 독립 적용) + 누적 조합
   (base → +candle → +volume → +position) — T+1 시가갭 평균/중앙값,
   갭업 확률, 비용(왕복 0.3%) 차감 후 평균 수익률, n. 보조로 T+1 고가
   기준도 병기.
2. 시간 반분(이전 절반 off160~250 / 최근 절반 off60~150 — 세션 전체
   공용 표준) 재현 확인 — 최종 조합(base+candle+volume+position) 대상.
3. 대조군 = base(거래대금 상위100) 단독 코호트의 평균 익일 갭. 최종
   조합과의 평균 차이 z검정.

규칙6(대조군 유동성매칭): 대조군 자체가 base(유동성 상위 100)라 그대로
충족. 규칙7(z검정): harness.ev_gap_zscore는 -1/0/+2R 이산분포 전용
공식(README)이라 이 연속형 % 수익률엔 안 맞음 — 표준 2표본 평균차
z검정을 이 스크립트에 새로 작성(재구현 아님, 애초에 다른 데이터
타입용 통상 공식). 규칙8: KR 단일 시장만 다뤄 해당 없음.

【사전 판정 기준 — 측정 전 고정】
최종 조합(base+candle+volume+position)이 (a) 비용 차감 후 평균 갭
+0.5% 이상 **그리고** (b) 이전/최근 절반 둘 다 비용 차감 후 평균 갭
+0.5% 이상(같은 기준을 양쪽에 적용 — RSI<50 시간분할 캠페인과 동일
관례) **그리고** (c) 대조군(base) 대비 z>=1.96로 유의 우위 → 탭 신설
진행. 하나라도 미달이면 → 조건 재설계 또는 보류.

실행: 리포 루트에서
  python3 scripts/measurements/2026-08-29_kr_jongga_betting_backtest.py
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

OFFSETS = harness.checkpoints(60, 250, 10)
RECENT_HALF = set(range(60, 151, 10))
EARLIER_HALF = set(range(160, 251, 10))
ROUND_TRIP_COST = 0.003     # 왕복 수수료+슬리피지 0.3%(사용자 지시)
FETCH_DAYS = 1100           # off250 + 252lookback + 여유 확보용(모듈 docstring 참고)
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
    """day-T(off) 시점 KR 전종목 거래대금(종가×거래량) 순위. 1=최고."""
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
    """day-T(off) 조건 평가 + T+1 시가/고가 갭. 반환: 레코드 리스트."""
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
        vol20 = v.iloc[-21:-1]   # T 제외 직전 20거래일
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
    """연속형 % 수익률 두 그룹 평균차 z검정(표준 2표본, 표본분산) —
    harness.ev_gap_zscore는 -1/0/+2R 이산분포 전용 공식이라 이 데이터
    타입엔 안 맞아 새로 작성(모듈 docstring 참고, 재구현 아님)."""
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


if __name__ == "__main__":
    _t0 = time.time()
    data = fetch_kr_long_universe()

    print("\n" + "=" * 70)
    print("체크포인트별 조건 평가 + T+1 갭 수집")
    print("=" * 70)
    all_records = []
    for oi, off in enumerate(OFFSETS):
        rank = turnover_rank_at(data, off)
        recs = evaluate(data, off, rank)
        all_records.extend(recs)
        n_base = sum(1 for r in recs if r["base"])
        print(f"[collect] offset {off} 완료 ({oi+1}/{len(OFFSETS)}) 평가대상={len(recs)} "
              f"base통과={n_base} 누적={len(all_records)} elapsed={time.time()-_t0:.0f}s", flush=True)

    print(f"\n총 평가 레코드 {len(all_records)}건 (base 통과 {sum(1 for r in all_records if r['base'])}건)")

    print("\n" + "=" * 70)
    print("【측정 1】 조건 단독")
    print("=" * 70)
    base_only = [r for r in all_records if r["base"]]
    candle_only = [r for r in all_records if r["candle"]]
    volume_only = [r for r in all_records if r["volume"]]
    position_only = [r for r in all_records if r["position"]]
    print_stats("base 단독(거래대금 상위100)", base_only)
    print_stats("candle 단독(+3%양봉·짧은윗꼬리)", candle_only)
    print_stats("volume 단독(20일평균 2배+)", volume_only)
    print_stats("position 단독(20일선 위·52주고점-15%이내)", position_only)

    print("\n" + "=" * 70)
    print("【측정 1】 누적 조합 (base → +candle → +volume → +position)")
    print("=" * 70)
    combo1 = [r for r in all_records if r["base"]]
    combo2 = [r for r in combo1 if r["candle"]]
    combo3 = [r for r in combo2 if r["volume"]]
    combo4 = [r for r in combo3 if r["position"]]
    print_stats("① base", combo1)
    print_stats("② base+candle", combo2)
    print_stats("③ base+candle+volume", combo3)
    print_stats("④ base+candle+volume+position (최종)", combo4)

    print("\n" + "=" * 70)
    print("【측정 2】 시간 반분 재현 확인 — 최종 조합(④)")
    print("=" * 70)
    earlier_final = [r for r in combo4 if r["half"] == "earlier"]
    recent_final = [r for r in combo4 if r["half"] == "recent"]
    s_earlier = print_stats("이전 절반(off160~250)", earlier_final)
    s_recent = print_stats("최근 절반(off60~150)", recent_final)
    earlier_ok = s_earlier.get("n", 0) > 0 and s_earlier.get("net_mean", -1) >= 0.005
    recent_ok = s_recent.get("n", 0) > 0 and s_recent.get("net_mean", -1) >= 0.005
    print(f"    → 이전 절반 {'재현됨' if earlier_ok else '미달'}, 최근 절반 {'재현됨' if recent_ok else '미달'} "
          f"(기준: 비용차감후 평균갭 +0.5%↑)")

    print("\n" + "=" * 70)
    print("【측정 3】 대조군(base) 대비 최종 조합(④) 유의성")
    print("=" * 70)
    z, sig = mean_gap_zscore(base_only, combo4)
    s_base = stats(base_only)
    s_combo4 = stats(combo4)
    if z is not None:
        gap_vs_base = s_combo4["mean_gap"] - s_base["mean_gap"]
        print(f"    base 평균갭={s_base['mean_gap']*100:+.2f}%(n={s_base['n']}) vs "
              f"최종조합 평균갭={s_combo4['mean_gap']*100:+.2f}%(n={s_combo4['n']})")
        print(f"    격차(최종-base)={gap_vs_base*100:+.2f}%p  z={z:.2f}  {'유의(|z|>=1.96)' if sig else '유의하지 않음'}")
    else:
        print("    표본 부족 — z검정 불가")
        gap_vs_base = None

    print("\n" + "=" * 70)
    print("【사전 등록 판정】")
    print("=" * 70)
    final_net = s_combo4.get("net_mean")
    cond_a = final_net is not None and final_net >= 0.005
    cond_b = earlier_ok and recent_ok
    cond_c = (z is not None) and sig and (gap_vs_base is not None and gap_vs_base > 0)
    print(f"  (a) 최종조합 비용차감후 평균갭 >= +0.5%: {'충족' if cond_a else '미달'}"
          f"{f' ({final_net*100:+.2f}%)' if final_net is not None else ' (표본없음)'}")
    print(f"  (b) 이전·최근 절반 둘 다 재현: {'충족' if cond_b else '미달'}")
    print(f"  (c) 대조군(base) 대비 유의 우위: {'충족' if cond_c else '미달'}")
    if cond_a and cond_b and cond_c:
        print("\n  → 전부 충족: 탭 신설 진행 검토 대상.")
    else:
        print("\n  → 하나 이상 미달: 조건 재설계 또는 보류.")

    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)
