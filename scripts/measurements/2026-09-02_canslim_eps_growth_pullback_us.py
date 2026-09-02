"""
CAN SLIM의 C·A(펀더멘털 성장 지표)가 눌림목(pullback) 스캐너 히트의
EV를 개선하는가 — 축소판(사용자 지시, 2026-09-02).

【배경】
SEC EDGAR 통합 모듈(반나절~하루 작업) 착수 전에, "펀더멘털이 이 시스템
에서 작동하긴 하는가"부터 싸게 확인한다. yfinance만으로 룩어헤드 없이
계산 가능한 4개 지표만 측정:
  (a) 직전 분기 EPS YoY
  (c) 최근 3년 EPS CAGR(트레일링 4분기합 기준)
  (e) 성장 가속 — 직전 분기 YoY > 그 전 분기 YoY (오닐이 가장 강조한
      "earnings acceleration")
  (f) 첫 흑자 전환 — 직전 known 분기가 흑자, 그 전 known 분기가 <=0
(b) 매출 YoY, (d) ROE는 이번 대상 외(SEC EDGAR 필요 — 아래 "데이터
확보 가능성" 사전조사에서 확인: yfinance quarterly_income_stmt/
quarterly_balance_sheet는 "오늘 기준 최근 5~7분기" 스냅샷만 주고 과거
시점 재구성이 안 됨. 매출·자기자본 전용 깊은 발표일 데이터가 yfinance엔
없음).

【핵심 함정 — 룩어헤드 방지】
yfinance Ticker.get_earnings_dates(limit=100)는 (발표일, EPS 실제치)를
날짜와 함께 준다(Yahoo 상한 limit=100, 발표일 tz-aware datetime).
체크포인트 시점(signal_date)보다 "발표일 < signal_date"인 분기만
"그 시점에 이미 알려져 있던 값"으로 취급한다 — `_assert_no_lookahead()`
가 매 히트마다 이 불변식을 실제로 assert한다(사용자 지시: "명시적으로
확인하라"). yfinance의 quarterly_income_stmt/income_stmt(오늘 기준
최근 5~7분기 스냅샷만 주고 과거 시점 재구성 불가)는 이 측정에 전혀
쓰지 않는다 — earnings.py(기존 실시간 배지용 모듈)와 이 스크립트가
쓰는 데이터 소스가 다른 이유이기도 하다.

【대상 탭】
눌림목(pullback) — 2026-08-25 기관수급(I) 측정과 같은 탭(정밀 비교
가능하게) + Minervini/O'Neil 셋업이 이 탭에 가장 직접 대응. US만
(사용자 지시, KR은 DART 필요해 이번 대상 외).

【방법론 — README 규칙 준수】
- 규칙3: harness.py 공용 로직 재사용(fetch/RS/2R레이스/유동성필터).
- 규칙6: 대조군 불필요 — 히트 집합 '내부'를 지표로 나눠 비교(전부 같은
  유동성 컷을 통과한 히트에서 나옴, 2026-08-25 institutional_flow와
  동일 논리).
- 규칙7: 상위/하위 비교는 harness.ev_gap_zscore()로 유의성 재확인.
- 규칙8: 이번 측정은 US 단독이라 KR/US 분해 대상 아님(명시).
- 규칙9: checkpoints(60,950,10) = 90개(가용 최대 표본) 사용, "채택"
  판정은 이 표본으로만 내린다.
- 규칙10: fetch_universe_data(us_period="5y", validate_offsets=OFFSETS)
  로 fetch 깊이 부족을 실패로 강제.

【사전 등록 판정 기준】
EV 격차 +0.15R & z>=1.96 & 시기 반분(전반/후반 체크포인트) 둘 다 재현
→ 채택(카드 배지+필터 칩 검토 대상). 미달 → 기록하고 종결, SEC EDGAR
착수 안 함.

【판정불가 비율 안전장치(사용자 지시)】
지표별 판정불가(unknown) 비율을 개별로 계산·보고한다. (a)/(e)(둘 다
"핵심 질문"의 당사자, 5~6분기 깊이만 필요)가 30%를 넘으면 그 사실을
먼저 보고하고 EV 분석 없이 종료한다. (c)는 구조적으로 훨씬 깊은
이력(최소 16 known 분기)이 필요해 판정불가 비율이 따로, 더 높게
나올 수 있음 — (c) 단독으로 30%를 넘어도 (a)/(e)가 살아있으면 그
사실만 보고하고 (a)/(e)/(f) 분석은 계속한다(핵심 질문이 그 둘의
비교이므로).

실행: 리포 루트에서
`python3 scripts/measurements/2026-09-02_canslim_eps_growth_pullback_us.py`
1단계(US 가격 유니버스, 5년치) harness로 수분, 2단계(히트 유니크
티커별 get_earnings_dates) 유니크 티커 수 × 동시성10 비례.
"""
import sys
import os
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "measurements"))

import harness
from scanner import analyze, CONFIG

OFFSETS = harness.checkpoints(60, 950, 10)   # 규칙9: 90개, 채택 판정용 최대 표본
MIN_BARS_FLOOR = CONFIG["min_bars"]
MIN_N_FOR_JUDGMENT = 30
EV_GAP_THRESHOLD = 0.15   # R, 사전 등록
UNKNOWN_RATE_STOP = 0.30  # 사용자 지시: (a)/(e) 이 넘으면 보고 후 중단


# ══════════════════════════════════════════════════════════════════
# 1단계: 눌림목 US 히트 수집 (하네스 재사용)
# ══════════════════════════════════════════════════════════════════
def collect_pullback_hits(data):
    tickers = list(data.keys())   # data는 US만 fetch됨(markets=("us",))
    hits = []
    t0 = time.time()
    for oi, off in enumerate(OFFSETS):
        trunc_cache = {}
        for t in tickers:
            df = data[t]
            if len(df) - off < MIN_BARS_FLOOR:
                continue
            trunc_cache[t] = harness.truncate_at(df, off)
        # US는 벤치마크 차감 생략(harness 문서화된 검증된 가정) — b_kospi/
        # b_kosdaq에 0.0을 넣어도 US 랭킹엔 영향 없음(KR 티커가 없으므로).
        rs_ranks, rs_moms = harness.compute_rs_at_checkpoint(trunc_cache, 0.0, 0.0)

        for t, hist in trunc_cache.items():
            rr = rs_ranks.get(t)
            rm = rs_moms.get(t)
            try:
                hit = analyze(hist, rs_rank=rr, rs_mom=rm, cfg=CONFIG, is_kr=False)
            except Exception:
                continue
            if hit is None:
                continue
            if not harness.passes_liquidity_filter(hit, is_kr=False):
                continue
            hits.append({
                "ticker": t, "off": off,
                "signal_date": hist.index[-1].date(),
                "close": hit.get("close"), "stop": hit.get("stop"),
                "future": harness.future_after(data[t], off),
            })
        print(f"[PASS1] off={off} hits_so_far={len(hits)} elapsed={time.time()-t0:.0f}s "
              f"({oi+1}/{len(OFFSETS)})", flush=True)
    return hits


# ══════════════════════════════════════════════════════════════════
# 2단계: 히트 종목 EPS 발표이력 수집 (유니크 티커만)
# ══════════════════════════════════════════════════════════════════
# [사고 기록, 2026-09-02] 최초 실행(동시성10, 재시도 없음)에서 1410개 중
# 758개(54%)가 KeyError: ['Earnings Date']로 실패 — MSFT/JPM/JNJ/INTC 등
# 실적 데이터가 확실히 있는 대형주까지 실패해 "진짜 데이터 없음"이
# 아님을 바로 알 수 있었다. yfinance base.py 718행
# (`df.dropna(subset="Earnings Date")`)에서 발생하는 이 에러는 Yahoo가
# HTML 테이블 스크레이핑 요청을 과도한 동시 호출로 판단해 정상 페이지
# 대신 차단/왜곡된 응답을 준 것(레이트리밋) — 실제로 로그를 보면 처음
# 600개(동시성10, ~4.3req/s)까지는 전부 성공하다가 그 이후로 급격히
# 전부 실패로 전환됨, 시간에 따른 패턴이 레이트리밋과 일치. "판정불가"로
# 잘못 집계될 뻔한 사례라 캐시+재시도+동시성 완화로 재작업.
_EPS_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_eps_cache_2026-09-02")
os.makedirs(_EPS_CACHE_DIR, exist_ok=True)


def _eps_cache_path(ticker: str) -> str:
    safe = ticker.replace("/", "_").replace("\\", "_")
    return os.path.join(_EPS_CACHE_DIR, f"{safe}.json")


def fetch_eps_history(ticker: str, limit: int = 100, max_retries: int = 3):
    """(발표일:date, Reported EPS:float) 리스트, 발표일 오름차순.
    Reported EPS가 아직 없는 행(예정 실적)은 제외. 실패/데이터없음이면
    None — 조용히 빈 리스트로 위장하지 않는다(호출부가 unknown으로
    분리해서 집계할 수 있게). 성공만 디스크에 캐시(레이트리밋으로 인한
    실패는 캐시하지 않음 — 다음 실행에서 자동 재시도되게, 영구 실패로
    낙인찍지 않는다). 레이트리밋(위 사고기록)에 걸리면 지수백오프로
    재시도."""
    cache_path = _eps_cache_path(ticker)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            return [(date.fromisoformat(d), v) for d, v in cached]
        except (OSError, ValueError):
            pass   # 캐시 손상 — 아래에서 새로 받는다
    last_err = None
    for attempt in range(max_retries):
        try:
            import yfinance as yf
            tk = yf.Ticker(ticker)
            df = tk.get_earnings_dates(limit=limit)
            if df is None or df.empty:
                return None   # 진짜 데이터 없음(레이트리밋 아님, 재시도 무의미)
            df = df.dropna(subset=["Reported EPS"])
            if df.empty:
                return None
            df = df.sort_index()
            result = [(idx.date(), float(row["Reported EPS"])) for idx, row in df.iterrows()]
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump([[d.isoformat(), v] for d, v in result], f)
            return result
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                time.sleep(3 * (attempt + 1))   # 3s, 6s 백오프
                continue
    print(f"[EPS] {ticker} 조회 실패({max_retries}회 재시도 소진): {type(last_err).__name__}: {last_err}", flush=True)
    return None


def fetch_eps_for_hits(hits, max_workers=3):
    """max_workers=3(원래 10에서 완화) — 위 사고기록의 레이트리밋 재발 방지.
    캐시가 있으면 이 함수 재호출은 사실상 즉시 끝난다."""
    unique_tickers = sorted({h["ticker"] for h in hits})
    print(f"[PASS2] unique tickers to fetch EPS history: {len(unique_tickers)}", flush=True)
    eps_data = {}
    t0 = time.time()
    done = 0
    # 사고기록: 동시성만 낮추는 걸로는 부족할 수 있다(레이트리밋이 "동시
    # 개수"가 아니라 "짧은 시간창 내 총 요청수" 기준일 가능성) — 제출
    # 자체를 0.3초 간격으로 흩뿌려 전체 요청을 시간축에 펼친다.
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {}
        for t in unique_tickers:
            futs[ex.submit(fetch_eps_history, t)] = t
            time.sleep(0.3)
        for fut in as_completed(futs):
            t = futs[fut]
            eps_data[t] = fut.result()
            done += 1
            if done % 50 == 0:
                print(f"[PASS2] fetched {done}/{len(unique_tickers)} elapsed={time.time()-t0:.0f}s", flush=True)
    ok = sum(1 for v in eps_data.values() if v is not None)
    fail_rate = 1 - (ok / len(unique_tickers)) if unique_tickers else 0
    print(f"[PASS2] done: {ok}/{len(unique_tickers)} succeeded ({fail_rate:.0%} 실패), "
          f"elapsed={time.time()-t0:.0f}s", flush=True)
    if fail_rate > UNKNOWN_RATE_STOP:
        print(f"[경고] EPS fetch 실패율 {fail_rate:.0%} > {UNKNOWN_RATE_STOP:.0%} — "
              f"레이트리밋 재발 의심, 결과를 신뢰하지 말 것(호출부가 이후 판정불가 "
              f"안전장치에서 다시 걸러냄).", flush=True)
    return eps_data


# ══════════════════════════════════════════════════════════════════
# 3단계: 시점별(as-of-T) 재구성 — 룩어헤드 방지 핵심부
# ══════════════════════════════════════════════════════════════════
_lookahead_checks_done = 0


def _assert_no_lookahead(known: list, signal_date: date):
    """known의 모든 발표일이 signal_date보다 엄격히 이전인지 실제로
    assert(사용자 지시: "assert로 확인" — 슬라이싱 로직에 버그가 생기면
    여기서 즉시 터진다, 조용히 넘어가지 않는다)."""
    global _lookahead_checks_done
    for d, _ in known:
        assert d < signal_date, f"lookahead violation: announce={d} >= signal={signal_date}"
    _lookahead_checks_done += 1


def _pct(new, old):
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old) * 100


def compute_metrics_at(eps_hist: list, signal_date: date) -> dict:
    """eps_hist(오름차순 (발표일,EPS))에서 signal_date 시점에 실제로
    공개돼 있던 분기만 골라 (a)(c)(e)(f)를 계산. 부족하면 해당 항목만
    None(판정불가) — 나머지 계산 가능한 항목은 살린다."""
    known = [(d, e) for d, e in eps_hist if d < signal_date]
    _assert_no_lookahead(known, signal_date)
    vals = [e for _, e in known]
    out = {"n_known_quarters": len(vals)}

    # (a) 직전 분기 EPS YoY, (e)용 그 전 분기 YoY도 같이
    q_yoy = q_yoy_prev = None
    if len(vals) >= 5:
        q_yoy = _pct(vals[-1], vals[-5])
    if len(vals) >= 6:
        q_yoy_prev = _pct(vals[-2], vals[-6])
    out["eps_yoy_pct"] = q_yoy
    out["eps_yoy_prev_pct"] = q_yoy_prev
    out["accelerating"] = (q_yoy is not None and q_yoy_prev is not None and q_yoy > q_yoy_prev)
    if q_yoy is None or q_yoy_prev is None:
        out["accelerating"] = None   # 판정불가(False로 잘못 채우지 않음)

    # (f) 첫 흑자 전환 — 직전 known 분기가 <=0에서 >0으로
    if len(vals) >= 2:
        out["profit_turn"] = (vals[-2] <= 0 and vals[-1] > 0)
    else:
        out["profit_turn"] = None

    # (c) 3년 EPS CAGR — 트레일링4분기합(TTM) 두 시점(현재/3년전) 비교.
    # 3년 전 TTM = known[-16:-12]의 합(각 4분기 묶음), 최근 TTM = known[-4:]합.
    # len(vals)>=16 필요 — 이게 (c)의 판정불가율이 (a)/(e)/(f)보다 훨씬
    # 높게 나올 구조적 이유(신규상장·이력짧은 종목은 원천적으로 불가,
    # 데이터 결손이 아니라 "그 회사가 그만큼 안 됨"인 경우가 대부분).
    cagr = None
    if len(vals) >= 16:
        ttm_now = sum(vals[-4:])
        ttm_3y_ago = sum(vals[-16:-12])
        if ttm_3y_ago > 0 and ttm_now > 0:
            cagr = ((ttm_now / ttm_3y_ago) ** (1 / 3) - 1) * 100
        # ttm_3y_ago<=0(적자 기저)이면 CAGR 정의 자체가 무의미 — 판정불가로 둠
        # (음수/0에서 성장률 계산은 부호 왜곡, 추정으로 채우지 않음).
    out["eps_cagr_3y_pct"] = cagr
    return out


def attach_metrics_and_race(hits, eps_data):
    enriched = []
    n_no_eps = 0
    for h in hits:
        eps_hist = eps_data.get(h["ticker"])
        if eps_hist is None:
            n_no_eps += 1
            continue
        m = compute_metrics_at(eps_hist, h["signal_date"])
        outcome = harness.race(h["close"], h["stop"], h["future"])
        enriched.append({
            "ticker": h["ticker"], "off": h["off"], "outcome": outcome, **m,
        })
    print(f"[PASS3] enriched={len(enriched)} (EPS 발표이력 자체가 없어 제외={n_no_eps}), "
          f"lookahead assert 통과 {_lookahead_checks_done}건", flush=True)
    return enriched


# ══════════════════════════════════════════════════════════════════
# 4단계: 지표별 EV 분석
# ══════════════════════════════════════════════════════════════════
def tercile_split(enriched, field):
    valid = [h for h in enriched if h.get(field) is not None]
    if len(valid) < MIN_N_FOR_JUDGMENT * 3:
        return None, len(valid)
    valid.sort(key=lambda h: h[field])
    n = len(valid)
    edges = [0, n // 3, (2 * n) // 3, n]
    names = ["하위", "중위", "상위"]
    out = {}
    for i, name in enumerate(names):
        seg = valid[edges[i]:edges[i + 1]]
        out[name] = harness.ev_summary([h["outcome"] for h in seg])
    z, sig = harness.ev_gap_zscore(out["하위"], out["상위"])
    gap = (out["상위"]["ev_R"] - out["하위"]["ev_R"]) if (out["상위"]["ev_R"] is not None and out["하위"]["ev_R"] is not None) else None
    mid_between = None
    if all(out[k]["ev_R"] is not None for k in names):
        mid_between = out["하위"]["ev_R"] <= out["중위"]["ev_R"] <= out["상위"]["ev_R"] or \
                      out["하위"]["ev_R"] >= out["중위"]["ev_R"] >= out["상위"]["ev_R"]
    return {"n_valid": n, "terciles": out, "gap_상위_하위_R": gap, "z": z, "significant": sig,
            "monotonic_3group": mid_between}, n


def binary_split(enriched, pred):
    """pred(hit)->bool|None. True/False 두 그룹 EV+z."""
    valid = [h for h in enriched if pred(h) is not None]
    if len(valid) < MIN_N_FOR_JUDGMENT * 2:
        return None, len(valid)
    grp_true = [h for h in valid if pred(h) is True]
    grp_false = [h for h in valid if pred(h) is False]
    if len(grp_true) < MIN_N_FOR_JUDGMENT or len(grp_false) < MIN_N_FOR_JUDGMENT:
        return {"n_valid": len(valid), "n_true": len(grp_true), "n_false": len(grp_false),
                "note": f"한쪽 표본 부족(<{MIN_N_FOR_JUDGMENT}) — z검정 생략"}, len(valid)
    ev_true = harness.ev_summary([h["outcome"] for h in grp_true])
    ev_false = harness.ev_summary([h["outcome"] for h in grp_false])
    z, sig = harness.ev_gap_zscore(ev_false, ev_true)
    gap = (ev_true["ev_R"] - ev_false["ev_R"]) if (ev_true["ev_R"] is not None and ev_false["ev_R"] is not None) else None
    return {"n_valid": len(valid), "true": ev_true, "false": ev_false, "gap_R": gap, "z": z, "significant": sig}, len(valid)


def half_split_reproduction(enriched, pred_or_field, is_binary):
    """전반(off<=median offset)/후반 체크포인트로 나눠 같은 비교를 반복 —
    사전 등록 "시기 반분 재현" 요구."""
    mid_off = OFFSETS[len(OFFSETS) // 2]
    early = [h for h in enriched if h["off"] <= mid_off]
    late = [h for h in enriched if h["off"] > mid_off]
    results = {}
    for label, subset in (("초반(최근시점)", early), ("후반(과거시점)", late)):
        if is_binary:
            r, n = binary_split(subset, pred_or_field)
        else:
            r, n = tercile_split(subset, pred_or_field)
        results[label] = r
    return results


def unknown_rate(hits_or_enriched, field):
    n = len(hits_or_enriched)
    if n == 0:
        return None
    unk = sum(1 for h in hits_or_enriched if h.get(field) is None)
    return unk / n


def run(data, out_path=None):
    hits = collect_pullback_hits(data)
    daily_avg = round(len(hits) / len(OFFSETS), 1)
    print(f"[SUMMARY] 눌림목 US 히트 총 {len(hits)}건, 체크포인트당 평균 {daily_avg}건, "
          f"유니크 종목 {len({h['ticker'] for h in hits})}개", flush=True)

    eps_data = fetch_eps_for_hits(hits)
    unique_tickers = {h["ticker"] for h in hits}
    fetch_fail_rate = 1 - (sum(1 for t in unique_tickers if eps_data.get(t) is not None) / len(unique_tickers))
    print(f"[PASS2 요약] EPS fetch 자체 실패율(유니크 티커 기준): {fetch_fail_rate:.0%}", flush=True)
    if fetch_fail_rate > UNKNOWN_RATE_STOP:
        print(f"[중단] EPS fetch 실패율 {fetch_fail_rate:.0%} > {UNKNOWN_RATE_STOP:.0%} — "
              f"2026-09-02 사고기록과 같은 레이트리밋 재발 의심. EV 분석 없이 종료, "
              f"재실행 권장(캐시가 있어 재실행은 실패분만 재시도됨).", flush=True)
        report = {"n_hits_total": len(hits), "n_unique_tickers": len(unique_tickers),
                   "stopped_reason": "eps_fetch_fail_rate_exceeded", "fetch_fail_rate": fetch_fail_rate}
        if out_path:
            _save(report, out_path)
        return report

    enriched = attach_metrics_and_race(hits, eps_data)

    report = {
        "n_hits_total": len(hits), "n_unique_tickers": len({h["ticker"] for h in hits}),
        "n_enriched": len(enriched), "offsets": OFFSETS, "fetch_fail_rate": fetch_fail_rate,
    }

    # ── 판정불가 비율 보고 + 안전장치(사용자 지시) ──
    rates = {
        "eps_yoy_pct(a)": unknown_rate(enriched, "eps_yoy_pct"),
        "eps_cagr_3y_pct(c)": unknown_rate(enriched, "eps_cagr_3y_pct"),
        "accelerating(e)": unknown_rate(enriched, "accelerating"),
        "profit_turn(f)": unknown_rate(enriched, "profit_turn"),
    }
    report["unknown_rates"] = rates
    print(f"[판정불가 비율] {rates}", flush=True)

    core_blocked = (rates["eps_yoy_pct(a)"] or 1) > UNKNOWN_RATE_STOP or (rates["accelerating(e)"] or 1) > UNKNOWN_RATE_STOP
    if core_blocked:
        print(f"[중단] 핵심 지표(a)/(e) 판정불가 비율이 {UNKNOWN_RATE_STOP:.0%}를 넘음 — "
              f"EV 분석 없이 여기서 종료(사용자 지시).", flush=True)
        report["stopped_reason"] = "core_unknown_rate_exceeded"
        if out_path:
            _save(report, out_path)
        return report
    if (rates["eps_cagr_3y_pct(c)"] or 0) > UNKNOWN_RATE_STOP:
        print(f"[알림] (c) 3년 CAGR 판정불가 비율 {rates['eps_cagr_3y_pct(c)']:.0%} > "
              f"{UNKNOWN_RATE_STOP:.0%} — 구조적(이력 짧은 종목 다수)으로 예상된 결과, "
              f"(c) 표본만 줄어든 채로 계속 진행하고 (a)/(e)/(f)는 정상 진행.", flush=True)

    # ── 측정1: 지표별 3분위(연속값 a, c) / 이분(불리언 e, f) ──
    report["tercile_a_eps_yoy"], n_a = tercile_split(enriched, "eps_yoy_pct")
    report["tercile_c_eps_cagr3y"], n_c = tercile_split(enriched, "eps_cagr_3y_pct")
    report["binary_e_accelerating"], n_e = binary_split(enriched, lambda h: h.get("accelerating"))
    report["binary_f_profit_turn"], n_f = binary_split(enriched, lambda h: h.get("profit_turn"))
    print(f"[측정1] (a)n={n_a} (c)n={n_c} (e)n={n_e} (f)n={n_f}", flush=True)

    # ── 측정2: 오닐 기준선(EPS YoY>=25%) 통과 여부 이분 ──
    report["binary_oneil_c_25pct"], n_oneil = binary_split(
        enriched, lambda h: (h["eps_yoy_pct"] >= 25.0) if h.get("eps_yoy_pct") is not None else None)
    print(f"[측정2] 오닐C(EPS+25%) 이분 n={n_oneil}", flush=True)

    # ── 측정3: 개별 대비 조합(a+e+f 통과 개수)이 나은지 ──
    def combo_score(h):
        parts = []
        if h.get("eps_yoy_pct") is not None:
            parts.append(h["eps_yoy_pct"] >= 25.0)
        if h.get("accelerating") is not None:
            parts.append(h["accelerating"])
        if h.get("profit_turn") is not None:
            parts.append(h["profit_turn"])
        return parts
    combo_valid = [h for h in enriched if combo_score(h)]
    for h in combo_valid:
        h["_combo_n_pass"] = sum(combo_score(h))
        h["_combo_n_checked"] = len(combo_score(h))
    high_combo = [h for h in combo_valid if h["_combo_n_pass"] >= 2 and h["_combo_n_checked"] >= 2]
    low_combo = [h for h in combo_valid if h["_combo_n_pass"] == 0 and h["_combo_n_checked"] >= 2]
    if len(high_combo) >= MIN_N_FOR_JUDGMENT and len(low_combo) >= MIN_N_FOR_JUDGMENT:
        ev_high = harness.ev_summary([h["outcome"] for h in high_combo])
        ev_low = harness.ev_summary([h["outcome"] for h in low_combo])
        z, sig = harness.ev_gap_zscore(ev_low, ev_high)
        gap = (ev_high["ev_R"] - ev_low["ev_R"]) if (ev_high["ev_R"] is not None and ev_low["ev_R"] is not None) else None
        report["combo_2plus_vs_0"] = {"n_high": len(high_combo), "n_low": len(low_combo),
                                        "high": ev_high, "low": ev_low, "gap_R": gap, "z": z, "significant": sig}
    else:
        report["combo_2plus_vs_0"] = {"note": f"표본 부족(high={len(high_combo)}, low={len(low_combo)})"}
    print(f"[측정3] 조합 스코어 비교: {report['combo_2plus_vs_0']}", flush=True)

    # ── 핵심 질문: (e)가 (a) 단독보다 나은지 — z, gap 직접 비교 ──
    a_result = report["tercile_a_eps_yoy"]
    e_result = report["binary_e_accelerating"]
    a_gap = a_result["gap_상위_하위_R"] if a_result else None
    a_z = a_result["z"] if a_result else None
    e_gap = e_result.get("gap_R") if e_result else None
    e_z = e_result.get("z") if e_result else None
    report["core_question_e_vs_a"] = {
        "a_eps_yoy_gap_R": a_gap, "a_z": a_z,
        "e_accelerating_gap_R": e_gap, "e_z": e_z,
        "e_stronger": (e_gap is not None and a_gap is not None and abs(e_gap) > abs(a_gap)),
    }
    print(f"[핵심질문] {report['core_question_e_vs_a']}", flush=True)

    # ── 시기 반분 재현 — best 지표(z 최대, |z|>=1.96인 것 중)만 ──
    candidates = []
    if a_result and a_result.get("significant"):
        candidates.append(("a_eps_yoy", "eps_yoy_pct", False, a_result["z"]))
    if e_result and e_result.get("significant"):
        candidates.append(("e_accelerating", lambda h: h.get("accelerating"), True, e_result["z"]))
    if report.get("binary_f_profit_turn") and report["binary_f_profit_turn"].get("significant"):
        candidates.append(("f_profit_turn", lambda h: h.get("profit_turn"), True, report["binary_f_profit_turn"]["z"]))
    if candidates:
        best_name, best_pred, is_bin, best_z = max(candidates, key=lambda x: abs(x[3]))
        half = half_split_reproduction(enriched, best_pred, is_bin)
        report["half_split_best"] = {"field": best_name, "result": half}
        print(f"[시기반분] 최유력 지표={best_name}(z={best_z:.2f}): {half}", flush=True)
    else:
        report["half_split_best"] = None
        print("[시기반분] z>=1.96 통과 지표 없음 — 반분 재현 생략", flush=True)

    # ── 최종 판정(사전 등록 기준) ──
    def judge(name, result, is_binary):
        if result is None:
            return "표본부족"
        gap = result.get("gap_R") if is_binary else result.get("gap_상위_하위_R")
        z = result.get("z")
        sig = result.get("significant")
        if gap is None or z is None:
            return "표본부족"
        if abs(gap) < EV_GAP_THRESHOLD or not sig:
            return f"기각(gap={gap:+.3f}R, z={z:.2f})"
        return f"채택후보(gap={gap:+.3f}R, z={z:.2f}) — 시기반분 재현 확인 필요"
    report["verdicts"] = {
        "a_eps_yoy": judge("a", a_result, False),
        "e_accelerating": judge("e", e_result, True),
        "f_profit_turn": judge("f", report.get("binary_f_profit_turn"), True),
        "oneil_c_25pct": judge("oneil", report.get("binary_oneil_c_25pct"), True),
    }
    print(f"[최종판정(반분 재현 확인 전)] {report['verdicts']}", flush=True)

    if out_path:
        _save(report, out_path)
    return report


def _save(report, out_path):
    def _default(o):
        return str(o)
    with open(out_path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=_default)
    print(f"SAVED report to {out_path}", flush=True)


if __name__ == "__main__":
    data, kr_u, us_u = harness.fetch_universe_data(markets=("us",), us_period="5y", validate_offsets=OFFSETS)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "2026-09-02_canslim_eps_growth_pullback_us.results.json")
    run(data, out_path=out)
