import inspect
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

import crypto_quant_lab.validation as validation_package
import crypto_quant_lab.validation.annualized_metrics as annualized_metrics_module
from crypto_quant_lab.backtest.costs import ZeroCostModel
from crypto_quant_lab.backtest.models import (
    BacktestConfig,
    BacktestResult,
    EquityPoint,
    PositionTarget,
)
from crypto_quant_lab.backtest.store_runner import run_backtest_from_store
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.market_data.timeframes import candle_duration
from crypto_quant_lab.storage.base import HistoricalCandle
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore
from crypto_quant_lab.validation.annualized_metrics import (
    compute_annualized_sharpe_ratio,
    compute_cagr,
    compute_calmar_ratio,
    compute_sortino_ratio,
)
from crypto_quant_lab.validation.candidate import Candidate, Trial
from crypto_quant_lab.validation.metrics import (
    compute_periodic_returns,
    compute_stage1_metrics,
    compute_stage2_metrics,
)
from crypto_quant_lab.validation.rolling import WindowResult, run_rolling_backtest_from_store
from crypto_quant_lab.validation.windows import TemporalWindow

EXCHANGE = "binance"
MARKET_TYPE = "usdm_perp"
SYMBOL = "BTCUSDT"
TIMEFRAME = "1h"

T0 = datetime(2024, 1, 1, 8, 0, tzinfo=UTC)
AS_OF_TIME = datetime(2024, 1, 2, 0, 0, tzinfo=UTC)

PERIODS_PER_YEAR_1H = Decimal(8760)
PERIODS_PER_YEAR_4H = Decimal(2190)


class _RecordingPolicy:
    def __init__(self, target_fn):
        self._target_fn = target_fn

    def target_position(self, context):
        return self._target_fn(context)


def _point(equity, *, time=T0):
    return EquityPoint(time=time, equity=equity)


def _curve(*equities, start=T0, step=timedelta(hours=1)):
    points = []
    t = start
    for equity in equities:
        points.append(EquityPoint(time=t, equity=equity))
        t += step
    return tuple(points)


def _result(
    *,
    initial_cash=Decimal(1000),
    final_cash=None,
    final_equity=Decimal(1000),
    total_realized_pnl=Decimal(0),
    total_unrealized_pnl=Decimal(0),
    total_pnl=None,
    total_cost=Decimal(0),
    fill_count=0,
    trade_count=0,
    equity_curve=None,
):
    if final_cash is None:
        final_cash = final_equity
    if total_pnl is None:
        total_pnl = final_equity - initial_cash
    if equity_curve is None:
        equity_curve = (_point(final_equity),)
    return BacktestResult(
        initial_cash=initial_cash,
        final_cash=final_cash,
        final_equity=final_equity,
        total_realized_pnl=total_realized_pnl,
        total_unrealized_pnl=total_unrealized_pnl,
        total_pnl=total_pnl,
        total_cost=total_cost,
        fill_count=fill_count,
        trade_count=trade_count,
        equity_curve=equity_curve,
    )


def _make_record(open_time, *, price=Decimal(100)):
    candle = Candle(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        open_time=open_time,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal(1),
    )
    return HistoricalCandle(exchange=EXCHANGE, market_type=MARKET_TYPE, candle=candle)


def _candle_store(tmp_path, prices):
    store = SQLiteHistoricalCandleStore(tmp_path / "candles.db")
    records = []
    t = T0
    for price in prices:
        records.append(_make_record(t, price=price))
        t += timedelta(hours=1)
    store.write_batch(records)
    return store


def _config(initial_cash=Decimal(1000), position_quantity=Decimal(1)):
    return BacktestConfig(initial_cash=initial_cash, position_quantity=position_quantity)


def _run(store, *, policy, prices, cost_model=None):
    requested_end = T0 + timedelta(hours=len(prices))
    return run_backtest_from_store(
        store,
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        requested_start=T0,
        requested_end=requested_end,
        as_of_time=AS_OF_TIME,
        config=_config(),
        policy=policy,
        cost_model=cost_model or ZeroCostModel(),
    )


# =====================================================================
# API / value-object surface (criteria 1, 2)
# =====================================================================


def test_all_four_functions_exist_and_are_callable():
    assert callable(compute_annualized_sharpe_ratio)
    assert callable(compute_sortino_ratio)
    assert callable(compute_cagr)
    assert callable(compute_calmar_ratio)


def test_sharpe_exact_signature():
    sig = inspect.signature(compute_annualized_sharpe_ratio)
    params = list(sig.parameters.values())
    assert params[0].name == "result"
    assert params[0].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params[0].default is inspect.Parameter.empty
    assert sig.parameters["timeframe"].kind == inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["timeframe"].default is inspect.Parameter.empty
    assert sig.parameters["risk_free_per_period"].kind == inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["risk_free_per_period"].default == Decimal(0)


def test_sortino_exact_signature():
    sig = inspect.signature(compute_sortino_ratio)
    params = list(sig.parameters.values())
    assert params[0].name == "result"
    assert params[0].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert sig.parameters["timeframe"].kind == inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["timeframe"].default is inspect.Parameter.empty
    assert (
        sig.parameters["minimum_acceptable_return_per_period"].kind
        == inspect.Parameter.KEYWORD_ONLY
    )
    assert sig.parameters["minimum_acceptable_return_per_period"].default == Decimal(0)


def test_cagr_exact_signature():
    sig = inspect.signature(compute_cagr)
    params = list(sig.parameters.values())
    assert params[0].name == "result"
    assert params[0].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert sig.parameters["timeframe"].kind == inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["timeframe"].default is inspect.Parameter.empty
    assert set(sig.parameters) == {"result", "timeframe"}


def test_calmar_exact_signature():
    sig = inspect.signature(compute_calmar_ratio)
    params = list(sig.parameters.values())
    assert params[0].name == "result"
    assert params[0].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert sig.parameters["timeframe"].kind == inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["timeframe"].default is inspect.Parameter.empty
    assert set(sig.parameters) == {"result", "timeframe"}


def test_all_four_functions_return_bare_decimal_never_a_dataclass():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1200),
        equity_curve=_curve(Decimal(1100), Decimal(900), Decimal(1200)),
    )
    sharpe = compute_annualized_sharpe_ratio(result, timeframe=TIMEFRAME)
    sortino = compute_sortino_ratio(result, timeframe=TIMEFRAME)
    cagr = compute_cagr(result, timeframe=TIMEFRAME)
    calmar = compute_calmar_ratio(result, timeframe=TIMEFRAME)
    for value in (sharpe, sortino, cagr, calmar):
        assert type(value) is Decimal


def test_no_function_ever_returns_none():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1200),
        equity_curve=_curve(Decimal(1100), Decimal(900), Decimal(1200)),
    )
    assert compute_annualized_sharpe_ratio(result, timeframe=TIMEFRAME) is not None
    assert compute_sortino_ratio(result, timeframe=TIMEFRAME) is not None
    assert compute_cagr(result, timeframe=TIMEFRAME) is not None
    assert compute_calmar_ratio(result, timeframe=TIMEFRAME) is not None


def test_validation_package_root_unchanged():
    assert not hasattr(validation_package, "compute_annualized_sharpe_ratio")
    assert not hasattr(validation_package, "compute_sortino_ratio")
    assert not hasattr(validation_package, "compute_cagr")
    assert not hasattr(validation_package, "compute_calmar_ratio")


def test_backtest_result_has_no_annualized_fields():
    field_names = {f.name for f in fields(BacktestResult)}
    assert "annualized_sharpe" not in field_names
    assert "sortino_ratio" not in field_names
    assert "cagr" not in field_names
    assert "calmar_ratio" not in field_names


def test_window_result_has_no_annualized_fields():
    field_names = {f.name for f in fields(WindowResult)}
    assert "annualized_sharpe" not in field_names
    assert "sortino_ratio" not in field_names
    assert "cagr" not in field_names
    assert "calmar_ratio" not in field_names


def test_candidate_and_trial_have_no_annualized_fields():
    for field_names in ({f.name for f in fields(Candidate)}, {f.name for f in fields(Trial)}):
        assert "annualized_sharpe" not in field_names
        assert "sortino_ratio" not in field_names
        assert "cagr" not in field_names
        assert "calmar_ratio" not in field_names


# =====================================================================
# Existing Stage-1/Stage-2/rolling/windows/candidate modules unchanged (criterion 3)
# =====================================================================


def test_annualized_metrics_module_does_not_import_rolling_windows_candidate():
    assert not hasattr(annualized_metrics_module, "rolling")
    assert not hasattr(annualized_metrics_module, "windows")
    assert not hasattr(annualized_metrics_module, "candidate")
    assert not hasattr(annualized_metrics_module, "run_rolling_backtest_from_store")
    assert not hasattr(annualized_metrics_module, "run_context_aware_rolling_backtest_from_store")
    assert not hasattr(annualized_metrics_module, "Candidate")
    assert not hasattr(annualized_metrics_module, "Trial")
    assert not hasattr(annualized_metrics_module, "WindowResult")


def test_annualized_metrics_module_only_imports_public_metrics_symbols():
    assert not hasattr(annualized_metrics_module, "_metrics_decimal_context")
    assert not hasattr(annualized_metrics_module, "_require_core_result_contract")
    assert not hasattr(annualized_metrics_module, "_require_positive_denominators")
    assert not hasattr(annualized_metrics_module, "_require_backtest_result")
    assert not hasattr(annualized_metrics_module, "_require_valid_equity_curve")


def test_existing_stage1_stage2_public_api_still_present_and_callable():
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(1100))
    assert compute_stage1_metrics(result).total_return == Decimal("0.1")
    result_two = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1100), Decimal(1210), Decimal(1100)),
    )
    assert isinstance(compute_stage2_metrics(result_two).sharpe_ratio, Decimal)
    assert len(compute_periodic_returns(result_two)) == 3


# =====================================================================
# timeframe / calendar-basis validation (criteria 4, 5, 6, 7)
# =====================================================================


@pytest.mark.parametrize(
    "call",
    [
        lambda result: compute_annualized_sharpe_ratio(result, timeframe=123),
        lambda result: compute_sortino_ratio(result, timeframe=123),
        lambda result: compute_cagr(result, timeframe=123),
        lambda result: compute_calmar_ratio(result, timeframe=123),
    ],
)
def test_timeframe_must_be_str_type_error(call):
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(1000))
    with pytest.raises(TypeError, match="timeframe must be a str"):
        call(result)


def test_timeframe_type_checked_before_risk_free_type_for_sharpe():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1100), Decimal(1210), Decimal(1100)),
    )
    with pytest.raises(TypeError, match="timeframe must be a str"):
        compute_annualized_sharpe_ratio(result, timeframe=123, risk_free_per_period="not-decimal")


def test_timeframe_type_checked_before_target_type_for_sortino():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1100), Decimal(1210), Decimal(1100)),
    )
    with pytest.raises(TypeError, match="timeframe must be a str"):
        compute_sortino_ratio(
            result, timeframe=123, minimum_acceptable_return_per_period="not-decimal"
        )


@pytest.mark.parametrize(
    "call",
    [
        lambda result: compute_annualized_sharpe_ratio(result, timeframe="1M"),
        lambda result: compute_sortino_ratio(result, timeframe="1M"),
        lambda result: compute_cagr(result, timeframe="1M"),
        lambda result: compute_calmar_ratio(result, timeframe="1M"),
    ],
)
def test_unsupported_timeframe_propagates_candle_duration_error_unchanged(call):
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(1000))
    try:
        candle_duration("1M")
        pytest.fail("expected candle_duration to reject '1M'")
    except ValueError as expected:
        expected_message = str(expected)
    with pytest.raises(ValueError, match="unsupported timeframe") as excinfo:
        call(result)
    assert str(excinfo.value) == expected_message


def test_unsupported_timeframe_checked_before_sortino_target_validation():
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(1000))
    with pytest.raises(ValueError, match="unsupported timeframe"):
        compute_sortino_ratio(
            result, timeframe="1M", minimum_acceptable_return_per_period="not-decimal"
        )


def test_periods_per_year_exact_for_1h_and_4h_via_annualized_sharpe():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1100), Decimal(1210), Decimal(1100)),
    )
    stage2 = compute_stage2_metrics(result)
    expected_1h = stage2.sharpe_ratio * PERIODS_PER_YEAR_1H.sqrt()
    expected_4h = stage2.sharpe_ratio * PERIODS_PER_YEAR_4H.sqrt()
    assert compute_annualized_sharpe_ratio(result, timeframe="1h") == expected_1h
    assert compute_annualized_sharpe_ratio(result, timeframe="4h") == expected_4h
    assert expected_1h != expected_4h


def test_calendar_basis_is_365_days_no_leap_year_effect():
    # Same return-period count and total_return, deliberately spanning a
    # leap-year February (2024-02-29) vs. an ordinary period -- CAGR must
    # be identical because periods_per_year is derived purely from
    # `timeframe`, never from real elapsed calendar time.
    leap_curve = (
        EquityPoint(time=datetime(2024, 2, 28, 23, 0, tzinfo=UTC), equity=Decimal(1050)),
        EquityPoint(time=datetime(2024, 2, 29, 0, 0, tzinfo=UTC), equity=Decimal(1100)),
    )
    ordinary_curve = (
        EquityPoint(time=datetime(2023, 3, 1, 0, 0, tzinfo=UTC), equity=Decimal(1050)),
        EquityPoint(time=datetime(2023, 3, 1, 1, 0, tzinfo=UTC), equity=Decimal(1100)),
    )
    leap_result = _result(
        initial_cash=Decimal(1000), final_equity=Decimal(1100), equity_curve=leap_curve
    )
    ordinary_result = _result(
        initial_cash=Decimal(1000), final_equity=Decimal(1100), equity_curve=ordinary_curve
    )
    assert compute_cagr(leap_result, timeframe="1h") == compute_cagr(
        ordinary_result, timeframe="1h"
    )


def test_calendar_basis_not_configurable():
    for fn in (
        compute_annualized_sharpe_ratio,
        compute_sortino_ratio,
        compute_cagr,
        compute_calmar_ratio,
    ):
        params = set(inspect.signature(fn).parameters)
        assert "calendar" not in params
        assert "periods_per_year" not in params
        assert "year_days" not in params


def test_no_float_conversion_anywhere_in_module_source():
    source = inspect.getsource(annualized_metrics_module)
    assert "float(" not in source
    assert "numpy" not in source.lower()
    assert "pandas" not in source.lower()
    assert ".quantize(" not in source


# =====================================================================
# Annualized Sharpe (criteria 8, 9, 10, 11)
# =====================================================================


def test_sharpe_propagates_stage2_zero_stdev_error_unchanged():
    # constant periodic returns -> Stage-2's total return_stdev is zero.
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(810),
        equity_curve=_curve(Decimal(900), Decimal(810)),
    )
    with pytest.raises(ValueError, match="return_stdev must be greater than zero"):
        compute_annualized_sharpe_ratio(result, timeframe=TIMEFRAME)


def test_sharpe_propagates_stage2_insufficient_returns_error_unchanged():
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(1000))
    with pytest.raises(ValueError, match="at least two periodic returns"):
        compute_annualized_sharpe_ratio(result, timeframe=TIMEFRAME)


def test_sharpe_does_not_reinvent_return_series_or_stdev_algorithm():
    source = inspect.getsource(compute_annualized_sharpe_ratio)
    assert "compute_stage2_metrics(" in source
    assert "equity_curve" not in source


def test_sharpe_exact_operation_order_sharpe_ratio_times_sqrt():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1100), Decimal(1210), Decimal(1100)),
    )
    risk_free = Decimal("0.0001234567890123456789012345")
    stage2 = compute_stage2_metrics(result, risk_free_per_period=risk_free)
    locked = stage2.sharpe_ratio * PERIODS_PER_YEAR_1H.sqrt()
    assert (
        compute_annualized_sharpe_ratio(result, timeframe="1h", risk_free_per_period=risk_free)
        == locked
    )


def test_sharpe_risk_free_convention_is_per_period_matches_stage2():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1100), Decimal(1210), Decimal(1100)),
    )
    rate = Decimal("0.001")
    stage2_with_rate = compute_stage2_metrics(result, risk_free_per_period=rate)
    stage2_without_rate = compute_stage2_metrics(result)
    assert stage2_with_rate.sharpe_ratio != stage2_without_rate.sharpe_ratio
    annualized_with_rate = compute_annualized_sharpe_ratio(
        result, timeframe="1h", risk_free_per_period=rate
    )
    annualized_without_rate = compute_annualized_sharpe_ratio(result, timeframe="1h")
    assert annualized_with_rate != annualized_without_rate


def test_sharpe_negative_is_legal():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(900),
        equity_curve=_curve(Decimal(950), Decimal(850), Decimal(900)),
    )
    sharpe = compute_annualized_sharpe_ratio(result, timeframe=TIMEFRAME)
    assert sharpe < Decimal(0)


def test_sharpe_zero_is_legal_when_mean_equals_risk_free():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1100), Decimal(1210), Decimal(1100)),
    )
    stage2 = compute_stage2_metrics(result)
    sharpe = compute_annualized_sharpe_ratio(
        result, timeframe=TIMEFRAME, risk_free_per_period=stage2.mean_return
    )
    assert sharpe == Decimal(0)


def test_sharpe_non_finite_output_rejected():
    result = _result(
        initial_cash=Decimal("1E-500000"),
        final_equity=Decimal("1E+500000"),
        equity_curve=_curve(Decimal("1E-499999"), Decimal("1E+500000")),
    )
    with pytest.raises(ValueError):
        compute_annualized_sharpe_ratio(result, timeframe=TIMEFRAME)


# =====================================================================
# Sortino ratio (criteria 12, 13, 14, 15, 16, 17)
# =====================================================================


def test_sortino_succeeds_when_stage2_would_reject_zero_total_stdev():
    # constant periodic returns (-10% each period): Stage-2's total
    # return_stdev is zero (sample variance of a constant series), but the
    # downside deviation is a positive constant, so Sortino remains defined.
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(810),
        equity_curve=_curve(Decimal(900), Decimal(810)),
    )
    with pytest.raises(ValueError, match="return_stdev must be greater than zero"):
        compute_stage2_metrics(result)

    sortino = compute_sortino_ratio(result, timeframe="1h")
    expected = Decimal(-1) * PERIODS_PER_YEAR_1H.sqrt()
    assert sortino == expected


def test_sortino_does_not_call_stage2_metrics():
    source = inspect.getsource(compute_sortino_ratio)
    assert "compute_periodic_returns(" in source
    assert "compute_stage2_metrics(" not in source


def test_sortino_default_minimum_acceptable_return_is_zero():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1050), Decimal(950), Decimal(1100)),
    )
    default_call = compute_sortino_ratio(result, timeframe=TIMEFRAME)
    explicit_call = compute_sortino_ratio(
        result, timeframe=TIMEFRAME, minimum_acceptable_return_per_period=Decimal(0)
    )
    assert default_call == explicit_call


def test_sortino_rejects_non_decimal_target_type():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1050), Decimal(950), Decimal(1100)),
    )
    with pytest.raises(TypeError, match="minimum_acceptable_return_per_period must be a Decimal"):
        compute_sortino_ratio(result, timeframe=TIMEFRAME, minimum_acceptable_return_per_period=0.0)


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_sortino_rejects_non_finite_target(bad):
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1050), Decimal(950), Decimal(1100)),
    )
    with pytest.raises(ValueError, match="minimum_acceptable_return_per_period must be finite"):
        compute_sortino_ratio(result, timeframe=TIMEFRAME, minimum_acceptable_return_per_period=bad)


def test_sortino_requires_at_least_two_returns():
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(1000))
    with pytest.raises(ValueError, match="at least two periodic returns"):
        compute_sortino_ratio(result, timeframe=TIMEFRAME)


def test_sortino_downside_denominator_uses_population_n_not_sample_n_minus_1():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1050), Decimal(950), Decimal(1100)),
    )
    returns = compute_periodic_returns(result)
    n = len(returns)
    mean_return = sum(returns, Decimal(0)) / Decimal(n)
    downside_squared_sum = Decimal(0)
    for periodic_return in returns:
        deviation = periodic_return - Decimal(0)
        downside_squared_sum += min(Decimal(0), deviation) ** 2
    downside_deviation_population = (downside_squared_sum / Decimal(n)).sqrt()
    downside_deviation_sample = (downside_squared_sum / Decimal(n - 1)).sqrt()
    assert downside_deviation_population != downside_deviation_sample

    expected = (mean_return / downside_deviation_population) * PERIODS_PER_YEAR_1H.sqrt()
    assert compute_sortino_ratio(result, timeframe="1h") == expected


def test_sortino_mixed_upside_and_downside_observations():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1050), Decimal(950), Decimal(1100)),
    )
    sortino = compute_sortino_ratio(result, timeframe=TIMEFRAME)
    assert isinstance(sortino, Decimal)


def test_sortino_no_downside_observations_is_rejected():
    # every periodic return is positive -> downside deviation is zero.
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1210),
        equity_curve=_curve(Decimal(1100), Decimal(1210)),
    )
    with pytest.raises(ValueError, match="downside_deviation must be finite and greater than zero"):
        compute_sortino_ratio(result, timeframe=TIMEFRAME)


def test_sortino_boundary_equality_at_target_is_rejected():
    # every periodic return exactly equals the target -> deviation is zero
    # for every observation -> downside deviation is zero.
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1000),
        equity_curve=_curve(Decimal(1000), Decimal(1000)),
    )
    with pytest.raises(ValueError, match="downside_deviation must be finite and greater than zero"):
        compute_sortino_ratio(result, timeframe=TIMEFRAME)


def test_sortino_negative_numerator_is_legal():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(810),
        equity_curve=_curve(Decimal(900), Decimal(810)),
    )
    sortino = compute_sortino_ratio(result, timeframe=TIMEFRAME)
    assert sortino < Decimal(0)


def test_sortino_annualization_same_mechanism_as_sharpe():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(810),
        equity_curve=_curve(Decimal(900), Decimal(810)),
    )
    sortino_1h = compute_sortino_ratio(result, timeframe="1h")
    sortino_4h = compute_sortino_ratio(result, timeframe="4h")
    ratio = sortino_1h / sortino_4h
    assert ratio == PERIODS_PER_YEAR_1H.sqrt() / PERIODS_PER_YEAR_4H.sqrt()


def test_sortino_non_finite_output_rejected():
    result = _result(
        initial_cash=Decimal("1E-500000"),
        final_equity=Decimal("1E-500000"),
        equity_curve=_curve(Decimal("1E-500000"), Decimal("1E+500000"), Decimal("1E-500000")),
    )
    with pytest.raises(ValueError):
        compute_sortino_ratio(result, timeframe=TIMEFRAME)


# =====================================================================
# CAGR (criteria 18, 19, 20, 21, 22, 23)
# =====================================================================


def test_cagr_reuses_stage1_total_return_never_recomputes_independently():
    source = inspect.getsource(compute_cagr)
    assert "compute_stage1_metrics" in source
    assert "result.final_equity" not in source
    assert "result.initial_cash" not in source


def test_cagr_exact_formula_and_operation_order():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1200),
        equity_curve=_curve(Decimal(1100), Decimal(900), Decimal(1200)),
    )
    stage1 = compute_stage1_metrics(result)
    n = len(result.equity_curve)
    base = Decimal(1) + stage1.total_return
    exponent = PERIODS_PER_YEAR_1H / Decimal(n)
    expected = base**exponent - Decimal(1)
    assert compute_cagr(result, timeframe="1h") == expected


def test_cagr_n_equals_len_of_equity_curve():
    result_short = _result(
        initial_cash=Decimal(1000), final_equity=Decimal(1100), equity_curve=_curve(Decimal(1100))
    )
    result_long = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1050), Decimal(1075), Decimal(1100)),
    )
    cagr_short = compute_cagr(result_short, timeframe="1h")
    cagr_long = compute_cagr(result_long, timeframe="1h")
    assert cagr_short != cagr_long


def test_cagr_succeeds_with_single_equity_point_where_sharpe_sortino_reject():
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(1050))
    cagr = compute_cagr(result, timeframe=TIMEFRAME)
    assert isinstance(cagr, Decimal)
    with pytest.raises(ValueError, match="at least two periodic returns"):
        compute_annualized_sharpe_ratio(result, timeframe=TIMEFRAME)
    with pytest.raises(ValueError, match="at least two periodic returns"):
        compute_sortino_ratio(result, timeframe=TIMEFRAME)


def test_cagr_flat_equity_is_exact_zero():
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(1000))
    assert compute_cagr(result, timeframe=TIMEFRAME) == Decimal(0)


def test_cagr_total_wipeout_is_exact_negative_one():
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(0))
    assert compute_cagr(result, timeframe=TIMEFRAME) == Decimal(-1)


def test_cagr_negative_final_equity_below_negative_one_total_return_rejected():
    # 7 equity points -> periods_per_year / n = 8760 / 7 is a fractional
    # (non-integer) exponent, so a negative base produces NaN rather than
    # the integer-exponent special case.
    curve = (
        _point(Decimal(1000), time=T0),
        _point(Decimal(1000), time=T0 + timedelta(hours=1)),
        _point(Decimal(1000), time=T0 + timedelta(hours=2)),
        _point(Decimal(1000), time=T0 + timedelta(hours=3)),
        _point(Decimal(1000), time=T0 + timedelta(hours=4)),
        _point(Decimal(1000), time=T0 + timedelta(hours=5)),
        _point(Decimal(-1000), time=T0 + timedelta(hours=6)),
    )
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(-1000), equity_curve=curve)
    stage1 = compute_stage1_metrics(result)
    assert stage1.total_return < Decimal(-1)
    with pytest.raises(ValueError, match="computed CAGR must be finite"):
        compute_cagr(result, timeframe="1h")


def test_cagr_ignores_equity_curve_timestamps():
    tight_curve = _curve(
        Decimal(1010), Decimal(1020), Decimal(1030), start=T0, step=timedelta(hours=1)
    )
    wide_curve = _curve(
        Decimal(1010), Decimal(1020), Decimal(1030), start=T0, step=timedelta(hours=500)
    )
    tight_result = _result(
        initial_cash=Decimal(1000), final_equity=Decimal(1030), equity_curve=tight_curve
    )
    wide_result = _result(
        initial_cash=Decimal(1000), final_equity=Decimal(1030), equity_curve=wide_curve
    )
    assert compute_cagr(tight_result, timeframe="1h") == compute_cagr(wide_result, timeframe="1h")


def test_cagr_overflow_is_rejected():
    result = _result(
        initial_cash=Decimal(1),
        final_equity=Decimal("1E+1000"),
        equity_curve=(_point(Decimal("1E+1000")),),
    )
    with pytest.raises(ValueError, match="computed CAGR must be finite"):
        compute_cagr(result, timeframe=TIMEFRAME)


# =====================================================================
# Calmar ratio (criteria 24, 25, 26)
# =====================================================================


def test_calmar_reuses_cagr_and_stage1_max_drawdown():
    source = inspect.getsource(compute_calmar_ratio)
    assert "compute_cagr" in source
    assert "compute_stage1_metrics" in source


def test_calmar_propagates_undefined_cagr_error_unchanged():
    curve = (
        _point(Decimal(1000), time=T0),
        _point(Decimal(1000), time=T0 + timedelta(hours=1)),
        _point(Decimal(1000), time=T0 + timedelta(hours=2)),
        _point(Decimal(1000), time=T0 + timedelta(hours=3)),
        _point(Decimal(1000), time=T0 + timedelta(hours=4)),
        _point(Decimal(1000), time=T0 + timedelta(hours=5)),
        _point(Decimal(-1000), time=T0 + timedelta(hours=6)),
    )
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(-1000), equity_curve=curve)
    with pytest.raises(ValueError, match="computed CAGR must be finite"):
        compute_calmar_ratio(result, timeframe="1h")


def test_calmar_exact_formula():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1200),
        equity_curve=_curve(Decimal(1100), Decimal(900), Decimal(1200)),
    )
    cagr = compute_cagr(result, timeframe=TIMEFRAME)
    max_drawdown = compute_stage1_metrics(result).max_drawdown
    expected = cagr / max_drawdown
    assert compute_calmar_ratio(result, timeframe=TIMEFRAME) == expected


def test_calmar_zero_drawdown_is_rejected_never_infinity():
    # monotonically non-decreasing equity curve -> zero max drawdown.
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1200),
        equity_curve=_curve(Decimal(1000), Decimal(1100), Decimal(1200)),
    )
    assert compute_stage1_metrics(result).max_drawdown == Decimal(0)
    with pytest.raises(ValueError, match="computed Calmar ratio must be finite"):
        compute_calmar_ratio(result, timeframe=TIMEFRAME)


def test_calmar_negative_cagr_is_legal():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(900),
        equity_curve=_curve(Decimal(950), Decimal(850), Decimal(900)),
    )
    calmar = compute_calmar_ratio(result, timeframe=TIMEFRAME)
    assert calmar < Decimal(0)


def test_calmar_max_drawdown_equal_to_one():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(500),
        equity_curve=_curve(Decimal(1000), Decimal(0), Decimal(500)),
    )
    assert compute_stage1_metrics(result).max_drawdown == Decimal(1)
    calmar = compute_calmar_ratio(result, timeframe=TIMEFRAME)
    assert isinstance(calmar, Decimal)


def test_calmar_max_drawdown_greater_than_one():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(500),
        equity_curve=_curve(Decimal(1000), Decimal(-200), Decimal(500)),
    )
    assert compute_stage1_metrics(result).max_drawdown > Decimal(1)
    calmar = compute_calmar_ratio(result, timeframe=TIMEFRAME)
    assert isinstance(calmar, Decimal)


# =====================================================================
# Decimal-context determinism (criteria 27, 28)
# =====================================================================


def test_sharpe_independent_of_low_ambient_precision():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1100), Decimal(1210), Decimal(1100)),
    )
    default = compute_annualized_sharpe_ratio(result, timeframe="1h")
    with localcontext() as ctx:
        ctx.prec = 3
        low_precision = compute_annualized_sharpe_ratio(result, timeframe="1h")
    assert low_precision == default
    assert len(low_precision.as_tuple().digits) >= 20


def test_sortino_independent_of_high_ambient_precision():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(810),
        equity_curve=_curve(Decimal(900), Decimal(810)),
    )
    default = compute_sortino_ratio(result, timeframe="1h")
    with localcontext() as ctx:
        ctx.prec = 200
        high_precision = compute_sortino_ratio(result, timeframe="1h")
    assert high_precision == default


def test_cagr_independent_of_ambient_rounding_mode():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1200),
        equity_curve=_curve(Decimal(1100), Decimal(900), Decimal(1200)),
    )
    default = compute_cagr(result, timeframe="1h")
    from decimal import ROUND_DOWN

    with localcontext() as ctx:
        ctx.rounding = ROUND_DOWN
        ctx.prec = 5
        rounded_down = compute_cagr(result, timeframe="1h")
    assert rounded_down == default


def test_calmar_deterministic_after_ambient_context_mutation():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1200),
        equity_curve=_curve(Decimal(1100), Decimal(900), Decimal(1200)),
    )
    first = compute_calmar_ratio(result, timeframe="1h")
    with localcontext() as ctx:
        ctx.prec = 2
        compute_calmar_ratio(result, timeframe="1h")
    second = compute_calmar_ratio(result, timeframe="1h")
    assert first == second


# =====================================================================
# Purity, determinism, no mutation (criterion 29)
# =====================================================================


def test_functions_do_not_mutate_input_result():
    equity_curve = _curve(Decimal(1100), Decimal(900), Decimal(1200))
    result = _result(
        initial_cash=Decimal(1000), final_equity=Decimal(1200), equity_curve=equity_curve
    )
    before = result
    compute_annualized_sharpe_ratio(result, timeframe="1h")
    compute_sortino_ratio(result, timeframe="1h")
    compute_cagr(result, timeframe="1h")
    compute_calmar_ratio(result, timeframe="1h")
    assert result == before
    assert result.equity_curve == equity_curve


def test_deterministic_repeated_calls():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1200),
        equity_curve=_curve(Decimal(1100), Decimal(900), Decimal(1200)),
    )
    assert compute_annualized_sharpe_ratio(
        result, timeframe="1h"
    ) == compute_annualized_sharpe_ratio(result, timeframe="1h")
    assert compute_sortino_ratio(result, timeframe="1h") == compute_sortino_ratio(
        result, timeframe="1h"
    )
    assert compute_cagr(result, timeframe="1h") == compute_cagr(result, timeframe="1h")
    assert compute_calmar_ratio(result, timeframe="1h") == compute_calmar_ratio(
        result, timeframe="1h"
    )


def test_no_cross_window_aggregation_between_independent_results():
    result_a = _result(initial_cash=Decimal(1000), final_equity=Decimal(1200))
    result_b = _result(initial_cash=Decimal(1000), final_equity=Decimal(900))
    cagr_a_first = compute_cagr(result_a, timeframe="1h")
    cagr_b_first = compute_cagr(result_b, timeframe="1h")
    cagr_b_second = compute_cagr(result_b, timeframe="1h")
    cagr_a_second = compute_cagr(result_a, timeframe="1h")
    assert cagr_a_first == cagr_a_second
    assert cagr_b_first == cagr_b_second
    assert cagr_a_first != cagr_b_first


# =====================================================================
# Compatibility: direct BacktestResult, independent WindowResult.result,
# and real canonical backtest integration (criterion 30)
# =====================================================================


def test_all_four_functions_work_on_a_real_canonical_backtest_result(tmp_path):
    prices = [Decimal(100), Decimal(110), Decimal(100), Decimal(120)]
    store = _candle_store(tmp_path, prices=prices)
    policy = _RecordingPolicy(lambda context: PositionTarget.LONG)
    result = _run(store, policy=policy, prices=prices)

    sharpe = compute_annualized_sharpe_ratio(result, timeframe=TIMEFRAME)
    sortino = compute_sortino_ratio(result, timeframe=TIMEFRAME)
    cagr = compute_cagr(result, timeframe=TIMEFRAME)
    calmar = compute_calmar_ratio(result, timeframe=TIMEFRAME)
    for value in (sharpe, sortino, cagr, calmar):
        assert isinstance(value, Decimal)


def test_all_four_functions_work_on_independent_window_result(tmp_path):
    prices = [
        Decimal(100),
        Decimal(110),
        Decimal(100),
        Decimal(90),
        Decimal(100),
        Decimal(130),
        Decimal(90),
        Decimal(150),
    ]
    store = _candle_store(tmp_path, prices=prices)
    windows = (
        TemporalWindow(start=T0, end=T0 + timedelta(hours=4)),
        TemporalWindow(start=T0 + timedelta(hours=4), end=T0 + timedelta(hours=8)),
    )
    rolling_result = run_rolling_backtest_from_store(
        store,
        windows,
        policy_factory=lambda: _RecordingPolicy(lambda context: PositionTarget.LONG),
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        as_of_time=AS_OF_TIME,
        config=_config(),
        cost_model=ZeroCostModel(),
    )
    assert len(rolling_result) == 2
    for window_result in rolling_result:
        assert isinstance(window_result, WindowResult)
        cagr = compute_cagr(window_result.result, timeframe=TIMEFRAME)
        assert isinstance(cagr, Decimal)
