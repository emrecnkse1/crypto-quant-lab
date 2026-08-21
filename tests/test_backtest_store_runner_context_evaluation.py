from datetime import UTC, datetime, timedelta, timezone, tzinfo
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
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.base import HistoricalCandle
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore

EXCHANGE = "binance"
MARKET_TYPE = "usdm_perp"
SYMBOL = "BTCUSDT"

RUN_START = datetime(2024, 1, 1, 8, 0, tzinfo=UTC)
EVALUATION_START = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)  # 2 context candles: 08:00, 09:00
RUN_END = datetime(2024, 1, 1, 14, 0, tzinfo=UTC)  # 4 evaluation candles: 10:00-13:00


class _BrokenTzInfo(tzinfo):
    """A tzinfo that pretends to be attached but reports no offset (pseudo-naive)."""

    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return None


class _RecordingPolicy:
    def __init__(self, target_fn):
        self._target_fn = target_fn
        self.contexts = []

    def target_position(self, context):
        self.contexts.append(context)
        return self._target_fn(context)


class _PoisonCandleStore:
    def query(self, *args, **kwargs):
        raise AssertionError(
            "candle store must not be queried before raw evaluation_start validation"
        )

    def write_batch(self, records):
        raise AssertionError("write_batch must never be called")

    def close(self):
        pass


class _PoisonFundingStore:
    def query_events(self, **kwargs):
        raise AssertionError("funding store must not be queried for an invalid evaluation_start")

    def query_coverage(self, **kwargs):
        raise AssertionError("funding store must not be queried for an invalid evaluation_start")

    def close(self):
        pass


class _CountingCandleStore:
    """Wraps a real store, recording `query()` calls without altering behavior."""

    def __init__(self, inner):
        self._inner = inner
        self.query_calls: list[dict] = []

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
        return self._inner.query(exchange, market_type, symbol, timeframe, start_time, end_time)

    def write_batch(self, records):
        return self._inner.write_batch(records)

    def close(self):
        return self._inner.close()


class _FakeFundingStore:
    """Records calls; returns scripted coverage/events."""

    def __init__(self, *, coverage=(), events=()):
        self._coverage = coverage
        self._events = events
        self.event_calls: list[dict] = []
        self.coverage_calls: list[dict] = []

    def query_events(self, *, exchange, market_type, symbol, start_time, end_time):
        self.event_calls.append(
            {
                "exchange": exchange,
                "market_type": market_type,
                "symbol": symbol,
                "start_time": start_time,
                "end_time": end_time,
            }
        )
        return self._events

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
        return self._coverage

    def close(self):
        pass


def _make_record(open_time, *, price=Decimal(100), timeframe="1h"):
    candle = Candle(
        symbol=SYMBOL,
        timeframe=timeframe,
        open_time=open_time,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal(1),
    )
    return HistoricalCandle(exchange=EXCHANGE, market_type=MARKET_TYPE, candle=candle)


def _candle_store(tmp_path, hours=(8, 9, 10, 11, 12, 13)):
    store = SQLiteHistoricalCandleStore(tmp_path / "candles.db")
    store.write_batch([_make_record(datetime(2024, 1, 1, hour, 0, tzinfo=UTC)) for hour in hours])
    return store


def _config(initial_cash=Decimal(1000), position_quantity=Decimal(1)):
    return BacktestConfig(initial_cash=initial_cash, position_quantity=position_quantity)


def _flat_policy():
    return _RecordingPolicy(lambda context: PositionTarget.FLAT)


def _funding_event(event_time, *, funding_rate="0.001", reference_price="100", rate_type="Regular"):
    return HistoricalFundingEvent(
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        funding=FundingEvent(
            event_time=event_time,
            funding_rate=Decimal(funding_rate),
            reference_price=Decimal(reference_price),
            rate_type=rate_type,
        ),
    )


def _coverage(start, end):
    return FundingCoverageInterval(start_time=start, end_time=end)


def _run(
    store,
    *,
    policy,
    evaluation_start=None,
    funding_store=None,
    funding_model=None,
    funding_required=False,
    requested_start=RUN_START,
    requested_end=RUN_END,
    as_of_time=RUN_END,
    cost_model=None,
):
    return run_backtest_from_store(
        store,
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        timeframe="1h",
        requested_start=requested_start,
        requested_end=requested_end,
        as_of_time=as_of_time,
        config=_config(),
        policy=policy,
        cost_model=cost_model or ZeroCostModel(),
        funding_required=funding_required,
        funding_store=funding_store,
        funding_model=funding_model,
        evaluation_start=evaluation_start,
    )


# --- legacy / zero context ---


def test_evaluation_start_none_legacy_behavior_unchanged(tmp_path):
    store = _candle_store(tmp_path)
    result = _run(store, policy=_flat_policy())
    assert isinstance(result, BacktestResult)
    assert len(result.equity_curve) == 6  # every loaded candle is an evaluation candle


def test_evaluation_start_equals_requested_start_matches_legacy(tmp_path):
    store = _candle_store(tmp_path)

    def _target(context):
        return PositionTarget.LONG if len(context.candles) == 1 else PositionTarget.FLAT

    omitted = _run(
        store,
        policy=_RecordingPolicy(_target),
        funding_required=True,
        funding_store=_FakeFundingStore(
            coverage=[_coverage(RUN_START, RUN_END)],
            events=[_funding_event(datetime(2024, 1, 1, 9, 0, tzinfo=UTC))],
        ),
        funding_model=LinearFundingModel(),
    )
    explicit = _run(
        store,
        policy=_RecordingPolicy(_target),
        funding_required=True,
        funding_store=_FakeFundingStore(
            coverage=[_coverage(RUN_START, RUN_END)],
            events=[_funding_event(datetime(2024, 1, 1, 9, 0, tzinfo=UTC))],
        ),
        funding_model=LinearFundingModel(),
        evaluation_start=RUN_START,
    )
    assert omitted == explicit


# --- candle path ---


def test_candle_query_starts_at_requested_start_not_evaluation_start(tmp_path):
    counting_store = _CountingCandleStore(_candle_store(tmp_path))
    _run(counting_store, policy=_flat_policy(), evaluation_start=EVALUATION_START)
    assert len(counting_store.query_calls) > 0
    assert all(call["start_time"] == RUN_START for call in counting_store.query_calls)


def test_first_policy_context_includes_context_candles(tmp_path):
    store = _candle_store(tmp_path)
    policy = _flat_policy()
    _run(store, policy=policy, evaluation_start=EVALUATION_START)
    assert len(policy.contexts) == 4  # 4 evaluation candles (10,11,12,13), not 6
    assert len(policy.contexts[0].candles) == 3  # 2 context (08,09) + E0 (10)


def test_store_backed_result_matches_direct_context_aware_replay(tmp_path):
    store = _candle_store(tmp_path)

    def _target(context):
        return PositionTarget.LONG if len(context.candles) == 3 else PositionTarget.FLAT

    store_result = _run(store, policy=_RecordingPolicy(_target), evaluation_start=EVALUATION_START)

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
    direct_result = run_backtest_replay(
        candles,
        as_of_time=RUN_END,
        config=_config(),
        policy=_RecordingPolicy(_target),
        cost_model=ZeroCostModel(),
        evaluation_start=EVALUATION_START,
    )
    assert store_result == direct_result


# --- funding path ---


def test_funding_query_starts_at_evaluation_start(tmp_path):
    store = _candle_store(tmp_path)
    funding_store = _FakeFundingStore(coverage=[_coverage(EVALUATION_START, RUN_END)], events=[])
    _run(
        store,
        policy=_flat_policy(),
        funding_required=True,
        funding_store=funding_store,
        funding_model=LinearFundingModel(),
        evaluation_start=EVALUATION_START,
    )
    assert all(call["start_time"] == EVALUATION_START for call in funding_store.event_calls)
    assert all(call["start_time"] == EVALUATION_START for call in funding_store.coverage_calls)
    assert all(call["end_time"] == RUN_END for call in funding_store.event_calls)


def test_context_funding_coverage_not_required(tmp_path):
    store = _candle_store(tmp_path)
    # authoritative coverage exists ONLY over [evaluation_start, run_end) — nothing over
    # [requested_start, evaluation_start).
    funding_store = _FakeFundingStore(coverage=[_coverage(EVALUATION_START, RUN_END)], events=[])
    result = _run(
        store,
        policy=_flat_policy(),
        funding_required=True,
        funding_store=funding_store,
        funding_model=LinearFundingModel(),
        evaluation_start=EVALUATION_START,
    )
    assert isinstance(result, BacktestResult)


def test_zero_event_authoritative_economic_coverage_passes(tmp_path):
    store = _candle_store(tmp_path)
    funding_store = _FakeFundingStore(coverage=[_coverage(EVALUATION_START, RUN_END)], events=[])
    result = _run(
        store,
        policy=_flat_policy(),
        funding_required=True,
        funding_store=funding_store,
        funding_model=LinearFundingModel(),
        evaluation_start=EVALUATION_START,
    )
    assert result.total_cost == Decimal(0)
    assert len(funding_store.event_calls) == 2  # quality's own read + the second read


def test_missing_economic_funding_coverage_fails(tmp_path):
    store = _candle_store(tmp_path)
    # coverage over only half of [evaluation_start, run_end)
    funding_store = _FakeFundingStore(
        coverage=[_coverage(EVALUATION_START, datetime(2024, 1, 1, 12, 0, tzinfo=UTC))], events=[]
    )
    with pytest.raises(ValueError, match="funding quality gate failed"):
        _run(
            store,
            policy=_flat_policy(),
            funding_required=True,
            funding_store=funding_store,
            funding_model=LinearFundingModel(),
            evaluation_start=EVALUATION_START,
        )


def test_bad_funding_store_event_before_evaluation_start_fails(tmp_path):
    store = _candle_store(tmp_path)
    bad_event = _funding_event(datetime(2024, 1, 1, 9, 0, tzinfo=UTC))  # inside context range
    funding_store = _FakeFundingStore(
        coverage=[_coverage(EVALUATION_START, RUN_END)], events=[bad_event]
    )
    # Rejected even earlier than replay's own lower-bound check: the funding
    # quality-gate layer itself validates returned events against its own
    # requested [evaluation_start, run_end) range.
    with pytest.raises(ValueError, match="outside the requested"):
        _run(
            store,
            policy=_flat_policy(),
            funding_required=True,
            funding_store=funding_store,
            funding_model=LinearFundingModel(),
            evaluation_start=EVALUATION_START,
        )


def test_funding_required_false_context_aware_run_performs_no_funding_io(tmp_path):
    store = _candle_store(tmp_path)
    result = _run(store, policy=_flat_policy(), evaluation_start=EVALUATION_START)
    assert isinstance(result, BacktestResult)
    assert result.total_cost == Decimal(0)


# --- boundaries ---


def test_evaluation_start_before_requested_start_fails_before_io():
    with pytest.raises(ValueError, match="evaluation_start must satisfy"):
        _run(
            _PoisonCandleStore(),
            policy=_flat_policy(),
            evaluation_start=datetime(2024, 1, 1, 7, 0, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    "bad_evaluation_start",
    [RUN_END, RUN_END + timedelta(hours=1)],
    ids=["equal_to_requested_end", "after_requested_end"],
)
def test_evaluation_start_at_or_after_requested_end_fails_before_io(bad_evaluation_start):
    with pytest.raises(ValueError, match="evaluation_start must satisfy"):
        _run(_PoisonCandleStore(), policy=_flat_policy(), evaluation_start=bad_evaluation_start)


def test_misaligned_evaluation_start_fails_before_io():
    with pytest.raises(ValueError, match="grid"):
        _run(
            _PoisonCandleStore(),
            policy=_flat_policy(),
            evaluation_start=datetime(2024, 1, 1, 10, 30, tzinfo=UTC),
        )


def test_non_datetime_evaluation_start_fails_before_io():
    with pytest.raises(TypeError):
        _run(_PoisonCandleStore(), policy=_flat_policy(), evaluation_start="2024-01-01T10:00:00Z")


def test_naive_evaluation_start_fails_before_io():
    with pytest.raises(ValueError):
        _run(
            _PoisonCandleStore(),
            policy=_flat_policy(),
            evaluation_start=datetime(2024, 1, 1, 10, 0),  # noqa: DTZ001
        )


def test_pseudo_naive_evaluation_start_fails_before_io():
    with pytest.raises(ValueError):
        _run(
            _PoisonCandleStore(),
            policy=_flat_policy(),
            evaluation_start=datetime(2024, 1, 1, 10, 0, tzinfo=_BrokenTzInfo()),
        )


def test_non_utc_equivalent_evaluation_start_accepted(tmp_path):
    store = _candle_store(tmp_path)
    plus_five = timezone(timedelta(hours=5))
    policy = _flat_policy()
    _run(
        store,
        policy=policy,
        evaluation_start=datetime(2024, 1, 1, 15, 0, tzinfo=plus_five),  # == 10:00 UTC
    )
    assert len(policy.contexts) == 4  # same classification as EVALUATION_START (10:00 UTC)


def test_evaluation_start_at_last_prepared_candle_is_legal(tmp_path):
    store = _candle_store(tmp_path)
    policy = _flat_policy()
    result = _run(
        store,
        policy=policy,
        evaluation_start=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),  # last loaded candle's open
    )
    assert len(policy.contexts) == 1
    assert len(result.equity_curve) == 1
    assert result.fill_count == 0


def test_effective_end_invalid_boundary_fails_after_candle_io_before_funding_io(tmp_path):
    counting_store = _CountingCandleStore(_candle_store(tmp_path))
    funding_store = _PoisonFundingStore()

    # as_of_time=11:00 clamps effective_end to 11:00 -> prepared candles are only
    # 08:00/09:00/10:00; the actual run end becomes 11:00 even though the raw
    # requested_end (14:00) alone would make evaluation_start=12:00 look legal.
    with pytest.raises(
        ValueError, match="evaluation_start must leave at least one evaluation candle"
    ):
        _run(
            counting_store,
            policy=_flat_policy(),
            evaluation_start=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            funding_required=True,
            funding_store=funding_store,
            funding_model=LinearFundingModel(),
        )

    assert len(counting_store.query_calls) > 0  # candle store WAS queried to learn effective_end
