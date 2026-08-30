"""
macro_calendar.py — 캘린더 탭의 매크로 일정(FOMC/CPI/PPI/고용보고서/GDP,
한국은행 금통위 등) 생성. money_flow_report.py와 같은 원칙: Claude API +
서버사이드 web_search 툴을 쓰고, 실패해도 예외를 던지지 않고
(events, error) 튜플로 반환 — 호출부(app.py)가 이전 캐시로 폴백할 수 있게
한다. app.py에 대한 의존성 없음(app.py → macro_calendar.py 방향으로만
import) — money_flow.py/money_flow_report.py와 같은 원칙.

돈의 흐름과 달리 매일 재생성할 이유가 없는 데이터(향후 4주 일정은 하루
지난다고 크게 안 바뀜)라 주 1회 갱신으로 충분 — 캐시 staleness 판정은
app.py가 한다(이 모듈은 생성만 책임진다).
"""
from __future__ import annotations

import json
import os
import re

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 6000   # v5.111: 카테고리 7개로 확장(연준연설/국채입찰/옵션만기 등 추가)돼
                     # 웹서치 왕복·이벤트 수가 늘어남 — money_flow_report.py가 8000에서
                     # 잘렸던 전례(v5.87)를 감안해 여유 있게 상향
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 8}   # v5.111: 카테고리 5→7개로 늘어 5→8 상향

PROMPT_TEMPLATE = """너는 투자자를 위한 경제 캘린더를 만드는 애널리스트다. web_search 도구를 \
사용해서 오늘({today})부터 4주(28일) 이내의 미국(US)과 한국(KR) 주요 경제/통화정책 일정을 \
조사해라.

포함할 이벤트(v5.111 확장 — 항목이 너무 적으면 화면이 허전하니 아래 카테고리를 \
전부 훑어서 4주 안에 해당하는 건 빠짐없이 담아라):
- US 통화정책/지표: FOMC 회의(금리결정 발표일), CPI, PPI, 고용보고서(비농업고용지표), GDP 발표
- US 연준 인사 연설: 연준 의장/부의장/주요 이사의 예정된 공개 연설·의회 증언(웹서치로 일정 확인, \
  불확실하면 빼라)
- US 국채 입찰: 10년물·30년물 등 시장 영향이 큰 주요 국채 입찰일
- US 옵션만기일(쿼드러플위칭): 3/6/9/12월 세 번째 금요일 — 이 4주 구간에 해당 월의 세 번째 \
  금요일이 있으면 반드시 포함(계산 가능, 웹서치로 재확인)
- US 빅테크 실적 발표일: 애플·마이크로소프트·구글(알파벳)·아마존·메타·엔비디아 등 초대형주 \
  — 사용자 보유 여부와 무관하게 이 종목들의 실적은 시장 전체 방향에 영향을 준다. 발표일이 \
  아직 공식 미확정이면 예상 주간(예: "10월 마지막 주 예정")이라도 웹서치로 확인해 최대한 \
  포함해라(불확실하면 제외).
- KR: 한국은행 금융통화위원회(금통위) 회의, 주요 경제지표(CPI 등) 발표
- KR 선물옵션 만기일: 매월 두 번째 목요일(코스피200 선물·옵션 동시만기) — 해당 월의 두 번째 \
  목요일이 이 4주 구간에 있으면 포함

각 이벤트마다 다음을 조사해라: 정확한 발표 날짜(YYYY-MM-DD, 시간까지는 몰라도 됨 — \
날짜가 아직 미확정이면 그 이벤트는 제외), 국가(US 또는 KR), 이벤트명(한국어, 간결하게 — \
빅테크 실적은 "애플 실적발표"처럼 종목명 포함), 중요도(FOMC·CPI·고용보고서는 "high", \
그 외(PPI·GDP·금통위·연준연설·국채입찰·옵션만기·선물옵션만기·빅테크실적 등)는 "med").

조사가 끝나면 다른 설명 없이 최종 답변 맨 끝에 아래 JSON 코드블록 하나만 출력해라. \
날짜순으로 정렬해서 담아라:

```json
{{
  "events": [
    {{"date": "YYYY-MM-DD", "country": "US", "event": "FOMC 금리결정", "importance": "high"}}
  ]
}}
```"""


def generate_calendar(today: str) -> tuple[list | None, str | None]:
    """(events, error) 튜플 — 정확히 하나만 채워짐. today: "YYYY-MM-DD"."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None, "ANTHROPIC_API_KEY 환경변수 미설정"

    try:
        import anthropic
    except ImportError:
        return None, "anthropic 패키지 미설치 (requirements.txt 확인)"

    prompt = PROMPT_TEMPLATE.format(today=today)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            tools=[WEB_SEARCH_TOOL],
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        return None, f"Claude API 호출 실패: {type(e).__name__}: {e}"

    try:
        text_parts = [block.text for block in resp.content if getattr(block, "type", None) == "text"]
    except Exception as e:
        return None, f"응답 파싱 실패: {type(e).__name__}: {e}"

    text = "\n\n".join(p for p in text_parts if p).strip()
    if not text:
        return None, "API 응답에 텍스트 블록이 없음(웹서치만 반환됐거나 빈 응답)"

    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not blocks:
        return None, "응답에서 JSON 블록을 찾지 못함"
    try:
        data = json.loads(blocks[-1])
    except json.JSONDecodeError as e:
        return None, f"JSON 파싱 실패: {e}"

    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return None, "JSON에 events 배열이 없음"

    out = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        date = ev.get("date")
        country = (ev.get("country") or "").upper()
        event = ev.get("event")
        importance = ev.get("importance") if ev.get("importance") in ("high", "med") else "med"
        if not (date and re.match(r"^\d{4}-\d{2}-\d{2}$", str(date)) and country in ("US", "KR") and event):
            continue
        out.append({"date": date, "country": country, "event": str(event), "importance": importance})
    return out, None
