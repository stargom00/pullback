"""delJournal()이 서버 응답을 기다린 뒤 그 결과로 렌더하는지 검증 (v5.247,
사용자 지시 — "낙관적 렌더 금지: 실패가 성공처럼 보이면 안 된다").

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


SET_JOURNAL_SRC = _extract_function("setJournal")
SAVE_TO_SERVER_SRC = _extract_function("_saveJournalToServer")
DEL_JOURNAL_SRC = _extract_function("delJournal")


def _run_node(script: str, timeout=15):
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — 실행 테스트 스킵")
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        raise AssertionError(f"node 실행 실패:\n{res.stderr}")
    return res.stdout


def _harness(fetch_impl_js: str, seed_records_json: str):
    """공통 스텁 환경 — fetch만 시나리오별로 다르게 주입."""
    return f"""
let journalCache = {seed_records_json};
let editingId = null;
let _journalSaveChain = Promise.resolve();
let renderCallCount = 0;
let alertMessages = [];
let showSaveErrorCalled = false;
function getJournal() {{ return journalCache; }}
function renderJournal() {{ renderCallCount++; }}
function showSaveError() {{ showSaveErrorCalled = true; }}
function confirm(msg) {{ return true; }}
function alert(msg) {{ alertMessages.push(msg); }}
{fetch_impl_js}
{SET_JOURNAL_SRC}
{SAVE_TO_SERVER_SRC}
{DEL_JOURNAL_SRC}

(async () => {{
  await delJournal(1001);
  console.log(JSON.stringify({{
    journalCache, renderCallCount, alertMessages, showSaveErrorCalled
  }}));
}})();
"""


def test_normal_delete_removes_record_and_renders_once():
    """정상 케이스: 서버가 실제로 지운 배열(1001 없음)을 돌려줌."""
    seed = json.dumps([{"id": 1001, "ticker": "008930.KS"}, {"id": 1002, "ticker": "005930.KS"}])
    fetch_impl = """
    async function fetch(url, opts) {
      return { ok: true, json: async () => ({ ok: true, journal: [{id:1002, ticker:'005930.KS'}] }) };
    }
    """
    out = json.loads(_run_node(_harness(fetch_impl, seed)))
    ids = [r["id"] for r in out["journalCache"]]
    assert 1001 not in ids
    assert 1002 in ids
    assert out["renderCallCount"] == 1
    assert out["alertMessages"] == []


def test_server_rejects_deletion_record_stays_and_alerts():
    """★ 핵심. 서버 응답은 성공(ok=true)이지만 그 id가 journal에 여전히
    있음(가드가 거부한 것처럼) — 화면(journalCache)에도 남아야 하고
    사용자에게 알림이 떠야 한다. 낙관적 렌더 금지 확인."""
    seed = json.dumps([{"id": 1001, "ticker": "008930.KS"}, {"id": 1002, "ticker": "005930.KS"}])
    fetch_impl = """
    async function fetch(url, opts) {
      // deletedIds를 보냈는데도 서버가 되살린 상황을 흉내(이상 상황 방어 확인용)
      return { ok: true, json: async () => ({ ok: true, journal: [{id:1001, ticker:'008930.KS'}, {id:1002, ticker:'005930.KS'}] }) };
    }
    """
    out = json.loads(_run_node(_harness(fetch_impl, seed)))
    ids = [r["id"] for r in out["journalCache"]]
    assert 1001 in ids, "서버가 거부했는데 화면에서 사라짐 — 낙관적 렌더 금지 위반"
    assert len(out["alertMessages"]) == 1
    assert "삭제되지 않았어요" in out["alertMessages"][0]
    assert out["renderCallCount"] == 1


def test_network_failure_reverts_optimistic_removal_and_alerts():
    """★ 네트워크 완전 실패(재시도까지 실패) — setJournal()이 이미
    journalCache를 낙관적으로 지운 상태이므로, delJournal()이 되돌려야
    한다(실패가 성공처럼 보이면 안 됨)."""
    seed = json.dumps([{"id": 1001, "ticker": "008930.KS"}, {"id": 1002, "ticker": "005930.KS"}])
    fetch_impl = """
    async function fetch(url, opts) { throw new Error('network down'); }
    """
    out = json.loads(_run_node(_harness(fetch_impl, seed), timeout=20))
    ids = [r["id"] for r in out["journalCache"]]
    assert 1001 in ids, "네트워크 실패인데도 삭제된 것처럼 보임 — 낙관적 렌더 금지 위반"
    assert 1002 in ids
    assert out["showSaveErrorCalled"] is True
    assert len(out["alertMessages"]) == 1
    assert "네트워크" in out["alertMessages"][0]
    assert out["renderCallCount"] == 1


def test_del_journal_sends_deleted_ids():
    """delJournal()이 실제로 deletedIds를 payload에 실어 보내는지 —
    fetch 호출 인자를 그대로 캡처해서 확인."""
    seed = json.dumps([{"id": 1001, "ticker": "008930.KS"}])
    fetch_impl = """
    let capturedBody = null;
    async function fetch(url, opts) {
      capturedBody = JSON.parse(opts.body);
      return { ok: true, json: async () => ({ ok: true, journal: [] }) };
    }
    """
    script = _harness(fetch_impl, seed).replace(
        "console.log(JSON.stringify({",
        "console.log(JSON.stringify({ capturedBody,"
    )
    out = json.loads(_run_node(script))
    assert out["capturedBody"]["deleted_ids"] == [1001]
    assert out["capturedBody"]["records"] == []
