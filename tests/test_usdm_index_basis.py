"""Faz 7 third slice: USDⓈ-M index-price klines, time-safe close basis, official basis cross-check.

No network: HTTP is mocked. Expected values are hand-derived, never produced by
calling the code under test.
"""

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal, localcontext

import pytest

import crypto_quant_lab.market_data.binance_usdm as usdm_module
import crypto_quant_lab.research.basis as basis_module
from crypto_quant_lab.data_quality.usdm_ingestion import (
    ingest_binance_usdm_index_price_klines,
    ingest_binance_usdm_perpetual_klines,
)
from crypto_quant_lab.market_data.binance_usdm import (
    USDM_BASE_URL,
    USDM_INDEX_PRICE_KLINES_PATH,
    BinanceApiError,
    build_usdm_index_price_klines_url,
    fetch_binance_usdm_index_price_klines,
    parse_binance_usdm_index_price_kline,
    parse_binance_usdm_kline,
)
from crypto_quant_lab.market_data.binance_usdm_basis import (
    OFFICIAL_BASIS_PATH,
    OFFICIAL_BASIS_RATE_DISPLAY_TOLERANCE,
    BinanceOfficialBasisRecord,
    build_official_basis_url,
    check_official_basis_consistency,
    fetch_binance_official_basis,
    parse_binance_official_basis_record,
)
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.research.basis import (
    CloseBasisHistory,
    CloseBasisObservation,
    compare_with_official_basis,
    compute_close_basis_observation,
    load_close_basis_history,
    pair_close_basis,
)
from crypto_quant_lab.storage.base import DataConflictError, HistoricalCandle
from crypto_quant_lab.storage.datasets import (
    BINANCE,
    BINANCE_USDM_INDEX_PRICE_KLINES_SOURCE,
    INDEX_PRICE,
    MARK_PRICE,
    USDM_PERPETUAL,
    CandleCoverageInterval,
    CandleDataset,
    binance_usdm_index_price_dataset,
    binance_usdm_perpetual_contract_trade_dataset,
    coverage_contains,
)
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore

PAIR = "BTCUSDT"
TIMEFRAME = "1h"
T0 = datetime(2026, 1, 1, tzinfo=UTC)
T0_MS = 1767225600000  # 2026-01-01T00:00:00Z
HOUR = timedelta(hours=1)
HOUR_MS = 3_600_000
US = timedelta(microseconds=1)
AS_OF = T0 + HOUR * 48
CONTRACT = binance_usdm_perpetual_contract_trade_dataset(PAIR, TIMEFRAME)
INDEX = binance_usdm_index_price_dataset(PAIR, TIMEFRAME)


def _index_row(hour, *, close="100", open_="100", high=None, low=None, count=3600):
    open_ms = T0_MS + hour * HOUR_MS
    high = high if high is not None else max(open_, close, key=Decimal)
    low = low if low is not None else min(open_, close, key=Decimal)
    return [open_ms, open_, high, low, close, "0", open_ms + HOUR_MS - 1, "0", count,
            "0", "0", "0"]  # fmt: skip


def _contract_row(hour, *, close="100"):
    open_ms = T0_MS + hour * HOUR_MS
    high = max("100", close, key=Decimal)
    low = min("100", close, key=Decimal)
    return [open_ms, "100", high, low, close, "1", open_ms + HOUR_MS - 1, "100.0", 7,
            "0.5", "50.0", "0"]  # fmt: skip


class _Fake:
    """Serves rows like Binance: open time in [startTime, endTime] inclusive, ascending, <= limit."""

    def __init__(self, rows, parse, *, limit=1000):
        self.rows = rows
        self.parse = parse
        self.limit = limit
        self.calls = []

    def __call__(self, *, start_time_ms, end_time_ms):
        self.calls.append((start_time_ms, end_time_ms))
        selected = [row for row in self.rows if start_time_ms <= row[0] <= end_time_ms]
        return [self.parse(row, PAIR, TIMEFRAME) for row in selected[: self.limit]]


def _index_fake(rows, **kwargs):
    return _Fake(rows, parse_binance_usdm_index_price_kline, **kwargs)


def _contract_fake(rows, **kwargs):
    return _Fake(rows, parse_binance_usdm_kline, **kwargs)


def _ingest_index(store, fetch, *, start=T0, end=T0 + HOUR * 26, as_of=AS_OF, **kwargs):
    return ingest_binance_usdm_index_price_klines(
        store,
        pair=PAIR,
        timeframe=TIMEFRAME,
        requested_start=start,
        requested_end=end,
        as_of_time=as_of,
        fetch_page=fetch,
        **kwargs,
    )


def _ingest_contract(store, fetch, *, start=T0, end=T0 + HOUR * 26, as_of=AS_OF):
    return ingest_binance_usdm_perpetual_klines(
        store,
        symbol=PAIR,
        timeframe=TIMEFRAME,
        requested_start=start,
        requested_end=end,
        as_of_time=as_of,
        fetch_page=fetch,
    )


def _record(dataset, hour, close):
    return HistoricalCandle(
        exchange=dataset.exchange,
        market_type=dataset.market_type,
        candle=Candle(
            symbol=dataset.symbol,
            timeframe=dataset.timeframe,
            open_time=T0 + HOUR * hour,
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=Decimal(0),
        ),
    )


def _mock_http(monkeypatch, payload):
    seen = []

    def fake_request(url, timeout):
        seen.append(url)
        return json.dumps(payload).encode()

    monkeypatch.setattr(usdm_module, "_request", fake_request)
    return seen


# ================================================================
# index-price adapter
# ================================================================


def test_index_url_uses_pair_parameter_and_exact_path():
    url = build_usdm_index_price_klines_url(
        PAIR, TIMEFRAME, start_time_ms=T0_MS, end_time_ms=T0_MS + HOUR_MS, limit=1000
    )
    assert url == (
        f"{USDM_BASE_URL}{USDM_INDEX_PRICE_KLINES_PATH}?pair=BTCUSDT&interval=1h"
        f"&startTime={T0_MS}&endTime={T0_MS + HOUR_MS}&limit=1000"
    )
    assert url.startswith("https://fapi.binance.com/fapi/v1/indexPriceKlines?")


@pytest.mark.parametrize("limit", [0, 1501, True, "10"])
def test_index_limit_and_range_bounds_are_enforced(limit):
    with pytest.raises(ValueError, match="limit"):
        build_usdm_index_price_klines_url(PAIR, TIMEFRAME, start_time_ms=0, end_time_ms=1,
                                          limit=limit)  # fmt: skip


def test_index_invalid_pair_and_range_are_rejected():
    with pytest.raises(ValueError, match="pair cannot be empty"):
        build_usdm_index_price_klines_url("", TIMEFRAME, start_time_ms=0, end_time_ms=1, limit=1)
    with pytest.raises(ValueError, match="must be <="):
        build_usdm_index_price_klines_url(PAIR, TIMEFRAME, start_time_ms=2, end_time_ms=1, limit=1)
    assert "limit=1500" in build_usdm_index_price_klines_url(
        PAIR, TIMEFRAME, start_time_ms=0, end_time_ms=1, limit=1500
    )


def test_valid_index_row_parses_exact_decimals_and_zero_volume():
    raw = [1758585600000, "112655.27804348", "112834.25369565", "112383.45434783",
           "112397.84673913", "0", 1758589199999, "0", 3600, "0", "0", "0"]  # fmt: skip
    kline = parse_binance_usdm_index_price_kline(raw, PAIR, TIMEFRAME)
    candle = kline.candle
    assert candle.symbol == "BTCUSDT"
    assert candle.open_time == datetime(2025, 9, 23, tzinfo=UTC)
    assert kline.close_time == datetime(2025, 9, 23, 0, 59, 59, 999000, tzinfo=UTC)
    assert candle.close == Decimal("112397.84673913")
    assert str(candle.close) == "112397.84673913"
    assert candle.open == Decimal("112655.27804348")
    assert candle.high == Decimal("112834.25369565")
    assert candle.low == Decimal("112383.45434783")
    assert candle.volume == Decimal(0)


def _mutated(index, value):
    row = _index_row(0)
    row[index] = value
    return row


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (_index_row(0)[:11], "exactly 12 fields"),
        ([*_index_row(0), "0"], "exactly 12 fields"),
        (_mutated(4, 100.0), "JSON string"),
        (_mutated(4, "NaN"), "finite"),
        (_mutated(4, "abc"), "invalid close"),
        (_mutated(4, "0"), "close must be > 0"),
        (_mutated(3, "-1"), "low must be > 0"),
        (_mutated(8, "3600"), "sample count"),
        (_mutated(8, -1), "sample count"),
        (_mutated(5, 0), "ignore field \\[5\\]"),
        (_mutated(11, None), "ignore field \\[11\\]"),
        (_mutated(0, True), "open time"),
        (_mutated(6, "x"), "close time"),
        ("not a row", "sequence"),
    ],
)
def test_malformed_index_rows_are_rejected(raw, message):
    with pytest.raises(ValueError, match=message):
        parse_binance_usdm_index_price_kline(raw, PAIR, TIMEFRAME)


def test_index_ohlc_invariants_are_enforced():
    with pytest.raises(ValueError, match="high cannot be less"):
        parse_binance_usdm_index_price_kline(
            _index_row(0, open_="100", close="100", high="99", low="98"), PAIR, TIMEFRAME
        )


def test_fetch_index_page_decodes_and_fails_closed(monkeypatch):
    seen = _mock_http(monkeypatch, [_index_row(0, close="101"), _index_row(1, close="102")])
    klines = fetch_binance_usdm_index_price_klines(
        PAIR, TIMEFRAME, start_time_ms=T0_MS, end_time_ms=T0_MS + HOUR_MS, limit=2
    )
    assert [k.candle.close for k in klines] == [Decimal(101), Decimal(102)]
    assert "/fapi/v1/indexPriceKlines?pair=BTCUSDT" in seen[0]
    with pytest.raises(ValueError, match="page exceeded limit"):
        fetch_binance_usdm_index_price_klines(
            PAIR, TIMEFRAME, start_time_ms=T0_MS, end_time_ms=T0_MS + HOUR_MS, limit=1
        )
    _mock_http(monkeypatch, {"code": -1121, "msg": "Invalid symbol."})
    with pytest.raises(BinanceApiError, match="-1121"):
        fetch_binance_usdm_index_price_klines(
            PAIR, TIMEFRAME, start_time_ms=T0_MS, end_time_ms=T0_MS + HOUR_MS
        )


# ================================================================
# index-price ingestion, provenance and storage
# ================================================================


def test_index_ingestion_paginates_half_open_and_excludes_unclosed_tail(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "index.db")
    fetch = _index_fake([_index_row(hour, close=str(100 + hour)) for hour in range(30)], limit=5)
    as_of = T0 + HOUR * 20 + timedelta(minutes=30)  # hour 20 is still open
    result = _ingest_index(store, fetch, end=T0 + HOUR * 26, as_of=as_of)
    assert result.dataset == INDEX
    assert result.effective_end == T0 + HOUR * 20
    assert result.candle_count == 20
    assert (result.leading_absent_count, result.internal_missing_count,
            result.trailing_absent_count) == (0, 0, 0)  # fmt: skip
    assert len(fetch.calls) == 4  # 4 full pages of 5; the cursor then reaches effective_end
    rows = store.query(BINANCE, USDM_PERPETUAL, PAIR, TIMEFRAME, T0, T0 + HOUR * 30)
    assert [row.candle.open_time for row in rows] == [T0 + HOUR * h for h in range(20)]
    assert rows[-1].candle.close == Decimal(119)
    assert store.query_dataset(*INDEX.namespace) == INDEX
    assert store.query_coverage(*INDEX.namespace, T0, T0 + HOUR * 30) == [
        CandleCoverageInterval(start_time=T0, end_time=T0 + HOUR * 20)
    ]


def test_index_dataset_identity_is_canonical():
    assert INDEX == CandleDataset(
        exchange="binance",
        market_type="usdm_perpetual",
        symbol="BTCUSDT",
        timeframe="1h",
        price_kind="index_price",
        source="binance:GET https://fapi.binance.com/fapi/v1/indexPriceKlines",
    )
    assert INDEX.price_kind == INDEX_PRICE
    assert INDEX.source == BINANCE_USDM_INDEX_PRICE_KLINES_SOURCE
    assert INDEX.namespace == CONTRACT.namespace
    assert INDEX != CONTRACT


@pytest.mark.parametrize(
    "rows",
    [
        [_index_row(0), _index_row(1), _index_row(1), _index_row(2)],
        [_index_row(0), _index_row(2), _index_row(1)],
    ],
)
def test_index_duplicate_or_unordered_rows_write_nothing(tmp_path, rows):
    store = SQLiteHistoricalCandleStore(tmp_path / "index.db")
    with pytest.raises(ValueError):
        _ingest_index(store, _index_fake(rows), end=T0 + HOUR * 3)
    assert store.query(BINANCE, USDM_PERPETUAL, PAIR, TIMEFRAME, T0, T0 + HOUR * 3) == []
    assert store.query_dataset(*INDEX.namespace) is None
    assert store.query_coverage(*INDEX.namespace, T0, T0 + HOUR * 3) == []


def test_index_mid_pagination_api_error_rolls_back_everything(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "index.db")
    inner = _index_fake([_index_row(hour) for hour in range(10)], limit=5)

    def failing(*, start_time_ms, end_time_ms):
        if inner.calls:
            raise BinanceApiError("Binance USDⓈ-M API HTTP 400: boom")
        return inner(start_time_ms=start_time_ms, end_time_ms=end_time_ms)

    with pytest.raises(BinanceApiError):
        _ingest_index(store, failing, end=T0 + HOUR * 10)
    assert store.query(BINANCE, USDM_PERPETUAL, PAIR, TIMEFRAME, T0, T0 + HOUR * 10) == []
    assert store.query_dataset(*INDEX.namespace) is None
    assert store.query_coverage(*INDEX.namespace, T0, T0 + HOUR * 10) == []


def test_contract_and_index_can_never_share_one_store(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "shared.db")
    _ingest_contract(store, _contract_fake([_contract_row(h) for h in range(3)]),
                     end=T0 + HOUR * 3)  # fmt: skip
    with pytest.raises(DataConflictError, match="price_kind='contract_trade'"):
        _ingest_index(store, _index_fake([_index_row(h, close="99") for h in range(3)]),
                      end=T0 + HOUR * 3)  # fmt: skip
    assert store.query_dataset(*CONTRACT.namespace) == CONTRACT
    rows = store.query(BINANCE, USDM_PERPETUAL, PAIR, TIMEFRAME, T0, T0 + HOUR * 3)
    assert [row.candle.close for row in rows] == [Decimal(100)] * 3
    assert store.query_coverage(*CONTRACT.namespace, T0, T0 + HOUR * 3) == [
        CandleCoverageInterval(start_time=T0, end_time=T0 + HOUR * 3)
    ]


def test_index_reingestion_is_idempotent_and_changed_value_conflicts(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "index.db")
    rows = [_index_row(h, close="101") for h in range(3)]
    _ingest_index(store, _index_fake(rows), end=T0 + HOUR * 3)
    _ingest_index(store, _index_fake(rows), end=T0 + HOUR * 3)
    assert len(store.query(BINANCE, USDM_PERPETUAL, PAIR, TIMEFRAME, T0, T0 + HOUR * 3)) == 3
    changed = [_index_row(h, close="102") for h in range(3)]
    with pytest.raises(DataConflictError):
        _ingest_index(store, _index_fake(changed), end=T0 + HOUR * 3)


def test_legacy_rows_in_index_namespace_are_never_relabeled(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "legacy.db")
    store.write_batch([_record(INDEX, 0, "100")])
    with pytest.raises(DataConflictError, match="unknown provenance"):
        _ingest_index(store, _index_fake([_index_row(0)]), end=T0 + HOUR)
    assert store.query_dataset(*INDEX.namespace) is None


def test_coverage_contains_is_exact():
    intervals = [
        CandleCoverageInterval(start_time=T0 + HOUR * 2, end_time=T0 + HOUR * 4),
        CandleCoverageInterval(start_time=T0, end_time=T0 + HOUR * 2),
    ]
    assert coverage_contains(intervals, T0, T0 + HOUR * 4)
    assert not coverage_contains(intervals, T0, T0 + HOUR * 4 + US)
    assert not coverage_contains(intervals[:1], T0, T0 + HOUR * 4)
    assert not coverage_contains([], T0, T0 + HOUR)


# ================================================================
# close basis computation
# ================================================================


@pytest.mark.parametrize(
    ("contract", "index", "basis", "rate"),
    [
        ("100.5", "100", "0.5", "0.005"),  # premium
        ("99", "100", "-1", "-0.01"),  # discount
        ("100", "100", "0", "0"),  # zero basis
        ("84004.40", "84036.18086957", "-31.78086957", None),
    ],
)
def test_close_basis_premium_discount_zero_exact(contract, index, basis, rate):
    obs = compute_close_basis_observation(
        _record(CONTRACT, 0, contract), _record(INDEX, 0, index),
        contract_dataset=CONTRACT, index_dataset=INDEX,
    )  # fmt: skip
    assert obs.close_basis == Decimal(basis)
    assert str(obs.close_basis) == basis
    if rate is not None:
        assert obs.close_basis_rate == Decimal(rate)
    assert (obs.contract_close, obs.index_close) == (Decimal(contract), Decimal(index))
    assert (obs.exchange, obs.symbol, obs.timeframe) == ("binance", "BTCUSDT", "1h")
    assert (obs.open_time, obs.close_time, obs.available_at) == (T0, T0 + HOUR, T0 + HOUR)
    assert (obs.contract_dataset, obs.index_dataset) == (CONTRACT, INDEX)


def test_close_basis_rate_precision_is_fixed_and_context_independent():
    def compute(contract, index):
        return compute_close_basis_observation(
            _record(CONTRACT, 0, contract), _record(INDEX, 0, index),
            contract_dataset=CONTRACT, index_dataset=INDEX,
        ).close_basis_rate  # fmt: skip

    third = Decimal("0." + "3" * 34)
    minus_two_thirds = Decimal("-0." + "6" * 33 + "7")
    assert compute("4", "3") == third
    assert compute("1", "3") == minus_two_thirds
    with localcontext() as ctx:
        ctx.prec = 3
        ctx.rounding = ROUND_DOWN
        assert compute("4", "3") == third
        assert compute("1", "3") == minus_two_thirds
        assert compute("84004.40", "84036.18086957") == compute_close_basis_observation(
            _record(CONTRACT, 0, "84004.40"), _record(INDEX, 0, "84036.18086957"),
            contract_dataset=CONTRACT, index_dataset=INDEX,
        ).close_basis_rate  # fmt: skip
    # -3178086957 / 8403618086957 via fractions.Fraction: floor(|q| * 10**37) =
    # 3781807935718321243747864266012206, remainder 0.53 -> half-even rounds up.
    assert compute("84004.40", "84036.18086957") == Decimal(
        "-0.0003781807935718321243747864266012207"
    )


@pytest.mark.parametrize("index_close", ["0", "-1"])
def test_zero_or_negative_index_close_is_rejected(index_close):
    with pytest.raises(ValueError, match="index close must be a finite price > 0"):
        compute_close_basis_observation(
            _record(CONTRACT, 0, "100"), _record(INDEX, 0, index_close),
            contract_dataset=CONTRACT, index_dataset=INDEX,
        )  # fmt: skip


def test_mismatched_symbol_timeframe_and_price_kind_are_rejected():
    eth_index = binance_usdm_index_price_dataset("ETHUSDT", TIMEFRAME)
    index_4h = binance_usdm_index_price_dataset(PAIR, "4h")
    mark = CandleDataset(BINANCE, USDM_PERPETUAL, PAIR, TIMEFRAME, MARK_PRICE, "binance:mark")
    for index_dataset, message in (
        (eth_index, "does not match index namespace"),
        (index_4h, "does not match index namespace"),
        (mark, "index_dataset price_kind"),
        (CONTRACT, "index_dataset price_kind"),
    ):
        with pytest.raises(ValueError, match=message):
            compute_close_basis_observation(
                _record(CONTRACT, 0, "100"), _record(INDEX, 0, "100"),
                contract_dataset=CONTRACT, index_dataset=index_dataset,
            )  # fmt: skip
    with pytest.raises(ValueError, match="contract_dataset price_kind"):
        compute_close_basis_observation(
            _record(CONTRACT, 0, "100"), _record(INDEX, 0, "100"),
            contract_dataset=INDEX, index_dataset=INDEX,
        )  # fmt: skip
    eth_record = _record(binance_usdm_index_price_dataset("ETHUSDT", TIMEFRAME), 0, "100")
    with pytest.raises(ValueError, match="does not match"):
        compute_close_basis_observation(
            _record(CONTRACT, 0, "100"), eth_record, contract_dataset=CONTRACT, index_dataset=INDEX
        )


def test_misaligned_intervals_are_rejected():
    with pytest.raises(ValueError, match="misaligned intervals"):
        compute_close_basis_observation(
            _record(CONTRACT, 0, "100"), _record(INDEX, 1, "100"),
            contract_dataset=CONTRACT, index_dataset=INDEX,
        )  # fmt: skip


def test_forged_observation_is_rejected():
    obs = compute_close_basis_observation(
        _record(CONTRACT, 0, "101"), _record(INDEX, 0, "100"),
        contract_dataset=CONTRACT, index_dataset=INDEX,
    )  # fmt: skip
    fields = {name: getattr(obs, name) for name in CloseBasisObservation.__slots__}
    with pytest.raises(ValueError, match="do not match the close prices"):
        CloseBasisObservation(**{**fields, "close_basis": Decimal(2)})
    with pytest.raises(ValueError, match="available_at cannot precede"):
        CloseBasisObservation(**{**fields, "available_at": T0 + HOUR - US})
    with pytest.raises(ValueError, match="close_time must equal"):
        CloseBasisObservation(**{**fields, "close_time": T0})


def test_pairing_reports_gaps_and_never_fills():
    contract = [_record(CONTRACT, h, str(100 + h)) for h in (0, 1, 3, 4)]
    index = [_record(INDEX, h, "100") for h in (0, 1, 2, 3)]
    pairing = pair_close_basis(
        contract, index, contract_dataset=CONTRACT, index_dataset=INDEX,
        start_time=T0, end_time=T0 + HOUR * 6,
    )  # fmt: skip
    assert [obs.open_time for obs in pairing.observations] == [T0, T0 + HOUR, T0 + HOUR * 3]
    assert [obs.close_basis for obs in pairing.observations] == [
        Decimal(0), Decimal(1), Decimal(3)
    ]  # fmt: skip
    assert pairing.contract_only_open_times == (T0 + HOUR * 4,)
    assert pairing.index_only_open_times == (T0 + HOUR * 2,)
    assert pairing.both_missing_open_times == (T0 + HOUR * 5,)
    reversed_pairing = pair_close_basis(
        list(reversed(contract)), list(reversed(index)), contract_dataset=CONTRACT,
        index_dataset=INDEX, start_time=T0, end_time=T0 + HOUR * 6,
    )  # fmt: skip
    assert reversed_pairing == pairing


def test_pairing_rejects_duplicates_out_of_range_and_misaligned_range():
    kwargs = {"contract_dataset": CONTRACT, "index_dataset": INDEX}
    with pytest.raises(ValueError, match="duplicate contract candle"):
        pair_close_basis([_record(CONTRACT, 0, "1")] * 2, [], start_time=T0,
                         end_time=T0 + HOUR, **kwargs)  # fmt: skip
    with pytest.raises(ValueError, match="outside the range"):
        pair_close_basis([], [_record(INDEX, 1, "1")], start_time=T0, end_time=T0 + HOUR,
                         **kwargs)  # fmt: skip
    with pytest.raises(ValueError, match="aligned"):
        pair_close_basis([], [], start_time=T0 + US, end_time=T0 + HOUR, **kwargs)


# ================================================================
# temporal safety (store-backed history)
# ================================================================


def _stores(tmp_path, *, contract_hours=range(6), index_hours=range(6), end_hour=6):
    tmp_path.mkdir(parents=True, exist_ok=True)
    contract_store = SQLiteHistoricalCandleStore(tmp_path / "contract.db")
    index_store = SQLiteHistoricalCandleStore(tmp_path / "index.db")
    _ingest_contract(contract_store,
                     _contract_fake([_contract_row(h, close=str(100 + h)) for h in contract_hours]),
                     end=T0 + HOUR * end_hour)  # fmt: skip
    _ingest_index(index_store, _index_fake([_index_row(h) for h in index_hours]),
                  end=T0 + HOUR * end_hour)  # fmt: skip
    return contract_store, index_store


def _history(contract_store, index_store, end_hour=6):
    return load_close_basis_history(
        contract_store, index_store, symbol=PAIR, timeframe=TIMEFRAME,
        start_time=T0, end_time=T0 + HOUR * end_hour,
    )  # fmt: skip


def test_observation_is_invisible_before_close_and_visible_exactly_at_it(tmp_path):
    history = _history(*_stores(tmp_path))
    assert history.visible_at(T0) == ()  # interval start is not a publication time
    assert history.visible_at(T0 + HOUR - US) == ()
    at_close = history.visible_at(T0 + HOUR)
    assert [obs.open_time for obs in at_close] == [T0]
    assert at_close[0].close_basis == Decimal(0)
    latest = history.latest_at(T0 + HOUR * 3 + timedelta(minutes=59))
    assert (latest.open_time, latest.close_basis) == (T0 + HOUR * 2, Decimal(2))
    assert len(history.visible_at(T0 + HOUR * 3 + timedelta(minutes=59))) == 3
    assert history.latest_at(T0 + HOUR * 6).open_time == T0 + HOUR * 5


def test_as_of_is_compared_as_a_utc_instant(tmp_path):
    history = _history(*_stores(tmp_path))
    istanbul = T0.astimezone(timezone(timedelta(hours=3)))
    assert history.visible_at(istanbul + HOUR * 2) == history.visible_at(T0 + HOUR * 2)
    assert len(history.visible_at(istanbul + HOUR * 2)) == 2


def test_as_of_outside_coverage_is_an_error_not_an_empty_answer(tmp_path):
    history = _history(*_stores(tmp_path))
    with pytest.raises(ValueError, match="outside coverage"):
        history.latest_at(T0 + HOUR * 6 + US)
    with pytest.raises(ValueError, match="outside coverage"):
        history.visible_at(T0 - US)


def test_missing_candle_on_either_side_never_becomes_visible(tmp_path):
    history = _history(*_stores(tmp_path, contract_hours=(0, 2, 3, 4, 5),
                                index_hours=(0, 1, 2, 4, 5)))  # fmt: skip
    assert [obs.open_time for obs in history.visible_at(T0 + HOUR * 6)] == [
        T0, T0 + HOUR * 2, T0 + HOUR * 4, T0 + HOUR * 5
    ]  # fmt: skip
    assert history.contract_only_open_times == (T0 + HOUR * 3,)
    assert history.index_only_open_times == (T0 + HOUR,)
    assert history.both_missing_open_times == ()
    assert history.latest_at(T0 + HOUR * 2).open_time == T0  # no fill from hour 1


def test_later_data_does_not_change_earlier_visibility(tmp_path):
    short = _history(*_stores(tmp_path / "a", end_hour=3), end_hour=3)
    long = _history(*_stores(tmp_path / "b", end_hour=6), end_hour=6)
    for as_of in (T0, T0 + HOUR, T0 + HOUR * 2 + US, T0 + HOUR * 3):
        assert short.visible_at(as_of) == long.visible_at(as_of)


def test_history_is_immutable_and_rejects_duplicates_and_mixed_provenance():
    obs = compute_close_basis_observation(
        _record(CONTRACT, 0, "101"), _record(INDEX, 0, "100"),
        contract_dataset=CONTRACT, index_dataset=INDEX,
    )  # fmt: skip
    pairing = basis_module.CloseBasisPairing((obs, obs), (), (), ())
    with pytest.raises(ValueError, match="duplicate observation"):
        CloseBasisHistory(coverage_start=T0, coverage_end=T0 + HOUR, pairing=pairing)
    other_contract = CandleDataset(BINANCE, USDM_PERPETUAL, PAIR, TIMEFRAME, "contract_trade",
                                   "binance:other")  # fmt: skip
    obs2 = compute_close_basis_observation(
        _record(CONTRACT, 1, "101"), _record(INDEX, 1, "100"),
        contract_dataset=other_contract, index_dataset=INDEX,
    )  # fmt: skip
    mixed = basis_module.CloseBasisPairing((obs, obs2), (), (), ())
    with pytest.raises(ValueError, match="different provenance"):
        CloseBasisHistory(coverage_start=T0, coverage_end=T0 + HOUR * 2, pairing=mixed)
    history = CloseBasisHistory(
        coverage_start=T0, coverage_end=T0 + HOUR,
        pairing=basis_module.CloseBasisPairing((obs,), (), (), ()),
    )  # fmt: skip
    with pytest.raises(AttributeError):
        history._observations = ()
    with pytest.raises(ValueError, match="outside coverage"):
        CloseBasisHistory(coverage_start=T0 + HOUR, coverage_end=T0 + HOUR * 2,
                          pairing=basis_module.CloseBasisPairing((obs,), (), (), ()))  # fmt: skip


def test_loader_enforces_separate_registered_and_covered_stores(tmp_path):
    contract_store, index_store = _stores(tmp_path)
    with pytest.raises(ValueError, match="separate stores"):
        _history(contract_store, contract_store)
    with pytest.raises(ValueError, match="index_store namespace .* is registered as"):
        _history(contract_store, SQLiteHistoricalCandleStore(tmp_path / "empty.db"))
    with pytest.raises(ValueError, match="contract_store namespace .* is registered as"):
        _history(index_store, contract_store)
    with pytest.raises(ValueError, match="coverage does not contain"):
        _history(contract_store, index_store, end_hour=7)


def test_basis_layer_has_no_trading_or_accounting_dependency():
    source = basis_module.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "crypto_quant_lab.backtest" not in text
    assert "PositionTarget" not in text


# ================================================================
# official basis adapter and comparison
# ================================================================

OFFICIAL_ROW = {
    "indexPrice": "84036.18086957",
    "contractType": "PERPETUAL",
    "basisRate": "-0.0004",
    "futuresPrice": "84004.40",
    "annualizedBasisRate": "",
    "basis": "-31.78086957",
    "pair": "BTCUSDT",
    "timestamp": 1790182800000,
}


def _official(**changes):
    return {**OFFICIAL_ROW, **changes}


def _parse(raw):
    return parse_binance_official_basis_record(raw, pair=PAIR, contract_type="PERPETUAL",
                                               period="1h")  # fmt: skip


def test_official_record_parses_verbatim():
    record = _parse(OFFICIAL_ROW)
    assert record == BinanceOfficialBasisRecord(
        pair="BTCUSDT",
        contract_type="PERPETUAL",
        period="1h",
        timestamp=datetime(2026, 9, 23, 17, tzinfo=UTC),
        futures_price=Decimal("84004.40"),
        index_price=Decimal("84036.18086957"),
        basis=Decimal("-31.78086957"),
        basis_rate=Decimal("-0.0004"),
        annualized_basis_rate=None,
    )
    assert _parse(_official(annualizedBasisRate="0.12")).annualized_basis_rate == Decimal("0.12")


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({k: v for k, v in OFFICIAL_ROW.items() if k != "basis"}, "keys"),
        (_official(extra="1"), "keys"),
        (_official(pair="ETHUSDT"), "does not match"),
        (_official(contractType="CURRENT_QUARTER"), "does not match"),
        (_official(timestamp="1790182800000"), "invalid timestamp"),
        (_official(timestamp=1790182800001), "not aligned"),
        (_official(indexPrice="0"), "prices must be > 0"),
        (_official(basis="abc"), "invalid basis"),
        (_official(basisRate=-0.0004), "JSON string"),
        (_official(futuresPrice="Infinity"), "finite"),
        ([1, 2], "JSON object"),
    ],
)
def test_malformed_official_records_are_rejected(raw, message):
    with pytest.raises(ValueError, match=message):
        _parse(raw)


def test_official_url_and_parameter_contract():
    url = build_official_basis_url(PAIR, "PERPETUAL", "1h", start_time_ms=1, end_time_ms=2,
                                   limit=500)  # fmt: skip
    assert url == (
        f"{USDM_BASE_URL}{OFFICIAL_BASIS_PATH}?pair=BTCUSDT&contractType=PERPETUAL&period=1h"
        "&startTime=1&endTime=2&limit=500"
    )
    with pytest.raises(ValueError, match="contract_type"):
        build_official_basis_url(PAIR, "perpetual", "1h", start_time_ms=1, end_time_ms=2, limit=1)
    for period in ("1m", "3d", "8h"):
        with pytest.raises(ValueError, match="period"):
            build_official_basis_url(PAIR, "PERPETUAL", period, start_time_ms=1, end_time_ms=2,
                                     limit=1)  # fmt: skip
    with pytest.raises(ValueError, match="limit"):
        build_official_basis_url(PAIR, "PERPETUAL", "1h", start_time_ms=1, end_time_ms=2,
                                 limit=501)  # fmt: skip


BASIS_T = datetime(2026, 9, 23, 17, tzinfo=UTC)
BASIS_AS_OF = datetime(2026, 9, 24, tzinfo=UTC)


def _fetch_official(rows, *, start=BASIS_T, end=BASIS_T + HOUR * 2, as_of=BASIS_AS_OF, **kw):
    seen = []

    def fetch_rows(url, limit, timeout):
        seen.append((url, limit))
        return rows

    records = fetch_binance_official_basis(PAIR, "PERPETUAL", "1h", start_time=start,
                                           end_time=end, as_of_time=as_of,
                                           fetch_rows=fetch_rows, **kw)  # fmt: skip
    return records, seen


def _row_at(hours):
    return _official(timestamp=1790182800000 + hours * HOUR_MS)


def test_official_fetch_is_half_open_and_ordered():
    records, seen = _fetch_official([_row_at(0), _row_at(1), _row_at(2)])
    assert [r.timestamp for r in records] == [BASIS_T, BASIS_T + HOUR]  # inclusive end dropped
    assert "startTime=1790182800000&endTime=1790190000000" in seen[0][0]
    with pytest.raises(ValueError, match="unordered or duplicated"):
        _fetch_official([_row_at(1), _row_at(0)])
    with pytest.raises(ValueError, match="unordered or duplicated"):
        _fetch_official([_row_at(0), _row_at(0)])
    with pytest.raises(ValueError, match="outside the requested range"):
        _fetch_official([_row_at(3)])


def test_official_fetch_enforces_the_30_day_window_and_clock():
    thirty_days = timedelta(days=30)
    records, _ = _fetch_official([], start=BASIS_AS_OF - thirty_days,
                                 end=BASIS_AS_OF - thirty_days + HOUR)  # fmt: skip
    assert records == ()
    with pytest.raises(ValueError, match="older than the documented 30-day"):
        _fetch_official([], start=BASIS_AS_OF - thirty_days - HOUR,
                        end=BASIS_AS_OF - thirty_days)  # fmt: skip
    with pytest.raises(ValueError, match="after as_of_time"):
        _fetch_official([], end=BASIS_AS_OF + HOUR)
    with pytest.raises(ValueError, match="aligned"):
        _fetch_official([], start=BASIS_T + US)
    with pytest.raises(ValueError, match="more than limit"):
        _fetch_official([], start=BASIS_AS_OF - HOUR * 3, end=BASIS_AS_OF, limit=2)


def test_official_fetch_api_error_is_fail_closed(monkeypatch):
    _mock_http(monkeypatch, {"code": -1102, "msg": "Mandatory parameter 'pair' was not sent"})
    with pytest.raises(BinanceApiError, match="-1102"):
        fetch_binance_official_basis(PAIR, "PERPETUAL", "1h", start_time=BASIS_T,
                                     end_time=BASIS_T + HOUR, as_of_time=BASIS_AS_OF)  # fmt: skip


def test_official_record_algebraic_consistency():
    ok = check_official_basis_consistency(_parse(OFFICIAL_ROW))
    assert ok.basis_residual == 0
    # -0.0004 - (-31.78086957 / 84036.18086957) = -0.0000218...
    assert Decimal("-0.0000219") < ok.basis_rate_residual < Decimal("-0.0000218")
    assert ok.is_consistent
    assert OFFICIAL_BASIS_RATE_DISPLAY_TOLERANCE == Decimal("0.00005")
    bad_basis = check_official_basis_consistency(_parse(_official(basis="-31.78086958")))
    assert bad_basis.basis_residual == Decimal("-0.00000001")
    assert not bad_basis.is_consistent
    assert not check_official_basis_consistency(
        _parse(_official(basisRate="-0.0005"))
    ).is_consistent


def test_official_rate_tolerance_boundary_is_inclusive():
    boundary = _official(
        indexPrice="10000", futuresPrice="10000.5", basis="0.5", basisRate="0.0001"
    )  # implied 0.00005, residual exactly 0.00005
    assert check_official_basis_consistency(_parse(boundary)).is_consistent
    beyond = {**boundary, "basisRate": "0.00010001"}
    assert not check_official_basis_consistency(_parse(beyond)).is_consistent


def _official_record(hour, *, futures, index, basis):
    return BinanceOfficialBasisRecord(
        pair=PAIR, contract_type="PERPETUAL", period="1h", timestamp=T0 + HOUR * hour,
        futures_price=Decimal(futures), index_price=Decimal(index), basis=Decimal(basis),
        basis_rate=Decimal(0), annualized_basis_rate=None,
    )  # fmt: skip


def test_comparison_matches_close_time_to_snapshot_timestamp_and_measures_differences():
    obs = [
        compute_close_basis_observation(
            _record(CONTRACT, h, close),
            _record(INDEX, h, "100"),
            contract_dataset=CONTRACT,
            index_dataset=INDEX,
        )
        for h, close in ((0, "100.5"), (1, "99"), (2, "100"))
    ]
    records = [
        _official_record(0, futures="100", index="100", basis="0"),  # no obs closes at T0
        _official_record(1, futures="100.6", index="100", basis="0.6"),  # vs 0.5 / 0.005
        _official_record(2, futures="99", index="100", basis="-1"),  # vs -1 / -0.01
    ]
    result = compare_with_official_basis(obs, records, rate_tolerance=Decimal("0.0005"))
    assert result.comparable_count == 2
    assert result.official_only_timestamps == (T0,)
    assert result.observation_only_close_times == (T0 + HOUR * 3,)
    assert result.max_abs_basis_difference == Decimal("0.1")
    assert result.mean_abs_basis_difference == Decimal("0.05")
    assert result.max_abs_rate_difference == Decimal("0.001")
    assert result.mean_abs_rate_difference == Decimal("0.0005")
    assert result.exceeding_timestamps == (T0 + HOUR,)
    assert result.rate_tolerance == Decimal("0.0005")


def test_comparison_rejects_mismatches_and_bad_tolerance():
    obs = [compute_close_basis_observation(
        _record(CONTRACT, 0, "100"), _record(INDEX, 0, "100"),
        contract_dataset=CONTRACT, index_dataset=INDEX)]  # fmt: skip
    eth = BinanceOfficialBasisRecord(
        pair="ETHUSDT", contract_type="PERPETUAL", period="1h", timestamp=T0 + HOUR,
        futures_price=Decimal(1), index_price=Decimal(1), basis=Decimal(0),
        basis_rate=Decimal(0), annualized_basis_rate=None,
    )  # fmt: skip
    with pytest.raises(ValueError, match="mismatched pair/period"):
        compare_with_official_basis(obs, [eth], rate_tolerance=Decimal("0.0001"))
    rec = _official_record(1, futures="100", index="100", basis="0")
    with pytest.raises(ValueError, match="duplicate official record"):
        compare_with_official_basis(obs, [rec, rec], rate_tolerance=Decimal("0.0001"))
    with pytest.raises(ValueError, match="rate_tolerance must be >= 0"):
        compare_with_official_basis(obs, [rec], rate_tolerance=Decimal("-0.1"))
    with pytest.raises(TypeError):
        compare_with_official_basis(obs, [rec], rate_tolerance=0.0001)
