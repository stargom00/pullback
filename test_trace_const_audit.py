"""app.py의 _trace_* 함수(진단 재현 함수) 상수 복사 감사 (v5.61).

배경: `_trace_pullback`이 scanner.analyze()의 slope_floor(주도주 0.98/일반
1.0 분기)를 리터럴로 복사해두고 있다가, scanner.py 쪽만 v5.60에서 바뀌면서
동기화가 안 됐던 사고(docs/rs_definition_and_slope_investigation.md 6절).
"CONFIG 밖의 판정 상수를 함수 안에 리터럴로 복사"라는 같은 클래스가 다시
생기지 않게 두 가지를 정적으로 검사한다.

한계(그래서 완전자동 FAIL 판정은 좁게만 건다): 이 파일만 봐서는 어떤
리터럴이 "scanner.py의 특정 값과 반드시 같아야 하는 사본"인지, 그냥
app.py 고유의 상수인지 구별할 수 없다(같은 함수 페어링을 사람이 알아야
하는 지식이라 완전 자동화가 어려움 — CLAUDE.md에도 명시). 그래서:

1. BARE_LITERAL_TERNARY(FAIL, 높은 정밀도): `X = <리터럴> if 조건 else
   <리터럴>` 형태 — 정확히 이번 사고가 난 모양(slope_floor = 0.98 if
   is_leader else 1.0). cfg[...]/cfg.get(...) 양쪽 분기면 이미 안전하니
   제외. 이 패턴이 app.py _trace_*에 다시 나타나면 무조건 FAIL 처리 —
   scanner.py에 대응하는 실제 로직이 있는지, 있다면 값이 같은지 사람이
   확인하고 cfg[...] 참조로 바꾸거나(가능하면) 최소한 동기화 주석을 남길 것.
2. BARE_FLOAT_LITERAL(INFO, 체크리스트): 함수 안의 모든 float 리터럴을
   나열만 한다 — scanner.py의 대응 함수를 바꿀 때 이 목록을 눈으로 대조.
   차단(FAIL)하지 않는 이유: 대부분 정상(반올림 자릿수, %변환 등)이라
   전부 막으면 노이즈가 커서 오히려 안 보게 됨.
"""
import ast
from pathlib import Path

APP_PATH = Path(__file__).parent / "app.py"


class Finding:
    def __init__(self, func, kind, detail, line, severity="FAIL"):
        self.func, self.kind, self.detail, self.line, self.severity = (
            func, kind, detail, line, severity)

    def __repr__(self):
        return f"[{self.severity}] {self.func}:{self.line} {self.kind} — {self.detail}"


def _is_cfg_lookup(node):
    """cfg["x"] / cfg.get("x", ...) 형태인지."""
    if isinstance(node, ast.Subscript):
        return isinstance(node.value, ast.Name) and node.value.id == "cfg"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return (node.func.attr == "get" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "cfg")
    return False


def _is_bare_numeric_const(node):
    return (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool))


def find_trace_functions(tree):
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith("_trace_")]


def audit_function(func_node):
    findings = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.IfExp):
            body_bare = _is_bare_numeric_const(node.body)
            orelse_bare = _is_bare_numeric_const(node.orelse)
            body_cfg = _is_cfg_lookup(node.body)
            orelse_cfg = _is_cfg_lookup(node.orelse)
            if body_bare and orelse_bare and not (body_cfg or orelse_cfg):
                findings.append(Finding(
                    func_node.name, "BARE_LITERAL_TERNARY",
                    f"{node.body.value} if <조건> else {node.orelse.value} — "
                    "scanner.py의 판정 상수를 리터럴로 복사한 모양(v5.60 slope_floor "
                    "사고와 동일 패턴). scanner.py에 대응 로직이 있으면 cfg[...] 참조로 "
                    "바꾸거나(CONFIG로 승격), 구조상 안 되면 동기화 주석을 남길 것.",
                    node.lineno))
        if _is_bare_numeric_const(node) and isinstance(node.value, float):
            findings.append(Finding(
                func_node.name, "BARE_FLOAT_LITERAL", f"{node.value}",
                node.lineno, severity="INFO"))
    return findings


def analyze_module(path=APP_PATH):
    src = path.read_text()
    tree = ast.parse(src, filename=str(path))
    findings = []
    for fn in find_trace_functions(tree):
        findings.extend(audit_function(fn))
    return findings


def main():
    findings = analyze_module()
    fails = [f for f in findings if f.severity == "FAIL"]
    infos = [f for f in findings if f.severity == "INFO"]
    for f in fails:
        print(f)
    if infos:
        print("--- INFO(체크리스트 — scanner.py 대응 함수와 값 대조할 것) ---")
        for f in infos:
            print(f)
    print(f"\n{len(fails)} FAIL, {len(infos)} INFO")
    return 0 if not fails else 1


def test_no_bare_literal_ternary_in_trace_functions():
    findings = analyze_module()
    fails = [f for f in findings if f.severity == "FAIL"]
    assert not fails, "_trace_* 상수 복사 감사 위반:\n" + "\n".join(str(f) for f in fails)


if __name__ == "__main__":
    import sys
    sys.exit(main())
