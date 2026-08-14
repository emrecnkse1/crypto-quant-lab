from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal

import pytest

from crypto_quant_lab.data_quality.finalization import (
    is_binance_historical_kline_finalized,
    validated_close_boundary,
)
from crypto_quant_lab.market_data.binance_historical import BinanceHistoricalKline
from crypto_quant_lab.market_data.models import Candle


class _BrokenTzInfo(tzinfo):
    """A tzinfo that pretends to be attached but reports no offset (pseudo-naive)."""

    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return None


def _make_candle(open_time: datetime, timeframe: str = "1h") -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=timeframe,
        open_time=open_time,
        open=Decimal(100),
        high=Decimal(100),
        low=Decimal(100),
        close=Decimal(100),
        volume=Decimal(1),
    )


def _make_kline(
    open_time: datetime, close_time: datetime, timeframe: str = "1h"
) -> BinanceHistoricalKline:
    return BinanceHistoricalKline(
        candle=_make_candle(open_time, timeframe=timeframe), close_time=close_time
    )


# --- validated_close_boundary: consistency ---


def test_1h_consistent_close_time_yields_expected_boundary():
    kline = _make_kline(
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 10, 59, 59, 999000, tzinfo=UTC),
        timeframe="1h",
    )

    assert validated_close_boundary(kline) == datetime(2024, 1, 1, 11, 0, tzinfo=UTC)


def test_4h_consistent_close_time_yields_expected_boundary():
    kline = _make_kline(
        datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 15, 59, 59, 999000, tzinfo=UTC),
        timeframe="4h",
    )

    assert validated_close_boundary(kline) == datetime(2024, 1, 1, 16, 0, tzinfo=UTC)


def test_close_time_one_millisecond_early_is_rejected():
    kline = _make_kline(
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 10, 59, 59, 998000, tzinfo=UTC),
        timeframe="1h",
    )

    with pytest.raises(ValueError, match="close_time"):
        validated_close_boundary(kline)


def test_close_time_one_millisecond_late_is_rejected():
    kline = _make_kline(
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 11, 0, 0, 0, tzinfo=UTC),
        timeframe="1h",
    )

    with pytest.raises(ValueError, match="close_time"):
        validated_close_boundary(kline)


def test_close_time_microsecond_level_inconsistency_is_rejected():
    kline = _make_kline(
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 10, 59, 59, 999001, tzinfo=UTC),
        timeframe="1h",
    )

    with pytest.raises(ValueError, match="close_time"):
        validated_close_boundary(kline)


def test_unsupported_timeframe_is_rejected():
    kline = _make_kline(
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 10, 14, 59, 999000, tzinfo=UTC),
        timeframe="15m",
    )

    with pytest.raises(ValueError, match="timeframe"):
        validated_close_boundary(kline)


def test_validated_boundary_is_utc_aware():
    kline = _make_kline(
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 10, 59, 59, 999000, tzinfo=UTC),
        timeframe="1h",
    )

    result = validated_close_boundary(kline)

    assert result.tzinfo is UTC


def test_naive_close_time_is_rejected():
    kline = _make_kline(
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 10, 59, 59, 999000),  # noqa: DTZ001
        timeframe="1h",
    )

    with pytest.raises(ValueError):
        validated_close_boundary(kline)


def test_pseudo_naive_close_time_is_rejected():
    kline = _make_kline(
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 10, 59, 59, 999000, tzinfo=_BrokenTzInfo()),
        timeframe="1h",
    )

    with pytest.raises(ValueError):
        validated_close_boundary(kline)


# --- is_binance_historical_kline_finalized ---

_BOUNDARY_KLINE_ARGS = (
    datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
    datetime(2024, 1, 1, 10, 59, 59, 999000, tzinfo=UTC),
)


def test_as_of_just_before_boundary_is_not_finalized():
    kline = _make_kline(*_BOUNDARY_KLINE_ARGS)

    as_of = datetime(2024, 1, 1, 10, 59, 59, 999999, tzinfo=UTC)

    assert is_binance_historical_kline_finalized(kline, as_of) is False


def test_as_of_exactly_at_boundary_is_finalized():
    kline = _make_kline(*_BOUNDARY_KLINE_ARGS)

    as_of = datetime(2024, 1, 1, 11, 0, 0, tzinfo=UTC)

    assert is_binance_historical_kline_finalized(kline, as_of) is True


def test_as_of_just_after_boundary_is_finalized():
    kline = _make_kline(*_BOUNDARY_KLINE_ARGS)

    as_of = datetime(2024, 1, 1, 11, 0, 0, 1, tzinfo=UTC)

    assert is_binance_historical_kline_finalized(kline, as_of) is True


def test_non_utc_aware_as_of_same_instant_gives_correct_result():
    kline = _make_kline(*_BOUNDARY_KLINE_ARGS)

    plus_five = timezone(timedelta(hours=5))
    as_of = datetime(2024, 1, 1, 16, 0, 0, tzinfo=plus_five)  # == 11:00:00 UTC

    assert is_binance_historical_kline_finalized(kline, as_of) is True


def test_arbitrary_non_grid_aligned_as_of_is_accepted():
    kline = _make_kline(*_BOUNDARY_KLINE_ARGS)

    as_of = datetime(2024, 1, 1, 11, 0, 0, 123456, tzinfo=UTC)

    assert is_binance_historical_kline_finalized(kline, as_of) is True


def test_naive_as_of_is_rejected():
    kline = _make_kline(*_BOUNDARY_KLINE_ARGS)

    with pytest.raises(ValueError):
        is_binance_historical_kline_finalized(kline, datetime(2024, 1, 1, 11, 0, 0))  # noqa: DTZ001


def test_pseudo_naive_as_of_is_rejected():
    kline = _make_kline(*_BOUNDARY_KLINE_ARGS)

    with pytest.raises(ValueError):
        is_binance_historical_kline_finalized(
            kline, datetime(2024, 1, 1, 11, 0, 0, tzinfo=_BrokenTzInfo())
        )


def test_inconsistent_close_time_with_late_as_of_raises_not_false():
    kline = _make_kline(
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 10, 59, 59, 998000, tzinfo=UTC),
        timeframe="1h",
    )

    far_future_as_of = datetime(2024, 1, 2, 0, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="close_time"):
        is_binance_historical_kline_finalized(kline, far_future_as_of)
