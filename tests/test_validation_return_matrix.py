import dataclasses
import inspect
import typing
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

import crypto_quant_lab.validation as validation_package
import crypto_quant_lab.validation.annualized_metrics as annualized_metrics_module
import crypto_quant_lab.validation.candidate as candidate_module
import crypto_quant_lab.validation.deflated_sharpe as deflated_sharpe_module
import crypto_quant_lab.validation.metrics as metrics_module
import crypto_quant_lab.validation.purging as purging_module
import crypto_quant_lab.validation.return_matrix as return_matrix_module
import crypto_quant_lab.validation.rolling as rolling_module
import crypto_quant_lab.validation.trial_group as trial_group_module
import crypto_quant_lab.validation.windows as windows_module
from crypto_quant_lab.backtest.costs import ZeroCostModel
from crypto_quant_lab.backtest.models import (
    BacktestConfig,
    BacktestResult,
    EquityPoint,
    PositionTarget,
)
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.base import HistoricalCandle
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore
from crypto_quant_lab.validation.candidate import Candidate, Trial
from crypto_quant_lab.validation.metrics import compute_periodic_returns
from crypto_quant_lab.validation.return_matrix import (
    TrialReturnMatrix,
    build_trial_return_matrix,
)
from crypto_quant_lab.validation.rolling import WindowResult, run_rolling_backtest_from_store
from crypto_quant_lab.validation.trial_group import TrialGroup
from crypto_quant_lab.validation.windows import TemporalWindow

START = datetime(2024, 1, 1, tzinfo=UTC)
EXCHANGE = "binance"
MARKET_TYPE = "usdm_perp"
SYMBOL = "BTCUSDT"
TIMEFRAME = "1h"
AS_OF_TIME = datetime(2024, 1, 3, tzinfo=UTC)
HOUR = timedelta(hours=1)


def _window(start_hour, end_hour):
    return TemporalWindow(start=START + HOUR * start_hour, end=START + HOUR * end_hour)


def _result(first_hour, equities, *, initial_cash=Decimal(1000)):
    # like replay, each point is stamped at its candle's close (open + 1h)
    points = tuple(
        EquityPoint(time=START + HOUR * (first_hour + 1 + index), equity=Decimal(value))
        for index, value in enumerate(equities)
    )
    final = points[-1].equity if points else initial_cash
    return BacktestResult(
        initial_cash=initial_cash,
        final_cash=final,
        final_equity=final,
        total_realized_pnl=Decimal(0),
        total_unrealized_pnl=Decimal(0),
        total_pnl=Decimal(0),
        total_cost=Decimal(0),
        fill_count=0,
        trade_count=0,
        equity_curve=points,
    )


def _trial(candidate_id, results):
    return Trial(
        candidate=Candidate(candidate_id=candidate_id, parameters=()),
        results=results,
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        as_of_time=AS_OF_TIME,
        config=BacktestConfig(initial_cash=Decimal(1000), position_quantity=Decimal(1)),
    )


# Two windows with a one-hour gap between them: [0, 3) and [4, 6).
W0 = _window(0, 3)
W1 = _window(4, 6)
CURVES = {
    "a": (("1010", "1000", "1030"), ("990", "1000")),
    "b": (("1000", "1020", "1020"), ("1005", "1010")),
}


def _group(curves=None, windows=(W0, W1), first_hours=(0, 4)):
    curves = CURVES if curves is None else curves
    trials = tuple(
        _trial(
            candidate_id,
            tuple(
                WindowResult(window=window, result=_result(first_hour, per_window))
                for window, first_hour, per_window in zip(
                    windows, first_hours, per_window_curves, strict=True
                )
            ),
        )
        for candidate_id, per_window_curves in curves.items()
    )
    return TrialGroup(group_id="g", trials=trials)


def _raises_exactly(exception_type, message, callable_, *args, **kwargs):
    with pytest.raises(exception_type) as excinfo:
        callable_(*args, **kwargs)
    assert type(excinfo.value) is exception_type
    assert str(excinfo.value) == message


def _matrix(**overrides):
    kwargs = {
        "candidate_ids": ("a", "b"),
        "observation_times": (START, START + HOUR, START + HOUR * 4),
        "window_indices": (0, 0, 1),
        "returns": (
            (Decimal("0.01"), Decimal(0)),
            (Decimal("-0.01"), Decimal("0.02")),
            (Decimal(0), Decimal("0.005")),
        ),
    }
    kwargs.update(overrides)
    return TrialReturnMatrix(**kwargs)


# ================================================================
# API / scope (criteria 1, 2, 20, 21)
# ================================================================


def test_public_symbols_are_exactly_the_locked_api():
    public = {name for name in dir(return_matrix_module) if not name.startswith("_")}
    assert public == {"TrialReturnMatrix", "build_trial_return_matrix"}


def test_value_object_shape_is_locked():
    assert [field.name for field in dataclasses.fields(TrialReturnMatrix)] == [
        "candidate_ids",
        "observation_times",
        "window_indices",
        "returns",
    ]
    assert typing.get_type_hints(TrialReturnMatrix) == {
        "candidate_ids": tuple[str, ...],
        "observation_times": tuple[datetime, ...],
        "window_indices": tuple[int, ...],
        "returns": tuple[tuple[Decimal, ...], ...],
    }
    params = TrialReturnMatrix.__dataclass_params__
    assert params.frozen is True and params.eq is True and params.order is False
    matrix = _matrix()
    assert not hasattr(matrix, "__dict__")
    with pytest.raises(FrozenInstanceError):
        matrix.returns = ()


def test_builder_signature_is_locked():
    signature = inspect.signature(build_trial_return_matrix)
    assert list(signature.parameters) == ["group"]
    assert typing.get_type_hints(build_trial_return_matrix) == {
        "group": TrialGroup,
        "return": TrialReturnMatrix,
    }


def test_not_exported_at_package_root():
    assert not hasattr(validation_package, "TrialReturnMatrix")
    assert not hasattr(validation_package, "build_trial_return_matrix")


def test_module_defines_no_pbo_selection_or_purging_symbol():
    forbidden = {
        "pbo",
        "compute_pbo",
        "cscv",
        "partition",
        "select",
        "rank",
        "logit",
        "best",
        "purge",
        "fill",
        "interpolate",
        "correlation",
    }
    names = {name.lower() for name in dir(return_matrix_module)}
    assert not (forbidden & names)


def _import_lines(module):
    return [
        line.strip()
        for line in inspect.getsource(module).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]


def test_import_direction_is_locked():
    modules = {line.split()[1] for line in _import_lines(return_matrix_module)}
    assert modules == {
        "dataclasses",
        "datetime",
        "decimal",
        "crypto_quant_lab.storage.sqlite_codec",
        "crypto_quant_lab.validation.metrics",
        "crypto_quant_lab.validation.trial_group",
    }
    source = inspect.getsource(return_matrix_module)
    for forbidden in (
        "float(",
        "import random",
        "import time",
        "_compute_periodic_returns_unchecked",
    ):
        assert forbidden not in source


@pytest.mark.parametrize(
    "module",
    [
        candidate_module,
        trial_group_module,
        metrics_module,
        annualized_metrics_module,
        deflated_sharpe_module,
        rolling_module,
        windows_module,
        purging_module,
        validation_package,
    ],
    ids=lambda module: module.__name__,
)
def test_no_existing_module_imports_return_matrix(module):
    for line in _import_lines(module):
        assert "return_matrix" not in line


# ================================================================
# TrialReturnMatrix structural invariants (criteria 3-7)
# ================================================================


def test_valid_matrix_constructs():
    matrix = _matrix()
    assert matrix.candidate_ids == ("a", "b")
    assert matrix.returns[1][1] == Decimal("0.02")


@pytest.mark.parametrize(
    ("overrides", "error", "message"),
    [
        ({"candidate_ids": ["a", "b"]}, TypeError, "candidate_ids must be a tuple, got list"),
        ({"candidate_ids": ()}, ValueError, "candidate_ids must not be empty"),
        ({"candidate_ids": ("a", 2)}, TypeError, "candidate_ids[1] must be a str, got int"),
        (
            {"candidate_ids": ("a", "a")},
            ValueError,
            "candidate_ids[1] 'a' duplicates candidate_ids[0]",
        ),
        ({"observation_times": [START]}, TypeError, "observation_times must be a tuple, got list"),
        ({"observation_times": ()}, ValueError, "observation_times must not be empty"),
        (
            {"observation_times": (START, "x", START)},
            TypeError,
            "observation_times[1] must be a datetime, got str",
        ),
        (
            {"observation_times": (START, START + HOUR, START + HOUR)},
            ValueError,
            "observation_times[2] must be after observation_times[1]",
        ),
        ({"window_indices": [0, 0, 1]}, TypeError, "window_indices must be a tuple, got list"),
        ({"window_indices": (0, 0)}, ValueError, "window_indices must have 3 entries, got 2"),
        ({"window_indices": (0, True, 1)}, TypeError, "window_indices[1] must be an int, got bool"),
        ({"window_indices": (1, 1, 2)}, ValueError, "window_indices[0] must be 0, got 1"),
        (
            {"window_indices": (0, 2, 2)},
            ValueError,
            "window_indices[1] must equal window_indices[0] or window_indices[0] + 1, got 2",
        ),
        (
            {"window_indices": (0, 1, 0)},
            ValueError,
            "window_indices[2] must equal window_indices[1] or window_indices[1] + 1, got 0",
        ),
        ({"returns": [()]}, TypeError, "returns must be a tuple, got list"),
        ({"returns": ((Decimal(0), Decimal(0)),)}, ValueError, "returns must have 3 rows, got 1"),
    ],
)
def test_structural_invariants_have_exact_messages(overrides, error, message):
    _raises_exactly(error, message, _matrix, **overrides)


def test_naive_observation_time_propagates_codec_error_unchanged():
    _raises_exactly(
        ValueError,
        "value must be timezone-aware",
        _matrix,
        observation_times=(START, datetime(2024, 1, 1, 1), START + HOUR * 4),  # noqa: DTZ001
    )


def test_row_and_cell_checks_are_separate_global_passes():
    zero = Decimal(0)
    # row 2 is not a tuple while row 0 has the wrong width: the type pass wins
    _raises_exactly(
        TypeError,
        "returns[2] must be a tuple, got list",
        _matrix,
        returns=((zero,), (zero, zero), [zero, zero]),
    )
    # row 2 has the wrong width while row 0 has a non-Decimal cell: width pass wins
    _raises_exactly(
        ValueError,
        "returns[2] must have 2 columns, got 1",
        _matrix,
        returns=((0.5, zero), (zero, zero), (zero,)),
    )
    # a non-Decimal cell later wins over a non-finite cell earlier
    _raises_exactly(
        TypeError,
        "returns[2][1] must be a Decimal, got float",
        _matrix,
        returns=((Decimal("NaN"), zero), (zero, zero), (zero, 0.5)),
    )
    _raises_exactly(
        ValueError,
        "returns[0][0] must be finite, got NaN",
        _matrix,
        returns=((Decimal("NaN"), zero), (zero, zero), (zero, zero)),
    )


def test_field_order_of_validation_is_enforced():
    # a candidate_ids error wins over every later field error
    _raises_exactly(
        ValueError,
        "candidate_ids must not be empty",
        _matrix,
        candidate_ids=(),
        observation_times=(),
        window_indices=None,
        returns=None,
    )
    # a later str-type violation wins over an earlier duplicate (global passes)
    _raises_exactly(
        TypeError,
        "candidate_ids[2] must be a str, got int",
        _matrix,
        candidate_ids=("a", "a", 3),
    )
    # observation_times error wins over window_indices/returns errors
    _raises_exactly(
        ValueError,
        "observation_times must not be empty",
        _matrix,
        observation_times=(),
        window_indices=None,
        returns=None,
    )
    # window_indices error wins over returns error
    _raises_exactly(
        TypeError,
        "window_indices must be a tuple, got NoneType",
        _matrix,
        window_indices=None,
        returns=None,
    )


# ================================================================
# builder validation (criteria 8-13)
# ================================================================


def test_builder_rejects_non_group():
    _raises_exactly(
        TypeError, "group must be a TrialGroup, got tuple", build_trial_return_matrix, ()
    )


@pytest.mark.parametrize(
    ("windows", "first_hours", "bad_index", "start", "end"),
    [
        pytest.param((_window(0, 3), _window(2, 5)), (0, 2), 1, 2, 3, id="overlap"),
        pytest.param((_window(0, 3), _window(0, 3)), (0, 0), 1, 0, 3, id="duplicate"),
        pytest.param((_window(4, 6), _window(0, 3)), (4, 0), 1, 0, 6, id="reversed"),
    ],
)
def test_builder_rejects_overlapping_duplicate_or_reversed_windows(
    windows, first_hours, bad_index, start, end
):
    curves = {"a": (("1010", "1000"), ("990", "1000"))}
    group = _group(curves, windows=windows, first_hours=first_hours)
    message = (
        "evaluation windows must be chronologically ordered and non-overlapping: "
        f"windows[{bad_index}].start ({START + HOUR * start!r}) is before "
        f"windows[{bad_index - 1}].end ({START + HOUR * end!r})"
    )
    _raises_exactly(ValueError, message, build_trial_return_matrix, group)


def test_builder_accepts_adjacent_windows():
    curves = {"a": (("1010", "1000", "1030"), ("990", "1000", "1002"))}
    group = _group(curves, windows=(_window(0, 3), _window(3, 6)), first_hours=(0, 3))
    matrix = build_trial_return_matrix(group)
    assert matrix.window_indices == (0, 0, 0, 1, 1, 1)


def test_builder_rejects_observation_outside_its_window():
    curves = {"a": (("1010", "1000", "1030", "1040"), ("990", "1000"))}
    group = _group(curves)
    message = (
        "trials[0].results[0].result.equity_curve[3].time "
        f"({START + HOUR * 4!r}) is outside the observation range "
        f"({START!r}, {START + HOUR * 3!r}] of its evaluation window"
    )
    _raises_exactly(ValueError, message, build_trial_return_matrix, group)


def test_builder_rejects_misaligned_observation_times():
    trials = (
        _trial("a", (WindowResult(window=W0, result=_result(0, ("1010", "1000"))),)),
        _trial("b", (WindowResult(window=W0, result=_result(1, ("1010", "1000"))),)),
    )
    _raises_exactly(
        ValueError,
        "trials[1].results[0] equity observation times do not match trials[0].results[0]",
        build_trial_return_matrix,
        TrialGroup(group_id="g", trials=trials),
    )


def test_builder_rejects_missing_observation_instead_of_filling():
    curves = {"a": CURVES["a"], "b": (("1000", "1020"), ("1005", "1010"))}
    _raises_exactly(
        ValueError,
        "trials[1].results[0] equity observation times do not match trials[0].results[0]",
        build_trial_return_matrix,
        _group(curves),
    )


def test_same_instant_times_with_different_tzinfo_are_synchronous():
    plus_two = timezone(timedelta(hours=2))
    shifted = BacktestResult(
        initial_cash=Decimal(1000),
        final_cash=Decimal(1000),
        final_equity=Decimal(1000),
        total_realized_pnl=Decimal(0),
        total_unrealized_pnl=Decimal(0),
        total_pnl=Decimal(0),
        total_cost=Decimal(0),
        fill_count=0,
        trade_count=0,
        equity_curve=(
            EquityPoint(time=datetime(2024, 1, 1, 3, tzinfo=plus_two), equity=Decimal(1010)),
            EquityPoint(time=datetime(2024, 1, 1, 4, tzinfo=plus_two), equity=Decimal(1000)),
        ),
    )
    trials = (
        _trial("a", (WindowResult(window=W0, result=_result(0, ("1005", "1000"))),)),
        _trial("b", (WindowResult(window=W0, result=shifted),)),
    )
    matrix = build_trial_return_matrix(TrialGroup(group_id="g", trials=trials))
    assert matrix.observation_times == (START + HOUR, START + HOUR * 2)
    assert matrix.returns[0][1] == Decimal("0.01")


def test_lower_layer_return_errors_propagate_unchanged():
    curves = {"a": (("1010", "0", "1000"), ("990", "1000"))}
    group = _group(curves)
    with pytest.raises(ValueError) as expected:
        compute_periodic_returns(group.trials[0].results[0].result)
    _raises_exactly(ValueError, str(expected.value), build_trial_return_matrix, group)


def test_empty_equity_curve_error_propagates_unchanged():
    curves = {"a": ((), ("990", "1000"))}
    group = _group(curves)
    with pytest.raises(ValueError) as expected:
        compute_periodic_returns(group.trials[0].results[0].result)
    _raises_exactly(ValueError, str(expected.value), build_trial_return_matrix, group)


def test_builder_validation_order():
    # overlap (step 2) wins over an out-of-window observation (step 3)
    curves = {"a": (("1010", "1000", "1030", "1040"), ("990", "1000"))}
    group = _group(curves, windows=(_window(0, 3), _window(2, 5)), first_hours=(0, 2))
    with pytest.raises(ValueError) as excinfo:
        build_trial_return_matrix(group)
    assert str(excinfo.value).startswith("evaluation windows must be chronologically ordered")
    # out-of-window (step 3) wins over misalignment (step 4)
    curves = {
        "a": (("1010", "1000", "1030", "1040"), ("990", "1000")),
        "b": (("1010", "1000"), ("990", "1000")),
    }
    with pytest.raises(ValueError) as excinfo:
        build_trial_return_matrix(_group(curves))
    assert "is outside the observation range" in str(excinfo.value)
    # misalignment (step 4) wins over a return error in a later trial (step 5)
    curves = {"a": CURVES["a"], "b": (("1000", "0"), ("1005", "1010"))}
    _raises_exactly(
        ValueError,
        "trials[1].results[0] equity observation times do not match trials[0].results[0]",
        build_trial_return_matrix,
        _group(curves),
    )


# ================================================================
# orientation, ordering, cells (criteria 14-19)
# ================================================================


def test_orientation_rows_are_observations_columns_are_trials():
    matrix = build_trial_return_matrix(_group())
    assert matrix.candidate_ids == ("a", "b")
    assert len(matrix.returns) == 5  # 3 + 2 observations, nothing dropped or filled
    assert all(len(row) == 2 for row in matrix.returns)
    assert matrix.observation_times == (
        START + HOUR,
        START + HOUR * 2,
        START + HOUR * 3,
        START + HOUR * 5,
        START + HOUR * 6,
    )
    assert matrix.window_indices == (0, 0, 0, 1, 1)


def test_cells_are_per_window_simple_returns_with_capital_reset():
    matrix = build_trial_return_matrix(_group())
    # hand-computed: each window starts from initial_cash 1000 (Bölüm 11, 15.13)
    expected_a = (
        Decimal("0.01"),
        Decimal(1000) / Decimal(1010) - 1,
        Decimal("0.03"),
        Decimal("-0.01"),
        Decimal(1000) / Decimal(990) - 1,
    )
    expected_b = (
        Decimal(0),
        Decimal("0.02"),
        Decimal(0),
        Decimal("0.005"),
        Decimal(1010) / Decimal(1005) - 1,
    )
    assert tuple(row[0] for row in matrix.returns) == expected_a
    assert tuple(row[1] for row in matrix.returns) == expected_b


def test_cells_equal_existing_periodic_returns_for_every_window():
    group = _group()
    matrix = build_trial_return_matrix(group)
    for column, trial in enumerate(group.trials):
        expected = tuple(
            value
            for window_result in trial.results
            for value in compute_periodic_returns(window_result.result)
        )
        assert tuple(row[column] for row in matrix.returns) == expected


def test_column_order_follows_group_order_without_ranking():
    reversed_curves = {"b": CURVES["b"], "a": CURVES["a"]}
    matrix = build_trial_return_matrix(_group(reversed_curves))
    assert matrix.candidate_ids == ("b", "a")
    assert matrix.returns[0] == (Decimal(0), Decimal("0.01"))


def test_observation_times_reuse_reference_trial_objects():
    group = _group()
    matrix = build_trial_return_matrix(group)
    first = group.trials[0].results[0].result.equity_curve[0].time
    assert matrix.observation_times[0] is first


def test_single_trial_and_single_window_groups_are_supported():
    group = _group({"a": (("1010", "1000", "1030"),)}, windows=(W0,), first_hours=(0,))
    matrix = build_trial_return_matrix(group)
    assert matrix.candidate_ids == ("a",)
    assert matrix.window_indices == (0, 0, 0)


def test_no_mutation_and_determinism():
    group = _group()
    snapshot = hash(group)
    first = build_trial_return_matrix(group)
    second = build_trial_return_matrix(group)
    assert first == second
    assert hash(first) == hash(second)
    assert hash(group) == snapshot


# ================================================================
# real SQLite / rolling integration (criterion 22)
# ================================================================

PRICES = (
    "100", "102", "99", "103", "101", "106", "104", "108", "103", "107",
    "110", "105", "109", "112", "108", "113", "111", "116", "112", "118",
)  # fmt: skip


class _LongPolicy:
    def target_position(self, context):
        return PositionTarget.LONG


class _ShortPolicy:
    def target_position(self, context):
        return PositionTarget.SHORT


class _AlternatingPolicy:
    def target_position(self, context):
        return PositionTarget.LONG if len(context.candles) % 2 else PositionTarget.SHORT


def _candle(hour, price):
    candle = Candle(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        open_time=START + HOUR * hour,
        open=Decimal(price),
        high=Decimal(price),
        low=Decimal(price),
        close=Decimal(price),
        volume=Decimal(1),
    )
    return HistoricalCandle(exchange=EXCHANGE, market_type=MARKET_TYPE, candle=candle)


def test_real_sqlite_rolling_multi_window_integration(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "candles.db")
    try:
        store.write_batch([_candle(hour, price) for hour, price in enumerate(PRICES)])
        windows = (_window(0, 6), _window(8, 14), _window(14, 20))
        trials = []
        for candidate_id, policy in (
            ("long", _LongPolicy),
            ("short", _ShortPolicy),
            ("alternating", _AlternatingPolicy),
        ):
            results = run_rolling_backtest_from_store(
                store,
                windows,
                policy_factory=policy,
                exchange=EXCHANGE,
                market_type=MARKET_TYPE,
                symbol=SYMBOL,
                timeframe=TIMEFRAME,
                as_of_time=AS_OF_TIME,
                config=BacktestConfig(initial_cash=Decimal(1000), position_quantity=Decimal(1)),
                cost_model=ZeroCostModel(),
            )
            trials.append(_trial(candidate_id, results))
        group = TrialGroup(group_id="rolling", trials=tuple(trials))

        matrix = build_trial_return_matrix(group)

        assert matrix.candidate_ids == ("long", "short", "alternating")
        expected_times = tuple(
            point.time for result in trials[0].results for point in result.result.equity_curve
        )
        assert matrix.observation_times == expected_times
        assert len(matrix.returns) == len(expected_times)
        assert set(matrix.window_indices) == {0, 1, 2}
        for column, trial in enumerate(trials):
            expected = tuple(
                value
                for window_result in trial.results
                for value in compute_periodic_returns(window_result.result)
            )
            assert tuple(row[column] for row in matrix.returns) == expected
        # the gap between windows 0 and 1 stays visible in the row timestamps
        gap_rows = [
            index
            for index in range(1, len(matrix.window_indices))
            if matrix.window_indices[index] != matrix.window_indices[index - 1]
        ]
        first_gap = gap_rows[0]
        assert (
            matrix.observation_times[first_gap] - matrix.observation_times[first_gap - 1]
        ) > HOUR
    finally:
        store.close()
