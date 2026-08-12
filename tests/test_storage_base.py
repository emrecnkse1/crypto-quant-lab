import inspect
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage import base
from crypto_quant_lab.storage.base import (
    DataConflictError,
    DataCorruptionError,
    HistoricalCandle,
    HistoricalCandleStore,
    StorageError,
)


def make_candle(**overrides):
    fields = {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "open_time": datetime(2024, 1, 1, tzinfo=UTC),
        "open": Decimal(50000),
        "high": Decimal(50500),
        "low": Decimal(49500),
        "close": Decimal(50200),
        "volume": Decimal("12.5"),
    }
    fields.update(overrides)
    return Candle(**fields)


def make_historical_candle(**overrides):
    fields = {
        "exchange": "binance",
        "market_type": "spot",
        "candle": make_candle(),
    }
    fields.update(overrides)
    return HistoricalCandle(**fields)


def test_valid_historical_candle_can_be_created():
    record = make_historical_candle()
    assert record.exchange == "binance"
    assert record.market_type == "spot"


def test_empty_exchange_is_rejected():
    with pytest.raises(ValueError, match="exchange"):
        make_historical_candle(exchange="")


def test_whitespace_exchange_is_rejected():
    with pytest.raises(ValueError, match="exchange"):
        make_historical_candle(exchange="   ")


def test_empty_market_type_is_rejected():
    with pytest.raises(ValueError, match="market_type"):
        make_historical_candle(market_type="")


def test_whitespace_market_type_is_rejected():
    with pytest.raises(ValueError, match="market_type"):
        make_historical_candle(market_type="   ")


def test_canonical_key_contains_correct_fields_in_order():
    candle = make_candle()
    record = make_historical_candle(candle=candle)

    assert record.canonical_key == (
        "binance",
        "spot",
        candle.symbol,
        candle.timeframe,
        candle.open_time,
    )


def test_historical_candle_is_immutable():
    record = make_historical_candle()
    with pytest.raises(FrozenInstanceError):
        record.exchange = "other"


def test_storage_exception_hierarchy():
    assert issubclass(DataConflictError, StorageError)
    assert issubclass(DataCorruptionError, StorageError)
    assert issubclass(StorageError, Exception)


def test_base_module_does_not_import_sqlite3():
    source = inspect.getsource(base)
    assert "sqlite3" not in source


def test_protocol_defines_required_contract():
    for method_name in ("write_batch", "query", "close"):
        assert hasattr(HistoricalCandleStore, method_name)

    query_params = set(inspect.signature(HistoricalCandleStore.query).parameters)
    assert query_params == {
        "self",
        "exchange",
        "market_type",
        "symbol",
        "timeframe",
        "start_time",
        "end_time",
    }

    write_batch_params = set(inspect.signature(HistoricalCandleStore.write_batch).parameters)
    assert write_batch_params == {"self", "records"}

    close_params = set(inspect.signature(HistoricalCandleStore.close).parameters)
    assert close_params == {"self"}
