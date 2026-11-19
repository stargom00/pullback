"""추적 장부(내가 그은 선) — v5.386. 사용자 지시 2026-11-19: "종목 추적이 잘안돼. 하이브도 내가 채널 다 그려놓고 까먹어서 매매못함 …
알림봇이나 일지매매시트로 보완할수있는거 설계해봐" → 설계 제안(선을 그으면 그 자리에서 등록, 지켜보는 건 봇) → "응 만들어".

선 = 두 점 [날짜, 가격]. 값은 두 점을 **달력 일수**로 잇고 늘린 직선:
  scale "log"(기본, 로그 차트에서 그은 선)    : p1 × (p2/p1)^((on−d1)/(d2−d1))
  scale "linear"(일반 차트에서 그은 선)        : p1 + (p2−p1) × (on−d1)/(d2−d1)
얼마냐봇 trendlines.line_value와 같은 정의다(봇은 별도 레포라 import 공유 불가 — 두 레포 테스트에 같은 대조값을 둔다:
test_track_lines.py::test_shared_vectors ↔ stock-alert test_trendlines.py). 트레이딩뷰 선은 "봉 수" 기준 직선이라 휴장일만큼
어긋난다(주말은 고르게 섞여 영향 작음) — 선 ±3% 접근 알림 용도에는 충분하다고 보고 봇 v2.56의 달력 일수 정의를 그대로 쓴다.

접근 ±3%·돌파/이탈 ±3%는 봇 v2.56 상수(NEAR_PCT·BREAK_PCT, 사용자 공부 노트의 예시 규칙 — 측정 근거 없음). 여기 NEAR_PCT는 화면 묶음용 사본
(scanner가 아닌 봇 값과 동기화 필요 — 봇 trendlines.NEAR_PCT).
**표시·알림 전용 · 측정 전.** 선에 닿았다고 오르내린다는 주장 아님.
"""
from __future__ import annotations

import math
import re
from datetime import date

LABELS = ("빗각", "채널 상단", "채널 하단", "수평선", "기타")
SCALES = ("log", "linear")
STATUSES = ("추적", "종료")
NEAR_PCT = 3.0            # 봇 trendlines.NEAR_PCT와 동기화 필요(화면 "근접" 묶음)
NAME_MAX, THEME_MAX, NOTE_MAX = 40, 30, 300
FIELDS = {"id", "ticker", "name", "theme", "label", "kind", "scale", "p1", "p2", "note", "status",
          "rev", "updated_at", "created_at"}


def _d(s) -> date:
    return s if isinstance(s, date) else date.fromisoformat(str(s)[:10])


def line_value(line: dict, on) -> float:
    (d1, p1), (d2, p2) = line["p1"], line["p2"]
    d1, d2, on = _d(d1), _d(d2), _d(on)
    span = (d2 - d1).days
    if span == 0:
        raise ValueError(f"{line.get('id')}: 두 점 날짜가 같다")
    k = (on - d1).days / span
    p1, p2 = float(p1), float(p2)
    if line.get("scale") == "linear":
        return p1 + (p2 - p1) * k
    return p1 * math.exp(math.log(p2 / p1) * k)


def kind_of(line: dict) -> str:
    p1, p2 = float(line["p1"][1]), float(line["p2"][1])
    a, b = (p1, p2) if _d(line["p1"][0]) <= _d(line["p2"][0]) else (p2, p1)
    return "flat" if abs(b / a - 1) < 1e-9 else ("rise" if b > a else "fall")


def _pt_bad(p, nm) -> "str | None":
    if not (isinstance(p, (list, tuple)) and len(p) == 2):
        return f"{nm}은 [날짜, 가격]"
    try:
        if not isinstance(p[0], str) or date.fromisoformat(p[0]).isoformat() != p[0]:
            raise ValueError
    except ValueError:
        return f"{nm} 날짜는 YYYY-MM-DD"
    if isinstance(p[1], bool) or not isinstance(p[1], (int, float)) or not math.isfinite(p[1]) or p[1] <= 0:
        return f"{nm} 가격은 0보다 큰 숫자"
    return None


def invalid(rec: dict) -> "str | None":
    """저장 전 검증 — 문제를 문자열로, 없으면 None. kind는 서버가 두 점으로 다시 계산해 넣는다(받은 값은 안 믿는다)."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(rec.get("id") or "")):
        return "id는 영숫자·-·_ 64자 이하"
    tk = rec.get("ticker")
    if not (isinstance(tk, str) and re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,15}", tk)):
        return "티커는 대문자(예: 352820.KS, NVDA)"
    nm = rec.get("name")
    if not isinstance(nm, str) or not nm.strip() or len(nm) > NAME_MAX or "\n" in nm:
        return f"종목 이름을 입력하세요({NAME_MAX}자 이하)"
    th = rec.get("theme")
    if th is not None and (not isinstance(th, str) or len(th) > THEME_MAX or "\n" in th):
        return f"테마는 {THEME_MAX}자 이하 한 줄"
    if rec.get("label") is not None and rec.get("label") not in LABELS:
        return f"선 종류는 {list(LABELS)} 중 하나"
    if rec.get("scale") is not None and rec.get("scale") not in SCALES:
        return "scale은 log|linear"
    if rec.get("status") is not None and rec.get("status") not in STATUSES:
        return f"상태는 {list(STATUSES)} 중 하나"
    for k in ("p1", "p2"):
        bad = _pt_bad(rec.get(k), "첫 점" if k == "p1" else "둘째 점")
        if bad:
            return bad
    if rec["p1"][0] == rec["p2"][0]:
        return "두 점 날짜가 달라야 해요"
    note = rec.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > NOTE_MAX):
        return f"메모는 {NOTE_MAX}자 이하"
    extra = set(rec) - FIELDS
    if extra:
        return f"알 수 없는 필드: {sorted(extra)}"
    return None


def view_row(rec: dict, close, on) -> dict:
    """화면·봇용 계산 필드. close 없으면(가격 조회 실패) value만. on = 그 종가의 날짜."""
    v = line_value(rec, on)
    out = {"value": v, "value_date": str(on), "close": close, "dist_pct": None, "to_line_pct": None, "zone": "unknown"}
    if close:
        d = (float(close) / v - 1) * 100
        out["dist_pct"] = d
        out["to_line_pct"] = (v / float(close) - 1) * 100      # v5.390 화면용: 지금 가격에서 선까지 가려면 몇 % (위 선 +, 아래 선 −)
        out["zone"] = "near" if abs(d) <= NEAR_PCT + 1e-9 else ("above" if d > 0 else "below")
    return out
