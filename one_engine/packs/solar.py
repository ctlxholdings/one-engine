"""Solar asset generators: the production and prices a rooftop portfolio sells (plan step 8).

What this pack is
-----------------
An asset generator turns physical and commercial inputs into the series a
case's contracts are written on (an offtake's ``volume`` and ``price``). It
posts nothing: the case runner runs it before the books are built and
substitutes its outputs wherever the case file says
``{generator: name, output: x}`` (see :mod:`one_engine.cases`). The money
layers then treat those series like any other term, as DERIVED values.

Averta is the first case. Its live revenue engine
(01-averta ``engine_v2/revenue/revenue_engine.py``) has five layers:

* L0-L4, per climate zone: an hourly (8760) production profile, the hourly
  tariff, and their sumproduct, which reduces each zone to a quarterly yield
  (kWh per kWp in each calendar quarter) and two production-weighted rates
  (the regulated part of the tariff, peajes and cargos, and the market part,
  energy and margin);
* L5, the quarterly assembly, :func:`rooftop_revenue` here.

Generators
----------
``zone_sumproduct`` (Averta's L0-L4)
    For each zone, from its latitude and its twelve monthly yields (kWh per
    kWp, PVGIS) and a tariff calendar with a price per tariff period:

    * L0, the hourly profile: each day's curve is a Gaussian centred on
      solar noon, ``sigma`` a fifth of the day length, nil outside sunrise
      to sunset (the day length from the latitude and Cooper's
      declination); each month's curves are scaled so the month sums to its
      stated yield. This is the live engine's synthetic profile, which it
      uses when the hourly PVGIS files (``data/pvgis_hourly/*.npz``,
      gitignored) are absent;
    * L3, the hourly price: each hour's tariff period from the day type
      (a weekend or national holiday is ``D``; a weekday takes its month's
      season's type) and the type's period by hour; the regulated price of
      a period is (peaje + cargo) x the discount factor, the market price
      (market energy + margin) x the discount factor;
    * L4, the sumproduct: the zone's regulated and market rates are the
      production-weighted means of those hourly prices, and its quarterly
      yield is the sum of its months.

    The profile needs trigonometry, so the shares of each month's production
    falling in each tariff period are computed in floats; the monthly and
    quarterly yields stay exact. Output ``zones``, in the shape
    ``rooftop_revenue`` reads (weight, quarterly_yield, regulated_rate,
    market_rate), and ``period_shares`` (by zone, the share of the year's
    production in each tariff period).

    Not built: the hourly PVGIS files' TMY selection, and a mix of 2.0TD and
    3.0TD consumers (Averta's configuration is all 3.0TD).

``rooftop_revenue`` (Averta's L5)
    For each period from COD, with ``y`` the operating year
    (``(t - cod) // periods_per_year``) and ``q`` its quarter:

    * production = kWp x the portfolio's yield in quarter ``q`` (the zones'
      quarterly yields weighted by their allocation) x the PVGIS
      calibration x (1 - degradation) ** y x (1 - curtailment);
    * with a stated ``specific_yield`` (the P90 basis), every quarterly
      yield is scaled so the year sums to it;
    * the self-consumed part is production x the self-consumption ratio x
      the period's occupancy; the rest is surplus, sold to the grid;
    * occupancy starts at ``initial``; from the period after COD, each
      period a share ``churn_annual / periods_per_year`` of last period's
      occupants leaves, and the occupants who left ``lag`` periods earlier
      are replaced (``lag`` is the replacement time in periods, at least
      one); it is held within [0, 1];
    * the self-consumption price is the portfolio's effective rate (the
      zones' regulated plus market rates, weighted) x (1 + escalation) ** y
      x (1 - consumer default rate); the surplus price is the pool price x
      (1 + surplus escalation) ** y.

    Outputs: ``autoconsumo_volume``, ``autoconsumo_price``,
    ``surplus_volume``, ``surplus_price`` (one value per period, nil before
    COD), ``occupancy``, and the numbers behind them: ``effective_rate``,
    ``regulated_rate``, ``market_rate``, ``annual_yield`` (the portfolio's
    kWh per kWp a year, before calibration) and ``quarterly_yield``.

    Not built: the live engine's decomposed tariff stress (independent
    regulated and market escalation, a capture-ratio decline) and its
    tariff multipliers. The Averta configuration sets none of them; a case
    that names one refuses, since the reader reads no such key.

``rooftop_valuation`` (Averta's exit value, ``compute_year1_noi``,
``compute_forward_noi`` and ``compute_model_ev`` in ``run_averta.py``)
    The value a buyer pays for the portfolio: forward NOI capitalised at a
    cap rate. Per project:

    * year-1 revenue = kWp x the PVGIS calibration x (1 - the consumer
      default rate) x the zones' weighted revenue per kWp, each zone's being
      its annual yield x (the self-consumption ratio x its regulated plus
      market rate + the surplus share x the pool price). The default rate
      applies to the surplus too, as the live code applies it;
    * year-1 costs: each line's stated annual amount;
    * the forward year is the buyer's first, ``exit // periods_per_year +
      1``; a cohort started at period ``c`` is ``age = forward year - c /
      periods_per_year`` years old then (a fraction of a year for cohorts
      started mid-year). Its revenue is year-1 revenue x ((1 - degradation)
      x (1 + escalation)) ** age, each cost line its year-1 amount x (1 +
      its escalation) ** age;
    * forward NOI per project is the cohorts' NOI weighted by their counts;
      the portfolio's is that x the copies in the schedule; the value is
      the portfolio's NOI / the cap rate.

    Outputs: ``year1_revenue``, ``year1_costs`` (by line), ``year1_noi``,
    ``forward_noi`` (per project), ``portfolio_noi`` and ``value``.

    Declared: this is a second revenue path, separate from
    ``rooftop_revenue``. It applies the default rate (the SPV's prices
    carry it too, at the parameters' 1%), but no occupancy, and it
    escalates by fractional years where the SPV steps once a year; so year-1
    revenue here (17,393.74) is not the SPV's first operating year
    (16,773.42), and the P90 yield never reaches the value
    (``tests/test_case_averta_spv.py``). A fractional power is irrational,
    so each cohort's escalation factor is computed in floats.

All other arithmetic is exact (:class:`fractions.Fraction`); the live
engine's floats differ from it by rounding only.
"""
from __future__ import annotations

import datetime as _dt
import math
from fractions import Fraction
from typing import Any, Dict, List

from one_engine.inputs import InputError, Reader
from one_engine.units import exact


def _occupancy(initial: Fraction, churn_annual: Fraction, lag: int, n: int, per_year: int, cod: int) -> List[Fraction]:
    """Occupancy by period: churn from the period after COD, the departed replaced ``lag`` periods later."""
    occ = [initial] * n
    departed = [Fraction(0)] * n
    for t in range(cod + 1, n):
        departed[t] = occ[t - 1] * churn_annual / per_year
        replaced = departed[t - lag] if t >= cod + lag else Fraction(0)
        occ[t] = min(Fraction(1), max(Fraction(0), occ[t - 1] - departed[t] + replaced))
    return occ


def rooftop_revenue(r: Reader) -> Dict[str, Any]:
    n, per_year, cod = int(r.take("periods")), int(r.take("periods_per_year")), int(r.take("cod"))
    kwp, calibration = exact(r.take("kwp")), exact(r.take("calibration"))
    degradation, curtailment = exact(r.take("degradation")), exact(r.take("curtailment"))
    ratio = exact(r.take("autoconsumo_ratio"))

    o = Reader(r.take("occupancy"), source="rooftop_revenue.occupancy")
    initial, churn = exact(o.take("initial")), exact(o.take("churn_annual"))
    months = exact(o.take("replacement_months"))
    o.finish()
    # the replacement time in periods, rounded as the live engine rounds it (half to even), at least one
    lag = max(1, round(months / (Fraction(12) / per_year)))

    zones = dict(r.take("zones"))
    if not zones:
        raise InputError("rooftop_revenue: no zone is allocated")
    quarterly = [Fraction(0)] * per_year
    regulated = market = weights = Fraction(0)
    for name, raw in zones.items():
        z = Reader(raw, source=f"rooftop_revenue.zones.{name}")
        w = exact(z.take("weight"))
        qy = [exact(x) for x in z.take("quarterly_yield")]
        if len(qy) != per_year:
            raise InputError(f"rooftop_revenue: zone '{name}' has {len(qy)} quarterly yields, not {per_year}")
        quarterly = [a + w * b for a, b in zip(quarterly, qy)]
        regulated += w * exact(z.take("regulated_rate"))
        market += w * exact(z.take("market_rate"))
        weights += w
        z.finish()
    if weights != 1:
        raise InputError(f"rooftop_revenue: the zones' weights sum to {weights}, not 1")
    annual = sum(quarterly, Fraction(0))
    stated = r.take_or_default("specific_yield", None, reason="the zones' own yields are used (the P50 basis)").value
    if stated is not None:
        # the P90 basis: the same quarterly shape, scaled to the stated annual yield
        quarterly = [x * exact(stated) / annual for x in quarterly]
        annual = exact(stated)

    tr = Reader(r.take("tariff"), source="rooftop_revenue.tariff")
    escalation, default = exact(tr.take("escalation")), exact(tr.take("default_rate"))
    tr.finish()
    sr = Reader(r.take("surplus"), source="rooftop_revenue.surplus")
    pool, surplus_escalation = exact(sr.take("price")), exact(sr.take("escalation"))
    sr.finish()

    occ = _occupancy(initial, churn, lag, n, per_year, cod)
    rate = regulated + market
    zero = [Fraction(0)] * n
    ac_v, ac_p, su_v, su_p = list(zero), list(zero), list(zero), list(zero)
    for t in range(cod, n):
        year, q = divmod(t - cod, per_year)
        production = kwp * quarterly[q] * calibration * (1 - degradation) ** year * (1 - curtailment)
        ac_v[t] = production * ratio * occ[t]
        su_v[t] = production - ac_v[t]
        ac_p[t] = rate * (1 + escalation) ** year * (1 - default)
        su_p[t] = pool * (1 + surplus_escalation) ** year
    return {"autoconsumo_volume": ac_v, "autoconsumo_price": ac_p, "surplus_volume": su_v, "surplus_price": su_p,
            "occupancy": occ, "effective_rate": rate, "regulated_rate": regulated, "market_rate": market,
            "annual_yield": annual, "quarterly_yield": quarterly}


def _day_curve(latitude: float, doy: int) -> List[float]:
    """One day's unscaled hourly curve: a Gaussian on solar noon, nil outside sunrise to sunset."""
    decl = math.radians(23.45) * math.sin(math.radians(360 / 365 * (284 + doy)))
    cos_ha = min(1.0, max(-1.0, -math.tan(math.radians(latitude)) * math.tan(decl)))
    daylight = 2 * math.degrees(math.acos(cos_ha)) / 15
    sunrise, sunset = max(0.0, 12.0 - daylight / 2), min(24.0, 12.0 + daylight / 2)
    noon, sigma = (sunrise + sunset) / 2, (sunset - sunrise) / 5.0
    return [math.exp(-0.5 * ((h + 0.5 - noon) / sigma) ** 2) if sunrise <= h + 0.5 <= sunset else 0.0
            for h in range(24)]


def _date(x: Any) -> _dt.date:
    return x if isinstance(x, _dt.date) else _dt.date.fromisoformat(str(x))


def zone_sumproduct(r: Reader) -> Dict[str, Any]:
    year = int(r.take("year"))
    if _dt.date(year, 12, 31).timetuple().tm_yday != 365:
        raise InputError(f"zone_sumproduct: {year} is a leap year; the 8760-hour grid is not built for it")
    cr = Reader(r.take("calendar"), source="zone_sumproduct.calendar")
    seasons = list(cr.take("season_by_month"))
    day_types = dict(cr.take("day_type_by_season"))
    rest_day = cr.take("rest_day_type")
    by_hour = {k: [int(p) for p in v] for k, v in dict(cr.take("period_by_hour")).items()}
    holidays = {_date(d) for d in cr.take("holidays")}
    cr.finish()
    if len(seasons) != 12 or any(len(v) != 24 for v in by_hour.values()):
        raise InputError("zone_sumproduct: a season for each of 12 months and a period for each of 24 hours")
    pr = Reader(r.take("prices"), source="zone_sumproduct.prices")
    peaje, cargo, market = ({int(k): exact(v) for k, v in dict(pr.take(x)).items()} for x in ("peaje", "cargo", "market"))
    margin, discount = exact(pr.take("margin")), exact(pr.take("discount_factor"))
    pr.finish()
    periods = sorted({p for v in by_hour.values() for p in v})
    if sorted(peaje) != periods or sorted(cargo) != periods or sorted(market) != periods:
        raise InputError(f"zone_sumproduct: a peaje, cargo and market price for each period {periods}")
    regulated_price = {p: (peaje[p] + cargo[p]) * discount for p in periods}
    market_price = {p: (market[p] + margin) * discount for p in periods}

    # each day's tariff period by hour: the calendar does not depend on the zone
    days = [_dt.date(year, 1, 1) + _dt.timedelta(days=k) for k in range(365)]
    day_periods = [by_hour[rest_day if d.weekday() >= 5 or d in holidays else day_types[seasons[d.month - 1]]]
                   for d in days]

    zones, shares = {}, {}
    for name, raw in dict(r.take("zones")).items():
        z = Reader(raw, source=f"zone_sumproduct.zones.{name}")
        weight, latitude = z.take("weight"), float(exact(z.take("latitude")))
        monthly = [exact(x) for x in z.take("monthly_yield")]
        z.finish()
        if len(monthly) != 12:
            raise InputError(f"zone_sumproduct: zone '{name}' has {len(monthly)} monthly yields, not 12")
        # the share of each month's production in each tariff period (the scaling cancels in a share)
        by_period = {p: Fraction(0) for p in periods}
        for m in range(12):
            month_days = [k for k, d in enumerate(days) if d.month == m + 1]
            raw_by_period = {p: 0.0 for p in periods}
            for k in month_days:
                for h, x in enumerate(_day_curve(latitude, k + 1)):
                    raw_by_period[day_periods[k][h]] += x
            total = sum(raw_by_period.values())
            for p in periods:
                by_period[p] += monthly[m] * exact(raw_by_period[p] / total)
        annual = sum(monthly, Fraction(0))
        shares[name] = {p: by_period[p] / annual for p in periods}
        zones[name] = {
            "weight": weight,
            "quarterly_yield": [sum(monthly[3 * q:3 * q + 3], Fraction(0)) for q in range(4)],
            "regulated_rate": sum((shares[name][p] * regulated_price[p] for p in periods), Fraction(0)),
            "market_rate": sum((shares[name][p] * market_price[p] for p in periods), Fraction(0)),
        }
    return {"zones": zones, "period_shares": shares}


def rooftop_valuation(r: Reader) -> Dict[str, Any]:
    kwp, calibration = exact(r.take("kwp")), exact(r.take("calibration"))
    ratio, default = exact(r.take("autoconsumo_ratio")), exact(r.take("default_rate"))
    pool = exact(r.take("surplus_price"))
    per_kwp = weights = Fraction(0)
    for name, raw in dict(r.take("zones")).items():
        z = Reader(raw, source=f"rooftop_valuation.zones.{name}")
        w = exact(z.take("weight"))
        annual = sum((exact(x) for x in z.take("quarterly_yield")), Fraction(0))
        rate = exact(z.take("regulated_rate")) + exact(z.take("market_rate"))
        z.finish()
        per_kwp += w * annual * (ratio * rate + (1 - ratio) * pool)
        weights += w
    if weights != 1:
        raise InputError(f"rooftop_valuation: the zones' weights sum to {weights}, not 1")
    revenue = kwp * calibration * (1 - default) * per_kwp

    costs, escalations = {}, {}
    for name, raw in dict(r.take("costs")).items():
        c = Reader(raw, source=f"rooftop_valuation.costs.{name}")
        costs[name], escalations[name] = exact(c.take("annual")), float(exact(c.take("escalation")))
        c.finish()
    g = Reader(r.take("revenue_growth"), source="rooftop_valuation.revenue_growth")
    degradation, escalation = float(exact(g.take("degradation"))), float(exact(g.take("escalation")))
    g.finish()

    schedule = [exact(x) for x in r.take("schedule")]
    per_year, exit_period = int(r.take("periods_per_year")), int(r.take("exit"))
    cap_rate = exact(r.take("cap_rate"))
    count = sum(schedule, Fraction(0))
    if count <= 0:
        raise InputError("rooftop_valuation: the schedule starts no copies")
    forward_year = exit_period // per_year + 1
    weighted = Fraction(0)
    for c, n in enumerate(schedule):
        if not n:
            continue
        age = forward_year - c / per_year
        noi = revenue * exact(((1 - degradation) ** age) * ((1 + escalation) ** age))
        noi -= sum((costs[k] * exact((1 + escalations[k]) ** age) for k in costs), Fraction(0))
        weighted += n * noi
    forward = weighted / count
    return {"year1_revenue": revenue, "year1_costs": costs, "year1_noi": revenue - sum(costs.values(), Fraction(0)),
            "forward_noi": forward, "portfolio_noi": forward * count, "value": forward * count / cap_rate}


GENERATORS = {"zone_sumproduct": zone_sumproduct, "rooftop_revenue": rooftop_revenue,
              "rooftop_valuation": rooftop_valuation}
