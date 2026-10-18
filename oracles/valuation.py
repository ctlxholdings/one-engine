"""McKinsey *Valuation* (5th ed., 2010) as an oracle family: the book's exhibits, run through the books.

The book
--------
Ali's approved copy, as the text the PDF extracts to (``N_1.pdf.txt``, sha-256
``BOOK_SHA256``). It is copyrighted and is never vendored whole: the
repository keeps only *excerpts*, the exact lines of each exhibit, and the
book itself stays in the project's shared folder (``BOOK_FILE``, or the path
in ``$VALUATION_BOOK``). :func:`read_book` refuses a book whose hash moved.

The oracle
----------
``cases/reference/valuation/<excerpt>.txt``: the exhibit's lines exactly as
the book prints them (one or several ranges of book lines; an exhibit that
continues across a page, or the three exhibits of one worked company). The
manifest records the excerpt's hash and, in its origin, the book lines it
came from (``book lines a-b, c-d``); ``tests/test_oracles_valuation.py``
re-reads those lines from the book wherever the book is present, so an
excerpt that is not the book's text fails. The excerpts are vendored by
:func:`vendor_excerpt`, never by hand.

What the family reads from an excerpt
-------------------------------------
The text is read mechanically, the way a reader reads a table: a *row* is a
label followed by its numbers. A number is a whole token: ``9,790``,
``(1,027)`` (negative), ``37.6%``, ``$50``; a token of dashes is one nil per
dash (``–––`` is three nil cells, as the text extraction glues them). A bare
integer of one or two digits at the start of a line followed by text is a
footnote mark, not a figure (``67% probability ...`` opens with a figure). Every other number in the excerpt is a *printed figure*, and every
printed figure must be accounted for: read by a row, or on a line the
translation sets aside with a reason (column headings, footnote prose). An
unaccounted figure is a failed check, so a translation cannot skip the
inconvenient rows. The accounting is by cell: a row that takes the prior
year of a run leaves its current year unaccounted until another row takes
it (a balance read once as an input and once as a figure to compare).

Translation
-----------
``cases/valuation/<oracle>.yaml``. An excerpt that prints two companies
side by side is two oracles, ``<excerpt>@<company>``, each reading its own
columns. Keys:

* ``excerpt``: the excerpt it reads;
* ``periods``: the case periods each row's numbers fall in, in order (the
  default for every row); the case may have more periods than any row;
* ``rows``: name -> the row's label as printed (with its footnote mark if
  glued to it), or ``{label, occurrence, continued, periods, scale, skip}``
  or ``{line, periods, scale, skip}``. ``line``: every cell of that excerpt
  line (1-based), a line of numbers only, such as a chart's data labels.
  ``continued``: the run goes on over that many following lines of numbers
  only (a row the extraction broke). A number printed inside a label
  ("Debt-to-equity in 2006") is part of the label, accounted for by it, and
  listed in the receipt. Further keys:
  ``occurrence`` the k-th time the label opens a run of numbers in the
  excerpt (default 1; the toy exhibits print two statements side by side,
  so a label recurs on one line), ``periods`` this row's own periods,
  ``scale`` a power of ten the printed number is multiplied by to give the
  engine's amount (0.01 for a percent), ``skip`` how many numbers after the
  label belong to other columns (the second company of a side-by-side
  exhibit);
* ``inputs``: the rows handed to the case. A ``formulas`` (or
  ``valuation_formulas``: the book's formulas callable by name) generator with
  ``inputs: exhibit`` receives each as a series over the case periods (nil
  outside the row's periods) and, for each of the row's periods ``p``, as
  the number ``<row>__p``;
* ``case``: a one_engine case. The only numbers typed in it are structural
  (:data:`one_engine.oracles.golds.STRUCTURAL`), a reading's sign, and the
  365 days of a calendar year (:data:`DAYS_PER_YEAR`, for the book's
  days-based ratios); any other fails the ``translation traced`` check;
* ``entity``: whose books the figures are read from;
* ``figures``: row -> where the engine reports it, any reading of
  :meth:`GoldFamily._read <one_engine.oracles.golds.GoldFamily._read>`
  (``cut``, ``balance``, ``opening``, ``change``, ``driver``, ``sum``,
  ``ratio``, ...), read at the row's periods;
* ``not_compared``: row -> why it is read but not compared (a figure the
  book takes from another exhibit the case does not model);
* ``set_aside``: line text (the line as printed, stripped) -> why its
  numbers are no figures;
* ``columns_of``: for a side-by-side exhibit, the other companies whose
  columns share its rows (``[lowes]``): their cells count as accounted for
  only as the other company's own translation reads them;
* ``plant``: the input row the planted run changes; ``notes``.

Checks, per oracle
------------------
* ``translation traced``: no number typed in the case or the readings;
* ``every printed figure accounted for``;
* one check per figure row, each number within one unit of the last digit
  printed (the book rounds each line independently, so a total can sit a
  unit away from the sum of its printed parts);
* ``books``: the integrity checks of every book and each rulebook's checks.

A row a translation declares but neither feeds, compares nor sets aside is a
failed check ("not reported").

Planted error
-------------
The input row named by ``plant``, each of its printed periods moved 1% away
from nil, or by ten units of its last printed digit where that is more (a
row of small percents, or a nil), given to the translation; the excerpt is
untouched.
"""
from __future__ import annotations

import contextlib
import copy
import fcntl
import hashlib
import json
import os
import pathlib
import re
import tempfile
from fractions import Fraction
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from one_engine.inputs import load_yaml_strict
from one_engine.oracles import MANIFEST, REFERENCE, Blocked, Check, Family, OracleChanged, sha256, worst_rel
from one_engine.oracles.golds import GoldFamily, _fmt, _numbers, untraced

FOLDER = "valuation"
TRANSLATIONS = pathlib.Path(__file__).resolve().parent.parent / "cases" / "valuation"
#: the book as Ali supplied it on 2026-10-18 (the same text was attached twice, byte for byte)
BOOK_SHA256 = "fc62aa5b9ec37a358980a08fcbd093850b82bcd7eebf2e23b3b5b7bc7d17815c"
BOOK_FILE = pathlib.Path("/mnt/project-files/uploads/hearth/71cd23b5-8adf-47d8-9190-5cd8c65919db")
#: the calendar year's days, the one constant a translation may type besides the structural ones
DAYS_PER_YEAR = 365
#: a row may scale its printed number by a power of ten (0.01: a percent; 1000: printed in thousands)
SCALES = {Fraction(10) ** k for k in range(-4, 10) if k}

#: the table of contents (book lines 69-131), as part, number, title, first page; appendices A-E and the index
PARTS = {1: "Foundations of Value", 2: "Core Valuation Techniques", 3: "Intrinsic Value and the Stock Market",
         4: "Managing for Value", 5: "Advanced Valuation Issues", 6: "Special Situations"}
CHAPTERS: Tuple[Tuple[int, str, str, int], ...] = (
    (1, "1", "Why Value Value?", 3), (1, "2", "Fundamental Principles of Value Creation", 17),
    (1, "3", "The Expectations Treadmill", 45), (1, "4", "Return on Invested Capital", 59), (1, "5", "Growth", 81),
    (2, "6", "Frameworks for Valuation", 103), (2, "7", "Reorganizing the Financial Statements", 133),
    (2, "8", "Analyzing Performance and Competitive Position", 165), (2, "9", "Forecasting Performance", 187),
    (2, "10", "Estimating Continuing Value", 213), (2, "11", "Estimating the Cost of Capital", 235),
    (2, "12", "Moving from Enterprise Value to Value per Share", 273),
    (2, "13", "Calculating and Interpreting Results", 295), (2, "14", "Using Multiples to Triangulate Results", 313),
    (3, "15", "Market Value Tracks Return on Invested Capital and Growth", 337),
    (3, "16", "Markets Value Substance, Not Form", 357), (3, "17", "Emotions and Mispricing in the Market", 381),
    (3, "18", "Investors and Managers in Efficient Markets", 397), (4, "19", "Corporate Portfolio Strategy", 413),
    (4, "20", "Performance Management", 429), (4, "21", "Mergers and Acquisitions", 445),
    (4, "22", "Creating Value through Divestitures", 469), (4, "23", "Capital Structure", 489),
    (4, "24", "Investor Communications", 525), (5, "25", "Taxes", 543),
    (5, "26", "Nonoperating Expenses, One-Time Charges, Reserves, and Provisions", 559),
    (5, "27", "Leases, Pensions, and Other Obligations", 575), (5, "28", "Capitalized Expenses", 593),
    (5, "29", "Inflation", 605), (5, "30", "Foreign Currency", 621), (5, "31", "Case Study: Heineken", 637),
    (6, "32", "Valuing Flexibility", 679), (6, "33", "Valuation in Emerging Markets", 713),
    (6, "34", "Valuing High-Growth Companies", 741), (6, "35", "Valuing Cyclical Companies", 755),
    (6, "36", "Valuing Banks", 765),
    (0, "A", "Economic Profit and the Key Value Driver Formula", 791),
    (0, "B", "Discounted Economic Profit Equals Discounted Free Cash Flow", 795),
    (0, "C", "Derivation of Free Cash Flow, Weighted Average Cost of Capital, and Adjusted Present Value", 799),
    (0, "D", "Levering and Unlevering the Cost of Equity", 805),
    (0, "E", "Leverage and the Price-to-Earnings Multiple", 813),
)
INDEX_PAGE = 817

_EVEN = re.compile(r"^(\d{1,3}) [A-Z][A-Z0-9 ,:’&\-\.]{3,}$")      # "142 REORGANIZING THE FINANCIAL STATEMENTS"
_ODD = re.compile(r"^[A-Z][A-Z0-9 ,:’&\-\.]{3,} (\d{1,3})$")       # "REORGANIZING ...: KEY CONCEPTS 137"
#: a caption; usually a line of its own, once glued after a chart's axis label ("Revenue ($ million) EXHIBIT 5.6–...")
_EXHIBIT = re.compile(r"(?:^|(?<= ))EXHIBIT ([0-9]{1,2}|[A-E])\.([0-9]{1,2})(?=[ –]|$)[ –]?(.*)$")
# a number may end a sentence: "2009." is 2009 (a trailing comma already falls inside the digits)
_NUMBER = re.compile(r"^(\()?(-|–)?\$?([0-9][0-9,]*(?:\.[0-9]+)?)(%)?(\))?\.?$")
_DASHES = re.compile(r"^[–—-]+$")


# ── the book ────────────────────────────────────────────────────────────────

def book_path() -> Optional[pathlib.Path]:
    """The book's text, from ``$VALUATION_BOOK`` or the shared folder; ``None`` where it is not present."""
    p = pathlib.Path(os.environ.get("VALUATION_BOOK", BOOK_FILE))
    return p if p.exists() else None


def read_book(path: Optional[pathlib.Path] = None) -> List[str]:
    """The book's lines (book line ``k`` is ``lines[k - 1]``); refuses a text whose hash is not the book's."""
    path = path or book_path()
    if path is None:
        raise OracleChanged("the valuation book is not present (set $VALUATION_BOOK)")
    got = sha256(path)
    if got != BOOK_SHA256:
        raise OracleChanged(f"{path}: hash {got[:12]}… is not the book's {BOOK_SHA256[:12]}…")
    return path.read_text(encoding="utf-8").splitlines()


def chapter_starts(lines: Sequence[str]) -> Dict[str, int]:
    """The line index where each chapter (and appendix) opens: its number alone on a line, then its title."""
    out: Dict[str, int] = {}
    for _part, num, title, _first in CHAPTERS:
        marks = (num,) if num.isdigit() else (f"APPENDIX{num}", f"APPENDIX {num}")
        head = title.split()[0].lower()
        for i in range(len(lines) - 1):
            if lines[i].strip() in marks and lines[i + 1].strip().lower().startswith(head) and i > 300:
                out[num] = i
                break
    return out


def pages(lines: Sequence[str]) -> List[Optional[int]]:
    """The printed page of every line, from the running heads and the chapters' opening pages.

    A running head is a page's first line ("142 REORGANIZING ..." on an even
    page, "... KEY CONCEPTS 137" on an odd one). A line that looks like one
    but breaks the page order (a chart label such as "RONIC 16 12") is not
    taken: a head counts only one to three pages after the last one counted.
    A chapter's opening page has no head; its lines take the page the table
    of contents gives the chapter, from the chapter's number line to the next
    head.
    """
    first_page = {num: first for _p, num, _t, first in CHAPTERS}
    starts = {i: first_page[num] for num, i in chapter_starts(lines).items()}
    out: List[Optional[int]] = [None] * len(lines)
    page = None
    for i, line in enumerate(lines):
        if i in starts:
            page = starts[i]
        else:
            m = _EVEN.match(line.strip()) or _ODD.match(line.strip())
            if m and (page is None or page < int(m.group(1)) <= page + 3):
                page = int(m.group(1))
        out[i] = page
    return out


def chapter_of(page: Optional[int]) -> Optional[str]:
    """The chapter (or appendix) a printed page falls in; ``None`` before chapter 1 or in the index."""
    if page is None or page >= INDEX_PAGE:
        return None
    found = None
    for _part, num, _title, first in CHAPTERS:
        if page >= first:
            found = num
    return found


def book_map(lines: Sequence[str]) -> Dict[str, Any]:
    """Chapters with their first pages and every exhibit with its line, page, chapter and title."""
    pg = pages(lines)
    exhibits = []
    for i, line in enumerate(lines):
        m = _EXHIBIT.search(line.strip())
        if m:
            exhibits.append({"id": f"{m.group(1)}.{m.group(2)}", "line": i + 1, "page": pg[i],
                             "chapter": m.group(1), "title": m.group(3).strip()})
    return {"chapters": [{"part": p, "id": n, "title": t, "first_page": f} for p, n, t, f in CHAPTERS],
            "exhibits": exhibits}


# ── excerpts ────────────────────────────────────────────────────────────────

def _ranges_text(ranges: Sequence[Tuple[int, int]]) -> str:
    return ", ".join(f"{a}-{b}" for a, b in ranges)


def excerpt_ranges(excerpt: str, manifest: pathlib.Path = MANIFEST) -> List[Tuple[int, int]]:
    """The book lines an excerpt was cut from, as its manifest origin records them."""
    origin = json.loads(manifest.read_text())["files"][f"{FOLDER}/{excerpt}.txt"]["origin"]
    m = re.search(r"book lines ([0-9, \-]+);", origin)
    if not m:
        raise OracleChanged(f"{excerpt}: the manifest origin names no book lines")
    return [tuple(int(x) for x in part.split("-")) for part in m.group(1).split(", ")]


def cut_excerpt(lines: Sequence[str], ranges: Sequence[Tuple[int, int]]) -> str:
    """The text of book lines ``a..b`` (inclusive, 1-based) for each range, one line per line."""
    out = []
    for a, b in ranges:
        if not 1 <= a <= b <= len(lines):
            raise ValueError(f"book lines {a}-{b} are outside the book's {len(lines)} lines")
        out += lines[a - 1:b]
    return "\n".join(out) + "\n"


def vendor_excerpt(excerpt: str, ranges: Sequence[Tuple[int, int]], lines: Optional[Sequence[str]] = None,
                   root: pathlib.Path = REFERENCE, manifest: pathlib.Path = MANIFEST) -> str:
    """Write an excerpt from the book and record its hash and book lines in the manifest; return the hash.

    An excerpt already vendored is never rewritten: a different cut is a new
    excerpt name. This is the only way excerpts enter the repository. The
    manifest is read and rewritten under an exclusive lock, so several
    translators vendoring at once cannot drop each other's entries.
    """
    lines = read_book() if lines is None else lines
    text = cut_excerpt(lines, ranges)
    with _locked(manifest):
        return _vendor(excerpt, ranges, text, root / FOLDER / f"{excerpt}.txt", manifest)


@contextlib.contextmanager
def _locked(manifest: pathlib.Path):
    """An exclusive lock, held while the manifest is read and rewritten.

    The lock file lives in the temporary directory, named after the manifest's
    path, so it never lands among the vendored files the manifest accounts for.
    """
    key = hashlib.sha256(str(manifest.resolve()).encode()).hexdigest()[:16]
    with open(pathlib.Path(tempfile.gettempdir()) / f"one_engine-manifest-{key}.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _vendor(excerpt: str, ranges: Sequence[Tuple[int, int]], text: str, path: pathlib.Path,
            manifest: pathlib.Path) -> str:
    doc = json.loads(manifest.read_text())
    key = f"{FOLDER}/{excerpt}.txt"
    if key in doc["files"] or path.exists():
        raise OracleChanged(f"{key} is already vendored; an oracle is never rewritten")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    doc["files"][key] = {
        "sha256": digest,
        "origin": (f"Koller, Goedhart, Wessels, Valuation, 5th ed. (Wiley, 2010), text extraction sha256 "
                   f"{BOOK_SHA256}; book lines {_ranges_text(ranges)}; cut by one_engine.oracles.valuation."
                   f"vendor_excerpt, line endings normalised to LF, nothing else changed"),
        "licence": ("Copyright John Wiley & Sons / McKinsey & Company. Ali's approved copy; the exhibit's lines "
                    "only, for internal verification. Not for redistribution."),
    }
    doc["files"] = dict(sorted(doc["files"].items()))
    manifest.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    return digest


# ── reading an excerpt ──────────────────────────────────────────────────────

def _number(tok: str) -> Optional[Tuple[Fraction, int]]:
    """A printed number and its decimals (``(1,027)`` is -1027, 0); ``None`` if the token is not one."""
    m = _NUMBER.match(tok)
    if not m or bool(m.group(1)) != bool(m.group(5)):
        return None
    digits = m.group(3).replace(",", "")
    x = Fraction(digits)
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    return (-x if (m.group(1) or m.group(2)) else x), decimals


_FOOTNOTE = re.compile(r"[0-9]{1,2}$")     # a footnote mark: a bare integer, no %, $, comma or point


def _cells(tok: str) -> Optional[List[Tuple[Fraction, int]]]:
    """The cells a token prints: one number, or one nil per dash; ``None`` for a word."""
    n = _number(tok)
    if n is not None:
        return [n]
    if _DASHES.match(tok):
        return [(Fraction(0), 0)] * len(tok)
    return None


def figures_of(text: str) -> List[Tuple[int, int, List[Tuple[Fraction, int]]]]:
    """Every printed figure of an excerpt: ``(line index, token index, cells)``.

    A bare integer of one or two digits that opens a line and is followed by
    a word is a footnote mark (``1 Assumes a marginal tax of 25% ...``), not
    a figure. Only a bare integer: ``67% probability ...`` and ``$50 per
    share ...`` open with figures, and a four-digit year is a figure the
    translation reads or sets aside.
    """
    out = []
    for li, line in enumerate(text.splitlines()):
        toks = line.split()
        for ti, tok in enumerate(toks):
            cells = _cells(tok)
            if cells is None:
                continue
            if ti == 0 and len(toks) > 1 and _cells(toks[1]) is None and _FOOTNOTE.match(tok):
                continue
            out.append((li, ti, cells))
    return out


Position = Tuple[int, int, int]          # (line index, token index, cell within the token)


def _line_cells(toks: Sequence[str], li: int, start: int) -> Tuple[List[Position], List[Tuple[Fraction, int]]]:
    """The run of cells from token ``start`` of a line up to its first word."""
    j, cells, pos = start, [], []
    while j < len(toks) and _cells(toks[j]) is not None:
        got = _cells(toks[j])
        cells += got
        pos += [(li, j, k) for k in range(len(got))]
        j += 1
    return pos, cells


def read_row(text: str, label: str, occurrence: int = 1, continued: int = 0
             ) -> Tuple[List[Position], List[Tuple[Fraction, int]], List[Position]]:
    """The ``occurrence``-th run of numbers that ``label`` opens: cell positions, cells, and label cells.

    A cell's position is ``(line index, token index, cell within the
    token)``: a token of three dashes holds three cells, so a row that takes
    one of them leaves the other two for another row to account for. The
    label must stand on token boundaries and be followed by at least one
    number. ``continued`` extends the run over that many following lines,
    each of which must hold numbers only (a row the extraction broke over
    lines). Numbers inside the label itself ("Debt-to-equity in 2006", a
    subscript "t – 1") are returned as the label's cells: text the
    translation declares, not figures. Raises ``KeyError`` when there is no
    such occurrence, or a continuation line holds a word.
    """
    want = label.split()
    lines = text.splitlines()
    seen = 0
    for li, line in enumerate(lines):
        toks = line.split()
        for start in range(len(toks) - len(want) + 1):
            if toks[start:start + len(want)] != want:
                continue
            pos, cells = _line_cells(toks, li, start + len(want))
            if not cells:
                continue
            seen += 1
            if seen != occurrence:
                continue
            for lj in range(li + 1, li + 1 + continued):
                more = lines[lj].split() if lj < len(lines) else []
                if not more or any(_cells(t) is None for t in more):
                    raise KeyError(f"label {label!r}: continuation line {lj + 1} is not numbers only")
                p2, c2 = _line_cells(more, lj, 0)
                pos, cells = pos + p2, cells + c2
            in_label = [(li, start + i, k) for i, t in enumerate(want) if _cells(t) is not None
                        for k in range(len(_cells(t)))]
            return pos, cells, in_label
    raise KeyError(f"label {label!r} opens no run of numbers (occurrence {occurrence}; found {seen})")


def read_line(text: str, line: int) -> Tuple[List[Position], List[Tuple[Fraction, int]]]:
    """Every printed figure of excerpt line ``line`` (1-based), in order, whatever words sit between.

    For a line no label opens: a chart's data labels, or a scenario line
    whose figures are separated by words (``67% probability ... 1,954``).
    The cells are exactly the line's printed figures as :func:`figures_of`
    counts them, so a footnote mark is not one of them. Raises ``KeyError``
    when the line prints no figure.
    """
    lines = text.splitlines()
    if not 1 <= line <= len(lines):
        raise KeyError(f"line {line} is not a line of the excerpt")
    pos, cells = [], []
    for li, ti, got in figures_of(lines[line - 1]):
        pos += [(line - 1, ti, k) for k in range(len(got))]
        cells += got
    if not cells:
        raise KeyError(f"line {line} prints no figure")
    return pos, cells


# ── the family ──────────────────────────────────────────────────────────────

_KEYS = {"excerpt", "periods", "rows", "inputs", "case", "entity", "figures", "not_compared", "set_aside",
         "plant", "notes", "columns_of"}
_ROW_KEYS = {"label", "line", "continued", "occurrence", "periods", "scale", "skip"}


class ValuationFamily(Family):
    name = "valuation"
    planted = ("the input row the translation names under 'plant', each printed period moved 1% away from nil "
               "(at least ten units of its last printed digit)")
    tolerance = ("each printed figure within one unit of its last printed digit (the book rounds every line "
                 "on its own)")

    def __init__(self, translations: pathlib.Path = TRANSLATIONS):
        self.translations = translations

    def excerpts(self) -> List[str]:
        return sorted(p.stem for p in (REFERENCE / FOLDER).glob("*.txt"))

    def oracles(self) -> List[str]:
        out = []
        tr = {p.stem for p in self.translations.glob("*.yaml")} if self.translations.exists() else set()
        for ex in self.excerpts():
            mine = sorted(t for t in tr if t == ex or t.startswith(ex + "@"))
            out += mine or [ex]
        return out

    def source(self, oid: str) -> str:
        return f"{FOLDER}/{oid.split('@')[0]}.txt"

    def translation(self, oid: str) -> Optional[pathlib.Path]:
        p = self.translations / f"{oid}.yaml"
        return p if p.exists() else None

    def run(self, oid: str, plant: bool = False) -> Tuple[List[Check], Dict[str, Any]]:
        path = self.translation(oid)
        if path is None:
            raise Blocked(f"no translation yet (cases/{self.translations.name}/{oid}.yaml)")
        tr = load_yaml_strict(path.read_text())
        unknown = sorted(set(tr) - _KEYS)
        if unknown or tr.get("excerpt") != oid.split("@")[0]:
            raise ValueError(f"{path.name}: keys {unknown} unknown, or excerpt is not '{oid.split('@')[0]}'")
        text = (REFERENCE / self.source(oid)).read_text(encoding="utf-8")
        n = max(_case_periods(tr["case"]), 1)

        rows, claimed, problems, rows_meta = _read_rows(tr, text, path.name)
        for other in tr.get("columns_of") or []:
            # a side-by-side exhibit: the other company's columns are accounted for only by its own
            # translation reading them, never by this one saying they are someone else's
            sib = self.translation(f"{oid.split('@')[0]}@{other}")
            if sib is None or sib.name == path.name:
                problems.append(f"columns_of names '{other}', which has no translation of this excerpt")
                continue
            claimed |= _read_rows(load_yaml_strict(sib.read_text()), text, sib.name)[1]

        set_aside = tr.get("set_aside") or {}
        lines = text.splitlines()
        aside_lines = {li for li, line in enumerate(lines) if line.strip() in set_aside}
        stale = sorted(set(set_aside) - {lines[li].strip() for li in aside_lines})
        if stale:
            problems.append(f"set aside lines the excerpt does not print: {stale}")
        loose = [(li, ti) for li, ti, cells in figures_of(text) if li not in aside_lines
                 and any((li, ti, k) not in claimed for k in range(len(cells)))]
        loose_text = "; ".join(f"line {li + 1}: {lines[li].split()[ti]}" for li, ti in loose[:8])
        checks = [Check("every printed figure accounted for", "the excerpt", "0 unaccounted",
                        f"{len(loose)} unaccounted ({loose_text})" if loose else "0", not loose)]
        if problems:
            checks.append(Check("rows read", "the excerpt", "every row found", "; ".join(problems), False))

        inputs: Dict[str, Any] = {}
        for name in tr.get("inputs") or []:
            if name not in rows:
                raise ValueError(f"{path.name}: input {name} is not a row read from the excerpt")
            series = [Fraction(0)] * n
            for p, (x, _d) in zip(rows[name]["periods"], rows[name]["cells"]):
                series[p] = x * rows[name]["scale"]
            inputs[name] = series
        planted = None
        if plant:
            name = tr["plant"]
            if name not in inputs:
                raise Blocked(f"plant names '{name}', which is not an input row")
            row = rows[name]
            for p, (x, decimals) in zip(row["periods"], row["cells"]):
                # 1% of the amount, but never less than ten units of the printed last digit: a small
                # percent moved by 1% stays inside the one-unit tolerance and no figure could catch it
                v, unit = inputs[name][p], row["scale"] / 10 ** decimals
                delta = max(abs(v) / 100, 10 * unit)
                inputs[name][p] = v + delta if v >= 0 else v - delta
            planted = f"{name}: +1% away from nil, at least ten units of its last printed digit"
        fed_inputs: Dict[str, Any] = {}
        for name, series in inputs.items():
            fed_inputs[name] = [str(x) for x in series]
            for p in rows[name]["periods"]:
                fed_inputs[f"{name}__{p}"] = str(series[p])

        case = copy.deepcopy(tr["case"])
        fed = []
        for gname, g in (case.get("generators") or {}).items():
            if g.get("kind") in ("formulas", "valuation_formulas") and g.get("inputs") == "exhibit":
                g["inputs"] = fed_inputs
                fed.append(gname)
        if inputs and not fed:
            raise ValueError(f"{path.name}: no formulas generator takes 'inputs: exhibit'")
        record = {"translation": f"cases/{self.translations.name}/{path.name}",
                  "translation_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "inputs": sorted(inputs), "fed_to": fed}
        if rows_meta["labels_with_numbers"]:
            record["labels_with_numbers"] = rows_meta["labels_with_numbers"]
        if planted:
            record["planted"] = planted
        figures = tr.get("figures") or {}
        # a reading's sign (+1 or -1: an expense read as the book prints it) is structural, not a figure;
        # so are the days of a calendar year, which the book's days-based ratios (days of inventory,
        # receivables in days) divide by and never print
        gate = [g for g in untraced({"case": tr["case"], "figures": figures})
                if not re.search(r"\.sign = -?1$", g) and not g.endswith(f" = {DAYS_PER_YEAR}")]
        checks.insert(0, Check("translation traced", "every number in the case", "0 untraced",
                               "; ".join(gate) if gate else "0", not gate))

        from one_engine.cases import run_case          # the engine, imported only when an exhibit runs
        result = run_case(case)
        results = {None: result}
        not_compared = tr.get("not_compared") or {}
        for name, spec_r in rows.items():
            if name in inputs:
                continue
            if name in not_compared:
                record.setdefault("not_compared", {})[name] = not_compared[name]
                continue
            reading = figures.get(name)
            if reading is None:
                checks.append(Check(name, "every period", "a mapped row", "not reported", False))
                continue
            got_all = GoldFamily._read(results, tr["entity"], reading, n)
            checks.append(_compare(name, spec_r, got_all))
        extra = sorted((set(figures) | set(not_compared)) - set(rows))
        if extra:
            checks.append(Check("figure map", "rows", "only rows read from the excerpt", f"unknown {extra}", False))
        bad = result.failed_checks()
        checks.append(Check("books", "every book and rulebook", "no failed check", "; ".join(bad) or "none", not bad))
        return checks, record


def _read_rows(tr: Mapping[str, Any], text: str, where: str
               ) -> Tuple[Dict[str, Dict[str, Any]], set, List[str], Dict[str, Any]]:
    """A translation's rows read from the excerpt: the rows, the cells they take, what could not be read,
    and the labels that hold numbers of their own (listed in the receipt, for a reviewer to see)."""
    default_periods = list(tr["periods"])
    rows: Dict[str, Dict[str, Any]] = {}
    claimed = set()
    problems: List[str] = []
    labelled: List[str] = []
    for name, raw in (tr.get("rows") or {}).items():
        spec = {"label": raw} if isinstance(raw, str) else dict(raw)
        bad = sorted(set(spec) - _ROW_KEYS)
        if bad or ("label" in spec) == ("line" in spec) or ("line" in spec and "continued" in spec):
            raise ValueError(f"{where}: row {name}: keys {bad} unknown, or not one of label and line "
                             f"(continued goes with a label)")
        periods = list(spec.get("periods", default_periods))
        scale = Fraction(str(spec.get("scale", 1)))
        if scale != 1 and scale not in SCALES:
            raise ValueError(f"{where}: row {name}: scale {scale} is not a power of ten")
        skip = int(spec.get("skip", 0))
        try:
            if "line" in spec:
                idx, cells = read_line(text, int(spec["line"]))
            else:
                idx, cells, in_label = read_row(text, spec["label"], int(spec.get("occurrence", 1)),
                                                int(spec.get("continued", 0)))
                # a number printed inside the label is the label's text, declared in the translation
                claimed |= set(in_label)
                if in_label:
                    labelled.append(spec["label"])
        except KeyError as e:
            problems.append(f"{name}: {e.args[0]}")
            continue
        mine = cells[skip:skip + len(periods)]
        if len(mine) != len(periods):
            problems.append(f"{name}: reads {len(cells)} numbers, needs {skip + len(periods)}")
            continue
        # only the cells the row takes are accounted for: a run's other cells (the second
        # company's columns, the current year of a row read for its prior year) need a row of their own
        claimed |= set(idx[skip:skip + len(periods)])
        rows[name] = {"periods": periods, "scale": scale, "cells": mine}
    rows_meta = {"labels_with_numbers": labelled}
    return rows, claimed, problems, rows_meta


def _case_periods(case: Mapping[str, Any]) -> int:
    return int(case.get("periods", 0))


def _compare(name: str, row: Mapping[str, Any], got_all: List[Fraction]) -> Check:
    """A figure row against the engine at the row's periods, within one unit of each printed last digit."""
    want, bad = [], []
    for k, (p, (x, decimals)) in enumerate(zip(row["periods"], row["cells"])):
        got = got_all[p] / row["scale"]
        unit = Fraction(1, 10 ** decimals)
        want.append((x, got))
        if abs(got - x) > unit:
            bad.append(k)
    if bad:
        k = bad[0]
        x, got = want[k]
        return Check(name, f"t={row['periods'][k]}", _fmt(x), _fmt(got), False,
                     rel=worst_rel([want[i] for i in bad]))
    return Check(name, f"all {len(want)} printed", _fmt(want[-1][0]) + " at the last", _fmt(want[-1][1]) + " at the last",
                 True)
