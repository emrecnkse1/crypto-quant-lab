from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal

import pytest

from crypto_quant_lab.backtest.costs import ZeroCostModel
from crypto_quant_lab.backtest.models import BacktestConfig, PositionTarget
from crypto_quant_lab.backtest.replay import run_backtest_replay
from crypto_quant_lab.market_data.models import Candle


class _BrokenTzInfo(tzinfo):
    """A tzinfo that pretends to be attached but reports no offset (pseudo-naive)."""

    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return None


class _RecordingPolicy:
    def __init__(self, target_fn):
        self._target_fn = target_fn
        self.contexts = []

    def target_position(self, context):
        self.contexts.append(context)
        return self._target_fn(context)


class _BadPolicy:
    def target_position(self, context):
        return "LONG"


class _RecordingCostModel:
    def __init__(self, cost=Decimal(0)):
        self._cost = cost
        self.calls = []

    def calculate_cost(self, *, quantity, execution_price):
        self.calls.append((quantity, execution_price))
        return self._cost


def _candle(open_time, *, open_, high, low, close, symbol="BTCUSDT", timeframe="1h"):
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=Decimal(1),
    )


def _sequential_candles(n, *, start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC), price=Decimal(100)):
    step = timedelta(hours=1)
    candles = []
    t = start
    for _ in range(n):
        candles.append(_candle(t, open_=price, high=price, low=price, close=price))
        t = t + step
    return tuple(candles)


def _config(initial_cash=Decimal(1000), position_quantity=Decimal(1)):
    return BacktestConfig(initial_cash=initial_cash, position_quantity=position_quantity)


def _flat_policy():
    return _RecordingPolicy(lambda context: PositionTarget.FLAT)


def _generous_as_of(candles):
    return candles[-1].open_time + timedelta(days=1)


# --- dataset validation ---


def test_candles_list_is_rejected():
    candles = list(_sequential_candles(2))
    with pytest.raises(TypeError, match="candles"):
        run_backtest_replay(
            candles,
            as_of_time=_generous_as_of(candles),
            config=_config(),
            policy=_flat_policy(),
            cost_model=ZeroCostModel(),
        )


def test_empty_candles_is_rejected():
    with pytest.raises(ValueError, match="empty"):
        run_backtest_replay(
            (),
            as_of_time=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            config=_config(),
            policy=_flat_policy(),
            cost_model=ZeroCostModel(),
        )


def test_non_candle_element_is_rejected():
    with pytest.raises(TypeError, match="candles"):
        run_backtest_replay(
            ("not a candle",),
            as_of_time=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            config=_config(),
            policy=_flat_policy(),
            cost_model=ZeroCostModel(),
        )


def test_duplicate_open_time_is_rejected():
    candle = _candle(
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        open_=Decimal(100),
        high=Decimal(100),
        low=Decimal(100),
        close=Decimal(100),
    )
    candles = (candle, candle)
    with pytest.raises(ValueError, match="ascending"):
        run_backtest_replay(
            candles,
            as_of_time=_generous_as_of(candles),
            config=_config(),
            policy=_flat_policy(),
            cost_model=ZeroCostModel(),
        )


def test_backward_open_time_is_rejected():
    candles = (
        _candle(
            datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            open_=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
        ),
        _candle(
            datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            open_=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
        ),
    )
    with pytest.raises(ValueError, match="ascending"):
        run_backtest_replay(
            candles,
            as_of_time=datetime(2024, 1, 1, 20, 0, tzinfo=UTC),
            config=_config(),
            policy=_flat_policy(),
            cost_model=ZeroCostModel(),
        )


def test_mixed_symbol_is_rejected():
    candles = (
        _candle(
            datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            open_=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
            symbol="BTCUSDT",
        ),
        _candle(
            datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            open_=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
            symbol="ETHUSDT",
        ),
    )
    with pytest.raises(ValueError, match="symbol"):
        run_backtest_replay(
            candles,
            as_of_time=_generous_as_of(candles),
            config=_config(),
            policy=_flat_policy(),
            cost_model=ZeroCostModel(),
        )


def test_mixed_timeframe_is_rejected():
    candles = (
        _candle(
            datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            open_=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
            timeframe="1h",
        ),
        _candle(
            datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            open_=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
            timeframe="4h",
        ),
    )
    with pytest.raises(ValueError, match="timeframe"):
        run_backtest_replay(
            candles,
            as_of_time=_generous_as_of(candles),
            config=_config(),
            policy=_flat_policy(),
            cost_model=ZeroCostModel(),
        )


def test_gap_between_candles_is_rejected():
    candles = (
        _candle(
            datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            open_=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
        ),
        _candle(
            datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            open_=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
        ),
    )
    with pytest.raises(ValueError, match="contiguous"):
        run_backtest_replay(
            candles,
            as_of_time=datetime(2024, 1, 1, 20, 0, tzinfo=UTC),
            config=_config(),
            policy=_flat_policy(),
            cost_model=ZeroCostModel(),
        )


def test_unsupported_timeframe_is_rejected():
    candles = (
        _candle(
            datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            open_=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
            timeframe="15m",
        ),
    )
    with pytest.raises(ValueError, match="unsupported timeframe"):
        run_backtest_replay(
            candles,
            as_of_time=datetime(2024, 1, 1, 20, 0, tzinfo=UTC),
            config=_config(),
            policy=_flat_policy(),
            cost_model=ZeroCostModel(),
        )


def test_wrong_config_type_is_rejected():
    candles = _sequential_candles(2)
    with pytest.raises(TypeError, match="config"):
        run_backtest_replay(
            candles,
            as_of_time=_generous_as_of(candles),
            config="not a config",
            policy=_flat_policy(),
            cost_model=ZeroCostModel(),
        )


# --- global as_of ---


def test_candle_unavailable_relative_to_global_as_of_is_rejected():
    candles = _sequential_candles(2)  # availabilities: 11:00, 12:00
    with pytest.raises(ValueError, match="as_of_time"):
        run_backtest_replay(
            candles,
            as_of_time=datetime(2024, 1, 1, 11, 30, tzinfo=UTC),
            config=_config(),
            policy=_flat_policy(),
            cost_model=ZeroCostModel(),
        )


def test_exact_final_availability_boundary_as_of_is_accepted():
    candles = _sequential_candles(2)  # final availability: 12:00
    result = run_backtest_replay(
        candles,
        as_of_time=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        config=_config(),
        policy=_flat_policy(),
        cost_model=ZeroCostModel(),
    )
    assert result.final_equity == Decimal(1000)


def test_global_as_of_later_than_dataset_end_is_accepted():
    candles = _sequential_candles(2)
    result = run_backtest_replay(
        candles,
        as_of_time=datetime(2024, 1, 2, 0, 0, tzinfo=UTC),
        config=_config(),
        policy=_flat_policy(),
        cost_model=ZeroCostModel(),
    )
    assert result.final_equity == Decimal(1000)


def test_naive_as_of_time_is_rejected():
    candles = _sequential_candles(2)
    with pytest.raises(ValueError):
        run_backtest_replay(
            candles,
            as_of_time=datetime(2024, 1, 1, 20, 0),  # noqa: DTZ001
            config=_config(),
            policy=_flat_policy(),
            cost_model=ZeroCostModel(),
        )


def test_pseudo_naive_as_of_time_is_rejected():
    candles = _sequential_candles(2)
    with pytest.raises(ValueError):
        run_backtest_replay(
            candles,
            as_of_time=datetime(2024, 1, 1, 20, 0, tzinfo=_BrokenTzInfo()),
            config=_config(),
            policy=_flat_policy(),
            cost_model=ZeroCostModel(),
        )


def test_non_utc_aware_equivalent_as_of_time_is_accepted():
    plus_five = timezone(timedelta(hours=5))
    candles = _sequential_candles(2)  # final availability: 12:00 UTC
    result = run_backtest_replay(
        candles,
        as_of_time=datetime(2024, 1, 1, 17, 0, tzinfo=plus_five),  # == 12:00 UTC
        config=_config(),
        policy=_flat_policy(),
        cost_model=ZeroCostModel(),
    )
    assert result.final_equity == Decimal(1000)


# --- prevalidation atomicity ---


def test_invalid_dataset_causes_zero_policy_calls():
    candles = (
        _candle(
            datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            open_=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
        ),
        _candle(
            datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            open_=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
        ),
        _candle(
            datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            open_=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
        ),
        _candle(
            datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
            open_=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
        ),  # gap
    )
    policy = _flat_policy()
    with pytest.raises(ValueError, match="contiguous"):
        run_backtest_replay(
            candles,
            as_of_time=datetime(2024, 1, 2, 0, 0, tzinfo=UTC),
            config=_config(),
            policy=policy,
            cost_model=ZeroCostModel(),
        )
    assert policy.contexts == []


def test_invalid_dataset_causes_zero_cost_calls():
    candles = (
        _candle(
            datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            open_=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
        ),
        _candle(
            datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            open_=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
        ),
        _candle(
            datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            open_=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
        ),
        _candle(
            datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
            open_=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
        ),  # gap
    )
    cost_model = _RecordingCostModel()
    with pytest.raises(ValueError, match="contiguous"):
        run_backtest_replay(
            candles,
            as_of_time=datetime(2024, 1, 2, 0, 0, tzinfo=UTC),
            config=_config(),
            policy=_RecordingPolicy(lambda context: PositionTarget.LONG),
            cost_model=cost_model,
        )
    assert cost_model.calls == []


# --- NO TRADE ---


def test_always_flat_result_fields():
    candles = _sequential_candles(4)
    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(initial_cash=Decimal(1000)),
        policy=_flat_policy(),
        cost_model=ZeroCostModel(),
    )
    assert result.fill_count == 0
    assert result.trade_count == 0
    assert result.total_cost == Decimal(0)
    assert result.total_realized_pnl == Decimal(0)
    assert result.total_unrealized_pnl == Decimal(0)
    assert result.total_pnl == Decimal(0)
    assert result.final_equity == Decimal(1000)


def test_always_flat_curve_length_matches_candles():
    candles = _sequential_candles(4)
    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=_flat_policy(),
        cost_model=ZeroCostModel(),
    )
    assert len(result.equity_curve) == 4


def test_always_flat_curve_every_point_equals_initial_cash():
    candles = _sequential_candles(4)
    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(initial_cash=Decimal(1000)),
        policy=_flat_policy(),
        cost_model=ZeroCostModel(),
    )
    assert all(point.equity == Decimal(1000) for point in result.equity_curve)


# --- one-candle dataset ---


def test_one_candle_dataset_is_legal_no_trade():
    candles = _sequential_candles(1)
    policy = _RecordingPolicy(
        lambda context: PositionTarget.LONG
    )  # signal discarded: no next candle
    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=policy,
        cost_model=ZeroCostModel(),
    )
    assert len(policy.contexts) == 1
    assert result.fill_count == 0
    assert result.trade_count == 0
    assert len(result.equity_curve) == 1
    assert result.final_equity == Decimal(1000)


# --- context / anti-lookahead ---


def test_context_prefix_lengths_and_no_future_candles():
    candles = _sequential_candles(4)
    policy = _flat_policy()
    run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=policy,
        cost_model=ZeroCostModel(),
    )
    assert len(policy.contexts) == 4
    for i, context in enumerate(policy.contexts):
        assert context.candles == candles[: i + 1]


def test_context_as_of_time_matches_availability_of_last_candle():
    candles = _sequential_candles(4)
    policy = _flat_policy()
    run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=policy,
        cost_model=ZeroCostModel(),
    )
    for context in policy.contexts:
        expected = context.candles[-1].open_time + timedelta(hours=1)
        assert context.as_of_time == expected


def test_last_candle_policy_is_called():
    candles = _sequential_candles(4)
    policy = _flat_policy()
    run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=policy,
        cost_model=ZeroCostModel(),
    )
    assert policy.contexts[-1].candles == candles


# --- execution integration ---


def test_first_fill_uses_next_candle_open_not_signal_candle_close():
    candles = (
        _candle(
            datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            open_=Decimal(90),
            high=Decimal(200),
            low=Decimal(80),
            close=Decimal(123),
        ),
        _candle(
            datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            open_=Decimal(500),
            high=Decimal(510),
            low=Decimal(490),
            close=Decimal(505),
        ),
    )
    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(initial_cash=Decimal(1000), position_quantity=Decimal(1)),
        policy=_RecordingPolicy(lambda context: PositionTarget.LONG),
        cost_model=ZeroCostModel(),
    )
    assert result.final_cash == Decimal(500)  # 1000 - 500 (next-open), not 1000 - 123
    assert result.total_unrealized_pnl == Decimal(5)  # (505 - 500) * 1
    assert result.fill_count == 1
    assert result.trade_count == 1


def test_repeated_target_produces_no_additional_fill():
    candles = _sequential_candles(3)
    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=_RecordingPolicy(lambda context: PositionTarget.LONG),
        cost_model=ZeroCostModel(),
    )
    assert result.fill_count == 1
    assert result.trade_count == 1


def test_last_candle_signal_produces_no_fill():
    candles = _sequential_candles(3)

    def _target(context):
        return PositionTarget.LONG if len(context.candles) == 3 else PositionTarget.FLAT

    cost_model = _RecordingCostModel()
    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=_RecordingPolicy(_target),
        cost_model=cost_model,
    )
    assert result.fill_count == 0
    assert result.trade_count == 0
    assert cost_model.calls == []
    assert result.total_cost == Decimal(0)


def test_no_fabricated_final_close_leaves_position_open():
    candles = _sequential_candles(3)

    def _target(context):
        length = len(context.candles)
        if length == 1:
            return PositionTarget.LONG
        if length == 2:
            return PositionTarget.LONG  # no-op
        return PositionTarget.FLAT  # last candle: would close, but no next candle exists

    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=_RecordingPolicy(_target),
        cost_model=ZeroCostModel(),
    )
    assert result.fill_count == 1
    assert result.trade_count == 1
    assert result.final_cash != result.final_equity  # position still open


# --- counts ---


def test_open_then_close_counts():
    candles = _sequential_candles(3)

    def _target(context):
        length = len(context.candles)
        if length == 1:
            return PositionTarget.LONG
        return PositionTarget.FLAT

    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=_RecordingPolicy(_target),
        cost_model=ZeroCostModel(),
    )
    assert result.fill_count == 2
    assert result.trade_count == 2


def test_reversal_counts_and_single_cost_call():
    candles = _sequential_candles(4)

    def _target(context):
        length = len(context.candles)
        if length == 1:
            return PositionTarget.LONG
        return PositionTarget.SHORT

    cost_model = _RecordingCostModel(cost=Decimal(0))
    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(position_quantity=Decimal(1)),
        policy=_RecordingPolicy(_target),
        cost_model=cost_model,
    )
    assert result.fill_count == 2
    assert result.trade_count == 3  # open(1) + reversal(2)
    assert len(cost_model.calls) == 2
    assert cost_model.calls[1][0] == Decimal(2)  # reversal fill quantity == 2q


# --- cost ---


def test_zero_cost_model_full_run_total_cost_zero():
    candles = _sequential_candles(3)

    def _target(context):
        return PositionTarget.LONG if len(context.candles) == 1 else PositionTarget.FLAT

    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=_RecordingPolicy(_target),
        cost_model=ZeroCostModel(),
    )
    assert result.total_cost == Decimal(0)


def test_fixed_cost_model_cumulative_total():
    candles = _sequential_candles(3)

    def _target(context):
        return PositionTarget.LONG if len(context.candles) == 1 else PositionTarget.FLAT

    cost_model = _RecordingCostModel(cost=Decimal(2))
    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=_RecordingPolicy(_target),
        cost_model=cost_model,
    )
    assert result.total_cost == Decimal(4)
    assert len(cost_model.calls) == 2


def test_negative_rebate_cost_supported():
    candles = _sequential_candles(2)
    cost_model = _RecordingCostModel(cost=Decimal(-1))
    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=_RecordingPolicy(lambda context: PositionTarget.LONG),
        cost_model=cost_model,
    )
    assert result.total_cost == Decimal(-1)


# --- result ---


def test_open_position_at_end_no_forced_close():
    candles = (
        _candle(
            datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            open_=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
        ),
        _candle(
            datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            open_=Decimal(100),
            high=Decimal(110),
            low=Decimal(90),
            close=Decimal(110),
        ),
    )
    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(initial_cash=Decimal(1000), position_quantity=Decimal(1)),
        policy=_RecordingPolicy(lambda context: PositionTarget.LONG),
        cost_model=ZeroCostModel(),
    )
    assert result.final_cash != result.final_equity
    assert result.total_unrealized_pnl == Decimal(10)  # (110 - 100) * 1


def test_final_equity_point_matches_final_equity():
    candles = _sequential_candles(3)

    def _target(context):
        return PositionTarget.LONG if len(context.candles) == 1 else PositionTarget.FLAT

    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=_RecordingPolicy(_target),
        cost_model=ZeroCostModel(),
    )
    assert result.equity_curve[-1].equity == result.final_equity


# --- validation ---


def test_invalid_policy_output_type_is_rejected():
    candles = _sequential_candles(2)
    with pytest.raises(TypeError, match="PositionTarget"):
        run_backtest_replay(
            candles,
            as_of_time=_generous_as_of(candles),
            config=_config(),
            policy=_BadPolicy(),
            cost_model=ZeroCostModel(),
        )


# --- determinism ---


def test_deterministic_replay_repeated_run_equal():
    candles = _sequential_candles(4)

    def _target(context):
        length = len(context.candles)
        if length == 1:
            return PositionTarget.LONG
        if length == 3:
            return PositionTarget.SHORT
        return PositionTarget.LONG if length < 3 else PositionTarget.SHORT

    config = _config()
    as_of_time = _generous_as_of(candles)

    first = run_backtest_replay(
        candles,
        as_of_time=as_of_time,
        config=config,
        policy=_RecordingPolicy(_target),
        cost_model=ZeroCostModel(),
    )
    second = run_backtest_replay(
        candles,
        as_of_time=as_of_time,
        config=config,
        policy=_RecordingPolicy(_target),
        cost_model=ZeroCostModel(),
    )
    assert first == second
