"""Faz 7 second slice: Binance USDⓈ-M perpetual klines with mechanical market provenance.

Covers the strict /fapi/v1/klines adapter, deterministic pagination, the
provenance-bound atomic ingestion, spot/perpetual/price-kind separation in the
candle store (with backward compatibility for legacy spot databases) and the
provenance-checked research entry point. No network: HTTP is mocked. Expected
values are hand-derived, never produced by calling the code under test.
"""

import json
import sqlite3
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO

import pytest

import crypto_quant_lab.market_data.binance_usdm as usdm_module
from crypto_quant_lab.backtest.costs import ProportionalCommissionModel, ZeroCostModel
from crypto_quant_lab.backtest.models import BacktestConfig
from crypto_quant_lab.data_quality.usdm_ingestion import ingest_binance_usdm_perpetual_klines
from crypto_quant_lab.funding.calculator import LinearFundingModel
from crypto_quant_lab.funding.models import FundingEvent, HistoricalFundingEvent
from crypto_quant_lab.funding.sqlite import SQLiteHistoricalFundingStore
from crypto_quant_lab.market_data.binance_usdm import (
    USDM_BASE_URL,
    USDM_KLINES_PATH,
    BinanceApiError,
    build_usdm_klines_url,
    fetch_binance_usdm_klines,
    parse_binance_usdm_kline,
)
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.research.funding_carry import (
    funding_carry_candidate,
    load_funding_signal_history,
    no_trade_control_candidate,
)
from crypto_quant_lab.research.usdm_perpetual import evaluate_usdm_perpetual_funding_research
from crypto_quant_lab.storage.base import DataConflictError, HistoricalCandle, StorageError
from crypto_quant_lab.storage.datasets import (
    BINANCE,
    BINANCE_USDM_KLINES_SOURCE,
    CONTRACT_TRADE,
    MARK_PRICE,
    SPOT,
    USDM_PERPETUAL,
    CandleCoverageInterval,
    CandleDataset,
    binance_usdm_perpetual_contract_trade_dataset,
)
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore
from crypto_quant_lab.validation.metrics import compute_stage1_metrics
from crypto_quant_lab.validation.windows import TemporalWindow

SYMBOL = "BTCUSDT"
TIMEFRAME = "1h"
T0 = datetime(2026, 1, 1, tzinfo=UTC)
T0_MS = 1767225600000  # 2026-01-01T00:00:00Z
HOUR = timedelta(hours=1)
HOUR_MS = 3_600_000
AS_OF = T0 + HOUR * 48
DATASET = binance_usdm_perpetual_contract_trade_dataset(SYMBOL, TIMEFRAME)


def _row(hour, *, open_="100", high="100", low="100", close="100", volume="1", trades=7):
    open_ms = T0_MS + hour * HOUR_MS
    return [open_ms, open_, high, low, close, volume, open_ms + HOUR_MS - 1, "100.0", trades,
            "0.5", "50.0", "0"]  # fmt: skip


class _FakeExchange:
    """Serves rows like Binance: open time in [startTime, endTime] inclusive, ascending, <= limit."""

    def __init__(self, hours, *, limit=1000, rows=None):
        self.rows = rows if rows is not None else [_row(hour) for hour in hours]
        self.limit = limit
        self.calls = []

    def __call__(self, *, start_time_ms, end_time_ms):
        self.calls.append((start_time_ms, end_time_ms))
        selected = [row for row in self.rows if start_time_ms <= row[0] <= end_time_ms]
        return [parse_binance_usdm_kline(row, SYMBOL, TIMEFRAME) for row in selected[: self.limit]]


def _store(tmp_path, name="candles.db"):
    return SQLiteHistoricalCandleStore(tmp_path / name)


def _ingest(store, fetch, *, start=T0, end=T0 + HOUR * 26, as_of=AS_OF, **kwargs):
    return ingest_binance_usdm_perpetual_klines(
        store,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        requested_start=start,
        requested_end=end,
        as_of_time=as_of,
        fetch_page=fetch,
        **kwargs,
    )


def _perp_rows(store, start=T0, end=T0 + HOUR * 30):
    return store.query(BINANCE, USDM_PERPETUAL, SYMBOL, TIMEFRAME, start, end)


# ================================================================
# adapter and parsing
# ================================================================


def test_url_uses_futures_base_path_and_exact_parameters():
    url = build_usdm_klines_url(SYMBOL, TIMEFRAME, start_time_ms=1, end_time_ms=2, limit=1500)
    assert USDM_BASE_URL == "https://fapi.binance.com"
    assert USDM_KLINES_PATH == "/fapi/v1/klines"
    assert url == (
        "https://fapi.binance.com/fapi/v1/klines"
        "?symbol=BTCUSDT&interval=1h&startTime=1&endTime=2&limit=1500"
    )


@pytest.mark.parametrize("limit", [0, 1501, True, 10.0])
def test_limit_bounds_are_enforced(limit):
    with pytest.raises(ValueError, match="limit must be an int between 1 and 1500"):
        build_usdm_klines_url(SYMBOL, TIMEFRAME, start_time_ms=1, end_time_ms=2, limit=limit)


def test_valid_twelve_field_row_parses_without_float():
    kline = parse_binance_usdm_kline(
        _row(0, open_="93548.80", high="94449.20", low="93460.20", close="94363.60",
             volume="5744.609"),
        SYMBOL,
        TIMEFRAME,
    )  # fmt: skip
    assert kline.candle == Candle(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        open_time=T0,
        open=Decimal("93548.80"),
        high=Decimal("94449.20"),
        low=Decimal("93460.20"),
        close=Decimal("94363.60"),
        volume=Decimal("5744.609"),
    )
    assert kline.close_time == T0 + HOUR - timedelta(milliseconds=1)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda row: row[:11], "exactly 12 fields, got 11"),
        (lambda row: [*row, "extra"], "exactly 12 fields, got 13"),
        (lambda row: ["x", *row[1:]], "invalid open time"),
        (lambda row: [-1, *row[1:]], "invalid open time"),
        (lambda row: [row[0], 100.0, *row[2:]], "open must be a JSON string, got float"),
        (lambda row: [row[0], "abc", *row[2:]], "invalid open"),
        (lambda row: [row[0], "NaN", *row[2:]], "open must be finite"),
        (lambda row: [*row[:4], "Infinity", *row[5:]], "close must be finite"),
        (lambda row: [row[0], "0", *row[2:]], "open must be > 0"),
        (lambda row: [*row[:5], "-1", *row[6:]], "volume must be >= 0"),
        (lambda row: [*row[:8], -3, *row[9:]], "invalid number of trades"),
        (lambda row: [*row[:8], "3", *row[9:]], "invalid number of trades"),
        (lambda row: [*row[:9], "-0.1", *row[10:]], "taker buy base volume must be >= 0"),
        (lambda row: [*row[:11], 0], "ignore field must be a JSON string"),
    ],
)
def test_malformed_rows_are_rejected(mutate, message):
    with pytest.raises(ValueError, match=message):
        parse_binance_usdm_kline(mutate(_row(0)), SYMBOL, TIMEFRAME)


def test_ohlc_invariants_are_enforced():
    with pytest.raises(ValueError, match="high cannot be less than"):
        parse_binance_usdm_kline(_row(0, high="99"), SYMBOL, TIMEFRAME)
    with pytest.raises(ValueError, match="low cannot be greater than"):
        parse_binance_usdm_kline(_row(0, low="101", high="102"), SYMBOL, TIMEFRAME)


def test_fetch_decodes_one_page_and_rejects_overfull_pages(monkeypatch):
    seen = []

    def fake_request(url, timeout):
        seen.append(url)
        return json.dumps([_row(0), _row(1)]).encode()

    monkeypatch.setattr(usdm_module, "_request", fake_request)
    page = fetch_binance_usdm_klines(SYMBOL, TIMEFRAME, start_time_ms=T0_MS,
                                     end_time_ms=T0_MS + HOUR_MS, limit=2)  # fmt: skip
    assert [kline.candle.open_time for kline in page] == [T0, T0 + HOUR]
    assert seen[0].startswith("https://fapi.binance.com/fapi/v1/klines?")
    with pytest.raises(ValueError, match="page exceeded limit"):
        fetch_binance_usdm_klines(SYMBOL, TIMEFRAME, start_time_ms=0, end_time_ms=1, limit=1)


def test_empty_response_is_an_empty_page(monkeypatch):
    monkeypatch.setattr(usdm_module, "_request", lambda url, timeout: b"[]")
    assert fetch_binance_usdm_klines(SYMBOL, TIMEFRAME, start_time_ms=0, end_time_ms=1) == []


def test_api_error_object_and_http_error_are_not_connection_errors(monkeypatch):
    original_request = usdm_module._request
    monkeypatch.setattr(
        usdm_module, "_request", lambda url, timeout: b'{"code":-1121,"msg":"Invalid symbol."}'
    )
    with pytest.raises(BinanceApiError, match="Invalid symbol"):
        fetch_binance_usdm_klines(SYMBOL, TIMEFRAME, start_time_ms=0, end_time_ms=1)

    def raise_http(url, timeout):
        raise urllib.error.HTTPError(url, 400, "Bad Request", {}, BytesIO(b'{"code":-1121}'))

    monkeypatch.setattr(urllib.request, "urlopen", raise_http)
    with pytest.raises(BinanceApiError, match="HTTP 400"):
        original_request("https://fapi.binance.com/x", 1.0)

    def raise_url(url, timeout):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(urllib.request, "urlopen", raise_url)
    with pytest.raises(ConnectionError):
        original_request("https://fapi.binance.com/x", 1.0)


# ================================================================
# pagination and ingestion
# ================================================================


def test_single_page_exact_half_open_range(tmp_path):
    store = _store(tmp_path)
    exchange = _FakeExchange(range(30))
    result = _ingest(store, exchange, end=T0 + HOUR * 4)
    rows = _perp_rows(store)
    assert [row.candle.open_time for row in rows] == [T0 + HOUR * h for h in range(4)]
    assert result.candle_count == 4
    # inclusive endTime overfetch (open == 04:00) is filtered, not stored
    assert T0 + HOUR * 4 not in [row.candle.open_time for row in rows]
    store.close()


def test_multiple_pages_exact_limit_and_short_last_page(tmp_path):
    store = _store(tmp_path)
    exchange = _FakeExchange(range(26), limit=5)
    result = _ingest(store, exchange)
    assert result.candle_count == 26
    assert [row.candle.open_time for row in _perp_rows(store)] == [T0 + HOUR * h for h in range(26)]
    # 26 candles at 5 per page: 5 full pages + a short last page of 1
    assert len(exchange.calls) == 6
    assert exchange.calls[1][0] == T0_MS + 5 * HOUR_MS  # cursor = last open + duration
    store.close()


def test_page_boundary_duplicate_is_rejected_and_nothing_is_written(tmp_path):
    store = _store(tmp_path)
    rows = [_row(h) for h in range(10)]

    class Overlapping(_FakeExchange):
        def __call__(self, *, start_time_ms, end_time_ms):
            page = super().__call__(start_time_ms=start_time_ms, end_time_ms=end_time_ms)
            if len(self.calls) == 2:  # second page repeats the previous page's last candle
                return [parse_binance_usdm_kline(rows[2], SYMBOL, TIMEFRAME), *page]
            return page

    with pytest.raises(ValueError, match="behind current_cursor"):
        _ingest(store, Overlapping(range(10), limit=3), end=T0 + HOUR * 10)
    assert _perp_rows(store) == []
    assert store.query_dataset(*DATASET.namespace) is None
    store.close()


def test_unordered_and_non_advancing_pages_are_rejected(tmp_path):
    store = _store(tmp_path)
    unordered = _FakeExchange(None, rows=[_row(1), _row(0)])
    with pytest.raises(ValueError, match="behind current_cursor|not strictly ascending"):
        _ingest(store, unordered, end=T0 + HOUR * 2)

    class Stuck(_FakeExchange):
        def __call__(self, *, start_time_ms, end_time_ms):
            self.calls.append((start_time_ms, end_time_ms))
            return [parse_binance_usdm_kline(_row(0), SYMBOL, TIMEFRAME)]

    with pytest.raises(ValueError, match="behind current_cursor"):
        _ingest(store, Stuck(()), end=T0 + HOUR * 3)
    assert _perp_rows(store) == []
    store.close()


def test_mid_pagination_failure_commits_no_candles_and_no_coverage(tmp_path):
    store = _store(tmp_path)

    class FailsOnSecondPage(_FakeExchange):
        def __call__(self, *, start_time_ms, end_time_ms):
            if len(self.calls) == 1:
                self.calls.append((start_time_ms, end_time_ms))
                raise ValueError("upstream returned garbage")
            return super().__call__(start_time_ms=start_time_ms, end_time_ms=end_time_ms)

    with pytest.raises(ValueError, match="upstream returned garbage"):
        _ingest(store, FailsOnSecondPage(range(26), limit=5))
    assert _perp_rows(store) == []
    assert store.query_coverage(*DATASET.namespace, T0, T0 + HOUR * 26) == []
    assert store.query_dataset(*DATASET.namespace) is None
    store.close()


def test_connection_errors_are_retried_then_fail_closed(tmp_path):
    store = _store(tmp_path)

    class Flaky(_FakeExchange):
        def __call__(self, *, start_time_ms, end_time_ms):
            self.calls.append(("attempt",))
            if len(self.calls) == 1:
                raise ConnectionError("blip")
            return super().__call__(start_time_ms=start_time_ms, end_time_ms=end_time_ms)

    assert _ingest(store, Flaky(range(4)), end=T0 + HOUR * 4).candle_count == 4

    class Down(_FakeExchange):
        def __call__(self, *, start_time_ms, end_time_ms):
            raise ConnectionError("down")

    other = _store(tmp_path, "other.db")
    with pytest.raises(ConnectionError):
        _ingest(other, Down(()), end=T0 + HOUR * 4)
    assert other.query_dataset(*DATASET.namespace) is None
    store.close()
    other.close()


def test_unfinished_tail_is_excluded_and_reported(tmp_path):
    store = _store(tmp_path)
    as_of = T0 + HOUR * 3 + timedelta(minutes=30)  # the 03:00 candle is still open
    result = _ingest(store, _FakeExchange(range(10)), end=T0 + HOUR * 6, as_of=as_of)
    assert result.effective_end == T0 + HOUR * 3
    assert result.requested_end == T0 + HOUR * 6
    assert [row.candle.open_time for row in _perp_rows(store)] == [T0, T0 + HOUR, T0 + HOUR * 2]
    assert store.query_coverage(*DATASET.namespace, T0, T0 + HOUR * 6) == [
        CandleCoverageInterval(start_time=T0, end_time=T0 + HOUR * 3)
    ]
    store.close()


def test_inconsistent_close_time_is_rejected(tmp_path):
    store = _store(tmp_path)
    bad = _row(0)
    bad[6] = bad[0] + HOUR_MS  # close time must be open + duration - 1ms
    with pytest.raises(ValueError, match="close_time is inconsistent"):
        _ingest(store, _FakeExchange(None, rows=[bad]), end=T0 + HOUR)
    store.close()


def test_leading_internal_and_trailing_absences_are_reported_not_filled(tmp_path):
    store = _store(tmp_path)
    hours = [2, 3, 4, 6, 7]  # nothing before 02:00, gap at 05:00, nothing after 07:00
    result = _ingest(store, _FakeExchange(hours), end=T0 + HOUR * 10)
    assert (result.leading_absent_count, result.internal_missing_count,
            result.trailing_absent_count) == (2, 1, 2)  # fmt: skip
    assert result.first_open_time == T0 + HOUR * 2
    assert result.last_open_time == T0 + HOUR * 7
    assert len(_perp_rows(store)) == 5
    store.close()


def test_reingestion_is_idempotent(tmp_path):
    store = _store(tmp_path)
    first = _ingest(store, _FakeExchange(range(26)))
    second = _ingest(store, _FakeExchange(range(26)))
    assert first == second
    assert len(_perp_rows(store)) == 26
    assert store.query_coverage(*DATASET.namespace, T0, T0 + HOUR * 26) == [
        CandleCoverageInterval(start_time=T0, end_time=T0 + HOUR * 26)
    ]
    store.close()


def test_changed_upstream_value_on_reingestion_is_a_conflict(tmp_path):
    store = _store(tmp_path)
    _ingest(store, _FakeExchange(range(4)), end=T0 + HOUR * 4)
    changed = _FakeExchange(
        None, rows=[_row(0, close="100", high="101"), *[_row(h) for h in (1, 2, 3)]]
    )
    with pytest.raises(DataConflictError):
        _ingest(store, changed, end=T0 + HOUR * 4)
    store.close()


# ================================================================
# market / price-kind separation and backward compatibility
# ================================================================


def _spot_record(hour=0, close="200"):
    candle = Candle(symbol=SYMBOL, timeframe=TIMEFRAME, open_time=T0 + HOUR * hour,
                    open=Decimal(close), high=Decimal(close), low=Decimal(close),
                    close=Decimal(close), volume=Decimal(1))  # fmt: skip
    return HistoricalCandle(exchange=BINANCE, market_type=SPOT, candle=candle)


def test_spot_and_perpetual_same_symbol_timeframe_open_time_do_not_collide(tmp_path):
    store = _store(tmp_path)
    store.write_batch([_spot_record(0)])
    _ingest(store, _FakeExchange(range(4)), end=T0 + HOUR * 4)
    spot = store.query(BINANCE, SPOT, SYMBOL, TIMEFRAME, T0, T0 + HOUR)
    perp = store.query(BINANCE, USDM_PERPETUAL, SYMBOL, TIMEFRAME, T0, T0 + HOUR)
    assert spot[0].candle.close == Decimal(200)
    assert perp[0].candle.close == Decimal(100)
    assert store.query_dataset(BINANCE, SPOT, SYMBOL, TIMEFRAME) is None  # legacy: unknown
    assert store.query_dataset(*DATASET.namespace) == DATASET
    store.close()


def test_registered_namespace_cannot_be_written_through_legacy_write_batch(tmp_path):
    store = _store(tmp_path)
    _ingest(store, _FakeExchange(range(4)), end=T0 + HOUR * 4)
    mark_like = HistoricalCandle(exchange=BINANCE, market_type=USDM_PERPETUAL,
                                 candle=_spot_record(10).candle)  # fmt: skip
    with pytest.raises(StorageError, match="registered dataset provenance"):
        store.write_batch([mark_like])
    assert len(_perp_rows(store)) == 4
    store.close()


def test_unknown_provenance_rows_are_never_relabeled(tmp_path):
    store = _store(tmp_path)
    legacy = HistoricalCandle(exchange=BINANCE, market_type=USDM_PERPETUAL,
                              candle=_spot_record(0).candle)  # fmt: skip
    store.write_batch([legacy])
    with pytest.raises(DataConflictError, match="unknown provenance"):
        _ingest(store, _FakeExchange(range(1, 4)), start=T0 + HOUR, end=T0 + HOUR * 4)
    assert store.query_dataset(*DATASET.namespace) is None
    assert len(_perp_rows(store)) == 1
    store.close()


def test_conflicting_price_kind_registration_is_rejected(tmp_path):
    store = _store(tmp_path)
    _ingest(store, _FakeExchange(range(4)), end=T0 + HOUR * 4)
    mark = CandleDataset(
        exchange=BINANCE,
        market_type=USDM_PERPETUAL,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        price_kind=MARK_PRICE,
        source="binance:GET https://fapi.binance.com/fapi/v1/markPriceKlines",
    )
    with pytest.raises(DataConflictError, match="is registered as price_kind='contract_trade'"):
        store.write_ingestion_batch([], dataset=mark, covered_start=T0, covered_end=T0 + HOUR)
    store.close()


def test_legacy_database_opens_keeps_rows_and_gains_empty_provenance_tables(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(str(path))
    with connection:
        connection.execute(
            "CREATE TABLE historical_candles (exchange TEXT NOT NULL, market_type TEXT NOT NULL, "
            "symbol TEXT NOT NULL, timeframe TEXT NOT NULL, open_time_us INTEGER NOT NULL, "
            "open TEXT NOT NULL, high TEXT NOT NULL, low TEXT NOT NULL, close TEXT NOT NULL, "
            "volume TEXT NOT NULL, "
            "PRIMARY KEY (exchange, market_type, symbol, timeframe, open_time_us))"
        )
        connection.execute(
            "INSERT INTO historical_candles VALUES "
            "('binance','spot','BTCUSDT','1h',1767225600000000,'1','1','1','1','1')"
        )
    connection.close()
    store = SQLiteHistoricalCandleStore(path)
    assert len(store.query(BINANCE, SPOT, SYMBOL, TIMEFRAME, T0, T0 + HOUR)) == 1
    assert store.query_dataset(BINANCE, SPOT, SYMBOL, TIMEFRAME) is None
    assert store.query_coverage(BINANCE, SPOT, SYMBOL, TIMEFRAME, T0, T0 + HOUR) == []
    store.write_batch([_spot_record(1)])  # legacy write path still works
    store.close()


def test_dataset_record_validation():
    with pytest.raises(ValueError, match="price_kind cannot be empty"):
        CandleDataset(exchange=BINANCE, market_type=USDM_PERPETUAL, symbol=SYMBOL,
                      timeframe=TIMEFRAME, price_kind=" ", source="x")  # fmt: skip
    assert DATASET.price_kind == CONTRACT_TRADE
    assert DATASET.source == BINANCE_USDM_KLINES_SOURCE


# ================================================================
# provenance-checked research entry point
# ================================================================


def _funding_store(path, market_type=USDM_PERPETUAL, symbol=SYMBOL):
    store = SQLiteHistoricalFundingStore(path)
    events = [(0, "0.001"), (8, "0.0015"), (16, "0.001")]
    store.write_ingestion_batch(
        [
            HistoricalFundingEvent(
                exchange=BINANCE,
                market_type=market_type,
                symbol=symbol,
                funding=FundingEvent(
                    event_time=T0 + HOUR * hour,
                    funding_rate=Decimal(rate),
                    reference_price=Decimal(100),
                    rate_type="Regular",
                ),
            )
            for hour, rate in events
        ],
        exchange=BINANCE,
        market_type=market_type,
        symbol=symbol,
        covered_start=T0,
        covered_end=T0 + HOUR * 25,
    )
    return store


def _history(funding, market_type=USDM_PERPETUAL):
    return load_funding_signal_history(
        funding, exchange=BINANCE, market_type=market_type, symbol=SYMBOL, coverage_start=T0,
        coverage_end=T0 + HOUR * 25, publication_lag=timedelta(0),
    )  # fmt: skip


CARRY = funding_carry_candidate(
    "carry",
    short_entry_rate=Decimal("0.0005"),
    long_entry_rate=Decimal("-0.0005"),
    max_funding_age=timedelta(hours=9),
    publication_lag=timedelta(0),
)
WINDOW = TemporalWindow(start=T0, end=T0 + HOUR * 12)


def _run(candles, funding, history, candidate=CARRY, *, windows=(WINDOW,), cost_model=None):
    return evaluate_usdm_perpetual_funding_research(
        candles,
        funding,
        history,
        candidate,
        windows=windows,
        timeframe=TIMEFRAME,
        as_of_time=AS_OF,
        config=BacktestConfig(initial_cash=Decimal(1000), position_quantity=Decimal(1)),
        cost_model=cost_model or ZeroCostModel(),
        funding_model=LinearFundingModel(),
    )


def test_mocked_http_to_ingestion_to_research_trial_and_metrics(tmp_path, monkeypatch):
    rows = [_row(h) for h in range(26)]

    def fake_request(url, timeout):
        query = dict(part.split("=") for part in url.split("?")[1].split("&"))
        start, end, limit = int(query["startTime"]), int(query["endTime"]), int(query["limit"])
        return json.dumps([r for r in rows if start <= r[0] <= end][:limit]).encode()

    monkeypatch.setattr(usdm_module, "_request", fake_request)
    candles = _store(tmp_path)
    ingest_binance_usdm_perpetual_klines(candles, symbol=SYMBOL, timeframe=TIMEFRAME,
                                         requested_start=T0, requested_end=T0 + HOUR * 26,
                                         as_of_time=AS_OF, page_limit=7)  # fmt: skip
    funding = _funding_store(tmp_path / "funding.db")
    trial = _run(candles, funding, _history(funding))
    result = trial.results[0].result
    # SHORT from 01:00 open at 100; 08:00 settlement: -1 x 100 x 0.0015 -> +0.15 cash, once
    assert result.fill_count == 1
    assert result.final_equity == Decimal("1000.15")
    assert compute_stage1_metrics(result).total_return == Decimal("0.00015")
    costly = _run(candles, funding, _history(funding),
                  cost_model=ProportionalCommissionModel(rate=Decimal("0.0005")))  # fmt: skip
    # one fill, notional 100 x 0.0005 = 0.05 commission on top of the funding
    assert costly.results[0].result.final_equity == Decimal("1000.10")
    control = _run(candles, funding, _history(funding), no_trade_control_candidate("control"))
    assert control.results[0].result.final_equity == Decimal(1000)
    candles.close()
    funding.close()


def test_spot_candle_dataset_is_rejected_before_any_backtest(tmp_path):
    candles = _store(tmp_path)
    candles.write_batch([_spot_record(h, "100") for h in range(26)])
    funding = _funding_store(tmp_path / "funding.db")
    with pytest.raises(ValueError, match="has no registered provenance"):
        _run(candles, funding, _history(funding))
    candles.close()
    funding.close()


def test_mark_price_candle_dataset_is_rejected(tmp_path):
    candles = _store(tmp_path)
    mark = CandleDataset(exchange=BINANCE, market_type=USDM_PERPETUAL, symbol=SYMBOL,
                         timeframe=TIMEFRAME, price_kind=MARK_PRICE, source="mark")  # fmt: skip
    records = [HistoricalCandle(exchange=BINANCE, market_type=USDM_PERPETUAL,
                                candle=_spot_record(h, "100").candle) for h in range(26)]  # fmt: skip
    candles.write_ingestion_batch(records, dataset=mark, covered_start=T0,
                                  covered_end=T0 + HOUR * 26)  # fmt: skip
    funding = _funding_store(tmp_path / "funding.db")
    with pytest.raises(ValueError, match="registered as price_kind='mark_price'"):
        _run(candles, funding, _history(funding))
    candles.close()
    funding.close()


def test_perpetual_candles_cannot_serve_a_spot_funding_history(tmp_path):
    candles = _store(tmp_path)
    _ingest(candles, _FakeExchange(range(26)))
    funding = _funding_store(tmp_path / "funding.db", market_type=SPOT)
    with pytest.raises(ValueError, match="funding history must be"):
        _run(candles, funding, _history(funding, market_type=SPOT))
    candles.close()
    funding.close()


def test_missing_candle_or_funding_coverage_is_rejected_before_backtest(tmp_path):
    candles = _store(tmp_path)
    _ingest(candles, _FakeExchange(range(26)), end=T0 + HOUR * 6)
    funding = _funding_store(tmp_path / "funding.db")
    with pytest.raises(ValueError, match="not inside the candle store's authoritative coverage"):
        _run(candles, funding, _history(funding))
    full = _store(tmp_path, "full.db")
    _ingest(full, _FakeExchange(range(40)), end=T0 + HOUR * 40)
    late = (TemporalWindow(start=T0 + HOUR * 20, end=T0 + HOUR * 30),)
    with pytest.raises(ValueError, match="outside funding history coverage"):
        _run(full, funding, _history(funding), windows=late)
    candles.close()
    full.close()
    funding.close()


def test_later_candle_data_does_not_change_earlier_results(tmp_path):
    base = _store(tmp_path, "base.db")
    _ingest(base, _FakeExchange(range(26)))
    changed_rows = [_row(h) if h < 13 else _row(h, open_="150", high="150", low="150", close="150")
                    for h in range(26)]  # fmt: skip
    changed = _store(tmp_path, "changed.db")
    _ingest(changed, _FakeExchange(None, rows=changed_rows))
    funding = _funding_store(tmp_path / "funding.db")
    first = _run(base, funding, _history(funding))
    second = _run(changed, funding, _history(funding))
    assert first == second  # window ends at 12:00; later prices are irrelevant
    base.close()
    changed.close()
    funding.close()
