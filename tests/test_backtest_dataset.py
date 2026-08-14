from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from crypto_quant_lab.backtest.dataset import prepare_backtest_dataset
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


def _scripted_store(*outcomes):
    outcomes_iter = iter(outcomes)
    query_calls = []
    write_batch_calls = []

    class _Store:
        def query(self, exchange, market_type, symbol, timeframe, start_time, end_time):
            query_calls.append(
                {
                    "exchange": exchange,
                    "market_type": market_type,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "start_time": start_time,
                    "end_time": end_time,
                }
            )
            outcome = next(outcomes_iter)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        def write_batch(self, records):
            write_batch_calls.append(list(records))

        def close(self):
            pass

    store = _Store()
    store.query_calls = query_calls
    store.write_batch_calls = write_batch_calls
    return store


# --- real SQLite: happy paths ---


def test_perfect_1h_dataset_returns_ordered_candles(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [_make_record(datetime(2024, 1, 1, hour, 0, tzinfo=UTC)) for hour in (10, 11, 12, 13)]
    )

    dataset = prepare_backtest_dataset(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
    )
    store.close()

    assert isinstance(dataset, tuple)
    assert [c.open_time for c in dataset] == [
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
    ]


def test_perfect_4h_dataset_returns_ordered_candles(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [
            _make_record(datetime(2024, 1, 1, hour, 0, tzinfo=UTC), timeframe="4h")
            for hour in (8, 12, 16)
        ]
    )

    dataset = prepare_backtest_dataset(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="4h",
        requested_start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 20, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 20, 0, tzinfo=UTC),
    )
    store.close()

    assert [c.open_time for c in dataset] == [
        datetime(2024, 1, 1, 8, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 16, 0, tzinfo=UTC),
    ]


def test_decimal_ohlcv_fidelity_preserved(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [
            _make_record(
                datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
                open_=Decimal("50000.12345678"),
                high=Decimal("50500.00000001"),
                low=Decimal("49500.00000001"),
                close=Decimal("50200.99999999"),
                volume=Decimal("12.50000000"),
            )
        ]
    )

    dataset = prepare_backtest_dataset(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
    )
    store.close()

    candle = dataset[0]
    assert str(candle.open) == "50000.12345678"
    assert str(candle.high) == "50500.00000001"
    assert str(candle.low) == "49500.00000001"
    assert str(candle.close) == "50200.99999999"
    assert str(candle.volume) == "12.50000000"


def test_incomplete_tail_is_excluded(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [_make_record(datetime(2024, 1, 1, hour, 0, tzinfo=UTC)) for hour in (10, 11, 12, 13)]
    )

    dataset = prepare_backtest_dataset(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 15, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 14, 37, tzinfo=UTC),
    )
    store.close()

    assert [c.open_time.hour for c in dataset] == [10, 11, 12, 13]


# --- real SQLite: quality gate rejection ---


def test_real_gap_causes_quality_rejection(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [_make_record(datetime(2024, 1, 1, hour, 0, tzinfo=UTC)) for hour in (10, 12, 13)]
    )

    with pytest.raises(ValueError):
        prepare_backtest_dataset(
            store,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        )
    store.close()


def test_real_unaligned_row_causes_quality_rejection(tmp_path):
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
        prepare_backtest_dataset(
            store,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        )
    store.close()


def test_empty_store_causes_quality_rejection(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")

    with pytest.raises(ValueError):
        prepare_backtest_dataset(
            store,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        )
    store.close()


# --- real SQLite: invalid range/time inputs ---


def test_unsupported_timeframe_is_rejected(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")

    with pytest.raises(ValueError):
        prepare_backtest_dataset(
            store,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="15m",
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        )
    store.close()


def test_unaligned_requested_start_is_rejected(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")

    with pytest.raises(ValueError):
        prepare_backtest_dataset(
            store,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=datetime(2024, 1, 1, 10, 30, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        )
    store.close()


def test_unaligned_requested_end_is_rejected(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")

    with pytest.raises(ValueError):
        prepare_backtest_dataset(
            store,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 10, 30, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        )
    store.close()


def test_naive_as_of_time_is_rejected(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")

    with pytest.raises(ValueError):
        prepare_backtest_dataset(
            store,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 11, 0),  # noqa: DTZ001
        )
    store.close()


def test_non_utc_aware_equivalent_as_of_time_succeeds(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch([_make_record(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))])

    plus_five = timezone(timedelta(hours=5))
    dataset = prepare_backtest_dataset(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 16, 0, tzinfo=plus_five),  # == 11:00 UTC
    )
    store.close()

    assert len(dataset) == 1


# --- scripted store: two-query boundary defenses ---
#
# First scripted outcome always satisfies the quality gate for
# requested=[10:00,13:00), as_of=11:37 -> effective_end=11:00, so the only
# expected slot is 10:00. The second outcome is independently scripted to
# simulate a store inconsistency between the two queries.

_REQUESTED_START = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
_REQUESTED_END = datetime(2024, 1, 1, 13, 0, tzinfo=UTC)
_AS_OF_TIME = datetime(2024, 1, 1, 11, 37, tzinfo=UTC)
_EFFECTIVE_END = datetime(2024, 1, 1, 11, 0, tzinfo=UTC)


def _call_prepare(store):
    return prepare_backtest_dataset(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=_REQUESTED_START,
        requested_end=_REQUESTED_END,
        as_of_time=_AS_OF_TIME,
    )


def test_second_query_uses_effective_end_not_requested_end():
    quality_rows = [_make_record(_REQUESTED_START)]
    dataset_rows = [_make_record(_REQUESTED_START)]
    store = _scripted_store(quality_rows, dataset_rows)

    _call_prepare(store)

    assert len(store.query_calls) == 2
    assert store.query_calls[1]["end_time"] == _EFFECTIVE_END
    assert store.query_calls[1]["end_time"] != _REQUESTED_END


def test_second_query_unsorted_rows_are_rejected():
    quality_rows = [_make_record(_REQUESTED_START)]
    unsorted_rows = [
        _make_record(datetime(2024, 1, 1, 10, 30, tzinfo=UTC)),
        _make_record(_REQUESTED_START),
    ]
    store = _scripted_store(quality_rows, unsorted_rows)

    with pytest.raises(ValueError):
        _call_prepare(store)


def test_second_query_duplicate_rows_are_rejected():
    quality_rows = [_make_record(_REQUESTED_START)]
    duplicate_rows = [_make_record(_REQUESTED_START), _make_record(_REQUESTED_START)]
    store = _scripted_store(quality_rows, duplicate_rows)

    with pytest.raises(ValueError):
        _call_prepare(store)


@pytest.mark.parametrize(
    "bad_record",
    [
        _make_record(_REQUESTED_START, exchange="wrong_exchange"),
        _make_record(_REQUESTED_START, symbol="ETHUSDT"),
        _make_record(_REQUESTED_START, timeframe="4h"),
    ],
    ids=["wrong_exchange", "wrong_symbol", "wrong_timeframe"],
)
def test_second_query_metadata_mismatch_is_rejected(bad_record):
    quality_rows = [_make_record(_REQUESTED_START)]
    store = _scripted_store(quality_rows, [bad_record])

    with pytest.raises(ValueError):
        _call_prepare(store)


def test_second_query_empty_result_is_rejected():
    quality_rows = [_make_record(_REQUESTED_START)]
    store = _scripted_store(quality_rows, [])

    with pytest.raises(ValueError):
        _call_prepare(store)


def test_storage_error_from_second_query_propagates_unchanged():
    quality_rows = [_make_record(_REQUESTED_START)]
    store = _scripted_store(quality_rows, StorageError("simulated storage failure"))

    with pytest.raises(StorageError, match="simulated storage failure"):
        _call_prepare(store)


def test_write_batch_is_never_called():
    quality_rows = [_make_record(_REQUESTED_START)]
    dataset_rows = [_make_record(_REQUESTED_START)]
    store = _scripted_store(quality_rows, dataset_rows)

    _call_prepare(store)

    assert store.write_batch_calls == []


# --- determinism ---


def test_repeated_calls_are_deterministic(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [_make_record(datetime(2024, 1, 1, hour, 0, tzinfo=UTC)) for hour in (10, 11, 12)]
    )

    kwargs = {
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "requested_start": datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        "requested_end": datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
        "as_of_time": datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
    }

    first = prepare_backtest_dataset(store, **kwargs)
    second = prepare_backtest_dataset(store, **kwargs)
    store.close()

    assert first == second
