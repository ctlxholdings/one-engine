"""Dates (module card 3): cycles, stubs, end of month, business days, day counts.

Each rule here is one the ACTUS corpus prices (tests/test_case_actus.py runs
the whole corpus); these tests pin each rule on its own so a wrong one names
itself.
"""
from datetime import date as D
from fractions import Fraction as F

import pytest

from one_engine.dates import CalendarError, Cycle, instant, is_business_day, schedule, shift, year_fraction

I = instant


def iso(xs):
    return [x.date().isoformat() for x in xs]


def test_a_cycle_reads_step_unit_and_stub():
    assert Cycle.parse("P3ML1") == Cycle(3, "M", False)
    assert Cycle.parse("P27DL1").months is None and Cycle.parse("P1YL0").months == 12
    for bad in ("P1M", "1ML0", "P0ML0", "P1XL0"):
        with pytest.raises(CalendarError):
            Cycle.parse(bad)


def test_the_stub_flag_decides_the_last_period():
    # maturity off the monthly cycle: L0 merges the odd piece into the period before, L1 keeps it
    long_ = schedule(I("2013-01-01"), "P1ML0", I("2013-04-15"))
    short = schedule(I("2013-01-01"), "P1ML1", I("2013-04-15"))
    assert iso(long_) == ["2013-01-01", "2013-02-01", "2013-03-01", "2013-04-15"]
    assert iso(short) == ["2013-01-01", "2013-02-01", "2013-03-01", "2013-04-01", "2013-04-15"]
    # on the cycle there is no stub and the two agree
    assert schedule(I("2013-01-01"), "P1ML0", I("2013-04-01")) == schedule(I("2013-01-01"), "P1ML1", I("2013-04-01"))


def test_end_of_month_moves_a_month_end_anchor_only():
    assert iso(schedule(I("2013-04-30"), "P1ML1", I("2013-07-15"), "EOM"))[:3] == ["2013-04-30", "2013-05-31", "2013-06-30"]
    assert iso(schedule(I("2013-04-30"), "P1ML1", I("2013-07-15"), "SD"))[:3] == ["2013-04-30", "2013-05-30", "2013-06-30"]
    assert iso(schedule(I("2013-04-15"), "P1ML1", I("2013-06-15"), "EOM"))[:2] == ["2013-04-15", "2013-05-15"]
    assert iso(schedule(I("2013-01-31"), "P1ML1", I("2013-04-15"), "SD"))[:3] == ["2013-01-31", "2013-02-28", "2013-03-31"]


def test_a_holiday_list_on_the_weekday_calendar_moves_payments_off_holidays_too():
    # ModelOff 2014 "Precise Debt": Good Friday and Easter Monday 2015 are holidays
    easter = frozenset({D(2015, 4, 3), D(2015, 4, 6)})
    assert is_business_day(I("2015-04-02"), "MF", easter) and not is_business_day(I("2015-04-03"), "MF", easter)
    assert not is_business_day(I("2015-04-04"), "MF", easter) and is_business_day(I("2015-04-03"), "MF")
    assert shift(I("2015-04-03"), "SCMF", "MF", easter)[0] == I("2015-04-07")      # Friday, weekend, Monday
    assert shift(I("2015-04-03"), "SCMF", "MF")[0] == I("2015-04-03")              # no list: a weekday
    # modified following falls back to the last business day of the month, skipping holidays too
    assert shift(I("2016-10-30"), "SCMF", "MF", frozenset({D(2016, 10, 31)}))[0] == I("2016-10-28")
    assert shift(I("2016-10-30"), "SCMF", "MF", frozenset({D(2016, 10, 31), D(2016, 10, 28)}))[0] == I("2016-10-27")
    with pytest.raises(CalendarError, match="holiday"):
        shift(I("2015-04-03"), "SCMF", "NC", easter)


def test_calculate_shift_moves_the_payment_and_not_the_accrual():
    sat = I("2013-06-01")                                       # a Saturday
    assert shift(sat, "SCF", "MF") == (I("2013-06-03"), I("2013-06-03"))
    assert shift(sat, "CSF", "MF") == (I("2013-06-03"), sat)
    assert shift(I("2013-08-31"), "SCMF", "MF")[0] == I("2013-08-30")   # following would leave August
    assert shift(I("2013-08-31"), "SCF", "MF")[0] == I("2013-09-02")
    assert shift(sat, "SCF", "NC") == (sat, sat)
    with pytest.raises(CalendarError):
        shift(sat, "SCF", "TARGET")
    with pytest.raises(CalendarError):
        shift(sat, "MODFOLLOW", "MF")


def test_day_counts():
    a, b = I("2015-12-01"), I("2016-02-01")
    assert year_fraction("A365", a, b) == F(62, 365)
    assert year_fraction("A360", a, b) == F(62, 360)
    assert year_fraction("AA", a, b) == F(31, 365) + F(31, 366)
    assert year_fraction("30E360", I("2013-01-31"), I("2013-03-31")) == F(60, 360)
    assert year_fraction("30E360", I("2014-07-01"), I("2014-12-31")) == F(179, 360)    # pam19's last coupon
    assert year_fraction("A365", b, a) == 0
    with pytest.raises(CalendarError):
        year_fraction("BUS252", a, b)


def test_a_time_of_day_prices_as_the_next_midnight():
    # pam25 matures 2013-12-31T23:59:59 and prices its last coupon over 61 days, not 60
    assert year_fraction("A365", I("2013-11-01"), I("2013-12-31T23:59:59")) == F(61, 365)
    assert I("2013-12-31T23:59:59") > I("2013-12-31")
    assert I("2013-01-01T00:00") == I("2013-01-01")
    with pytest.raises(CalendarError):
        I("2013-01-01T12:00:00")


# ── days named by their rank in a month (ModelOff 2017 "Collect the Cash", step 7q) ──

import datetime as dt  # noqa: E402

from one_engine.dates import nth_business_day, nth_weekday  # noqa: E402


def test_the_third_thursday_and_a_fifth_weekday_a_month_lacks():
    assert nth_weekday(2018, 1, 3, 3) == dt.datetime(2018, 1, 18)
    assert nth_weekday(2018, 2, 3, 3) == dt.datetime(2018, 2, 15)          # February 2018 starts on a Thursday
    assert nth_weekday(2018, 2, 3, 1) == dt.datetime(2018, 2, 1)
    assert nth_weekday(2018, 3, 3, 5) == dt.datetime(2018, 3, 29)
    with pytest.raises(CalendarError, match="has no weekday"):
        nth_weekday(2018, 2, 3, 5)
    with pytest.raises(CalendarError, match="do not name a day"):
        nth_weekday(2018, 2, 7, 1)


def test_the_nth_business_day_skips_weekends_and_holidays():
    assert nth_business_day(2018, 9, 1, "MF") == dt.datetime(2018, 9, 3)    # 1 September 2018 is a Saturday
    assert nth_business_day(2018, 9, 3, "MF") == dt.datetime(2018, 9, 5)
    assert nth_business_day(2018, 9, 1, "MF", frozenset({dt.date(2018, 9, 3)})) == dt.datetime(2018, 9, 4)
    assert nth_business_day(2018, 9, 1, "NC") == dt.datetime(2018, 9, 1)
    with pytest.raises(CalendarError, match="fewer than 30"):
        nth_business_day(2018, 9, 30, "MF")
