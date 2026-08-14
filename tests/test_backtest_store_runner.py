from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_quant_lab.backtest.costs import ZeroCostModel
from crypto_quant_lab.backtest.models import BacktestConfig, BacktestResult, PositionTarget
from crypto_quant_lab.backtest.store_runner import run_backtest_from_store
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.base import HistoricalCandle, StorageError
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore


def _make_record(
    open_time: datetime,
    timeframe: str = "1h",
    exchange: str = "binance",
    market_type: str = "spot",
    symbol: str = "BTCUSDT",
    open_: Decimal = Decimal(100),
    high: Decimal = Decimal(100),
    low: Decimal = Decimal(100),
    close: Decimal = Decimal(100),
    volume: Decimal = Decimal(1),
) -> HistoricalCandle:
    candle = Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )
    return HistoricalCandle(exchange=exchange, market_type=market_type, candle=candle)


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


class _RaisingStore:
    def query(self, exchange, market_type, symbol, timeframe, start_time, end_time):
        raise StorageError("sentinel")

    def write_batch(self, records):
        raise AssertionError("write_batch must never be called")

    def close(self):
        pass


def _config(initial_cash=Decimal(1000), position_quantity=Decimal(1)):
    return BacktestConfig(initial_cash=initial_cash, position_quantity=position_quantity)


def _always_flat_policy():
    return _RecordingPolicy(lambda context: PositionTarget.FLAT)


def _open_then_close_target(context):
    return PositionTarget.LONG if len(context.candles) == 1 else PositionTarget.FLAT


# --- real SQLite: happy paths ---


def test_perfect_1h_no_trade(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [_make_record(datetime(2024, 1, 1, hour, 0, tzinfo=UTC)) for hour in (10, 11, 12, 13)]
    )
    query_args = (
        "binance",
        "spot",
        "BTCUSDT",
        "1h",
        datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
        datetime(2024, 1, 2, 0, 0, tzinfo=UTC),
    )
    before_rows = store.query(*query_args)

    result = run_backtest_from_store(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        config=_config(),
        policy=_always_flat_policy(),
        cost_model=ZeroCostModel(),
    )

    after_rows = store.query(*query_args)
    store.close()

    assert isinstance(result, BacktestResult)
    assert result.fill_count == 0
    assert result.trade_count == 0
    assert result.total_cost == Decimal(0)
    assert result.total_pnl == Decimal(0)
    assert result.final_equity == Decimal(1000)
    assert len(result.equity_curve) == 4
    assert after_rows == before_rows  # read-only cheap check


def test_perfect_4h_succeeds(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [
            _make_record(datetime(2024, 1, 1, hour, 0, tzinfo=UTC), timeframe="4h")
            for hour in (8, 12, 16)
        ]
    )

    result = run_backtest_from_store(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="4h",
        requested_start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 20, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 20, 0, tzinfo=UTC),
        config=_config(),
        policy=_always_flat_policy(),
        cost_model=ZeroCostModel(),
    )
    store.close()

    assert isinstance(result, BacktestResult)
    assert len(result.equity_curve) == 3


def test_real_next_open_execution_uses_next_candle_open(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [
            _make_record(
                datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
                open_=Decimal(90),
                high=Decimal(200),
                low=Decimal(80),
                close=Decimal(123),
            ),
            _make_record(
                datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
                open_=Decimal(500),
                high=Decimal(510),
                low=Decimal(490),
                close=Decimal(505),
            ),
        ]
    )

    result = run_backtest_from_store(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        config=_config(initial_cash=Decimal(1000), position_quantity=Decimal(1)),
        policy=_RecordingPolicy(lambda context: PositionTarget.LONG),
        cost_model=ZeroCostModel(),
    )
    store.close()

    assert result.fill_count == 1
    assert result.final_cash == Decimal(500)  # 1000 - 500 (next-open), not 1000 - 123
    assert result.total_unrealized_pnl == Decimal(5)  # (505 - 500) * 1


def test_real_open_then_close_counts(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [_make_record(datetime(2024, 1, 1, hour, 0, tzinfo=UTC)) for hour in (10, 11, 12, 13)]
    )

    result = run_backtest_from_store(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        config=_config(),
        policy=_RecordingPolicy(_open_then_close_target),
        cost_model=ZeroCostModel(),
    )
    store.close()

    assert result.fill_count == 2
    assert result.trade_count == 2


def test_incomplete_requested_tail_excluded_from_replay(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [_make_record(datetime(2024, 1, 1, hour, 0, tzinfo=UTC)) for hour in (10, 11, 12, 13)]
    )

    policy = _always_flat_policy()
    result = run_backtest_from_store(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 15, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 14, 37, tzinfo=UTC),
        config=_config(),
        policy=policy,
        cost_model=ZeroCostModel(),
    )
    store.close()

    assert len(result.equity_curve) == 4
    assert policy.contexts[-1].candles[-1].open_time.hour == 13


def test_one_candle_effective_dataset_succeeds(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch([_make_record(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))])

    policy = _RecordingPolicy(lambda context: PositionTarget.LONG)
    cost_model = _RecordingCostModel()
    result = run_backtest_from_store(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        config=_config(),
        policy=policy,
        cost_model=cost_model,
    )
    store.close()

    assert len(result.equity_curve) == 1
    assert len(policy.contexts) == 1
    assert result.fill_count == 0
    assert result.trade_count == 0
    assert cost_model.calls == []


# --- real SQLite: quality gate rejection + atomicity ---


def test_real_gap_causes_rejection_with_zero_collaborator_calls(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [_make_record(datetime(2024, 1, 1, hour, 0, tzinfo=UTC)) for hour in (10, 12, 13)]
    )

    policy = _always_flat_policy()
    cost_model = _RecordingCostModel()
    with pytest.raises(ValueError):
        run_backtest_from_store(
            store,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
            config=_config(),
            policy=policy,
            cost_model=cost_model,
        )
    store.close()

    assert policy.contexts == []
    assert cost_model.calls == []


def test_real_unaligned_row_causes_rejection(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [
            _make_record(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
            _make_record(datetime(2024, 1, 1, 11, 30, tzinfo=UTC)),
            _make_record(datetime(2024, 1, 1, 12, 0, tzinfo=UTC)),
            _make_record(datetime(2024, 1, 1, 13, 0, tzinfo=UTC)),
        ]
    )

    with pytest.raises(ValueError):
        run_backtest_from_store(
            store,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
            config=_config(),
            policy=_always_flat_policy(),
            cost_model=ZeroCostModel(),
        )
    store.close()


def test_empty_store_causes_rejection_with_zero_policy_calls(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")

    policy = _always_flat_policy()
    with pytest.raises(ValueError):
        run_backtest_from_store(
            store,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            config=_config(),
            policy=policy,
            cost_model=ZeroCostModel(),
        )
    store.close()

    assert policy.contexts == []


def test_storage_error_propagates_unchanged():
    with pytest.raises(StorageError, match="sentinel"):
        run_backtest_from_store(
            _RaisingStore(),
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            config=_config(),
            policy=_always_flat_policy(),
            cost_model=ZeroCostModel(),
        )


def test_naive_as_of_time_propagates_as_value_error(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch([_make_record(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))])

    with pytest.raises(ValueError):
        run_backtest_from_store(
            store,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 11, 0),  # noqa: DTZ001
            config=_config(),
            policy=_always_flat_policy(),
            cost_model=ZeroCostModel(),
        )
    store.close()


def test_unsupported_timeframe_propagates(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")

    with pytest.raises(ValueError):
        run_backtest_from_store(
            store,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="15m",
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            config=_config(),
            policy=_always_flat_policy(),
            cost_model=ZeroCostModel(),
        )
    store.close()


# --- real SQLite: cost integration ---


def test_zero_cost_model_store_backed_total_cost_zero(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [_make_record(datetime(2024, 1, 1, hour, 0, tzinfo=UTC)) for hour in (10, 11, 12, 13)]
    )

    result = run_backtest_from_store(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        config=_config(),
        policy=_RecordingPolicy(_open_then_close_target),
        cost_model=ZeroCostModel(),
    )
    store.close()

    assert result.total_cost == Decimal(0)


def test_fixed_cost_model_store_backed_cumulative_total(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [_make_record(datetime(2024, 1, 1, hour, 0, tzinfo=UTC)) for hour in (10, 11, 12, 13)]
    )

    cost_model = _RecordingCostModel(cost=Decimal(2))
    result = run_backtest_from_store(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        config=_config(),
        policy=_RecordingPolicy(_open_then_close_target),
        cost_model=cost_model,
    )
    store.close()

    assert result.fill_count == 2
    assert result.total_cost == Decimal(4)


# --- real SQLite: determinism ---


def test_real_store_determinism_repeated_run_equal(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [_make_record(datetime(2024, 1, 1, hour, 0, tzinfo=UTC)) for hour in (10, 11, 12, 13)]
    )

    kwargs = {
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "requested_start": datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        "requested_end": datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        "as_of_time": datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        "config": _config(),
    }

    first = run_backtest_from_store(
        store,
        policy=_RecordingPolicy(_open_then_close_target),
        cost_model=ZeroCostModel(),
        **kwargs,
    )
    second = run_backtest_from_store(
        store,
        policy=_RecordingPolicy(_open_then_close_target),
        cost_model=ZeroCostModel(),
        **kwargs,
    )
    store.close()

    assert first == second
