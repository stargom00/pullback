"""v5.300 — 프론트 일지 저장이 레코드 단위(PUT/DELETE)로만 나가는지.

production의 setJournal / _saveJournalToServer / apiParse 원문을 추출해 가짜 fetch로
**그대로 실행**한다(재구현 금지). 시드는 "서버와 맞춘 상태"(_journalMarkSynced).

사보타주 확인(2026-09-30): _saveJournalToServer의 "바뀐 레코드만" 필터를 전부 보내도록
바꾸면 test_one_change_sends_one_put FAIL — 원복.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

TEXT = (Path(__file__).resolve().parent / "static" / "index.html").read_text(encoding="utf-8")


def _fn(name):
    i = TEXT.index(f"function {name}(")
    start = i - 6 if TEXT[i - 6:i] == "async " else i
    b = TEXT.index("{", TEXT.index(")", i))
    d = 0
    for k in range(b, len(TEXT)):
        d += {"{": 1, "}": -1}.get(TEXT[k], 0)
        if d == 0:
            return TEXT[start:k + 1]
    raise AssertionError(name)


SEED = [{"id": i, "ticker": f"T{i}", "note": "", "rev": 1} for i in (1, 2, 3)]


def _run(fetch_js, action_js):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    js = "\n".join([
        f"let journalCache = {json.dumps(SEED)};",
        "let _journalLoadError = null; let _journalSynced = new Map(); let _journalConflictNotes = [];",
        "let _journalSaveChain = Promise.resolve(); const calls = []; let toast = null;",
        "const document = { getElementById: () => null, body: { appendChild(el) { toast = el.textContent; } },",
        "  createElement: () => { const el = { style: {}, setAttribute() {}, remove() {} };",
        "    Object.defineProperty(el, 'textContent', { set(v) { toast = v; }, get() { return toast; } }); return el; } };",
        "function _normalize(a) { return a; } function showSaveError() {} function alert() {}",
        "const _st = setTimeout; globalThis.setTimeout = (f, ms) => _st(f, Math.min(ms, 5));   // 알림 자동 닫힘 타이머 단축",
        "function getJournal() { return journalCache; }",
        fetch_js,
        *[_fn(n) for n in ("_apiError", "apiParse", "_jrKey", "_journalMarkSynced", "setJournal",
                           "_journalReplaceLocal", "_showJournalConflictToast", "_saveJournalToServer")],
        "_journalMarkSynced(journalCache);",
        f"(async () => {{ {action_js}; console.log(JSON.stringify({{ calls, cache: journalCache, toast }})); }})();",
    ])
    p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


OK_FETCH = """
async function fetch(url, opts) {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  calls.push({ method: opts && opts.method || 'GET', url, body });
  const rec = body && body.record ? Object.assign({}, body.record, { rev: (body.base_rev || 0) + 1 }) : null;
  return { ok: true, status: 200, text: async () => JSON.stringify({ ok: true, record: rec }) };
}"""


def test_one_change_sends_one_put():
    r = _run(OK_FETCH, "const j = getJournal(); j[1].note = '수정'; await setJournal(j)")
    assert [(c["method"], c["url"]) for c in r["calls"]] == [("PUT", "/api/journal/2")]
    assert r["calls"][0]["body"]["base_rev"] == 1 and r["calls"][0]["body"]["record"]["note"] == "수정"
    assert r["cache"][1]["rev"] == 2, "서버가 준 rev가 반영되지 않았다"


def test_no_change_sends_nothing_and_second_save_is_idempotent():
    r = _run(OK_FETCH, "const j = getJournal(); j[0].note = 'a'; await setJournal(j); await setJournal(getJournal())")
    assert len(r["calls"]) == 1


def test_missing_from_array_is_never_deleted_without_explicit_ids():
    r = _run(OK_FETCH, "await setJournal(getJournal().filter(x => x.id !== 3))")
    assert r["calls"] == [], "배열에서 빠졌다는 이유만으로 삭제·전송이 나갔다"


def test_explicit_delete_sends_delete_only():
    r = _run(OK_FETCH, "await setJournal(getJournal().filter(x => x.id !== 3), { deletedIds: [3] })")
    assert [(c["method"], c["url"]) for c in r["calls"]] == [("DELETE", "/api/journal/3")]


def test_conflict_replaces_with_server_copy_and_notifies():
    fetch = """
async function fetch(url, opts) {
  calls.push({ method: opts.method, url });
  return { ok: false, status: 409, text: async () => JSON.stringify({ ok: false, code: 'conflict',
    record: { id: 2, ticker: 'T2', note: '서버쪽 변경', rev: 5 } }) };
}"""
    r = _run(fetch, "const j = getJournal(); j[1].note = '낡은 탭 변경'; await setJournal(j)")
    assert len(r["calls"]) == 1, "409 뒤에 다시 덮어쓰려 했다"
    assert r["cache"][1] == {"id": 2, "ticker": "T2", "note": "서버쪽 변경", "rev": 5}
    assert r["toast"] and "서버 최신본" in r["toast"]


def test_edit_flag_only_for_edit_id():
    r = _run(OK_FETCH, "const j = getJournal(); j[0].note='a'; j[2].note='c'; await setJournal(j, { editId: 3 })")
    flags = {c["url"]: c["body"]["edit"] for c in r["calls"]}
    assert flags == {"/api/journal/1": False, "/api/journal/3": True}


def test_no_full_array_post_anywhere():
    code = "\n".join(l for l in TEXT.split("\n") if not l.strip().startswith("//"))
    assert "fetch('/api/journal', {" not in code, "전체 배열 POST 호출이 남아 있다"
