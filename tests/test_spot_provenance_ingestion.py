"""Spot ingestion with provenance + coverage (FUNDING_RESEARCH_SPEC.md Bölüm 19.13).

The legacy `ingest_binance_historical_range` is covered by the existing data
quality tests and stays provenance-less; these tests cover the additive
`ingest_binance_spot_klines_with_provenance`. No network: pages come from a
fake transport or a patched urlopen. Expected metadata is written literally.
"""

import io
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_quant_lab.data_quality.ingestion import (
    ingest_binance_historical_range,
    ingest_binance_spot_klines_with_provenance,
)
from crypto_quant_lab.market_data.binance_historical import parse_binance_historical_kline
from crypto_quant_lab.storage.base import DataConflictError, StorageError
from crypto_quant_lab.storage.datasets import CandleCoverageInterval, CandleDataset
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T0_MS = 1767225600000
H = timedelta(hours=1)
H_MS = 3_600_000
AS_OF = T0 + 10 * H
NS = ("binance", "spot", "BTCUSDT", "1h")
SOURCE = "synthetic:test-spot/v1"


def row(k, close="100"):
    open_ms = T0_MS + k * H_MS
    high = max("100", close, key=Decimal)
    low = min("100", close, key=Decimal)
    return [open_ms, "100", high, low, close, "1", open_ms + H_MS - 1, "100", 1, "0.5", "50", "0"]


def fake(rows, *, fail_after=None):
    calls = []

    def fetch(*, start_time_ms, end_time_ms):
        calls.append(start_time_ms)
        if fail_after is not None and len(calls) > fail_after:
            raise ConnectionError("transport dropped")
        page = [r for r in rows if start_time_ms <= r[0] <= end_time_ms][:2]
        return [parse_binance_historical_kline(r, "BTCUSDT", "1h") for r in page]

    fetch.calls = calls
    return fetch


def ingest(store, fetch, *, end=T0 + 4 * H, source=SOURCE, **kwargs):
    return ingest_binance_spot_klines_with_provenance(
        store,
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=T0,
        requested_end=end,
        as_of_time=AS_OF,
        fetch_page=fetch,
        source=source,
        **kwargs,
    )


def untouched(store):
    return (
        store.query_dataset(*NS) is None
        and store.query_coverage(*NS, T0, T0 + 10 * H) == []
        and store.query(*NS, T0, T0 + 10 * H) == []
    )


def test_successful_ingestion_registers_provenance_and_exact_coverage(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "spot.db")
    fetch = fake([row(k) for k in range(4)])
    result = ingest(store, fetch)
    assert result.candle_count == 4
    assert store.query_dataset(*NS) == CandleDataset(
        "binance", "spot", "BTCUSDT", "1h", "spot_trade", "synthetic:test-spot/v1"
    )
    assert store.query_coverage(*NS, T0, T0 + 10 * H) == [
        CandleCoverageInterval(start_time=T0, end_time=T0 + 4 * H)
    ]
    assert len(fetch.calls) == 2  # pages of 2 rows


def test_real_adapter_path_records_the_real_endpoint(tmp_path, monkeypatch):
    seen = []

    def urlopen(url, timeout):
        seen.append(url)
        return io.BytesIO(json.dumps([row(0), row(1)]).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    store = SQLiteHistoricalCandleStore(tmp_path / "spot.db")
    ingest_binance_spot_klines_with_provenance(
        store,
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=T0,
        requested_end=T0 + 2 * H,
        as_of_time=AS_OF,
    )
    assert seen[0].startswith("https://data-api.binance.vision/api/v3/klines?")
    assert store.query_dataset(*NS).source == (
        "binance:GET https://data-api.binance.vision/api/v3/klines"
    )


def test_source_label_rules(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "spot.db")
    with pytest.raises(ValueError, match="must declare its source label"):
        ingest(store, fake([row(0)]), source=None)
    with pytest.raises(ValueError, match="source is fixed by the real spot adapter"):
        ingest_binance_spot_klines_with_provenance(
            store,
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=T0,
            requested_end=T0 + H,
            as_of_time=AS_OF,
            source=SOURCE,
        )
    assert untouched(store)


def test_empty_complete_range_records_authoritative_absence(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "spot.db")
    result = ingest(store, fake([]))
    assert (result.candle_count, result.leading_absent_count) == (0, 4)
    assert store.query_dataset(*NS).source == SOURCE
    assert store.query_coverage(*NS, T0, T0 + 10 * H) == [
        CandleCoverageInterval(start_time=T0, end_time=T0 + 4 * H)
    ]
    assert store.query(*NS, T0, T0 + 10 * H) == []


def test_failed_or_incomplete_pagination_writes_nothing(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "spot.db")
    with pytest.raises(ConnectionError, match="transport dropped"):
        ingest(store, fake([row(k) for k in range(4)], fail_after=1), max_attempts=1)
    assert untouched(store)
    unordered = [row(1), row(0), row(2), row(3)]
    with pytest.raises(ValueError, match="not strictly ascending"):
        ingest(store, fake(unordered))
    assert untouched(store)


def test_retry_is_idempotent_and_conflicts_are_refused(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "spot.db")
    ingest(store, fake([row(k) for k in range(4)]))
    ingest(store, fake([row(k) for k in range(4)]))  # same content: no change
    assert len(store.query(*NS, T0, T0 + 10 * H)) == 4
    assert len(store.query_coverage(*NS, T0, T0 + 10 * H)) == 1
    with pytest.raises(DataConflictError):
        ingest(store, fake([row(k, "101") for k in range(4)]))
    assert [r.candle.close for r in store.query(*NS, T0, T0 + 10 * H)] == [Decimal(100)] * 4
    with pytest.raises(DataConflictError, match="is registered as"):
        ingest(store, fake([row(k) for k in range(4)]), source="synthetic:other")


def test_legacy_provenance_less_rows_are_never_relabeled(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "spot.db")
    ingest_binance_historical_range(
        store,
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=T0,
        requested_end=T0 + 2 * H,
        as_of_time=AS_OF,
        fetch_page=fake([row(0), row(1)]),
    )
    assert store.query_dataset(*NS) is None  # legacy path: still no provenance
    with pytest.raises(DataConflictError, match="unknown provenance"):
        ingest(store, fake([row(k) for k in range(4)]))
    assert store.query_dataset(*NS) is None


def test_registered_namespace_refuses_the_legacy_write_path(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "spot.db")
    ingest(store, fake([row(k) for k in range(4)]))
    with pytest.raises(StorageError, match="registered dataset provenance"):
        ingest_binance_historical_range(
            store,
            symbol="BTCUSDT",
            timeframe="1h",
            requested_start=T0 + 4 * H,
            requested_end=T0 + 6 * H,
            as_of_time=AS_OF,
            fetch_page=fake([row(4), row(5)]),
        )
