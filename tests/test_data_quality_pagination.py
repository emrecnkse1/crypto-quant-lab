from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from crypto_quant_lab.data_quality import pagination as pagination_module
from crypto_quant_lab.data_quality.pagination import paginate_historical_klines
from crypto_quant_lab.data_quality.transport import transport_end_ms, transport_start_ms
from crypto_quant_lab.market_data.binance_historical import BinanceHistoricalKline
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.market_data.timeframes import candle_duration


def _make_kline(open_time: datetime, timeframe: str = "1h") -> BinanceHistoricalKline:
    candle = Candle(
        symbol="BTCUSDT",
        timeframe=timeframe,
        open_time=open_time,
        open=Decimal(100),
        high=Decimal(100),
        low=Decimal(100),
        close=Decimal(100),
        volume=Decimal(1),
    )
    close_time = open_time + candle_duration(timeframe) - timedelta(milliseconds=1)
    return BinanceHistoricalKline(candle=candle, close_time=close_time)


def _sequential_fetch_page(*pages):
    call_args = []
    pages_iter = iter(pages)

    def _fetch(*, start_time_ms: int, end_time_ms: int):
        call_args.append({"start_time_ms": start_time_ms, "end_time_ms": end_time_ms})
        try:
            return next(pages_iter)
        except StopIteration:
            return []

    _fetch.call_args = call_args
    return _fetch


def test_single_page_covers_full_range_and_fetches_once():
    page = [
        _make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
        _make_kline(datetime(2024, 1, 1, 11, 0, tzinfo=UTC)),
    ]
    fetch = _sequential_fetch_page(page)

    result = paginate_historical_klines(
        fetch,
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        effective_end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        timeframe="1h",
    )

    assert [k.candle.open_time for k in result] == [
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
    ]
    assert len(fetch.call_args) == 1


def test_multi_page_continues_regardless_of_page_size():
    page1 = [_make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))]
    page2 = [
        _make_kline(datetime(2024, 1, 1, 11, 0, tzinfo=UTC)),
        _make_kline(datetime(2024, 1, 1, 12, 0, tzinfo=UTC)),
    ]
    fetch = _sequential_fetch_page(page1, page2)

    result = paginate_historical_klines(
        fetch,
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        effective_end=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
        timeframe="1h",
    )

    assert [k.candle.open_time for k in result] == [
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
    ]
    assert len(fetch.call_args) == 2


def test_valid_empty_first_page_stops_without_retry():
    fetch = _sequential_fetch_page([])

    result = paginate_historical_klines(
        fetch,
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        effective_end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        timeframe="1h",
    )

    assert result == []
    assert len(fetch.call_args) == 1


def test_duplicate_raw_timestamp_is_rejected():
    page = [
        _make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
        _make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
    ]
    fetch = _sequential_fetch_page(page)

    with pytest.raises(ValueError):
        paginate_historical_klines(
            fetch,
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            effective_end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            timeframe="1h",
        )


def test_backward_raw_timestamp_is_rejected():
    page = [
        _make_kline(datetime(2024, 1, 1, 11, 0, tzinfo=UTC)),
        _make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
    ]
    fetch = _sequential_fetch_page(page)

    with pytest.raises(ValueError):
        paginate_historical_klines(
            fetch,
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            effective_end=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
            timeframe="1h",
        )


def test_raw_row_behind_current_cursor_is_rejected():
    page1 = [_make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))]
    page2 = [_make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))]
    fetch = _sequential_fetch_page(page1, page2)

    with pytest.raises(ValueError):
        paginate_historical_klines(
            fetch,
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            effective_end=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
            timeframe="1h",
        )


def test_gap_in_raw_page_is_accepted_not_filled():
    page = [
        _make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
        _make_kline(datetime(2024, 1, 1, 12, 0, tzinfo=UTC)),
    ]
    fetch = _sequential_fetch_page(page)

    result = paginate_historical_klines(
        fetch,
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        effective_end=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
        timeframe="1h",
    )

    assert [k.candle.open_time for k in result] == [
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
    ]


def test_trailing_out_of_range_row_is_excluded_from_result():
    page = [
        _make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
        _make_kline(datetime(2024, 1, 1, 11, 0, tzinfo=UTC)),
        _make_kline(datetime(2024, 1, 1, 12, 0, tzinfo=UTC)),
    ]
    fetch = _sequential_fetch_page(page)

    result = paginate_historical_klines(
        fetch,
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        effective_end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        timeframe="1h",
    )

    assert [k.candle.open_time for k in result] == [
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
    ]
    assert len(fetch.call_args) == 1


def test_entire_page_out_of_range_is_valid_and_result_empty():
    page = [_make_kline(datetime(2024, 1, 1, 11, 0, tzinfo=UTC))]
    fetch = _sequential_fetch_page(page)

    result = paginate_historical_klines(
        fetch,
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        effective_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        timeframe="1h",
    )

    assert result == []


def test_transport_call_values_match_transport_helpers():
    page = [_make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))]
    fetch = _sequential_fetch_page(page)

    requested_start = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    effective_end = datetime(2024, 1, 1, 11, 0, tzinfo=UTC)

    paginate_historical_klines(
        fetch,
        requested_start=requested_start,
        effective_end=effective_end,
        timeframe="1h",
    )

    assert fetch.call_args[0]["start_time_ms"] == transport_start_ms(requested_start)
    assert fetch.call_args[0]["end_time_ms"] == transport_end_ms(effective_end)


def test_non_utc_aware_instants_are_handled_correctly():
    plus_five = timezone(timedelta(hours=5))
    page = [_make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))]
    fetch = _sequential_fetch_page(page)

    result = paginate_historical_klines(
        fetch,
        requested_start=datetime(2024, 1, 1, 15, 0, tzinfo=plus_five),  # == 10:00 UTC
        effective_end=datetime(2024, 1, 1, 16, 0, tzinfo=plus_five),  # == 11:00 UTC
        timeframe="1h",
    )

    assert [k.candle.open_time for k in result] == [datetime(2024, 1, 1, 10, 0, tzinfo=UTC)]


def test_naive_requested_start_is_rejected():
    fetch = _sequential_fetch_page([])

    with pytest.raises(ValueError):
        paginate_historical_klines(
            fetch,
            requested_start=datetime(2024, 1, 1, 10, 0),  # noqa: DTZ001
            effective_end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            timeframe="1h",
        )


def test_naive_effective_end_is_rejected():
    fetch = _sequential_fetch_page([])

    with pytest.raises(ValueError):
        paginate_historical_klines(
            fetch,
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            effective_end=datetime(2024, 1, 1, 12, 0),  # noqa: DTZ001
            timeframe="1h",
        )


def test_requested_start_equal_effective_end_is_rejected():
    fetch = _sequential_fetch_page([])

    with pytest.raises(ValueError):
        paginate_historical_klines(
            fetch,
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            effective_end=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            timeframe="1h",
        )


def test_requested_start_after_effective_end_is_rejected():
    fetch = _sequential_fetch_page([])

    with pytest.raises(ValueError):
        paginate_historical_klines(
            fetch,
            requested_start=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            effective_end=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            timeframe="1h",
        )


def test_unsupported_timeframe_is_rejected():
    fetch = _sequential_fetch_page([])

    with pytest.raises(ValueError):
        paginate_historical_klines(
            fetch,
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            effective_end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            timeframe="15m",
        )
    assert fetch.call_args == []


def test_result_order_is_ascending():
    page1 = [_make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))]
    page2 = [_make_kline(datetime(2024, 1, 1, 11, 0, tzinfo=UTC))]
    fetch = _sequential_fetch_page(page1, page2)

    result = paginate_historical_klines(
        fetch,
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        effective_end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        timeframe="1h",
    )

    open_times = [k.candle.open_time for k in result]
    assert open_times == sorted(open_times)


def test_result_klines_are_the_same_objects_fetch_page_returned():
    page = [_make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))]
    fetch = _sequential_fetch_page(page)

    result = paginate_historical_klines(
        fetch,
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        effective_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        timeframe="1h",
    )

    assert result[0] is page[0]


def test_next_cursor_guard_rejects_non_advancing_cursor(monkeypatch):
    monkeypatch.setattr(pagination_module, "candle_duration", lambda timeframe: timedelta(0))
    page = [_make_kline(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))]
    fetch = _sequential_fetch_page(page)

    with pytest.raises(ValueError):
        paginate_historical_klines(
            fetch,
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            effective_end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            timeframe="1h",
        )


def test_connection_error_from_fetch_page_propagates_without_retry():
    def _raise(*, start_time_ms: int, end_time_ms: int):
        raise ConnectionError("simulated network failure")

    with pytest.raises(ConnectionError, match="simulated network failure"):
        paginate_historical_klines(
            _raise,
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            effective_end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            timeframe="1h",
        )
