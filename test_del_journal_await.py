"""delJournal()이 서버 응답을 기다린 뒤 그 결과로 렌더하는지 검증 (v5.247,
사용자 지시 — "낙관적 렌더 금지: 실패가 성공처럼 보이면 안 된다").
v5.300: 저장이 레코드 단위(PUT/DELETE /api/journal/{id})로 바뀌어 시나리오를 새
프로토콜로 옮겼다 — 삭제는 DELETE 1건만, 다른 레코드 PUT 0건, 서버 실패면 되돌림.

setJournal()/_saveJournalToServer()/delJournal() 세 함수를 실제 소스
그대로 추출해 Node에서 실행 — fetch/confirm/alert/renderJournal을
스텁으로 대체해 세 가지 시나리오(정상 삭제, 서버가 가드로 거부, 네트워크
완전 실패)를 결정론적으로 검증한다. setTimeout 재시도(800ms)는 네트워크
실패 케이스에서 실제로 한 번 걸린다(짧게 걸리는 걸 감수 — 재구현 아닌
실제 코드 그대로 실행이 우선)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / "static" / "index.html"


def _extract_function(name: str) -> str:
    text = INDEX_PATH.read_text(encoding="utf-8")
    marker = f"function {name}("
    start = text.find(marker)
    assert start != -1, f"static/index.html에서 `{marker}`를 못 찾음"
    # async function인 경우 "async " 접두어도 포함해야 한다 — marker 자체는
    # "function {name}("만 찾으므로 그 앞에 "async "가 있으면 시작 위치를
    # 당겨야 추출된 소스가 실제로 async 함수로 실행된다(빠뜨리면 내부
    # await가 "await isn't allowed in non-async function"으로 깨짐).
    if text[:start].endswith("async "):
        start -= len("async ")
    paren_start = text.index("(", start)
    depth = 0
    paren_end = None
    for i in range(paren_start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                paren_end = i
                break
    assert paren_end is not None
    brace_start = text.index("{", paren_end)
    depth = 0
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise AssertionError(f"`{name}` 함수의 닫는 중괄호를 못 찾음")


SRC = "\n".join(_extract_function(n) for n in (
    "_apiError", "apiParse", "_jrKey", "_journalMarkSynced", "setJournal",
    "_journalReplaceLocal", "_showJournalConflictToast", "_saveJournalToServer", "delJournal"))


def _run_node(script: str, timeout=15):
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — 실행 테스트 스킵")
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        raise AssertionError(f"node 실행 실패:\n{res.stderr}")
    return res.stdout


def _harness(fetch_impl_js: str, seed_records_json: str):
    """공통 스텁 환경 — fetch만 시나리오별로 다르게 주입. 시드는 '서버와 맞춘 상태'."""
    return f"""
let journalCache = {seed_records_json};
let _journalLoadError = null;   // v5.299: 로드 성공 상태
let _journalSynced = new Map(); let _journalConflictNotes = [];
let editingId = null;
let _journalSaveChain = Promise.resolve();
let renderCallCount = 0;
let alertMessages = [];
let showSaveErrorCalled = false;
const calls = [];
const document = {{ getElementById: () => null, createElement: () => ({{ style: {{}}, setAttribute() {{}} }}), body: {{ appendChild() {{}} }} }};
function _normalize(a) {{ return a; }}
function getJournal() {{ return journalCache; }}
function renderJournal() {{ renderCallCount++; }}
function showSaveError() {{ showSaveErrorCalled = true; }}
function confirm(msg) {{ return true; }}
function alert(msg) {{ alertMessages.push(msg); }}
{fetch_impl_js}
{SRC}
_journalMarkSynced(journalCache);
(async () => {{
  await delJournal(1001);
  console.log(JSON.stringify({{ journalCache, renderCallCount, alertMessages, showSaveErrorCalled, calls }}));
}})();
"""


SEED = json.dumps([{"id": 1001, "ticker": "008930.KS", "rev": 1}, {"id": 1002, "ticker": "005930.KS", "rev": 1}])


def test_normal_delete_removes_record_and_renders_once():
    fetch_impl = """
    async function fetch(url, opts) {
      calls.push((opts && opts.method || 'GET') + ' ' + url);
      return { ok: true, status: 200, text: async () => JSON.stringify({ ok: true, deleted: 1001 }) };
    }
    """
    out = json.loads(_run_node(_harness(fetch_impl, SEED)))
    ids = [r["id"] for r in out["journalCache"]]
    assert ids == [1002]
    assert out["calls"] == ["DELETE /api/journal/1001"], "삭제 외 다른 요청(PUT·POST)이 나갔다"
    assert out["renderCallCount"] == 1 and out["alertMessages"] == []


def test_server_error_keeps_record_and_alerts():
    """★ 서버가 삭제에 실패(500, 재시도까지) — 화면에서 지워진 채로 두면 실패가 성공처럼
    보인다. 되돌리고 알린다(낙관적 렌더 금지)."""
    fetch_impl = """
    async function fetch(url, opts) {
      calls.push((opts && opts.method || 'GET') + ' ' + url);
      return { ok: false, status: 500, text: async () => 'boom' };
    }
    """
    out = json.loads(_run_node(_harness(fetch_impl, SEED), timeout=20))
    ids = [r["id"] for r in out["journalCache"]]
    assert 1001 in ids and 1002 in ids, "서버가 실패했는데 화면에서 사라짐"
    assert out["showSaveErrorCalled"] is True
    assert len(out["alertMessages"]) == 1
    assert out["calls"] == ["DELETE /api/journal/1001"] * 2   # 최초 + 1회 재시도


def test_network_failure_reverts_optimistic_removal_and_alerts():
    fetch_impl = """
    async function fetch(url, opts) { throw new Error('network down'); }
    """
    out = json.loads(_run_node(_harness(fetch_impl, SEED), timeout=20))
    ids = [r["id"] for r in out["journalCache"]]
    assert 1001 in ids and 1002 in ids
    assert out["showSaveErrorCalled"] is True
    assert len(out["alertMessages"]) == 1 and "네트워크" in out["alertMessages"][0]
    assert out["renderCallCount"] == 1
