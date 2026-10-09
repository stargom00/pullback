"""종목 카드 "🗞 왜?" — 최근 뉴스·공시로 움직임 원인을 Claude가 3줄 요약 (v5.346, 사용자 지시 2026-10-10).

사용자 지시 요지: "누를 때만 생성, 캐시 우선, 매수·매도 의견 없음, 추측 금지." 출력 3줄(한국어):
  1. 무슨 일 — 원인 뉴스/공시 한 줄, 날짜 포함
  2. 성격 — 일회성 이벤트 / 실적·구조 변화 / 희석(유증·CB·ATM) 중 하나
  3. 근거(출처·날짜) — 뉴스에서 원인을 못 찾으면 "뉴스상 원인 불명". US는 끝에 "US 공시 미확인"(사용자 결정).

app.py 미의존(money_flow_report.py·theme_map.py와 같은 원칙) — 일봉은 호출부가 넘긴다.
유료 API 가드(CLAUDE.md "유료 API 가드는 함수 레벨·영속"): 이 모듈의 `get_summary()`가 직접
  ① 같은 종목·같은 KST 날짜 캐시(파일 영속) — 재클릭은 API 재호출 없음
  ② 강제 갱신 쿨다운 120초(그 종목 마지막 생성 시각, 파일 영속)
  ③ 종목별 진행 중 잠금(프로세스 안)
  ④ `api_call_guard.check_and_count("news_summary")` — 별도 버킷 NEWS_SUMMARY_DAILY_LIMIT(기본 50, 사용자 지시)
를 모두 거친다. ANTHROPIC_API_KEY는 환경변수로만 읽고 어디에도 쓰지 않는다(로그·캐시·응답에 키 없음 — 테스트가 고정).

데이터 소스(0단계 확인, 2026-10-10 실측): KR 뉴스 `m.stock.naver.com/api/news/stock/{code}`, KR 공시
`m.stock.naver.com/api/stock/{code}/disclosure`(둘 다 JSON), US 뉴스 Yahoo Finance RSS(XML). US 공시 소스는 없다.
수집 실패(`news_error`)와 요약 실패(`summary_error`)는 따로 돌려준다 — 예외를 삼키지 않고 메시지를 그대로 카드에 보인다.
"""
from __future__ import annotations

import html
import json
import os
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

import api_call_guard

KST = timezone(timedelta(hours=9))
CALLER = "news_summary"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"     # 사용자 지시: 환경변수 CLAUDE_MODEL_NEWS, 기본값
MAX_TOKENS = 300                                # 사용자 지시
NEWS_DAYS = 7                                   # "최근 7일 해당 종목 뉴스"
NEWS_MAX = 15                                   # "최대 15건"
DISC_MAX = 10                                   # "KR 공시 최근 7일 최대 10건"
BAR_DAYS = 5                                    # "최근 5일 일봉 등락률·거래대금 배수"
TURNOVER_BASE = 20                              # 거래대금 배수 기준 = 그날 전 20거래일 평균(AI 판단 — 지시에 기준 기간 없음)
FORCE_COOLDOWN_SEC = 120                        # 사용자 지시 "강제 갱신 쿨다운 120초"
CACHE_KEEP_DAYS = 14                            # 캐시 보관(AI 판단 — 파일이 무한히 커지지 않게)
BODY_CHARS = 200                                # 기사 요약 길이 자르기(입력 토큰 통제, AI 판단)
CATEGORIES = ("일회성 이벤트", "실적·구조 변화", "희석")
UNKNOWN = "뉴스상 원인 불명"
US_NO_DISC = "US 공시 미확인"
HTTP_TIMEOUT = 10
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}

SYSTEM_PROMPT = f"""너는 주식 종목의 최근 움직임 원인을 뉴스·공시에서만 찾아 정리하는 비서다.
규칙:
- 주어진 뉴스·공시·일봉 외의 지식이나 추측을 쓰지 마라. 근거가 없으면 없다고 써라.
- 매수·매도·보유 의견, 전망, 목표가를 쓰지 마라.
- 정확히 3줄, 한국어, 각 줄은 아래 형식으로 시작한다. 다른 말은 쓰지 마라.
1. 무슨 일: (움직임의 원인 뉴스/공시 한 줄, 날짜 MM-DD 포함. 원인을 못 찾으면 "{UNKNOWN}")
2. 성격: ({' / '.join(CATEGORIES)} 중 정확히 하나. 희석이면 괄호로 유증·CB·BW·ATM 등 종류. 원인을 못 찾았으면 "판단 불가")
3. 근거: (근거 기사의 언론사·날짜 또는 공시 제목·날짜. 원인을 못 찾았으면 "{UNKNOWN}")"""


class NewsFetchError(Exception):
    """뉴스·공시 수집 실패 — 요약 실패와 구분해 카드에 보인다."""


# ── 저장 위치(money_flow_report._resolve_state_dir와 같은 우선순위: JOURNAL_DIR → /data → 앱 폴더) ──
def _resolve_state_dir() -> str:
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


CACHE_PATH = os.path.join(_resolve_state_dir(), "news_summary_cache.json")
_FILE_LOCK = threading.Lock()
_KEY_LOCKS: dict = {}
_KEY_LOCKS_GUARD = threading.Lock()


def _now() -> datetime:
    return datetime.now(KST)


def _today() -> str:
    return _now().strftime("%Y-%m-%d")


def cache_key(ticker_norm: str, day: str) -> str:
    """캐시 키 = 종목 + KST 날짜(사용자 지시 "종목+날짜(KST) 단위")."""
    return f"{ticker_norm}|{day}"


def normalize(ticker: str, market: str | None) -> tuple[str, str]:
    """(정규화 키, 시장). KR은 6자리 코드(접미사 무관 — naver API가 코드만 받는다), US는 대문자 심볼."""
    t = (ticker or "").strip().upper()
    mkt = (market or "").strip().upper() or ("KR" if t.endswith((".KS", ".KQ")) or (len(t.split(".")[0]) == 6 and t.split(".")[0][:1].isdigit()) else "US")
    if mkt == "KR":
        return "KR:" + t.split(".")[0], "KR"
    return "US:" + t, "US"


def _load_cache() -> dict:
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(d: dict):
    cut = (_now() - timedelta(days=CACHE_KEEP_DAYS)).strftime("%Y-%m-%d")
    entries = {k: v for k, v in (d.get("entries") or {}).items() if k.split("|")[-1] >= cut}
    d = {**d, "entries": entries}
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, CACHE_PATH)


# ── 수집 ────────────────────────────────────────────────────────────
def _clean(s: str) -> str:
    """HTML 엔티티 풀기(naver 본문 요약이 "&lt;기자…"처럼 이스케이프돼 온다 — 2026-10-10 실측) + 공백 정리."""
    return " ".join(html.unescape(str(s)).split())


def _get_json(url, params=None):
    try:
        r = requests.get(url, params=params, headers=_HEADERS, timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        raise NewsFetchError(f"{url} 요청 실패: {type(e).__name__}: {e}") from e
    if r.status_code != 200:
        raise NewsFetchError(f"{url} HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError as e:
        raise NewsFetchError(f"{url} JSON 아님(소스 개편 의심)") from e


def fetch_kr_news(code: str, now: datetime | None = None) -> list:
    """naver 종목 뉴스 — 최근 NEWS_DAYS일, 최신순 최대 NEWS_MAX건. [{date, source, title, body}]."""
    now = now or _now()
    cut = now - timedelta(days=NEWS_DAYS)
    out = []
    for page in (1, 2, 3):
        data = _get_json(f"https://m.stock.naver.com/api/news/stock/{code}", {"pageSize": 20, "page": page})
        if not isinstance(data, list):
            raise NewsFetchError("naver 뉴스 응답 형식이 목록이 아님(소스 개편 의심)")
        items = [it for blk in data for it in (blk.get("items") or [])]
        if not items:
            break
        older = False
        for it in items:
            try:
                dt = datetime.strptime(str(it["datetime"])[:12], "%Y%m%d%H%M").replace(tzinfo=KST)
            except (KeyError, ValueError) as e:
                raise NewsFetchError(f"naver 뉴스 날짜 형식 이상: {it.get('datetime')!r}") from e
            if dt < cut:
                older = True
                continue
            out.append({"date": dt.strftime("%Y-%m-%d %H:%M"), "source": it.get("officeName") or "",
                        "title": _clean(it.get("titleFull") or it.get("title") or ""), "body": _clean(it.get("body") or "")[:BODY_CHARS]})
        if older or len(out) >= NEWS_MAX:
            break
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:NEWS_MAX]


def fetch_kr_disclosures(code: str, now: datetime | None = None) -> list:
    """naver 종목 공시 — 최근 NEWS_DAYS일, 최신순 최대 DISC_MAX건. [{date, title}]."""
    now = now or _now()
    cut = now - timedelta(days=NEWS_DAYS)
    data = _get_json(f"https://m.stock.naver.com/api/stock/{code}/disclosure", {"pageSize": 20, "page": 1})
    if not isinstance(data, list):
        raise NewsFetchError("naver 공시 응답 형식이 목록이 아님(소스 개편 의심)")
    out = []
    for it in data:
        try:
            dt = datetime.fromisoformat(str(it["datetime"])[:19]).replace(tzinfo=KST)
        except (KeyError, ValueError) as e:
            raise NewsFetchError(f"naver 공시 날짜 형식 이상: {it.get('datetime')!r}") from e
        if dt >= cut:
            out.append({"date": dt.strftime("%Y-%m-%d %H:%M"), "title": _clean(it.get("title") or "")})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:DISC_MAX]


def fetch_us_news(symbol: str, now: datetime | None = None) -> list:
    """Yahoo Finance 종목 RSS — 최근 NEWS_DAYS일, 최신순 최대 NEWS_MAX건(날짜는 KST로)."""
    now = now or _now()
    cut = now - timedelta(days=NEWS_DAYS)
    url = "https://feeds.finance.yahoo.com/rss/2.0/headline"
    try:
        r = requests.get(url, params={"s": symbol, "region": "US", "lang": "en-US"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        raise NewsFetchError(f"Yahoo RSS 요청 실패: {type(e).__name__}: {e}") from e
    if r.status_code != 200:
        raise NewsFetchError(f"Yahoo RSS HTTP {r.status_code}")
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError as e:
        raise NewsFetchError("Yahoo RSS XML 파싱 실패(소스 개편 의심)") from e
    out = []
    for it in root.iter("item"):
        try:
            dt = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(KST)
        except (TypeError, ValueError) as e:
            raise NewsFetchError(f"Yahoo RSS 날짜 형식 이상: {it.findtext('pubDate')!r}") from e
        if dt >= cut:
            out.append({"date": dt.strftime("%Y-%m-%d %H:%M"), "source": "Yahoo Finance",
                        "title": _clean(it.findtext("title") or ""), "body": _clean(it.findtext("description") or "")[:BODY_CHARS]})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:NEWS_MAX]


def price_rows(df) -> list:
    """최근 BAR_DAYS일: 날짜·종가·등락률(%)·거래대금 배수(그날 거래대금 ÷ 그 전 TURNOVER_BASE거래일 평균). df 없거나 짧으면 있는 만큼."""
    if df is None or len(df) < 2:
        return []
    c = df["Close"].astype(float)
    tv = c * df["Volume"].astype(float)
    out = []
    for i in range(max(1, len(df) - BAR_DAYS), len(df)):
        base = tv.iloc[max(0, i - TURNOVER_BASE):i]
        mult = float(tv.iloc[i] / base.mean()) if len(base) and base.mean() > 0 else None
        out.append({"date": str(df.index[i].date()), "close": float(c.iloc[i]),
                    "chg_pct": round((float(c.iloc[i]) / float(c.iloc[i - 1]) - 1) * 100, 2),
                    "turnover_mult": round(mult, 2) if mult is not None else None})
    return out


def build_user_prompt(name: str, ticker_norm: str, market: str, bars: list, news: list, discs: list) -> str:
    lines = [f"종목: {name or ''} ({ticker_norm.split(':', 1)[1]}, {market})", "", "[최근 5거래일 일봉]"]
    lines += [f"- {b['date']} 종가 {b['close']:g} 등락 {b['chg_pct']:+.2f}% 거래대금 {b['turnover_mult']}배(직전 {TURNOVER_BASE}일 평균 대비)"
              for b in bars] or ["- (일봉 없음)"]
    lines += ["", f"[최근 {NEWS_DAYS}일 뉴스 {len(news)}건]"]
    lines += [f"- {n['date']} [{n['source']}] {n['title']} — {n['body']}" for n in news] or ["- (없음)"]
    if market == "KR":
        lines += ["", f"[최근 {NEWS_DAYS}일 공시 {len(discs)}건]"]
        lines += [f"- {d['date']} {d['title']}" for d in discs] or ["- (없음)"]
    else:
        lines += ["", "[공시] US 공시는 입력에 없음"]
    return "\n".join(lines)


def _call_model(system: str, user: str, model: str, api_key: str) -> tuple[str, str, int, int]:
    """Anthropic 호출 — (text, model, input_tokens, output_tokens). 테스트는 이 함수를 바꿔 끼운다."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(model=model, max_tokens=MAX_TOKENS, system=system,
                                  messages=[{"role": "user", "content": user}])
    text = "".join(getattr(b, "text", "") for b in resp.content)
    return text, resp.model, int(resp.usage.input_tokens), int(resp.usage.output_tokens)


def parse_lines(text: str, market: str) -> list:
    """3줄 형식 검사 — 아니면 ValueError(요약 실패로 보인다). US는 3번째 줄 끝에 "US 공시 미확인"을 붙인다."""
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    if len(lines) != 3 or not all(lines[i].startswith(f"{i + 1}.") for i in range(3)):
        raise ValueError("응답이 3줄 형식이 아님")
    if not (any(c in lines[1] for c in CATEGORIES) or "판단 불가" in lines[1]):
        raise ValueError("성격 줄에 정해진 분류가 없음")
    if market == "US" and US_NO_DISC not in lines[2]:
        lines[2] = f"{lines[2]} · {US_NO_DISC}"
    return lines


def _key_lock(key: str) -> threading.Lock:
    with _KEY_LOCKS_GUARD:
        return _KEY_LOCKS.setdefault(key, threading.Lock())


def get_summary(ticker: str, market: str | None, name: str = "", bars: list | None = None, force: bool = False) -> dict:
    """버튼 한 번. 반환 dict의 status:
    cached · ok · no_news(뉴스·공시 0건, API 미호출) · no_key · limit · cooldown · in_progress · news_error · summary_error."""
    tnorm, mkt = normalize(ticker, market)
    day = _today()
    key = cache_key(tnorm, day)
    base = {"ticker": ticker, "ticker_norm": tnorm, "market": mkt, "date": day}
    with _FILE_LOCK:
        cache = _load_cache()
    ent = (cache.get("entries") or {}).get(key)
    if ent and not force:
        return {**base, **ent, "status": "cached"}
    if force and ent:
        age = time.time() - float(ent.get("generated_ts") or 0)
        if age < FORCE_COOLDOWN_SEC:
            return {**base, **ent, "status": "cooldown", "retry_after_sec": int(FORCE_COOLDOWN_SEC - age) + 1}
    lock = _key_lock(tnorm)
    if not lock.acquire(blocking=False):
        return {**base, "status": "in_progress", "message": "이미 요약 중"}
    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY") or ""
        if not api_key:
            return {**base, "status": "no_key", "message": "API 키 미설정"}
        code = tnorm.split(":", 1)[1]
        try:
            if mkt == "KR":
                news, discs = fetch_kr_news(code), fetch_kr_disclosures(code)
            else:
                news, discs = fetch_us_news(code), []
        except NewsFetchError as e:
            print(f"[news_summary] {tnorm} 뉴스 수집 실패: {e}")
            return {**base, "status": "news_error", "message": f"뉴스 수집 실패: {e}"}
        bars = bars or []
        entry = {"news_count": len(news), "disclosure_count": len(discs), "bars": bars,
                 "generated_at": _now().strftime("%Y-%m-%d %H:%M:%S"), "generated_ts": time.time()}
        if not news and not discs:
            lines = [f"1. 무슨 일: 최근 {NEWS_DAYS}일 뉴스·공시 없음 — {UNKNOWN}", "2. 성격: 판단 불가", f"3. 근거: {UNKNOWN}"]
            if mkt == "US":
                lines[2] += f" · {US_NO_DISC}"
            entry.update({"lines": lines, "model": None, "tokens_in": 0, "tokens_out": 0, "kind": "no_news"})
            _store(key, entry)
            print(f"[news_summary] {tnorm} 뉴스·공시 0건 — API 미호출")
            return {**base, **entry, "status": "no_news"}
        allowed, _ = api_call_guard.check_and_count(CALLER)
        if not allowed:
            return {**base, "status": "limit", "message": "오늘 한도 초과",
                    "limit": api_call_guard.bucket_limit(CALLER)}
        model = os.environ.get("CLAUDE_MODEL_NEWS") or DEFAULT_MODEL
        user = build_user_prompt(name, tnorm, mkt, bars, news, discs)
        try:
            text, used_model, tin, tout = _call_model(SYSTEM_PROMPT, user, model, api_key)
        except Exception as e:      # 예외를 삼키지 않는다 — 종류와 메시지를 그대로 카드에 보인다(키는 메시지에 없다)
            print(f"[news_summary] {tnorm} 요약 실패: {type(e).__name__}: {e}")
            return {**base, "status": "summary_error", "message": f"요약 실패: {type(e).__name__}: {e}"}
        try:
            lines = parse_lines(text, mkt)
        except ValueError as e:
            print(f"[news_summary] {tnorm} 요약 형식 오류: {e} (in {tin} / out {tout})")
            return {**base, "status": "summary_error", "message": f"요약 실패: {e}", "raw": text,
                    "model": used_model, "tokens_in": tin, "tokens_out": tout}
        entry.update({"lines": lines, "model": used_model, "tokens_in": tin, "tokens_out": tout, "kind": "summary"})
        _store(key, entry)
        print(f"[news_summary] {tnorm} 요약 완료 model={used_model} in={tin} out={tout} news={len(news)} disc={len(discs)}")
        return {**base, **entry, "status": "ok"}
    finally:
        lock.release()


def _store(key: str, entry: dict):
    with _FILE_LOCK:
        cache = _load_cache()
        cache.setdefault("entries", {})[key] = entry
        _save_cache(cache)
