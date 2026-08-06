"""min_bars 감사 린터 (v5.32) — "게이트는 통과하는데 내부 슬라이스/rolling이
더 많은 봉을 요구하는" 결함 클래스를 정적으로 잡는다. 2026-08 감사에서 4건
(trend_grade/analyze_inverse/rs_raw_score/STAGE2_CONFIG)을 사람이 코드를
한 줄씩 읽어서 찾았는데, 다음에 analyze_* 함수나 헬퍼가 추가되면 같은 결함이
또 생길 수 있어 이 검사를 pytest에 붙여둔다.

pandas 동작이 패턴마다 달라 판정 기준도 다르다:
  .iloc[-N:]  (콜론 슬라이스)  → len<N이어도 절대 안 죽고 전체 시리즈로
      조용히 축소된다(가드 유무와 무관하게 결과 동일) → 검사 대상 아님.
  .iloc[-N]   (스칼라)         → len<N이면 IndexError.
  .rolling(N).mean()           → len<N이면 마지막 값이 NaN(pandas 기본
      안전망). 이 NaN이 가드 없이 비교/불리언에 쓰이면 trend_grade류 버그.
  .rolling(min(N, len(...)))   → 위 안전망을 스스로 무력화하는 자기클램프.
      항상 안티패턴(게이트 무관하게 FAIL).
  idx = -min(days, len(x)-1) - 1  → price_ago류 인덱스 클램프. 항상 안티패턴.

"보호됨" 판정은 변수 단위로 연결한다 — 단순히 "함수 어딘가에 len 체크가
있으면 다 안전"으로 치면(초기 버전이 이렇게 했다가 오탐/누락 둘 다 발생함:
trend_grade의 실제 버그가 무관한 `len(lo)>=252` 체크 때문에 안 잡혔었다),
전혀 무관한 가드가 위험한 rolling을 가려버린다. 대신 rolling(N)이 대입된
변수명(과 그 원본 시리즈명)의 집합을, isnan/isna 인자 또는 len(...) 가드
인자에 등장하는 Name들의 집합과 교집합으로 비교한다 — ast.dump 문자열
substring이 아니라 실제 Name 노드 집합 비교라 짧은 변수명(c, v 등)도
안전하다.

인터프로시저: analyze_* 함수가 이 파일 안의 다른 함수(trend_grade 등)를
호출하면, 그 헬퍼가 "자기 게이트"(함수 초입 `if len(x)<N: return`류)를
가졌는지로 안전 여부가 갈린다.
  - 헬퍼가 자기 게이트를 가짐 → 부족한 데이터에도 스스로 bail-out하므로
    호출부 게이트가 더 작아도 안전(그 헬퍼 자체의 게이트 vs 내부 요구치
    정합성은 OWN_GATE_INSUFFICIENT로 별도 검사).
  - 헬퍼가 자기 게이트가 없음 → 헬퍼의 raw 요구치를 그대로 호출부에 전파해
    비교(HELPER_EXCEEDS_GATE).

한계: 게이트 인식은 `cfg` 기본값(CONFIG dict) 또는 함수 초입의
`if len(x) < N: return`류 리터럴 비교만 인식한다. 더 복잡한 게이트는
NO_GATE_FOUND(INFO)로 표시되니 새 함수 추가 시 이 목록을 같이 확인할 것.
동적 윈도우 크기(정수 리터럴이 아님)는 애초에 집계되지 않는다. 변수 링크도
"대입 즉시 rolling" 형태만 추적하므로(`x = s.rolling(N)...`), 재대입/복잡한
체이닝은 놓칠 수 있다 — 이런 경우 결과가 FAIL로 나오면 사람이 직접 확인.
"""
import ast
import sys
from pathlib import Path

SCANNER_PATH = Path(__file__).parent / "scanner.py"


class Finding:
    def __init__(self, func, kind, detail, line, severity="FAIL"):
        self.func, self.kind, self.detail, self.line, self.severity = (
            func, kind, detail, line, severity)

    def __repr__(self):
        return f"[{self.severity}] {self.func}:{self.line} {self.kind} — {self.detail}"


def _const_int(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, int) else None


def _base_name(node):
    while isinstance(node, (ast.Attribute, ast.Call)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


def _names_in(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _is_clamp_min_call(node):
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "min"):
        return False
    for arg in node.args:
        if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id == "len":
            return True
        if (isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Sub)
                and isinstance(arg.left, ast.Call) and isinstance(arg.left.func, ast.Name)
                and arg.left.func.id == "len"):
            return True
    return False


def _unwrap_float_int(node):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("float", "int") and len(node.args) == 1:
        return node.args[0]
    return node


def _closure(name, aliases, seen=None):
    """m200 = float(ma200.iloc[-1]) 같은 스칼라 파생 변수를, isnan(m200)이
    실제로는 ma200(=rolling(200) 결과)을 보호하고 있음을 알 수 있게 역추적."""
    seen = seen if seen is not None else set()
    if name in seen:
        return seen
    seen.add(name)
    for base in aliases.get(name, ()):
        _closure(base, aliases, seen)
    return seen


def _rolling_source_base(call_node):
    """`X.rolling(N).mean()` 체인에서 X의 베이스 변수명을 뽑는다."""
    node = call_node.func  # Attribute('rolling') 또는 그 뒤 체인
    while isinstance(node, ast.Attribute):
        node = node.value
    if isinstance(node, ast.Call):
        node = node.func
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


class FuncAnalyzer(ast.NodeVisitor):
    def __init__(self, func_name):
        self.func_name = func_name
        # (n, line, is_self_clamp, candidate_names:set)
        self.rolling_windows = []
        # (n, line, candidate_names:set)
        self.scalar_ilocs = []
        self.index_clamps = []
        self.isnan_names = []        # [set(...), ...] — isnan/isna(EXPR)의 EXPR 내 Name들
        self.len_guards = []         # [(threshold|None, set(...)), ...]
        self.ternary_guarded_lines = set()
        self.calls = []
        self.var_aliases = {}   # TARGET -> {BASE, ...} — TARGET = float(BASE.iloc[-1]) 류
        self._current_assign_target = None

    def _register_iloc_alias(self, target, value_node):
        unwrapped = _unwrap_float_int(value_node)
        if isinstance(unwrapped, ast.Subscript) and isinstance(unwrapped.value, ast.Attribute) and unwrapped.value.attr == "iloc":
            base = _base_name(unwrapped.value)
            if base:
                self.var_aliases.setdefault(target, set()).add(base)

    def visit_Assign(self, node):
        v = node.value
        if isinstance(v, ast.BinOp) and isinstance(v.op, ast.Sub):
            left = v.left
            if isinstance(left, ast.UnaryOp) and isinstance(left.op, ast.USub) and _is_clamp_min_call(left.operand):
                self.index_clamps.append(node.lineno)

        target = node.targets[0].id if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) else None
        if target:
            self._register_iloc_alias(target, v)
        elif (len(node.targets) == 1 and isinstance(node.targets[0], ast.Tuple)
                and isinstance(v, ast.Tuple) and len(node.targets[0].elts) == len(v.elts)):
            # m20, m60, m200 = float(ma20.iloc[-1]), float(ma60.iloc[-1]), float(ma200.iloc[-1])
            for t_elt, v_elt in zip(node.targets[0].elts, v.elts):
                if isinstance(t_elt, ast.Name):
                    self._register_iloc_alias(t_elt.id, v_elt)

        prev = self._current_assign_target
        self._current_assign_target = target
        self.generic_visit(node)
        self._current_assign_target = prev

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute) and node.func.attr == "rolling" and node.args:
            src = _rolling_source_base(node)
            candidates = {n for n in (src, self._current_assign_target) if n}
            n = _const_int(node.args[0])
            if n is not None:
                self.rolling_windows.append((n, node.lineno, False, candidates))
            elif _is_clamp_min_call(node.args[0]):
                self.rolling_windows.append((None, node.lineno, True, candidates))

        attr = node.func.attr if isinstance(node.func, ast.Attribute) else (
            node.func.id if isinstance(node.func, ast.Name) else None)
        if attr in ("isnan", "isna") and node.args:
            self.isnan_names.append(_names_in(node.args[0]))

        if isinstance(node.func, ast.Name):
            self.calls.append((node.func.id, node.lineno))
        self.generic_visit(node)

    def visit_GeneratorExp(self, node):
        self._check_isnan_comprehension(node)
        self.generic_visit(node)

    def visit_ListComp(self, node):
        self._check_isnan_comprehension(node)
        self.generic_visit(node)

    def _check_isnan_comprehension(self, node):
        """any(math.isnan(x) for x in (m20, m60, m200)) 류 — 루프변수 x가
        아니라 튜플/리스트 리터럴 안의 실제 변수들을 보호 대상으로 등록."""
        if len(node.generators) != 1:
            return
        gen = node.generators[0]
        if not isinstance(gen.target, ast.Name):
            return
        loop_var = gen.target.id
        elt = node.elt
        attr = elt.func.attr if isinstance(elt, ast.Call) and isinstance(elt.func, ast.Attribute) else (
            elt.func.id if isinstance(elt, ast.Call) and isinstance(elt.func, ast.Name) else None)
        if attr not in ("isnan", "isna") or not elt.args:
            return
        if not (isinstance(elt.args[0], ast.Name) and elt.args[0].id == loop_var):
            return
        if isinstance(gen.iter, (ast.Tuple, ast.List, ast.Set)):
            self.isnan_names.append(_names_in(gen.iter))

    def visit_Compare(self, node):
        if (isinstance(node.left, ast.Call) and isinstance(node.left.func, ast.Name)
                and node.left.func.id == "len" and node.ops
                and isinstance(node.ops[0], (ast.GtE, ast.Gt))):
            n = _const_int(node.comparators[0])
            if n is not None and node.left.args:
                self.len_guards.append((n, _names_in(node.left.args[0])))
        self.generic_visit(node)

    def _register_bare_len_guard(self, test):
        candidates = test.values if isinstance(test, ast.BoolOp) else [test]
        for cand in candidates:
            inner = cand.operand if isinstance(cand, ast.UnaryOp) and isinstance(cand.op, ast.Not) else cand
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id == "len" and inner.args:
                self.len_guards.append((None, _names_in(inner.args[0])))

    def visit_If(self, node):
        self._register_bare_len_guard(node.test)
        self.generic_visit(node)

    def visit_IfExp(self, node):
        self._register_bare_len_guard(node.test)
        test_names = _names_in(node.test)
        for sub in ast.walk(node.body):
            if isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Attribute) and sub.value.attr == "iloc":
                base = _base_name(sub.value)
                if base and base in test_names:
                    self.ternary_guarded_lines.add(sub.lineno)
        self.generic_visit(node)

    def visit_Subscript(self, node):
        sl = node.slice
        if isinstance(sl, ast.Slice):
            self.generic_visit(node)
            return  # X.iloc[-N:] — 항상 안전, 집계 안 함
        n = None
        if isinstance(sl, ast.UnaryOp) and isinstance(sl.op, ast.USub):
            n = _const_int(sl.operand)
        elif isinstance(sl, ast.Constant) and isinstance(sl.value, int) and sl.value < 0:
            n = -sl.value
        if n is not None and isinstance(node.value, ast.Attribute) and node.value.attr == "iloc":
            base = _base_name(node.value)
            self.scalar_ilocs.append((n, node.lineno, {base} if base else set()))
        self.generic_visit(node)


def extract_config_min_bars(tree):
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if isinstance(node.value, ast.Dict):
                for k, val in zip(node.value.keys, node.value.values):
                    if isinstance(k, ast.Constant) and k.value == "min_bars":
                        n = _const_int(val)
                        if n is not None:
                            out[node.targets[0].id] = n
    return out


def extract_own_gate(func_node, config_min_bars):
    if func_node.args.defaults:
        for arg, default in zip(func_node.args.args[-len(func_node.args.defaults):], func_node.args.defaults):
            if arg.arg == "cfg" and isinstance(default, ast.Name) and default.id in config_min_bars:
                return config_min_bars[default.id]

    for node in ast.walk(func_node):
        if isinstance(node, ast.If):
            test = node.test
            comparisons = (test.values if isinstance(test, ast.BoolOp) else [test])
            for cmp in comparisons:
                if (isinstance(cmp, ast.Compare) and isinstance(cmp.left, ast.Call)
                        and isinstance(cmp.left.func, ast.Name) and cmp.left.func.id == "len"
                        and cmp.ops and isinstance(cmp.ops[0], ast.Lt)):
                    n = _const_int(cmp.comparators[0])
                    if n is not None:
                        return n
    return None


def analyze_module(path=SCANNER_PATH):
    src = path.read_text()
    tree = ast.parse(src, filename=str(path))
    config_min_bars = extract_config_min_bars(tree)

    func_nodes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    gates, analyzers = {}, {}
    for name, node in func_nodes.items():
        gates[name] = extract_own_gate(node, config_min_bars)
        fa = FuncAnalyzer(name)
        fa.visit(node)
        analyzers[name] = fa

    def is_protected(fa, n, candidates):
        if not candidates:
            return False
        expanded = set()
        for c in candidates:
            expanded |= _closure(c, fa.var_aliases)
        for names in fa.isnan_names:
            names_expanded = set()
            for nm in names:
                names_expanded |= _closure(nm, fa.var_aliases)
            if names_expanded & expanded:
                return True
        for threshold, names in fa.len_guards:
            if threshold is not None and threshold < n:
                continue
            names_expanded = set()
            for nm in names:
                names_expanded |= _closure(nm, fa.var_aliases)
            if names_expanded & expanded:
                return True
        return False

    def raw_requirement(name, _seen=None):
        """이 함수 자체가 '보호 없이' 요구하는 최대 봉 수 — 자기 게이트가
        있는 콜리(스스로 bail-out하는 헬퍼)는 재귀에서 제외한다(그런 헬퍼를
        부족한 데이터로 호출하는 건 정상적인 graceful-degradation)."""
        _seen = (_seen or set()) | {name}
        fa = analyzers.get(name)
        if fa is None:
            return 0
        req = 0
        for n, line, is_clamp, candidates in fa.rolling_windows:
            if not is_clamp and n is not None and not is_protected(fa, n, candidates):
                req = max(req, n)
        for n, line, candidates in fa.scalar_ilocs:
            if line not in fa.ternary_guarded_lines and not is_protected(fa, n, candidates):
                req = max(req, n)
        for callee, line in fa.calls:
            if callee in func_nodes and callee not in _seen and gates.get(callee) is None:
                req = max(req, raw_requirement(callee, _seen))
        return req

    req_cache = {}

    def own_requirement(name):
        if name in req_cache:
            return req_cache[name]
        g = gates.get(name)
        result = g if g is not None else raw_requirement(name)
        req_cache[name] = result
        return result

    findings = []
    for name, fa in analyzers.items():
        gate = gates.get(name)

        for n, line, is_clamp, candidates in fa.rolling_windows:
            if is_clamp:
                findings.append(Finding(name, "SELF_CLAMP_ROLLING",
                    "rolling(min(N, len(...))) — NaN 안전망을 스스로 무력화", line))
                continue
            if gate is not None and n > gate:
                sev = "WARN" if is_protected(fa, n, candidates) else "FAIL"
                findings.append(Finding(name, "UNGUARDED_ROLLING",
                    f"rolling({n}) > gate({gate})" + (" (연결된 길이체크/isnan 존재 — 확인)" if sev == "WARN" else " — NaN이 무가드로 비교에 쓰일 수 있음"),
                    line, severity=sev))

        for line in fa.index_clamps:
            findings.append(Finding(name, "INDEX_CLAMP",
                "idx = -min(days, len(x)-1)-1 — 오래된 봉으로 조용히 대체", line))

        for n, line, candidates in fa.scalar_ilocs:
            if line in fa.ternary_guarded_lines:
                continue
            if gate is not None and n > gate:
                sev = "WARN" if is_protected(fa, n, candidates) else "FAIL"
                findings.append(Finding(name, "UNGUARDED_SCALAR_ILOC",
                    f".iloc[-{n}] > gate({gate}) — len 부족하면 IndexError", line, severity=sev))

        if gate is None and (fa.rolling_windows or fa.scalar_ilocs):
            findings.append(Finding(name, "NO_GATE_FOUND",
                "min_bars 게이트를 못 찾음 — 수동 확인 필요",
                func_nodes[name].lineno, severity="INFO"))

        if gate is not None:
            internal_max = raw_requirement(name)
            if internal_max > gate:
                findings.append(Finding(name, "OWN_GATE_INSUFFICIENT",
                    f"자기 게이트({gate}) < 내부 요구치({internal_max})", func_nodes[name].lineno))

        if gate is not None:
            for callee, line in fa.calls:
                if callee == name or callee not in func_nodes:
                    continue
                if gates.get(callee) is not None:
                    continue
                req = own_requirement(callee)
                if req > gate:
                    findings.append(Finding(name, "HELPER_EXCEEDS_GATE",
                        f"{callee}()의 내부 요구치 {req} > 이 함수 gate({gate}) (헬퍼에 자기 게이트 없음)", line))

    return findings, gates


def main():
    findings, gates = analyze_module()
    fails = [f for f in findings if f.severity == "FAIL"]
    warns = [f for f in findings if f.severity == "WARN"]
    infos = [f for f in findings if f.severity == "INFO"]

    print(f"게이트 인식: {sum(1 for v in gates.values() if v is not None)}/{len(gates)}개 함수")
    for f in fails:
        print(f)
    if warns:
        print("--- WARN(수동 확인 권장, non-blocking) ---")
        for f in warns:
            print(f)
    if infos:
        print("--- INFO ---")
        for f in infos:
            print(f)
    print(f"\n{len(fails)} FAIL, {len(warns)} WARN, {len(infos)} INFO")
    return 0 if not fails else 1


def test_no_min_bars_gaps():
    findings, _ = analyze_module()
    fails = [f for f in findings if f.severity == "FAIL"]
    assert not fails, "min_bars 감사 위반:\n" + "\n".join(str(f) for f in fails)


if __name__ == "__main__":
    sys.exit(main())
