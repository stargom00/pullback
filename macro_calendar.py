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
from datetime import datetime, timedelta, timezone

import api_call_guard

KST = timezone(timedelta(hours=9))
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 6000   # v5.111: 카테고리 7개로 확장(연준연설/국채입찰/옵션만기 등 추가)돼
                     # 웹서치 왕복·이벤트 수가 늘어남 — money_flow_report.py가 8000에서
                     # 잘렸던 전례(v5.87)를 감안해 여유 있게 상향
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 8}   # v5.111: 카테고리 5→7개로 늘어 5→8 상향

# v5.125(사용자 지시, API 비용 급증 조사): 날짜만 빼면 매 호출 100% 동일한
# 텍스트라 system + cache_control로 분리(주 1회 자동 갱신은 캐시 TTL을
# 넘기지만, 프롬프트 튜닝 중 수동 재실행을 짧은 간격으로 반복할 때는 캐시가
# 적중해 입력비를 ~90% 아낀다 — 손해는 없음, 캐시 미스여도 원래 내던 토큰).
SYSTEM_PROMPT = """너는 투자자를 위한 경제 캘린더를 만드는 애널리스트다. web_search 도구를 \
사용해서 오늘부터 4주(28일) 이내의 미국(US)과 한국(KR) 주요 경제/통화정책 일정을 \
조사해라. 오늘 날짜는 사용자 메시지에 별도로 주어진다.

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
{
  "events": [
    {"date": "YYYY-MM-DD", "country": "US", "event": "FOMC 금리결정", "importance": "high"}
  ]
}
```"""


# v5.141(사용자 지시, 돈의흐름 restart-loop 사고 재발방지 2번): app.py의
# 7일 staleness+24시간 재시도 스로틀은 "UI 신선도" 정책이라 그대로 app.py에
# 남긴다(제품 판단이지 비용 안전 문제가 아님) — 대신 이 함수 자신은 아주
# 짧은 최소 호출 간격 + 전역 상한(api_call_guard)만 자체 확인해서, 앞으로
# 어떤 새 호출부가 생기든(app.py의 스케줄러/수동 라우트를 거치지 않고
# 직접 이 함수를 부르는 경우 포함) 최소한의 폭주는 막는다.
MIN_CALL_INTERVAL_SEC = 60


def _resolve_guard_dir() -> str:
    candidates = []
    env_dir = os.environ.get("JOURNAL_DIR")
    if env_dir:
        candidates.append(env_dir)
    candidates.append("/data")
    candidates.append(os.path.dirname(__file__))
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            test = os.path.join(d, ".write_test")
            with open(test, "w") as f:
                f.write("ok")
            os.remove(test)
            return d
        except OSError:
            continue
    return os.path.dirname(__file__)


GUARD_STATE_PATH = os.path.join(_resolve_guard_dir(), "macro_calendar_guard_state.json")


def _load_guard() -> dict:
    try:
        with open(GUARD_STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_guard(data: dict):
    try:
        tmp = GUARD_STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, GUARD_STATE_PATH)
    except OSError as e:
        print(f"[macro_calendar] 가드 상태 저장 실패: {e}")


def generate_calendar(today: str) -> tuple[list | None, str | None]:
    """(events, error) 튜플 — 정확히 하나만 채워짐. today: "YYYY-MM-DD"."""
    guard = _load_guard()
    now_ts = datetime.now(KST).timestamp()
    last_ts = guard.get("last_attempt_ts")
    if last_ts and (now_ts - last_ts) < MIN_CALL_INTERVAL_SEC:
        remaining = int(MIN_CALL_INTERVAL_SEC - (now_ts - last_ts))
        return None, f"너무 잦은 호출 — {remaining}초 후 다시 시도(비용 보호)"
    guard["last_attempt_ts"] = now_ts
    _save_guard(guard)

    allowed, alert = api_call_guard.check_and_count("macro_calendar")
    if alert:
        print(f"[macro_calendar] {alert}")
    if not allowed:
        return None, "일일 Claude API 호출 상한 도달 — 차단됨(비용 보호)"

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None, "ANTHROPIC_API_KEY 환경변수 미설정"

    try:
        import anthropic
    except ImportError:
        return None, "anthropic 패키지 미설치 (requirements.txt 확인)"

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=[WEB_SEARCH_TOOL],
            messages=[{"role": "user", "content": f"오늘 날짜는 {today}이다. 이 날짜 기준으로 조사해라."}],
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
