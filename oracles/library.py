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
The engine answers irr, npv, xirr, xnpv, mirr and the annuity functions pv,
fv, pmt, ipmt, ppmt, nper and rate (:mod:`one_engine.measures`). A test
that calls any other library function (xfv, xnfv, nfv, cumprinc, cumipmt)
is ``blocked`` and the reason names the functions the engine lacks. A
pyxirr case asking for a day count other than actual/365 is blocked the
same way. The reader also declines, by name, a numpy test that asks for
equality with a 28-digit ``Decimal`` (that pins Decimal's rounding at each
step, which exact arithmetic does not reproduce in the last digits), numpy's
mirr over a table of streams, and a pyxirr test built on ``pyarray!``.

numpy-financial's tests are read as Python: a ``Decimal("...")`` is the
exact number it writes, arrays broadcast as numpy broadcasts them (one
engine call per element), ``when`` is 0 / "end" or 1 / "begin". pyxirr's
annuity tests are Rust: each statement is rewritten as Python by a fixed
list of substitutions (:data:`_RUST_TO_PYTHON`) and read the same way;
``assert_future_value!`` is the test's own future value of the four
numbers, whose tolerances are read from the vendored helpers.rs.

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

For the annuity functions the change is the rate + 0.01 (rate's own plant
is the present value x 1.01), applied to every element the first call
broadcasts to. Where the test expects no answer, ipmt and ppmt are asked for
period 1 and rate is given the final value at which 10% repays the loan;
mirr's stream is flipped only when it has one sign, else scaled. ipmt in
advance at period 1 is nil at any rate, so there the period moves to 2.
"""
from __future__ import annotations

import ast
import csv
import datetime as _dt
import itertools
import re
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from one_engine import measures as M
from one_engine.oracles import REFERENCE, Blocked, Check, Family, verify

FOLDER = "library"
NUMPY = f"{FOLDER}/numpy-financial/test_financial.py"
PYXIRR = f"{FOLDER}/pyxirr/tests"
PYXIRR_FILES = ("test_periodic", "test_scheduled")
HELPERS = f"{PYXIRR}/common/helpers.rs"
#: the rate measures the pyxirr irr/npv/xirr/xnpv readers call; the annuity functions and mirr are
#: answered through :data:`SIGNATURES`
ENGINE = {"irr": M.irr, "npv": M.npv, "xirr": M.xirr, "xnpv": M.xnpv}
#: tests that are not oracles at all, with why
EXCLUDED = {
    "numpy-financial/TestNpv::test_invalid_rates_shape":
        "tests numpy's refusal of a rate array of the wrong shape; the engine takes one rate",
    "numpy-financial/TestNpv::test_invalid_cashflows_shape":
        "tests numpy's refusal of a cash-flow array of the wrong shape; the engine takes one flat stream",
    "numpy-financial/TestIpmt::test_0d_inputs":
        "tests that numpy returns a scalar rather than a 0-d array; it states no answer",
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

    def _call(self, fn: Callable, *args, **kw) -> Any:
        if any(isinstance(a, Refused) for a in args):
            return Refused("an input to this call was itself refused")
        try:
            return fn(*args, **kw)
        except M.AmbiguousRate as e:
            return Refused(str(e), tuple(e.roots))
        except M.MeasureError as e:
            return Refused(str(e))

    def begin(self) -> bool:
        """One library call starts: whether the plant applies to it (to every element it broadcasts to)."""
        return self._first()

    def irr(self, stream: List[float], plant: Optional[bool] = None) -> Any:
        if self._first() if plant is None else plant:
            stream = self._flipped(stream) if self.flip else self._scaled(stream)
        return self._call(M.irr, stream)

    def mirr(self, stream: List[float], finance: float, reinvest: float, plant: Optional[bool] = None) -> Any:
        # flipped only when the stream has no answer (one sign): numpy's mirr tests read "no answer"
        # and a number in one body, and flipping the ends of a stream that has a rate moves it too little
        if self._first() if plant is None else plant:
            one_sign = all(c >= 0 for c in stream) or all(c <= 0 for c in stream)
            stream = self._flipped(stream) if one_sign else self._scaled(stream)
        return self._call(M.mirr, stream, finance, reinvest)

    def tvm(self, name: str, kw: Dict[str, Any], plant: Optional[bool] = None) -> Any:
        """An annuity function (pv, fv, pmt, ipmt, ppmt, nper, rate) on one element's arguments.

        The plant (see the module docstring): the rate + 0.01, or for
        ``rate`` the present value x 1.01; ipmt in advance at period 1 is
        nil at any rate, so there the period moves to 2. When the test
        expects no answer: ipmt and ppmt are asked for period 1 (2 when it
        is 1 already), and
        ``rate`` gets the final value at which 10% repays the loan.
        """
        kw = dict(kw)
        if "when" in kw:
            kw["when"] = _when(kw["when"])
        if any(isinstance(v, Refused) for v in kw.values()):
            return Refused("an input to this call was itself refused")
        if self._first() if plant is None else plant:
            if name == "rate":
                if self.flip:
                    kw["fv"] = M.fv(0.1, kw["nper"], kw["pmt"], kw["pv"], kw.get("when", 0))
                else:
                    kw["pv"] = kw["pv"] * 1.01
            elif self.flip and name in ("ipmt", "ppmt"):
                kw["per"] = 2 if kw["per"] == 1 else 1
            elif name == "ipmt" and kw.get("when", 0) == 1 and kw["per"] == 1:
                kw["per"] = 2
            else:
                kw["rate"] = kw["rate"] + 0.01
        return self._call(getattr(M, name), **kw)

    def npv(self, rate: float, stream: List[float], plant: Optional[bool] = None) -> Any:
        if self._first() if plant is None else plant:
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
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_show(x) for x in v) + "]"
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
    """Whether the test's answer is "no answer" (an ``isnan``, ``pytest.raises`` or ``assert_raises``)."""
    src = ast.unparse(fn)
    return any(s in src for s in ("numpy.isnan", "math.isnan", "is_nan", "pytest.raises", "assert_raises"))


#: each numpy-financial function's arguments in order (numpy_financial 2.0.0), for positional calls
SIGNATURES = {
    "irr": ("values",), "npv": ("rate", "values"), "mirr": ("values", "finance_rate", "reinvest_rate"),
    "fv": ("rate", "nper", "pmt", "pv", "when"), "pv": ("rate", "nper", "pmt", "fv", "when"),
    "pmt": ("rate", "nper", "pv", "fv", "when"), "ipmt": ("rate", "per", "nper", "pv", "fv", "when"),
    "ppmt": ("rate", "per", "nper", "pv", "fv", "when"), "nper": ("rate", "pmt", "pv", "fv", "when"),
    "rate": ("nper", "pmt", "pv", "fv", "when"),
}
#: numpy's switch between answering NaN and raising: both are "no answer", so it is read and dropped
_DROPPED = {"raise_exceptions"}
#: Decimal's own working precision: an equality with a Decimal literal this long pins its rounding
_FLOAT_DIGITS = 17


class _Dec(Fraction):
    """A ``Decimal("...")`` literal, read as the exact number it writes, remembering how many digits."""

    def __new__(cls, text: str):
        self = super().__new__(cls, text)
        self.digits = len(re.sub(r"[^0-9]", "", text.lstrip("+-0.").split("e")[0].split("E")[0]))
        return self


class _IsNan:
    """``numpy.isnan``, ``math.isnan`` or ``Decimal.is_nan`` used as a value (bound to a name)."""


class _DecimalType:
    """The name ``Decimal`` used as a value (a parametrized number type)."""


def _when(v: Any) -> int:
    """numpy-financial's ``when``: 0 / "end" for payments in arrears, 1 / "begin" in advance."""
    if v in ("end", "begin"):
        return int(v == "begin")
    if not isinstance(v, (str, list)) and v in (0, 1):
        return int(v)
    raise Blocked(f"the reader cannot read when={v!r}")


def _shape(v: Any) -> Tuple[int, ...]:
    return (len(v),) + _shape(v[0]) if isinstance(v, (list, tuple)) and v else ((0,) if isinstance(v, (list, tuple)) else ())


def _broadcast(fn: Callable[[Dict[str, Any]], Any], kw: Dict[str, Any]) -> Any:
    """``fn`` over every element of numpy's broadcast of ``kw``'s arrays (nested lists): a scalar,
    or nested lists in the broadcast shape. Shapes align from the right; a length of 1 stretches."""
    shapes = {k: _shape(v) for k, v in kw.items()}
    rank = max((len(s) for s in shapes.values()), default=0)
    out = []
    for d in range(rank):
        sizes = {s[len(s) - rank + d] for s in shapes.values() if len(s) - rank + d >= 0} - {1}
        if len(sizes) > 1:
            raise Blocked(f"the arguments' shapes {sorted(shapes.values())} do not broadcast")
        out.append(sizes.pop() if sizes else 1)

    def pick(v: Any, s: Tuple[int, ...], idx: Tuple[int, ...]) -> Any:
        for i, n in zip(idx[rank - len(s):], s):
            v = v[0 if n == 1 else i]
        return v

    def build(prefix: Tuple[int, ...]) -> Any:
        if len(prefix) == rank:
            return fn({k: pick(v, shapes[k], prefix) for k, v in kw.items()})
        return [build(prefix + (i,)) for i in range(out[len(prefix)])]

    return build(())


def _flat(v: Any) -> List[Any]:
    return [x for e in v for x in _flat(e)] if isinstance(v, (list, tuple)) else [v]


def _is_nan(v: Any) -> bool:
    return isinstance(v, Refused) or (isinstance(v, float) and v != v)


def _pinned_decimal(*vs: Any) -> None:
    for v in vs:
        for x in _flat(v):
            if isinstance(x, _Dec) and x.digits >= _FLOAT_DIGITS:
                raise Blocked(f"the test asks for equality with a {x.digits}-digit Decimal, which pins Decimal's "
                              f"own rounding at each step; the engine computes exactly, or in floats")


class _NumpyReader:
    """Evaluates one numpy-financial test body over literals, with ``npf`` routed to the engine.

    Only the forms the tests use are read (arithmetic, tuples and lists,
    ``Decimal``, ``numpy.array`` / ``arange``, ``range``, subscripts, a
    conditional on a parametrized value, ``*args``); anything else is a
    :class:`Blocked` naming the expression, never a guess. A ``Decimal``
    is read as the exact number it writes, so a test in Decimals runs the
    engine on exact inputs. Arrays are broadcast as numpy does, one engine
    call per element; the engine functions take scalars.
    """

    def __init__(self, eng: _Engine, env: Dict[str, Any]):
        self.eng, self.env, self.checks = eng, env, []

    def _args(self, call: ast.Call) -> List[Any]:
        out: List[Any] = []
        for a in call.args:
            if isinstance(a, ast.Starred):
                out.extend(self.value(a.value))
            else:
                out.append(self.value(a))
        return out

    def npf(self, name: str, call: ast.Call) -> Any:
        """One ``npf`` call: its arguments bound by name, broadcast, each element to the engine."""
        kw = dict(zip(SIGNATURES[name], self._args(call)))
        for k in call.keywords:
            if k.arg not in _DROPPED:
                if k.arg not in SIGNATURES[name]:
                    raise Blocked(f"the engine's {name} takes no {k.arg}")
                kw[k.arg] = self.value(k.value)
        return self.call(name, kw)

    def call(self, name: str, kw: Dict[str, Any]) -> Any:
        """A library function on its arguments by name (numpy-financial's names)."""
        plant = self.eng.begin()
        if name == "irr":
            return self.eng.irr(kw["values"], plant)
        if name == "npv":
            return self.eng.npv(kw["rate"], kw["values"], plant)
        if name == "mirr":
            if any(isinstance(v, list) for v in _flat_once(kw["values"])) or \
                    isinstance(kw["finance_rate"], list) or isinstance(kw["reinvest_rate"], list):
                raise Blocked("mirr over a table of streams and rates (numpy's row-by-rate grid); "
                              "the engine's mirr takes one stream")
            return self.eng.mirr(kw["values"], kw["finance_rate"], kw["reinvest_rate"], plant)
        return _broadcast(lambda e: self.eng.tvm(name, e, plant), kw)

    def value(self, node: ast.expr) -> Any:
        ev = self.value
        if isinstance(node, ast.Constant) and (node.value is None or isinstance(node.value, (int, float, str))):
            return node.value
        if isinstance(node, (ast.List, ast.Tuple)):
            vs = [ev(e) for e in node.elts]
            return vs if isinstance(node, ast.List) else tuple(vs)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = ev(node.operand)
            return v if isinstance(v, Refused) or isinstance(node.op, ast.UAdd) else -v
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            a, b = ev(node.left), ev(node.right)
            return a if isinstance(a, Refused) else b if isinstance(b, Refused) else _OPS[type(node.op)](a, b)
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _COMPARE:
            a, b = ev(node.left), ev(node.comparators[0])
            return _COMPARE[type(node.ops[0])](a, b)
        if isinstance(node, ast.IfExp):
            return ev(node.body) if ev(node.test) else ev(node.orelse)
        if isinstance(node, ast.Subscript):
            v, i = ev(node.value), node.slice
            if isinstance(i, ast.Slice):
                return v[slice(*(None if x is None else int(ev(x)) for x in (i.lower, i.upper, i.step)))]
            return v[int(ev(i))]
        if isinstance(node, ast.Name):
            if node.id in self.env:
                return self.env[node.id]
            if node.id == "Decimal":
                return _DecimalType
            if node.id == "float":
                return float
        if isinstance(node, ast.Attribute) and ast.unparse(node) in ("numpy.nan", "numpy.inf"):
            return float(ast.unparse(node)[len("numpy."):])
        if isinstance(node, ast.Attribute) and ast.unparse(node) in ("numpy.isnan", "math.isnan", "Decimal.is_nan"):
            return _IsNan
        if isinstance(node, ast.Call):
            f = ast.unparse(node.func)
            if f.startswith("npf.") and f[4:] in SIGNATURES:
                return self.npf(f[4:], node)
            if f.startswith("npf."):
                raise Blocked(f"the engine has no {f[4:]}")
            args = self._args(node)
            fn = ev(node.func) if isinstance(node.func, ast.Name) and node.func.id in self.env else None
            if f == "Decimal" or fn is _DecimalType:
                (a,) = args
                return _Dec(a) if isinstance(a, str) else Fraction(a)
            if fn is float:
                return float(args[0])
            if f in ("numpy.isnan", "math.isnan") or fn is _IsNan:
                return _is_nan(args[0])
            if f == "numpy.array" and len(args) == 1:
                return args[0]
            if f in ("numpy.arange", "range") and len(args) in (1, 2):
                return [i for i in range(*(int(a) for a in args))]
            if f == "list" and len(args) == 1:
                return list(args[0])
            if f in ("len", "str") and len(args) == 1:
                return len(args[0]) if f == "len" else str(args[0])
            if isinstance(node.func, ast.Attribute) and node.func.attr == "split" and len(args) == 1:
                return ev(node.func.value).split(args[0])
        raise Blocked(f"the reader cannot read `{ast.unparse(node)}`")

    def _kw(self, call: ast.Call, name: str, position: int, default: Any) -> Any:
        if len(call.args) > position:
            return self.value(call.args[position])
        for k in call.keywords:
            if k.arg == name:
                return self.value(k.value)
        return default

    def _each(self, call: ast.Call, got: Any, want: Any) -> List[Tuple[str, Any, Any]]:
        """``(where, got, want)`` per element of the first two arguments, which must have one shape.

        Each argument is evaluated once, by the caller: a second evaluation
        would make a second engine call, which the plant does not reach."""
        name = ast.unparse(call.args[0])
        if not isinstance(want, (list, tuple)) and not isinstance(got, (list, tuple)):
            return [("", got, want)]
        if _shape(got) != _shape(want):
            self.checks.append(Check(name, "", f"shape {_shape(want)}", f"shape {_shape(got)}", False))
            return []
        return [(f"[{i}]", g, w) for i, (g, w) in enumerate(zip(_flat(got), _flat(want)))]

    def _check(self, call: ast.Call, ok: Callable[[Any, Any], bool], shown: Callable[[Any], str],
               got: Any = None, want: Any = None, evaluated: bool = False) -> None:
        if not evaluated:
            got, want = self.value(call.args[0]), self.value(call.args[1])
        for at, got, want in self._each(call, got, want):
            self.checks.append(Check(ast.unparse(call.args[0]), at, shown(want), _show(got), ok(got, want)))

    def statement(self, st: ast.stmt) -> None:
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant):
            return                                        # a docstring
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            self.env[st.targets[0].id] = self.value(st.value)
            return
        if isinstance(st, ast.If):
            for inner in (st.body if self.value(st.test) else st.orelse):
                self.statement(inner)
            return
        if isinstance(st, ast.With) and len(st.items) == 1 and \
                ast.unparse(st.items[0].context_expr.func) == "numpy.errstate":
            for inner in st.body:                         # numpy's floating-point warnings: no answer in it
                self.statement(inner)
            return
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call):
            call, f = st.value, ast.unparse(st.value.func)
            if f == "assert_allclose":
                rtol = float(self._kw(call, "rtol", 2, 1e-7))
                atol = float(self._kw(call, "atol", 3, 0.0))
                self._check(call, lambda g, w: (_is_nan(g) and _is_nan(w)) or (
                    not _is_nan(g) and not _is_nan(w) and abs(float(g) - float(w)) <= atol + rtol * abs(float(w))),
                    lambda w: f"{_show(w)} (rtol {rtol:g}, atol {atol:g})")
                return
            if f == "assert_decimal_close":
                tol = self._kw(call, "tol", 2, Fraction(1, 10 ** 7))
                self._check(call, lambda g, w: not isinstance(g, Refused) and abs(g - w) <= tol,
                            lambda w: f"{_show(w)} (within {float(tol):g})")
                return
            if f == "assert_equal":
                got, want = self.value(call.args[0]), self.value(call.args[1])
                _pinned_decimal(got, want)
                self._check(call, lambda g, w: (_is_nan(g) and _is_nan(w)) or (
                    not isinstance(g, Refused) and not isinstance(w, Refused) and g == w), _show, got, want, True)
                return
            if f == "assert_raises" and ast.unparse(call.args[1]).startswith("npf."):
                fake = ast.Call(func=call.args[1], args=call.args[2:], keywords=call.keywords)
                self.checks.append(_refuses(ast.unparse(fake), _raised(self.value(fake))))
                return
            if f == "assert_" and len(call.args) == 1:
                self.statement(ast.Assert(test=call.args[0]))
                return
        if isinstance(st, ast.Assert) and isinstance(st.test, ast.Call):
            f, fn = ast.unparse(st.test.func), None
            if isinstance(st.test.func, ast.Name) and st.test.func.id in self.env:
                fn = self.env[st.test.func.id]
            if f in ("numpy.isnan", "math.isnan") or fn is _IsNan:
                self.checks.append(_refuses(ast.unparse(st.test.args[0]), self.value(st.test.args[0])))
                return
            if f == "numpy.isfinite":
                got = self.value(st.test.args[0])
                self.checks.append(Check(ast.unparse(st.test.args[0]), "", "a number", _show(got),
                                         not isinstance(got, Refused)))
                return
        if isinstance(st, ast.Assert) and isinstance(st.test, ast.Compare) and len(st.test.ops) == 1 \
                and isinstance(st.test.ops[0], ast.Eq):
            got, want = self.value(st.test.left), self.value(st.test.comparators[0])
            _pinned_decimal(got, want)
            self.checks.append(Check(ast.unparse(st.test.left), "", _show(want), _show(got),
                                     not isinstance(got, Refused) and got == want))
            return
        if isinstance(st, ast.With) and len(st.items) == 1 and \
                ast.unparse(st.items[0].context_expr.func).startswith("pytest.raises"):
            for inner in st.body:
                if not (isinstance(inner, ast.Expr) and isinstance(inner.value, ast.Call)):
                    raise Blocked(f"the reader cannot read `{ast.unparse(inner)}`")
                self.checks.append(_refuses(ast.unparse(inner.value), _raised(self.value(inner.value))))
            return
        raise Blocked(f"the reader cannot read `{ast.unparse(st)}`")


def _raised(got: Any) -> Any:
    """What a ``raises`` check sees of a broadcast call: numpy raises when any element has no answer."""
    for x in _flat(got):
        if isinstance(x, Refused):
            return x
    return got


def _flat_once(v: Any) -> List[Any]:
    return list(v) if isinstance(v, (list, tuple)) else [v]


_OPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b, ast.Pow: lambda a, b: a ** b}
_COMPARE = {ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b,
            ast.Is: lambda a, b: a is b, ast.IsNot: lambda a, b: a is not b}


def _run_numpy(oid: str, plant: bool) -> Tuple[List[Check], Dict[str, Any]]:
    fn, case = _numpy_tests()[oid]
    missing = [n for n in _npf_calls(fn) if n not in SIGNATURES]
    if missing:
        raise Blocked(f"the engine has no {', '.join(missing)}")
    reader = _NumpyReader(_Engine(plant, _expects_nothing(fn)), {})
    reader.env = {k: reader.value(v) for k, v in (case or {}).items()}
    inputs = {"test": ast.unparse(fn), **{k: _show_input(v) for k, v in reader.env.items()}}
    for st in fn.body:
        reader.statement(st)
    return reader.checks, inputs


def _show_input(v: Any) -> Any:
    """An input as the receipt's JSON can hold it (a Decimal or fraction as its text)."""
    if isinstance(v, (list, tuple)):
        return [_show_input(x) for x in v]
    if isinstance(v, Fraction):
        return str(v)
    if v is _DecimalType:
        return "Decimal"
    if v is float:
        return "float"
    return v


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


# ── pyxirr's annuity tests: each Rust statement rewritten as Python, then read as numpy's are ──

#: pyxirr's keyword for numpy-financial's ``when`` (a bool, or a list of them), and its switch between
#: raising and answering None (both "no answer", so dropped)
_PYX_KEYWORDS = {"pmt_at_beginning": "when", "silent": None}
#: the Rust forms the annuity tests use, each as the Python the numpy reader reads (applied in order)
_RUST_TO_PYTHON = [
    (r'pyxirr_call(?:_impl)?!\(\s*py,\s*"(\w+)",', r'pyx("\1",'),
    (r'py_dict!\(\s*py,\s*"(\w+)"\s*=>\s*', r'pyd("\1", '),
    (r'PyList::new\(py,\s*(\[[^\]]*\])\)\.unwrap\(\)', r'\1'),
    (r'py\.eval\(c_str!\("(range\([^"]*\))"\),\s*None,\s*None\)\.unwrap\(\)', r'list(\1)'),
    (r'\((\w+)\.\.=(\w+)\)\.collect::<Vec<_>>\(\)', r'list(range(\1, \2 + 1))'),
    (r'\((\w+)\.\.(\w+)\)\.collect::<Vec<_>>\(\)', r'list(range(\1, \2))'),
    (r'\.unwrap_err\(\)', ''),
    (r'(\w+(?:\[\w+\])*)\.is_instance_of::<[\w:]+>\(py\)', r'isnan(\1)'),
    (r'(\w+(?:\[\w+\])*)\.is_(?:none|nan)\(\)', r'isnan(\1)'),
    (r'(\w+)\.len\(\)', r'len(\1)'),
    (r'f64::NAN', 'nan'),
    (r'\btrue\b', 'True'),
    (r'\bfalse\b', 'False'),
    (r'&(\w)', r'\1'),
    (r'\bSome\(([^()]*)\)', r'\1'),
]


def _python(rust: str) -> ast.expr:
    """One Rust expression of the annuity tests, rewritten (:data:`_RUST_TO_PYTHON`) and parsed."""
    text = rust.strip()
    for pattern, repl in _RUST_TO_PYTHON:
        text = re.sub(pattern, repl, text)
    try:
        return ast.parse(text, mode="eval").body
    except SyntaxError:
        raise Blocked(f"the reader cannot read `{rust.strip()}`") from None


@lru_cache(maxsize=None)
def _constants() -> Dict[str, float]:
    """The file's ``const NAME: f64 = x;`` lines."""
    return {k: float(v.replace("_", "")) for k, v in re.findall(r"const (\w+): f64 = ([0-9._eE+-]+);", _rust("test_periodic"))}


@lru_cache(maxsize=None)
def _future_value_eps() -> Tuple[float, float]:
    """``assert_future_value!``'s two tolerances (zero rate, other rates), read from helpers.rs."""
    m = re.search(r"macro_rules! assert_future_value(.*?)\n}", (REFERENCE / HELPERS).read_text(), re.S).group(1)
    zero = re.search(r"assert_almost_eq!\(fv \+ pv \+ pmt \* nper, 0\.0\)", m)
    other = re.search(r"assert_almost_eq!\(result, 0\.0, ([0-9.e+-]+)\)", m)
    if not (zero and other):
        raise Blocked("helpers.rs's assert_future_value! is not in the form the reader reads")
    return _default_eps(), float(other.group(1))


class _PyxirrReader(_NumpyReader):
    """The numpy reader over pyxirr statements: ``pyx(name, args, kwargs)`` is a pyxirr call."""

    def value(self, node: ast.expr) -> Any:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "pyd":
            key, val = (self.value(a) for a in node.args)
            return {key: val}
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "pyx":
            name = node.args[0].value
            if name not in SIGNATURES:
                raise Blocked(f"the engine has no {name}")
            args = self.value(node.args[1])
            kw = dict(zip(SIGNATURES[name], args if isinstance(args, tuple) else (args,)))
            for k, v in (self.value(node.args[2]) if len(node.args) > 2 else {}).items():
                if k not in _PYX_KEYWORDS:
                    raise Blocked(f"the reader cannot read pyxirr's {k}")
                if _PYX_KEYWORDS[k] is not None:
                    kw[_PYX_KEYWORDS[k]] = v
            return self.call(name, kw)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "isnan":
            return _is_nan(self.value(node.args[0]))
        if isinstance(node, ast.Name) and node.id in _constants():
            return _constants()[node.id]
        if isinstance(node, ast.Name) and node.id == "nan":
            return float("nan")
        if isinstance(node, ast.Name) and node.id == "None":
            return None
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "range":
            return list(range(*(int(self.value(a)) for a in node.args)))
        return super().value(node)

    def run(self, body: str) -> None:
        """The statements of a ``Python::with_gil(|py| { ... })`` body, in order."""
        m = re.search(r"Python::with_gil\(\|py\|\s*\{", body)
        if not m:
            raise Blocked("the reader found no Python::with_gil body")
        inner = body[m.end():_close(body, m.end() - 1) - 1]
        self._block(inner)

    def _block(self, text: str) -> None:
        i = 0
        while i < len(text):
            if text[i].isspace() or text[i] == ";":
                i += 1
                continue
            fm = re.compile(r"for (\w+) in 0\.\.(\w+(?:\.len\(\))?)\s*\{").match(text, i)
            if fm:
                end = _close(text, fm.end() - 1)
                for k in range(int(self.value(_python(fm.group(2))))):
                    self.env[fm.group(1)] = k
                    self._block(text[fm.end():end - 1])
                self.env.pop(fm.group(1), None)
                i = end
                continue
            j = i
            while j < len(text) and text[j] != ";":
                j = _close(text, j) if text[j] in "([{" else (text.index('"', j + 1) + 1 if text[j] == '"' else j + 1)
            self._statement(text[i:j].strip())
            i = j + 1

    def _statement(self, st: str) -> None:
        if st.startswith("let "):
            m = re.match(r"let (\w+)\s*(?::[^=]+)?=\s*(.*)$", st, re.S)
            self.env[m.group(1)] = self.value(_python(m.group(2)))
            return
        mm = re.match(r"match (\w+\[\w+\])\s*\{\s*Some\((\w+)\)\s*=>\s*assert_almost_eq!\((\w+),\s*(\w+\[\w+\])"
                      r"(?:,\s*([0-9.e+-]+))?\),\s*None\s*=>\s*assert!\(\4\.is_nan\(\)\),?\s*\}$", st, re.S)
        if mm and mm.group(2) == mm.group(3):
            got, want = self.value(_python(mm.group(1))), self.value(_python(mm.group(4)))
            eps = float(mm.group(5)) if mm.group(5) else _default_eps()
            at = f"[{self.env.get('i')}]"
            self.checks.append(_refuses(mm.group(1), got, at) if want != want else _near(mm.group(1), got, want, eps, at))
            return
        fe = re.match(r"(\w+)\.into_iter\(\)\.for_each\(\|(\w+)\|\s*assert!\(\2\.is_none\(\)\)\)$", st)
        if fe:
            for k, x in enumerate(_flat_once(self.env[fe.group(1)])):
                self.checks.append(_refuses(fe.group(1), x, f"[{k}]"))
            return
        m = re.match(r"(assert_almost_eq|assert_future_value|assert|assert_eq)!\((.*)\)$", st, re.S)
        if not m:
            raise Blocked(f"the reader cannot read `{st}`")
        parts = _split(m.group(2))
        at = f"[{self.env['i']}]" if "i" in self.env else ""
        if m.group(1) == "assert_almost_eq":
            got, want = self.value(_python(parts[0])), self.value(_python(parts[1]))
            eps = float(self.value(_python(parts[2]))) if len(parts) > 2 else _default_eps()
            self.checks.append(_near(parts[0], got, want, eps, at))
        elif m.group(1) == "assert_eq" and parts[1] == "None":
            self.checks.append(_refuses(parts[0], self.value(_python(parts[0]))))
        elif m.group(1) == "assert" and re.fullmatch(r"\w+(?:\[\w+\])*\.(?:is_none\(\)|is_instance_of::<[\w:]+>\(py\))",
                                                      parts[0]):
            name = parts[0].split(".")[0]
            self.checks.append(_refuses(name, self.value(_python(name)), at))
        elif m.group(1) == "assert_future_value":
            self._future_value(parts)
        else:
            raise Blocked(f"the reader cannot read `{st}`")

    def _future_value(self, parts: List[str]) -> None:
        """helpers.rs's ``assert_future_value!(rate, nper, pmt, pv, fv, pmt_at_beginning)``: the
        test's own future value of the four (Option arguments as None or Some) is 0."""
        rate, nper, pmt_, pv_, fv_, begin = (self.value(_python(p)) for p in parts)
        name = next(p for p in parts[:4] if not re.fullmatch(r"[A-Z_]+|[0-9.]+", p))
        if any(isinstance(v, Refused) for v in (rate, nper, pmt_, pv_)):
            bad = next(v for v in (rate, nper, pmt_, pv_) if isinstance(v, Refused))
            self.checks.append(Check(name, "", "the future value is 0", _show(bad), False))
            return
        fv_ = 0.0 if fv_ is None else float(fv_)
        zero_eps, eps = _future_value_eps()
        if rate == 0:
            residual, tol = fv_ + pv_ + pmt_ * nper, zero_eps
        else:
            w = 1.0 if begin else 0.0
            g = (1.0 + rate) ** nper
            residual, tol = fv_ + pv_ * g + pmt_ * (1.0 + rate * w) / rate * (g - 1.0), eps
        self.checks.append(Check(name, "", f"the future value is 0 (within {tol:g})", repr(residual),
                                 abs(residual) < tol))


def _annuity_body(t: _RustTest, args: Dict[str, Any], eng: _Engine) -> List[Check]:
    reader = _PyxirrReader(eng, {})
    reader.run(t.body)
    return reader.checks


#: how each pyxirr test of irr, npv, xirr or xnpv is read (an annuity test is read by :func:`_annuity_body`)
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
    missing = [f for f in _called(t.body) if f not in ENGINE and f not in SIGNATURES]
    if missing:
        raise Blocked(f"the engine has no {', '.join(missing)}")
    annuity = all(f in SIGNATURES and f not in ("irr", "npv") for f in _called(t.body))
    if name not in READERS and not annuity:
        raise Blocked("no reader is written for this test's shape")
    raw = dict(t.cases)[label[:-1]] if label else ()
    args = {p: _literal(a) for p, a in zip(t.params, raw)}
    expected = args.get("expected")
    flip = (expected is not None and expected != expected) or any(
        s in t.body for s in ("unwrap_err", "is_none()", "is_nan()", "assert_eq!(result, None)"))
    checks = READERS.get(name, _annuity_body)(t, args, _Engine(plant, flip))
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
