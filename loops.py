"""Loop solver: closes declared fixed points across runs (module card 15).

What a loop is
--------------
Some figures of an assembly depend on the run they feed. A sculpted loan's
size is the present value of the debt service that future CFADS can carry,
yet the loan is drawn in construction, before that CFADS exists in any
forward pass. The assembly therefore runs with a stated value for the
figure (the loop's ``start``), a rule reads the finished run and returns a
new value, and the assembly runs again with it, until the value a run
uses and the value it returns agree.

That is the "loop" half of the debt-structuring seam (consolidation step 3,
``experiments/sculpt_seam.py``): the sculpting rule is pack data, the
sizing scalar within one run is this solver's fixed point, and choosing a
tenor or a gearing across runs is the Run driver's search.

What it owns
------------
* **Declared loops only.** Each :class:`Loop` names its outputs and their
  starting values. A loop variable no term reads, or a term that reads a
  loop nobody declared, is refused by the caller that wires terms (the
  case runner); this module refuses a rule whose outputs are not the
  declared ones.
* **A fresh run per iteration.** ``run`` builds the assembly from its
  inputs each time; nothing carries from one iteration to the next except
  the loop values themselves (the generic-framework compounding scar: a
  solver that mutated ledgers between passes added a movement twice).
* **Residuals reported, non-convergence stops.** Each loop's residual is
  the largest absolute change of any of its outputs between the value a
  run used and the value its rule returned. The run is accepted when every
  residual is at or below its tolerance; when a loop reaches its iteration
  limit still above tolerance, :class:`LoopError` stops the run and names
  the residuals.

Arithmetic
----------
Loop values are carried as floats between iterations (solvers compute in
float, the step 3 arithmetic decision); each enters the next run as the
exact fraction of that float. A tolerance of 0 then asks that the rule
return, bit for bit, the float the run used: possible when the rule's
inputs do not depend on the loop (unlevered CFADS), and impossible
otherwise, where a positive tolerance is declared.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generic, List, Mapping, Sequence, Tuple, TypeVar

R = TypeVar("R")
LoopValues = Dict[str, Any]          # output name -> float, or a list of floats


class LoopError(RuntimeError):
    """A loop is malformed, or did not converge within its iteration limit."""


@dataclass(frozen=True)
class Loop:
    """One declared fixed point.

    Attributes
    ----------
    name:
        The loop's name; terms refer to its outputs as ``{loop: name, output: ...}``.
    start:
        Output name -> starting value (a number, or a list for a series).
        The names are the loop's outputs; the rule must return exactly them.
    rule:
        Reads a finished run and returns the outputs' new values.
    tolerance:
        The largest residual accepted (absolute, in the outputs' units).
    max_iterations:
        Runs allowed before the loop is declared not to converge.
    """

    name: str
    start: Mapping[str, Any]
    rule: Callable[[Any], Mapping[str, Any]]
    tolerance: float
    max_iterations: int

    def __post_init__(self):
        if not self.start:
            raise LoopError(f"loop '{self.name}' declares no output")
        if self.max_iterations < 1:
            raise LoopError(f"loop '{self.name}': max_iterations must be at least 1")
        if self.tolerance < 0:
            raise LoopError(f"loop '{self.name}': tolerance must not be negative")


@dataclass
class LoopReport:
    """How one loop closed: the values the accepted run used, its residual and the runs it took."""

    name: str
    values: LoopValues
    residual: float
    iterations: int
    history: List[float] = field(default_factory=list)      # residual after each run


def _floats(x: Any) -> Any:
    return [float(v) for v in x] if isinstance(x, (list, tuple)) else float(x)


def _residual(old: Any, new: Any, where: str) -> float:
    """Largest absolute change between two values of one output.

    A number as the start of a series stands for that number in every
    period (a schedule starting at zero); a series never turns back into a
    number.
    """
    if not isinstance(old, list) and isinstance(new, list):
        old = [old] * len(new)
    if isinstance(old, list) != isinstance(new, list):
        raise LoopError(f"{where}: a series and a number cannot be compared")
    if isinstance(old, list):
        if len(old) != len(new):
            raise LoopError(f"{where}: series of {len(old)} and {len(new)} values")
        return max((abs(a - b) for a, b in zip(old, new)), default=0.0)
    return abs(old - new)


def close(loops: Sequence[Loop], run: Callable[[Mapping[str, LoopValues]], R]) -> Tuple[R, Dict[str, LoopReport]]:
    """Run until every loop's rule returns the values its run used, within tolerance.

    Parameters
    ----------
    loops:
        The declared loops, closed jointly: each run uses every loop's
        current values, and each rule reads the same finished run.
    run:
        Builds and runs the assembly from ``{loop name: {output: value}}``.
        It must build afresh each call.

    Returns
    -------
    The accepted run, and a :class:`LoopReport` per loop.

    Raises
    ------
    LoopError
        A rule returns outputs other than those declared, or a loop reaches
        its iteration limit with its residual above tolerance.
    """
    names = [lp.name for lp in loops]
    if len(set(names)) != len(names):
        raise LoopError(f"loop names repeat: {names}")
    values = {lp.name: {k: _floats(v) for k, v in lp.start.items()} for lp in loops}
    history: Dict[str, List[float]] = {n: [] for n in names}
    it = 0
    while True:
        it += 1
        result = run({n: dict(v) for n, v in values.items()})     # copies: the run cannot edit the loop state
        new, residual = {}, {}
        for lp in loops:
            out = lp.rule(result)
            if set(out) != set(lp.start):
                raise LoopError(f"loop '{lp.name}': rule returned {sorted(out)}, declared {sorted(lp.start)}")
            new[lp.name] = {k: _floats(v) for k, v in out.items()}
            residual[lp.name] = max(_residual(values[lp.name][k], new[lp.name][k], f"loop '{lp.name}'.{k}")
                                    for k in lp.start)
            history[lp.name].append(residual[lp.name])
        open_loops = [lp for lp in loops if residual[lp.name] > lp.tolerance]
        if not open_loops:
            return result, {lp.name: LoopReport(lp.name, values[lp.name], residual[lp.name], it, history[lp.name])
                            for lp in loops}
        spent = [lp for lp in open_loops if it >= lp.max_iterations]
        if spent:
            raise LoopError("did not converge: " + "; ".join(
                f"loop '{lp.name}' residual {residual[lp.name]:.6g} above tolerance {lp.tolerance:g} "
                f"after {it} run(s)" for lp in spent))
        values = new
