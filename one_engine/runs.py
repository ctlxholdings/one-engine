"""Run driver: worlds layered onto a base case, and results combined across runs (module card 16).

A world is an overlay on a case document: a few declared overrides that
break the deal in one place (the esco failure panel's ten worlds, a P90
yield, a guarantor that does not pay). The base stays as it is; each run
gets its own composed copy, so the only differences between two runs are
the overrides the world declares (the card's "Never": no change between
runs except declared overrides).

World format
------------
::

    world, family, what, watch   description (``watch`` is what to look at)
    set:               {dotted.path: value}  replace a value the base HAS
    set_where_present: {dotted.path: value}  replace it only where the base has it
                                             (one world can bind several kinds of base)
    add:               {dotted.path: {key: value}}  new entries under a mapping
                                             (created if absent); an existing key refuses

A world that binds nothing refuses, and so does a ``set`` the base has no
surface for. The composed document is an ordinary case and goes through
the runner's unread-key refusal like any other. (The composer was first
written beside the failure panel's test; it moved here for the credit
pilot, which needs it outside a test.)

Combined results (credit pilot C2, 2026-10-15)
----------------------------------------------
* :func:`moved`: the inputs a world moves, by name, so a classification
  scheme (:class:`one_engine.inputs.Scheme`) can say what kind of risk the
  world is.
* :func:`exposure`: per party, the party's cut in a world less the same
  cut in the base, period by period. Who bears a risk is read from this
  table, never declared: run a world that moves the risk's input and see
  whose position falls.
"""
from __future__ import annotations

import copy
from fractions import Fraction
from typing import Any, Dict, List, Mapping, Tuple

WORLD_KEYS = {"world", "family", "what", "watch", "set", "set_where_present", "add"}


def _parent(doc: Any, dotted: str) -> Tuple[Any, str]:
    """The mapping holding the last segment of ``dotted``, or None where the path does not exist."""
    *parents, leaf = dotted.split(".")
    node = doc
    for k in parents:
        if not isinstance(node, dict) or k not in node:
            return None, leaf
        node = node[k]
    return (node if isinstance(node, dict) else None), leaf


def compose(base: Mapping[str, Any], *worlds: Mapping[str, Any]) -> Dict[str, Any]:
    """Layer world overlays onto a base case (pure data in, pure data out); see the module docstring."""
    doc = copy.deepcopy(base)
    for w in worlds:
        name = w.get("world")
        unknown = set(w) - WORLD_KEYS
        if unknown:
            raise ValueError(f"world {name!r}: unknown keys {sorted(unknown)}")
        bound = 0
        for dotted, value in (w.get("set") or {}).items():
            node, leaf = _parent(doc, dotted)
            if node is None or leaf not in node:
                raise ValueError(f"world {name!r}: the base has no {dotted} to set")
            node[leaf] = copy.deepcopy(value)
            bound += 1
        for dotted, value in (w.get("set_where_present") or {}).items():
            node, leaf = _parent(doc, dotted)
            if node is not None and leaf in node:
                node[leaf] = copy.deepcopy(value)
                bound += 1
        for dotted, entries in (w.get("add") or {}).items():
            node, leaf = _parent(doc, dotted)
            if node is None:
                raise ValueError(f"world {name!r}: the base has nowhere to add {dotted}")
            target = node.setdefault(leaf, {})
            for key, value in entries.items():
                if key in target:
                    raise ValueError(f"world {name!r}: {dotted}.{key} is already in the base")
                target[key] = copy.deepcopy(value)
                bound += 1
        if not bound:
            raise ValueError(f"world {name!r} binds nothing on this base")
    return doc


def moved(world: Mapping[str, Any]) -> List[str]:
    """The names of the inputs ``world`` writes values to, sorted, each once.

    A name is the last segment of a path whose value is written. Under
    ``add``, and inside a written mapping (a template's own overrides, such
    as a cohorts generator's ``set``), the search goes down to the values:
    ``{generators.portfolio: {set: {generators.revenue.specific_yield: 1443}}}``
    moves ``specific_yield``, not ``portfolio`` or ``set``.
    """
    names = set()

    def walk(key: str, value: Any) -> None:
        if isinstance(value, Mapping):
            for k, v in value.items():
                walk(str(k), v)
        else:
            names.add(key.split(".")[-1])

    for section in ("set", "set_where_present", "add"):
        for dotted, value in (world.get(section) or {}).items():
            walk(dotted, value)
    return sorted(names)


def exposure(base: Any, world: Any, parties: Mapping[str, Tuple[str, int]]) -> Dict[str, List[Fraction]]:
    """Each party's cut in ``world`` less the same cut in ``base``, period by period.

    ``base`` and ``world`` are case results (anything with ``cut(name)``
    returning a :class:`~one_engine.cuts.Series`). ``parties`` maps a party
    to ``(cut name, sign)``: sign 1 when the cut is the party's own
    position (its own book's cash), -1 when the cut is read from the other
    side (a lender's receipts are the borrower's debt-service payments).
    A negative entry is a period the world costs the party.

    Raises
    ------
    ValueError
        If a sign is not 1 or -1, or the two runs' cuts have different
        lengths (two runs of one deal share the clock; a mismatch means
        the world changed the assembly, not a value).
    """
    out: Dict[str, List[Fraction]] = {}
    for party, (cut, sign) in parties.items():
        if sign not in (1, -1):
            raise ValueError(f"party {party!r}: sign must be 1 or -1, got {sign!r}")
        b, w = base.cut(cut).values, world.cut(cut).values
        if len(b) != len(w):
            raise ValueError(f"party {party!r}: cut {cut!r} has {len(b)} periods in the base, {len(w)} in the world")
        out[party] = [sign * (y - x) for x, y in zip(b, w)]
    return out
