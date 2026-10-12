"""Library test suites as an oracle family: numpy-financial and pyxirr, read from their own test files.

The oracles
-----------
Two open-source libraries publish the answers their rate and present-value
functions must give, as test files. Both are vendored unmodified under
``cases/reference/library`` and pinned in the manifest:

* ``numpy-financial/test_financial.py`` (numpy/numpy-financial v2.0.0,
  BSD-3): pytest classes; the answers are literals inside
  ``assert_allclose`` / ``assert numpy.isnan`` / ``pytest.raises``;
* ``pyxirr/tests/test_periodic.rs`` and ``test_scheduled.rs``
  (Anexen/pyxirr v0.10.8, Unlicense): Rust ``rstest`` functions; the
  answers sit in ``#[case(...)]`` attributes and ``assert_almost_eq!``
  calls, some reading dated flows from ``tests/samples/*.csv``. The
  default tolerance of ``assert_almost_eq!`` (1e-9) is read from the
  vendored ``tests/common/helpers.rs``, not typed here.

Nothing is translated by hand. The test files are read as data: Python by
its syntax tree, Rust by bracket-matching its attributes and macro calls.
Every number compared, every flow and every tolerance comes from the
vendored files.

Ids
---
``numpy-financial/<Class>::<test>`` and ``pyxirr/<file>::<test>``; a
parametrized test is one oracle per case, ``...[<n>]`` (numpy, in pytest's
order) or ``...[<label>]`` (pyxirr, the ``case::label`` name, else the
case's position). A failing case then names itself on the scoreboard
instead of sinking its whole test.

What runs, what is blocked, what is left out
--------------------------------------------
The engine's rate measures are :func:`one_engine.measures.irr`, ``npv``,
``xirr`` and ``xnpv``. A test that calls any other library function (pv,
fv, pmt, ipmt, ppmt, nper, rate, mirr, xfv, xnfv, cumprinc, ...) is
``blocked`` and the reason names the functions the engine lacks. A pyxirr
case asking for a day count other than actual/365 is blocked the same way.

Left out of the oracle list, with the reason in :data:`EXCLUDED`:
property tests (``@given``: random inputs, no stored answer), tests of
numpy array shapes (the engine's measures take one flat stream), and a
pyxirr test that calls no pyxirr function (it checks its own macro).

How each library's conventions map onto the engine
---------------------------------------------------
* npv: both libraries discount the first flow at t = 0, as the engine
  does. pyxirr's ``start_from_zero=False`` discounts it one period: read
  as the engine's npv of the stream with a zero flow put in front.
* "no answer": numpy returns NaN or raises ``NoRealSolutionError``;
  pyxirr returns ``None`` (NaN in its tests) or raises
  ``InvalidPaymentsError``. The engine answers with a refusal
  (:class:`one_engine.measures.MeasureError`). A refusal agrees with
  "no answer" and with nothing else; any other exception the engine raises
  fails the whole receipt (``differs``: the engine raised).
* several roots: the engine refuses (:class:`AmbiguousRate`) and names
  them; the libraries pick one. Such a check fails and its ``got`` lists
  every root, so a reader sees whether the library's pick is among them.

Comparison rules (each test's own)
----------------------------------
pyxirr ``assert_almost_eq!(a, b[, eps])``: ``|a - b| < eps``. numpy
``assert_allclose(actual, desired, rtol=1e-7, atol=0)``: ``|actual -
desired| <= atol + rtol * |desired|``; ``assert_equal``: equal.

Planted error
-------------
The first engine call a test makes gets one input changed: its first
non-zero flow x 1.01. When the test expects no answer, that cannot show
anything, so instead the stream is made to start negative and end positive
(first flow ``-(|f| + 1)``, last ``|f| + 1``; an empty stream becomes
``[-1, 1]``), which gives it a rate; for npv the rate moves by +0.01. Only
the first call is changed: a check such as ``npv(irr(x), x) == 0`` stays
true when both calls see the same changed stream.
"""
from __future__ import annotations

import ast
import csv
import datetime as _dt
import itertools
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from one_engine import measures as M
from one_engine.oracles import REFERENCE, Blocked, Check, Family, verify

FOLDER = "library"
NUMPY = f"{FOLDER}/numpy-financial/test_financial.py"
PYXIRR = f"{FOLDER}/pyxirr/tests"
PYXIRR_FILES = ("test_periodic", "test_scheduled")
HELPERS = f"{PYXIRR}/common/helpers.rs"
#: the library functions the engine answers, by the engine function that answers them
ENGINE = {"irr": M.irr, "npv": M.npv, "xirr": M.xirr, "xnpv": M.xnpv}
#: tests that are not oracles at all, with why
EXCLUDED = {
    "numpy-financial/TestNpv::test_invalid_rates_shape":
        "tests numpy's refusal of a rate array of the wrong shape; the engine takes one rate",
    "numpy-financial/TestNpv::test_invalid_cashflows_shape":
        "tests numpy's refusal of a cash-flow array of the wrong shape; the engine takes one flat stream",
    "pyxirr/test_periodic::test_fv_macro_working":
        "calls no pyxirr function: it checks the test file's own assert_future_value! macro",
}
GIVEN = "a property test (@given): random inputs with no stored answer"


# ── the engine's side ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Refused:
    """The engine declined to give a number (a :class:`MeasureError`), with its reason."""

    why: str
    roots: Tuple[float, ...] = ()

    def __str__(self) -> str:
        if self.roots:
            return "refuses: several rates fit: " + ", ".join(repr(r) for r in self.roots)
        return f"refuses: {self.why}"


class _Engine:
    """The four measures as the tests call them, with the planted change on the first call only.

    ``flip`` says the test expects no answer, so the plant must give the
    stream (or npv's rate) an answer rather than nudge a number (see the
    module docstring, Planted error).
    """

    def __init__(self, plant: bool, flip: bool):
        self.plant, self.flip, self.calls = plant, flip, 0

    def _first(self) -> bool:
        self.calls += 1
        return self.plant and self.calls == 1

    @staticmethod
    def _scaled(stream: List[float]) -> List[float]:
        out = list(stream)
        for i, v in enumerate(out):
            if v:
                out[i] = v * 1.01
                break
        return out

    @staticmethod
    def _flipped(stream: List[float], order: Optional[Sequence[Any]] = None) -> List[float]:
        """The stream made to start negative and end positive; ``order`` (dates) says which is which."""
        if not stream:
            return [-1.0, 1.0]
        out = list(stream)
        idx = range(len(out)) if order is None else sorted(range(len(out)), key=lambda i: order[i])
        first, last = idx[0], idx[-1]
        out[first], out[last] = -(abs(out[first]) + 1), abs(out[last]) + 1
        return out

    def _call(self, fn: Callable, *args) -> Any:
        if any(isinstance(a, Refused) for a in args):
            return Refused("an input to this call was itself refused")
        try:
            return fn(*args)
        except M.AmbiguousRate as e:
            return Refused(str(e), tuple(e.roots))
        except M.MeasureError as e:
            return Refused(str(e))

    def irr(self, stream: List[float]) -> Any:
        if self._first():
            stream = self._flipped(stream) if self.flip else self._scaled(stream)
        return self._call(M.irr, stream)

    def npv(self, rate: float, stream: List[float]) -> Any:
        if self._first():
            if self.flip:
                rate = rate + 0.01
            else:
                stream = self._scaled(stream)
        return self._call(M.npv, stream, rate)

    def xirr(self, dates: List[_dt.date], amounts: List[float]) -> Any:
        if self._first():
            if self.flip and not amounts:
                dates = [_dt.date(2000, 1, 1), _dt.date(2001, 1, 1)]
            amounts = self._flipped(amounts, dates) if self.flip else self._scaled(amounts)
        return self._call(M.xirr, amounts, dates)

    def xnpv(self, rate: float, dates: List[_dt.date], amounts: List[float]) -> Any:
        if self._first():
            if self.flip:
                rate = rate + 0.01
            else:
                amounts = self._scaled(amounts)
        return self._call(M.xnpv, amounts, dates, rate)


def _show(v: Any) -> str:
    return str(v) if isinstance(v, Refused) else repr(float(v))


def _near(name: str, got: Any, want: float, eps: float, at: str = "") -> Check:
    """pyxirr's ``assert_almost_eq!``: strictly within ``eps``; a refusal never is."""
    ok = not isinstance(got, Refused) and abs(float(got) - want) < eps
    return Check(name, at, f"{want!r} (within {eps:g})", _show(got), ok)


def _refuses(name: str, got: Any, at: str = "") -> Check:
    return Check(name, at, "no answer", _show(got), isinstance(got, Refused) and not got.roots)


# ── numpy-financial: the test file read by its syntax tree ──────────────────

@lru_cache(maxsize=None)
def _numpy_tree() -> ast.Module:
    return ast.parse((REFERENCE / NUMPY).read_text())


def _is_given(fn: ast.FunctionDef) -> bool:
    return any(isinstance(d, ast.Call) and getattr(d.func, "id", None) == "given" for d in fn.decorator_list)


def _parametrize(fn: ast.FunctionDef) -> List[Dict[str, ast.expr]]:
    """Every case of a test's ``pytest.mark.parametrize`` decorators, as name -> literal node.

    Stacked decorators multiply; pytest applies the lowest first, so its ids
    run over the lowest decorator's values fastest (``[0-end]`` order is not
    needed here, only a stable one).
    """
    axes = []
    for d in fn.decorator_list:
        if isinstance(d, ast.Call) and ast.unparse(d.func) == "pytest.mark.parametrize":
            names = [n.strip() for n in ast.literal_eval(d.args[0]).split(",")]
            values = d.args[1].elts
            if len(names) == 1:
                axes.append([{names[0]: v} for v in values])
            else:
                axes.append([dict(zip(names, v.elts)) for v in values])
    cases = [{}]
    for axis in reversed(axes):
        cases = [{**c, **a} for c in cases for a in axis]
    return cases


@lru_cache(maxsize=None)
def _numpy_tests() -> Dict[str, Tuple[ast.FunctionDef, Optional[Dict[str, ast.expr]]]]:
    """``oid -> (test function, its parametrize case or None)``, excluded tests left out."""
    out: Dict[str, Tuple[ast.FunctionDef, Optional[Dict[str, ast.expr]]]] = {}
    for cls in _numpy_tree().body:
        if not isinstance(cls, ast.ClassDef):
            continue
        for fn in cls.body:
            if not (isinstance(fn, ast.FunctionDef) and fn.name.startswith("test_")):
                continue
            base = f"numpy-financial/{cls.name}::{fn.name}"
            if base in EXCLUDED or _is_given(fn):
                continue
            cases = _parametrize(fn)
            if cases == [{}]:
                out[base] = (fn, None)
            else:
                for i, case in enumerate(cases):
                    out[f"{base}[{i}]"] = (fn, case)
    return out


def _npf_calls(fn: ast.FunctionDef) -> List[str]:
    """The ``npf`` functions a test calls (its exception classes, ``npf.XxxError``, are not functions:
    ``pytest.raises`` around them is read as "no answer")."""
    return sorted({n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                   and n.value.id == "npf" and not n.attr[0].isupper()})


def _expects_nothing(fn: ast.FunctionDef) -> bool:
    """Whether the test's answer is "no answer" (``isnan`` or ``pytest.raises``)."""
    src = ast.unparse(fn)
    return "numpy.isnan" in src or "pytest.raises" in src


class _NumpyReader:
    """Evaluates one numpy-financial test body over literals, with ``npf`` routed to the engine.

    Only the forms the rate and npv tests use are read; anything else is a
    :class:`Blocked` naming the expression, never a guess.
    """

    def __init__(self, eng: _Engine, env: Dict[str, Any]):
        self.eng, self.env, self.checks = eng, env, []

    def value(self, node: ast.expr) -> Any:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, (ast.List, ast.Tuple)):
            return [self.value(e) for e in node.elts]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            v = self.value(node.operand)
            return v if isinstance(v, Refused) else -v
        if isinstance(node, ast.Name) and node.id in self.env:
            return self.env[node.id]
        if isinstance(node, ast.Call):
            f = ast.unparse(node.func)
            if f == "numpy.array" and len(node.args) == 1:
                return self.value(node.args[0])
            if f == "numpy.arange" and len(node.args) == 1:
                return [float(i) for i in range(int(self.value(node.args[0])))]
            if f == "npf.irr":
                return self.eng.irr(self.value(node.args[0]))
            if f == "npf.npv":
                return self.eng.npv(self.value(node.args[0]), self.value(node.args[1]))
        raise Blocked(f"the reader cannot read `{ast.unparse(node)}`")

    def _kw(self, call: ast.Call, name: str, default: float) -> float:
        for k in call.keywords:
            if k.arg == name:
                return float(self.value(k.value))
        return default

    def statement(self, st: ast.stmt) -> None:
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant):
            return                                        # a docstring
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            self.env[st.targets[0].id] = self.value(st.value)
            return
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call):
            call, f = st.value, ast.unparse(st.value.func)
            if f == "assert_allclose":
                got, want = self.value(call.args[0]), float(self.value(call.args[1]))
                rtol, atol = self._kw(call, "rtol", 1e-7), self._kw(call, "atol", 0.0)
                ok = not isinstance(got, Refused) and abs(float(got) - want) <= atol + rtol * abs(want)
                self.checks.append(Check(ast.unparse(call.args[0]), "", f"{want!r} (rtol {rtol:g}, atol {atol:g})",
                                         _show(got), ok))
                return
            if f == "assert_equal":
                got, want = self.value(call.args[0]), self.value(call.args[1])
                self.checks.append(Check(ast.unparse(call.args[0]), "", repr(want), _show(got), got == want))
                return
        if isinstance(st, ast.Assert) and isinstance(st.test, ast.Call):
            f = ast.unparse(st.test.func)
            if f in ("numpy.isnan", "numpy.isfinite"):
                got = self.value(st.test.args[0])
                name = ast.unparse(st.test.args[0])
                if f == "numpy.isnan":
                    self.checks.append(_refuses(name, got))
                else:
                    self.checks.append(Check(name, "", "a number", _show(got), not isinstance(got, Refused)))
                return
        if isinstance(st, ast.With) and len(st.items) == 1 and \
                ast.unparse(st.items[0].context_expr.func).startswith("pytest.raises"):
            for inner in st.body:
                if not (isinstance(inner, ast.Expr) and isinstance(inner.value, ast.Call)):
                    raise Blocked(f"the reader cannot read `{ast.unparse(inner)}`")
                self.checks.append(_refuses(ast.unparse(inner.value), self.value(inner.value)))
            return
        raise Blocked(f"the reader cannot read `{ast.unparse(st)}`")


def _run_numpy(oid: str, plant: bool) -> Tuple[List[Check], Dict[str, Any]]:
    fn, case = _numpy_tests()[oid]
    missing = [n for n in _npf_calls(fn) if n not in ENGINE]
    if missing:
        raise Blocked(f"the engine has no {', '.join(missing)}")
    reader = _NumpyReader(_Engine(plant, _expects_nothing(fn)), {})
    reader.env = {k: reader.value(v) for k, v in (case or {}).items()}
    inputs = {"test": ast.unparse(fn), **{k: v for k, v in reader.env.items()}}
    for st in fn.body:
        reader.statement(st)
    return reader.checks, inputs


# ── pyxirr: the Rust test files read by their brackets ──────────────────────

_PAIRS = {"(": ")", "[": "]", "{": "}"}


def _close(text: str, i: int) -> int:
    """Index just past the bracket that closes the one at ``text[i]`` (strings skipped)."""
    stack, j = [], i
    while j < len(text):
        c = text[j]
        if c == '"':
            j = text.index('"', j + 1)
        elif c in _PAIRS:
            stack.append(_PAIRS[c])
        elif stack and c == stack[-1]:
            stack.pop()
            if not stack:
                return j + 1
        j += 1
    raise ValueError(f"unbalanced bracket at {i}")


def _split(args: str) -> List[str]:
    """Split ``args`` on its top-level commas."""
    out, depth, start, j = [], 0, 0, 0
    while j < len(args):
        c = args[j]
        if c == '"':
            j = args.index('"', j + 1)
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            out.append(args[start:j].strip())
            start = j + 1
        j += 1
    if args[start:].strip():
        out.append(args[start:].strip())
    return out


def _literal(src: str) -> Any:
    """A Rust literal as the tests write them: a number, ``f64::NAN``, a string, ``&[...]``, ``&[x; n]``."""
    s = src.strip()
    if s.startswith("&"):
        s = s[1:].strip()
    if s.startswith("["):
        body = s[1:_close(s, 0) - 1]
        if ";" in body and "[" not in body:
            v, n = body.split(";")
            return [_literal(v)] * int(_literal(n))
        return [_literal(p) for p in _split(body)]
    if s.startswith('"'):
        return s[1:-1]
    if s in ("f64::NAN", "-f64::NAN"):
        return float("nan")
    return float(s.replace("_", ""))


@lru_cache(maxsize=None)
def _rust(stem: str) -> str:
    """A pyxirr test file with its ``//`` comments removed (no string in these files holds ``//``)."""
    return re.sub(r"//[^\n]*", "", (REFERENCE / PYXIRR / f"{stem}.rs").read_text())


@dataclass(frozen=True)
class _RustTest:
    stem: str
    name: str
    params: Tuple[str, ...]
    cases: Tuple[Tuple[str, Tuple[str, ...]], ...]       # (label, raw args)
    body: str


@lru_cache(maxsize=None)
def _rust_tests(stem: str) -> Dict[str, _RustTest]:
    text, out, attrs, i = _rust(stem), {}, [], 0
    while i < len(text):
        if text.startswith("#[", i):
            j = _close(text, i + 1)
            attrs.append(text[i + 2:j - 1].strip())
            i = j
            continue
        m = re.compile(r"fn (test_\w+)\s*\(").match(text, i)
        if m:
            p_end = _close(text, m.end() - 1)
            params = tuple(re.findall(r"#\[case\]\s*(\w+)\s*:", text[m.end():p_end - 1]))
            b = text.index("{", p_end)
            body = text[b:_close(text, b)]
            cases = []
            for a in attrs:
                cm = re.match(r"case(?:::(\w+))?\s*\(", a)
                if cm:
                    cases.append((cm.group(1) or str(len(cases)), tuple(_split(a[cm.end():-1]))))
            out[m.group(1)] = _RustTest(stem, m.group(1), params, tuple(cases), body)
            attrs, i = [], _close(text, b)
            continue
        if not text[i].isspace():
            attrs = [] if text[i] not in "#" else attrs
        i += 1
    return out


@lru_cache(maxsize=None)
def _default_eps() -> float:
    """``assert_almost_eq!``'s tolerance when a test states none, read from the vendored helpers.rs."""
    m = re.search(r"let eps: f64 = ([0-9.e+-]+);", (REFERENCE / HELPERS).read_text())
    return float(m.group(1))


def _asserts(body: str) -> List[Tuple[str, str, float]]:
    """Every ``assert_almost_eq!(a, b[, eps])`` in a body: the two sides as written, and eps."""
    out = []
    for m in re.finditer(r"assert_almost_eq!\(", body):
        parts = _split(body[m.end():_close(body, m.end() - 1) - 1])
        out.append((parts[0], parts[1], float(parts[2]) if len(parts) > 2 else _default_eps()))
    return out


def _called(body: str) -> List[str]:
    return sorted(set(re.findall(r'pyxirr_call(?:_impl)?!\(\s*py,\s*"(\w+)"', body)))


def _csv(name: str) -> Tuple[List[_dt.date], List[float]]:
    """A sample file the way PaymentsLoader reads it: rows ``YYYY-MM-DD,amount``, in file order."""
    rel = f"{PYXIRR}/{name[len('tests/'):]}"
    verify(rel)
    dates, amounts = [], []
    with (REFERENCE / rel).open(newline="") as f:
        for d, a in csv.reader(f):                    # as PaymentsLoader: csv.reader, then strptime
            dates.append(_dt.datetime.strptime(d, "%Y-%m-%d").date())
            amounts.append(float(a))
    return dates, amounts


def _body_list(body: str, pattern: str) -> List[Any]:
    """The literal after ``pattern`` in a body (``PyList::new(py, [...])``, ``let x = [...]``)."""
    m = re.search(pattern, body)
    if not m:
        raise Blocked(f"the reader found no `{pattern}` in the test body")
    return _literal(body[m.end() - 1:_close(body, m.end() - 1)])


def _irr_case(t: _RustTest, args: Dict[str, Any], eng: _Engine) -> List[Check]:
    """irr of the case's stream against its answer; npv at that rate against 0 when the test checks it."""
    if "first" in args:
        stream = args["first"] + args["other"]
    elif isinstance(args["input"], str):
        stream = _csv(args["input"])[1]
    else:
        stream = args["input"]
    rate = eng.irr(stream)
    checks = []
    for a, b, eps in _asserts(t.body):
        if b == "expected":
            checks.append(_near("irr", rate, args["expected"], eps))
        elif a == "npv" and b == "0.0":
            checks.append(_near("npv at that irr", eng.npv(rate, stream), 0.0, eps))
    return checks


def _xirr_samples(t: _RustTest, args: Dict[str, Any], eng: _Engine) -> List[Check]:
    dates, amounts = _csv(args["input"])
    rate = eng.xirr(dates, amounts)
    if args["expected"] != args["expected"]:                      # NaN: pyxirr finds no rate
        return [_refuses("xirr", rate)]
    checks = []
    for a, b, eps in _asserts(t.body):
        if a == "result" and b == "expected":
            checks.append(_near("xirr", rate, args["expected"], eps))
        elif a == "xnpv" and b == "0.0" and not isinstance(rate, Refused):   # pyxirr: `if let Some(rate)`
            checks.append(_near("xnpv at that xirr", eng.xnpv(rate, dates, amounts), 0.0, eps))
    return checks


def _xnpv_samples(t: _RustTest, args: Dict[str, Any], eng: _Engine) -> List[Check]:
    dates, amounts = _csv(args["input"])
    rate = float(re.search(r"let rate = ([0-9.e+-]+);", t.body).group(1))
    (_, _, eps), = _asserts(t.body)
    return [_near(f"xnpv at {rate:g}", eng.xnpv(rate, dates, amounts), args["expected"], eps)]


def _npv_body(t: _RustTest, args: Dict[str, Any], eng: _Engine) -> List[Check]:
    """pyxirr's npv tests: values, rate and ``start_from_zero`` all read from the body."""
    values = _body_list(t.body, r"PyList::new\(py, \[")
    rate = float(re.search(r'"npv", \(([0-9.e+-]+), &values\)', t.body).group(1))
    if re.search(r'"start_from_zero" => false', t.body):
        values = [0.0] + values                                    # first flow discounted one period
    (_, want, eps), = _asserts(t.body)
    return [_near(f"npv at {rate:g}", eng.npv(rate, values), _literal(want), eps)]


def _refusal_body(t: _RustTest, args: Dict[str, Any], eng: _Engine) -> List[Check]:
    """A test that the call gives no answer (``unwrap_err`` / ``silent`` returning None)."""
    fn = _called(t.body)[0]
    if "PyList::empty" in t.body:
        values: List[float] = []
    else:
        values = _body_list(t.body, r"PyList::new\(py, \[")
    got = eng.irr(values) if fn == "irr" else eng.xirr([], values)
    return [_refuses(fn, got)]


def _xirr_day_count(t: _RustTest, args: Dict[str, Any], eng: _Engine) -> List[Check]:
    if args["day_count"] != "act/365F":
        raise Blocked(f"the engine's xirr counts actual/365 only; this case asks for {args['day_count']}")
    dates = [_dt.datetime.strptime(d, "%m/%d/%Y").date() for d in _body_list(t.body, r"let dates = \[")]
    amounts = [float(a) for a in _body_list(t.body, r"let amounts = \[")]
    (_, _, eps), = _asserts(t.body)
    return [_near("xirr act/365F", eng.xirr(dates, amounts), args["expected"], eps)]


#: how each pyxirr test that calls only the engine's four functions is read
READERS: Dict[str, Callable[[_RustTest, Dict[str, Any], _Engine], List[Check]]] = {
    "test_npv_works": _npv_body, "test_npv_start_from_zero": _npv_body, "test_npv_zero_rate": _npv_body,
    "test_irr_works": _irr_case, "test_irr_equal_payments": _irr_case, "test_irr_special_cases": _irr_case,
    "test_gh_46": _irr_case, "test_irr_samples": _irr_case, "test_irr_zeros": _refusal_body,
    "test_xirr_samples": _xirr_samples, "test_xnpv_samples": _xnpv_samples, "test_xirr_silent": _refusal_body,
    "test_xirr_day_count": _xirr_day_count,
}


def _pyxirr_oids() -> List[str]:
    out = []
    for stem in PYXIRR_FILES:
        for t in _rust_tests(stem).values():
            if not _called(t.body):
                continue                                  # tests the test macro itself (see EXCLUDED)
            base = f"pyxirr/{stem}::{t.name}"
            out += [f"{base}[{label}]" for label, _ in t.cases] if t.cases else [base]
    return out


def _run_pyxirr(oid: str, plant: bool) -> Tuple[List[Check], Dict[str, Any]]:
    stem, rest = oid[len("pyxirr/"):].split("::")
    name, _, label = rest.partition("[")
    t = _rust_tests(stem)[name]
    missing = [f for f in _called(t.body) if f not in ENGINE]
    if missing:
        raise Blocked(f"the engine has no {', '.join(missing)}")
    if name not in READERS:
        raise Blocked("no reader is written for this test's shape")
    raw = dict(t.cases)[label[:-1]] if label else ()
    args = {p: _literal(a) for p, a in zip(t.params, raw)}
    expected = args.get("expected")
    flip = (expected is not None and expected != expected) or "unwrap_err" in t.body
    checks = READERS[name](t, args, _Engine(plant, flip))
    return checks, {"test": f"{stem}.rs::{name}", **{k: v for k, v in args.items()}}


# ── the family ──────────────────────────────────────────────────────────────

class LibraryFamily(Family):
    name = "library"
    planted = ("the first engine call's first non-zero flow x 1.01; when the test expects no answer, the "
               "stream made to start negative and end positive, or npv's rate + 0.01")
    tolerance = ("each test's own: pyxirr's assert_almost_eq (|a - b| < eps, eps 1e-9 unless stated); "
                 "numpy's assert_allclose (|a - d| <= atol + rtol |d|, rtol 1e-7 unless stated)")

    def oracles(self) -> List[str]:
        return sorted(_numpy_tests()) + sorted(_pyxirr_oids())

    def source(self, oid: str) -> str:
        if oid.startswith("numpy-financial/"):
            return NUMPY
        return f"{PYXIRR}/{oid[len('pyxirr/'):].split('::')[0]}.rs"

    def run(self, oid: str, plant: bool = False) -> Tuple[List[Check], Dict[str, Any]]:
        if oid.startswith("numpy-financial/"):
            return _run_numpy(oid, plant)
        verify(HELPERS)
        return _run_pyxirr(oid, plant)
