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
MAX_TOKENS = 4000
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}

PROMPT_TEMPLATE = """너는 투자자를 위한 경제 캘린더를 만드는 애널리스트다. web_search 도구를 \
사용해서 오늘({today})부터 4주(28일) 이내의 미국(US)과 한국(KR) 주요 경제/통화정책 일정을 \
조사해라.

포함할 이벤트:
- US: FOMC 회의(금리결정 발표일), CPI, PPI, 고용보고서(비농업고용지표), GDP 발표
- KR: 한국은행 금융통화위원회(금통위) 회의, 주요 경제지표(CPI 등) 발표

각 이벤트마다 다음을 조사해라: 정확한 발표 날짜(YYYY-MM-DD, 시간까지는 몰라도 됨 — \
날짜가 아직 미확정이면 그 이벤트는 제외), 국가(US 또는 KR), 이벤트명(한국어, 간결하게), \
중요도(FOMC·CPI·고용보고서는 "high", 그 외(PPI·GDP·금통위 등)는 "med").

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
