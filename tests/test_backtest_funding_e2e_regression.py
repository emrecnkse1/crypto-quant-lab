"""Authoritative Faz 5B funding E2E golden regression + determinism (FUNDING_SPEC.md Bölüm 24,
Microstep 12: "Funding E2E golden + determinism").

Expected values are hand-computed from FUNDING_SPEC.md's locked contracts
(Bölüm 8, 9, 12) and BACKTEST_SPEC.md's own formulas, not from production
code — do not replace the hard-coded oracle below with production-helper
calculations (e.g. `apply_fill`, `apply_cash_cost`, `build_backtest_result`,
`run_backtest_replay`). This file intentionally duplicates the small
canonical-fixture style already used by `tests/test_backtest_regression.py`
and `tests/test_backtest_transaction_cost_regression.py`; it is not a shared
helper module.

This is a real-SQLite, high-level acceptance checkpoint only — it does not
recreate FUNDING-SPEC MS8-MS11's exhaustive matrices (missing/partial
coverage, bad configuration, wrong partition, zero-event coverage, error
propagation), which are already covered by
`tests/test_backtest_funding_store_runner.py` and
`tests/test_backtest_funding_replay.py`.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar

from crypto_quant_lab.backtest.costs import ProportionalCommissionModel
from crypto_quant_lab.backtest.models import (
    BacktestConfig,
    BacktestResult,
    EquityPoint,
    PositionTarget,
)
from crypto_quant_lab.backtest.store_runner import run_backtest_from_store
from crypto_quant_lab.funding.calculator import LinearFundingModel
from crypto_quant_lab.funding.models import FundingEvent, HistoricalFundingEvent
from crypto_quant_lab.funding.sqlite import SQLiteHistoricalFundingStore
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.base import HistoricalCandle
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore

_EXCHANGE = "binance"
_MARKET_TYPE = "usdm_perp"
_SYMBOL = "BTCUSDT"
_TIMEFRAME = "1h"

_CANONICAL_SPECS = (
    (0, Decimal(100), Decimal(101), Decimal(99), Decimal(100), Decimal(1)),
    (1, Decimal(101), Decimal(103), Decimal(100), Decimal(102), Decimal(1)),
    (2, Decimal(103), Decimal(105), Decimal(102), Decimal(104), Decimal(1)),
    (3, Decimal(105), Decimal(107), Decimal(104), Decimal(106), Decimal(1)),
    (4, Decimal(107), Decimal(109), Decimal(106), Decimal(108), Decimal(1)),
)


class _CanonicalPolicy:
    """Deterministic, context-length-driven target sequence for the canonical scenario."""

    _TARGETS_BY_LENGTH: ClassVar[dict[int, PositionTarget]] = {
        1: PositionTarget.LONG,
        2: PositionTarget.LONG,
        3: PositionTarget.SHORT,
        4: PositionTarget.FLAT,
        5: PositionTarget.FLAT,
    }

    def __init__(self):
        self.contexts = []

    def target_position(self, context):
        self.contexts.append(context)
        length = len(context.candles)
        if length not in self._TARGETS_BY_LENGTH:
            raise AssertionError(f"unexpected context length: {length}")
        return self._TARGETS_BY_LENGTH[length]


def _make_canonical_candles():
    candles = []
    for hour, open_, high, low, close, volume in _CANONICAL_SPECS:
        candles.append(
            Candle(
                symbol=_SYMBOL,
                timeframe=_TIMEFRAME,
                open_time=datetime(2026, 1, 1, hour, 0, tzinfo=UTC),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
            )
        )
    return tuple(candles)


def _make_candle_store(candle_db_path):
    store = SQLiteHistoricalCandleStore(candle_db_path)
    records = [
        HistoricalCandle(exchange=_EXCHANGE, market_type=_MARKET_TYPE, candle=candle)
        for candle in _make_canonical_candles()
    ]
    store.write_batch(records)
    return store


def _funding_event(*, hour, minute, rate_type, funding_rate, reference_price):
    return HistoricalFundingEvent(
        exchange=_EXCHANGE,
        market_type=_MARKET_TYPE,
        symbol=_SYMBOL,
        funding=FundingEvent(
            event_time=datetime(2026, 1, 1, hour, minute, tzinfo=UTC),
            funding_rate=Decimal(funding_rate),
            reference_price=Decimal(reference_price),
            rate_type=rate_type,
        ),
    )


def _canonical_funding_events():
    event_a = _funding_event(
        hour=1, minute=30, rate_type="Regular", funding_rate="0.001", reference_price="100"
    )
    event_b = _funding_event(
        hour=3, minute=0, rate_type="Regular", funding_rate="0.002", reference_price="100"
    )
    event_c = _funding_event(
        hour=3, minute=0, rate_type="Special", funding_rate="-0.0005", reference_price="100"
    )
    event_d = _funding_event(
        hour=3, minute=30, rate_type="Regular", funding_rate="0.001", reference_price="100"
    )
    return event_a, event_b, event_c, event_d


def _make_funding_store(funding_db_path):
    store = SQLiteHistoricalFundingStore(funding_db_path)
    event_a, event_b, event_c, event_d = _canonical_funding_events()
    # Intentionally non-chronological insertion order: the real SQLite query
    # must be what produces (event_time ASC, rate_type ASC) order, not
    # insertion order (FUNDING_DATA_SPEC.md Bölüm 29).
    store.write_ingestion_batch(
        [event_a, event_d, event_c, event_b],
        exchange=_EXCHANGE,
        market_type=_MARKET_TYPE,
        symbol=_SYMBOL,
        covered_start=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        covered_end=datetime(2026, 1, 1, 5, 0, tzinfo=UTC),
    )
    return store


def _config():
    return BacktestConfig(initial_cash=Decimal(1000), position_quantity=Decimal(1))


def _cost_model():
    return ProportionalCommissionModel(rate=Decimal("0.001"))


def _expected_result():
    return BacktestResult(
        initial_cash=Decimal(1000),
        final_cash=Decimal("1001.432"),
        final_equity=Decimal("1001.432"),
        total_realized_pnl=Decimal(2),
        total_unrealized_pnl=Decimal(0),
        total_pnl=Decimal("1.432"),
        total_cost=Decimal("0.568"),
        fill_count=3,
        trade_count=4,
        equity_curve=(
            EquityPoint(time=datetime(2026, 1, 1, 1, 0, tzinfo=UTC), equity=Decimal(1000)),
            EquityPoint(time=datetime(2026, 1, 1, 2, 0, tzinfo=UTC), equity=Decimal("1000.799")),
            EquityPoint(time=datetime(2026, 1, 1, 3, 0, tzinfo=UTC), equity=Decimal("1002.649")),
            EquityPoint(time=datetime(2026, 1, 1, 4, 0, tzinfo=UTC), equity=Decimal("1002.539")),
            EquityPoint(time=datetime(2026, 1, 1, 5, 0, tzinfo=UTC), equity=Decimal("1001.432")),
        ),
    )


def _run_kwargs():
    return {
        "exchange": _EXCHANGE,
        "market_type": _MARKET_TYPE,
        "symbol": _SYMBOL,
        "timeframe": _TIMEFRAME,
        "requested_start": datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        "requested_end": datetime(2026, 1, 1, 5, 0, tzinfo=UTC),
        "as_of_time": datetime(2026, 1, 1, 5, 0, tzinfo=UTC),
        "config": _config(),
        "cost_model": _cost_model(),
        "funding_required": True,
        "funding_model": LinearFundingModel(),
    }


def test_canonical_funding_e2e_matches_golden_result(tmp_path):
    candle_store = _make_candle_store(tmp_path / "candles.db")
    funding_store = _make_funding_store(tmp_path / "funding.db")
    try:
        actual = run_backtest_from_store(
            candle_store,
            policy=_CanonicalPolicy(),
            funding_store=funding_store,
            **_run_kwargs(),
        )
    finally:
        candle_store.close()
        funding_store.close()

    expected = _expected_result()
    assert actual == expected

    assert actual.total_pnl == actual.final_equity - Decimal(1000)
    assert (
        actual.total_pnl
        == actual.total_realized_pnl + actual.total_unrealized_pnl - actual.total_cost
    )


def test_canonical_funding_e2e_is_deterministic_across_reopen(tmp_path):
    candle_db_path = tmp_path / "candles.db"
    funding_db_path = tmp_path / "funding.db"

    candle_store = _make_candle_store(candle_db_path)
    funding_store = _make_funding_store(funding_db_path)
    try:
        kwargs = _run_kwargs()
        result1 = run_backtest_from_store(
            candle_store, policy=_CanonicalPolicy(), funding_store=funding_store, **kwargs
        )
        result2 = run_backtest_from_store(
            candle_store, policy=_CanonicalPolicy(), funding_store=funding_store, **kwargs
        )
    finally:
        candle_store.close()
        funding_store.close()

    expected = _expected_result()
    assert result1 == expected
    assert result2 == expected
    assert result1 == result2

    reopened_candle_store = SQLiteHistoricalCandleStore(candle_db_path)
    reopened_funding_store = SQLiteHistoricalFundingStore(funding_db_path)
    try:
        result3 = run_backtest_from_store(
            reopened_candle_store,
            policy=_CanonicalPolicy(),
            funding_store=reopened_funding_store,
            **_run_kwargs(),
        )
    finally:
        reopened_candle_store.close()
        reopened_funding_store.close()

    assert result3 == result1
    assert result3 == expected
