"""v5.298 — static/index.html의 apiJson/apiParse(API JSON 공통 헬퍼).

Railway 프록시 타임아웃이 준 일반 텍스트 "upstream error"를 프론트가 상태 확인 없이
res.json()으로 읽어 "SyntaxError: Unexpected token 'u'"만 보였다 — 원인(HTTP 상태·
본문)이 가려졌다. 헬퍼 텍스트를 추출해 가짜 fetch로 **그대로 실행**한다(재구현 금지).

사보타주 확인(2026-09-30): ① apiParse의 `!res.ok` 검사 제거 → test_502_upstream_text
FAIL(처음엔 통과했다 — 파싱 실패 경로가 같은 문구를 내서. notJson·JSON 5xx 검사 추가로 보강) ② 교체한 곳 하나(loadEod)를 `await res.json()` 직접 호출로 되돌림 →
test_no_direct_json_on_fetch_responses FAIL. 둘 다 원복.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

TEXT = (Path(__file__).resolve().parent / "static" / "index.html").read_text(encoding="utf-8")


def _fn(name):
    i = TEXT.index(f"function {name}(")
    b = TEXT.index("{", i)
    d = 0
    for k in range(b, len(TEXT)):
        d += {"{": 1, "}": -1}.get(TEXT[k], 0)
        if d == 0:
            return TEXT[i:k + 1]
    raise AssertionError(name)


def _src():
    # async function 선언 앞의 "async " 포함해서 가져온다
    out = []
    for n in ("_apiError", "apiParse", "apiJson"):
        f = _fn(n)
        pre = TEXT[TEXT.index(f) - 6:TEXT.index(f)]
        out.append(("async " if pre == "async " else "") + f)
    return "\n".join(out)


def _run(status, body, cfg=None, ok=None):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    js = _src() + f"""
const fetch = async () => ({{ status: {status}, ok: {json.dumps(ok if ok is not None else (200 <= status < 300))},
  text: async () => {json.dumps(body)} }});
apiJson('/x', undefined, {json.dumps(cfg) if cfg else 'undefined'})
  .then(v => console.log(JSON.stringify({{ok: true, v}})))
  .catch(e => console.log(JSON.stringify({{ok: false, msg: e.message, status: e.status, body: e.body, notJson: e.notJson}})));
"""
    p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_502_upstream_text():
    r = _run(502, "upstream error")
    assert r["ok"] is False
    assert "502" in r["msg"] and "upstream error" in r["msg"]
    assert r["status"] == 502 and r["body"] == "upstream error"
    # 상태 검사로 걸려야 한다 — res.ok 검사가 빠져도 JSON 파싱 실패로 "502"와 본문이
    # 메시지에 들어가 버려 위 두 줄만으론 못 잡는다(사보타주 ①에서 실제로 통과했다).
    assert r["notJson"] is False and "JSON 아님" not in r["msg"]
    # JSON 본문을 가진 5xx도 HTTP 오류로 던져야 한다(파싱이 성공해도 값으로 새면 안 됨)
    r2 = _run(503, json.dumps({"detail": "busy"}))
    assert r2["ok"] is False and r2["status"] == 503 and "busy" in r2["msg"]


def test_200_json_value_passthrough():
    r = _run(200, json.dumps({"a": 1, "b": [1, 2]}))
    assert r == {"ok": True, "v": {"a": 1, "b": [1, 2]}}


def test_200_not_json():
    r = _run(200, "<html>oops</html>")
    assert r["ok"] is False and "JSON 아님" in r["msg"] and r["notJson"] is True and r["status"] == 200


def test_401_login_required_is_visible_not_swallowed():
    """프론트엔 401 전용 로그인 처리가 원래 없다 — 서버(_auth_gate)가 API엔 401 JSON
    {"ok":false,"error":"로그인 필요"}, 페이지 이동엔 /login 리다이렉트를 한다. 헬퍼는
    이 401을 삼키지 않고 상태·본문 그대로 호출부 catch로 올린다."""
    body = json.dumps({"ok": False, "error": "로그인 필요"}, ensure_ascii=False)
    r = _run(401, body)
    assert r["ok"] is False and r["status"] == 401 and "로그인 필요" in r["msg"]
    # 일지 로드처럼 allow:'any'인 곳은 예전처럼 본문 JSON을 그대로 받는다
    r2 = _run(401, body, {"allow": "any"})
    assert r2 == {"ok": True, "v": {"ok": False, "error": "로그인 필요"}}


def test_allowed_error_status_returns_body():
    r = _run(409, json.dumps({"ok": False, "code": "already_above_pivot"}), {"allow": [409]})
    assert r == {"ok": True, "v": {"ok": False, "code": "already_above_pivot"}}
    r2 = _run(500, json.dumps({"ok": False}), {"allow": [409]})
    assert r2["ok"] is False and r2["status"] == 500


def test_204_and_empty_body_is_null():
    assert _run(204, "") == {"ok": True, "v": None}


def test_no_direct_json_on_fetch_responses():
    """헬퍼 밖에서 fetch 응답에 .json()을 직접 부르는 곳 0곳(예외 목록 없음)."""
    code = "\n".join(l for l in TEXT.split("\n") if not l.strip().startswith("//"))
    hits = [m.start() for m in re.finditer(r"\.json\(\)", code)]
    assert not hits, [code[h - 60:h + 10] for h in hits]
    assert "apiParse(" in _fn("apiJson")
