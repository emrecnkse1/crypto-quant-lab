"""Descriptive parameter-neighborhood map (VALIDATION_SPEC.md Bölüm 17.7.1-17.7.8, 28.T).

A real parametric policy family (momentum threshold x direction) is run
through SQLite -> rolling backtest -> TrialGroup; the neighbor structure is
derived by hand from the grid, each Sharpe ratio is the candidate's own
Stage-2 value.
"""

from decimal import Decimal

import pytest
import test_validation_pbo as base

from crypto_quant_lab.backtest.costs import ZeroCostModel
from crypto_quant_lab.backtest.models import BacktestConfig, PositionTarget
from crypto_quant_lab.validation.candidate import Candidate, Trial
from crypto_quant_lab.validation.metrics import compute_stage2_metrics
from crypto_quant_lab.validation.parameter_stability import (
    MAP_SCOPE,
    ParameterNeighbor,
    map_parameter_neighborhoods,
)
from crypto_quant_lab.validation.rolling import run_rolling_backtest_from_store
from crypto_quant_lab.validation.trial_group import TrialGroup
from crypto_quant_lab.validation.windows import TemporalWindow

CONFIG = BacktestConfig(initial_cash=Decimal(1000), position_quantity=Decimal(1))
WINDOW = (TemporalWindow(start=base.START, end=base.START + base.HOUR * 16),)


class Momentum:
    """Enter `side` after a close-to-close move above `threshold`, otherwise flat."""

    def __init__(self, threshold, side):
        self.threshold, self.side = threshold, side

    def target_position(self, context):
        candles = context.candles
        if len(candles) < 2:
            return PositionTarget.FLAT
        move = candles[-1].close - candles[-2].close
        return self.side if move > self.threshold else PositionTarget.FLAT


def trial(store, threshold, side, *, windows=WINDOW, extra=()):
    results = run_rolling_backtest_from_store(
        store, windows, policy_factory=lambda: Momentum(Decimal(threshold), side),
        exchange=base.EXCHANGE, market_type=base.MARKET_TYPE, symbol=base.SYMBOL,
        timeframe=base.TIMEFRAME, as_of_time=base.AS_OF_TIME, config=CONFIG,
        cost_model=ZeroCostModel(),
    )  # fmt: skip
    parameters = (("side", side.value), ("threshold", Decimal(threshold))) + extra
    return Trial(candidate=Candidate(candidate_id=f"{side.value.lower()}-{threshold}",
                                     parameters=tuple(sorted(parameters))),
                 results=results, exchange=base.EXCHANGE, market_type=base.MARKET_TYPE,
                 symbol=base.SYMBOL, timeframe=base.TIMEFRAME, as_of_time=base.AS_OF_TIME,
                 config=CONFIG)  # fmt: skip


@pytest.fixture
def store(tmp_path):
    candle_store = base.SQLiteHistoricalCandleStore(tmp_path / "candles.db")
    candle_store.write_batch([base._candle(h, p) for h, p in enumerate(base.PRICES)])
    yield candle_store
    candle_store.close()


LONG, SHORT = PositionTarget.LONG, PositionTarget.SHORT


def test_real_parametric_family_neighbors(store):
    grid = [(0, LONG), (3, LONG), (5, LONG), (0, SHORT), (5, SHORT)]
    group = TrialGroup(group_id="momentum", trials=tuple(trial(store, t, s) for t, s in grid))
    result = map_parameter_neighborhoods(group)
    assert result.scope == MAP_SCOPE and "no stability score" in result.scope
    assert (result.ordered_parameters, result.categorical_parameters) == (("threshold",), ("side",))
    by_id = {c.candidate_id: c for c in result.candidates}
    shape = {cid: [(n.direction, n.candidate_id) for n in c.neighbors] for cid, c in by_id.items()}
    # thresholds per side: LONG {0, 3, 5}, SHORT {0, 5}; sides never neighbor each other
    assert shape == {
        "long-0": [("upper", "long-3")],
        "long-3": [("lower", "long-0"), ("upper", "long-5")],
        "long-5": [("lower", "long-3")],
        "short-0": [("upper", "short-5")],
        "short-5": [("lower", "short-0")],
    }
    for t in group.trials:  # every Sharpe is the candidate's own unchanged Stage-2 value
        own = compute_stage2_metrics(t.results[0].result).sharpe_ratio
        cid = t.candidate.candidate_id
        assert by_id[cid].sharpe_ratio == own
        for c in result.candidates:
            for n in c.neighbors:
                if n.candidate_id == cid:
                    assert n.sharpe_ratio == own
    assert by_id["long-3"].neighbors[1].value == Decimal(5)


def test_two_ordered_axes(store):
    grid = [(0, 1), (0, 2), (3, 1), (3, 2)]
    trials = tuple(trial(store, t, LONG, extra=(("size", s),)) for t, s in grid)
    trials = tuple(
        Trial(candidate=Candidate(candidate_id=f"t{t}-s{s}", parameters=tr.candidate.parameters),
              results=tr.results, exchange=tr.exchange, market_type=tr.market_type,
              symbol=tr.symbol, timeframe=tr.timeframe, as_of_time=tr.as_of_time,
              config=tr.config)
        for (t, s), tr in zip(grid, trials, strict=True)
    )  # fmt: skip
    result = map_parameter_neighborhoods(TrialGroup(group_id="grid", trials=trials))
    assert result.ordered_parameters == ("size", "threshold")
    corner = result.candidates[0]  # t0-s1: upper along size (t0-s2) and threshold (t3-s1)
    assert [(n.parameter, n.direction, n.candidate_id) for n in corner.neighbors] == [
        ("size", "upper", "t0-s2"), ("threshold", "upper", "t3-s1")]  # fmt: skip


def test_invalid_groups_are_refused(store):
    one = TrialGroup(group_id="one", trials=(trial(store, 0, LONG),))
    with pytest.raises(ValueError, match="^at least two candidates are required, got 1$"):
        map_parameter_neighborhoods(one)
    mixed = TrialGroup(group_id="mixed", trials=(trial(store, 0, LONG),
                                                trial(store, 3, LONG, extra=(("x", 1),))))  # fmt: skip
    with pytest.raises(ValueError, match=r"^trials\[1\] parameter keys .* differ from trials\[0\]"):
        map_parameter_neighborhoods(mixed)
    a, b = trial(store, 0, LONG), trial(store, 0, LONG)
    b = Trial(candidate=Candidate(candidate_id="dup", parameters=a.candidate.parameters),
              results=b.results, exchange=b.exchange, market_type=b.market_type, symbol=b.symbol,
              timeframe=b.timeframe, as_of_time=b.as_of_time, config=b.config)  # fmt: skip
    with pytest.raises(ValueError, match=r"^trials\[1\] repeats the parameters of trials\[0\]"):
        map_parameter_neighborhoods(TrialGroup(group_id="dup", trials=(a, b)))
    categorical = TrialGroup(group_id="cat", trials=(trial(store, 0, LONG), trial(store, 0, SHORT)))
    categorical = TrialGroup(group_id="cat", trials=tuple(
        Trial(candidate=Candidate(candidate_id=t.candidate.candidate_id,
                                  parameters=(("side", t.candidate.parameters[0][1]),)),
              results=t.results, exchange=t.exchange, market_type=t.market_type,
              symbol=t.symbol, timeframe=t.timeframe, as_of_time=t.as_of_time, config=t.config)
        for t in categorical.trials))  # fmt: skip
    with pytest.raises(ValueError, match="^no ordered .* a neighborhood is undefined$"):
        map_parameter_neighborhoods(categorical)
    two_windows = (TemporalWindow(start=base.START, end=base.START + base.HOUR * 8),
                   TemporalWindow(start=base.START + base.HOUR * 8,
                                  end=base.START + base.HOUR * 16))  # fmt: skip
    multi = TrialGroup(group_id="multi", trials=(trial(store, 0, LONG, windows=two_windows),
                                                trial(store, 3, LONG, windows=two_windows)))  # fmt: skip
    with pytest.raises(ValueError, match="multi-window pooling is not defined"):
        map_parameter_neighborhoods(multi)
    with pytest.raises(TypeError, match="^group must be a TrialGroup, got str$"):
        map_parameter_neighborhoods("g")
    with pytest.raises(ValueError, match="direction must be 'lower' or 'upper'"):
        ParameterNeighbor("k", "left", "c", 1, Decimal(0))


def test_deterministic_and_selects_nothing(store):
    group = TrialGroup(group_id="m", trials=(trial(store, 0, LONG), trial(store, 3, LONG)))
    assert map_parameter_neighborhoods(group) == map_parameter_neighborhoods(group)
    import crypto_quant_lab.validation.parameter_stability as module

    assert not {"select", "best", "rank", "score", "optimize"} & {n.lower() for n in dir(module)}
