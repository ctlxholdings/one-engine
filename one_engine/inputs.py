"""Inputs read once, with their state and source (module card 2).

Three invariants from the consolidation map (§8) start here:

* **One source per number.** A key written twice in a source file is an
  error. YAML loaders keep the last duplicate and say nothing, which is how
  one number can silently have two values. :func:`load_yaml_strict` refuses.
* **Nothing silent at intake.** A key that nobody reads is an error, raised
  by :meth:`Reader.finish`. Planted case: the nightmare fixture's
  ``financial.promote`` block, which the old loader never read because it
  looks for ``promote_terms``. The run went ahead without a promote.
* **State travels.** Every value has a :class:`State`. A default is never
  passed off as a stated value: :meth:`Reader.take_or_default` tags it
  ``DEFAULT`` and records why. Downstream, the kernel keeps the state on
  each posting (:mod:`one_engine.kernel`), and Reports must show it.

Classes on inputs (credit pilot C1, 2026-10-15): a :class:`Scheme` maps
names to classes. Cuts classify accounts with it (:mod:`one_engine.cuts`);
the same type classifies inputs and contract terms by name, so a credit
scheme can say which risk kind each input moves (``specific_yield`` ->
``revenue_quantity``). An input the scheme does not classify refuses, as an
unclassified account refuses a cut.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterable, Mapping


class InputError(ValueError):
    """An input is missing, unknown, duplicated or unread."""


class State(enum.Enum):
    """Where a value stands. The names follow esco-product's origin tags.

    STATED      given by a document or a person, with a source.
    DERIVED     computed by the engine from other values.
    DEFAULT     not given; a declared default was used, with a reason.
    STAND_IN    a placeholder (proxy capex, a guessed price) that must not
                reach a headline figure unflagged.
    NOT_PRINTED the source does not give it, and no value is invented.
    """

    STATED = "stated"
    DERIVED = "derived"
    DEFAULT = "default"
    STAND_IN = "stand_in"
    NOT_PRINTED = "not_printed"


@dataclass(frozen=True)
class Value:
    """An input value with its state and where it came from."""

    value: Any
    state: State
    source: str


@dataclass(frozen=True)
class Scheme:
    """A classification scheme: name -> class name (accounts for cuts, inputs for a credit reading)."""

    name: str
    classes: Mapping[str, str]

    @property
    def class_names(self) -> FrozenSet[str]:
        return frozenset(self.classes.values())

    def classify(self, names: Iterable[str]) -> Dict[str, str]:
        """The class of each name; a name the scheme does not classify refuses (nothing drops out)."""
        names = list(names)
        missing = sorted(n for n in names if n not in self.classes)
        if missing:
            raise InputError(f"scheme '{self.name}' does not classify: {', '.join(missing)}")
        return {n: self.classes[n] for n in names}


class Reader:
    """Read a mapping of inputs and account for every key.

    Usage::

        r = Reader(yaml_dict, source="params.yaml")
        rate = r.take("tax_rate")
        lockup = r.take_or_default("dscr_lockup", 1.20, reason="term sheet silent")
        r.finish()     # raises if any key was never read

    Reading the same key twice is allowed: two modules may legitimately need
    one number. What is refused is a key that no module reads.
    """

    def __init__(self, data: Mapping[str, Any], source: str):
        if not isinstance(data, Mapping):
            raise InputError(f"{source}: expected a mapping, got {type(data).__name__}")
        self._data = dict(data)
        self._source = source
        self._read: set[str] = set()

    def take(self, key: str) -> Any:
        """Return the stated value of ``key``; refuse if it is absent."""
        return self.take_value(key).value

    def take_value(self, key: str) -> Value:
        """Return ``key`` as a :class:`Value` tagged STATED with its source."""
        if key not in self._data:
            raise InputError(f"{self._source}: required input '{key}' is missing")
        self._read.add(key)
        return Value(self._data[key], State.STATED, f"{self._source}:{key}")

    def take_or_default(self, key: str, default: Any, reason: str) -> Value:
        """Return ``key`` if given, else ``default`` tagged DEFAULT with ``reason``.

        ``reason`` is required: a default with no stated reason is exactly the
        silent fallback this module exists to prevent.
        """
        if not reason:
            raise InputError(f"{self._source}: a default for '{key}' needs a reason")
        if key in self._data:
            return self.take_value(key)
        return Value(default, State.DEFAULT, f"{self._source}:{key} defaulted ({reason})")

    def finish(self) -> None:
        """Refuse if any key was never read."""
        unread = sorted(set(self._data) - self._read)
        if unread:
            raise InputError(f"{self._source}: inputs given but read by nothing: {', '.join(unread)}")


def load_yaml_strict(text: str) -> Any:
    """Parse YAML, refusing duplicate keys in any mapping; decimals stay exact."""
    import yaml

    class _Strict(yaml.SafeLoader):
        pass

    def _construct(loader, node, deep=False):
        # A merge (``<<: *opex``) is how a shared number stays one number
        # across several mappings; keys written next to it override it on
        # purpose. Only keys written twice explicitly are refused.
        seen = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = loader.construct_object(key_node, deep=deep)
            if key in seen:
                raise InputError(f"key '{key}' is written twice (line {key_node.start_mark.line + 1})")
            seen.add(key)
        return loader.construct_mapping(node, deep=deep)

    def _decimal(loader, node):
        # A number typed in a file is a decimal: 0.108 means 108/1000, not the
        # float nearest to it. Keeping it as Decimal lets units.exact() take
        # it exactly.
        from decimal import Decimal
        return Decimal(loader.construct_scalar(node).replace("_", ""))

    _Strict.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct)
    _Strict.add_constructor("tag:yaml.org,2002:float", _decimal)
    return yaml.load(text, Loader=_Strict)
