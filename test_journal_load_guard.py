"""v5.299 — 일지 로드 실패 시 localStorage 이전 경로·서버 쓰기 차단.

[재현 확정] 서버 일지 60건 + localStorage 옛 일지 3건 + GET /api/journal 502
"upstream error" → 예전 코드는 옛 3건을 POST해 서버 일지가 3건이 됐다(서버 병합
규칙상 배열에 없는 오래된 레코드 = 삭제). production의 loadJournalFromServer ·
setJournal · apiJson 원문을 그대로 추출해 가짜 fetch/localStorage로 실행한다.

사보타주 확인(2026-09-30): 이전 발동 조건을 v5.298 것(`(!serverData ||
serverData.length === 0) && localData.length > 0`, 실패는 빈 배열)으로 되돌리면
test_502_text_never_migrates FAIL — 원복. v5.300에서 이전 기능 자체를 제거.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

TEXT = (Path(__file__).resolve().parent / "static" / "index.html").read_text(encoding="utf-8")
OLD = [{"id": 9001, "ticker": "005930.KS", "name": "옛기록"}]


def _fn(name):
    i = TEXT.index(f"function {name}(")
    start = i - 6 if TEXT[i - 6:i] == "async " else i
    b = TEXT.index("{", i)
    d = 0
    for k in range(b, len(TEXT)):
        d += {"{": 1, "}": -1}.get(TEXT[k], 0)
        if d == 0:
            return TEXT[start:k + 1]
    raise AssertionError(name)


def _run(get_status, get_body, local=OLD, then_save=False):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    js = "\n".join([
        "let journalCache = []; let mode = 'journal'; let _calendarData = null;",
        "const JKEY = 'pullback_journal_v1';",
        f"const _store = {{ [JKEY]: {json.dumps(json.dumps(local))} }};",
        "const localStorage = { getItem: k => (k in _store ? _store[k] : null) };",
        "const posts = []; const alerts = []; let tracked = 0;",
        "function alert(m) { alerts.push(m); }",
        "function _normalize(a) { return a; }",
        "function updateTracking() { tracked++; }",
        "function renderJournal() {} function renderCalendar() {}",
        "const document = { getElementById: () => ({ style: { display: 'none' } }) };",
        "let _journalSaveChain = Promise.resolve(); let _journalSynced = new Map();",
        f"""async function fetch(url, opts) {{
  if (opts && opts.method === 'POST') {{ posts.push(url); return {{ ok: true, status: 200, text: async () => '{{"ok":true}}' }}; }}
  return {{ ok: {json.dumps(200 <= get_status < 300)}, status: {get_status}, text: async () => {json.dumps(get_body)} }};
}}""",
        "async function _saveJournalToServer() { posts.push('/api/journal(save)'); return true; }",
        _fn("_apiError"), _fn("apiParse"), _fn("apiJson"),
        "let _journalLoadError = null;",
        _fn("_jrKey"), _fn("_journalMarkSynced"),
        _fn("loadJournalFromServer"), _fn("setJournal"),
        f"""(async () => {{
  await loadJournalFromServer();
  {"await setJournal([{id: 1}]);" if then_save else ""}
  console.log(JSON.stringify({{ posts, alerts, tracked, cache: journalCache,
    err: _journalLoadError ? {{ status: _journalLoadError.status, msg: _journalLoadError.message }} : null }}));
}})();""",
    ])
    p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_502_text_never_migrates():
    r = _run(502, "upstream error")
    assert r["posts"] == [], "로드 실패인데 서버에 썼다(옛 일지 이전 경로)"
    assert r["err"]["status"] == 502 and "upstream error" in r["err"]["msg"]
    assert r["tracked"] == 0, "로드 실패인데 자동 추적(자동저장)이 돌았다"


def test_200_empty_with_local_does_not_migrate_any_more():
    """v5.300: 옛 일지 자동 이전 기능 제거 — 서버가 정상으로 비어 있어도 올리지 않는다.
    브라우저 키는 지우지 않는다(마지막 사본일 수 있음)."""
    r = _run(200, "[]")
    assert r["posts"] == [] and r["err"] is None and r["cache"] == []
    assert "localStorage.removeItem" not in TEXT and "setItem(JKEY" not in TEXT


def test_200_normal_journal_does_not_migrate():
    srv = [{"id": 1, "ticker": "AAPL"}]
    r = _run(200, json.dumps(srv))
    assert r["posts"] == [] and r["cache"] == srv and r["err"] is None


def test_401_does_not_migrate():
    r = _run(401, json.dumps({"ok": False, "error": "로그인 필요"}, ensure_ascii=False))
    assert r["posts"] == [] and r["err"]["status"] == 401


def test_200_non_array_json_does_not_migrate():
    r = _run(200, json.dumps({"ok": False}))
    assert r["posts"] == [] and r["err"] is not None


def test_save_is_blocked_after_load_failure():
    r = _run(502, "upstream error", then_save=True)
    assert r["posts"] == [], "로드 실패 상태에서 저장이 서버로 나갔다"
    assert r["alerts"] and "불러오지 못해" in r["alerts"][0]
    assert r["cache"] == [], "거부된 저장이 캐시를 바꿨다"


def test_journal_get_uses_helper_without_exception():
    fn = _fn("loadJournalFromServer")
    assert "apiJson('/api/journal')" in fn and "allow: 'any'" not in fn
