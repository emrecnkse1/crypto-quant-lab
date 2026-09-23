"""Faz 7 regression audit: edge behaviors of the funding, ingestion, provenance and basis slices.

Each test closes a behavior not exercised by the slice test files
(test_research_funding_carry.py, test_usdm_perpetual_klines.py,
test_usdm_index_basis.py). No network. Expected values are hand-derived.
"""

import copy
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, Inexact, Rounded, localcontext

import pytest

from crypto_quant_lab.data_quality.usdm_ingestion import (
    ingest_binance_usdm_index_price_klines,
    ingest_binance_usdm_perpetual_klines,
)
from crypto_quant_lab.funding.models import FundingEvent
from crypto_quant_lab.market_data.binance_usdm import (
    parse_binance_usdm_index_price_kline,
    parse_binance_usdm_kline,
)
from crypto_quant_lab.market_data.binance_usdm_basis import (
    BinanceOfficialBasisRecord,
    check_official_basis_consistency,
    fetch_binance_official_basis,
    parse_binance_official_basis_record,
)
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.research.basis import (
    compare_with_official_basis,
    compute_close_basis_observation,
    load_close_basis_history,
    pair_close_basis,
)
from crypto_quant_lab.research.funding_carry import FundingSignalHistory
from crypto_quant_lab.storage.base import HistoricalCandle
from crypto_quant_lab.storage.datasets import (
    CandleCoverageInterval,
    binance_usdm_index_price_dataset,
    binance_usdm_perpetual_contract_trade_dataset,
    coverage_contains,
)
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore

PAIR = "BTCUSDT"
TF = "1h"
T0 = datetime(2026, 1, 1, tzinfo=UTC)
T0_MS = 1767225600000
HOUR = timedelta(hours=1)
HOUR_MS = 3_600_000
US = timedelta(microseconds=1)
AS_OF = T0 + HOUR * 48
PLUS3 = timezone(timedelta(hours=3))
NAIVE_T0 = datetime(2026, 1, 1)  # noqa: DTZ001 - deliberately naive
CONTRACT = binance_usdm_perpetual_contract_trade_dataset(PAIR, TF)
INDEX = binance_usdm_index_price_dataset(PAIR, TF)


def _contract_row(hour, close="100"):
    open_ms = T0_MS + hour * HOUR_MS
    high = max("100", close, key=Decimal)
    low = min("100", close, key=Decimal)
    return [open_ms, "100", high, low, close, "1", open_ms + HOUR_MS - 1, "100.0", 7, "0.5",
            "50.0", "0"]  # fmt: skip


def _index_row(hour, close="100"):
    open_ms = T0_MS + hour * HOUR_MS
    high = max("100", close, key=Decimal)
    low = min("100", close, key=Decimal)
    return [open_ms, "100", high, low, close, "0", open_ms + HOUR_MS - 1, "0", 3600, "0", "0",
            "0"]  # fmt: skip


def _fake(rows, parse):
    calls = []

    def fetch(*, start_time_ms, end_time_ms):
        calls.append((start_time_ms, end_time_ms))
        return [parse(r, PAIR, TF) for r in rows if start_time_ms <= r[0] <= end_time_ms]

    fetch.calls = calls
    return fetch


def _ingest_contract(store, hours, *, start=T0, end=None):
    end = end or T0 + HOUR * (max(hours) + 1)
    return ingest_binance_usdm_perpetual_klines(
        store, symbol=PAIR, timeframe=TF, requested_start=start, requested_end=end,
        as_of_time=AS_OF, fetch_page=_fake([_contract_row(h) for h in hours],
                                           parse_binance_usdm_kline),
    )  # fmt: skip


def _ingest_index(store, hours, *, start=T0, end=None, fetch=None, **kwargs):
    end = end or T0 + HOUR * (max(hours) + 1)
    fetch = fetch or _fake([_index_row(h) for h in hours], parse_binance_usdm_index_price_kline)
    return ingest_binance_usdm_index_price_klines(
        store, pair=PAIR, timeframe=TF, requested_start=start, requested_end=end,
        as_of_time=AS_OF, fetch_page=fetch, **kwargs,
    )  # fmt: skip


def _record(dataset, hour, close):
    price = Decimal(close)
    return HistoricalCandle(
        exchange=dataset.exchange,
        market_type=dataset.market_type,
        candle=Candle(symbol=PAIR, timeframe=TF, open_time=T0 + HOUR * hour, open=price,
                      high=price, low=price, close=price, volume=Decimal(0)),
    )  # fmt: skip


def _funding_history(lag=timedelta(seconds=60)):
    events = tuple(
        FundingEvent(
            event_time=T0 + HOUR * 8 * k,
            funding_rate=Decimal("0.0001") * (k + 1),
            reference_price=Decimal(100),
            rate_type="Regular",
        )
        for k in range(3)
    )
    return FundingSignalHistory(
        exchange="binance", market_type="usdm_perpetual", symbol=PAIR, coverage_start=T0,
        coverage_end=T0 + HOUR * 24, publication_lag=lag, events=events,
    )  # fmt: skip


# ---------------------------------------------------------------- time semantics


def test_naive_datetimes_are_rejected_before_any_side_effect(tmp_path):
    with pytest.raises(ValueError, match="timezone-aware"):
        _funding_history().latest_settled_at(NAIVE_T0 + HOUR)
    store = SQLiteHistoricalCandleStore(tmp_path / "index.db")
    with pytest.raises(ValueError, match="timezone-aware"):
        _ingest_index(store, [0, 1], start=NAIVE_T0, end=T0 + HOUR * 2)
    assert store.query_dataset(*INDEX.namespace) is None
    with pytest.raises(ValueError, match="timezone-aware"):
        pair_close_basis([], [], contract_dataset=CONTRACT, index_dataset=INDEX,
                         start_time=NAIVE_T0, end_time=T0 + HOUR)  # fmt: skip
    with pytest.raises(ValueError, match="timezone-aware"):
        fetch_binance_official_basis(PAIR, "PERPETUAL", "1h", start_time=NAIVE_T0,
                                     end_time=T0 + HOUR, as_of_time=AS_OF,
                                     fetch_rows=lambda *a: [])  # fmt: skip


def test_equivalent_timezone_instants_give_identical_funding_answers():
    history = _funding_history()
    boundary_utc = T0 + HOUR * 8 + timedelta(seconds=60)  # event 1 time + lag
    boundary_plus3 = boundary_utc.astimezone(PLUS3)
    assert history.latest_settled_at(boundary_plus3) == history.latest_settled_at(boundary_utc)
    assert history.latest_settled_at(boundary_plus3).funding_rate == Decimal("0.0002")
    assert history.latest_settled_at((boundary_utc - US).astimezone(PLUS3)).funding_rate == (
        Decimal("0.0001")
    )


# ---------------------------------------------------------------- ingestion failures


def test_index_connection_errors_are_retried_a_bounded_number_of_times(tmp_path):
    calls = []

    def always_down(*, start_time_ms, end_time_ms):
        calls.append(start_time_ms)
        raise ConnectionError("down")

    store = SQLiteHistoricalCandleStore(tmp_path / "index.db")
    with pytest.raises(ConnectionError):
        _ingest_index(store, [0, 1], fetch=always_down, max_attempts=2)
    assert len(calls) == 2
    assert store.query_dataset(*INDEX.namespace) is None
    assert store.query_coverage(*INDEX.namespace, T0, T0 + HOUR * 2) == []

    inner = _fake([_index_row(h) for h in range(2)], parse_binance_usdm_index_price_kline)
    flaky_calls = []

    def flaky(*, start_time_ms, end_time_ms):
        flaky_calls.append(start_time_ms)
        if len(flaky_calls) == 1:
            raise ConnectionError("blip")
        return inner(start_time_ms=start_time_ms, end_time_ms=end_time_ms)

    result = _ingest_index(store, [0, 1], fetch=flaky, max_attempts=2)
    assert (result.candle_count, len(flaky_calls)) == (2, 2)


def test_failed_second_ingestion_keeps_earlier_coverage_and_adds_none(tmp_path):
    contract = SQLiteHistoricalCandleStore(tmp_path / "contract.db")
    index = SQLiteHistoricalCandleStore(tmp_path / "index.db")
    _ingest_contract(contract, range(6))
    _ingest_index(index, range(3))

    def broken(*, start_time_ms, end_time_ms):
        return [parse_binance_usdm_index_price_kline(_index_row(4), PAIR, TF),
                parse_binance_usdm_index_price_kline(_index_row(3), PAIR, TF)]  # fmt: skip

    with pytest.raises(ValueError):
        _ingest_index(index, [3, 4, 5], start=T0 + HOUR * 3, end=T0 + HOUR * 6, fetch=broken)
    assert index.query_coverage(*INDEX.namespace, T0, T0 + HOUR * 6) == [
        CandleCoverageInterval(start_time=T0, end_time=T0 + HOUR * 3)
    ]
    with pytest.raises(ValueError, match="index_store coverage does not contain"):
        load_close_basis_history(contract, index, symbol=PAIR, timeframe=TF, start_time=T0,
                                 end_time=T0 + HOUR * 6)  # fmt: skip
    history = load_close_basis_history(contract, index, symbol=PAIR, timeframe=TF,
                                       start_time=T0, end_time=T0 + HOUR * 3)  # fmt: skip
    assert len(history.visible_at(T0 + HOUR * 3)) == 3


def test_adjacent_ingestions_form_one_covered_range(tmp_path):
    contract = SQLiteHistoricalCandleStore(tmp_path / "contract.db")
    index = SQLiteHistoricalCandleStore(tmp_path / "index.db")
    _ingest_contract(contract, range(6))
    _ingest_index(index, [3, 4, 5], start=T0 + HOUR * 3, end=T0 + HOUR * 6)  # out of order
    _ingest_index(index, [0, 1, 2], start=T0, end=T0 + HOUR * 3)
    history = load_close_basis_history(contract, index, symbol=PAIR, timeframe=TF, start_time=T0,
                                       end_time=T0 + HOUR * 6)  # fmt: skip
    assert [o.open_time for o in history.visible_at(T0 + HOUR * 6)] == [
        T0 + HOUR * h for h in range(6)
    ]


# ---------------------------------------------------------------- store identity


def test_same_physical_database_through_another_path_and_connection_is_rejected(tmp_path):
    path = tmp_path / "shared.db"
    contract = SQLiteHistoricalCandleStore(path)
    _ingest_contract(contract, range(3))
    (tmp_path / "sub").mkdir()
    alias = SQLiteHistoricalCandleStore(tmp_path / "sub" / ".." / "shared.db")
    with pytest.raises(ValueError, match="index_store namespace .* is registered as"):
        load_close_basis_history(contract, alias, symbol=PAIR, timeframe=TF, start_time=T0,
                                 end_time=T0 + HOUR * 3)  # fmt: skip
    with pytest.raises(Exception, match="price_kind='contract_trade'"):
        _ingest_index(alias, range(3))


# ---------------------------------------------------------------- coverage union


@pytest.mark.parametrize(
    ("intervals", "start", "end", "expected"),
    [
        ([(0, 2), (1, 3)], 0, 3, True),  # overlapping
        ([(0, 4), (1, 2)], 0, 4, True),  # nested
        ([(2, 4), (0, 2), (0, 2)], 0, 4, True),  # touching, unordered, duplicate
        ([(0, 2), (2, 4)], 1, 3, True),  # sub-range across the seam
        ([(0, 1), (2, 3)], 0, 3, False),  # gap
        ([(1, 3)], 0, 2, False),  # starts too late
        ([(0, 2)], 0, 3, False),  # ends too early
    ],
)
def test_coverage_union_edge_cases(intervals, start, end, expected):
    built = [
        CandleCoverageInterval(start_time=T0 + HOUR * a, end_time=T0 + HOUR * b)
        for a, b in intervals
    ]
    assert coverage_contains(built, T0 + HOUR * start, T0 + HOUR * end) is expected


def test_coverage_gap_of_one_microsecond_is_detected():
    built = [
        CandleCoverageInterval(start_time=T0, end_time=T0 + HOUR),
        CandleCoverageInterval(start_time=T0 + HOUR + US, end_time=T0 + HOUR * 2),
    ]
    assert not coverage_contains(built, T0, T0 + HOUR * 2)
    assert coverage_contains(built, T0 + HOUR + US, T0 + HOUR * 2)


# ---------------------------------------------------------------- numeric robustness


@pytest.mark.parametrize("bad", ["sNaN", "-Infinity", "-sNaN", "NaN"])
def test_non_finite_values_fail_with_value_error_in_every_parser(bad):
    contract = _contract_row(0)
    contract[4] = bad
    index = _index_row(0)
    index[4] = bad
    official = {"indexPrice": "100", "contractType": "PERPETUAL", "basisRate": bad,
                "futuresPrice": "100", "annualizedBasisRate": "", "basis": "0",
                "pair": PAIR, "timestamp": T0_MS}  # fmt: skip
    with pytest.raises(ValueError, match="finite"):
        parse_binance_usdm_kline(contract, PAIR, TF)
    with pytest.raises(ValueError, match="finite"):
        parse_binance_usdm_index_price_kline(index, PAIR, TF)
    with pytest.raises(ValueError, match="finite"):
        parse_binance_official_basis_record(official, pair=PAIR, contract_type="PERPETUAL",
                                            period="1h")  # fmt: skip


def test_ambient_decimal_precision_and_traps_do_not_leak_into_the_basis_layer():
    record = BinanceOfficialBasisRecord(
        pair=PAIR, contract_type="PERPETUAL", period="1h", timestamp=T0 + HOUR,
        futures_price=Decimal("84004.40"), index_price=Decimal("84036.18086957"),
        basis=Decimal("-31.78086957"), basis_rate=Decimal("-0.0004"), annualized_basis_rate=None,
    )  # fmt: skip

    def run():
        obs = compute_close_basis_observation(
            _record(CONTRACT, 0, "84004.40"), _record(INDEX, 0, "84036.18086957"),
            contract_dataset=CONTRACT, index_dataset=INDEX,
        )  # fmt: skip
        return (
            obs,
            check_official_basis_consistency(record),
            compare_with_official_basis([obs], [record], rate_tolerance=Decimal("0.0001")),
            parse_binance_usdm_index_price_kline(_index_row(0, "112397.84673913"), PAIR, TF),
        )

    baseline = run()
    with localcontext() as ctx:
        ctx.prec = 2
        ctx.traps[Inexact] = True
        ctx.traps[Rounded] = True
        assert run() == baseline
    assert baseline[1].is_consistent
    assert baseline[2].max_abs_basis_difference == Decimal(0)


def test_pairing_and_comparison_do_not_mutate_inputs_and_repeat_exactly():
    contract = [_record(CONTRACT, h, str(100 + h)) for h in (2, 0, 1)]
    index = [_record(INDEX, h, "100") for h in (1, 2, 0)]
    before = copy.deepcopy((contract, index))
    kwargs = {"contract_dataset": CONTRACT, "index_dataset": INDEX, "start_time": T0,
              "end_time": T0 + HOUR * 3}  # fmt: skip
    first = pair_close_basis(contract, index, **kwargs)
    second = pair_close_basis(contract, index, **kwargs)
    assert first == second
    assert (contract, index) == before
    records = [
        BinanceOfficialBasisRecord(PAIR, "PERPETUAL", "1h", T0 + HOUR * h, Decimal(101),
                                   Decimal(100), Decimal(1), Decimal("0.01"), None)
        for h in (2, 1)
    ]  # fmt: skip
    records_before = list(records)
    observations = list(first.observations)
    one = compare_with_official_basis(observations, records, rate_tolerance=Decimal(0))
    assert one == compare_with_official_basis(observations, records, rate_tolerance=Decimal(0))
    assert records == records_before
    assert list(first.observations) == observations
    # obs closing at 1h: basis 0 vs official 1 -> diff 1; at 2h: basis 1 vs 1 -> diff 0
    assert one.max_abs_basis_difference == Decimal(1)
    assert one.exceeding_timestamps == (T0 + HOUR,)
