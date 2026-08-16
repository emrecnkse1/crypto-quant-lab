from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_quant_lab.backtest.costs import ProportionalCommissionModel, ZeroCostModel
from crypto_quant_lab.backtest.models import BacktestConfig, PositionTarget
from crypto_quant_lab.backtest.replay import run_backtest_replay
from crypto_quant_lab.funding.calculator import LinearFundingModel
from crypto_quant_lab.funding.models import FundingEvent, HistoricalFundingEvent
from crypto_quant_lab.market_data.models import Candle

EXCHANGE = "binance"
MARKET_TYPE = "usdm_perp"
SYMBOL = "BTCUSDT"


class _RecordingPolicy:
    def __init__(self, target_fn):
        self._target_fn = target_fn
        self.contexts = []

    def target_position(self, context):
        self.contexts.append(context)
        return self._target_fn(context)


class _PoisonFundingModel:
    def calculate_funding_cost(self, **kwargs):
        raise AssertionError("funding_model must not be called when funding_events is empty")


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


def _sequential_candles(n, *, start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC), price=Decimal(100)):
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


def _long_policy():
    return _RecordingPolicy(lambda context: PositionTarget.LONG)


def _short_policy():
    return _RecordingPolicy(lambda context: PositionTarget.SHORT)


def _scripted_by_length(targets_by_length, default=PositionTarget.SHORT):
    return _RecordingPolicy(lambda context: targets_by_length.get(len(context.candles), default))


def _funding_event(
    event_time,
    rate_type="Regular",
    *,
    exchange=EXCHANGE,
    market_type=MARKET_TYPE,
    symbol=SYMBOL,
    funding_rate="0.001",
    reference_price="100",
):
    return HistoricalFundingEvent(
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        funding=FundingEvent(
            event_time=event_time,
            funding_rate=Decimal(funding_rate),
            reference_price=Decimal(reference_price),
            rate_type=rate_type,
        ),
    )


def _run(candles, *, policy, cost_model=None, funding_events=(), funding_model=None, config=None):
    return run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=config or _config(),
        policy=policy,
        cost_model=cost_model or ZeroCostModel(),
        funding_events=funding_events,
        funding_model=funding_model,
    )


# --- backward compatibility ---


def test_empty_funding_events_matches_legacy_call():
    candles = _sequential_candles(3)

    legacy = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=_long_policy(),
        cost_model=ZeroCostModel(),
    )
    explicit = run_backtest_replay(
        candles,
        as_of_time=_generous_as_of(candles),
        config=_config(),
        policy=_long_policy(),
        cost_model=ZeroCostModel(),
        funding_events=(),
        funding_model=None,
    )

    assert legacy == explicit


def test_poison_model_not_called_when_events_empty():
    candles = _sequential_candles(3)

    result = _run(candles, policy=_long_policy(), funding_model=_PoisonFundingModel())

    assert result.total_cost == Decimal(0)


def test_nonempty_events_without_model_raises():
    candles = _sequential_candles(2)
    event = _funding_event(candles[0].open_time)

    with pytest.raises(ValueError, match="funding_model"):
        _run(candles, policy=_flat_policy(), funding_events=(event,))


# --- sign matrix in replay ---


def test_long_positive_funding_between_marks():
    candles = _sequential_candles(4)
    event = _funding_event(candles[0].open_time + timedelta(hours=1, minutes=30))

    result = _run(
        candles, policy=_long_policy(), funding_events=(event,), funding_model=LinearFundingModel()
    )

    assert result.total_cost == Decimal("0.10")
    assert result.final_cash == Decimal("899.90")
    assert len(result.equity_curve) == len(candles)
    assert result.equity_curve[1].equity == Decimal("999.90")


def test_short_positive_funding_receives():
    candles = _sequential_candles(4)
    event = _funding_event(candles[0].open_time + timedelta(hours=1, minutes=30))

    result = _run(
        candles,
        policy=_short_policy(),
        funding_events=(event,),
        funding_model=LinearFundingModel(),
    )

    assert result.total_cost == Decimal("-0.10")
    assert result.final_cash == Decimal("1100.10")


def test_negative_rate_inverse_direction():
    candles = _sequential_candles(4)
    event = _funding_event(
        candles[0].open_time + timedelta(hours=1, minutes=30), funding_rate="-0.001"
    )

    result = _run(
        candles, policy=_long_policy(), funding_events=(event,), funding_model=LinearFundingModel()
    )

    assert result.total_cost == Decimal("-0.10")


def test_flat_funding_event_is_zero_cost():
    candles = _sequential_candles(3)
    event = _funding_event(candles[0].open_time + timedelta(minutes=30))

    result = _run(
        candles, policy=_flat_policy(), funding_events=(event,), funding_model=LinearFundingModel()
    )

    assert result.total_cost == Decimal(0)
    assert result.fill_count == 0
    assert result.trade_count == 0
    assert len(result.equity_curve) == len(candles)


# --- run boundary ---


def test_funding_at_run_start_is_legal_and_zero_when_flat():
    candles = _sequential_candles(2)
    event = _funding_event(candles[0].open_time)

    result = _run(
        candles, policy=_flat_policy(), funding_events=(event,), funding_model=LinearFundingModel()
    )

    assert result.total_cost == Decimal(0)


def test_funding_before_run_start_is_rejected():
    candles = _sequential_candles(2)
    event = _funding_event(candles[0].open_time - timedelta(milliseconds=1))

    with pytest.raises(ValueError, match="run_start"):
        _run(
            candles,
            policy=_flat_policy(),
            funding_events=(event,),
            funding_model=LinearFundingModel(),
        )


def test_funding_at_run_end_is_rejected():
    candles = _sequential_candles(2)
    run_end = candles[-1].open_time + timedelta(hours=1)  # feature_availability_time(last candle)
    event = _funding_event(run_end)

    with pytest.raises(ValueError, match="run_end"):
        _run(
            candles,
            policy=_flat_policy(),
            funding_events=(event,),
            funding_model=LinearFundingModel(),
        )


def test_funding_just_before_run_end_reflected_in_final_mark():
    candles = _sequential_candles(3)
    run_end = candles[-1].open_time + timedelta(hours=1)
    event = _funding_event(run_end - timedelta(milliseconds=1))

    result = _run(
        candles,
        policy=_scripted_by_length({1: PositionTarget.LONG}, default=PositionTarget.LONG),
        funding_events=(event,),
        funding_model=LinearFundingModel(),
    )

    assert result.total_cost == Decimal("0.10")
    assert result.equity_curve[-1].equity == Decimal("999.90")


# --- same-timestamp tie regressions ---


def test_same_time_open_from_flat_pays_no_funding():
    candles = _sequential_candles(2)
    tie_time = candles[0].open_time + timedelta(hours=1)  # == C1.open_time
    event = _funding_event(tie_time, funding_rate="0.001")

    result = _run(
        candles,
        policy=_scripted_by_length({1: PositionTarget.LONG}, default=PositionTarget.LONG),
        funding_events=(event,),
        funding_model=LinearFundingModel(),
    )

    assert result.total_cost == Decimal(0)
    assert result.final_cash == Decimal(900)


def test_same_time_close_to_flat_uses_held_position():
    candles = _sequential_candles(3)
    tie_time = candles[1].open_time + timedelta(hours=1)  # == C2.open_time
    event = _funding_event(tie_time, funding_rate="0.001")

    result = _run(
        candles,
        policy=_scripted_by_length(
            {1: PositionTarget.LONG, 2: PositionTarget.FLAT}, default=PositionTarget.FLAT
        ),
        funding_events=(event,),
        funding_model=LinearFundingModel(),
    )

    assert result.total_cost == Decimal("0.10")
    assert result.final_cash == Decimal("999.90")


def test_same_time_long_to_short_reversal_uses_pre_fill_position():
    candles = _sequential_candles(3)
    tie_time = candles[1].open_time + timedelta(hours=1)  # == C2.open_time
    event = _funding_event(tie_time, funding_rate="0.001")

    result = _run(
        candles,
        policy=_scripted_by_length(
            {1: PositionTarget.LONG, 2: PositionTarget.SHORT}, default=PositionTarget.SHORT
        ),
        funding_events=(event,),
        funding_model=LinearFundingModel(),
    )

    # Pre-fill LONG (+1) must pay: +0.10. Post-fill SHORT (-1) would wrongly
    # receive -0.10 — this is the critical tie-order regression.
    assert result.total_cost == Decimal("0.10")


def test_same_time_multiple_funding_events_applied_sequentially():
    candles = _sequential_candles(3)
    tie_time = candles[1].open_time + timedelta(hours=1)  # == C2.open_time == C1's availability
    regular = _funding_event(tie_time, "Regular", funding_rate="0.001")
    special = _funding_event(tie_time, "Special", funding_rate="-0.0005")

    result = _run(
        candles,
        policy=_scripted_by_length(
            {1: PositionTarget.LONG, 2: PositionTarget.LONG}, default=PositionTarget.LONG
        ),
        funding_events=(regular, special),
        funding_model=LinearFundingModel(),
    )

    assert result.total_cost == Decimal("0.05")
    assert result.final_cash == Decimal("899.95")


# --- transaction + funding integration ---


def test_transaction_and_funding_total_cost_sum():
    candles = _sequential_candles(3)
    event = _funding_event(
        candles[0].open_time + timedelta(hours=1, minutes=30), funding_rate="0.002"
    )

    result = _run(
        candles,
        policy=_long_policy(),
        cost_model=ProportionalCommissionModel(rate=Decimal("0.01")),
        funding_events=(event,),
        funding_model=LinearFundingModel(),
    )

    assert result.total_cost == Decimal("1.20")  # 1.00 transaction + 0.20 funding


def test_negative_total_cost_from_funding_receipt_is_legal():
    candles = _sequential_candles(4)
    event = _funding_event(candles[0].open_time + timedelta(hours=1, minutes=30))

    result = _run(
        candles,
        policy=_short_policy(),
        funding_events=(event,),
        funding_model=LinearFundingModel(),
    )

    assert result.total_cost < 0


def test_pnl_reconciliation_with_funding():
    candles = _sequential_candles(4)
    event = _funding_event(candles[0].open_time + timedelta(hours=1, minutes=30))

    result = _run(
        candles, policy=_long_policy(), funding_events=(event,), funding_model=LinearFundingModel()
    )

    assert (
        result.total_pnl
        == result.total_realized_pnl + result.total_unrealized_pnl - result.total_cost
    )
    assert result.total_pnl == result.final_equity - result.initial_cash


def test_deterministic_repeated_replay_with_funding():
    candles = _sequential_candles(4)
    event = _funding_event(candles[0].open_time + timedelta(hours=1, minutes=30))

    first = _run(
        candles, policy=_long_policy(), funding_events=(event,), funding_model=LinearFundingModel()
    )
    second = _run(
        candles, policy=_long_policy(), funding_events=(event,), funding_model=LinearFundingModel()
    )

    assert first == second


# --- structural input defense ---


def test_funding_event_wrong_type_raises():
    candles = _sequential_candles(2)

    with pytest.raises(TypeError, match="HistoricalFundingEvent"):
        _run(
            candles,
            policy=_flat_policy(),
            funding_events=("not-an-event",),
            funding_model=LinearFundingModel(),
        )


def test_funding_event_symbol_mismatch_raises():
    candles = _sequential_candles(2)
    event = _funding_event(candles[0].open_time, symbol="ETHUSDT")

    with pytest.raises(ValueError, match="symbol"):
        _run(
            candles,
            policy=_flat_policy(),
            funding_events=(event,),
            funding_model=LinearFundingModel(),
        )


def test_funding_event_exchange_inconsistency_raises():
    candles = _sequential_candles(2)
    e1 = _funding_event(candles[0].open_time)
    e2 = _funding_event(candles[0].open_time + timedelta(minutes=1), exchange="bybit")

    with pytest.raises(ValueError, match="partition"):
        _run(
            candles,
            policy=_flat_policy(),
            funding_events=(e1, e2),
            funding_model=LinearFundingModel(),
        )


def test_funding_event_market_type_inconsistency_raises():
    candles = _sequential_candles(2)
    e1 = _funding_event(candles[0].open_time)
    e2 = _funding_event(candles[0].open_time + timedelta(minutes=1), market_type="spot")

    with pytest.raises(ValueError, match="partition"):
        _run(
            candles,
            policy=_flat_policy(),
            funding_events=(e1, e2),
            funding_model=LinearFundingModel(),
        )


def test_funding_event_order_violation_raises():
    candles = _sequential_candles(2)
    later = _funding_event(candles[0].open_time + timedelta(minutes=30))
    earlier = _funding_event(candles[0].open_time + timedelta(minutes=10))

    with pytest.raises(ValueError, match="not ordered"):
        _run(
            candles,
            policy=_flat_policy(),
            funding_events=(later, earlier),
            funding_model=LinearFundingModel(),
        )


def test_funding_event_same_time_rate_type_descending_raises():
    candles = _sequential_candles(2)
    t = candles[0].open_time + timedelta(minutes=30)
    special = _funding_event(t, "Special")
    regular = _funding_event(t, "Regular")

    with pytest.raises(ValueError, match="not ordered"):
        _run(
            candles,
            policy=_flat_policy(),
            funding_events=(special, regular),
            funding_model=LinearFundingModel(),
        )


def test_duplicate_funding_event_key_raises():
    candles = _sequential_candles(2)
    t = candles[0].open_time + timedelta(minutes=30)
    e1 = _funding_event(t)
    e2 = _funding_event(t)

    with pytest.raises(ValueError, match="duplicate canonical funding event key"):
        _run(
            candles,
            policy=_flat_policy(),
            funding_events=(e1, e2),
            funding_model=LinearFundingModel(),
        )
