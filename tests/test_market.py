"""Market data (module card 7): dated series read at or before an instant."""
from fractions import Fraction as F

import pytest

from one_engine.dates import instant as I
from one_engine.market import Market, MarketError, Series


def test_an_exact_series_reads_only_its_observation_dates():
    s = Series.of("USD.SWP", [(I("2013-04-01"), "0.0105679"), (I("2013-07-01"), "0.0116790")])
    assert s.at(I("2013-04-01")) == F("0.0105679") and s.at(I("2013-07-01")) == F("0.0116790")
    for off in ("2013-06-30", "2014-01-01"):                        # between points, beyond the last
        with pytest.raises(MarketError, match="reads exact dates only"):
            s.at(I(off))


def test_reads_the_latest_observation_at_or_before():
    s = Series.of("CPI", [(I("2013-04-01"), "100"), (I("2013-07-01"), "101")], rule="step")
    assert s.at(I("2013-04-01")) == 100 and s.at(I("2013-06-30")) == 100 and s.at(I("2014-01-01")) == 101
    with pytest.raises(MarketError, match="not built"):
        Series.of("CPI", [(I("2013-04-01"), "100")], rule="linear")


def test_before_the_first_observation_refuses():
    s = Series.of("USD.SWP", [(I("2013-04-01"), "0.01")], rule="step")
    with pytest.raises(MarketError, match="no observation at or before"):
        s.at(I("2013-03-31"))


def test_a_series_must_move_forward_and_have_points():
    with pytest.raises(MarketError):
        Series.of("X", [(I("2013-04-01"), 1), (I("2013-04-01"), 2)])
    with pytest.raises(MarketError):
        Series.of("X", [])


def test_an_unknown_code_refuses():
    with pytest.raises(MarketError, match="no market series 'LIBOR'"):
        Market({}).series("LIBOR")
