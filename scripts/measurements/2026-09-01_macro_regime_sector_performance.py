"""
매크로 레짐 조건부 섹터 성과 백테스트 (2026-09-01, 사전 등록)

배경: "전반적인 경제 상황을 토대로 유리한 섹터·종목을 도출할 수 있어야
한다"(사용자 요청) — 현재 시스템은 bottom-up(돈의흐름·테마)만 있고
top-down(매크로→섹터) 레이어가 없다. 이 스크립트는 그 레이어가 실제로
예측력이 있는지 사전 등록 기준으로 검증한다.

【규칙 6(유동성매칭)】 sectors.py에 매핑된 종목만 대상(전체 유니버스가
아니라 이미 "섹터 분류가 의미 있는" 종목군으로 모집단을 좁힘 — 신호-EV
레이스가 아니라 횡단면 섹터 랭킹 비교라 harness.checkpoints/race와는
다른 종류의 측정이라 harness.py를 그대로 재사용하지 않음, README 규칙3
예외). 그 안에서도 harness.KR_LIQUIDITY_FLOOR(3억원)/US_LIQUIDITY_FLOOR
($2M) 재사용 — 단, 월별 시점매칭 유동성이 아니라 **현재 시점 유동성으로
종목을 한 번만 필터링**(10년×수백종목의 월별 롤링 유동성 계산은 이번
스크립트 범위 밖 — 단순화 지점으로 명시). 섹터당 최소 종목수(5개) 필터도
추가 — 1~2종목짜리 "섹터"는 사실상 개별종목 잡음이라 랭킹에 넣지 않음.

【규칙 7(유의성)】 두 곳에 적용: ① 분할재현 겹침률 — 우연(무작위 3개
겹침)의 기댓값을 초과문서기하분포로 계산해 p값 병기(같은 섹터수라도
섹터 개수 S가 작으면 우연히도 겹침률이 높게 나올 수 있어 raw 50%만으론
불충분). ② 셀별 상/하위 섹터 상대수익률 격차 — 연속값 두 표본 z검정
(harness.ev_gap_zscore와 같은 목적, 이산 R 분포 전용이라 그대로 못 써
새로 구현, welch_zscore()).

【규칙 8(KR/US 분리)】 전 측정 KR/US 완전 분리 — 섹터 분류·시장벤치마크·
결과 리포트 전부 시장별 독립.

【규칙 9(최대표본)】 yfinance 가용 최대 기간(보통 10년+, 티커별로 다름)
월간 리샘플 전부 사용 — 짧은 창으로 자르지 않음. 단, 매크로 레짐 8셀은
월 단위라 실질 표본이 원래 작다(10년≈120개월, 8셀 균등분배면 셀당 15개월)
— 셀별 실제 n을 항상 병기하고, n<MIN_MONTHS_PER_HALF(4)인 셀은 분할재현
비교에서 "표본부족" 처리한다(README 규칙9 정신 — 작은 표본을 채택 근거로
안 씀).

【데이터 기간 비대칭 — 알려진 한계】 측정4의 "돈의흐름 강세테마 추종" 비교
항목은 이 실행 시점에 theme_map.json 역사가 며칠~2주 수준(2026-08-29
도입)이라 10년 매크로 백테스트와 비교할 수 있는 기간이 전혀 없다 —
이 항목은 실행 시 자동으로 건너뛰고 사유를 출력한다(가짜 비교를 만들지
않음). 다른 두 항목(레짐전략/시장전체)은 정상 비교.

근거 문서: docs/macro_regime_sector.md
"""
import sys
import time
from collections import Counter

sys.path.insert(0, "/Users/seulkicho/pullback")
sys.path.insert(0, "/Users/seulkicho/pullback/scripts/measurements")

import numpy as np
import pandas as pd
import yfinance as yf

import naver_kr
import sectors
import harness  # KR_LIQUIDITY_FLOOR / US_LIQUIDITY_FLOOR 재사용(규칙3)

# ── 사전 고정 상수 ──────────────────────────────────────────────────
MACRO_TICKERS = {
    "dxy": "DX-Y.NYB", "tnx": "^TNX", "irx": "^IRX", "krwusd": "KRW=X",
    "gold": "GC=F", "silver": "SI=F", "copper": "HG=F", "oil": "CL=F",
    "vix": "^VIX", "hyg": "HYG", "lqd": "LQD",
}
REGIME_TREND_MONTHS = 3     # 달러/금리 "3개월 추세" 정의
VIX_THRESHOLD = 20.0        # 위험선호 경계
MIN_TICKERS_PER_SECTOR = 5  # 이 미만이면 "섹터"로 안 침(개별종목 잡음 방지)
MIN_MONTHS_PER_HALF = 4     # 분할재현 비교 최소 표본(README 규칙9 정신)
TOPN = 3                    # 상/하위 N섹터
ADOPT_OVERLAP_THRESHOLD = 0.5   # 사전 등록: 상위3 겹침률 50%+
WALKFORWARD_WARMUP_MONTHS = 36  # 측정4 워크포워드 워밍업(3년 누적 후 시작)


def log(msg):
    print(msg, flush=True)


# ── 매크로 데이터 ────────────────────────────────────────────────────
def fetch_macro_monthly() -> dict[str, pd.Series]:
    """yfinance 가용 최대 기간, 월봉 직접 요청(일봉 10년+ 받아 리샘플보다
    가벼움 — 매크로 지수는 분봉 정밀도가 필요 없음)."""
    out = {}
    for key, tk in MACRO_TICKERS.items():
        try:
            df = yf.Ticker(tk).history(period="max", interval="1mo", auto_adjust=True)
            if df is None or df.empty or "Close" not in df.columns:
                log(f"[macro] {key}({tk}) 데이터 없음")
                continue
            s = df["Close"].dropna()
            s.index = pd.DatetimeIndex(s.index).tz_localize(None)
            s.index = s.index.to_period("M").to_timestamp("M")
            s = s[~s.index.duplicated(keep="last")].sort_index()
            out[key] = s
        except Exception as e:
            log(f"[macro] {key}({tk}) fetch 실패: {e}")
    return out


def build_regime_series(macro: dict) -> pd.DataFrame:
    """월별 레짐 라벨(8셀) + 축별 판정값. 반환 인덱스=월말 타임스탬프."""
    dxy, tnx, vix = macro.get("dxy"), macro.get("tnx"), macro.get("vix")
    if dxy is None or tnx is None or vix is None:
        raise RuntimeError("레짐 계산에 필요한 핵심 매크로 시리즈(DXY/TNX/VIX) 중 하나가 없음")
    idx = dxy.index.intersection(tnx.index).intersection(vix.index)
    idx = idx.sort_values()
    dxy_trend = dxy.pct_change(REGIME_TREND_MONTHS)
    tnx_trend = tnx.pct_change(REGIME_TREND_MONTHS)
    rows = []
    for m in idx:
        d, t, v = dxy_trend.get(m), tnx_trend.get(m), vix.get(m)
        if pd.isna(d) or pd.isna(t) or pd.isna(v):
            continue
        dxy_up = bool(d > 0)
        tnx_up = bool(t > 0)
        vix_over = bool(v > VIX_THRESHOLD)
        cell = f"달러{'↑' if dxy_up else '↓'}금리{'↑' if tnx_up else '↓'}VIX{'>20' if vix_over else '≤20'}"
        rows.append({
            "month": m, "dxy_trend_pct": round(float(d) * 100, 2), "dxy_up": dxy_up,
            "tnx_trend_pct": round(float(t) * 100, 2), "tnx_up": tnx_up,
            "vix_level": round(float(v), 1), "vix_over20": vix_over,
            "cell": cell,
        })
    return pd.DataFrame(rows).set_index("month")


# ── 섹터 유니버스 가격 데이터 ────────────────────────────────────────
def resolve_sector_tickers() -> tuple[list, list]:
    kr = sorted({t for t in sectors.SECTOR_MAP if naver_kr.is_kr(t)})
    us = sorted({t for t in sectors.SECTOR_MAP if not naver_kr.is_kr(t)})
    return kr, us


def fetch_kr_monthly(tickers: list, days: int = 3650, concurrency: int = 12) -> dict:
    """KR 섹터 종목 월봉 종가 + 최근 평균거래대금(유동성 컷용, 현재시점
    1회성 — 스크립트 상단 docstring 참고). harness.fetch_universe_data의
    ThreadPoolExecutor 패턴 재사용(README 규칙3)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _one(t):
        df = naver_kr.fetch_history(t, days=days)
        if df is None or df.empty or len(df) < 40:
            return t, None
        close_m = df["Close"].resample("ME").last().dropna()
        if len(close_m) < 24:   # 최소 2년치 없으면 랭킹에 넣기엔 너무 짧음
            return t, None
        recent_turnover = float((df["Close"] * df["Volume"]).tail(60).mean())
        return t, {"close_m": close_m, "turnover": recent_turnover}

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


def fetch_us_monthly(tickers: list, batch: int = 80) -> dict:
    out = {}
    t0 = time.time()
    for i in range(0, len(tickers), batch):
        chunk = tickers[i:i + batch]
        try:
            raw = yf.download(chunk, period="max", interval="1mo",
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
                close_m = df["Close"].dropna()
                if len(close_m) < 24:
                    continue
                close_m.index = pd.DatetimeIndex(close_m.index).tz_localize(None)
                close_m.index = close_m.index.to_period("M").to_timestamp("M")
                # 월간 달러거래대금 근사 = 월합계거래량 × 월평균종가 / 그 달 영업일수(21 근사)
                vol_m = df["Volume"].dropna().tail(3)
                px_m = df["Close"].dropna().tail(3)
                recent_turnover = float((vol_m * px_m).mean() / 21) if len(vol_m) and len(px_m) else 0.0
                out[t] = {"close_m": close_m, "turnover": recent_turnover}
            except Exception:
                continue
        log(f"[us] batch {i//batch+1}/{(len(tickers)-1)//batch+1} elapsed={time.time()-t0:.0f}s")
    log(f"[us] 완료 {len(out)}/{len(tickers)} elapsed={time.time()-t0:.0f}s")
    return out


def build_sector_return_panel(price_data: dict, is_kr: bool) -> tuple[pd.DataFrame, pd.Series, dict]:
    """월별 {섹터: 상대수익률} 패널 + 시장(전체 유니버스 중앙값) 수익률 +
    섹터별 종목수. 유동성 컷(현재시점 1회성) + 섹터당 최소종목수 적용."""
    floor_ = harness.KR_LIQUIDITY_FLOOR if is_kr else harness.US_LIQUIDITY_FLOOR
    kept = {t: d for t, d in price_data.items() if d["turnover"] >= floor_}
    log(f"[{'kr' if is_kr else 'us'}] 유동성 컷 통과 {len(kept)}/{len(price_data)} "
        f"(기준 {floor_:,.0f})")

    rets = {}
    for t, d in kept.items():
        r = d["close_m"].pct_change()
        rets[t] = r
    ret_df = pd.DataFrame(rets)  # index=month, columns=ticker

    sector_of = {t: sectors.get_sector(t) for t in kept}
    sector_counts = Counter(sector_of.values())
    valid_sectors = {s for s, n in sector_counts.items() if n >= MIN_TICKERS_PER_SECTOR and s != "기타"}
    log(f"[{'kr' if is_kr else 'us'}] 유효 섹터(>={MIN_TICKERS_PER_SECTOR}종목) {len(valid_sectors)}개: "
        f"{sorted(valid_sectors)}")

    market_ret = ret_df.median(axis=1, skipna=True)   # 시장 프록시 = 유니버스 횡단면 중앙값

    sector_ret = pd.DataFrame(index=ret_df.index)
    for s in valid_sectors:
        members = [t for t, sec in sector_of.items() if sec == s]
        sector_ret[s] = ret_df[members].median(axis=1, skipna=True)

    rel_ret = sector_ret.sub(market_ret, axis=0)
    return rel_ret, market_ret, {s: sector_counts[s] for s in valid_sectors}, sector_ret


# ── 통계 유틸 (harness.py로 승격됨, README 규칙3 — 2026-09-01 단기반응
# 측정 스크립트도 동일 로직이 필요해져 하네스로 옮기고 여기선 재사용만) ──
welch_zscore = harness.welch_zscore
hypergeom_overlap_pvalue = harness.hypergeom_overlap_pvalue


# ── 측정 1: 셀별 상/하위 섹터 + 일관성 ────────────────────────────────
def measure1_cell_rankings(rel_ret_next: pd.DataFrame, regime: pd.DataFrame, market_label: str):
    """rel_ret_next: 이미 익월로 shift된 상대수익률 패널(index=레짐 관측월,
    값=그 다음달 실현 상대수익률). regime: build_regime_series() 결과."""
    log(f"\n{'='*70}\n측정1 — {market_label} 셀별 상/하위{TOPN} 섹터 (n=섹터 {rel_ret_next.shape[1]}개)\n{'='*70}")
    results = {}
    for cell in sorted(regime["cell"].unique()):
        months = regime.index[regime["cell"] == cell]
        sub = rel_ret_next.loc[rel_ret_next.index.intersection(months)].dropna(how="all")
        n = len(sub)
        if n == 0:
            continue
        avg = sub.mean(axis=0).dropna().sort_values(ascending=False)
        top = avg.head(TOPN)
        bottom = avg.tail(TOPN)
        # 일관성: 이 셀의 개별 월들에서, top 섹터가 "그 달" 전체 섹터 중
        # 상위TOPN 안에 실제로 들었던 비율.
        monthly_rank = sub.rank(axis=1, ascending=False)
        consistency = {}
        for sname in top.index:
            if sname not in monthly_rank.columns:
                continue
            in_top = (monthly_rank[sname] <= TOPN).sum()
            valid = monthly_rank[sname].notna().sum()
            consistency[sname] = round(in_top / valid, 2) if valid else None
        results[cell] = {"n": n, "top": top.round(4).to_dict(), "bottom": bottom.round(4).to_dict(),
                          "consistency": consistency}
        log(f"  [{cell}] n={n}개월")
        log(f"    상위{TOPN}: " + ", ".join(f"{s}({v:+.2%}, 일관성{consistency.get(s):.0%})"
                                          for s, v in top.items()))
        log(f"    하위{TOPN}: " + ", ".join(f"{s}({v:+.2%})" for s, v in bottom.items()))
        # 규칙7: 상위군 vs 하위군 관측치 풀 자체의 격차 유의성(월별 개별
        # 관측치를 그대로 풀링 — 셀 평균이 아니라 원 관측치 분산 사용)
        top_pool = pd.concat([sub[s] for s in top.index if s in sub.columns])
        bot_pool = pd.concat([sub[s] for s in bottom.index if s in sub.columns])
        z, sig = welch_zscore(bot_pool, top_pool)
        log(f"    상/하위 격차 z={z:.2f}({'유의' if sig else '비유의'})" if z is not None
            else "    상/하위 격차: 계산 불가(표본부족)")
    return results


# ── 측정 2: 시기 반분 재현 ────────────────────────────────────────────
def measure2_split_half(rel_ret_next: pd.DataFrame, regime: pd.DataFrame, market_label: str):
    log(f"\n{'='*70}\n측정2 — {market_label} 시기 반분 재현(사전 등록 채택 기준: 겹침률 {ADOPT_OVERLAP_THRESHOLD:.0%}+)\n{'='*70}")
    # 레짐 시계열(최대 440개월, DXY/TNX/VIX 가용기간)과 종목 수익률 실제
    # 가용기간이 다르다(KR은 naver_kr 특성상 ~10년 이내) — 전체 레짐
    # 인덱스를 반으로 쪼개면 시장 데이터가 아예 없는 구간이 통째로 "전반"에
    # 들어가 모든 셀이 표본부족으로 나오는 버그가 있었다(2026-09-01 최초
    # 실행에서 KR 8/8셀 전부 표본부족으로 발견, 원인 확인 후 수정). 반드시
    # "실제 수익률 데이터가 있는 월"만 추려 그 범위를 반으로 쪼갠다.
    covered_months = rel_ret_next.dropna(how="all").index
    months_sorted = regime.index.intersection(covered_months).sort_values()
    mid = len(months_sorted) // 2
    first_half, second_half = months_sorted[:mid], months_sorted[mid:]
    log(f"  실제 데이터 가용 구간: {months_sorted.min().date() if len(months_sorted) else 'N/A'} ~ "
        f"{months_sorted.max().date() if len(months_sorted) else 'N/A'} ({len(months_sorted)}개월) "
        f"→ 전반 {len(first_half)}개월 / 후반 {len(second_half)}개월")
    s_total = rel_ret_next.shape[1]

    def _top_by_cell(month_index):
        out = {}
        for cell in sorted(regime["cell"].unique()):
            cell_months = regime.index[(regime["cell"] == cell) & regime.index.isin(month_index)]
            sub = rel_ret_next.loc[rel_ret_next.index.intersection(cell_months)].dropna(how="all")
            if len(sub) < MIN_MONTHS_PER_HALF:
                out[cell] = None
                continue
            avg = sub.mean(axis=0).dropna().sort_values(ascending=False)
            out[cell] = (set(avg.head(TOPN).index), len(sub))
        return out

    top_first = _top_by_cell(first_half)
    top_second = _top_by_cell(second_half)

    overlaps = []
    sig_count = 0
    tested_count = 0
    for cell in sorted(regime["cell"].unique()):
        a, b = top_first.get(cell), top_second.get(cell)
        if a is None or b is None:
            log(f"  [{cell}] 표본부족(전반 또는 후반 n<{MIN_MONTHS_PER_HALF}) — 비교 제외")
            continue
        set_a, n_a = a
        set_b, n_b = b
        overlap_n = len(set_a & set_b)
        overlap_rate = overlap_n / TOPN
        pval = hypergeom_overlap_pvalue(overlap_n, s_total, TOPN)
        chance_rate = TOPN / s_total  # 무작위 두 top-N이 겹칠 기댓값(비율), 참고용 병기
        tested_count += 1
        sig = pval is not None and pval < 0.05
        if sig:
            sig_count += 1
        overlaps.append(overlap_rate)
        log(f"  [{cell}] 전반(n={n_a}) top{TOPN}={sorted(set_a)} / 후반(n={n_b}) top{TOPN}={sorted(set_b)} "
            f"→ 겹침 {overlap_n}/{TOPN}({overlap_rate:.0%}) · 우연기준선 {chance_rate:.0%} · "
            f"p={pval:.3f}({'유의' if sig else '비유의'})" if pval is not None else
            f"  [{cell}] 전반 top{TOPN}={sorted(set_a)} / 후반 top{TOPN}={sorted(set_b)} → p값 계산불가")

    avg_overlap = sum(overlaps) / len(overlaps) if overlaps else None
    log(f"\n  평균 겹침률: {avg_overlap:.1%}" if avg_overlap is not None else "  평균 겹침률: 계산 불가(비교 가능 셀 0개)")
    log(f"  통계적으로 유의(p<0.05)한 셀: {sig_count}/{tested_count}")
    return avg_overlap, sig_count, tested_count


# ── 측정 3: 현재 레짐 ─────────────────────────────────────────────────
def measure3_current_regime(regime: pd.DataFrame, cell_results: dict, market_label: str):
    log(f"\n{'='*70}\n측정3 — {market_label} 현재 레짐 판정\n{'='*70}")
    if regime.empty:
        log("  레짐 데이터 없음"); return
    last = regime.iloc[-1]
    log(f"  기준월: {regime.index[-1].date()}")
    log(f"  달러(DXY {REGIME_TREND_MONTHS}개월 추세): {last['dxy_trend_pct']:+.2f}% → "
        f"{'상승' if last['dxy_up'] else '하락'}")
    log(f"  금리(10Y {REGIME_TREND_MONTHS}개월 추세): {last['tnx_trend_pct']:+.2f}% → "
        f"{'상승' if last['tnx_up'] else '하락'}")
    log(f"  위험선호(VIX): {last['vix_level']} → {'공포(>20)' if last['vix_over20'] else '안정(≤20)'}")
    log(f"  현재 셀: {last['cell']}")
    cell_data = cell_results.get(last["cell"])
    if not cell_data:
        log("  이 셀의 과거 데이터 없음/부족 — 참고 불가")
        return
    n = cell_data["n"]
    reliable = n >= MIN_MONTHS_PER_HALF * 2
    reliability_note = "" if reliable else " (⚠️ 표본부족 — 참고용 아님)"
    log(f"  이 셀의 과거 관측 {n}개월{reliability_note}")
    log(f"  역사적 우호 섹터: {cell_data['top']}")
    log(f"  역사적 비우호 섹터: {cell_data['bottom']}")


# ── 측정 4: 실용 비교(워크포워드) ─────────────────────────────────────
def measure4_walkforward(rel_ret_next: pd.DataFrame, abs_ret_next: pd.DataFrame,
                          market_ret_next: pd.Series, regime: pd.DataFrame, market_label: str):
    log(f"\n{'='*70}\n측정4 — {market_label} 실용 비교(워크포워드, 룩어헤드 방지)\n{'='*70}")
    log("  '돈의흐름 강세테마 추종' 비교 항목: theme_map.json 도입이 2026-08월"
        "이라 이 10년+ 백테스트 기간과 겹치는 이력이 없음 — 가짜 비교를"
        "만들지 않기 위해 건너뜀. 향후 theme_map 이력이 충분히 쌓이면"
        "별도 측정 필요.")
    months_sorted = regime.index.sort_values()
    if len(months_sorted) <= WALKFORWARD_WARMUP_MONTHS:
        log(f"  워밍업 기간({WALKFORWARD_WARMUP_MONTHS}개월) 확보 불가 — 표본 부족")
        return
    strat_rets, mkt_rets = [], []
    for i in range(WALKFORWARD_WARMUP_MONTHS, len(months_sorted)):
        t = months_sorted[i]
        prior_months = months_sorted[:i]   # t 이전만 사용(룩어헤드 방지)
        cell = regime.loc[t, "cell"]
        cell_prior_months = regime.index[(regime["cell"] == cell) & regime.index.isin(prior_months)]
        sub = rel_ret_next.loc[rel_ret_next.index.intersection(cell_prior_months)].dropna(how="all")
        m_ret = market_ret_next.get(t)
        if m_ret is None or pd.isna(m_ret):
            continue
        if len(sub) < MIN_MONTHS_PER_HALF:
            # 이 레짐에 대한 사전 데이터 부족 — 시장 수익률로 대체(현금 아님)
            strat_rets.append(m_ret)
            mkt_rets.append(m_ret)
            continue
        top_sectors = sub.mean(axis=0).dropna().sort_values(ascending=False).head(TOPN).index.tolist()
        realized = [abs_ret_next.loc[t, s] for s in top_sectors
                    if s in abs_ret_next.columns and t in abs_ret_next.index and not pd.isna(abs_ret_next.loc[t, s])]
        strat_ret = sum(realized) / len(realized) if realized else m_ret
        strat_rets.append(strat_ret)
        mkt_rets.append(m_ret)

    if not strat_rets:
        log("  워크포워드 관측치 없음"); return
    strat_s = pd.Series(strat_rets)
    mkt_s = pd.Series(mkt_rets)
    cum_strat = (1 + strat_s).prod() - 1
    cum_mkt = (1 + mkt_s).prod() - 1
    n_months = len(strat_s)
    yrs = n_months / 12
    cagr_strat = (1 + cum_strat) ** (1 / yrs) - 1 if yrs > 0 else None
    cagr_mkt = (1 + cum_mkt) ** (1 / yrs) - 1 if yrs > 0 else None
    z, sig = welch_zscore(mkt_s, strat_s)
    log(f"  워크포워드 구간: {n_months}개월(~{yrs:.1f}년)")
    log(f"  레짐 우호섹터 상위{TOPN} 매월 보유: 누적 {cum_strat:+.1%}, CAGR {cagr_strat:+.1%}"
        if cagr_strat is not None else f"  레짐전략 누적 {cum_strat:+.1%}")
    log(f"  시장 전체(유니버스 중앙값): 누적 {cum_mkt:+.1%}, CAGR {cagr_mkt:+.1%}"
        if cagr_mkt is not None else f"  시장전체 누적 {cum_mkt:+.1%}")
    log(f"  월별 수익률 격차 z={z:.2f}({'유의' if sig else '비유의'})" if z is not None else "  격차 유의성 계산 불가")


def run_market(market_label: str, is_kr: bool, price_data: dict, regime: pd.DataFrame):
    rel_ret, market_ret, sector_counts, abs_ret = build_sector_return_panel(price_data, is_kr)
    if rel_ret.empty:
        log(f"[{market_label}] 섹터 수익률 패널이 비었음 — 측정 중단")
        return
    # "익월" 정렬: 레짐 관측월 t의 라벨에, t+1월 실현 수익률을 붙인다.
    rel_ret_next = rel_ret.shift(-1)
    abs_ret_next = abs_ret.shift(-1)
    market_ret_next = market_ret.shift(-1)

    cell_results = measure1_cell_rankings(rel_ret_next, regime, market_label)
    avg_overlap, sig_count, tested_count = measure2_split_half(rel_ret_next, regime, market_label)
    measure3_current_regime(regime, cell_results, market_label)
    measure4_walkforward(rel_ret_next, abs_ret_next, market_ret_next, regime, market_label)

    log(f"\n{'='*70}\n{market_label} 판정\n{'='*70}")
    if avg_overlap is None or tested_count == 0:
        log("  판정 불가 — 비교 가능한 셀이 없음(표본부족)")
    elif avg_overlap >= ADOPT_OVERLAP_THRESHOLD and sig_count >= max(1, tested_count // 2):
        log(f"  채택 — 평균 겹침률 {avg_overlap:.0%} >= {ADOPT_OVERLAP_THRESHOLD:.0%} "
            f"AND 유의 셀 {sig_count}/{tested_count}(과반) → '참고 레이어'로 홈 매크로 레짐 카드 설계 검토")
    else:
        log(f"  기각 — 평균 겹침률 {avg_overlap:.0%}"
            + ("" if avg_overlap >= ADOPT_OVERLAP_THRESHOLD else f" < {ADOPT_OVERLAP_THRESHOLD:.0%}")
            + f", 유의 셀 {sig_count}/{tested_count} → '매크로→섹터는 예측력 없음'으로 기록")


def main():
    t0 = time.time()
    log("="*70)
    log("매크로 레짐 데이터 수집")
    log("="*70)
    macro = fetch_macro_monthly()
    regime = build_regime_series(macro)
    log(f"레짐 시계열: {len(regime)}개월 ({regime.index.min().date()} ~ {regime.index.max().date()})")
    log(f"셀 분포: {Counter(regime['cell']).most_common()}")

    kr_tickers, us_tickers = resolve_sector_tickers()
    log(f"\n섹터 매핑 종목: KR {len(kr_tickers)}개, US {len(us_tickers)}개")

    log("\n" + "="*70)
    log("KR 섹터 종목 월봉 수집")
    log("="*70)
    kr_price = fetch_kr_monthly(kr_tickers)

    log("\n" + "="*70)
    log("US 섹터 종목 월봉 수집")
    log("="*70)
    us_price = fetch_us_monthly(us_tickers)

    run_market("KR", True, kr_price, regime)
    run_market("US", False, us_price, regime)

    log(f"\n[main] 총 소요시간 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
