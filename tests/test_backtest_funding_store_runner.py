from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_quant_lab.backtest.costs import ZeroCostModel
from crypto_quant_lab.backtest.dataset import prepare_backtest_dataset
from crypto_quant_lab.backtest.models import BacktestConfig, BacktestResult, PositionTarget
from crypto_quant_lab.backtest.replay import run_backtest_replay
from crypto_quant_lab.backtest.store_runner import run_backtest_from_store
from crypto_quant_lab.funding.calculator import LinearFundingModel
from crypto_quant_lab.funding.models import (
    FundingCoverageInterval,
    FundingEvent,
    HistoricalFundingEvent,
)
from crypto_quant_lab.funding.sqlite import SQLiteHistoricalFundingStore
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.base import DataCorruptionError, HistoricalCandle, StorageError
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore

EXCHANGE = "binance"
MARKET_TYPE = "usdm_perp"
SYMBOL = "BTCUSDT"


class _RecordingPolicy:
    def __init__(self, target_fn):
        self._target_fn = target_fn
        self.contexts = []

    def target_position(self, context):
        self.contexts.append(context)
        return self._target_fn(context)


class _RecordingCostModel:
    def __init__(self, cost=Decimal(0)):
        self._cost = cost
        self.calls = []

    def calculate_cost(self, *, quantity, execution_price):
        self.calls.append((quantity, execution_price))
        return self._cost


class _PoisonCandleStore:
    def query(self, *args, **kwargs):
        raise AssertionError("candle store must not be queried for invalid funding configuration")

    def write_batch(self, records):
        raise AssertionError("write_batch must never be called")

    def close(self):
        pass


class _PoisonFundingStore:
    def query_events(self, **kwargs):
        raise AssertionError("funding store must not be queried for invalid funding configuration")

    def query_coverage(self, **kwargs):
        raise AssertionError("funding store must not be queried for invalid funding configuration")

    def close(self):
        pass


class _FakeFundingStore:
    """Records calls; supports per-call-index scripted `query_events` outcomes.

    An outcome that is a `BaseException` instance is raised instead of
    returned — used to simulate `StorageError`/`DataCorruptionError`.
    """

    def __init__(self, *, coverage=(), events=(), events_by_call=None):
        self._coverage = coverage
        self._events = events
        self._events_by_call = events_by_call
        self.event_calls: list[dict] = []
        self.coverage_calls: list[dict] = []

    def query_events(self, *, exchange, market_type, symbol, start_time, end_time):
        index = len(self.event_calls)
        self.event_calls.append(
            {
                "exchange": exchange,
                "market_type": market_type,
                "symbol": symbol,
                "start_time": start_time,
                "end_time": end_time,
            }
        )
        outcome = self._events_by_call[index] if self._events_by_call is not None else self._events
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def query_coverage(self, *, exchange, market_type, symbol, start_time, end_time):
        self.coverage_calls.append(
            {
                "exchange": exchange,
                "market_type": market_type,
                "symbol": symbol,
                "start_time": start_time,
                "end_time": end_time,
            }
        )
        if isinstance(self._coverage, BaseException):
            raise self._coverage
        return self._coverage

    def close(self):
        pass


def _make_record(
    open_time,
    timeframe="1h",
    exchange=EXCHANGE,
    market_type=MARKET_TYPE,
    symbol=SYMBOL,
    price=Decimal(100),
) -> HistoricalCandle:
    candle = Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal(1),
    )
    return HistoricalCandle(exchange=exchange, market_type=market_type, candle=candle)


def _candle_store(tmp_path, hours=(10, 11, 12, 13)):
    store = SQLiteHistoricalCandleStore(tmp_path / "candles.db")
    store.write_batch([_make_record(datetime(2024, 1, 1, hour, 0, tzinfo=UTC)) for hour in hours])
    return store


def _config(initial_cash=Decimal(1000), position_quantity=Decimal(1)):
    return BacktestConfig(initial_cash=initial_cash, position_quantity=position_quantity)


def _flat_policy():
    return _RecordingPolicy(lambda context: PositionTarget.FLAT)


def _long_policy():
    return _RecordingPolicy(lambda context: PositionTarget.LONG)


def _funding_event(
    event_time,
    rate_type="Regular",
    *,
    exchange=EXCHANGE,
    market_type=MARKET_TYPE,
    symbol=SYMBOL,
    funding_rate="0.001",
    reference_price="100",
):
    return HistoricalFundingEvent(
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        funding=FundingEvent(
            event_time=event_time,
            funding_rate=Decimal(funding_rate),
            reference_price=Decimal(reference_price),
            rate_type=rate_type,
        ),
    )


def _coverage(start, end):
    return FundingCoverageInterval(start_time=start, end_time=end)


RUN_START = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
RUN_END = datetime(2024, 1, 1, 14, 0, tzinfo=UTC)


def _run(
    store, *, policy, funding_store=None, funding_model=None, funding_required=False, **kwargs
):
    return run_backtest_from_store(
        store,
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        timeframe="1h",
        requested_start=kwargs.pop("requested_start", RUN_START),
        requested_end=kwargs.pop("requested_end", RUN_END),
        as_of_time=kwargs.pop("as_of_time", RUN_END),
        config=kwargs.pop("config", None) or _config(),
        policy=policy,
        cost_model=kwargs.pop("cost_model", None) or ZeroCostModel(),
        funding_required=funding_required,
        funding_store=funding_store,
        funding_model=funding_model,
    )


# --- configuration matrix ---


def test_default_legacy_call_is_legal(tmp_path):
    store = _candle_store(tmp_path)

    result = run_backtest_from_store(
        store,
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        timeframe="1h",
        requested_start=RUN_START,
        requested_end=RUN_END,
        as_of_time=RUN_END,
        config=_config(),
        policy=_flat_policy(),
        cost_model=ZeroCostModel(),
    )

    assert isinstance(result, BacktestResult)


def test_explicit_disabled_matches_default(tmp_path):
    store = _candle_store(tmp_path)

    default = _run(store, policy=_flat_policy())
    explicit = _run(
        store, policy=_flat_policy(), funding_required=False, funding_store=None, funding_model=None
    )

    assert default == explicit


def test_required_true_missing_store_raises():
    with pytest.raises(ValueError, match="funding_store"):
        _run(
            _PoisonCandleStore(),
            policy=_flat_policy(),
            funding_required=True,
            funding_store=None,
            funding_model=LinearFundingModel(),
        )


def test_required_true_missing_model_raises():
    with pytest.raises(ValueError, match="funding_model"):
        _run(
            _PoisonCandleStore(),
            policy=_flat_policy(),
            funding_required=True,
            funding_store=_PoisonFundingStore(),
            funding_model=None,
        )


def test_required_false_with_store_raises():
    with pytest.raises(ValueError, match="funding_store"):
        _run(
            _PoisonCandleStore(),
            policy=_flat_policy(),
            funding_required=False,
            funding_store=_PoisonFundingStore(),
            funding_model=None,
        )


def test_required_false_with_model_raises():
    with pytest.raises(ValueError, match="funding_model"):
        _run(
            _PoisonCandleStore(),
            policy=_flat_policy(),
            funding_required=False,
            funding_store=None,
            funding_model=LinearFundingModel(),
        )


def test_required_false_with_both_raises():
    with pytest.raises(ValueError):
        _run(
            _PoisonCandleStore(),
            policy=_flat_policy(),
            funding_required=False,
            funding_store=_PoisonFundingStore(),
            funding_model=LinearFundingModel(),
        )


@pytest.mark.parametrize("bad_value", [0, 1, None, "true"])
def test_funding_required_type_is_rejected(bad_value):
    with pytest.raises(TypeError, match="funding_required"):
        _run(_PoisonCandleStore(), policy=_flat_policy(), funding_required=bad_value)


# --- legacy regression ---


def test_existing_store_runner_tests_still_pass_unchanged(tmp_path):
    # Sanity cross-check: legacy path produces the exact shape existing
    # tests already assert on (see tests/test_backtest_store_runner.py).
    store = _candle_store(tmp_path)

    result = _run(store, policy=_flat_policy())

    assert result.fill_count == 0
    assert result.total_cost == Decimal(0)
    assert len(result.equity_curve) == 4


# --- funding-enabled: coverage outcomes ---


def test_full_coverage_with_events_matches_direct_composition(tmp_path):
    store = _candle_store(tmp_path)
    event = _funding_event(datetime(2024, 1, 1, 11, 30, tzinfo=UTC))
    funding_store = _FakeFundingStore(coverage=[_coverage(RUN_START, RUN_END)], events=[event])

    result = _run(
        store,
        policy=_long_policy(),
        funding_required=True,
        funding_store=funding_store,
        funding_model=LinearFundingModel(),
    )

    candles = prepare_backtest_dataset(
        store,
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        timeframe="1h",
        requested_start=RUN_START,
        requested_end=RUN_END,
        as_of_time=RUN_END,
    )
    expected = run_backtest_replay(
        candles,
        as_of_time=RUN_END,
        config=_config(),
        policy=_long_policy(),
        cost_model=ZeroCostModel(),
        funding_events=(event,),
        funding_model=LinearFundingModel(),
    )

    assert result == expected
    assert funding_store.event_calls[-1] == {
        "exchange": EXCHANGE,
        "market_type": MARKET_TYPE,
        "symbol": SYMBOL,
        "start_time": RUN_START,
        "end_time": RUN_END,
    }


def test_full_coverage_zero_events_is_accepted(tmp_path):
    store = _candle_store(tmp_path)
    funding_store = _FakeFundingStore(coverage=[_coverage(RUN_START, RUN_END)], events=[])

    result = _run(
        store,
        policy=_flat_policy(),
        funding_required=True,
        funding_store=funding_store,
        funding_model=LinearFundingModel(),
    )

    assert isinstance(result, BacktestResult)
    assert result.total_cost == Decimal(0)
    assert len(funding_store.event_calls) == 2  # quality's own read + the second read


def test_no_coverage_is_rejected_before_replay(tmp_path):
    store = _candle_store(tmp_path)
    funding_store = _FakeFundingStore(coverage=[], events=[])
    policy = _flat_policy()
    cost_model = _RecordingCostModel()

    with pytest.raises(ValueError, match="funding quality gate failed"):
        _run(
            store,
            policy=policy,
            cost_model=cost_model,
            funding_required=True,
            funding_store=funding_store,
            funding_model=LinearFundingModel(),
        )

    assert policy.contexts == []
    assert cost_model.calls == []
    assert len(funding_store.event_calls) == 1  # only the quality builder's own read


def test_partial_coverage_is_rejected(tmp_path):
    store = _candle_store(tmp_path)
    funding_store = _FakeFundingStore(
        coverage=[_coverage(RUN_START, datetime(2024, 1, 1, 12, 0, tzinfo=UTC))], events=[]
    )

    with pytest.raises(ValueError, match="funding quality gate failed"):
        _run(
            store,
            policy=_flat_policy(),
            funding_required=True,
            funding_store=funding_store,
            funding_model=LinearFundingModel(),
        )


# --- partition defense on the second query ---


@pytest.mark.parametrize(
    "bad_kwargs", [{"exchange": "bybit"}, {"market_type": "spot"}, {"symbol": "ETHUSDT"}]
)
def test_wrong_partition_on_second_query_is_rejected(tmp_path, bad_kwargs):
    store = _candle_store(tmp_path)
    good_event = _funding_event(datetime(2024, 1, 1, 11, 30, tzinfo=UTC))
    bad_event = _funding_event(datetime(2024, 1, 1, 11, 30, tzinfo=UTC), **bad_kwargs)
    funding_store = _FakeFundingStore(
        coverage=[_coverage(RUN_START, RUN_END)], events_by_call=[[good_event], [bad_event]]
    )

    with pytest.raises(ValueError, match="partition"):
        _run(
            store,
            policy=_flat_policy(),
            funding_required=True,
            funding_store=funding_store,
            funding_model=LinearFundingModel(),
        )


def test_wrong_type_on_second_query_is_rejected(tmp_path):
    store = _candle_store(tmp_path)
    good_event = _funding_event(datetime(2024, 1, 1, 11, 30, tzinfo=UTC))
    funding_store = _FakeFundingStore(
        coverage=[_coverage(RUN_START, RUN_END)],
        events_by_call=[[good_event], [{"not": "an event"}]],
    )

    with pytest.raises(TypeError, match="HistoricalFundingEvent"):
        _run(
            store,
            policy=_flat_policy(),
            funding_required=True,
            funding_store=funding_store,
            funding_model=LinearFundingModel(),
        )


# --- error propagation ---


def test_quality_events_storage_error_propagates(tmp_path):
    store = _candle_store(tmp_path)
    funding_store = _FakeFundingStore(events=StorageError("sentinel"))

    with pytest.raises(StorageError, match="sentinel"):
        _run(
            store,
            policy=_flat_policy(),
            funding_required=True,
            funding_store=funding_store,
            funding_model=LinearFundingModel(),
        )


def test_quality_coverage_corruption_propagates(tmp_path):
    store = _candle_store(tmp_path)
    funding_store = _FakeFundingStore(events=[], coverage=DataCorruptionError("corrupt"))

    with pytest.raises(DataCorruptionError, match="corrupt"):
        _run(
            store,
            policy=_flat_policy(),
            funding_required=True,
            funding_store=funding_store,
            funding_model=LinearFundingModel(),
        )


def test_second_query_storage_error_propagates(tmp_path):
    store = _candle_store(tmp_path)
    funding_store = _FakeFundingStore(
        coverage=[_coverage(RUN_START, RUN_END)], events_by_call=[[], StorageError("sentinel")]
    )

    with pytest.raises(StorageError, match="sentinel"):
        _run(
            store,
            policy=_flat_policy(),
            funding_required=True,
            funding_store=funding_store,
            funding_model=LinearFundingModel(),
        )


# --- funding range derivation ---


def test_funding_range_derived_from_candles_not_requested_end(tmp_path):
    store = _candle_store(tmp_path)  # hours 10, 11, 12, 13
    funding_store = _FakeFundingStore(coverage=[_coverage(RUN_START, RUN_END)], events=[])

    _run(
        store,
        policy=_flat_policy(),
        requested_start=RUN_START,
        requested_end=datetime(2024, 1, 1, 15, 0, tzinfo=UTC),  # extends past actual data
        as_of_time=datetime(2024, 1, 1, 14, 37, tzinfo=UTC),
        funding_required=True,
        funding_store=funding_store,
        funding_model=LinearFundingModel(),
    )

    for call in [*funding_store.event_calls, *funding_store.coverage_calls]:
        assert call["start_time"] == RUN_START
        assert call["end_time"] == RUN_END  # not 15:00


# --- determinism ---


def test_deterministic_funding_enabled_run(tmp_path):
    store = _candle_store(tmp_path)
    event = _funding_event(datetime(2024, 1, 1, 11, 30, tzinfo=UTC))
    funding_store = _FakeFundingStore(coverage=[_coverage(RUN_START, RUN_END)], events=[event])

    first = _run(
        store,
        policy=_long_policy(),
        funding_required=True,
        funding_store=funding_store,
        funding_model=LinearFundingModel(),
    )
    second = _run(
        store,
        policy=_long_policy(),
        funding_required=True,
        funding_store=funding_store,
        funding_model=LinearFundingModel(),
    )

    assert first == second


# --- real SQLite wiring smoke ---


def test_real_sqlite_funding_wiring_smoke(tmp_path):
    candle_store = _candle_store(tmp_path)
    funding_store = SQLiteHistoricalFundingStore(tmp_path / "funding.db")
    event = _funding_event(datetime(2024, 1, 1, 11, 30, tzinfo=UTC))
    funding_store.write_ingestion_batch(
        [event],
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        covered_start=RUN_START,
        covered_end=RUN_END,
    )

    try:
        result = _run(
            candle_store,
            policy=_long_policy(),
            funding_required=True,
            funding_store=funding_store,
            funding_model=LinearFundingModel(),
        )
    finally:
        candle_store.close()
        funding_store.close()

    assert isinstance(result, BacktestResult)
    assert result.total_cost != Decimal(0)


def test_real_sqlite_funding_zero_event_smoke(tmp_path):
    candle_store = _candle_store(tmp_path)
    funding_store = SQLiteHistoricalFundingStore(tmp_path / "funding.db")
    funding_store.write_ingestion_batch(
        [],
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        covered_start=RUN_START,
        covered_end=RUN_END,
    )

    try:
        result = _run(
            candle_store,
            policy=_flat_policy(),
            funding_required=True,
            funding_store=funding_store,
            funding_model=LinearFundingModel(),
        )
    finally:
        candle_store.close()
        funding_store.close()

    assert isinstance(result, BacktestResult)
    assert result.total_cost == Decimal(0)
