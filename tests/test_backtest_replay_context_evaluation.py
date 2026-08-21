from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal

import pytest

from crypto_quant_lab.backtest.costs import ZeroCostModel
from crypto_quant_lab.backtest.models import BacktestConfig, PositionTarget
from crypto_quant_lab.backtest.replay import run_backtest_replay
from crypto_quant_lab.funding.calculator import LinearFundingModel
from crypto_quant_lab.funding.models import FundingEvent, HistoricalFundingEvent
from crypto_quant_lab.market_data.models import Candle

EXCHANGE = "binance"
MARKET_TYPE = "usdm_perp"
SYMBOL = "BTCUSDT"


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


class _RecordingCostModel:
    def __init__(self, cost=Decimal(0)):
        self._cost = cost
        self.calls = []

    def calculate_cost(self, *, quantity, execution_price):
        self.calls.append((quantity, execution_price))
        return self._cost


def _candle(open_time, *, price=Decimal(100), symbol=SYMBOL, timeframe="1h"):
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal(1),
    )


def _sequential_candles(n, *, start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC), price=Decimal(100)):
    step = timedelta(hours=1)
    candles = []
    t = start
    for _ in range(n):
        candles.append(_candle(t, price=price))
        t = t + step
    return tuple(candles)


def _config(initial_cash=Decimal(1000), position_quantity=Decimal(1)):
    return BacktestConfig(initial_cash=initial_cash, position_quantity=position_quantity)


def _generous_as_of(candles):
    return candles[-1].open_time + timedelta(days=1)


def _flat_policy():
    return _RecordingPolicy(lambda context: PositionTarget.FLAT)


def _open_then_close_target(context):
    return PositionTarget.LONG if len(context.candles) == 1 else PositionTarget.FLAT


def _funding_event(
    event_time,
    *,
    rate_type="Regular",
    funding_rate="0.001",
    reference_price="100",
):
    return HistoricalFundingEvent(
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        funding=FundingEvent(
            event_time=event_time,
            funding_rate=Decimal(funding_rate),
            reference_price=Decimal(reference_price),
            rate_type=rate_type,
        ),
    )


# --- legacy / zero context ---


def test_open_then_close_matches_legacy_when_evaluation_start_omitted():
    candles = _sequential_candles(3, start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC))
    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=_RecordingPolicy(_open_then_close_target),
        cost_model=ZeroCostModel(),
    )
    assert result.fill_count == 2
    assert result.trade_count == 2


def test_explicit_evaluation_start_at_first_candle_equals_omitted_result():
    candles = _sequential_candles(3, start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC))
    funding_events = (_funding_event(datetime(2024, 1, 1, 11, 0, tzinfo=UTC)),)

    omitted = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=_RecordingPolicy(_open_then_close_target),
        cost_model=ZeroCostModel(),
        funding_events=funding_events,
        funding_model=LinearFundingModel(),
    )
    explicit = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=_RecordingPolicy(_open_then_close_target),
        cost_model=ZeroCostModel(),
        funding_events=funding_events,
        funding_model=LinearFundingModel(),
        evaluation_start=candles[0].open_time,
    )
    assert omitted == explicit


# --- context / policy ---


def test_context_candles_produce_zero_policy_calls():
    candles = _sequential_candles(5, start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC))
    policy = _flat_policy()
    run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=policy,
        cost_model=ZeroCostModel(),
        evaluation_start=candles[2].open_time,  # 10:00 -> 2 context + 3 evaluation candles
    )
    assert len(policy.contexts) == 3


def test_first_evaluation_policy_context_contains_all_context_candles():
    candles = _sequential_candles(5, start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC))
    policy = _flat_policy()
    run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=policy,
        cost_model=ZeroCostModel(),
        evaluation_start=candles[2].open_time,
    )
    assert policy.contexts[0].candles == candles[:3]


def test_second_evaluation_context_excludes_future_evaluation_candles():
    candles = _sequential_candles(5, start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC))
    policy = _flat_policy()
    run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=policy,
        cost_model=ZeroCostModel(),
        evaluation_start=candles[2].open_time,
    )
    assert policy.contexts[1].candles == candles[:4]


def test_type_h_policy_can_use_context_history_for_first_decision():
    candles = (
        _candle(datetime(2024, 1, 1, 8, 0, tzinfo=UTC), price=Decimal(200)),  # context
        _candle(datetime(2024, 1, 1, 9, 0, tzinfo=UTC), price=Decimal(100)),  # context
        _candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC), price=Decimal(100)),  # evaluation
        _candle(datetime(2024, 1, 1, 11, 0, tzinfo=UTC), price=Decimal(100)),  # evaluation
    )

    def _target(context):
        return (
            PositionTarget.LONG if context.candles[0].close > Decimal(150) else PositionTarget.FLAT
        )

    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=_RecordingPolicy(_target),
        cost_model=ZeroCostModel(),
        evaluation_start=candles[2].open_time,
    )
    assert (
        result.fill_count == 1
    )  # first evaluation decision (informed by context) opened a position


# --- execution / economics ---


def test_first_possible_fill_is_evaluation_start_plus_duration_not_earlier():
    candles = _sequential_candles(4, start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC))
    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(initial_cash=Decimal(1000), position_quantity=Decimal(1)),
        policy=_RecordingPolicy(lambda context: PositionTarget.LONG),
        cost_model=ZeroCostModel(),
        evaluation_start=candles[2].open_time,  # 10:00
    )
    # first scored EquityPoint (at 11:00 == evaluation_start + duration) is still flat:
    # E0's own fill (targeting E1's open) hasn't posted yet at E0's own mark.
    assert result.equity_curve[0].equity == Decimal(1000)
    # the fill lands at E1's open (11:00), reflected in cash by the run's end.
    assert result.final_cash == Decimal(1000) - Decimal(100)


def test_context_produces_zero_equity_points():
    candles = _sequential_candles(5, start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC))
    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=_flat_policy(),
        cost_model=ZeroCostModel(),
        evaluation_start=candles[2].open_time,
    )
    assert len(result.equity_curve) == 3
    assert result.equity_curve[0].time == datetime(2024, 1, 1, 11, 0, tzinfo=UTC)


def test_context_produces_zero_transaction_cost():
    candles = _sequential_candles(4, start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC))
    cost_model = _RecordingCostModel()
    run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=_RecordingPolicy(lambda context: PositionTarget.LONG),
        cost_model=cost_model,
        evaluation_start=candles[2].open_time,
    )
    assert (
        len(cost_model.calls) == 1
    )  # exactly one evaluation-triggered fill, no context executions


def test_context_produces_zero_pnl_despite_dramatic_price_swings():
    candles = (
        _candle(datetime(2024, 1, 1, 8, 0, tzinfo=UTC), price=Decimal(1)),  # context, extreme swing
        _candle(
            datetime(2024, 1, 1, 9, 0, tzinfo=UTC), price=Decimal(100000)
        ),  # context, extreme swing
        _candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC), price=Decimal(100)),  # evaluation
        _candle(datetime(2024, 1, 1, 11, 0, tzinfo=UTC), price=Decimal(100)),  # evaluation
    )
    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(initial_cash=Decimal(1000)),
        policy=_flat_policy(),
        cost_model=ZeroCostModel(),
        evaluation_start=candles[2].open_time,
    )
    assert result.final_equity == Decimal(1000)
    assert result.total_pnl == Decimal(0)


# --- funding ---


def test_funding_event_at_evaluation_start_settles_against_flat():
    candles = _sequential_candles(5, start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC))
    evaluation_start = candles[2].open_time  # 10:00
    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=_flat_policy(),
        cost_model=ZeroCostModel(),
        funding_events=(_funding_event(evaluation_start),),
        funding_model=LinearFundingModel(),
        evaluation_start=evaluation_start,
    )
    assert result.total_cost == Decimal(0)


def test_funding_at_first_fill_timestamp_settles_before_fill():
    candles = _sequential_candles(5, start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC))
    evaluation_start = candles[2].open_time  # 10:00
    first_fill_time = datetime(2024, 1, 1, 11, 0, tzinfo=UTC)  # evaluation_start + duration
    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=_RecordingPolicy(lambda context: PositionTarget.LONG),
        cost_model=ZeroCostModel(),
        funding_events=(_funding_event(first_fill_time),),
        funding_model=LinearFundingModel(),
        evaluation_start=evaluation_start,
    )
    assert result.total_cost == Decimal(
        0
    )  # settled against entering flat position, before the fill
    assert result.fill_count == 1  # the fill itself still happened


def test_later_funding_after_position_open_has_nonzero_effect():
    candles = _sequential_candles(5, start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC))
    evaluation_start = candles[2].open_time  # 10:00
    later_funding_time = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)  # after the first fill posted
    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=_RecordingPolicy(lambda context: PositionTarget.LONG),
        cost_model=ZeroCostModel(),
        funding_events=(_funding_event(later_funding_time),),
        funding_model=LinearFundingModel(),
        evaluation_start=evaluation_start,
    )
    assert result.total_cost == Decimal("0.1")  # 1 * 100 * 0.001, position is LONG by then


def test_funding_event_before_evaluation_start_is_rejected():
    candles = _sequential_candles(5, start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC))
    with pytest.raises(ValueError, match="run_start"):
        run_backtest_replay(
            candles,
            as_of_time=_generous_as_of(candles),
            config=_config(),
            policy=_flat_policy(),
            cost_model=ZeroCostModel(),
            funding_events=(_funding_event(datetime(2024, 1, 1, 9, 0, tzinfo=UTC)),),
            funding_model=LinearFundingModel(),
            evaluation_start=candles[2].open_time,  # 10:00
        )


# --- boundaries ---


def test_evaluation_start_at_last_loaded_candle_is_legal_no_trade():
    candles = _sequential_candles(3, start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC))
    policy = _RecordingPolicy(lambda context: PositionTarget.LONG)
    result = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=policy,
        cost_model=ZeroCostModel(),
        evaluation_start=candles[-1].open_time,
    )
    assert len(policy.contexts) == 1
    assert len(result.equity_curve) == 1
    assert result.fill_count == 0


def test_evaluation_start_at_loaded_range_end_is_rejected():
    candles = _sequential_candles(3, start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC))
    with pytest.raises(ValueError, match="evaluation_start must satisfy"):
        run_backtest_replay(
            candles,
            as_of_time=_generous_as_of(candles),
            config=_config(),
            policy=_flat_policy(),
            cost_model=ZeroCostModel(),
            evaluation_start=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),  # == run end
        )


def test_evaluation_start_before_loaded_start_is_rejected():
    candles = _sequential_candles(3, start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC))
    with pytest.raises(ValueError, match="evaluation_start must satisfy"):
        run_backtest_replay(
            candles,
            as_of_time=_generous_as_of(candles),
            config=_config(),
            policy=_flat_policy(),
            cost_model=ZeroCostModel(),
            evaluation_start=datetime(2024, 1, 1, 9, 0, tzinfo=UTC),
        )


def test_misaligned_evaluation_start_is_rejected():
    candles = _sequential_candles(3, start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC))
    with pytest.raises(ValueError, match="grid"):
        run_backtest_replay(
            candles,
            as_of_time=_generous_as_of(candles),
            config=_config(),
            policy=_flat_policy(),
            cost_model=ZeroCostModel(),
            evaluation_start=datetime(2024, 1, 1, 10, 30, tzinfo=UTC),
        )


def test_naive_evaluation_start_is_rejected():
    candles = _sequential_candles(3, start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC))
    with pytest.raises(ValueError):
        run_backtest_replay(
            candles,
            as_of_time=_generous_as_of(candles),
            config=_config(),
            policy=_flat_policy(),
            cost_model=ZeroCostModel(),
            evaluation_start=datetime(2024, 1, 1, 10, 0),  # noqa: DTZ001
        )


def test_pseudo_naive_evaluation_start_is_rejected():
    candles = _sequential_candles(3, start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC))
    with pytest.raises(ValueError):
        run_backtest_replay(
            candles,
            as_of_time=_generous_as_of(candles),
            config=_config(),
            policy=_flat_policy(),
            cost_model=ZeroCostModel(),
            evaluation_start=datetime(2024, 1, 1, 10, 0, tzinfo=_BrokenTzInfo()),
        )


def test_non_datetime_evaluation_start_is_rejected():
    candles = _sequential_candles(3, start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC))
    with pytest.raises(TypeError):
        run_backtest_replay(
            candles,
            as_of_time=_generous_as_of(candles),
            config=_config(),
            policy=_flat_policy(),
            cost_model=ZeroCostModel(),
            evaluation_start="2024-01-01T10:00:00Z",
        )


def test_non_utc_aware_equivalent_evaluation_start_is_accepted():
    candles = _sequential_candles(5, start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC))
    plus_five = timezone(timedelta(hours=5))
    policy = _flat_policy()
    run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=policy,
        cost_model=ZeroCostModel(),
        evaluation_start=datetime(2024, 1, 1, 15, 0, tzinfo=plus_five),  # == 10:00 UTC
    )
    assert len(policy.contexts) == 3  # 2 context candles (08:00, 09:00), 3 evaluation candles
