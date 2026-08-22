import dataclasses
import inspect
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, tzinfo
from decimal import Decimal

import pytest

import crypto_quant_lab.validation as validation_package
import crypto_quant_lab.validation.candidate as candidate_module
import crypto_quant_lab.validation.metrics as metrics_module
import crypto_quant_lab.validation.rolling as rolling_module
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
from crypto_quant_lab.validation.candidate import Candidate, ParameterValue, Trial
from crypto_quant_lab.validation.metrics import (
    compute_periodic_returns,
    compute_stage1_metrics,
    compute_stage2_metrics,
)
from crypto_quant_lab.validation.rolling import WindowResult, run_rolling_backtest_from_store
from crypto_quant_lab.validation.windows import TemporalWindow


class _BrokenTzInfo(tzinfo):
    """A tzinfo that pretends to be attached but reports no offset (pseudo-naive)."""

    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return None


class _Arbitrary:
    """A plain custom object -- not part of the locked parameter-value domain."""


EXCHANGE = "binance"
MARKET_TYPE = "usdm_perp"
SYMBOL = "BTCUSDT"
TIMEFRAME = "1h"

AS_OF_TIME = datetime(2024, 1, 2, 0, 0, tzinfo=UTC)

W0 = TemporalWindow(
    start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
)
W1 = TemporalWindow(
    start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
)


def _config(initial_cash=Decimal(1000), position_quantity=Decimal(1)):
    return BacktestConfig(initial_cash=initial_cash, position_quantity=position_quantity)


def _result(initial_cash=Decimal(1000), equity_curve=None):
    if equity_curve is None:
        equity_curve = ()
    return BacktestResult(
        initial_cash=initial_cash,
        final_cash=initial_cash,
        final_equity=initial_cash,
        total_realized_pnl=Decimal(0),
        total_unrealized_pnl=Decimal(0),
        total_pnl=Decimal(0),
        total_cost=Decimal(0),
        fill_count=0,
        trade_count=0,
        equity_curve=equity_curve,
    )


def _window_result(window=W0, initial_cash=Decimal(1000)):
    return WindowResult(window=window, result=_result(initial_cash=initial_cash))


def _candidate(candidate_id="cand-1", parameters=()):
    return Candidate(candidate_id=candidate_id, parameters=parameters)


def _trial(**overrides):
    kwargs = {
        "candidate": _candidate(),
        "results": (_window_result(),),
        "exchange": EXCHANGE,
        "market_type": MARKET_TYPE,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "as_of_time": AS_OF_TIME,
        "config": _config(),
    }
    kwargs.update(overrides)
    return Trial(**kwargs)


def _make_candle_record(open_time, *, price=Decimal(100)):
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


def _candle_store(tmp_path, hours=range(8, 12)):
    store = SQLiteHistoricalCandleStore(tmp_path / "candles.db")
    store.write_batch(
        [_make_candle_record(datetime(2024, 1, 1, hour, 0, tzinfo=UTC)) for hour in hours]
    )
    return store


class _FlatPolicy:
    def target_position(self, context):
        return PositionTarget.FLAT


# ================================================================
# Candidate: valid construction / value semantics
# ================================================================


def test_candidate_valid_construction_with_empty_parameters():
    candidate = Candidate(candidate_id="c1", parameters=())
    assert candidate.candidate_id == "c1"
    assert candidate.parameters == ()


def test_candidate_valid_construction_with_multiple_parameters():
    params = (("alpha", Decimal("1.5")), ("beta", 2), ("gamma", "text"))
    candidate = Candidate(candidate_id="c1", parameters=params)
    assert candidate.parameters == params


def test_candidate_field_order_is_locked():
    assert [f.name for f in dataclasses.fields(Candidate)] == ["candidate_id", "parameters"]


def test_candidate_equality_is_value_based():
    a = Candidate(candidate_id="c1", parameters=(("k", 1),))
    b = Candidate(candidate_id="c1", parameters=(("k", 1),))
    assert a == b
    assert hash(a) == hash(b)


def test_candidate_inequality_for_different_ids_same_parameters():
    a = Candidate(candidate_id="c1", parameters=(("k", 1),))
    b = Candidate(candidate_id="c2", parameters=(("k", 1),))
    assert a != b


def test_candidate_is_hashable_as_set_member_and_dict_key():
    a = Candidate(candidate_id="c1", parameters=(("k", 1),))
    b = Candidate(candidate_id="c1", parameters=(("k", 1),))
    members = {a}
    assert b in members
    mapping = {a: "value"}
    assert mapping[b] == "value"


def test_candidate_is_frozen():
    candidate = Candidate(candidate_id="c1", parameters=())
    with pytest.raises(FrozenInstanceError):
        candidate.candidate_id = "c2"


def test_candidate_is_slotted():
    candidate = Candidate(candidate_id="c1", parameters=())
    assert not hasattr(candidate, "__dict__")


def test_candidate_deterministic_repeated_construction():
    kwargs = {"candidate_id": "c1", "parameters": (("k", 1),)}
    first = Candidate(**kwargs)
    second = Candidate(**kwargs)
    assert first == second
    assert hash(first) == hash(second)


def test_candidate_id_is_case_sensitive_and_not_normalized():
    lower = Candidate(candidate_id="abc", parameters=())
    upper = Candidate(candidate_id="ABC", parameters=())
    assert lower != upper
    assert lower.candidate_id == "abc"


# ================================================================
# Candidate: candidate_id validation
# ================================================================


def test_candidate_id_wrong_type_is_rejected():
    with pytest.raises(TypeError, match="candidate_id"):
        Candidate(candidate_id=123, parameters=())


@pytest.mark.parametrize("bad_id", ["", "   ", " c1", "c1 ", "\tc1", "c1\n"])
def test_candidate_id_empty_whitespace_or_padded_is_rejected(bad_id):
    with pytest.raises(ValueError, match="candidate_id"):
        Candidate(candidate_id=bad_id, parameters=())


def test_candidate_id_type_error_precedes_parameters_type_error():
    with pytest.raises(TypeError, match="candidate_id"):
        Candidate(candidate_id=123, parameters="not a tuple")


# ================================================================
# Candidate: parameters structural validation
# ================================================================


def test_candidate_non_tuple_parameters_is_rejected():
    with pytest.raises(TypeError, match="parameters"):
        Candidate(candidate_id="c1", parameters=[("k", 1)])


def test_candidate_invalid_parameter_entry_at_index_0_is_rejected():
    with pytest.raises(TypeError, match=r"parameters\[0\]"):
        Candidate(candidate_id="c1", parameters=("not a tuple",))


def test_candidate_invalid_parameter_entry_at_later_index_is_rejected():
    with pytest.raises(TypeError, match=r"parameters\[1\]"):
        Candidate(candidate_id="c1", parameters=(("a", 1), "not a tuple"))


@pytest.mark.parametrize("entry", [("only_one",), ("a", 1, "extra")])
def test_candidate_parameter_entry_wrong_length_is_rejected(entry):
    with pytest.raises(TypeError, match=r"parameters\[0\]"):
        Candidate(candidate_id="c1", parameters=(entry,))


def test_candidate_parameter_key_wrong_type_is_rejected():
    with pytest.raises(TypeError, match=r"parameters\[0\] key"):
        Candidate(candidate_id="c1", parameters=((123, "v"),))


@pytest.mark.parametrize("bad_key", ["", "   ", " a", "a "])
def test_candidate_parameter_key_empty_whitespace_or_padded_is_rejected(bad_key):
    with pytest.raises(ValueError, match=r"parameters\[0\] key"):
        Candidate(candidate_id="c1", parameters=((bad_key, 1),))


def test_candidate_duplicate_key_is_rejected_at_duplicate_index():
    with pytest.raises(ValueError, match=r"parameters\[1\].*duplicate"):
        Candidate(candidate_id="c1", parameters=(("a", 1), ("a", 2)))


def test_candidate_noncanonical_order_is_rejected_at_violation_index():
    with pytest.raises(ValueError, match=r"parameters\[1\]"):
        Candidate(candidate_id="c1", parameters=(("b", 1), ("a", 2)))


def test_candidate_parameters_are_not_silently_sorted_or_case_folded():
    with pytest.raises(ValueError):
        Candidate(candidate_id="c1", parameters=(("b", 1), ("a", 2)))
    candidate = Candidate(candidate_id="c1", parameters=(("A", 1), ("a", 2)))
    assert candidate.parameters == (("A", 1), ("a", 2))


# ================================================================
# Candidate: parameter value domain
# ================================================================


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        0,
        -5,
        10**30,
        Decimal("1.23"),
        Decimal(0),
        "",
        "   ",
        "text",
        None,
        (),
        (1, "a", None),
        (1, (2, 3), "nested"),
    ],
)
def test_candidate_accepts_all_legal_parameter_value_types(value):
    candidate = Candidate(candidate_id="c1", parameters=(("k", value),))
    assert candidate.parameters[0][1] == value


def test_candidate_bool_value_is_accepted_under_its_own_branch_before_int():
    candidate = Candidate(candidate_id="c1", parameters=(("flag", True),))
    assert candidate.parameters[0][1] is True
    assert type(candidate.parameters[0][1]) is bool


@pytest.mark.parametrize("value", [1.5, 0.0, -2.25])
def test_candidate_rejects_float_parameter_value(value):
    with pytest.raises(TypeError, match=r"parameters\[0\]"):
        Candidate(candidate_id="c1", parameters=(("k", value),))


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_candidate_rejects_non_finite_decimal_parameter_value(value):
    with pytest.raises(ValueError, match=r"parameters\[0\]"):
        Candidate(candidate_id="c1", parameters=(("k", value),))


@pytest.mark.parametrize("value", [[1, 2], {"a": 1}, {1, 2}])
def test_candidate_rejects_mutable_container_parameter_value(value):
    with pytest.raises(TypeError, match=r"parameters\[0\]"):
        Candidate(candidate_id="c1", parameters=(("k", value),))


def test_candidate_rejects_arbitrary_object_parameter_value():
    with pytest.raises(TypeError, match=r"parameters\[0\]"):
        Candidate(candidate_id="c1", parameters=(("k", _Arbitrary()),))


def test_candidate_rejects_nested_float_parameter_value():
    with pytest.raises(TypeError, match=r"parameters\[0\]"):
        Candidate(candidate_id="c1", parameters=(("k", (1, 2.5)),))


def test_candidate_rejects_nested_mutable_container_parameter_value():
    with pytest.raises(TypeError, match=r"parameters\[0\]"):
        Candidate(candidate_id="c1", parameters=(("k", (1, [2, 3])),))


def test_candidate_nested_invalid_value_reports_top_level_index_not_nested_index():
    with pytest.raises(TypeError, match=r"parameters\[1\]"):
        Candidate(candidate_id="c1", parameters=(("a", 1), ("b", (1, 2, _Arbitrary()))))


# ================================================================
# Candidate: multiple simultaneous violations / exact fail-fast order
# ================================================================


def test_candidate_structural_violation_takes_priority_over_earlier_value_violation():
    # index 0 would fail value-domain validation (float) in the value phase,
    # but index 1 fails key-type validation in the structural phase -- the
    # structural phase runs to completion over every entry before the value
    # phase ever begins, so the index-1 TypeError wins.
    with pytest.raises(TypeError, match=r"parameters\[1\]"):
        Candidate(candidate_id="c1", parameters=(("a", 1.5), (123, "v")))


def test_candidate_earliest_index_wins_among_multiple_structural_violations():
    with pytest.raises(TypeError, match=r"parameters\[0\]"):
        Candidate(candidate_id="c1", parameters=(("bad", "shape", "too-long"), (123, "v")))


def test_candidate_earliest_value_violation_wins_among_multiple():
    with pytest.raises(TypeError, match=r"parameters\[0\]"):
        Candidate(candidate_id="c1", parameters=(("a", 1.1), ("b", 2.2)))


# ================================================================
# Candidate: purity / no partial construction
# ================================================================


def test_candidate_input_unchanged_after_successful_construction():
    params = (("a", 1), ("b", 2))
    candidate = Candidate(candidate_id="c1", parameters=params)
    assert candidate.parameters == params
    assert candidate.parameters is params


def test_candidate_input_unchanged_after_failed_construction():
    candidate_id = "  padded  "
    sentinel = object()
    outcome = sentinel
    try:
        outcome = Candidate(candidate_id=candidate_id, parameters=())
    except ValueError:
        pass
    assert outcome is sentinel
    assert candidate_id == "  padded  "


# ================================================================
# Trial: valid construction / value semantics
# ================================================================


def test_trial_valid_construction_with_one_result():
    trial = _trial()
    assert trial.results == (_window_result(),)


def test_trial_valid_construction_with_multiple_results():
    results = (_window_result(window=W0), _window_result(window=W1))
    trial = _trial(results=results)
    assert trial.results == results


def test_trial_field_order_is_locked():
    assert [f.name for f in dataclasses.fields(Trial)] == [
        "candidate",
        "results",
        "exchange",
        "market_type",
        "symbol",
        "timeframe",
        "as_of_time",
        "config",
    ]


def test_trial_equality_is_value_based():
    a = _trial()
    b = _trial()
    assert a == b
    assert hash(a) == hash(b)


def test_trial_is_hashable_as_set_member_and_dict_key():
    a = _trial()
    b = _trial()
    members = {a}
    assert b in members


def test_trial_is_frozen():
    trial = _trial()
    with pytest.raises(FrozenInstanceError):
        trial.exchange = "okx"


def test_trial_is_slotted():
    trial = _trial()
    assert not hasattr(trial, "__dict__")


def test_trial_deterministic_repeated_construction():
    kwargs = {
        "candidate": _candidate(),
        "results": (_window_result(),),
        "exchange": EXCHANGE,
        "market_type": MARKET_TYPE,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "as_of_time": AS_OF_TIME,
        "config": _config(),
    }
    first = Trial(**kwargs)
    second = Trial(**kwargs)
    assert first == second
    assert hash(first) == hash(second)


def test_trial_provenance_equality_behavior():
    a = _trial(exchange="binance")
    b = _trial(exchange="binance")
    c = _trial(exchange="okx")
    assert a == b
    assert a != c


# ================================================================
# Trial: candidate / results validation
# ================================================================


def test_trial_candidate_wrong_type_is_rejected():
    with pytest.raises(TypeError, match="candidate"):
        _trial(candidate="not a candidate")


def test_trial_results_non_tuple_is_rejected():
    with pytest.raises(TypeError, match="results"):
        _trial(results=[_window_result()])


def test_trial_results_empty_is_rejected():
    with pytest.raises(ValueError, match="results"):
        _trial(results=())


def test_trial_results_invalid_member_at_index_0_is_rejected():
    with pytest.raises(TypeError, match=r"results\[0\]"):
        _trial(results=("not a window result",))


def test_trial_results_invalid_member_at_later_index_is_rejected():
    with pytest.raises(TypeError, match=r"results\[1\]"):
        _trial(results=(_window_result(), "not a window result"))


def test_trial_results_preserve_input_order():
    results = (_window_result(window=W1), _window_result(window=W0))
    trial = _trial(results=results)
    assert trial.results[0].window == W1
    assert trial.results[1].window == W0


def test_trial_duplicate_windows_are_accepted():
    wr = _window_result(window=W0)
    trial = _trial(results=(wr, wr))
    assert trial.results == (wr, wr)


def test_trial_overlapping_windows_are_accepted():
    overlapping = TemporalWindow(
        start=datetime(2024, 1, 1, 9, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC)
    )
    results = (_window_result(window=W0), _window_result(window=overlapping))
    trial = _trial(results=results)
    assert trial.results == results


def test_trial_does_not_copy_or_mutate_results_tuple():
    results = (_window_result(),)
    trial = _trial(results=results)
    assert trial.results is results


def test_trial_does_not_copy_or_mutate_candidate_or_config():
    candidate = _candidate()
    config = _config()
    trial = _trial(candidate=candidate, config=config, results=(_window_result(),))
    assert trial.candidate is candidate
    assert trial.config is config


# ================================================================
# Trial: provenance validation
# ================================================================


@pytest.mark.parametrize("field_name", ["exchange", "market_type", "symbol", "timeframe"])
def test_trial_provenance_field_wrong_type_is_rejected(field_name):
    with pytest.raises(TypeError, match=field_name):
        _trial(**{field_name: 123})


@pytest.mark.parametrize("field_name", ["exchange", "market_type", "symbol", "timeframe"])
@pytest.mark.parametrize("bad_value", ["", "   ", " padded", "padded "])
def test_trial_provenance_field_empty_whitespace_or_padded_is_rejected(field_name, bad_value):
    with pytest.raises(ValueError, match=field_name):
        _trial(**{field_name: bad_value})


# ================================================================
# Trial: as_of_time validation
# ================================================================


def test_trial_as_of_time_wrong_type_is_rejected():
    with pytest.raises(TypeError, match="datetime"):
        _trial(as_of_time="not a datetime")


def test_trial_as_of_time_naive_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        _trial(as_of_time=datetime(2024, 1, 2, 0, 0))  # noqa: DTZ001


def test_trial_as_of_time_pseudo_naive_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        _trial(as_of_time=datetime(2024, 1, 2, 0, 0, tzinfo=_BrokenTzInfo()))


def test_trial_as_of_time_valid_aware_datetime_is_accepted():
    trial = _trial(as_of_time=AS_OF_TIME)
    assert trial.as_of_time == AS_OF_TIME


# ================================================================
# Trial: config validation and initial_cash consistency
# ================================================================


def test_trial_config_wrong_type_is_rejected():
    with pytest.raises(TypeError, match="config"):
        _trial(config="not a config")


def test_trial_initial_cash_mismatch_at_index_0_is_rejected():
    with pytest.raises(ValueError, match=r"results\[0\]"):
        _trial(
            results=(_window_result(initial_cash=Decimal(999)),),
            config=_config(initial_cash=Decimal(1000)),
        )


def test_trial_initial_cash_mismatch_at_later_index_is_rejected():
    good = _window_result(window=W0, initial_cash=Decimal(1000))
    bad = _window_result(window=W1, initial_cash=Decimal(999))
    with pytest.raises(ValueError, match=r"results\[1\]"):
        _trial(results=(good, bad), config=_config(initial_cash=Decimal(1000)))


def test_trial_construction_succeeds_when_every_initial_cash_matches():
    results = (
        _window_result(window=W0, initial_cash=Decimal(500)),
        _window_result(window=W1, initial_cash=Decimal(500)),
    )
    trial = _trial(results=results, config=_config(initial_cash=Decimal(500)))
    assert trial.results == results


# ================================================================
# Trial: multiple simultaneous violations / exact fail-fast order
# ================================================================


def test_trial_candidate_violation_wins_over_every_other_simultaneous_violation():
    with pytest.raises(TypeError, match="candidate"):
        Trial(
            candidate="bad",
            results="bad",
            exchange=123,
            market_type=None,
            symbol="",
            timeframe="  ",
            as_of_time="bad",
            config="bad",
        )


def test_trial_results_type_violation_wins_over_later_violations():
    with pytest.raises(TypeError, match="results"):
        Trial(
            candidate=_candidate(),
            results="bad",
            exchange=123,
            market_type=None,
            symbol="",
            timeframe="  ",
            as_of_time="bad",
            config="bad",
        )


def test_trial_member_type_violation_wins_over_provenance_and_later_violations():
    with pytest.raises(TypeError, match=r"results\[0\]"):
        Trial(
            candidate=_candidate(),
            results=("bad",),
            exchange=123,
            market_type=None,
            symbol="",
            timeframe="  ",
            as_of_time="bad",
            config="bad",
        )


def test_trial_provenance_violation_wins_over_as_of_time_and_config_violations():
    with pytest.raises(TypeError, match="exchange"):
        Trial(
            candidate=_candidate(),
            results=(_window_result(),),
            exchange=123,
            market_type=None,
            symbol="",
            timeframe="  ",
            as_of_time="bad",
            config="bad",
        )


def test_trial_as_of_time_violation_wins_over_config_violation():
    with pytest.raises(TypeError, match="datetime"):
        Trial(
            candidate=_candidate(),
            results=(_window_result(),),
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            as_of_time="bad",
            config="bad",
        )


def test_trial_config_violation_wins_over_initial_cash_consistency():
    with pytest.raises(TypeError, match="config"):
        Trial(
            candidate=_candidate(),
            results=(_window_result(initial_cash=Decimal(999)),),
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            as_of_time=AS_OF_TIME,
            config="bad",
        )


# ================================================================
# Trial: purity / no partial construction
# ================================================================


def test_trial_construction_failure_raises_not_returns_partial_object():
    sentinel = object()
    outcome = sentinel
    try:
        outcome = _trial(results=(_window_result(initial_cash=Decimal(999)),))
    except ValueError:
        pass
    assert outcome is sentinel


# ================================================================
# Trial: no selection/role/score/aggregation surface (absence-of-field)
# ================================================================


def test_trial_has_no_selection_role_score_or_holdout_field():
    field_names = {f.name for f in dataclasses.fields(Trial)}
    forbidden = {
        "role",
        "is_in_sample",
        "is_out_of_sample",
        "is_test",
        "is_train",
        "selected",
        "score",
        "rank",
        "holdout",
        "policy_factory",
        "policy",
        "evaluator",
    }
    assert not (forbidden & field_names)


def test_no_callable_field_participates_in_candidate_or_trial_dataclass():
    for cls in (Candidate, Trial):
        for field in dataclasses.fields(cls):
            assert "Callable" not in str(field.type)


# ================================================================
# Module-level compatibility / non-coupling
# ================================================================


def test_symbols_available_at_locked_module_path():
    assert candidate_module.Candidate is Candidate
    assert candidate_module.Trial is Trial
    assert candidate_module.ParameterValue is ParameterValue


def test_candidate_trial_not_exported_at_package_root():
    assert not hasattr(validation_package, "Candidate")
    assert not hasattr(validation_package, "Trial")
    assert not hasattr(validation_package, "ParameterValue")


def test_candidate_module_defines_no_evaluator_or_optimizer_symbol():
    forbidden = {
        "evaluate",
        "evaluate_candidate",
        "evaluate_trial",
        "run_trial",
        "score",
        "rank",
        "select",
        "optimize",
        "search",
        "policy_factory",
        "BacktestPolicy",
    }
    present = set(dir(candidate_module))
    assert not (forbidden & present)


def test_candidate_module_public_symbols_include_only_the_locked_value_objects():
    public_names = {name for name in dir(candidate_module) if not name.startswith("_")}
    assert {"ParameterValue", "Candidate", "Trial"} <= public_names


def test_candidate_module_imports_no_metric_optimizer_policy_or_rolling_runner_function():
    source = inspect.getsource(candidate_module)
    forbidden_substrings = [
        "compute_stage1_metrics",
        "compute_stage2_metrics",
        "compute_periodic_returns",
        "run_rolling_backtest_from_store",
        "run_context_aware_rolling_backtest_from_store",
        "BacktestPolicy",
        "policy_factory",
    ]
    for substring in forbidden_substrings:
        assert substring not in source


def _has_no_candidate_import(module):
    """True if no `import`/`from ... import` line in `module`'s source references `candidate`.

    Deliberately narrower than a whole-source substring check: `rolling.py`'s
    own docstring legitimately mentions "no candidate identity" (an absence-
    of-coupling claim, not an import), so only import statement lines are
    inspected here.
    """
    for line in inspect.getsource(module).splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "candidate" not in stripped
    return True


def test_rolling_module_does_not_import_candidate_module():
    assert _has_no_candidate_import(rolling_module)


def test_metrics_module_does_not_import_candidate_module():
    assert _has_no_candidate_import(metrics_module)


def test_windows_module_does_not_import_candidate_module():
    assert _has_no_candidate_import(windows_module)


def test_existing_window_result_and_temporal_window_still_construct_as_before():
    window = TemporalWindow(
        start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 9, 0, tzinfo=UTC)
    )
    result = _result()
    window_result = WindowResult(window=window, result=result)
    assert window_result.window == window
    assert window_result.result == result


# ================================================================
# Real rolling-path compatibility + independent Stage-1/Stage-2 metrics
# ================================================================


def test_trial_packages_real_rolling_path_results_and_supports_independent_stage1_metrics(
    tmp_path,
):
    store = _candle_store(tmp_path, hours=range(8, 12))
    window = TemporalWindow(
        start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC)
    )
    try:
        results = run_rolling_backtest_from_store(
            store,
            (window,),
            policy_factory=_FlatPolicy,
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            as_of_time=AS_OF_TIME,
            config=_config(),
            cost_model=ZeroCostModel(),
        )
        trial = Trial(
            candidate=_candidate(),
            results=results,
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            as_of_time=AS_OF_TIME,
            config=_config(),
        )
        assert trial.results is results
        stage1 = compute_stage1_metrics(trial.results[0].result)
        assert stage1.total_return == Decimal(0)
        returns = compute_periodic_returns(trial.results[0].result)
        assert len(returns) >= 2
        assert all(r == Decimal(0) for r in returns)
    finally:
        store.close()


def test_trial_supports_independent_stage2_metrics_via_synthetic_equity_curve():
    curve = (
        EquityPoint(time=datetime(2024, 1, 1, 8, 0, tzinfo=UTC), equity=Decimal(1000)),
        EquityPoint(time=datetime(2024, 1, 1, 9, 0, tzinfo=UTC), equity=Decimal(1010)),
        EquityPoint(time=datetime(2024, 1, 1, 10, 0, tzinfo=UTC), equity=Decimal(990)),
    )
    result = BacktestResult(
        initial_cash=Decimal(1000),
        final_cash=Decimal(990),
        final_equity=Decimal(990),
        total_realized_pnl=Decimal(-10),
        total_unrealized_pnl=Decimal(0),
        total_pnl=Decimal(-10),
        total_cost=Decimal(0),
        fill_count=0,
        trade_count=0,
        equity_curve=curve,
    )
    trial = _trial(
        results=(WindowResult(window=W0, result=result),),
        config=_config(initial_cash=Decimal(1000)),
    )
    stage2 = compute_stage2_metrics(trial.results[0].result)
    assert stage2.mean_return != Decimal(0)


# ================================================================
# Leakage-safety claim boundary (absence-of-claim, structural)
# ================================================================


def test_candidate_trial_module_docstring_does_not_claim_selection_safety():
    forbidden_claims = ["prevents leakage", "guarantees no peeking", "enforces holdout"]
    doc = candidate_module.__doc__ or ""
    lowered = doc.lower()
    for claim in forbidden_claims:
        assert claim not in lowered
