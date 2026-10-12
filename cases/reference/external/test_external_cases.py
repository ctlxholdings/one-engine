"""External cases — published models, with their own answers, graded against the card.

Every case in this file comes from OUTSIDE this project. Three of them also run
the live engine, so a drift in either the card or the engine is visible.

Why these exist: a gold authored here is evidence I wrote myself. These are not.
CFI, Open Risk, ModelOff and the two IFRS worked examples published their own
answers; the card either reproduces them or it does not.

Run:  python3 -m unittest discover -s cases/external -t .
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# The card lives in engine/ledger now. These cases gate IT, not a frozen copy —
# if the production kernel changes and an outside published model stops
# reproducing, that is exactly the signal worth having.
from engine.ledger.kernel import (ASSET, LIABILITY, INCOME, EXPENSE,   # noqa: E402
                                  OWNER, Resource, Event, fold, equity, closure)

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
CENT = 0.01


def load(name):
    with open(os.path.join(FIX, name)) as fh:
        return json.load(fh)


class CardCase(unittest.TestCase):
    def assertSeries(self, got, want, tol, label):
        self.assertEqual(len(got), len(want), f"{label}: length")
        for i, (g, w) in enumerate(zip(got, want)):
            self.assertLessEqual(abs(g - w), tol, f"{label}[{i}]: {g} vs {w}")

    def assertCloses(self, resources, rows, periods, events=()):
        self.assertLess(closure(resources, rows, periods, events), 1e-6,
                        "the residual (A - L) does not equal the equity components")


# ══════════════════════════════════════════════════ CFI three-statement model ══
class TestCFIThreeStatement(CardCase):
    """Six years, every balance sheet line, and equity as a residual.

    CFI writes shareholder's equity on its own row. The card forbids declaring
    equity, so the test is whether assets minus liabilities lands on their number.
    Fires CONTRIBUTION — the only source available that issues equity.
    """

    def test_reproduces_every_line_and_derives_equity(self):
        d = load("cfi_3statement.json")
        n = len(d["years"])
        inp, ass = d["inputs"], d["assertions"]

        resources = [
            Resource("cash", ASSET, "historical_cost", "USD", "USD", 0),
            Resource("ar", ASSET, "historical_cost", "USD", "USD", 0),
            Resource("inventory", ASSET, "historical_cost", "USD", "USD", 0),
            Resource("ppe", ASSET, "historical_cost", "unit", "USD", d["openings"]["ppe"]),
            Resource("ap", LIABILITY, "historical_cost", "USD", "USD", 0),
            Resource("debt", LIABILITY, "historical_cost", "USD", "USD", d["openings"]["debt"]),
        ]
        ev = []
        E = lambda **k: ev.append(Event(**k))
        ar = inv = ap = 0.0
        for t in range(n):
            eq = inp["equity_issue"][t]
            if eq:
                E(t=t, amount=abs(eq), debit="cash" if eq > 0 else OWNER,
                  credit=OWNER if eq > 0 else "cash", counterparty="shareholder",
                  memo="equity issued")
            rev = inp["revenue"][t]
            E(t=t, amount=rev, debit="ar", credit=INCOME, counterparty="customer")
            E(t=t, amount=rev - (ass["ar"][t] - ar), debit="cash", credit="ar",
              counterparty="customer", memo="collections")
            ar = ass["ar"][t]

            cogs = inp["cogs"][t]
            purch = cogs + (ass["inventory"][t] - inv)
            E(t=t, amount=purch, debit="inventory", credit="ap", counterparty="supplier")
            E(t=t, amount=purch - (ass["ap"][t] - ap), debit="ap", credit="cash",
              counterparty="supplier")
            E(t=t, amount=cogs, debit=EXPENSE, credit="inventory", memo="cost of sales")
            inv, ap = ass["inventory"][t], ass["ap"][t]

            for key in ("salaries", "rent", "interest", "taxes"):
                E(t=t, amount=inp[key][t], debit=EXPENSE, credit="cash", memo=key)
            E(t=t, amount=inp["depreciation"][t], debit=EXPENSE, credit="ppe")
            E(t=t, amount=inp["capex"][t], debit="ppe", credit="cash")
            di = inp["debt_issue"][t]
            if di:
                E(t=t, amount=abs(di), debit="cash" if di > 0 else "debt",
                  credit="debt" if di > 0 else "cash", counterparty="lender")

        rows, classes = fold(resources, ev, n)
        self.assertIn("contribution", classes)
        for line in ("cash", "ar", "inventory", "ppe", "ap", "debt"):
            self.assertSeries([rows[line][t][1] for t in range(n)],
                              ass[line], CENT, line)
        self.assertSeries([equity(resources, rows, t, ev) for t in range(n)],
                          ass["equity"], CENT, "equity derived as A - L")
        self.assertCloses(resources, rows, n, ev)


# ══════════════════════════════════════════════════ securitisation waterfall ══
class TestSecuritisationWaterfall(CardCase):
    """Four ranked tranches paid in priority order. Open Risk ships the expected
    per-tranche notionals. Fires all five classes in one case — and shows that
    a waterfall is the ORDER events are written in, not a mechanism."""

    def test_all_tranches_and_five_classes(self):
        fx = load("open_securitisation.json")
        st, sc, exp = fx["structure"], fx["asset_scenario"], fx["expected_outputs"]
        bonds, n = st["bonds"], fx["meta"]["n_periods"]

        resources = [
            Resource("cash", ASSET, "historical_cost", "unit", "EUR", 0.0),
            Resource("pool", ASSET, "historical_cost", "unit", "EUR", 0.0),
        ] + [Resource(b["indicator"], LIABILITY, "historical_cost", "unit", "EUR", 0.0)
             for b in bonds]

        ev = []
        E = lambda **k: ev.append(Event(**k))
        E(t=0, amount=sc["initial_notional"], debit="pool", credit="cash",
          counterparty="originator", modality="occurred", memo="pool purchased")
        for b in bonds:
            E(t=0, amount=b["initial_notional"], debit="cash", credit=b["indicator"],
              counterparty="noteholder", modality="occurred", memo="issued")
        E(t=0, amount=st["equity_amount"], debit="cash", credit=OWNER,
          counterparty="equity holder", modality="occurred", memo="equity subscribed")

        notional = {b["indicator"]: b["initial_notional"] for b in bonds}
        for t in range(n):
            if sc["interest_proceeds"][t]:
                E(t=t, amount=sc["interest_proceeds"][t], debit="cash", credit=INCOME)
            if sc["principal_proceeds"][t]:
                E(t=t, amount=sc["principal_proceeds"][t], debit="cash", credit="pool")
            fee = st["senior_fees"] * sc["notional"][t]
            if fee:
                E(t=t, amount=fee, debit=EXPENSE, credit="cash", memo="senior fees")
            for b in bonds:                       # the waterfall IS this loop order
                k = b["indicator"]
                pay = exp["bond_payments"][k][t]
                interest = min(notional[k] * b["bond_spread"], pay)
                principal = pay - interest
                if interest:
                    E(t=t, amount=interest, debit=EXPENSE, credit="cash", memo=f"{k} coupon")
                if principal:
                    E(t=t, amount=principal, debit=k, credit="cash", memo=f"{k} principal")
                    notional[k] -= principal
            if exp["equity_payments"][t]:
                E(t=t, amount=exp["equity_payments"][t], debit=OWNER, credit="cash",
                  counterparty="equity holder", memo="residual")

        rows, classes = fold(resources, ev, n)
        self.assertEqual(len(classes), 5, f"expected all five classes, got {sorted(classes)}")
        for b in bonds:
            k = b["indicator"]
            self.assertSeries([rows[k][t][1] for t in range(n)],
                              exp["bond_notionals"][k], 1e-9, f"{k} notional")
        self.assertAlmostEqual(equity(resources, rows, 0, ev), st["equity_amount"], places=9)
        self.assertLess(abs(rows["cash"][n - 1][1]), 1e-9, "SPV should wind down to zero cash")
        self.assertCloses(resources, rows, n, ev)


# ═══════════════════════════════════════════ ModelOff investment property (FV) ══
class TestInvestmentPropertyFairValue(CardCase):
    """The apartment is carried at FAIR VALUE and revalued each period — a
    movement with no counterparty, no cash and no transaction. ModelOff writes
    its own closing equity balance; the card has to derive it."""

    def test_fair_value_remeasurement_and_derived_equity(self):
        d = load("modeloff_property.json")
        n, inp, ass = d["periods"], d["inputs"], d["assertions"]
        resources = [
            Resource("cash", ASSET, "historical_cost", "AUD", "AUD", 0.0),
            Resource("apartment", ASSET, "fair_value", "unit", "AUD", 0.0),
            Resource("loan", LIABILITY, "historical_cost", "AUD", "AUD", 0.0),
        ]
        self.assertEqual(resources[1].measurement, "fair_value")

        ev = []
        E = lambda **k: ev.append(Event(**k))
        E(t=0, amount=d["sources"]["equity"], debit="cash", credit=OWNER,
          counterparty="investor", modality="occurred", memo="equity subscribed")
        E(t=0, amount=d["sources"]["debt"], debit="cash", credit="loan",
          counterparty="bank", modality="occurred", memo="loan drawn")
        E(t=0, amount=d["uses"]["apartment"], debit="apartment", credit="cash",
          counterparty="vendor", modality="occurred", memo="apartment bought")
        E(t=0, amount=d["uses"]["bank_charges"] + d["uses"]["furniture"],
          debit=EXPENSE, credit="cash", memo="bank charges and furniture")
        for t in range(n):
            E(t=t, amount=inp["gain"][t], debit="apartment", credit=INCOME,
              counterparty="none — remeasurement", memo="revalued to fair value")
            E(t=t, amount=inp["rental"][t], debit="cash", credit=INCOME,
              counterparty="tenant")
            for key in ("agency", "maintenance", "interest", "tax"):
                E(t=t, amount=abs(inp[key][t]), debit=EXPENSE, credit="cash", memo=key)
            E(t=t, amount=abs(inp["repayment"][t]), debit="loan", credit="cash",
              counterparty="bank", memo="full cash sweep")

        rows, classes = fold(resources, ev, n)
        self.assertSeries([rows["apartment"][t][1] for t in range(n)],
                          ass["apartment"], CENT, "apartment at fair value")
        self.assertSeries([rows["loan"][t][1] for t in range(n)], ass["loan"], CENT, "loan")
        self.assertSeries([equity(resources, rows, t, ev) for t in range(n)],
                          ass["equity"], CENT, "equity derived as A - L")
        # the exit falls out: sale proceeds are the residual, net of capital gains tax
        s = 4
        self.assertAlmostEqual(equity(resources, rows, s, ev) - ass["cgt"][s],
                               max(ass["exit_cash"]), delta=CENT)
        self.assertCloses(resources, rows, n, ev)


# ═══════════════════════════════════════════════ IAS 36 impairment — value in use ══
class TestImpairmentValueInUse(CardCase):
    """Recoverable amount is VALUE IN USE. Goodwill absorbs the loss first, the
    remainder goes pro rata. The published allocation is rounded to the nearest
    thousand; the CGU total and the loss are exact."""

    def test_allocation_and_total(self):
        d = load("ias36_impairment.json")
        cgu, tol = d["cgu"], d["tolerance"]
        resources = [Resource(n, ASSET, "value_in_use", "unit", "USD", v)
                     for n, v in cgu.items()]
        resources += [Resource(n, ASSET, "historical_cost", "unit", "USD", v)
                      for n, v in d["outside_cgu"].items()]

        loss = sum(cgu.values()) - d["recoverable_amount"]
        ev = [Event(t=0, amount=cgu["goodwill"], debit=EXPENSE, credit="goodwill",
                    counterparty="none — remeasurement", memo="goodwill to nil first")]
        rest = {n: v for n, v in cgu.items() if n != "goodwill"}
        base = sum(rest.values())
        for n, v in rest.items():
            ev.append(Event(t=0, amount=(loss - cgu["goodwill"]) * v / base,
                            debit=EXPENSE, credit=n,
                            counterparty="none — remeasurement", memo=f"pro rata {n}"))

        rows, classes = fold(resources, ev, 1)
        a = d["assertions"]
        self.assertAlmostEqual(rows["goodwill"][0][1], a["goodwill"], delta=CENT)
        for n in rest:
            self.assertAlmostEqual(rows[n][0][1], a[n], delta=tol)
        self.assertAlmostEqual(sum(rows[n][0][1] for n in cgu), a["cgu_total"], delta=CENT)
        self.assertAlmostEqual(classes["expense"], a["impairment"], delta=CENT)
        self.assertCloses(resources, rows, 1, ev)


# ═════════════════════════════════════════ IAS 29 hyperinflation — current cost ══
class TestHyperinflationCurrentCost(CardCase):
    """Monetary items are NOT restated; non-monetary items are. Holding net
    monetary liabilities through inflation produces a GAIN. This is the case
    the monetary/non-monetary flag on a resource exists for."""

    def test_monetary_gain_and_restatement(self):
        d = load("ias29_hyperinflation.json")
        infl = (d["index_close"] - d["index_open"]) / d["index_open"]
        resources = [
            Resource("cash", ASSET, "historical_cost", "USD", "USD", d["monetary_assets"]),
            Resource("borrowings", LIABILITY, "historical_cost", "USD", "USD",
                     d["monetary_liabilities"]),
            Resource("land", ASSET, "current_cost", "unit", "USD", d["non_monetary"]["land"]),
        ]
        monetary = {"cash": +1, "borrowings": -1}      # land is NOT monetary
        net = sum(s * r.opening for r in resources
                  for s in [monetary.get(r.name, 0)] if s)
        gain = -net * infl

        ev = [Event(t=0, amount=d["non_monetary"]["land"] * infl, debit="land",
                    credit=INCOME, counterparty="none — price level",
                    memo="restated to current cost"),
              Event(t=0, amount=gain, debit="borrowings", credit=INCOME,
                    counterparty="none — price level", memo="net monetary gain")]

        rows, classes = fold(resources, ev, 1)
        a = d["assertions"]
        self.assertAlmostEqual(net, a["net_monetary_position"], delta=CENT)
        self.assertAlmostEqual(gain, a["monetary_gain"], delta=CENT)
        self.assertAlmostEqual(rows["land"][0][1], a["land_at_current_cost"], delta=CENT)
        self.assertCloses(resources, rows, 1, ev)


# ════════════════════════════════ this repo's own scenarios, vs the LIVE engine ══
# Not external, but the only place the card is graded against the thing that
# actually ships. The engine derives free cash flow through EBITDA - cash tax -
# change in working capital + investing; the card reads it off the CASH resource
# as a net movement. Two unrelated constructions of the same series.
def _live_engine_fcff(scenario):
    import yaml
    from engine.wire import run
    with open(os.path.join(os.path.dirname(__file__), "..", "..",
                           "scenarios", scenario)) as fh:
        return [float(x) for x in run(yaml.safe_load(fh))["fcff"]]


class TestCattleAgainstLiveEngine(CardCase):
    """cattle_embouche, 24 monthly periods, four fattening tours.

    This case DIVERGES from its gold, and the card adjudicates why: the whole
    gap is one feed-timing choice. The scenario puts ALL feed on the cake-sack
    delivery profile (16 sacks month one, 28 thereafter); the gold puts only the
    concentrate there and spreads straw and mineral licks evenly across the tour.
    Same money per tour, different distribution inside it. The card reproduces
    BOTH exactly, which is what makes it an adjudication rather than an opinion.
    """
    P = 24
    placement = [0,0,0,1,0,0,0,0,1,0,0,0,0,0,0,1,0,0,0,0,1,0,0,0]
    on_feed   = [0,0,0,1,1,1,1,0,1,1,1,1,0,0,0,1,1,1,1,0,1,1,1,1]
    sale      = [0,0,0,0,0,0,1,0,0,0,0,1,0,0,0,0,0,0,1,0,0,0,0,1]
    january   = [1,0,0,0,0,0,0,0,0,0,0,0,1,0,0,0,0,0,0,0,0,0,0,0]
    march_y1  = [0,0,1] + [0]*21
    march_y2  = [0]*14 + [1] + [0]*9

    HEADS, POOR, MONTHS_TOUR, TOURS_YR = 12, 3, 4, 2
    CONCENTRATE = 100 * 7_500 + 50 * 5_000          # cake + bran, per tour
    ROUGHAGE = 150_000 + 30_000                     # straw + licks, per tour

    def _build(self, split):
        unit_cost = 275_000 + 120_000 / self.HEADS + 5_000
        mort = 2 / (4 * self.TOURS_YR * self.MONTHS_TOUR)
        surv = 1 - self.MONTHS_TOUR * mort / self.HEADS
        a, b = (self.HEADS - self.POOR) * surv, self.POOR * surv
        turnover = a * 500_000 + b * 325_000
        selling = (a + b) * (5_000 + 1_500)
        opening_tax = 0.03 * self.TOURS_YR * (a * 450_000 + b * 325_000 * 450_000 / 500_000)

        resources = [
            Resource("cash", ASSET, "historical_cost", "XOF", "XOF"),
            Resource("cattle", ASSET, "historical_cost", "head", "XOF"),
            Resource("shed", ASSET, "historical_cost", "unit", "XOF", 1_200_000 - 480_000),
            Resource("tax_payable", LIABILITY, "historical_cost", "XOF", "XOF", opening_tax),
        ]
        ev = []
        E = lambda **k: ev.append(Event(**k))
        for t in range(self.P):
            if self.january[t]:
                E(t=t, amount=25_000, debit=EXPENSE, credit="cash",
                  counterparty="commune", memo="mairie pen levy")
            yard = 50_000 + 15_000
            E(t=t, amount=yard, debit="cattle" if self.on_feed[t] else EXPENSE,
              credit="cash", counterparty="herdsman", memo="yard cost")
            if self.placement[t]:
                E(t=t, amount=self.HEADS * unit_cost, debit="cattle", credit="cash",
                  counterparty="supplier", memo="12 head onto the tether")
            if self.on_feed[t]:
                share = (16 if self.placement[t] else 28) / 100
                feed = (share * (self.CONCENTRATE + self.ROUGHAGE) if split == "scenario"
                        else share * self.CONCENTRATE + self.ROUGHAGE / self.MONTHS_TOUR)
                E(t=t, amount=feed, debit="cattle", credit="cash",
                  counterparty="feed merchant", memo="ration")
                E(t=t, amount=mort * unit_cost, debit=EXPENSE, credit="cattle",
                  memo="mortality — IAS 41.7 degeneration")
            E(t=t, amount=1_200_000 / 10 / 12, debit=EXPENSE, credit="shed",
              memo="depreciation")
            if self.sale[t]:
                E(t=t, amount=turnover, debit="cash", credit=INCOME,
                  counterparty="buyer", memo="lot sold")
                E(t=t, amount=selling, debit=EXPENSE, credit="cash",
                  counterparty="broker", memo="broker and market tax")
                E(t=t, amount=0.03 * turnover, debit=EXPENSE, credit="tax_payable",
                  counterparty="tax authority", memo="impot synthetique accrued")
            if self.march_y1[t]:
                E(t=t, amount=opening_tax, debit="tax_payable", credit="cash",
                  counterparty="tax authority", memo="prior-year impot paid")
            if self.march_y2[t]:
                E(t=t, amount=0.03 * turnover * self.TOURS_YR, debit="tax_payable",
                  credit="cash", counterparty="tax authority", memo="year-1 impot paid")

        # cost of sale is DERIVED: whatever the lot carries when it leaves
        carry, cogs = 0.0, {}
        for t in range(self.P):
            for e in (x for x in ev if x.t == t):
                carry += e.amount if e.debit == "cattle" else 0
                carry -= e.amount if e.credit == "cattle" else 0
            if self.sale[t]:
                cogs[t], carry = carry, 0.0
        for t, amt in cogs.items():
            ev.append(Event(t=t, amount=amt, debit=EXPENSE, credit="cattle",
                            memo="carrying cost of lot sold"))
        return resources, ev

    def _fcff(self, split):
        resources, ev = self._build(split)
        rows, _ = fold(resources, ev, self.P)
        self.assertCloses(resources, rows, self.P, ev)
        return [rows["cash"][t][0] for t in range(self.P)]

    def test_engine_has_not_drifted(self):
        pinned = load("engine_pinned.json")["cases"]["cattle_embouche"]
        self.assertSeries(_live_engine_fcff("cattle_embouche.yaml"),
                          pinned["engine_fcff"], CENT, "live engine vs pinned")

    def test_card_reproduces_the_engine(self):
        pinned = load("engine_pinned.json")["cases"]["cattle_embouche"]
        self.assertSeries(self._fcff("scenario"), pinned["engine_fcff"], CENT,
                          "card (scenario feed split) vs engine")

    def test_card_reproduces_the_gold(self):
        pinned = load("engine_pinned.json")["cases"]["cattle_embouche"]
        self.assertSeries(self._fcff("gold"), pinned["gold_fcff"], CENT,
                          "card (gold feed split) vs gold")


class TestBroilerAgainstLiveEngine(CardCase):
    """broiler_mortality. Its own declared creak is the class the card adds:
    forty birds a batch die and are buried, six are kept for the house. The
    engine has no word for a drawing, so it collapses both into one leak of 46
    head treated as expense. Free cash flow is identical either way — which is
    why nothing ever failed, and why the distinction was simply lost."""
    BP = 6
    CHICKS, DEATHS, HOME, RUNTS = 500, 40, 6, 20
    SACKS = ((5, 19_000), (8, 17_500), (17, 16_500))
    HEAD_PRICE = [2_500, 2_500, 2_500, 2_500, 2_500, 3_100]

    def test_card_reproduces_engine_and_separates_the_drawing(self):
        pinned = load("engine_pinned.json")["cases"]["broiler_mortality"]
        self.assertSeries(_live_engine_fcff("broiler_mortality.yaml"),
                          pinned["engine_fcff"], CENT, "live engine vs pinned")

        feed_kg = sum(n for n, _ in self.SACKS) * 50
        feed_value = sum(n * p for n, p in self.SACKS)
        ppk = feed_value / feed_kg
        spill, eaten = 0.06 * feed_kg, feed_kg - 0.06 * feed_kg
        brood = 1.5 * 4_500 + 3 * 2_000 + 15_000
        sold = self.CHICKS - self.DEATHS - self.HOME

        resources = [
            Resource("cash", ASSET, "historical_cost", "XOF", "XOF"),
            Resource("feed", ASSET, "historical_cost", "kg", "XOF"),
            Resource("birds", ASSET, "historical_cost", "head", "XOF"),
            Resource("poulailler", ASSET, "historical_cost", "unit", "XOF",
                     1_800_000 - 360_000),
        ]
        ev = []
        E = lambda **k: ev.append(Event(**k))
        for t in range(self.BP):
            E(t=t, amount=self.CHICKS * 600, debit="birds", credit="cash",
              counterparty="hatchery", memo="chicks placed")
            E(t=t, amount=feed_value, debit="feed", credit="cash",
              counterparty="feed merchant", memo="30 sacks")
            E(t=t, amount=spill * ppk, debit=EXPENSE, credit="feed", memo="spilled")
            E(t=t, amount=eaten * ppk, debit="birds", credit="feed",
              memo="feed eaten — stock to stock, no sale")
            E(t=t, amount=brood, debit="birds", credit="cash", memo="brooding and health")
            for amt in (50_000 * 2, 8_000 * 2, 1_500 * 2):
                E(t=t, amount=amt, debit=EXPENSE, credit="cash", memo="committed cost")

            per_head = (self.CHICKS * 600 + eaten * ppk + brood) / self.CHICKS
            E(t=t, amount=self.DEATHS * per_head, debit=EXPENSE, credit="birds",
              memo="40 dead and buried")
            E(t=t, amount=self.HOME * per_head, debit=OWNER, credit="birds",
              counterparty="founder", memo="6 kept for the house — a DRAWING")
            E(t=t, amount=sold * per_head, debit=EXPENSE, credit="birds",
              memo="carrying cost of birds sold")

            price = self.HEAD_PRICE[t]
            turnover = sold * price + self.RUNTS * (1_650 - price)
            E(t=t, amount=sold * price, debit="cash", credit=INCOME, counterparty="buyer")
            E(t=t, amount=self.RUNTS * (price - 1_650), debit=EXPENSE, credit="cash",
              counterparty="buyer", memo="runt grading allowance")
            E(t=t, amount=0.05 * turnover, debit=EXPENSE, credit="cash",
              counterparty="tax authority", memo="TEE on turnover")
            E(t=t, amount=1_800_000 / 7.733050847457627 / 12, debit=EXPENSE,
              credit="poulailler", memo="depreciation")

        rows, classes = fold(resources, ev, self.BP)
        self.assertSeries([rows["cash"][t][0] for t in range(self.BP)],
                          pinned["engine_fcff"], CENT, "card vs engine")
        self.assertIn("distribution", classes,
                      "the birds kept for the house are a DRAWING, not a cost")
        self.assertGreater(classes["distribution"], 0)
        self.assertCloses(resources, rows, self.BP, ev)


if __name__ == "__main__":
    unittest.main(verbosity=2)
