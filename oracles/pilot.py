"""The golds library's pilot golds as an oracle family: hand golds not yet promoted.

The oracle
----------
``cases/reference/golds_pilot/<id>.yaml``, vendored unmodified from Ali's golds
library (``one-place/pilot``). The library's README calls ``pilot/`` its
staging area: a new gold lands there, clears the library's three-leg gate,
and is promoted to ``cases/`` on Ali's word. These have not been promoted.
Several are earlier versions of a promoted gold (``grain_v25`` and
``grain_v26`` beside ``cases/grain``) or of each other (``_v25``, ``_v26``
follow the library's gold contract versions 2.5 and 2.6); each version is its
own derivation, so each is its own oracle.

A pilot gold has the same layout as a promoted one (``inputs``, ``passage``,
``trace``, ``fcff``), so everything else, translations, readings, checks,
tolerance and the planted error, is :mod:`one_engine.oracles.golds`'s.
Because a pilot has not cleared the library's own gate, a difference may be
the pilot's; as for every oracle, it is reported, never fitted.

Translation
-----------
``cases/golds_pilot/<id>.yaml``, keyed ``gold: <id>`` like a promoted gold's.
"""
from __future__ import annotations

import pathlib

from one_engine.oracles.golds import GoldFamily

FOLDER = "golds_pilot"
TRANSLATIONS = pathlib.Path(__file__).resolve().parent.parent / "cases" / "golds_pilot"


class PilotFamily(GoldFamily):
    """The pilot golds (see the module docstring)."""

    name = "pilot"
    folder = FOLDER
    library_folder = "pilot"

    def __init__(self, translations: pathlib.Path = TRANSLATIONS):
        super().__init__(translations)
