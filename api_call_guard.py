"""
Claude API 호출 전역 일일 상한 (v5.141, 사용자 지시 — 돈의흐름 restart-loop
비용 사고의 근본 재발방지 4번). money_flow_report.py/theme_map.py/
macro_calendar.py 중 어느 모듈이 호출하든 이 함수를 거치면 하루 총 호출
횟수가 20회를 넘을 수 없다 — 개별 모듈의 자체 한도(theme_map의
AUTO/MANUAL_DAILY_LIMIT 등)가 전부 무력화되는 미지의 버그가 또 나와도
이 선은 못 넘게 하는 최후 방어선. 세 모듈이 각자 복붙하지 않고 이 모듈
하나를 같이 import하는 구조 — 상한 숫자가 세 곳에 흩어져 어긋나는 걸 방지.

app.py에 대한 의존성 없음(money_flow.py/theme_map.py와 같은 원칙) — 상태는
자체 경로 해석(JOURNAL_DIR→/data→앱폴더)으로 별도 파일에 영속, 재시작에도
카운트가 유지된다(이번 사고의 교훈 — 메모리 카운터는 재시작마다 리셋되어
무의미해짐).

이 레포엔 텔레그램 발송 코드가 없다(얼마냐봇은 별도 레포, money_flow.py류와
같은 원칙) — 여기선 경고/차단 메시지를 pending_alert 필드로 노출만 하고,
실제 발송은 얼마냐봇이 /api/apiguard/status를 폴링해서 하는 걸 전제로 한다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

DAILY_LIMIT = 20         # 사용자 지시: 일일 총 호출 상한
WARN_THRESHOLD = 14      # 상한의 70% — 경고 알림 기준
WARN_MESSAGE_TEMPLATE = "⚠️ Claude API 호출 {count}/{limit} — 평소보다 많음, 확인 필요"
BLOCK_MESSAGE = "🛑 일일 상한 도달 — Claude API 호출 차단됨. 원인 확인 필요"


def _resolve_guard_dir() -> str:
    """theme_map.py의 _resolve_theme_map_dir와 동일 우선순위(JOURNAL_DIR→
    /data→앱폴더)를 이 모듈 안에서 독립적으로 재현 — app.py를 import하지
    않기 위함(같은 원칙이 이미 money_flow.py/theme_map.py에 있음)."""
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


STATE_PATH = os.path.join(_resolve_guard_dir(), "api_call_guard_state.json")


def _today() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")  # 일자 기준 KST 자정(사용자 지시)


def _load() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("date") == _today():
            return data
    except (OSError, ValueError):
        pass
    return {"date": _today(), "count": 0, "warned": False, "blocked_notified": False, "pending_alert": None}


def _save(data: dict):
    try:
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, STATE_PATH)
    except OSError as e:
        print(f"[api_call_guard] 저장 실패: {e}")


def check_and_count(caller: str) -> tuple[bool, str | None]:
    """유료 API 호출 직전에 반드시 호출할 것. (allowed, alert_message) 반환.
    allowed=False면 이번 호출을 하면 안 된다(상한 도달). alert_message가
    채워지면(경고 진입 또는 최초 차단 시 한 번만) 호출부가 로그로 남기고,
    /api/apiguard/status로 노출된 pending_alert를 얼마냐봇이 폴링해 텔레그램
    으로 전달할 수 있게 한다."""
    data = _load()
    if data["count"] >= DAILY_LIMIT:
        if not data["blocked_notified"]:
            data["blocked_notified"] = True
            data["pending_alert"] = BLOCK_MESSAGE
            _save(data)
            print(f"[api_call_guard] {caller}: 일일 상한({DAILY_LIMIT}) 도달 — 차단")
            return False, BLOCK_MESSAGE
        return False, None
    data["count"] += 1
    msg = None
    if data["count"] >= WARN_THRESHOLD and not data["warned"]:
        data["warned"] = True
        msg = WARN_MESSAGE_TEMPLATE.format(count=data["count"], limit=DAILY_LIMIT)
        data["pending_alert"] = msg
        print(f"[api_call_guard] {caller}: {msg}")
    _save(data)
    return True, msg


def status() -> dict:
    """GET /api/apiguard/status 노출용 — 얼마냐봇 폴링 대상."""
    data = _load()
    return {
        "date": data["date"],
        "count": data["count"],
        "limit": DAILY_LIMIT,
        "warn_threshold": WARN_THRESHOLD,
        "warned": data["warned"],
        "blocked": data["count"] >= DAILY_LIMIT,
        "pending_alert": data.get("pending_alert"),
    }
