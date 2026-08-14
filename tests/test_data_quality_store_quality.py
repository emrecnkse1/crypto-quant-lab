from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from crypto_quant_lab.data_quality.store_quality import build_data_quality_report_from_store
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.base import DataCorruptionError, HistoricalCandle, StorageError
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


class _RecordingStore:
    def __init__(self, rows=None, raise_error=None):
        self.rows = rows if rows is not None else []
        self.raise_error = raise_error
        self.query_calls = []
        self.write_batch_calls = []

    def query(self, exchange, market_type, symbol, timeframe, start_time, end_time):
        self.query_calls.append(
            {
                "exchange": exchange,
                "market_type": market_type,
                "symbol": symbol,
                "timeframe": timeframe,
                "start_time": start_time,
                "end_time": end_time,
            }
        )
        if self.raise_error is not None:
            raise self.raise_error
        return self.rows

    def write_batch(self, records):
        self.write_batch_calls.append(list(records))

    def close(self):
        pass


def test_perfect_1h_range_via_real_sqlite_passes(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [
            _make_record(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
            _make_record(datetime(2024, 1, 1, 11, 0, tzinfo=UTC)),
            _make_record(datetime(2024, 1, 1, 12, 0, tzinfo=UTC)),
        ]
    )

    report = build_data_quality_report_from_store(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
    )
    store.close()

    assert report.expected_count == 3
    assert report.actual_count == 3
    assert report.missing_count == 0
    assert report.unaligned_count == 0
    assert report.overall_status == "PASS"


def test_perfect_4h_range_via_real_sqlite_passes(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [
            _make_record(datetime(2024, 1, 1, 8, 0, tzinfo=UTC), timeframe="4h"),
            _make_record(datetime(2024, 1, 1, 12, 0, tzinfo=UTC), timeframe="4h"),
            _make_record(datetime(2024, 1, 1, 16, 0, tzinfo=UTC), timeframe="4h"),
        ]
    )

    report = build_data_quality_report_from_store(
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

    assert report.overall_status == "PASS"


def test_missing_row_via_real_sqlite_fails_and_no_synthetic_row_created(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [
            _make_record(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
            _make_record(datetime(2024, 1, 1, 12, 0, tzinfo=UTC)),
        ]
    )

    report = build_data_quality_report_from_store(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
    )

    rows_after = store.query(
        "binance",
        "spot",
        "BTCUSDT",
        "1h",
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
    )
    store.close()

    assert report.expected_count == 3
    assert report.actual_count == 2
    assert report.missing_count == 1
    assert report.missing_samples == (datetime(2024, 1, 1, 11, 0, tzinfo=UTC),)
    assert report.overall_status == "FAIL"
    assert len(rows_after) == 2  # no synthetic row was written


def test_unaligned_row_via_real_sqlite_fails(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [
            _make_record(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
            _make_record(datetime(2024, 1, 1, 11, 30, tzinfo=UTC)),
            _make_record(datetime(2024, 1, 1, 12, 0, tzinfo=UTC)),
        ]
    )

    report = build_data_quality_report_from_store(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
    )
    store.close()

    assert report.expected_count == 3
    assert report.actual_count == 3
    assert report.missing_count == 1  # 11:00
    assert report.unaligned_count == 1  # 11:30
    assert report.overall_status == "FAIL"


def test_empty_store_fails(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")

    report = build_data_quality_report_from_store(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
    )
    store.close()

    assert report.first_observed_open_time is None
    assert report.last_observed_open_time is None
    assert report.actual_count == 0
    assert report.missing_count == report.expected_count
    assert report.overall_status == "FAIL"


def test_incomplete_tail_row_in_store_is_excluded_from_evaluation(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [
            _make_record(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
            _make_record(datetime(2024, 1, 1, 11, 0, tzinfo=UTC)),
            _make_record(datetime(2024, 1, 1, 12, 0, tzinfo=UTC)),
            _make_record(datetime(2024, 1, 1, 13, 0, tzinfo=UTC)),
            _make_record(datetime(2024, 1, 1, 14, 0, tzinfo=UTC)),  # tail row, really in DB
        ]
    )

    report = build_data_quality_report_from_store(
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

    assert report.effective_end == datetime(2024, 1, 1, 14, 0, tzinfo=UTC)
    assert report.actual_count == 4
    assert report.missing_count == 0
    assert report.unaligned_count == 0
    assert report.incomplete_tail_excluded_count == 1
    assert report.overall_status == "PASS"


def test_row_exactly_at_effective_end_is_excluded(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [
            _make_record(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
            _make_record(datetime(2024, 1, 1, 11, 0, tzinfo=UTC)),  # == effective_end
        ]
    )

    report = build_data_quality_report_from_store(
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

    assert report.effective_end == datetime(2024, 1, 1, 11, 0, tzinfo=UTC)
    assert report.expected_count == 1
    assert report.actual_count == 1
    assert report.overall_status == "PASS"


def test_first_and_last_observed_from_real_store(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [
            _make_record(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
            _make_record(datetime(2024, 1, 1, 11, 0, tzinfo=UTC)),
            _make_record(datetime(2024, 1, 1, 12, 0, tzinfo=UTC)),
        ]
    )

    report = build_data_quality_report_from_store(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
    )
    store.close()

    assert report.first_observed_open_time == datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    assert report.last_observed_open_time == datetime(2024, 1, 1, 12, 0, tzinfo=UTC)


def test_metadata_is_not_hardcoded(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [
            _make_record(
                datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
                exchange="test_exchange",
                market_type="perpetual",
                symbol="ETHUSDT",
            ),
        ]
    )

    report = build_data_quality_report_from_store(
        store,
        exchange="test_exchange",
        market_type="perpetual",
        symbol="ETHUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
    )
    store.close()

    assert report.exchange == "test_exchange"
    assert report.market_type == "perpetual"
    assert report.symbol == "ETHUSDT"
    assert report.timeframe == "1h"
    assert report.requested_start == datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    assert report.requested_end == datetime(2024, 1, 1, 11, 0, tzinfo=UTC)
    assert report.as_of_time == datetime(2024, 1, 1, 11, 0, tzinfo=UTC)


def test_ohlcv_values_do_not_affect_quality_result(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [
            _make_record(
                datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
                open_=Decimal("12345.6789"),
                high=Decimal("99999.9999"),
                low=Decimal("0.0001"),
                close=Decimal(50000),
                volume=Decimal("999999.99999999"),
            ),
            _make_record(datetime(2024, 1, 1, 11, 0, tzinfo=UTC)),
        ]
    )

    report = build_data_quality_report_from_store(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
    )
    store.close()

    assert report.missing_count == 0
    assert report.unaligned_count == 0
    assert report.overall_status == "PASS"


def test_storage_error_from_query_propagates_unchanged():
    store = _RecordingStore(raise_error=StorageError("simulated storage failure"))

    with pytest.raises(StorageError, match="simulated storage failure"):
        build_data_quality_report_from_store(
            store,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        )


def test_data_corruption_error_from_query_propagates_unchanged():
    store = _RecordingStore(raise_error=DataCorruptionError("simulated corruption"))

    with pytest.raises(DataCorruptionError, match="simulated corruption"):
        build_data_quality_report_from_store(
            store,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        )


def test_write_batch_is_never_called():
    store = _RecordingStore(rows=[])

    build_data_quality_report_from_store(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
    )

    assert store.write_batch_calls == []


def test_non_utc_aware_equivalent_input_behaves_correctly(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [
            _make_record(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
            _make_record(datetime(2024, 1, 1, 11, 0, tzinfo=UTC)),
        ]
    )

    plus_five = timezone(timedelta(hours=5))
    report = build_data_quality_report_from_store(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 15, 0, tzinfo=plus_five),  # == 10:00 UTC
        requested_end=datetime(2024, 1, 1, 17, 0, tzinfo=plus_five),  # == 12:00 UTC
        as_of_time=datetime(2024, 1, 1, 17, 0, tzinfo=plus_five),
    )
    store.close()

    assert report.expected_count == 2
    assert report.actual_count == 2
    assert report.missing_count == 0
    assert report.overall_status == "PASS"


def test_repeated_calls_are_deterministic(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [
            _make_record(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
            _make_record(datetime(2024, 1, 1, 12, 0, tzinfo=UTC)),
        ]
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

    first = build_data_quality_report_from_store(store, **kwargs)
    second = build_data_quality_report_from_store(store, **kwargs)
    store.close()

    assert first == second


def test_db_unchanged_after_quality_evaluation(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    store.write_batch(
        [
            _make_record(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
            _make_record(datetime(2024, 1, 1, 12, 0, tzinfo=UTC)),
        ]
    )

    before = store.query(
        "binance",
        "spot",
        "BTCUSDT",
        "1h",
        datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
        datetime(2024, 1, 2, 0, 0, tzinfo=UTC),
    )

    build_data_quality_report_from_store(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
    )

    after = store.query(
        "binance",
        "spot",
        "BTCUSDT",
        "1h",
        datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
        datetime(2024, 1, 2, 0, 0, tzinfo=UTC),
    )
    store.close()

    assert before == after


def test_store_query_called_exactly_once_with_effective_end_not_requested_end():
    store = _RecordingStore(rows=[])

    build_data_quality_report_from_store(
        store,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 15, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 14, 37, tzinfo=UTC),
    )

    assert len(store.query_calls) == 1
    call = store.query_calls[0]
    assert call["exchange"] == "binance"
    assert call["market_type"] == "spot"
    assert call["symbol"] == "BTCUSDT"
    assert call["timeframe"] == "1h"
    assert call["start_time"] == datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    assert call["end_time"] == datetime(2024, 1, 1, 14, 0, tzinfo=UTC)  # effective_end
    assert call["end_time"] != datetime(2024, 1, 1, 15, 0, tzinfo=UTC)  # not requested_end
