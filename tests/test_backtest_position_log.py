"""Additive position-interval provenance (BACKTEST_SPEC.md Bölüm 36; VALIDATION_SPEC.md Bölüm 17.2.34).

The observer must not change any economic result: every run is compared with
the identical run without an observer. Interval times are derived by hand from
the Faz 4 timing contract (decision at N's close, fill at N+1's OPEN).
"""

from datetime import datetime
from decimal import Decimal

import pytest
import test_validation_pbo as base

from crypto_quant_lab.backtest.costs import ProportionalCommissionModel, ZeroCostModel
from crypto_quant_lab.backtest.models import BacktestConfig, PositionTarget
from crypto_quant_lab.backtest.position_log import PositionInterval, PositionIntervalRecorder
from crypto_quant_lab.backtest.replay import run_backtest_replay
from crypto_quant_lab.validation.rolling import (
    ContextAwareWindow,
    run_context_aware_rolling_backtest_from_store,
    run_context_aware_rolling_backtest_with_positions_from_store,
    run_rolling_backtest_from_store,
    run_rolling_backtest_with_positions_from_store,
)
from crypto_quant_lab.validation.windows import TemporalWindow

S, H = base.START, base.HOUR
CONFIG = BacktestConfig(initial_cash=Decimal(1000), position_quantity=Decimal(1))
CANDLES = tuple(base._candle(h, p).candle for h, p in enumerate(base.PRICES[:6]))


class Script:
    """Returns a fixed target per decision index."""

    def __init__(self, targets):
        self.targets, self.calls = targets, 0

    def target_position(self, context):
        target = self.targets[self.calls]
        self.calls += 1
        return target


L, F, SH = PositionTarget.LONG, PositionTarget.FLAT, PositionTarget.SHORT


def replay(targets, **kwargs):
    return run_backtest_replay(CANDLES, as_of_time=base.AS_OF_TIME, config=CONFIG,
                               policy=Script(targets), cost_model=ProportionalCommissionModel(
                                   rate=Decimal("0.001")), **kwargs)  # fmt: skip


def test_observer_never_changes_the_result_and_records_hand_derived_intervals():
    targets = [L, L, F, SH, L, L]  # decisions at the closes of candles 0..5
    recorder = PositionIntervalRecorder()
    with_observer = replay(targets, position_observer=recorder)
    assert with_observer == replay(targets)  # economics identical
    # decision 0 (LONG) fills at candle 1 open = S+1h; decision 2 (FLAT) exits at S+3h;
    # decision 3 (SHORT) enters at S+4h; decision 4 (LONG) reverses at S+5h; still open
    q = Decimal(1)
    assert recorder.intervals == (
        PositionInterval("LONG", q, S + H, S + 3 * H),
        PositionInterval("SHORT", q, S + 4 * H, S + 5 * H),
        PositionInterval("LONG", q, S + 5 * H, None),
    )
    assert recorder.transition_count == with_observer.trade_count == 5


def test_produces_return_at_follows_the_mark_timing():
    interval = PositionInterval("LONG", Decimal(1), S + H, S + 3 * H)
    # marks at S+1h (pre-fill), S+2h, S+3h (pre-exit), S+4h
    assert [interval.produces_return_at(S + k * H) for k in (1, 2, 3, 4)] == [
        False, True, True, False]  # fmt: skip
    open_end = PositionInterval("SHORT", Decimal(1), S + H, None)
    assert open_end.produces_return_at(S + 10 * H)


def test_recorder_and_interval_validation():
    recorder = PositionIntervalRecorder()
    with pytest.raises(ValueError, match="never saw opened"):
        recorder.on_fill(fill_time=S, old_quantity=Decimal(1), new_quantity=Decimal(0))
    recorder.on_fill(fill_time=S, old_quantity=Decimal(0), new_quantity=Decimal(1))
    with pytest.raises(ValueError, match="arrived while a position was recorded open"):
        PositionIntervalRecorder.on_fill(recorder, fill_time=S + H, old_quantity=Decimal(0),
                                         new_quantity=Decimal(1))  # fmt: skip
    with pytest.raises(ValueError, match="requires a position change"):
        recorder.on_fill(fill_time=S + H, old_quantity=Decimal(1), new_quantity=Decimal(1))
    with pytest.raises(ValueError, match="exit_time must be after entry_time"):
        PositionInterval("LONG", Decimal(1), S + H, S + H)
    with pytest.raises(ValueError, match="side must be"):
        PositionInterval("FLAT", Decimal(1), S, None)
    with pytest.raises(TypeError, match="position_observer must provide a callable on_fill"):
        replay([L] * 6, position_observer=object())


def _kwargs(policy):
    return {"policy_factory": policy, "exchange": base.EXCHANGE, "market_type": base.MARKET_TYPE,
            "symbol": base.SYMBOL, "timeframe": base.TIMEFRAME, "as_of_time": base.AS_OF_TIME,
            "config": CONFIG, "cost_model": ZeroCostModel()}  # fmt: skip


def test_rolling_with_positions_returns_the_unchanged_window_results(tmp_path):
    store = base.SQLiteHistoricalCandleStore(tmp_path / "c.db")
    try:
        store.write_batch([base._candle(h, p) for h, p in enumerate(base.PRICES)])
        windows = (TemporalWindow(start=S, end=S + 8 * H), TemporalWindow(start=S + 8 * H,
                                                                         end=S + 16 * H))  # fmt: skip
        for _, policy in base.POLICIES:
            results, intervals = run_rolling_backtest_with_positions_from_store(
                store, windows, **_kwargs(policy)
            )
            assert results == run_rolling_backtest_from_store(store, windows, **_kwargs(policy))
            assert len(intervals) == 2
            for window_result, window_intervals in zip(results, intervals, strict=True):
                transitions = sum(1 if i.exit_time is None else 2 for i in window_intervals)
                assert transitions == window_result.result.trade_count
                for interval in window_intervals:  # fills happen inside their own window
                    assert window_result.window.start < interval.entry_time
                    assert (
                        interval.exit_time is None or interval.exit_time < window_result.window.end
                    )
        context = tuple(ContextAwareWindow(context_start=w.start - H, evaluation=w)
                        for w in (TemporalWindow(start=S + H, end=S + 8 * H),))  # fmt: skip
        results, intervals = run_context_aware_rolling_backtest_with_positions_from_store(
            store, context, **_kwargs(base.POLICIES[2][1])
        )
        assert results == run_context_aware_rolling_backtest_from_store(
            store, context, **_kwargs(base.POLICIES[2][1])
        )
        assert isinstance(intervals[0][0].entry_time, datetime)
    finally:
        store.close()
