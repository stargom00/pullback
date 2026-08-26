"""
돈의 흐름 데일리 리포트 — 2단계 해석 (Claude API, 2026-08-26 사용자 지시).
`money_flow.py`가 계산한 1단계 JSON을 입력으로
`docs/money_flow_prompt.md` 프롬프트를 그대로 붙여 Claude API를 호출해
마크다운 리포트를 만든다. app.py에 대한 의존성 없음(app.py →
money_flow_report.py 방향으로만 import) — money_flow.py와 같은 원칙.

실패(키 없음/패키지 미설치/네트워크/API 에러)해도 예외를 밖으로 던지지
않고 (None, 에러메시지)를 반환 — 호출부(app.py)가 1단계 JSON만으로
폴백 표시할 수 있게 하기 위함(사용자 지시: "호출 실패 시 1단계 계산
결과만이라도 표시").
"""
from __future__ import annotations

import json
import os
import re

MODEL = "claude-sonnet-4-6"
PROMPT_PATH = os.path.join(os.path.dirname(__file__), "docs", "money_flow_prompt.md")
# v5.87: 8000 → 16000. 11개 섹션(뉴스 검증·데이터 검증 포함) + 웹서치 결과
# 블록까지 합치면 8000으로는 항상 9번 섹션 부근에서 잘려 10번(최종 한 문장)
# ·11번(데이터 검증)·12번(기계판독 JSON)이 한 번도 생성된 적이 없었음
# (실측: KR/US 리포트 둘 다 8000에서 중간에 끊김). 텔레그램 봇이 최종 한
# 문장을 읽어야 해서(사용자 지시) 완주가 필수.
MAX_TOKENS = 16000
# 서버사이드 웹서치 툴 — Claude가 필요하다고 판단할 때만 자동 호출(8. 뉴스
# 검증 섹션용). 호출 횟수 상한으로 비용 통제.
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}


def _load_prompt() -> str | None:
    try:
        with open(PROMPT_PATH, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def generate_report(snapshot: dict) -> tuple[str | None, str | None]:
    """(markdown, error) 튜플 — 정확히 하나만 채워짐. snapshot은
    money_flow.compute_snapshot()/run_daily()의 반환값 그대로."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None, "ANTHROPIC_API_KEY 환경변수 미설정"

    prompt = _load_prompt()
    if prompt is None:
        return None, "docs/money_flow_prompt.md 를 읽을 수 없음"

    try:
        import anthropic
    except ImportError:
        return None, "anthropic 패키지 미설치 (requirements.txt 확인)"

    input_json = json.dumps(snapshot, ensure_ascii=False, indent=2, default=str)
    user_content = f"{prompt}\n```json\n{input_json}\n```"

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            tools=[WEB_SEARCH_TOOL],
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception as e:
        return None, f"Claude API 호출 실패: {type(e).__name__}: {e}"

    try:
        text_parts = [block.text for block in resp.content if getattr(block, "type", None) == "text"]
    except Exception as e:
        return None, f"응답 파싱 실패: {type(e).__name__}: {e}"

    markdown = "\n\n".join(p for p in text_parts if p).strip()
    if not markdown:
        return None, "API 응답에 텍스트 블록이 없음(웹서치만 반환됐거나 빈 응답)"
    return markdown, None


def extract_summary(markdown: str) -> dict | None:
    """리포트 맨 끝(섹션 12, docs/money_flow_prompt.md)의 기계판독용 JSON
    블록을 파싱. 블록이 없거나(구형 리포트·생성 중 잘림) 필수 키가 없으면
    None — 호출부(app.py)가 폴백 처리할 수 있게 예외를 던지지 않는다."""
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", markdown, re.DOTALL)
    if not blocks:
        return None
    try:
        data = json.loads(blocks[-1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if not {"strong_themes", "weak_themes", "final_sentence"} <= data.keys():
        return None
    return data
