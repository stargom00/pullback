"""기관·외국인 순매수 5일 이력 요약 (v5.275, 사용자 지시).

순수 계산 모듈 — 네트워크·파일·전역 상태 없음(`abc_screener.py`와 같은 성격).
조회는 `naver_kr.fetch_investor_trend()`가 한다.

화면 문구: `"기관 +3/5 · 외국인 +2/5 · 기준 09-18"`
**필터가 아니라 표시 전용이다.** 어떤 게이트도 이 값을 읽으면 안 된다.

[기타법인은 없다] naver 모바일 API는 외국인·기관·개인 셋만 준다. 셋의 합으로
잔차를 내도 기타법인이 아니다 — 삼성전자 5일 잔차가 +1.7M~+2.0M으로 부호·크기가
거의 고정이라(SK하이닉스도 +575K~+663K) 실제 순매수 계열일 수 없는 계통 오차다.
KRX 별도 소스가 필요하며, 그 전까지 **기타법인은 표시하지 않는다**.

[조용한 빈 결과] 없는 종목도 200 OK에 `[]`를 준다(실측). 그래서 "못 받음"(None),
"받았는데 0건"([]), "정상"을 전부 구분해 `ok`/`reason`으로 내보낸다 — 하나로
뭉개면 v5.246~v5.252에서 겪은 것처럼 몇 달 뒤에나 발견된다.
"""
from __future__ import annotations

WINDOW = 5          # 화면에 쓰는 일수(사용자 지시). 조회는 20일이라 여유가 있다.

ORGAN_KEY = "organPureBuyQuant"
FOREIGN_KEY = "foreignerPureBuyQuant"


def _num(v):
    """`"+2,746,972"` 같은 문자열을 숫자로. 못 읽으면 None(0으로 뭉개지 않는다)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).replace(",", "").replace("+", "").strip()
    if not t or t in ("-", "N/A"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _fmt_date(bizdate) -> str | None:
    """`"20260918"` → `"09-18"`. 형식이 다르면 원문 그대로 둔다."""
    t = str(bizdate or "")
    return f"{t[4:6]}-{t[6:8]}" if len(t) == 8 and t.isdigit() else (t or None)


def summarize(rows, window: int = WINDOW) -> dict:
    """최근 `window` 거래일 중 **순매수였던 날 수**를 센다.

    반환:
        {"ok": bool, "reason": str|None,
         "organ": int|None, "foreign": int|None,   # 순매수 일수
         "of": int,                                # 분모(실제로 센 일수)
         "asof": "09-18"|None,                     # bizdate[0] — 기준일
         "text": "기관 +3/5 · 외국인 +2/5"|None}

    분모를 `window`로 고정하지 않고 **실제로 센 일수**로 내보낸다. 상장 직후처럼
    3일치밖에 없는 종목을 "+2/5"로 쓰면 나머지 2일이 순매도였던 것처럼 읽힌다.
    """
    out = {"ok": False, "reason": None, "organ": None, "foreign": None,
           "of": 0, "asof": None, "text": None}
    if rows is None:
        out["reason"] = "조회 실패"
        return out
    if not rows:
        out["reason"] = "데이터 없음"      # 200 OK인데 0건 — 상장폐지·오타 등
        return out

    seg = rows[:window]
    organ = foreign = 0
    counted = 0
    for r in seg:
        o, f = _num(r.get(ORGAN_KEY)), _num(r.get(FOREIGN_KEY))
        if o is None and f is None:
            continue                      # 그 날 값 자체가 없음 — 분모에서도 뺀다
        counted += 1
        if o is not None and o > 0:
            organ += 1
        if f is not None and f > 0:
            foreign += 1

    if not counted:
        out["reason"] = "순매수 필드 없음"   # 스키마가 바뀐 신호 — 조용히 0으로 두지 않는다
        return out

    out.update(ok=True, organ=organ, foreign=foreign,
               of=counted, asof=_fmt_date(seg[0].get("bizdate")))
    out["text"] = f"기관 +{organ}/{counted} · 외국인 +{foreign}/{counted}"
    return out
