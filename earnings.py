"""
실적(EPS/매출) 성장 판정 — 미너비니 CAN SLIM 실적 기준 (v5.05, Phase 1).

[Phase 0 데이터 정찰 결과 — 이 구현의 전제]
미국·한국 각 20종목 샘플 테스트 (2026-07-25):
  - 미국(yfinance income_stmt/quarterly_income_stmt): 20/20 성공(100%).
    대형주~최근 상장 소형주까지 전부 연간 4년치 + 분기 5개치 EPS·매출 확보.
    지연 없음, NVDA/SK하이닉스급 실적 흐름과 방향성 검증 완료.
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
_KR_MAIN_URL = "https://finance.naver.com/item/main.naver"


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
    if qinc is not None and "Diluted EPS" in qinc.index:
        qeps = qinc.loc["Diluted EPS"].dropna().sort_index()
        vals = qeps.tolist()
        if len(vals) >= 5:
            q_yoy = _pct_change(vals[-1], vals[-5])
        if len(vals) >= 6:
            q_yoy_prev = _pct_change(vals[-2], vals[-6])
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
# 한국 — finance.naver.com "주요재무정보" 표 (colspan 헤더 매칭)
# ══════════════════════════════════════════════════════
def _parse_kr_table(html: str) -> dict | None:
    """헤더의 colspan(연간/분기 개수) + <th scope="col">기간(E)?</th> 라벨을
    먼저 읽고, 그 개수만큼만 값 셀을 순서대로 매칭 — 개수 추측(guess) 없음."""
    idx = html.find("주요재무정보")
    if idx == -1:
        return None
    table = html[idx:idx + 40000]

    m_annual_span = re.search(r'colspan="(\d+)"[^>]*th_cop_anal6', table)
    m_quarter_span = re.search(r'colspan="(\d+)"[^>]*th_cop_anal7', table)
    if not m_annual_span or not m_quarter_span:
        return None
    n_annual = int(m_annual_span.group(1))
    n_quarter = int(m_quarter_span.group(1))
    total_cols = n_annual + n_quarter

    thead_end = table.find("</thead>")
    thead = table[:thead_end] if thead_end > 0 else table[:6000]
    period_cells = re.findall(
        r'<th scope="col"[^>]*>\s*([\d.]+)\s*(?:<em>&#40;E&#41;</em>)?\s*</th>', thead)
    est_flags = [bool(e) for e in re.findall(
        r'<th scope="col"[^>]*>\s*[\d.]+\s*(<em>&#40;E&#41;</em>)?\s*</th>', thead)]
    if len(period_cells) != total_cols or len(est_flags) != total_cols:
        periods = None   # 라벨 개수가 안 맞으면 신뢰 불가
    else:
        periods = [{"period": p, "est": e} for p, e in zip(period_cells, est_flags)]

    def row_values(label_pattern: str):
        m = re.search(rf'<th scope="row"[^>]*><strong>{label_pattern}</strong></th>', table)
        if not m:
            return None
        seg = table[m.end():]
        tds = re.findall(r'<td class="([^"]*)">(.*?)</td>', seg, re.S)[:total_cols]
        if len(tds) < total_cols:
            return None
        out = []
        for cls, raw in tds:
            numm = re.search(r'(-?[\d,]+\.?\d*)', raw)
            val = float(numm.group(1).replace(",", "")) if numm else None
            out.append({"value": val, "est": "cell_strong" in cls})
        return out

    eps = row_values(r"EPS\(원\)")
    revenue = row_values(r"매출액")
    return {"n_annual": n_annual, "n_quarter": n_quarter,
            "periods": periods, "eps": eps, "revenue": revenue}


def _kr_earnings_growth(ticker: str) -> dict:
    code = naver_kr.to_code(ticker)
    try:
        resp = requests.get(_KR_MAIN_URL, params={"code": code},
                            headers=_KR_HEADERS, timeout=_KR_TIMEOUT)
        resp.encoding = "utf-8"   # v5.05: 이 페이지는 UTF-8 — EUC-KR로 잘못 지정하면
        html = resp.text          # "주요재무정보" 텍스트 매칭이 조용히 전부 실패함
    except Exception as e:
        return _empty(f"조회 실패: {e}")

    parsed = _parse_kr_table(html)
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
