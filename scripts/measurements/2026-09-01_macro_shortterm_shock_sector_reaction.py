"""
매크로 단기(주간) 반응 — 지표 급변 후 섹터 반응성 측정 (2026-09-01, 사전 등록)

배경: `docs/macro_regime_sector.md`(월간 레짐→섹터, 2026-09-01)가 기각됨
(시기 반분 겹침률 KR 6.7%/US 20.8%, 채택 기준 50%+ 미달). "레짐(추세)"
단위가 너무 느려서 신호가 안 잡히는 것뿐이고, 더 짧은 창(급변 이벤트)
에는 반응성이 있을 수 있다는 후속 가설을 검증한다.

【가설】 달러·금리·유가·금의 주간 급변(|주간변화| > 1.5σ, σ=해당 지표
가용 전체 기간 주간변화의 표준편차)이 있었던 다음 주에, 특정 KR/US
섹터가 시장 대비 초과수익을 낸다.

【이전 스크립트와 공유/차이 — README 규칙3】
- sectors.py 유니버스, 유동성 필터(harness.KR/US_LIQUIDITY_FLOOR), 섹터당
  최소 5종목, 시장벤치마크(유니버스 횡단면 중앙값), welch_zscore/
  hypergeom_overlap_pvalue(harness.py로 승격됨) — 전부 동일 재사용.
- **다른 점 1**: 레짐(3축 8셀, 상호배타적 파티션)과 달리 이벤트 유형은
  상호배타적이지 않다(같은 주에 달러급등+유가급락이 동시에 일어날 수
  있음) — 유형별로 독립적으로 "그 유형이 발생한 주" 집합을 만들어
  따로 분석한다.
- **다른 점 2**: KR 종목 fetch 기간을 이전 스크립트(days=3650, ~10년)
  보다 늘려 `days=10000`(~27년)으로 — naver_kr은 요청 기간에 비례해서만
  더 걸리지 않고(왕복 지연이 지배적, naver_kr.py fetch_history 문서화된
  실측) 상한도 없어서, "가용 최대 기간"(규칙9)을 더 충실히 만족시키려면
  굳이 10년으로 제한할 이유가 없었다. 이전 스크립트는 이미 실행·기록
  완료된 상태라 소급 변경하지 않음(그 결과는 그 자체로 최종).
- **다른 점 3**: 이번 가설엔 "측정3(현재판정)"/"측정4(실용비교)"가
  사용자 스펙에 없음 — 요청된 것만(이벤트정의→익주수익률→상하위+일관성,
  시기반분) 구현한다. 사전 등록 범위를 벗어난 측정을 추가하지 않는다.

【규칙6~9 준수】 이전 스크립트와 동일 원칙 — 유동성 매칭(현재시점 1회성,
단순화 명시), 유의성(welch z + 초과문서기하 p값), KR/US 완전분리,
최대가용표본(주간, 지표별 가용 전체기간) + 시기 반분 재현 필수(사전
등록 채택기준 50%+, 이번 요청에서 필수로 명시됨).

근거 문서: docs/macro_shortterm_shock_reaction.md
"""
import sys
import time
from collections import Counter

sys.path.insert(0, "/Users/seulkicho/pullback")
sys.path.insert(0, "/Users/seulkicho/pullback/scripts/measurements")

import pandas as pd
import yfinance as yf

import naver_kr
import sectors
import harness  # KR/US_LIQUIDITY_FLOOR, welch_zscore, hypergeom_overlap_pvalue

# ── 사전 고정 상수 ──────────────────────────────────────────────────
MACRO_TICKERS = {
    "달러": "DX-Y.NYB", "금리": "^TNX", "유가": "CL=F", "금": "GC=F",
}
SHOCK_SIGMA = 1.5           # 급변 임계값(표준편차 배수, 가용 전체기간 기준)
MIN_TICKERS_PER_SECTOR = 5
MIN_WEEKS_PER_HALF = 10     # 분할재현 비교 최소 표본 — 월간판(4)보다 절대
                            # 표본요구치를 높임(주간 이벤트는 발생빈도 자체가
                            # 낮아 4주로는 너무 헐거움 — README 규칙9 정신)
TOPN = 3
ADOPT_OVERLAP_THRESHOLD = 0.5
KR_FETCH_DAYS = 10000       # ~27년(위 docstring "다른 점 2" 참고)


def log(msg):
    print(msg, flush=True)


# ── 매크로 데이터 + 이벤트 정의 ──────────────────────────────────────
def fetch_macro_weekly() -> dict[str, pd.Series]:
    out = {}
    for key, tk in MACRO_TICKERS.items():
        try:
            df = yf.Ticker(tk).history(period="max", interval="1wk", auto_adjust=True)
            if df is None or df.empty or "Close" not in df.columns:
                log(f"[macro] {key}({tk}) 데이터 없음")
                continue
            s = df["Close"].dropna()
            s.index = pd.DatetimeIndex(s.index).tz_localize(None)
            s.index = s.index.to_period("W-FRI").to_timestamp("W-FRI")
            s = s[~s.index.duplicated(keep="last")].sort_index()
            out[key] = s
        except Exception as e:
            log(f"[macro] {key}({tk}) fetch 실패: {e}")
    return out


def build_shock_events(macro: dict) -> tuple[pd.DataFrame, dict]:
    """지표별 주간 %변화의 가용 전체기간 표준편차 기준 ±1.5σ 초과 주를
    이벤트로 표시. 반환: (주×이벤트유형 bool DataFrame, 지표별 sigma 딕셔너리)."""
    event_cols = {}
    sigmas = {}
    for key, s in macro.items():
        chg = s.pct_change().dropna()
        sigma = float(chg.std())
        sigmas[key] = sigma
        event_cols[f"{key}급등"] = chg > SHOCK_SIGMA * sigma
        event_cols[f"{key}급락"] = chg < -SHOCK_SIGMA * sigma
    events = pd.DataFrame(event_cols).fillna(False).astype(bool)
    return events, sigmas


# ── 섹터 유니버스 가격 데이터(주간) ──────────────────────────────────
def resolve_sector_tickers() -> tuple[list, list]:
    kr = sorted({t for t in sectors.SECTOR_MAP if naver_kr.is_kr(t)})
    us = sorted({t for t in sectors.SECTOR_MAP if not naver_kr.is_kr(t)})
    return kr, us


def fetch_kr_weekly(tickers: list, days: int = KR_FETCH_DAYS, concurrency: int = 12) -> dict:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _one(t):
        df = naver_kr.fetch_history(t, days=days)
        if df is None or df.empty or len(df) < 60:
            return t, None
        close_w = df["Close"].resample("W-FRI").last().dropna()
        if len(close_w) < 52:   # 최소 1년치 없으면 랭킹 대상에서 제외
            return t, None
        recent_turnover = float((df["Close"] * df["Volume"]).tail(60).mean())
        return t, {"close_w": close_w, "turnover": recent_turnover}

    out = {}
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = {ex.submit(_one, t): t for t in tickers}
        for fut in as_completed(futs):
            t, res = fut.result()
            if res is not None:
                out[t] = res
            done += 1
            if done % 50 == 0:
                log(f"[kr] {done}/{len(tickers)} elapsed={time.time()-t0:.0f}s")
    log(f"[kr] 완료 {len(out)}/{len(tickers)} elapsed={time.time()-t0:.0f}s")
    return out


def fetch_us_weekly(tickers: list, batch: int = 80) -> dict:
    out = {}
    t0 = time.time()
    for i in range(0, len(tickers), batch):
        chunk = tickers[i:i + batch]
        try:
            raw = yf.download(chunk, period="max", interval="1wk",
                               auto_adjust=True, group_by="ticker",
                               threads=True, progress=False)
        except Exception as e:
            log(f"[us] batch {i//batch+1} 실패: {e}")
            continue
        single = len(chunk) == 1
        for t in chunk:
            try:
                df = raw.copy() if single else raw[t].copy()
                df = df.dropna(how="all")
                if df is None or df.empty or "Close" not in df.columns:
                    continue
                close_w = df["Close"].dropna()
                if len(close_w) < 52:
                    continue
                close_w.index = pd.DatetimeIndex(close_w.index).tz_localize(None)
                close_w.index = close_w.index.to_period("W-FRI").to_timestamp("W-FRI")
                vol_w = df["Volume"].dropna().tail(4)
                px_w = df["Close"].dropna().tail(4)
                recent_turnover = float((vol_w * px_w).mean() / 5) if len(vol_w) and len(px_w) else 0.0
                out[t] = {"close_w": close_w, "turnover": recent_turnover}
            except Exception:
                continue
        log(f"[us] batch {i//batch+1}/{(len(tickers)-1)//batch+1} elapsed={time.time()-t0:.0f}s")
    log(f"[us] 완료 {len(out)}/{len(tickers)} elapsed={time.time()-t0:.0f}s")
    return out


def build_sector_return_panel(price_data: dict, is_kr: bool):
    floor_ = harness.KR_LIQUIDITY_FLOOR if is_kr else harness.US_LIQUIDITY_FLOOR
    kept = {t: d for t, d in price_data.items() if d["turnover"] >= floor_}
    log(f"[{'kr' if is_kr else 'us'}] 유동성 컷 통과 {len(kept)}/{len(price_data)} (기준 {floor_:,.0f})")

    ret_df = pd.DataFrame({t: d["close_w"].pct_change() for t, d in kept.items()})
    sector_of = {t: sectors.get_sector(t) for t in kept}
    sector_counts = Counter(sector_of.values())
    valid_sectors = {s for s, n in sector_counts.items() if n >= MIN_TICKERS_PER_SECTOR and s != "기타"}
    log(f"[{'kr' if is_kr else 'us'}] 유효 섹터(>={MIN_TICKERS_PER_SECTOR}종목) {len(valid_sectors)}개: {sorted(valid_sectors)}")

    market_ret = ret_df.median(axis=1, skipna=True)
    sector_ret = pd.DataFrame(index=ret_df.index)
    for s in valid_sectors:
        members = [t for t, sec in sector_of.items() if sec == s]
        sector_ret[s] = ret_df[members].median(axis=1, skipna=True)
    rel_ret = sector_ret.sub(market_ret, axis=0)
    return rel_ret


# ── 측정1+2: 이벤트유형별 상/하위 섹터 + 일관성 + 시기반분 재현 ──────────
def analyze_event_type(event_type: str, event_weeks: pd.Index, rel_ret_next: pd.DataFrame,
                        market_label: str) -> dict | None:
    weeks = rel_ret_next.index.intersection(event_weeks)
    sub = rel_ret_next.loc[weeks].dropna(how="all")
    n = len(sub)
    if n < MIN_TICKERS_PER_SECTOR:   # 최소한의 표본조차 없으면 건너뜀
        log(f"  [{event_type}] n={n} — 표본부족, 건너뜀")
        return None
    avg = sub.mean(axis=0).dropna().sort_values(ascending=False)
    if len(avg) < TOPN * 2:
        log(f"  [{event_type}] 유효 섹터 수 부족({len(avg)}) — 건너뜀")
        return None
    top, bottom = avg.head(TOPN), avg.tail(TOPN)
    monthly_rank = sub.rank(axis=1, ascending=False)
    consistency = {}
    for sname in top.index:
        if sname not in monthly_rank.columns:
            continue
        in_top = (monthly_rank[sname] <= TOPN).sum()
        valid = monthly_rank[sname].notna().sum()
        consistency[sname] = round(in_top / valid, 2) if valid else None
    top_pool = pd.concat([sub[s] for s in top.index if s in sub.columns])
    bot_pool = pd.concat([sub[s] for s in bottom.index if s in sub.columns])
    z, sig = harness.welch_zscore(bot_pool, top_pool)
    log(f"  [{event_type}] n={n}주")
    log(f"    상위{TOPN}: " + ", ".join(f"{s}({v:+.2%}, 일관성{consistency.get(s):.0%})" for s, v in top.items()))
    log(f"    하위{TOPN}: " + ", ".join(f"{s}({v:+.2%})" for s, v in bottom.items()))
    log(f"    상/하위 격차 z={z:.2f}({'유의' if sig else '비유의'})" if z is not None else "    격차: 계산 불가")

    return {"n": n, "top": top, "bottom": bottom, "consistency": consistency}


def split_half_for_event(event_type: str, event_weeks: pd.Index, rel_ret_next: pd.DataFrame,
                          s_total: int):
    """이 이벤트유형이 실제로 걸리는 주 중, 종목수익률 데이터가 있는 주만
    추려 반으로 쪼갠다(월간판에서 발견된 버그의 재발 방지 — 처음부터
    "데이터 있는 주"만 기준으로 분할)."""
    weeks = rel_ret_next.index.intersection(event_weeks)
    covered = rel_ret_next.loc[weeks].dropna(how="all").index.sort_values()
    mid = len(covered) // 2
    first_half, second_half = covered[:mid], covered[mid:]

    def _top(idx):
        if len(idx) < MIN_WEEKS_PER_HALF:
            return None
        sub = rel_ret_next.loc[idx].dropna(how="all")
        if len(sub) < MIN_WEEKS_PER_HALF:
            return None
        avg = sub.mean(axis=0).dropna().sort_values(ascending=False)
        if len(avg) < TOPN:
            return None
        return set(avg.head(TOPN).index), len(sub)

    a, b = _top(first_half), _top(second_half)
    if a is None or b is None:
        log(f"  [{event_type}] 표본부족(전반={len(first_half)}주/후반={len(second_half)}주, "
            f"최소{MIN_WEEKS_PER_HALF}) — 비교 제외")
        return None
    set_a, n_a = a
    set_b, n_b = b
    overlap_n = len(set_a & set_b)
    overlap_rate = overlap_n / TOPN
    pval = harness.hypergeom_overlap_pvalue(overlap_n, s_total, TOPN)
    chance_rate = TOPN / s_total
    sig = pval is not None and pval < 0.05
    p_str = f"p={pval:.3f}({'유의' if sig else '비유의'})" if pval is not None else "p값 계산불가"
    log(f"  [{event_type}] 전반(n={n_a}) top{TOPN}={sorted(set_a)} / 후반(n={n_b}) top{TOPN}={sorted(set_b)} "
        f"→ 겹침 {overlap_n}/{TOPN}({overlap_rate:.0%}) · 우연기준선 {chance_rate:.0%} · {p_str}")
    return overlap_rate, sig


def run_market(market_label: str, is_kr: bool, price_data: dict, events: pd.DataFrame, sigmas: dict):
    rel_ret = build_sector_return_panel(price_data, is_kr)
    if rel_ret.empty:
        log(f"[{market_label}] 섹터 수익률 패널이 비었음 — 측정 중단")
        return
    rel_ret_next = rel_ret.shift(-1)   # 익주 정렬
    s_total = rel_ret_next.shape[1]

    log(f"\n{'='*70}\n측정1 — {market_label} 이벤트유형별 상/하위{TOPN} 섹터 (n=섹터 {s_total}개)\n{'='*70}")
    for etype in events.columns:
        indicator = etype[:-2]  # "급등"/"급락" 제거
        log(f"\n[{etype}] (σ={sigmas.get(indicator, float('nan')):.4f}, "
            f"전체 이벤트 주수={int(events[etype].sum())})")
        event_weeks = events.index[events[etype]]
        analyze_event_type(etype, event_weeks, rel_ret_next, market_label)

    log(f"\n{'='*70}\n측정2 — {market_label} 시기 반분 재현(사전 등록 채택 기준: 겹침률 {ADOPT_OVERLAP_THRESHOLD:.0%}+)\n{'='*70}")
    overlaps, sig_count, tested = [], 0, 0
    for etype in events.columns:
        event_weeks = events.index[events[etype]]
        result = split_half_for_event(etype, event_weeks, rel_ret_next, s_total)
        if result is None:
            continue
        overlap_rate, sig = result
        overlaps.append(overlap_rate)
        tested += 1
        if sig:
            sig_count += 1

    avg_overlap = sum(overlaps) / len(overlaps) if overlaps else None
    log(f"\n  평균 겹침률: {avg_overlap:.1%}" if avg_overlap is not None else "\n  평균 겹침률: 계산 불가(비교 가능 이벤트유형 0개)")
    log(f"  통계적으로 유의(p<0.05)한 이벤트유형: {sig_count}/{tested}")

    log(f"\n{'='*70}\n{market_label} 판정\n{'='*70}")
    if avg_overlap is None or tested == 0:
        log("  판정 불가 — 비교 가능한 이벤트유형이 없음(표본부족)")
    elif avg_overlap >= ADOPT_OVERLAP_THRESHOLD and sig_count >= max(1, tested // 2):
        log(f"  채택 — 평균 겹침률 {avg_overlap:.0%} >= {ADOPT_OVERLAP_THRESHOLD:.0%} "
            f"AND 유의 유형 {sig_count}/{tested}(과반)")
    else:
        log(f"  기각 — 평균 겹침률 {avg_overlap:.0%}, 유의 유형 {sig_count}/{tested} "
            "→ '매크로 단기 반응도 예측력 없음'으로 기록")


def main():
    t0 = time.time()
    log("="*70)
    log("매크로(달러/금리/유가/금) 주간 데이터 + 급변 이벤트 정의")
    log("="*70)
    macro = fetch_macro_weekly()
    for k, s in macro.items():
        log(f"  {k}: {len(s)}주 ({s.index.min().date()} ~ {s.index.max().date()})")
    events, sigmas = build_shock_events(macro)
    log(f"이벤트유형별 발생 주수: {Counter({c: int(events[c].sum()) for c in events.columns})}")
    log(f"sigma(가용 전체기간 주간변화 표준편차): { {k: round(v,4) for k,v in sigmas.items()} }")

    kr_tickers, us_tickers = resolve_sector_tickers()
    log(f"\n섹터 매핑 종목: KR {len(kr_tickers)}개, US {len(us_tickers)}개")

    log("\n" + "="*70)
    log(f"KR 섹터 종목 주봉 수집 (fetch days={KR_FETCH_DAYS})")
    log("="*70)
    kr_price = fetch_kr_weekly(kr_tickers)

    log("\n" + "="*70)
    log("US 섹터 종목 주봉 수집")
    log("="*70)
    us_price = fetch_us_weekly(us_tickers)

    run_market("KR", True, kr_price, events, sigmas)
    run_market("US", False, us_price, events, sigmas)

    log(f"\n[main] 총 소요시간 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
