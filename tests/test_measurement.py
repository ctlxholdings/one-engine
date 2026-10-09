"""Measurement (card 10): straight-line register, declared first charge, NBV floor, disposal."""
from fractions import Fraction as F

import pytest

from one_engine.measurement import MeasurementError, Policy, Provision, Register


def test_first_charge_is_declared_and_moves_the_schedule():
    on = Register(Policy(life_periods=4, first_charge="in_service"))
    on.add(period=2, cost=100)
    assert [on.charge(t) for t in range(2, 7)] == [25, 25, 25, 25, 0]
    nxt = Register(Policy(life_periods=4, first_charge="next_period"))
    nxt.add(period=2, cost=100)
    assert [nxt.charge(t) for t in range(2, 8)] == [0, 25, 25, 25, 25, 0]
    with pytest.raises(MeasurementError):
        Policy(life_periods=4, first_charge="")                     # never defaulted


def test_nbv_floor_and_disposal_at_end():
    r = Register(Policy(life_periods=3, first_charge="in_service"))
    r.add(period=0, cost=F(10))
    r.add(period=1, cost=F(6))
    total = sum(r.charge(t) for t in range(0, 6))
    assert total == 16                                              # never below zero NBV
    r2 = Register(Policy(life_periods=10, first_charge="in_service"))
    r2.add(period=0, cost=100)
    for t in range(4):
        r2.charge(t)
    assert r2.nbv() == 60 and r2.dispose() == 60 and r2.nbv() == 0


def test_charge_periods_must_advance():
    r = Register(Policy(life_periods=3, first_charge="in_service"))
    r.add(period=0, cost=9)
    r.charge(0)
    with pytest.raises(MeasurementError):
        r.charge(0)                                                 # charged twice
    with pytest.raises(MeasurementError):
        r.add(period=-1, cost=1)


def test_provision_accrues_then_trues_up_and_pays_once():
    # the 50MW feature case: 2M over 40 quarters from Q4, settled at Q40
    p = Provision(2_000_000, start=4, periods=40, settle_at=40)
    charges = [p.charge(t) for t in range(41)]
    assert charges[3] == 0 and charges[4] == 50_000 and charges[39] == 50_000
    assert charges[40] == 200_000 and sum(charges) == 2_000_000
    assert [p.payment(t) for t in (39, 40)] == [0, 2_000_000]
