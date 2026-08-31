"""
테마-관련주 매핑 (v5.100, 사용자 지시) — 테마명을 입력받아 Claude API
(web_search 켜서)로 KR 관련주 명단을 생성해 캐싱한다. 테마로테이션 탭의
전제 인프라 — 이 파일 자체는 UI를 만들지 않는다(측정 통과 후 별도).

app.py에 대한 의존성 없음(money_flow.py/money_flow_report.py와 같은
원칙 — app.py → theme_map.py 방향으로만 import). KR 유니버스(ticker->name
dict)는 이 모듈이 직접 안 구하고 호출부(app.py)가 `universe.get_universe
("kr")`을 그대로 인자로 넘겨준다 — 순환 의존 방지 + 이미 fetch된 데이터
재사용(중복 조회 없음).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
MODEL = "claude-sonnet-4-6"   # money_flow_report.py와 동일 — 이미 이 앱에서 검증된 조합
MAX_TOKENS = 6000            # v5.124: 상한 8→25 확대에 맞춰 여유 있게 상향(entry당 reason 포함 25개 fit)
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}
STALE_DAYS = 30                 # 30일 경과 시 재생성 대상
DAILY_GENERATION_LIMIT = 3      # 비용 가드 — 하루 신규 매핑 생성 상한(사용자 지시 7번)

# v5.124 [설계 결정 — 사용자 지시]: 제약·바이오처럼 유니버스 내 후보가
# 50개+인 광의 테마는 8종목 상한이 breadth%를 너무 거칠게 만듦(8종목 =
# 12.5% 단위로만 움직임, 실제로 오늘자 라이프사이클 분석에서 breadth=
# 12.5%=1/8이 나온 게 그 증상). 두 대안(상한 확대 8→25~30 vs 하위테마
# 분할)을 검토해 **상한 확대**를 택함 — 이유: (1) DAILY_GENERATION_LIMIT
# =3/day 예산상 광의 테마 하나를 4개 하위테마로 쪼개면 그 하루 예산을
# 그 테마 하나가 다 써버림(다른 테마 재생성이 막힘), (2) 이번 계기가 된
# JW신약 3중히트 사례 자체가 "제약·바이오 섹터 전체" 단위의 확산/개별
# 이슈 판정이 목적이었지, 하위테마 단위 분리가 필요했던 사례가 아니었음,
# (3) 상한만 올리면 데이터모델·API·UI(테마 1개=엔트리 1개) 전부 무변경—
# 하위테마 분할은 테마 간 연결 개념을 새로 만들어야 해서 복잡도가 큼.
# 좁은 테마는 실제 후보가 8개 미만이면 규칙1(환각 금지)에 따라 Claude가
# 자연히 더 적게 반환하므로, 테마별로 "50개+인지" 사전 판정하는 로직은
# 불필요 — 상한을 균일하게 25로 올리는 것만으로 광의/협의 테마 모두 처리됨.
# v5.125(사용자 지시, API 비용 급증 조사): 테마명 한 줄만 빼면 매 호출
# 100% 동일한 텍스트라 system + cache_control로 분리 — DAILY_GENERATION_
# LIMIT=3/day라 같은 날 여러 테마를 몰아서 생성할 때(자동/수동 모두)
# 두 번째 호출부터 캐시 적중, 입력비 ~90% 절감.
SYSTEM_PROMPT = """너는 한국 주식시장 테마 분석가다. 사용자가 준 테마와 사업적으로
직결된 **한국거래소(코스피/코스닥) 상장사**의 관련주 명단을 만들어라.

규칙:
1. **실재하는 한국 상장사만** — 상장 여부가 불확실하면 넣지 마라. 웹서치로
   실제 종목코드를 확인해라(추측 금지). 실제 관련주가 적은 좁은 테마라면
   억지로 채우지 말고 실재하는 만큼만 반환해라.
2. 최대 25개(테마와 사업적으로 직결된 종목이 그만큼 많을 때만 — 좁은
   테마는 무리해서 채우지 마라), 사업 직결도가 높은 순서로 rank를
   매겨라(1=대장주).
3. **대장주(rank=1)는 이 테마와 가장 직접적으로 연결된 종목**이어야 한다
   (매출/사업 비중이 가장 큰 종목, 시가총액이 아니라 "이 재료와의
   연관성"이 기준). 주의: 이 rank는 정적(생성 시점 1회 고정) 값이며,
   실제 라이프사이클 분석의 일일 대장주/D0 판정은 매일 재계산되는 회전율
   (거래대금÷20일평균) 기준을 따로 쓴다 — 둘이 다른 종목을 가리킬 수 있음.
4. 각 종목마다 왜 관련 있는지 한 줄 근거(reason)를 적어라 — 구체적 사업
   내용 기반으로(예: "2차전지 양극재 국내 1위 생산" 같은 식), "테마
   관련주"처럼 뭉뚱그리지 마라.
5. 종목코드(ticker)는 6자리 숫자만 적어라(.KS/.KQ 접미사 없이). 시장
   구분이 확실하면 market 필드에 "KOSPI" 또는 "KOSDAQ"을 적어라(모르면
   생략 가능 — 호출부가 실제 유니버스와 대조해서 확정한다).

출력은 다른 설명 없이 아래 형식의 JSON 배열 하나만, ```json 코드블록
안에 담아라:

```json
[
  {"ticker": "005930", "name": "삼성전자", "market": "KOSPI", "rank": 1, "reason": "..."},
  ...
]
```
"""


def _resolve_theme_map_dir() -> str:
    """app.py의 _resolve_persistent_path/money_flow.py의 _resolve_money_flow_dir와
    동일한 우선순위(JOURNAL_DIR 환경변수 → /data → 앱 폴더)를 이 모듈
    안에서 독립적으로 재현 — app.py를 import하지 않기 위함."""
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


THEME_MAP_PATH = os.path.join(_resolve_theme_map_dir(), "theme_map.json")


def _load() -> dict:
    if os.path.exists(THEME_MAP_PATH):
        try:
            with open(THEME_MAP_PATH, encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except (ValueError, OSError):
            return {}
    return {}


def _save(data: dict):
    try:
        tmp = THEME_MAP_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, THEME_MAP_PATH)
    except OSError as e:
        print(f"[theme_map] 저장 실패: {e}")


def is_stale(entry: dict) -> bool:
    gen = entry.get("generated_at")
    if not gen:
        return True
    try:
        gen_dt = datetime.strptime(gen, "%Y-%m-%d")
    except ValueError:
        return True
    return (datetime.now(KST).replace(tzinfo=None) - gen_dt).days >= STALE_DAYS


def get(theme_name: str) -> dict | None:
    return _load().get(theme_name)


def list_all() -> dict:
    """{테마명: {generated_at, stock_count, source, stale}} 요약 — 전체 원본이
    아니라 목록용 경량 뷰(API /api/theme_map 용)."""
    data = _load()
    return {
        name: {
            "generated_at": e.get("generated_at"),
            "stock_count": len(e.get("stocks") or []),
            "source": e.get("source"),
            "stale": is_stale(e),
        }
        for name, e in data.items()
    }


def _extract_json_array(text: str):
    blocks = re.findall(r"```json\s*(\[.*?\])\s*```", text, re.DOTALL)
    if not blocks:
        blocks = re.findall(r"(\[[\s\S]*\])", text)   # 코드블록 없이 배열만 낸 경우 방어적 폴백
    for b in reversed(blocks):
        try:
            data = json.loads(b)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            continue
    return None


def _code_from_ticker(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    return digits[:6] if len(digits) >= 6 else digits


def generate_theme_map(theme_name: str, kr_universe: dict) -> dict:
    """theme_name에 대해 Claude(web_search)로 KR 관련주 생성 + 스캐너
    유니버스 대조 검증(환각 방지, 사용자 지시 2번). kr_universe:
    {ticker(.KS/.KQ 포함): name} — universe.get_universe("kr") 그대로
    전달받는다. 반환: {generated_at, stocks:[{ticker,name,rank,reason}],
    source:"claude", removed:[...]} 또는 실패 시 {"error": ...}(예외를
    던지지 않음 — money_flow_report.py와 동일 원칙, 호출부가 항상
    안전하게 처리 가능하게)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"error": "ANTHROPIC_API_KEY 환경변수 미설정"}
    try:
        import anthropic
    except ImportError:
        return {"error": "anthropic 패키지 미설치"}

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=[WEB_SEARCH_TOOL],
            messages=[{"role": "user", "content": f"테마: {theme_name}"}],
        )
    except Exception as e:
        return {"error": f"Claude API 호출 실패: {type(e).__name__}: {e}"}

    try:
        text = "\n".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    except Exception as e:
        return {"error": f"응답 파싱 실패: {type(e).__name__}: {e}"}

    items = _extract_json_array(text)
    if items is None:
        return {"error": "JSON 배열 추출 실패(응답 형식이 예상과 다름)"}

    # ── 환각 방지: 생성된 티커를 스캐너 유니버스와 대조(사용자 지시 2번) ──
    code_to_ticker = {}
    for t in kr_universe:
        code = t.split(".")[0]
        code_to_ticker[code] = t

    stocks, removed, seen = [], [], set()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        raw_ticker = str(raw.get("ticker") or "")
        name = str(raw.get("name") or "")
        code = _code_from_ticker(raw_ticker)
        ticker = code_to_ticker.get(code)
        if ticker is None:
            removed.append({"raw_ticker": raw_ticker, "name": name, "reason": "스캐너 유니버스에 없음(환각 또는 유니버스 밖 종목)"})
            continue
        if ticker in seen:
            continue
        seen.add(ticker)
        rank = raw.get("rank")
        stocks.append({
            "ticker": ticker, "name": kr_universe.get(ticker, name),
            "rank": rank if isinstance(rank, int) else None,
            "reason": raw.get("reason") or "",
        })
    stocks.sort(key=lambda s: s["rank"] if s["rank"] is not None else 999)

    if removed:
        print(f"[theme_map] {theme_name}: 유니버스 불일치로 제거된 종목 {len(removed)}건 — "
              f"{[r['raw_ticker'] or r['name'] for r in removed]}")

    return {
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d"),
        "stocks": stocks, "source": "claude", "removed": removed,
    }


def save_theme_map(theme_name: str, entry: dict):
    data = _load()
    data[theme_name] = entry
    _save(data)


def _today_generation_count(data: dict) -> int:
    today = datetime.now(KST).strftime("%Y-%m-%d")
    return sum(1 for e in data.values() if isinstance(e, dict) and e.get("generated_at") == today)


def today_generation_count() -> int:
    """공개 래퍼 — 호출부(app.py)가 비용 가드 체크용으로 씀(사용자 지시
    7번, 수동 생성 API도 동일 한도 공유)."""
    return _today_generation_count(_load())


def maybe_auto_generate(candidate_themes: list[str], kr_universe: dict) -> list[str]:
    """money_flow 일일 잡에서 호출(사용자 지시 4번) — 매핑이 없거나 30일
    경과한 테마 중, 하루 신규 생성 한도(DAILY_GENERATION_LIMIT) 안에서만
    실제로 생성한다. 반환: 이번 호출에서 실제로 생성된 테마명 리스트."""
    data = _load()
    count = _today_generation_count(data)
    generated = []
    for theme in candidate_themes:
        if count >= DAILY_GENERATION_LIMIT:
            print(f"[theme_map] 일일 생성 한도({DAILY_GENERATION_LIMIT}건) 도달 — 나머지 스킵")
            break
        existing = data.get(theme)
        if existing and not is_stale(existing):
            continue
        entry = generate_theme_map(theme, kr_universe)
        if entry.get("error"):
            print(f"[theme_map] {theme} 자동 생성 실패: {entry['error']}")
            continue
        data[theme] = entry
        count += 1
        generated.append(theme)
        print(f"[theme_map] {theme} 자동 생성 완료 — {len(entry.get('stocks') or [])}종목")
    if generated:
        _save(data)
    return generated
