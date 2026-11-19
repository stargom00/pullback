"""v5.393 테마 바구니 — 같은 테마 종목 묶음과 "테마 점화" 판정(순수 함수, app.py 미의존).

사용자 2026-11-19 "국장엔 테마가있어 … 종목 추적이 잘안돼" → 추적 설계 2단계 "이제 이거하자".
바구니 = {id, name, members:[{ticker, name}], note, status}. 종목별 오늘 등락률(전 봉 종가 대비)을 모아
테마가 같이 움직이는지를 한 줄로 요약한다.

**관심 신호 · 측정 전.** 점화한 테마가 이후 더 오른다는 건 잰 적 없다 — 승률·우위 주장 금지.

상수 출처(전부 AI 어림값 — 웹 검색·백테스트 없이 정함, 재검토 대상):
- IGNITE_UP_PCT 5.0: "강한 종목" = 오늘 +5% 이상. 눈에 띄는 양봉의 어림 기준.
- IGNITE_MIN_COUNT 3 · IGNITE_MIN_FRAC 0.3: 강한 종목이 3개 이상이면서 가격 받은 종목의 30% 이상이면 점화.
  한두 종목만 튄 것(개별 재료)과 테마가 같이 움직인 것을 가르려는 값.
- MAX_MEMBERS 40: 한 바구니 종목 수 상한(가격 조회 부담).

등락률 기준(v5.394 — 사용자 2026-11-19 "등락률이 너무 다른데"): **증권사·naver 화면과 같은 값**을 쓴다.
- KR = naver `/api/stock/{code}/basic`의 등락률(현재가 ÷ 전일 정규장 종가, 애프터마켓 중엔 애프터 현재가) — naver_kr.fetch_quote.
- US = 마지막 일봉 종가 ÷ 전 봉 종가(yfinance, 정규장).
v5.393은 KR을 naver 통합 일봉 종가끼리 나눴는데 화면과 크게 달랐다. 실측 2026-11-19 스튜디오드래곤: 화면 +29.96%(상한가) vs
일봉 기준 +6.0% — 11-18 애프터마켓에서 29,050까지 오른 것이 일봉으로는 11-18 봉에 들어갔기 때문. 지난 날짜의 화면 등락률은
naver가 주지 않아(지난 일봉 줄은 통합 종가 기준) "최근 점화" 되짚기는 뺐다.
"""
from __future__ import annotations

import math
import re

_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")

IGNITE_UP_PCT = 5.0
IGNITE_MIN_COUNT = 3
IGNITE_MIN_FRAC = 0.3
MAX_MEMBERS = 40
MIN_MEMBERS = 2
STATUSES = ("추적", "종료")


def norm_ticker(t) -> str:
    return str(t or "").upper().strip()


def parse_members(text: str) -> list:
    """양식의 여러 줄 글 → members. 한 줄 = "이름 티커" 또는 "티커"(쉼표·탭도 구분). 티커 = 줄의 마지막 토막."""
    out = []
    for line in str(text or "").replace(",", "\n").splitlines():
        parts = line.split()
        if not parts:
            continue
        tk = norm_ticker(parts[-1])
        out.append({"ticker": tk, "name": " ".join(parts[:-1]).strip() or tk})
    return out


def invalid(rec: dict) -> "str | None":
    if not str(rec.get("id") or "").strip():
        return "id 없음"
    if not str(rec.get("name") or "").strip():
        return "테마 이름을 적어 줘"
    if rec.get("status", "추적") not in STATUSES:
        return f"status는 {'/'.join(STATUSES)}"
    ms = rec.get("members")
    if not isinstance(ms, list) or not (MIN_MEMBERS <= len(ms) <= MAX_MEMBERS):
        return f"종목은 {MIN_MEMBERS}~{MAX_MEMBERS}개"
    seen = set()
    for m in ms:
        if not isinstance(m, dict):
            return "종목 형식 오류"
        tk = norm_ticker(m.get("ticker"))
        if not _TICKER_RE.match(tk):
            return f"티커가 이상해: {m.get('ticker')!r}"
        if tk in seen:
            return f"같은 종목이 두 번: {tk}"
        seen.add(tk)
    return None


def clean(rec: dict) -> dict:
    """저장할 모양으로(티커 대문자, 이름 없으면 티커)."""
    out = {k: v for k, v in rec.items() if k not in ("rows", "summary")}
    out["members"] = [{"ticker": norm_ticker(m.get("ticker")), "name": str(m.get("name") or "").strip() or norm_ticker(m.get("ticker"))}
                      for m in rec.get("members") or []]
    out["name"] = str(rec.get("name") or "").strip()
    out["note"] = str(rec.get("note") or "").strip()
    out.setdefault("status", "추적")
    return out


def member_row(member: dict, close, chg_pct, day) -> dict:
    """한 종목의 오늘 줄. 가격을 못 받았으면 chg_pct None(요약에서 빠지고 화면에 드러난다)."""
    ok = close is not None and chg_pct is not None and close > 0 and bool(day)
    return {"ticker": member["ticker"], "name": member.get("name") or member["ticker"],
            "close": float(close) if ok else None, "chg_pct": round(float(chg_pct), 2) if ok else None, "date": day if ok else None}


def summary(rows: list) -> dict:
    """바구니 요약. 가장 최근 봉 날짜(date)의 줄만 센다 — 거래정지·옛 봉은 n_stale로 따로 센다."""
    px = [r for r in rows if r.get("chg_pct") is not None]
    day = max((r["date"] for r in px if r.get("date")), default=None)
    cur = [r for r in px if r.get("date") == day]
    strong = [r for r in cur if r["chg_pct"] >= IGNITE_UP_PCT]
    need = max(IGNITE_MIN_COUNT, math.ceil(len(cur) * IGNITE_MIN_FRAC)) if cur else IGNITE_MIN_COUNT
    lead = max(cur, key=lambda r: r["chg_pct"], default=None)
    return {"n": len(rows), "n_px": len(cur), "n_failed": len(rows) - len(px), "n_stale": len(px) - len(cur),
            "date": day, "up": sum(1 for r in cur if r["chg_pct"] > 0), "strong": len(strong), "need": need,
            "avg_pct": round(sum(r["chg_pct"] for r in cur) / len(cur), 2) if cur else None,
            "leader": {"ticker": lead["ticker"], "name": lead["name"], "chg_pct": lead["chg_pct"]} if lead else None,
            "ignited": bool(cur) and len(strong) >= need}

