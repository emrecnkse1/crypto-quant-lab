from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_quant_lab.data_quality.ingestion import ingest_binance_historical_range
from crypto_quant_lab.market_data.binance_historical import BinanceHistoricalKline
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.market_data.timeframes import candle_duration
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore


def _make_candle(open_time: datetime, timeframe: str = "1h") -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=timeframe,
        open_time=open_time,
        open=Decimal("50000.12345678"),
        high=Decimal("50500.00000000"),
        low=Decimal("49500.00000000"),
        close=Decimal("50200.00000000"),
        volume=Decimal("12.50000000"),
    )


def _make_kline(open_time: datetime, timeframe: str = "1h") -> BinanceHistoricalKline:
    close_time = open_time + candle_duration(timeframe) - timedelta(milliseconds=1)
    return BinanceHistoricalKline(candle=_make_candle(open_time, timeframe), close_time=close_time)


def _sequential_fetch_page(*outcomes):
    call_args = []
    outcomes_iter = iter(outcomes)

    def _fetch(*, start_time_ms: int, end_time_ms: int):
        call_args.append({"start_time_ms": start_time_ms, "end_time_ms": end_time_ms})
        outcome = next(outcomes_iter, [])
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    _fetch.call_args = call_args
    return _fetch


class _RecordingStore:
    def __init__(self):
        self.write_batch_calls = []

    def write_batch(self, records):
        self.write_batch_calls.append(list(records))

    def query(self, exchange, market_type, symbol, timeframe, start_time, end_time):
        raise NotImplementedError

    def close(self):
        pass


def test_valid_finalized_candles_are_written_to_store(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    page = [
        _make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
        _make_kline(datetime(2024, 1, 1, 11, 0, tzinfo=UTC)),
    ]
    fetch = _sequential_fetch_page(page)

    ingest_binance_historical_range(
        store,
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        fetch_page=fetch,
    )

    result = store.query(
        "binance",
        "spot",
        "BTCUSDT",
        "1h",
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
    )
    store.close()

    assert len(result) == 2
    assert [r.candle.open_time for r in result] == [
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
    ]


def test_queried_candles_match_ingested_values_exactly(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    open_time = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    page = [_make_kline(open_time)]
    fetch = _sequential_fetch_page(page)

    ingest_binance_historical_range(
        store,
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=open_time,
        requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        fetch_page=fetch,
    )

    result = store.query(
        "binance",
        "spot",
        "BTCUSDT",
        "1h",
        open_time,
        datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
    )
    store.close()

    assert len(result) == 1
    candle = result[0].candle
    assert candle.open_time == open_time
    assert candle.open_time.tzinfo is UTC
    assert str(candle.open) == "50000.12345678"
    assert str(candle.high) == "50500.00000000"
    assert str(candle.low) == "49500.00000000"
    assert str(candle.close) == "50200.00000000"
    assert str(candle.volume) == "12.50000000"


def test_written_records_are_binance_spot(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    open_time = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    fetch = _sequential_fetch_page([_make_kline(open_time)])

    ingest_binance_historical_range(
        store,
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=open_time,
        requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        fetch_page=fetch,
    )

    result = store.query(
        "binance",
        "spot",
        "BTCUSDT",
        "1h",
        open_time,
        datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
    )
    store.close()

    assert result[0].exchange == "binance"
    assert result[0].market_type == "spot"


def test_same_range_ingested_twice_is_idempotent(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    open_time = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    requested_end = datetime(2024, 1, 1, 11, 0, tzinfo=UTC)

    for _ in range(2):
        fetch = _sequential_fetch_page([_make_kline(open_time)])
        ingest_binance_historical_range(
            store,
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=open_time,
            requested_end=requested_end,
            as_of_time=requested_end,
            fetch_page=fetch,
        )

    result = store.query("binance", "spot", "BTCUSDT", "1h", open_time, requested_end)
    store.close()

    assert len(result) == 1


def test_close_time_inconsistency_raises_and_leaves_store_unchanged(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    open_time = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    inconsistent_close_time = open_time + candle_duration("1h") - timedelta(milliseconds=2)
    bad_kline = BinanceHistoricalKline(
        candle=_make_candle(open_time), close_time=inconsistent_close_time
    )
    fetch = _sequential_fetch_page([bad_kline])

    with pytest.raises(ValueError, match="close_time"):
        ingest_binance_historical_range(
            store,
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=open_time,
            requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            fetch_page=fetch,
        )

    result = store.query(
        "binance",
        "spot",
        "BTCUSDT",
        "1h",
        open_time,
        datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
    )
    store.close()

    assert result == []


def test_non_finalized_candle_in_canonical_range_raises_not_silent_skip(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")

    # Unaligned open_time inside [requested_start, effective_end) whose close
    # boundary (open_time + duration) lands after as_of_time: consistent
    # close_time (no ValueError from validated_close_boundary), but genuinely
    # not finalized as of as_of_time.
    open_time = datetime(2024, 1, 1, 10, 59, 59, 999999, tzinfo=UTC)
    not_yet_finalized_kline = _make_kline(open_time)
    fetch = _sequential_fetch_page([not_yet_finalized_kline])

    with pytest.raises(ValueError, match="non-finalized"):
        ingest_binance_historical_range(
            store,
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            fetch_page=fetch,
        )

    result = store.query(
        "binance",
        "spot",
        "BTCUSDT",
        "1h",
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
    )
    store.close()

    assert result == []


def test_late_pagination_failure_leaves_store_unchanged(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    page1 = [_make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))]
    # page2's row is behind the cursor (11:00) established after page1.
    page2 = [_make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))]
    fetch = _sequential_fetch_page(page1, page2)

    with pytest.raises(ValueError):
        ingest_binance_historical_range(
            store,
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
            fetch_page=fetch,
        )

    result = store.query(
        "binance",
        "spot",
        "BTCUSDT",
        "1h",
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
    )
    store.close()

    assert result == []


def test_connection_error_then_success_retries_and_writes(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    open_time = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    fetch = _sequential_fetch_page(ConnectionError("transient"), [_make_kline(open_time)])

    ingest_binance_historical_range(
        store,
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=open_time,
        requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        fetch_page=fetch,
        max_attempts=2,
    )

    result = store.query(
        "binance",
        "spot",
        "BTCUSDT",
        "1h",
        open_time,
        datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
    )
    store.close()

    assert len(result) == 1
    assert len(fetch.call_args) == 2


def test_connection_error_exhausted_leaves_store_unchanged(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    fetch = _sequential_fetch_page(
        ConnectionError("e1"), ConnectionError("e2"), ConnectionError("e3")
    )

    with pytest.raises(ConnectionError):
        ingest_binance_historical_range(
            store,
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            fetch_page=fetch,
            max_attempts=3,
        )

    result = store.query(
        "binance",
        "spot",
        "BTCUSDT",
        "1h",
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
    )
    store.close()

    assert result == []


def test_empty_pagination_result_is_safe_no_op(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    fetch = _sequential_fetch_page([])

    ingest_binance_historical_range(
        store,
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        fetch_page=fetch,
    )

    result = store.query(
        "binance",
        "spot",
        "BTCUSDT",
        "1h",
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
    )
    store.close()

    assert result == []


def test_caller_as_of_time_is_used_not_hidden_wall_clock(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    fetch = _sequential_fetch_page([_make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))])

    # as_of_time is mid-slot (not a grid boundary past requested_start), so
    # nothing in the requested range can be finalized yet as of the given
    # as_of_time. If the real wall clock (today, 2026) were used instead,
    # this would not raise. fetch_page must never even be called.
    with pytest.raises(ValueError):
        ingest_binance_historical_range(
            store,
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 10, 30, tzinfo=UTC),
            fetch_page=fetch,
        )

    store.close()
    assert fetch.call_args == []


def test_write_batch_called_exactly_once_on_success():
    store = _RecordingStore()
    page = [
        _make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
        _make_kline(datetime(2024, 1, 1, 11, 0, tzinfo=UTC)),
    ]
    fetch = _sequential_fetch_page(page)

    ingest_binance_historical_range(
        store,
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        fetch_page=fetch,
    )

    assert len(store.write_batch_calls) == 1
    assert len(store.write_batch_calls[0]) == 2


def test_write_batch_never_called_for_empty_result():
    store = _RecordingStore()
    fetch = _sequential_fetch_page([])

    ingest_binance_historical_range(
        store,
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        fetch_page=fetch,
    )

    assert store.write_batch_calls == []


def test_atomicity_no_partial_write_when_second_page_is_invalid(tmp_path):
    """Strong regression: a valid first page must not be persisted if a later page fails."""
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    page1 = [_make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))]
    # page2 is backward relative to page1 -> pagination ordering/cursor violation.
    page2 = [_make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))]
    fetch = _sequential_fetch_page(page1, page2)

    with pytest.raises(ValueError):
        ingest_binance_historical_range(
            store,
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
            fetch_page=fetch,
        )

    result = store.query(
        "binance",
        "spot",
        "BTCUSDT",
        "1h",
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
    )
    store.close()

    assert result == []
