"""Venture pack: an ESCO venture's prospect-to-project pipeline (Capelo, Ferreira Dias and Pereira, 2017).

What this pack is for
---------------------
esco-product rebuilt the system-dynamics model of a new ESCO published by
Capelo, Ferreira Dias and Pereira (2017, ISDC paper 1039) from the
equations and constants of its supporting documentation: prospects informed,
interested, audited, offered a contract, built and running; staff, their
experience and learning; and the firm's money (cash, capital in projects,
debt, equity). It sits beside the deal-copy simulation of the firm pack and
does not replace it. This pack runs the same model so esco-product's own
module can retire.

The model
---------
Fifteen levels (stocks) and their flows, integrated by Euler's method at a
fixed step (esco-product's default: a month; a quarter-month as a check).
Each auxiliary is computed from the levels in dependency order exactly as
esco-product writes it (``_auxiliaries``; the comments name the documentation
line, "D:<n>"). The arithmetic is floating point, in the same order of
operations as esco-product's, so the runs match bit for bit; the model is
not exact arithmetic and does not pretend to be.

Readings the caller states (``switches``): ``audit_multiplier_1`` (the
documentation's audit subsidy effect of 0 read as the policy switched off,
so audits sell at 100%) and ``market_cost_of_debt`` (the interest rate
subsidy read as off). esco-product's base case sets both; "the appendix
read literally" sets neither. Its other defaults are kept: levels with no
printed start begin at 0; MIN(1, available / desired) is 1 when nothing is
desired; the market cost of debt is read off its printed graph by straight
lines between points (``debt_cost_curve``); market value added (MVA) is the
running sum of each step's economic value added discounted at that step's
cost of capital, compounding per step. ``mva_annual_effective`` gives the
variant with the cost of capital read as an annual effective rate.

Units: every quantity carries its dimension (K EUR, client, work hour,
employee, year; months and days convert as time). ``unit_check`` computes
every auxiliary once from a state with every level non-zero and compares
each computed dimension with the printed unit.

Money in the book
-----------------
The money levels are double-entry consistent: cash plus capital in projects
equals debt plus equity at every step (the equations move them in pairs).
The ``venture`` rulebook posts each step's money flows times the step to the
book, in the year the step starts: revenue, operating costs, interest and
tax against cash, investment from cash into capital in projects,
depreciation out of it, borrowing and repayment between cash and debt. The
balances then follow the model's own levels to within the floating-point
drift between summing exact postings and Euler-updating floats.

Not built: anything beyond the published equations (the authors' model file
is not used).
"""
from __future__ import annotations

import math
import re
from fractions import Fraction
from typing import Any, Dict, List, Mapping, Optional, Tuple

from one_engine.entities import Group
from one_engine.inputs import InputError, Reader
from one_engine.kernel import Nature, Posting

SOURCE = "venture"

# ── units ────────────────────────────────────────────────────────────────────

DIMS = ("KEur", "Cli", "Whr", "Emp", "T")
_ATOM = {"K Eur": ("KEur", 1.0), "Cli": ("Cli", 1.0), "Clients": ("Cli", 1.0), "Whr": ("Whr", 1.0), "Emp": ("Emp", 1.0),
         "yr": ("T", 1.0), "year": ("T", 1.0), "mo": ("T", 1 / 12), "da": ("T", 1 / 365)}


def parse_unit(u: str) -> Tuple[Tuple[int, ...], float]:
    """'K Eur/yr/Cli' -> (dimension exponents over DIMS, scale to base units). '%' scales by 0.01."""
    u = u.strip()
    if u in ("Dimensionless", ""):
        return (0,) * 5, 1.0
    dims, scale = [0] * 5, 1.0
    for i, tok in enumerate(re.split(r"\s*/\s*", u)):
        sign = 1 if i == 0 else -1
        if tok == "%":
            scale *= 0.01 ** sign
            continue
        if tok not in _ATOM:
            raise InputError(f"venture: unknown unit {tok!r} in {u!r}")
        d, s = _ATOM[tok]
        dims[DIMS.index(d)] += sign
        scale *= s ** sign
    return tuple(dims), scale


_TRACE: Optional[List[Tuple[str, str, str, str]]] = None   # dimension mismatches, collected only by unit_check
_CTX = [""]


def _show(d) -> str:
    num = [f"{n}^{e}" if e > 1 else n for n, e in zip(DIMS, d) if e > 0]
    den = [f"{n}^{-e}" if e < -1 else n for n, e in zip(DIMS, d) if e < 0]
    return ("*".join(num) or "1") + ("/" + "/".join(den) if den else "")


class Q:
    """A float in base units with its dimension; adding or comparing unlike dimensions is traced."""

    __slots__ = ("v", "d")

    def __init__(self, v, d=(0, 0, 0, 0, 0)):
        self.v, self.d = float(v), tuple(d)

    def _same(self, o, op):
        o = o if isinstance(o, Q) else Q(o)
        if o.d != self.d and o.v != 0 and self.v != 0 and _TRACE is not None:
            _TRACE.append((_CTX[0], op, _show(self.d), _show(o.d)))
        return o

    def __add__(self, o): o = self._same(o, "+"); return Q(self.v + o.v, self.d if self.v != 0 or o.v == 0 else o.d)
    __radd__ = __add__
    def __sub__(self, o): o = self._same(o, "-"); return Q(self.v - o.v, self.d if self.v != 0 or o.v == 0 else o.d)
    def __rsub__(self, o): return Q(o) - self if not isinstance(o, Q) else o - self
    def __neg__(self): return Q(-self.v, self.d)

    def __mul__(self, o):
        o = o if isinstance(o, Q) else Q(o)
        return Q(self.v * o.v, tuple(a + b for a, b in zip(self.d, o.d)))
    __rmul__ = __mul__

    def __truediv__(self, o):
        o = o if isinstance(o, Q) else Q(o)
        return Q(self.v / o.v, tuple(a - b for a, b in zip(self.d, o.d)))

    def __rtruediv__(self, o): return Q(o) / self
    def __gt__(self, o): o = self._same(o, ">"); return self.v > o.v
    def __lt__(self, o): o = self._same(o, "<"); return self.v < o.v


def MIN(a, b):
    b = a._same(b, "MIN") if isinstance(a, Q) else b
    return a if a.v <= b.v else b


def MAX(a, b):
    b = a._same(b, "MAX") if isinstance(a, Q) else b
    return a if a.v >= b.v else b


def IF(c, a, b):
    return a if c else b


ZERO = lambda d: Q(0, d)
YR = Q(1, parse_unit("yr")[0])


def constants(rows: List[Mapping[str, Any]]) -> Dict[str, Q]:
    """Constants ``[{name, value, unit}]`` in base units. Initial Cash is Initial Debt + Initial Equity
    (the documentation prints it as that formula); a value given for it must be that formula."""
    c = {}
    for x in rows:
        if x["name"] == "Initial Cash":
            if str(x["value"]).replace(" ", "") != "InitialDebt+InitialEquity":
                raise InputError(f"venture: Initial Cash is Initial Debt + Initial Equity, got {x['value']!r}")
            continue
        d, s = parse_unit(x["unit"])
        c[x["name"]] = Q(float(x["value"]) * s, d)
    c["Initial Cash"] = c["Initial Debt"] + c["Initial Equity"]
    return c


def graphcurve(x, x0, step, ys):
    """Straight lines between the printed points; flat beyond the ends."""
    if x <= x0:
        return ys[0]
    i = (x - x0) / step
    if i >= len(ys) - 1:
        return ys[-1]
    k = int(i)
    return ys[k] + (ys[k + 1] - ys[k]) * (i - k)


LEVELS = ["Potential", "Interested", "Audits in Progress", "Projects in Progress", "EPC Adopters", "Lost Prospects",
          "Demo Projects", "Public Projects", "HR", "HR Experience", "EPC Net Saving", "Capital in Projects", "Cash", "Debt",
          "Equity"]


def initial(c):
    """Levels at the start: printed initial values where given (D:99, 110, 173, 216, 305, 502), else 0."""
    U = lambda u: parse_unit(u)[0]
    return {
        "Potential": c["Initial Market"],
        "Interested": ZERO(U("Cli")), "Audits in Progress": ZERO(U("Cli")), "Projects in Progress": ZERO(U("Cli")),
        "EPC Adopters": ZERO(U("Cli")), "Lost Prospects": ZERO(U("Cli")), "Demo Projects": ZERO(U("Cli")),
        "Public Projects": ZERO(U("Cli")),
        "HR": c["Minimum HR Desired"],
        "HR Experience": c["Minimum HR Desired"] * c["Average Experience of New HR"],
        "EPC Net Saving": ZERO(U("K Eur/yr")), "Capital in Projects": ZERO(U("K Eur")),
        "Cash": c["Initial Cash"], "Debt": c["Initial Debt"], "Equity": c["Initial Equity"],
        "Acc Net Earnings": ZERO(U("K Eur")), "_learn_delay": ZERO(U("Cli/yr")), "_npv": ZERO(U("K Eur")),
        "_discount": Q(1.0),
    }


def _auxiliaries(s, c, t, dt, sw, curve):
    """Every auxiliary at time t from the levels s, in dependency order (documentation lines cited)."""
    a = {}
    T, STARTTIME, TIMESTEP = Q(t, YR.d), Q(0, YR.d), Q(dt, YR.d)

    def put(name, value):
        _CTX[0] = name
        a[name] = value
        return value

    _CTX[0] = "Average HR Experience"
    put("Average HR Experience", s["HR Experience"] / s["HR"])                                                 # D:47
    _CTX[0] = "Learning Effects Factor"
    ratio = MIN(a["Average HR Experience"] / c["HR Experience Reference"], Q(1))                              # D:385
    put("Learning Effects Factor", Q(ratio.v ** (math.log(1 + c["Productivity Factor Change per Double Experience"].v)
                                                 / math.log(2))))
    LEF = a["Learning Effects Factor"]
    for n, base in (("HR Effort per Audit Offered", "Normal HR Effort per Audit Offered"),                   # D:281
                    ("HR Effort per EPC Developed", "Normal HR Effort per EPC Developed"),                   # D:284
                    ("HR Effort per Implemented EPC", "Normal HR Effort per Implemented EPC"),               # D:289
                    ("HR Effort per Prospect", "Normal HR Effort per Prospect"),                             # D:294
                    ("HR Effort per Running EPC", "Normal HR Effort per Running EPC")):                      # D:297
        _CTX[0] = n
        put(n, c[base] / LEF)
    _CTX[0] = "Fraction of Interested"
    put("Fraction of Interested", c["Normal Fraction of Interested"] * LEF)                                    # D:211
    _CTX[0] = "Fraction of EPC Adoption"
    put("Fraction of EPC Adoption", c["Normal Fraction of EPC Audit Adoption"] * LEF)                          # D:206
    subsidy = Q(1) if sw["audit_multiplier_1"] else c["Audit Subsidy Effect"]
    _CTX[0] = "Fraction of Audit Adoption"
    put("Fraction of Audit Adoption", c["Normal Fraction of Audit Adoption"] * LEF *                           # D:202
        IF(T < STARTTIME + c["Term of Audit Subsidy"], subsidy, Q(1)))
    _CTX[0] = "Net Saving per EPC"
    put("Net Saving per EPC", c["Normal Net Saving per EPC"] * LEF)                                            # D:431

    # demand side
    _CTX[0] = "Desired Rate for Informing Prospects"
    put("Desired Rate for Informing Prospects", s["Potential"] / c["Time to Inform Prospects"])                # D:135
    _CTX[0] = "Desired Rate for Selling Audits"
    put("Desired Rate for Selling Audits", (s["Interested"] + s["Public Projects"]) / c["Time to Adopt Audit"])  # D:138
    _CTX[0] = "Desired Rate for Developing EPC"
    put("Desired Rate for Developing EPC", (s["Audits in Progress"] + s["Demo Projects"]) / c["Time to Develop EPC"])  # D:128
    _CTX[0] = "Desired Rate for Implementing EPC"
    put("Desired Rate for Implementing EPC", s["Projects in Progress"] / c["Time to Implement Project"])         # D:132
    _CTX[0] = "HR Effort Desired For Developing EPC"
    put("HR Effort Desired For Developing EPC", a["Desired Rate for Developing EPC"] * a["HR Effort per EPC Developed"])  # D:257
    _CTX[0] = "HR Effort Desired For Implementing EPC"
    put("HR Effort Desired For Implementing EPC",
        a["Desired Rate for Implementing EPC"] * a["HR Effort per Implemented EPC"])                           # D:262
    _CTX[0] = "HR Effort Desired For Running EPC"
    put("HR Effort Desired For Running EPC", s["EPC Adopters"] * a["HR Effort per Running EPC"])               # D:272
    _CTX[0] = "HR Effort Desired for Informing Prospects"
    put("HR Effort Desired for Informing Prospects",
        a["Desired Rate for Informing Prospects"] * a["HR Effort per Prospect"])                               # D:267
    _CTX[0] = "HR Effort Desired for Selling Audits"
    put("HR Effort Desired for Selling Audits", a["Desired Rate for Selling Audits"] * a["HR Effort per Audit Offered"])  # D:276
    _CTX[0] = "Total HR Effort Desired for EPC"
    put("Total HR Effort Desired for EPC", a["HR Effort Desired For Developing EPC"]                            # D:641
        + a["HR Effort Desired For Implementing EPC"] + a["HR Effort Desired For Running EPC"])
    _CTX[0] = "Total HR Effort Available"
    put("Total HR Effort Available", s["HR"] * c["Work Hours per Month"])                                      # D:638
    desired = a["Total HR Effort Desired for EPC"]
    _CTX[0] = "EPC share"
    share = MIN(Q(1), a["Total HR Effort Available"] / desired) if desired.v > 0 else Q(1)    # 1 when nothing is desired
    for n, d in (("HR Effort Assigned for Developing EPC", "HR Effort Desired For Developing EPC"),             # D:222
                 ("HR Effort Assigned for Implementing EPC", "HR Effort Desired For Implementing EPC"),         # D:227
                 ("HR Effort Assigned for Running EPC", "HR Effort Desired For Running EPC")):                 # D:237
        _CTX[0] = n
        put(n, share * a[d])
    _CTX[0] = "Total HR Effort Assigned For EPC"
    put("Total HR Effort Assigned For EPC", a["HR Effort Assigned for Developing EPC"]                          # D:633
        + a["HR Effort Assigned for Implementing EPC"] + a["HR Effort Assigned for Running EPC"])
    _CTX[0] = "HR Effort Available For Selling Audits"
    put("HR Effort Available For Selling Audits",
        a["Total HR Effort Available"] - a["Total HR Effort Assigned For EPC"])                                # D:252
    _CTX[0] = "HR Effort Assigned For Selling Audits"
    put("HR Effort Assigned For Selling Audits",
        MIN(a["HR Effort Available For Selling Audits"], a["HR Effort Desired for Selling Audits"]))            # D:242
    _CTX[0] = "HR Effort Available For Informing Prospects"
    put("HR Effort Available For Informing Prospects",                                                          # D:247, name slip read
        a["HR Effort Available For Selling Audits"] - a["HR Effort Assigned For Selling Audits"])
    _CTX[0] = "HR Effort Assigned for Informing Prospects"
    put("HR Effort Assigned for Informing Prospects",
        MIN(a["HR Effort Available For Informing Prospects"], a["HR Effort Desired for Informing Prospects"]))   # D:232

    # flows through the sales cycle
    _CTX[0] = "Become Informed by HR effort"
    put("Become Informed by HR effort", a["HR Effort Assigned for Informing Prospects"] / a["HR Effort per Prospect"])  # D:59
    _CTX[0] = "Total EPC Adopters"
    put("Total EPC Adopters", s["EPC Adopters"] + s["Projects in Progress"])                                   # D:625
    _CTX[0] = "Total Potential Market"
    put("Total Potential Market", s["Potential"] + s["Interested"] + s["Audits in Progress"]                    # D:651
        + s["Projects in Progress"] + s["EPC Adopters"] + s["Lost Prospects"])
    _CTX[0] = "Become Interested by WOM"
    put("Become Interested by WOM",
        a["Total EPC Adopters"] * c["WOM Contact Rate"] * (s["Potential"] / a["Total Potential Market"]))      # D:72
    _CTX[0] = "Become Interested"
    put("Become Interested", a["Become Interested by WOM"]
        + a["Become Informed by HR effort"] * a["Fraction of Interested"])                                     # D:64
    _CTX[0] = "No Interested"
    put("No Interested", a["Become Informed by HR effort"] * (1 - a["Fraction of Interested"]))                # D:435
    _CTX[0] = "Total Audits Offered"
    put("Total Audits Offered", a["HR Effort Assigned For Selling Audits"] / a["HR Effort per Audit Offered"])   # D:621
    _CTX[0] = "PP Calling Rate"
    put("PP Calling Rate", IF(T < STARTTIME + c["PP Programme Term"], c["PP Call"], ZERO(c["PP Call"].d)))    # D:513
    _CTX[0] = "Adopt PP Audit"
    put("Adopt PP Audit", MIN(s["Public Projects"] / c["Time to Adopt Audit"], a["Total Audits Offered"]))     # D:28
    _CTX[0] = "Audits Offered"
    put("Audits Offered", a["Total Audits Offered"] - a["Adopt PP Audit"])                                     # D:40
    _CTX[0] = "Adopt Audit"
    put("Adopt Audit", a["Audits Offered"] * a["Fraction of Audit Adoption"])                                  # D:17
    _CTX[0] = "Reject Audit"
    put("Reject Audit", a["Audits Offered"] * (1 - a["Fraction of Audit Adoption"]))                           # D:545
    _CTX[0] = "Total EPC Developed"
    put("Total EPC Developed", a["HR Effort Assigned for Developing EPC"] / a["HR Effort per EPC Developed"])    # D:628
    _CTX[0] = "Adopt Demo EPC"
    put("Adopt Demo EPC", MIN(s["Demo Projects"] / c["Time to Develop EPC"], a["Total EPC Developed"]))          # D:21
    _CTX[0] = "EPC Developed"
    put("EPC Developed", a["Total EPC Developed"] - a["Adopt Demo EPC"])                                       # D:161
    _CTX[0] = "Adopt EPC"
    put("Adopt EPC", a["EPC Developed"] * a["Fraction of EPC Adoption"])                                       # D:25
    _CTX[0] = "Reject EPC"
    put("Reject EPC", a["EPC Developed"] * (1 - a["Fraction of EPC Adoption"]))                                # D:551
    _CTX[0] = "DP Calling Rate"
    put("DP Calling Rate", IF(T < STARTTIME + c["Demo Programme Term"], c["DP Call"], ZERO(c["DP Call"].d)))   # D:148
    _CTX[0] = "Implement EPC"
    put("Implement EPC", a["HR Effort Assigned for Implementing EPC"] / a["HR Effort per Implemented EPC"])     # D:336
    _CTX[0] = "End EPC"
    put("End EPC", s["EPC Adopters"] / c["EPC Term"])                                                          # D:154
    _CTX[0] = "Regain Potential"
    put("Regain Potential", s["Lost Prospects"] / c["Time to Regain Potential"])                               # D:542
    _CTX[0] = "Total Lost Prospects"
    put("Total Lost Prospects", a["No Interested"] + a["Reject Audit"] + a["Reject EPC"] + a["End EPC"])        # D:646, name slip read
    _CTX[0] = "Increase in Potential Market"
    put("Increase in Potential Market", c["Yearly Increase in Potential Market"] * a["Total Potential Market"])   # D:347
    _CTX[0] = "Saving Variation from EPC adoption"
    put("Saving Variation from EPC adoption", a["Implement EPC"] * a["Net Saving per EPC"])                     # D:575
    _CTX[0] = "Saving Variation from EPC Ending"
    per_client = ZERO((s["EPC Net Saving"] / Q(1, (0, 1, 0, 0, 0))).d)
    put("Saving Variation from EPC Ending", a["End EPC"] * IF(s["EPC Net Saving"].v > 0,                        # D:579
        s["EPC Net Saving"] / s["EPC Adopters"] if s["EPC Adopters"].v else per_client, per_client))

    # staff
    _CTX[0] = "Minimum HR Needed"
    put("Minimum HR Needed", (a["Total HR Effort Desired for EPC"] + a["HR Effort Desired for Selling Audits"])  # D:419
        / c["Work Hours per Month"])
    _CTX[0] = "HR to Hire"
    target = MIN(MAX(c["Minimum HR Desired"], a["Minimum HR Needed"]), c["Maximum HR Desired"])                # D:331
    put("HR to Hire", IF((target - s["HR"]).v > 0, target - s["HR"], ZERO(s["HR"].d)))
    _CTX[0] = "HR Hiring Rate"
    put("HR Hiring Rate", a["HR to Hire"] / c["Average Time to Hire and Train"])                               # D:323
    _CTX[0] = "HR Leaving Rate"
    put("HR Leaving Rate", c["HR Attrition Rate"] * s["HR"])                                                   # D:327
    _CTX[0] = "Experience from EPC Adoption"
    put("Experience from EPC Adoption", s["_learn_delay"] * c["Experience Gain per EPC Adoption"] / 2)          # D:188
    _CTX[0] = "Experience from Hiring"
    put("Experience from Hiring", c["Average Experience of New HR"] * a["HR Hiring Rate"])                      # D:193
    _CTX[0] = "Increase HR Experience"
    put("Increase HR Experience", a["Experience from EPC Adoption"] + a["Experience from Hiring"])              # D:343
    _CTX[0] = "Loss of HR Experience"
    put("Loss of HR Experience", a["HR Leaving Rate"] * a["Average HR Experience"]                              # D:391
        + s["HR Experience"] * c["HR Fractional Experience Decay Rate"])

    # money
    _CTX[0] = "Investing in Projects"
    put("Investing in Projects", c["Investment per EPC"] * a["Implement EPC"])                                 # D:377
    _CTX[0] = "Depreciating Projects"
    put("Depreciating Projects", s["Capital in Projects"] / c["Projects Depreciation Time"])                    # D:124
    _CTX[0] = "Revenues"
    put("Revenues", s["EPC Net Saving"] + a["Adopt Audit"] * c["Revenue per Audit"]                             # D:571, name slip read
        + (a["Adopt PP Audit"] + a["Adopt Demo EPC"]) * c["Revenue per EPC Developed"])
    _CTX[0] = "HR Expenses"
    put("HR Expenses", c["Expenses per Employee"] * s["HR"])                                                   # D:301
    _CTX[0] = "Operating Costs"
    put("Operating Costs", a["HR Expenses"] + c["Overhead Costs"])                                             # D:487
    _CTX[0] = "Operating Expenses"
    put("Operating Expenses", a["Operating Costs"] + a["Depreciating Projects"])                               # D:490
    _CTX[0] = "EBIT"
    put("EBIT", a["Revenues"] - a["Operating Expenses"])                                                       # D:151
    _CTX[0] = "Debt Ratio"
    de = s["Debt"] + s["Equity"]
    put("Debt Ratio", s["Debt"] / de if de.v else Q(0))                                                        # D:113
    _CTX[0] = "Market Cost of Debt"
    put("Market Cost of Debt", Q(graphcurve(a["Debt Ratio"].v, curve["x0"], curve["step"], curve["ys"]) * 100 * 0.01,
                                 (0, 0, 0, 0, -1)))                                                            # D:400, x100 read as %
    _CTX[0] = "Cost of Debt"
    if sw["market_cost_of_debt"]:
        put("Cost of Debt", a["Market Cost of Debt"])
    else:
        put("Cost of Debt", IF(T < STARTTIME + c["Term of Interest Rate Subsidy"], c["Interest Rate Subsidy"],   # D:102
                               a["Market Cost of Debt"]))
    _CTX[0] = "Interest Expense"
    put("Interest Expense", s["Debt"] * a["Cost of Debt"])                                                     # D:365
    _CTX[0] = "Taxes"
    put("Taxes", (a["EBIT"] - a["Interest Expense"]) * c["Tax Rate"])                                          # D:590
    _CTX[0] = "Net Earnings"
    put("Net Earnings", a["EBIT"] - a["Interest Expense"] - a["Taxes"])                                        # D:428
    _CTX[0] = "Equity Var"
    put("Equity Var", a["Net Earnings"])                                                                       # D:176
    _CTX[0] = "NOPLAT"
    put("NOPLAT", a["EBIT"] * (1 - c["Tax Rate"]))                                                             # D:441
    _CTX[0] = "WACC"
    put("WACC", c["Cost of Equity"] * (s["Equity"] / de) + a["Cost of Debt"] * (1 - c["Tax Rate"]) * (s["Debt"] / de))  # D:655
    _CTX[0] = "EVA"
    put("EVA", a["NOPLAT"] - a["WACC"] * s["Capital in Projects"])                                             # D:179
    _CTX[0] = "Capital Employed"
    put("Capital Employed", s["Cash"] + s["Capital in Projects"])                                              # D:92
    _CTX[0] = "Capital Charge"
    put("Capital Charge", a["Capital Employed"] * a["WACC"])                                                   # D:87
    _CTX[0] = "Incoming Cash"
    put("Incoming Cash", a["Revenues"])                                                                        # D:340
    _CTX[0] = "Repaying"
    rep = ZERO(s["Debt"].d)
    if a["Debt Ratio"].v > c["Target Debt Ratio"].v:                                                           # D:556
        rep = MIN(MAX(s["Cash"] - c["Maximum Cash Level"], ZERO(s["Cash"].d)),
                  s["Debt"] - s["Debt"] / a["Debt Ratio"] * c["Target Debt Ratio"])
    put("Repaying", rep / TIMESTEP)
    _CTX[0] = "Outflowing Cash"
    put("Outflowing Cash", a["Investing in Projects"] + a["Taxes"] + a["Operating Costs"] + a["Interest Expense"]  # D:493
        + a["Repaying"])
    _CTX[0] = "Borrowing Cash"
    put("Borrowing Cash", MAX((c["Minimum cash level"] - s["Cash"]) / TIMESTEP - a["Incoming Cash"]             # D:82
                              + a["Outflowing Cash"], ZERO(a["Incoming Cash"].d)))
    _CTX[0] = "Borrowing"
    put("Borrowing", a["Borrowing Cash"])                                                                      # D:79
    _CTX[0] = "MVA"
    put("MVA", s["_npv"])                                                                                      # D:424
    _CTX[0] = ""
    return a


def _derivatives(s, a, c):
    return {
        "Potential": a["Increase in Potential Market"] + a["Regain Potential"] - a["Become Interested"] - a["No Interested"],  # D:502
        "Interested": a["Become Interested"] - a["Adopt Audit"] - a["Reject Audit"],                          # D:372
        "Audits in Progress": a["Adopt Audit"] + a["Adopt PP Audit"] - a["Adopt EPC"] - a["Reject EPC"],      # D:37
        "Projects in Progress": a["Adopt EPC"] + a["Adopt Demo EPC"] - a["Implement EPC"],                    # D:529
        "EPC Adopters": a["Implement EPC"] - a["End EPC"],                                                    # D:157
        "Lost Prospects": a["Total Lost Prospects"] - a["Regain Potential"],                                  # D:396
        "Demo Projects": a["Adopt Demo EPC"] - a["DP Calling Rate"],                                          # D:119, as printed
        "Public Projects": a["PP Calling Rate"] - a["Adopt PP Audit"],                                        # D:533
        "HR": a["HR Hiring Rate"] - a["HR Leaving Rate"],                                                     # D:216
        "HR Experience": a["Increase HR Experience"] - a["Loss of HR Experience"],                            # D:305
        "EPC Net Saving": a["Saving Variation from EPC adoption"] - a["Saving Variation from EPC Ending"],    # D:165
        "Capital in Projects": a["Investing in Projects"] - a["Depreciating Projects"],                       # D:95
        "Cash": a["Incoming Cash"] + a["Borrowing Cash"] - a["Outflowing Cash"],                              # D:99
        "Debt": a["Borrowing"] - a["Repaying"],                                                               # D:110
        "Equity": a["Equity Var"],                                                                            # D:173
        "Acc Net Earnings": a["Net Earnings"],                                                                # D:14
        "_learn_delay": (a["Implement EPC"] - s["_learn_delay"]) / c["Average Time to Learn from EPC"],       # D:188, first-order delay
        "_npv": a["EVA"] * s["_discount"],                                                                    # D:424
    }


def _row(year, s, a):
    return {"year": year, "staff": s["HR"].v, "interested prospects": s["Interested"].v,
            "adopters": s["EPC Adopters"].v, "projects in progress": s["Projects in Progress"].v,
            "accumulated net earnings": s["Acc Net Earnings"].v, "MVA": s["_npv"].v, "cash": s["Cash"].v,
            "debt": s["Debt"].v, "equity": s["Equity"].v, "revenues": a["Revenues"].v, "net earnings": a["Net Earnings"].v,
            "learning effects factor": a["Learning Effects Factor"].v, "potential": s["Potential"].v}


# money flows posted to the book: (auxiliary, debit, credit), each per year, times the step
MONEY = (("Revenues", "cash", "revenues"), ("Operating Costs", "operating_costs", "cash"),
         ("Interest Expense", "interest", "cash"), ("Taxes", "taxes", "cash"),
         ("Investing in Projects", "capital_in_projects", "cash"), ("Depreciating Projects", "depreciation", "capital_in_projects"),
         ("Borrowing Cash", "cash", "debt"), ("Repaying", "debt", "cash"))


def run(const_rows, switches, curve, dt=1 / 12, years=15, on_step=None):
    """Euler integration; year-end rows (levels and key auxiliaries at t = 0..years).

    ``on_step(k, t, a)`` is called for every step taken (k = 0..n-1) with that step's auxiliaries.
    """
    c = constants(const_rows)
    s = initial(c)
    n = round(years / dt)
    per_year = round(1 / dt)
    rows = []
    for k in range(n + 1):
        t = k * dt
        a = _auxiliaries(s, c, t, dt, switches, curve)
        if k % per_year == 0:
            rows.append(_row(round(t), s, a))
        if k == n:
            break
        if on_step:
            on_step(k, t, a)
        d = _derivatives(s, a, c)
        s = {key: s[key] + d[key] * Q(dt, YR.d) if key in d else s[key] for key in s}
        s["_discount"] = s["_discount"] / Q(1 + a["WACC"].v * dt)    # per-step compounding at the step's cost of capital
    return rows


def mva_annual_effective(const_rows, switches, curve, dt=1 / 12, years=15):
    """MVA at the end of the run with the cost of capital read as an annual effective rate: each step
    discounts by (1 + WACC) ** -dt instead of (1 + WACC * dt) ** -1."""
    c = constants(const_rows)
    s = initial(c)
    mva, disc = 0.0, 1.0
    for k in range(round(years / dt)):
        a = _auxiliaries(s, c, k * dt, dt, switches, curve)
        d = _derivatives(s, a, c)
        mva += a["EVA"].v * dt * disc
        disc /= (1 + a["WACC"].v) ** dt
        s = {key: s[key] + d[key] * Q(dt, YR.d) if key in d else s[key] for key in s}
        s["_discount"] = s["_discount"] / Q(1 + a["WACC"].v * dt)
    return mva


def summary(rows):
    """First year-end with at least one whole running project; adopters, staff and MVA at year 15; years
    with accumulated net earnings below zero; the first year MVA is above zero."""
    by = {r["year"]: r for r in rows}
    last = max(by)
    return {"first projects year": next((r["year"] for r in rows if r["adopters"] >= 1), None),
            "projects by year 15": by[last]["adopters"], "staff by year 15": by[last]["staff"],
            "negative years of accumulated net earnings": sum(1 for r in rows if r["year"] >= 1 and r["accumulated net earnings"] < 0),
            "MVA first positive year": next((r["year"] for r in rows if r["year"] >= 1 and r["MVA"] > 0), None),
            "MVA at year 15 (K EUR)": by[last]["MVA"]}


def unit_check(const_rows, printed_units, switches, curve):
    """(mismatches met inside formulas, rows comparing each entry's computed dimension and printed unit).

    Every auxiliary is computed once at t = 5 years from the start levels with every zero level set to 1,
    so every term carries its dimension.
    """
    global _TRACE
    c = constants(const_rows)
    s = initial(c)
    for k in LEVELS:
        if s[k].v == 0:
            s[k] = Q(1.0, s[k].d)
    s["_learn_delay"] = Q(1.0, s["_learn_delay"].d)
    _TRACE = []
    try:
        a = _auxiliaries(s, c, 5.0, 1 / 12, switches, curve)
        inside = list(dict.fromkeys(_TRACE))
    finally:
        _TRACE = None
    rows = []
    for u in printed_units:
        if u["type"] == "constant":
            continue
        val = a.get(u["name"]) if u["name"] in a else s.get(u["name"])
        if val is None:
            continue
        pd, pscale = parse_unit(u["unit"])
        if pd != val.d:
            status = "dimension differs"
        elif abs(pscale - 1.0) > 1e-12 and any(pd):
            status = "same quantity, printed in another time or percent unit"
        else:
            status = "agrees"
        rows.append({"entry": u["name"], "line": u["line"], "printed unit": u["unit"], "computed dimension": _show(val.d),
                     "check": status})
    return inside, rows


def venture(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    """The venture model run over the case's years, its money flows posted to one book.

    Inputs: ``entity``, ``phase``, ``constants`` (``[{name, value, unit}]``), ``switches``
    (``{audit_multiplier_1, market_cost_of_debt}``), ``debt_cost_curve`` (``{x0, step, ys}``: the market
    cost of debt against the debt ratio, in % a year over 100), ``step`` (years; esco-product's default 1/12)
    and optionally ``printed_units`` (``[{name, type, unit, line}]``) for the unit check. One period per year.

    Records ``venture`` per period: the year-end ``row`` and the model's money levels at that year end
    (``levels``); in the last period also ``rows`` (from year 0), ``summary``, ``mva_annual_effective`` and,
    when printed units are given, ``unit_check``.
    """
    name, where = ctx["name"], f"venture {ctx['name']}"
    entity, phase = r.take("entity"), r.take("phase")
    const_rows, sw, curve = r.take("constants"), r.take("switches"), r.take("debt_cost_curve")
    dt = float(Fraction(str(r.take("step"))))
    units = r.take_or_default("printed_units", None, reason="no unit check").value
    if set(sw) != {"audit_multiplier_1", "market_cost_of_debt"}:
        raise InputError(f"{where}: switches must be audit_multiplier_1 and market_cost_of_debt, got {sorted(sw)}")
    years = group.periods
    if round(1 / dt) * dt != 1 and abs(round(1 / dt) * dt - 1) > 1e-12:
        raise InputError(f"{where}: the step must divide a year, got {dt}")
    flows: Dict[int, List[Tuple[int, float, str, str, str]]] = {}

    def on_step(k, t, a):
        for aux, dr, cr in MONEY:
            v = a[aux].v * dt
            if v:
                flows.setdefault(int(k * dt + 1e-9), []).append((k, v, dr, cr, aux))

    rows = run(const_rows, sw, curve, dt, years, on_step)
    c = constants(const_rows)
    unit = ctx["currency"]
    natures = {"cash": Nature.ASSET, "capital_in_projects": Nature.ASSET, "debt": Nature.LIABILITY, "equity": Nature.EQUITY,
               "revenues": Nature.INCOME, "operating_costs": Nature.EXPENSE, "interest": Nature.EXPENSE,
               "taxes": Nature.EXPENSE, "depreciation": Nature.EXPENSE}
    opening = {"cash": Fraction(c["Initial Cash"].v), "debt": Fraction(c["Initial Debt"].v),
               "equity": Fraction(c["Initial Equity"].v)}
    for acct, nat in natures.items():
        if acct in opening:
            group.open(entity, acct, nat, unit, opening[acct])
        else:
            group.declare(entity, acct, nat, unit)
    extra = {"rows": rows, "summary": summary(rows), "mva_annual_effective": mva_annual_effective(const_rows, sw, curve, dt, years)}
    if units is not None:
        extra["unit_check"] = unit_check(const_rows, units, sw, curve)

    def handler(g: Group, t: int) -> None:
        book = g.book(entity)
        for k, v, dr, cr, aux in flows.get(t, []):
            if v < 0:                                     # a negative flow (a tax credit) runs the other way
                dr, cr, v = cr, dr, -v
            eff = {dr: 1 if book.accounts[dr].nature.debit_natured else -1,
                   cr: -1 if book.accounts[cr].nature.debit_natured else 1}
            g.post(entity, Posting(t, phase, dr, cr, Fraction(v), eff, SOURCE, f"{name}.{aux}#{k}"))
        levels = {a_: book.balance(a_, t) for a_ in natures}
        ni = levels["revenues"] - levels["operating_costs"] - levels["interest"] - levels["taxes"] - levels["depreciation"]
        levels["equity_with_earnings"] = levels["equity"] + ni
        rec = {"row": rows[t + 1], "levels": levels}
        if t == years - 1:
            rec.update(extra)
        g.record(name, t, **rec)

    group.register(phase, handler)


RULEBOOKS = {"venture": venture}
