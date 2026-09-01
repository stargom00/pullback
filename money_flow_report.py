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
from datetime import datetime, timedelta, timezone

import api_call_guard

KST = timezone(timedelta(hours=9))
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


# v5.125(사용자 지시, API 비용 급증 조사) — 입력 다이어트. Claude가 실제로
# 쓰는 값(종목당 분류·등락률·거래대금 규모)은 그대로 두고 중복/과잉정밀도만
# 뺀다: volume(turnover=종가×거래량이라 이미 포함), prev_rank(rank_change로
# 대체 가능한 파생값) 제거 + turnover/mcap_approx 소수점 반올림. 로컬
# 시뮬레이션(scripts/measurements 없이 직접 확인, 100종목 기준) 실측
# 40% 문자수 감소 — indent=2(원본 저장용 포맷) 대신 컴팩트 JSON으로 보내는
# 효과가 그 중 대부분(공백 제거만으로 32%p). 디스크 저장용 원본 snapshot은
# 건드리지 않음(호출부가 그대로 저장) — 이 함수는 API 입력 사본만 만든다.
# v5.141(사용자 지시, 돈의흐름 restart-loop 사고 근본수정 2번): "오늘 이미
# 생성했다"/"쿨다운 중이다" 상태를 이 함수 자신이 파일(영구 볼륨)에 기록·
# 확인한다 — macro_calendar.py가 이미 쓰던 패턴(파일에 generated_at 기록,
# staleness 판정을 재시작과 무관하게 재현)을 이식. app.py의 스케줄러 경로든
# 수동 POST /api/moneyflow/{market}/run 경로든, 다른 어떤 호출부가 생기든
# generate_report()를 거치는 한 이 게이트를 못 우회한다 — v5.139/v5.140의
# app.py 레벨 마커는 "1단계 무료 계산을 4분마다 반복하지 않기 위한" 별개
# 목적(비용 안전과 무관)으로 그대로 유지, 유료 API 호출 안전은 여기가 유일한
# 진원지.
MANUAL_COOLDOWN_SEC = 120   # v5.125 도입값 그대로 재사용(app.py의 옛 상수와 동일)


def _resolve_state_dir() -> str:
    """theme_map.py의 _resolve_theme_map_dir와 동일 우선순위 — app.py 미의존
    원칙 유지를 위해 이 모듈 안에서 독립 재현."""
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


STATE_PATH = os.path.join(_resolve_state_dir(), "money_flow_report_state.json")


def _load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(data: dict):
    try:
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, STATE_PATH)
    except OSError as e:
        print(f"[money_flow_report] 상태 저장 실패: {e}")


def _diet_snapshot(snapshot: dict) -> dict:
    def _diet_row(r: dict) -> dict:
        d = {k: v for k, v in r.items() if k not in ("volume", "prev_rank")}
        if d.get("turnover") is not None:
            d["turnover"] = round(d["turnover"])
        if d.get("mcap_approx") is not None:
            d["mcap_approx"] = round(d["mcap_approx"])
        return d
    return {**snapshot, "top": [_diet_row(r) for r in (snapshot.get("top") or [])]}


def generate_report(snapshot: dict, market: str, daykey: str) -> tuple[str | None, str | None]:
    """(markdown, error) 튜플 — 정확히 하나만 채워짐. snapshot은
    money_flow.compute_snapshot()/run_daily()의 반환값 그대로. market/daykey는
    v5.141부터 필수 인자(사용자 지시 2번) — 이 함수 자신이 "오늘 이미
    생성했다"/"쿨다운 중" 게이트를 파일로 판정하려면 호출부(스케줄러든 수동
    HTTP 라우트든)가 어느 시장·어느 거래일인지 알려줘야 한다."""
    state = _load_state()
    m = state.setdefault(market, {})
    if m.get("last_success_daykey") == daykey:
        return None, f"{market} {daykey} 오늘 이미 생성 완료 — 재생성 스킵(비용 보호)"
    now_ts = datetime.now(KST).timestamp()
    last_attempt = m.get("last_attempt_ts")
    if last_attempt and (now_ts - last_attempt) < MANUAL_COOLDOWN_SEC:
        remaining = int(MANUAL_COOLDOWN_SEC - (now_ts - last_attempt))
        return None, f"{market} 너무 잦은 재실행 — {remaining}초 후 다시 시도(비용 보호)"
    m["last_attempt_ts"] = now_ts
    _save_state(state)

    allowed, alert = api_call_guard.check_and_count("money_flow_report")
    if alert:
        print(f"[money_flow_report] {alert}")
    if not allowed:
        return None, "일일 Claude API 호출 상한 도달 — 차단됨(비용 보호)"

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

    # v5.125: 고정 프롬프트(~2000토큰)를 system으로 분리 + cache_control —
    # 하루 KR/US 각 1회 자동 실행 + 수동 재실행(🔄) 시 짧은 간격 반복
    # 호출이면 캐시 적중해 이 부분 입력비가 ~90% 절감된다(캐시 미스여도
    # 손해 없음 — 어차피 매번 내던 토큰).
    input_json = json.dumps(_diet_snapshot(snapshot), ensure_ascii=False,
                             separators=(",", ":"), default=str)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}],
            tools=[WEB_SEARCH_TOOL],
            messages=[{"role": "user", "content": f"```json\n{input_json}\n```"}],
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

    state = _load_state()
    state.setdefault(market, {})["last_success_daykey"] = daykey
    _save_state(state)
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
