from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_quant_lab.data_quality.ingestion import ingest_binance_historical_range
from crypto_quant_lab.data_quality.store_quality import build_data_quality_report_from_store
from crypto_quant_lab.market_data.binance_historical import BinanceHistoricalKline
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.market_data.timeframes import candle_duration
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore


def _make_candle(open_time: datetime, timeframe: str = "1h", symbol: str = "BTCUSDT") -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        open=Decimal("50000.12345678"),
        high=Decimal("50500.00000000"),
        low=Decimal("49500.00000000"),
        close=Decimal("50200.00000000"),
        volume=Decimal("12.50000000"),
    )


def _make_kline(
    open_time: datetime, *, symbol: str = "BTCUSDT", timeframe: str = "1h"
) -> BinanceHistoricalKline:
    close_time = open_time + candle_duration(timeframe) - timedelta(milliseconds=1)
    return BinanceHistoricalKline(
        candle=_make_candle(open_time, timeframe, symbol), close_time=close_time
    )


def _sequential_fetch_page(*pages):
    call_args = []
    pages_iter = iter(pages)

    def _fetch(*, start_time_ms: int, end_time_ms: int):
        call_args.append({"start_time_ms": start_time_ms, "end_time_ms": end_time_ms})
        return next(pages_iter, [])

    _fetch.call_args = call_args
    return _fetch


def test_multi_page_ingestion_to_real_store_to_quality_passes(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    page1 = [
        _make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
        _make_kline(datetime(2024, 1, 1, 11, 0, tzinfo=UTC)),
    ]
    page2 = [
        _make_kline(datetime(2024, 1, 1, 12, 0, tzinfo=UTC)),
        _make_kline(datetime(2024, 1, 1, 13, 0, tzinfo=UTC)),
    ]
    fetch = _sequential_fetch_page(page1, page2)

    ingest_binance_historical_range(
        store,
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        fetch_page=fetch,
    )

    rows = store.query(
        "binance",
        "spot",
        "BTCUSDT",
        "1h",
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
    )
    assert len(rows) == 4
    assert [r.candle.open_time for r in rows] == [
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
    ]

    report = build_data_quality_report_from_store(
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

    assert report.exchange == "binance"
    assert report.market_type == "spot"
    assert report.symbol == "BTCUSDT"
    assert report.timeframe == "1h"
    assert report.requested_start == datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    assert report.requested_end == datetime(2024, 1, 1, 14, 0, tzinfo=UTC)
    assert report.effective_end == datetime(2024, 1, 1, 14, 0, tzinfo=UTC)
    assert report.expected_count == 4
    assert report.actual_count == 4
    assert report.missing_count == 0
    assert report.unaligned_count == 0
    assert report.incomplete_tail_excluded_count == 0
    assert report.overall_status == "PASS"


def test_gap_survives_ingestion_without_repair_and_quality_reports_missing(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    page = [
        _make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
        _make_kline(datetime(2024, 1, 1, 12, 0, tzinfo=UTC)),
        _make_kline(datetime(2024, 1, 1, 13, 0, tzinfo=UTC)),
    ]
    fetch = _sequential_fetch_page(page)

    ingest_binance_historical_range(
        store,
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        fetch_page=fetch,
    )

    rows = store.query(
        "binance",
        "spot",
        "BTCUSDT",
        "1h",
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
    )
    open_times = [r.candle.open_time for r in rows]
    assert len(rows) == 3
    assert open_times == [
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
    ]
    assert datetime(2024, 1, 1, 11, 0, tzinfo=UTC) not in open_times

    report = build_data_quality_report_from_store(
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

    assert report.expected_count == 4
    assert report.actual_count == 3
    assert report.missing_count == 1
    assert report.missing_samples == (datetime(2024, 1, 1, 11, 0, tzinfo=UTC),)
    assert report.unaligned_count == 0
    assert report.overall_status == "FAIL"


def test_unaligned_candle_reaches_store_and_quality_reports_missing_and_unaligned(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    page = [
        _make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
        _make_kline(datetime(2024, 1, 1, 11, 30, tzinfo=UTC)),
        _make_kline(datetime(2024, 1, 1, 12, 0, tzinfo=UTC)),
        _make_kline(datetime(2024, 1, 1, 13, 0, tzinfo=UTC)),
    ]
    fetch = _sequential_fetch_page(page)

    ingest_binance_historical_range(
        store,
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        fetch_page=fetch,
    )

    rows = store.query(
        "binance",
        "spot",
        "BTCUSDT",
        "1h",
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
    )
    open_times = [r.candle.open_time for r in rows]
    assert len(rows) == 4
    assert datetime(2024, 1, 1, 11, 0, tzinfo=UTC) not in open_times
    assert datetime(2024, 1, 1, 11, 30, tzinfo=UTC) in open_times

    report = build_data_quality_report_from_store(
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

    assert report.actual_count == 4
    assert report.missing_count == 1
    assert report.missing_samples == (datetime(2024, 1, 1, 11, 0, tzinfo=UTC),)
    assert report.unaligned_count == 1
    assert report.unaligned_samples == (datetime(2024, 1, 1, 11, 30, tzinfo=UTC),)
    assert report.overall_status == "FAIL"


def test_incomplete_tail_is_excluded_end_to_end_and_does_not_fail_quality(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "test.db")
    page = [
        _make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
        _make_kline(datetime(2024, 1, 1, 11, 0, tzinfo=UTC)),
        _make_kline(datetime(2024, 1, 1, 12, 0, tzinfo=UTC)),
        _make_kline(datetime(2024, 1, 1, 13, 0, tzinfo=UTC)),
    ]
    fetch = _sequential_fetch_page(page)

    ingest_binance_historical_range(
        store,
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 15, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 14, 37, tzinfo=UTC),
        fetch_page=fetch,
    )

    rows = store.query(
        "binance",
        "spot",
        "BTCUSDT",
        "1h",
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
    )
    assert len(rows) == 4

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

    assert report.requested_start == datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    assert report.requested_end == datetime(2024, 1, 1, 15, 0, tzinfo=UTC)
    assert report.effective_end == datetime(2024, 1, 1, 14, 0, tzinfo=UTC)
    assert report.expected_count == 4
    assert report.actual_count == 4
    assert report.missing_count == 0
    assert report.unaligned_count == 0
    assert report.incomplete_tail_excluded_count == 1
    assert report.overall_status == "PASS"
