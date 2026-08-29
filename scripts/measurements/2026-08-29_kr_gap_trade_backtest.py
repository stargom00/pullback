"""
KR 갭 매매 백테스트 — 시초가 단타의 일봉 검증 버전 (2026-08-29, 사용자
지시). 측정 스크립트만 — scanner.py/app.py 미수정. 종가베팅 백테스트
인프라(`2026-08-29_kr_jongga_betting_backtest_extended.py`)를 그대로
재활용 — fetch(1900일)/체크포인트(60~950, 90개)/거래대금 랭킹/조건
판정(base·candle·volume·position) 로직이 전부 동일. 이 스크립트는 그
위에 "T+1 시가매수→종가매도"라는 새 수익률 측정만 얹는다.

【전략 정의】
T일에 종가베팅 채택 조건(base+candle+volume+position)을 통과한 종목이
T+1일 갭업 출발하면 시가 매수 → 당일 종가 매도. 일봉 데이터로 가능한
근사(장중 매수 타이밍은 "시가 직후"로 가정) — 장중 고가 시나리오는
참고치로만 병기(장중 타임스탬프 데이터 없음 — 실제 그 고가에 팔 수
있었다는 보장 없음, 이 레포 다른 백테스트들과 동일한 한계).

【후보 조건 — 종가베팅과 완전 동일, 재구현 안 함】
- base: 당일(T) 거래대금(종가×거래량) KR 전종목 상위 100 이내
- candle: 당일 등락률 +3%↑ & 종가가 당일 고가 대비 -2% 이내(윗꼬리 짧음)
- volume: 당일 거래량이 직전 20거래일(T 제외) 평균 대비 2배 이상
- position: 종가 > 20일 이동평균 & 52주 고가 대비 -15% 이내
- 상한가(전일比+30%) 근접 제외는 "종가 매수" 전용 제약이라 이 스크립트엔
  적용 안 함 — 갭 매매는 T+1일 시가에 사는 것이라 T일 상한가 여부와
  무관(오히려 T일 상한가 근접이 T+1 갭업으로 이어지는 흔한 패턴이라
  빼면 표본이 부당하게 줄어듦). 종가베팅과의 유일한 조건 차이.

【측정】
1. T+1 갭 크기 구간별(갭다운 / 0~+2% / +2~5% / +5%+) → 시가매수 시 당일
   수익률(시가→종가, 비용 0.3% 차감): 평균/중앙값/승률/n. 참고로 고가
   기준(시가→고가) 병기.
2. 시간 반분(이전 절반 off530~950 / 최근 절반 off60~520 — 종가베팅
   확장판과 동일 체크포인트 리스트를 정확히 반으로 나눈 것) 재현 확인.
3. 대조군: base(거래대금 상위100) 전체에 **동일 갭 구간 분류 + 동일
   측정**을 적용(그냥 base 전체 평균이 아니라 갭 구간별로 맞춰 비교 —
   "조건 4개 통과가 갭 구간 효과와 별개로 추가 가치가 있는가"를 갭
   크기라는 교란 변수를 통제한 채 보려는 목적, README 규칙6의 "동일
   유동성 컷 대조군" 정신과 같은 이유).
4. 종가베팅(T종가매수) vs 갭매매(T+1시가매수) 비교표 — 같은 히트
   레코드에서 두 수익률을 동시에 계산해(gap = 종가베팅 수익률 그 자체)
   갭 구간별로 나란히 표시.

규칙6: 위 3번이 대조군 매칭. 규칙7: harness 스타일 2표본 z검정을
gap-trade 전용으로 재사용(연속형 % 수익률 — jongga 스크립트에서 쓴
공식과 동일, harness.ev_gap_zscore는 이산 R분포 전용이라 부적합).
규칙8: KR 단일 시장만 다뤄 해당 없음.

【사전 등록 판정 기준】
어떤 갭 구간이든 (a) 비용차감후 평균수익률 +0.5%↑ AND (b) 이전/최근
절반 둘 다 같은 기준 재현 AND (c) 그 구간에 매칭된 base 대조군 대비
z>=1.96 유의 우위 — 셋 다 충족하면 그 구간을 "갭 매매 유효 구간"으로
채택(탭 신설 여부는 결과 보고 후 별도 결정). 전 구간 미달이면 "갭업
추격은 일봉 수준에서 엣지 없음"으로 확정 기록(분봉 확보 전까지 재논쟁
방지).

실행: 리포 루트에서
  python3 scripts/measurements/2026-08-29_kr_gap_trade_backtest.py
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

OFFSETS = harness.checkpoints(60, 950, 10)   # 90개 — 종가베팅 확장판과 동일 스펙
_half_idx = len(OFFSETS) // 2
RECENT_HALF = set(OFFSETS[:_half_idx])
EARLIER_HALF = set(OFFSETS[_half_idx:])
COST = 0.003                 # 왕복 수수료+슬리피지 0.3% — 종가베팅과 동일 가정
FETCH_DAYS = 1900            # ≈1274봉 — 종가베팅 확장판과 동일
MIN_BARS_AFTER_OFFSET = 260  # 52주 lookback(252) + 여유 8봉

GAP_BUCKETS = [
    ("갭다운(gap<0%)", lambda g: g < 0),
    ("0~+2%", lambda g: 0 <= g < 0.02),
    ("+2~5%", lambda g: 0.02 <= g < 0.05),
    ("+5%+", lambda g: g >= 0.05),
]


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
    """day-T(off) base 통과 종목 전부(대조군 포함) 평가 — combo(4조건)
    여부·T+1 갭·T+1 시가매수 당일수익률(gap-trade)·T종가매수 익일갭
    (종가베팅, 비교용)을 한 번에 계산."""
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
        if not base_ok:
            continue   # base 통과 종목만 수집(대조군도 base 안에서 뽑음 — 규칙6)
        combo_ok = candle_ok and volume_ok and position_ok

        open_t1 = float(future["Open"].iloc[0])
        high_t1 = float(future["High"].iloc[0])
        close_t1 = float(future["Close"].iloc[0])
        if open_t1 <= 0 or close_t1 <= 0:
            continue

        gap = open_t1 / close_t - 1.0                 # 종가베팅 수익률(비용 전) — T종가매수 기준
        intraday_ret = close_t1 / open_t1 - 1.0        # 갭매매 수익률(비용 전) — T+1시가매수 기준
        intraday_high_ret = high_t1 / open_t1 - 1.0     # 참고치(장중 고가, 타임스탬프 없음)

        out.append({
            "ticker": t, "off": off, "half": half, "combo_ok": combo_ok,
            "gap": gap, "intraday_ret": intraday_ret, "intraday_high_ret": intraday_high_ret,
        })
    return out


def stats(records, field="intraday_ret", cost=COST):
    n = len(records)
    if n == 0:
        return {"n": 0}
    vals = sorted(r[field] for r in records)
    mean_v = sum(vals) / n
    median_v = vals[n // 2] if n % 2 == 1 else (vals[n // 2 - 1] + vals[n // 2]) / 2
    win_rate = sum(1 for x in vals if x > 0) / n
    return {"n": n, "mean": mean_v, "median": median_v, "win_rate": win_rate, "net_mean": mean_v - cost}


def print_stats(label, records, field="intraday_ret"):
    s = stats(records, field)
    if s["n"] == 0:
        print(f"    {label}: n=0")
        return s
    print(f"    {label}: n={s['n']} 평균={s['mean']*100:+.2f}% 중앙값={s['median']*100:+.2f}% "
          f"승률={s['win_rate']*100:.1f}% 비용차감후={s['net_mean']*100:+.2f}%")
    return s


def mean_zscore(records_a, records_b, field="intraday_ret"):
    """연속형 % 수익률 2표본 평균차 z검정(표본분산) — jongga 백테스트
    스크립트와 동일 공식(harness.ev_gap_zscore는 이산 R분포 전용이라
    이 데이터엔 안 맞음, 재구현 아니라 데이터 타입에 맞는 통상 공식)."""
    va = [r[field] for r in records_a]
    vb = [r[field] for r in records_b]
    na, nb = len(va), len(vb)
    if na < 2 or nb < 2:
        return None, False
    ma_ = sum(va) / na
    mb_ = sum(vb) / nb
    var_a = sum((x - ma_) ** 2 for x in va) / (na - 1)
    var_b = sum((x - mb_) ** 2 for x in vb) / (nb - 1)
    se = (var_a / na + var_b / nb) ** 0.5
    if se == 0:
        return None, False
    z = (mb_ - ma_) / se
    return z, abs(z) >= 1.96


def bucket_of(gap):
    for label, pred in GAP_BUCKETS:
        if pred(gap):
            return label
    return None


if __name__ == "__main__":
    _t0 = time.time()
    data = fetch_kr_long_universe()

    print("\n" + "=" * 70)
    print(f"체크포인트별 base(거래대금상위100) 평가 + T+1 갭/당일수익률 수집 ({len(OFFSETS)}개 지점)")
    print("=" * 70)
    all_records = []
    for oi, off in enumerate(OFFSETS):
        rank = turnover_rank_at(data, off)
        recs = evaluate(data, off, rank)
        all_records.extend(recs)
        if (oi + 1) % 10 == 0 or oi == len(OFFSETS) - 1:
            n_combo = sum(1 for r in all_records if r["combo_ok"])
            print(f"[collect] offset {off} 완료 ({oi+1}/{len(OFFSETS)}) base누적={len(all_records)} "
                  f"combo누적={n_combo} elapsed={time.time()-_t0:.0f}s", flush=True)

    print(f"\n총 base 레코드 {len(all_records)}건 (combo 통과 {sum(1 for r in all_records if r['combo_ok'])}건)")

    combo_records = [r for r in all_records if r["combo_ok"]]
    for label, pred in GAP_BUCKETS:
        combo_records_b = [r for r in combo_records if pred(r["gap"])]
        for r in combo_records_b:
            r["bucket"] = label

    print("\n" + "=" * 70)
    print("【측정 1】 갭 구간별 — combo(4조건) 종목의 T+1 시가매수→종가매도 수익률")
    print("=" * 70)
    bucket_results = {}
    for label, pred in GAP_BUCKETS:
        recs = [r for r in combo_records if pred(r["gap"])]
        print(f"\n  -- {label} --")
        s = print_stats("combo(시가매수→종가매도)", recs)
        print_stats("  (참고, 고가기준)", recs, field="intraday_high_ret")
        bucket_results[label] = {"records": recs, "stats": s}

    print("\n" + "=" * 70)
    print("【측정 2】 시간 반분 재현 확인 (구간별)")
    print("=" * 70)
    for label, pred in GAP_BUCKETS:
        recs = bucket_results[label]["records"]
        earlier = [r for r in recs if r["half"] == "earlier"]
        recent = [r for r in recs if r["half"] == "recent"]
        print(f"\n  -- {label} --")
        s_e = print_stats("이전 절반", earlier)
        s_r = print_stats("최근 절반", recent)
        e_ok = s_e.get("n", 0) > 0 and s_e.get("net_mean", -1) >= 0.005
        r_ok = s_r.get("n", 0) > 0 and s_r.get("net_mean", -1) >= 0.005
        bucket_results[label]["half_ok"] = e_ok and r_ok
        print(f"    → 이전 {'재현' if e_ok else '미달'} / 최근 {'재현' if r_ok else '미달'}")

    print("\n" + "=" * 70)
    print("【측정 3】 대조군(base, 갭 구간 매칭) 대비 유의성")
    print("=" * 70)
    for label, pred in GAP_BUCKETS:
        base_bucket = [r for r in all_records if pred(r["gap"])]   # base 전체(combo 무관) 중 같은 갭 구간
        recs = bucket_results[label]["records"]
        z, sig = mean_zscore(base_bucket, recs)
        s_base = stats(base_bucket)
        s_combo = bucket_results[label]["stats"]
        print(f"\n  -- {label} --")
        print(f"    base(갭매칭 대조군): n={s_base.get('n',0)} 평균={ (s_base.get('mean',0) or 0)*100:+.2f}%")
        print(f"    combo: n={s_combo.get('n',0)} 평균={ (s_combo.get('mean',0) or 0)*100:+.2f}%")
        if z is not None:
            gap_vs_base = (s_combo.get("mean") or 0) - (s_base.get("mean") or 0)
            print(f"    격차(combo-base)={gap_vs_base*100:+.2f}%p z={z:.2f} {'유의' if sig else '유의하지 않음'}")
            bucket_results[label]["z_ok"] = sig and gap_vs_base > 0
        else:
            print("    표본 부족 — z검정 불가")
            bucket_results[label]["z_ok"] = False

    print("\n" + "=" * 70)
    print("【사전 등록 판정 — 갭 구간별】")
    print("=" * 70)
    any_adopted = False
    for label, pred in GAP_BUCKETS:
        r = bucket_results[label]
        s = r["stats"]
        cond_a = s.get("n", 0) > 0 and s.get("net_mean", -1) >= 0.005
        cond_b = r.get("half_ok", False)
        cond_c = r.get("z_ok", False)
        passed = cond_a and cond_b and cond_c
        any_adopted = any_adopted or passed
        print(f"  {label}: (a){'충족' if cond_a else '미달'} (b){'충족' if cond_b else '미달'} "
              f"(c){'충족' if cond_c else '미달'} ==> {'채택(갭 매매 유효 구간)' if passed else '미달'}")

    print("\n" + "=" * 70)
    print("【측정 4】 종가베팅(T종가매수) vs 갭매매(T+1시가매수) 비교 — 갭 구간별")
    print("=" * 70)
    print("  (같은 combo 레코드에서 두 수익률을 동시 계산 — gap 필드=종가베팅 수익률(비용 전))")
    for label, pred in GAP_BUCKETS:
        recs = bucket_results[label]["records"]
        s_jongga = stats(recs, field="gap")
        s_gaptrade = stats(recs, field="intraday_ret")
        print(f"\n  -- {label} (n={s_jongga.get('n',0)}) --")
        if s_jongga.get("n", 0) > 0:
            print(f"    종가베팅(T종가매수→T+1시가매도): 평균={s_jongga['mean']*100:+.2f}% "
                  f"비용차감후={s_jongga['net_mean']*100:+.2f}%")
            print(f"    갭매매(T+1시가매수→T+1종가매도): 평균={s_gaptrade['mean']*100:+.2f}% "
                  f"비용차감후={s_gaptrade['net_mean']*100:+.2f}%")
            better = "종가베팅" if s_jongga["net_mean"] > s_gaptrade["net_mean"] else "갭매매"
            print(f"    → 이 구간은 {better}가 더 나음 (격차 {abs(s_jongga['net_mean']-s_gaptrade['net_mean'])*100:.2f}%p)")

    print("\n" + "=" * 70)
    print(f"【최종 요약】 {'하나 이상의 갭 구간이 사전 기준을 충족 — 갭 매매 유효 구간 존재' if any_adopted else '전 구간 미달 — 갭업 추격은 일봉 수준에서 엣지 없음'}")
    print("=" * 70)

    print(f"\n[main] 전체 완료, elapsed={time.time()-_t0:.0f}s", flush=True)
