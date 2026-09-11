"""
실적(EPS/매출) 성장 판정 — 미너비니 CAN SLIM 실적 기준 (v5.05, Phase 1).

[Phase 0 데이터 정찰 결과 — 이 구현의 전제]
미국·한국 각 20종목 샘플 테스트 (2026-07-25):
  - 미국(yfinance income_stmt/quarterly_income_stmt): 20/20 성공(100%).
    대형주~최근 상장 소형주까지 전부 연간 4년치 + 분기 5개치 EPS·매출 확보.
    지연 없음, NVDA/SK하이닉스급 실적 흐름과 방향성 검증 완료.
  - [v5.252 교체] 한국은 이제 m.stock.naver.com 모바일 JSON API(아래 참고).
    아래 줄들은 원래 소스(PC 페이지) 정찰 기록 — 2026-09-10 naver PC 페이지
    SPA 개편으로 그 페이지는 200 OK에 표 0건이 돼 폐기.
  - 한국(finance.naver.com/item/main.naver HTML "주요재무정보" 표): 19/20
    성공(95%). 실패 1건(091990 셀트리온헬스케어)은 2023년 실제 합병으로
    상장폐지된 종목이라 정상 동작(스크레이핑 결함 아님).
    ⚠️ 이 페이지는 UTF-8(naver_kr.py의 다른 페이지들이 쓰는 EUC-KR과 다름
    — 인코딩 잘못 지정하면 파싱이 조용히 전부 실패한다, 실제로 한 번 그렇게
    실패했다가 원인 확인).
    열(연간/분기) 개수가 종목마다 다르게 "보였던" 것은 최초 나이브 정규식의
    한계였고, 실제로는 <thead>의 colspan 속성(최근 연간 실적 colspan=N,
    최근 분기 실적 colspan=M)에서 정확한 개수를 읽어올 수 있음을 확인 —
    이 모듈은 그 colspan 기반 헤더 매칭을 사용해 "guess-patch" 없이 구현.

[판정 기준 — 사용자 스펙 그대로]
  1) 3년 연간 EPS 연속 증가 (실제치 3개년만, 추정치 제외)
  2) 최근 분기 EPS YoY >= 25%
  3) 매출 YoY 동반 증가 (같은 분기 기준)
  4) (선택) 증가율 가속 — 최근 분기 YoY% > 직전 분기 YoY%
데이터가 없거나 부족하면 제외(fail)가 아니라 "판정불가"(verdict='unknown')로
반환 — 호출부가 배지를 안 붙이거나 별도로 표시하게 한다.
"""
import math
import re

import requests

import naver_kr

_KR_HEADERS = naver_kr._HEADERS
_KR_TIMEOUT = 10
# v5.252(사용자 지시): finance.naver.com/item/main.naver(PC HTML "주요재무정보"
# 표)가 2026-09-10 장마감 전후 Next.js SPA로 개편돼 KR 전 종목이 "실적 표 없음"
# (판정불가)으로 조용히 떨어졌다 — 같은 개편으로 v5.246(유니버스)/v5.251(시총
# 필터)이 먼저 고쳐졌고 이게 마지막. 모바일 JSON API로 교체: 연간(실적 3년 +
# 컨센서스 1년)/분기(실적 5분기 + 컨센서스 1분기) 구성이 옛 PC 표와 동일하고
# EPS·매출액 행이 있어 판정 로직은 그대로 둔다(데이터 소스만 교체).
_KR_FINANCE_URL = "https://m.stock.naver.com/api/stock/{code}/finance/{period}"


def _pct_change(new: float | None, old: float | None) -> float | None:
    """(new-old)/|old| — old가 0/None이면 계산 불가(None)."""
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old) * 100


def _empty(reason: str) -> dict:
    return {"ok": False, "verdict": "unknown", "reasons": [reason]}


# ══════════════════════════════════════════════════════
# 미국 — yfinance income_stmt / quarterly_income_stmt
# ══════════════════════════════════════════════════════
def _us_earnings_growth(ticker: str) -> dict:
    try:
        import yfinance as yf
    except Exception:
        return _empty("yfinance 미탑재")
    try:
        tk = yf.Ticker(ticker)
        inc = tk.income_stmt
        qinc = tk.quarterly_income_stmt
    except Exception as e:
        return _empty(f"조회 실패: {e}")

    if inc is None or "Diluted EPS" not in inc.index:
        return _empty("연간 EPS 데이터 없음")

    eps_row = inc.loc["Diluted EPS"].dropna().sort_index()   # 오래된→최신
    annual_eps = [round(float(x), 4) for x in eps_row.tolist()]
    annual_periods = [str(d)[:10] for d in eps_row.index]

    out = {"ok": True, "verdict": "unknown", "reasons": [],
           "annual_eps": annual_eps, "annual_periods": annual_periods}

    # ── 조건1: 3년 연속 증가 (최근 실제 3개년) ──
    growing3 = None
    if len(annual_eps) >= 3:
        a, b, c = annual_eps[-3], annual_eps[-2], annual_eps[-1]
        growing3 = a < b < c
    out["annual_eps_growing"] = growing3

    # ── 조건2/4: 분기 EPS YoY(+가속) ──
    q_yoy = q_yoy_prev = None
    out["eps_sum_last4q"] = None   # v5.204: 최근 4분기 EPS 합 — 섹터 대장 게이트(적자 여부)용
    if qinc is not None and "Diluted EPS" in qinc.index:
        qeps = qinc.loc["Diluted EPS"].dropna().sort_index()
        vals = qeps.tolist()
        if len(vals) >= 5:
            q_yoy = _pct_change(vals[-1], vals[-5])
        if len(vals) >= 6:
            q_yoy_prev = _pct_change(vals[-2], vals[-6])
        if len(vals) >= 4:
            out["eps_sum_last4q"] = round(sum(vals[-4:]), 4)
    out["quarterly_eps_yoy_pct"] = round(q_yoy, 1) if q_yoy is not None else None

    # ── 조건3: 매출 YoY(같은 분기) ──
    rev_yoy = None
    if qinc is not None and "Total Revenue" in qinc.index:
        qrev = qinc.loc["Total Revenue"].dropna().sort_index()
        rvals = qrev.tolist()
        if len(rvals) >= 5:
            rev_yoy = _pct_change(rvals[-1], rvals[-5])
    out["revenue_yoy_pct"] = round(rev_yoy, 1) if rev_yoy is not None else None

    out["accelerating"] = (
        q_yoy is not None and q_yoy_prev is not None and q_yoy > q_yoy_prev
    )

    _finalize_verdict(out, growing3, q_yoy, rev_yoy)
    return out


# ══════════════════════════════════════════════════════
# 한국 — m.stock.naver.com finance/annual + finance/quarter (v5.252)
# ══════════════════════════════════════════════════════
def _mstock_num(raw) -> float | None:
    """"6,564"/"-1,234"/"-"/""/None → float 또는 None."""
    if raw is None:
        return None
    txt = str(raw).replace(",", "").strip()
    if txt in ("", "-", "N/A"):
        return None
    try:
        return float(txt)
    except ValueError:
        return None


def _mstock_period_cells(payload: dict, row_title: str) -> list | None:
    """모바일 finance 응답 하나(연간 또는 분기)에서 row_title 행을 기간 순서
    (key 오름차순 = 과거→최근)대로 [{value, est}]로 뽑는다. est=컨센서스(추정치)
    열(isConsensus=="Y") — 옛 PC 표의 "(E)" 열과 같은 의미. 행/기간이 없으면 None."""
    fi = (payload or {}).get("financeInfo") or {}
    titles = sorted(fi.get("trTitleList") or [], key=lambda x: str(x.get("key")))
    if not titles:
        return None
    row = next((r for r in (fi.get("rowList") or []) if r.get("title") == row_title), None)
    if row is None:
        return None
    cols = row.get("columns") or {}
    return [{"value": _mstock_num((cols.get(t.get("key")) or {}).get("value")),
             "est": t.get("isConsensus") == "Y"} for t in titles]


def _parse_kr_mobile(annual: dict, quarter: dict) -> dict | None:
    """연간/분기 응답 → 옛 _parse_kr_table()과 같은 구조
    {n_annual, n_quarter, periods, eps, revenue}. eps/revenue는 연간 셀 뒤에
    분기 셀을 이어붙인 목록(옛 PC 표의 열 순서와 동일) — 아래 판정 코드가
    n_annual로 잘라 쓰므로 그대로 호환. 연간·분기 어느 쪽이든 기간이 없으면
    None(= "실적 표 없음")."""
    a_eps = _mstock_period_cells(annual, "EPS")
    q_eps = _mstock_period_cells(quarter, "EPS")
    a_rev = _mstock_period_cells(annual, "매출액")
    q_rev = _mstock_period_cells(quarter, "매출액")
    a_titles = ((annual or {}).get("financeInfo") or {}).get("trTitleList") or []
    q_titles = ((quarter or {}).get("financeInfo") or {}).get("trTitleList") or []
    if not a_titles or not q_titles:
        return None
    eps = (a_eps + q_eps) if (a_eps is not None and q_eps is not None) else None
    revenue = (a_rev + q_rev) if (a_rev is not None and q_rev is not None) else None
    return {"n_annual": len(a_titles), "n_quarter": len(q_titles), "periods": None,
            "eps": eps, "revenue": revenue}


def _fetch_kr_finance(code: str, period: str) -> dict:
    resp = requests.get(_KR_FINANCE_URL.format(code=code, period=period),
                        headers=_KR_HEADERS, timeout=_KR_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _kr_earnings_growth(ticker: str) -> dict:
    code = naver_kr.to_code(ticker)   # 알파벳 혼용 신규코드(0011A0 등)도 그대로
    try:
        annual = _fetch_kr_finance(code, "annual")
        quarter = _fetch_kr_finance(code, "quarter")
    except Exception as e:
        return _empty(f"조회 실패: {e}")

    parsed = _parse_kr_mobile(annual, quarter)
    if parsed is None:
        return _empty("실적 표 없음(상장폐지·병합 종목일 수 있음)")
    if parsed["eps"] is None:
        return _empty("EPS 행 파싱 실패")

    n_annual = parsed["n_annual"]
    eps_cells = parsed["eps"]
    rev_cells = parsed["revenue"]

    annual_eps_cells = eps_cells[:n_annual]
    quarterly_eps_cells = eps_cells[n_annual:]
    annual_rev_cells = rev_cells[:n_annual] if rev_cells else []
    quarterly_rev_cells = rev_cells[n_annual:] if rev_cells else []

    out = {"ok": True, "verdict": "unknown", "reasons": [],
           "annual_eps": [c["value"] for c in annual_eps_cells],
           "annual_eps_actual_only": [c["value"] for c in annual_eps_cells if not c["est"]]}

    # ── 조건1: 3년 연속 증가 (실제치만, 앞에서부터 3개 — 실제치는 항상
    #    맨 앞쪽이고 추정치는 뒤쪽에만 붙으므로 순서 그대로 사용 가능) ──
    actual_annual = [c["value"] for c in annual_eps_cells if not c["est"] and c["value"] is not None]
    growing3 = None
    if len(actual_annual) >= 3:
        a, b, c = actual_annual[-3], actual_annual[-2], actual_annual[-1]
        growing3 = a < b < c
    out["annual_eps_growing"] = growing3

    # ── 조건2/4: 분기 EPS YoY(+가속) — 실제치 기준, 4분기 전과 비교 ──
    actual_q_eps = [c["value"] for c in quarterly_eps_cells if not c["est"] and c["value"] is not None]
    q_yoy = q_yoy_prev = None
    if len(actual_q_eps) >= 4:
        out["eps_sum_last4q"] = round(sum(actual_q_eps[-4:]), 4)   # v5.204: 섹터 대장 게이트(적자 여부)용
    else:
        out["eps_sum_last4q"] = None
    if len(actual_q_eps) >= 5:
        q_yoy = _pct_change(actual_q_eps[-1], actual_q_eps[-5])
    if len(actual_q_eps) >= 6:
        q_yoy_prev = _pct_change(actual_q_eps[-2], actual_q_eps[-6])
    out["quarterly_eps_yoy_pct"] = round(q_yoy, 1) if q_yoy is not None else None

    # ── 조건3: 매출 YoY(같은 분기) ──
    rev_yoy = None
    if quarterly_rev_cells:
        actual_q_rev = [c["value"] for c in quarterly_rev_cells if not c["est"] and c["value"] is not None]
        if len(actual_q_rev) >= 5:
            rev_yoy = _pct_change(actual_q_rev[-1], actual_q_rev[-5])
    out["revenue_yoy_pct"] = round(rev_yoy, 1) if rev_yoy is not None else None

    out["accelerating"] = (
        q_yoy is not None and q_yoy_prev is not None and q_yoy > q_yoy_prev
    )

    _finalize_verdict(out, growing3, q_yoy, rev_yoy)
    return out


def _finalize_verdict(out: dict, growing3, q_yoy, rev_yoy) -> None:
    """세 조건 판정을 종합해 verdict(pass/fail/unknown) + reasons 채움.
    하나라도 계산 불가면 unknown(제외 아님) — 스펙 명시 사항."""
    if growing3 is None or q_yoy is None or rev_yoy is None:
        out["verdict"] = "unknown"
        if growing3 is None:
            out["reasons"].append("연간 EPS 실제치 3년치 부족")
        if q_yoy is None:
            out["reasons"].append("분기 EPS YoY 계산 불가(데이터 부족)")
        if rev_yoy is None:
            out["reasons"].append("매출 YoY 계산 불가(데이터 부족)")
        return
    ok1, ok2, ok3 = growing3, q_yoy >= 25.0, rev_yoy > 0
    out["verdict"] = "pass" if (ok1 and ok2 and ok3) else "fail"
    if not ok1:
        out["reasons"].append("3년 연속 EPS 증가 미충족")
    if not ok2:
        out["reasons"].append(f"분기 EPS YoY {q_yoy:.1f}% (기준 25%↑ 미달)")
    if not ok3:
        out["reasons"].append(f"매출 YoY {rev_yoy:.1f}% (감소)")


def get_earnings_growth(ticker: str) -> dict:
    """종목 실적 성장 판정 진입점. 시장(KR/US) 자동 판별."""
    if naver_kr.is_kr(ticker):
        return _kr_earnings_growth(ticker)
    return _us_earnings_growth(ticker)
