import dataclasses
import inspect
import itertools
import typing
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

import crypto_quant_lab.validation as validation_package
import crypto_quant_lab.validation.annualized_metrics as annualized_metrics_module
import crypto_quant_lab.validation.candidate as candidate_module
import crypto_quant_lab.validation.metrics as metrics_module
import crypto_quant_lab.validation.purging as purging_module
import crypto_quant_lab.validation.rolling as rolling_module
import crypto_quant_lab.validation.trial_group as trial_group_module
import crypto_quant_lab.validation.windows as windows_module
from crypto_quant_lab.backtest.costs import ZeroCostModel
from crypto_quant_lab.backtest.models import BacktestConfig, BacktestResult, PositionTarget
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.base import HistoricalCandle
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore
from crypto_quant_lab.validation.candidate import Candidate, Trial
from crypto_quant_lab.validation.rolling import WindowResult, run_rolling_backtest_from_store
from crypto_quant_lab.validation.trial_group import TrialGroup, recorded_trial_count
from crypto_quant_lab.validation.windows import TemporalWindow

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
W_OVERLAP = TemporalWindow(
    start=datetime(2024, 1, 1, 9, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC)
)


def _config(initial_cash=Decimal(1000), position_quantity=Decimal(1)):
    return BacktestConfig(initial_cash=initial_cash, position_quantity=position_quantity)


def _result(initial_cash=Decimal(1000)):
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
        equity_curve=(),
    )


def _trial(candidate_id="cand-1", parameters=(), windows=(W0,), **overrides):
    kwargs = {
        "candidate": Candidate(candidate_id=candidate_id, parameters=parameters),
        "results": tuple(WindowResult(window=window, result=_result()) for window in windows),
        "exchange": EXCHANGE,
        "market_type": MARKET_TYPE,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "as_of_time": AS_OF_TIME,
        "config": _config(),
    }
    kwargs.update(overrides)
    return Trial(**kwargs)


def _raises_exactly(exception_type, message, callable_, *args, **kwargs):
    with pytest.raises(exception_type) as excinfo:
        callable_(*args, **kwargs)
    assert type(excinfo.value) is exception_type
    assert str(excinfo.value) == message


# Field-specific mismatching values for the six provenance fields (Bölüm 20.6).
# `config` differs only in position_quantity so Trial's own initial_cash
# invariant (Bölüm 18.7) still holds.
MISMATCH_VALUES = {
    "exchange": "okx",
    "market_type": "spot",
    "symbol": "ETHUSDT",
    "timeframe": "4h",
    "as_of_time": datetime(2024, 1, 3, 0, 0, tzinfo=UTC),
    "config": _config(position_quantity=Decimal(2)),
}
REFERENCE_VALUES = {
    "exchange": EXCHANGE,
    "market_type": MARKET_TYPE,
    "symbol": SYMBOL,
    "timeframe": TIMEFRAME,
    "as_of_time": AS_OF_TIME,
    "config": _config(),
}
LOCKED_FIELD_ORDER = ("exchange", "market_type", "symbol", "timeframe", "as_of_time", "config")


def _mismatch_message(index, field_name):
    return (
        f"trials[{index}].{field_name} ({MISMATCH_VALUES[field_name]!r}) does not match "
        f"trials[0].{field_name} ({REFERENCE_VALUES[field_name]!r})"
    )


class _FlatPolicy:
    def target_position(self, context):
        return PositionTarget.FLAT


class _LongPolicy:
    def target_position(self, context):
        return PositionTarget.LONG


# ================================================================
# API / value-object shape
# ================================================================


def test_symbols_available_at_locked_module_path():
    assert trial_group_module.TrialGroup is TrialGroup
    assert trial_group_module.recorded_trial_count is recorded_trial_count


def test_module_public_symbols_are_exactly_the_locked_api():
    public_names = {name for name in dir(trial_group_module) if not name.startswith("_")}
    assert public_names == {"TrialGroup", "recorded_trial_count"}


def test_trial_group_field_order_is_locked():
    assert [field.name for field in dataclasses.fields(TrialGroup)] == ["group_id", "trials"]


def test_trial_group_resolved_annotations_match_locked_api():
    assert typing.get_type_hints(TrialGroup) == {"group_id": str, "trials": tuple[Trial, ...]}


def test_recorded_trial_count_signature_is_locked():
    signature = inspect.signature(recorded_trial_count)
    assert list(signature.parameters) == ["group"]
    assert signature.parameters["group"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert signature.parameters["group"].default is inspect.Parameter.empty
    assert typing.get_type_hints(recorded_trial_count) == {"group": TrialGroup, "return": int}


def test_trial_group_is_frozen():
    group = TrialGroup(group_id="g", trials=(_trial(),))
    with pytest.raises(FrozenInstanceError):
        group.group_id = "other"


def test_trial_group_is_slotted():
    group = TrialGroup(group_id="g", trials=(_trial(),))
    assert not hasattr(group, "__dict__")
    assert TrialGroup.__slots__ == ("group_id", "trials")


def test_trial_group_uses_default_dataclass_equality_and_hash():
    group_params = TrialGroup.__dataclass_params__
    assert group_params.eq is True
    assert group_params.frozen is True
    assert group_params.order is False
    assert group_params.unsafe_hash is False
    source = inspect.getsource(trial_group_module)
    for dunder in ("def __eq__", "def __hash__", "def __lt__", "def __le__"):
        assert dunder not in source


def test_trial_group_not_exported_at_package_root():
    assert not hasattr(validation_package, "TrialGroup")
    assert not hasattr(validation_package, "recorded_trial_count")


def test_candidate_and_trial_fields_are_unchanged():
    assert [field.name for field in dataclasses.fields(Candidate)] == [
        "candidate_id",
        "parameters",
    ]
    assert [field.name for field in dataclasses.fields(Trial)] == [
        "candidate",
        "results",
        "exchange",
        "market_type",
        "symbol",
        "timeframe",
        "as_of_time",
        "config",
    ]
    assert [field.name for field in dataclasses.fields(WindowResult)] == ["window", "result"]


# ================================================================
# group_id (stages 1-2)
# ================================================================


@pytest.mark.parametrize("group_id", [None, 1, b"g", ("g",)])
def test_group_id_wrong_type_is_rejected(group_id):
    _raises_exactly(
        TypeError,
        f"group_id must be a str, got {type(group_id).__name__}",
        TrialGroup,
        group_id=group_id,
        trials=(_trial(),),
    )


@pytest.mark.parametrize("group_id", ["", " ", "\t\n"])
def test_group_id_empty_or_whitespace_only_is_rejected(group_id):
    _raises_exactly(
        ValueError,
        "group_id must not be empty or whitespace-only",
        TrialGroup,
        group_id=group_id,
        trials=(_trial(),),
    )


@pytest.mark.parametrize("group_id", [" g", "g ", "\tg", "g\n"])
def test_group_id_padding_is_rejected_not_stripped(group_id):
    _raises_exactly(
        ValueError,
        "group_id must not have leading/trailing whitespace padding",
        TrialGroup,
        group_id=group_id,
        trials=(_trial(),),
    )


def test_group_id_is_case_sensitive_and_not_normalized():
    trials = (_trial(),)
    upper = TrialGroup(group_id="Exp-A", trials=trials)
    lower = TrialGroup(group_id="exp-a", trials=trials)
    assert upper.group_id == "Exp-A"
    assert upper != lower
    composed = TrialGroup(group_id="é", trials=trials)
    decomposed = TrialGroup(group_id="é", trials=trials)
    assert composed.group_id == "é"
    assert decomposed.group_id == "é"
    assert composed != decomposed


def test_group_id_with_internal_whitespace_is_accepted():
    group = TrialGroup(group_id="exp a", trials=(_trial(),))
    assert group.group_id == "exp a"


# ================================================================
# trials collection (stages 3-5)
# ================================================================


@pytest.mark.parametrize("trials", [None, [], [_trial()], {_trial()}, _trial()])
def test_trials_non_tuple_is_rejected(trials):
    _raises_exactly(
        TypeError,
        f"trials must be a tuple, got {type(trials).__name__}",
        TrialGroup,
        group_id="g",
        trials=trials,
    )


def test_trials_empty_tuple_is_rejected():
    _raises_exactly(ValueError, "trials must not be empty", TrialGroup, group_id="g", trials=())


def test_trials_invalid_element_at_index_0_is_rejected():
    _raises_exactly(
        TypeError,
        "trials[0] must be a Trial, got Candidate",
        TrialGroup,
        group_id="g",
        trials=(Candidate(candidate_id="c", parameters=()), _trial()),
    )


def test_trials_invalid_element_at_later_index_is_rejected():
    _raises_exactly(
        TypeError,
        "trials[2] must be a Trial, got NoneType",
        TrialGroup,
        group_id="g",
        trials=(_trial("a"), _trial("b"), None),
    )


# ================================================================
# candidate_id uniqueness (stage 6)
# ================================================================


def test_equal_rerun_trial_is_rejected_as_duplicate():
    first = _trial("cand-1")
    rerun = _trial("cand-1")
    assert first == rerun
    _raises_exactly(
        ValueError,
        "trials[1].candidate.candidate_id 'cand-1' duplicates trials[0].candidate.candidate_id",
        TrialGroup,
        group_id="g",
        trials=(first, rerun),
    )


def test_same_trial_object_twice_is_rejected_as_duplicate():
    trial = _trial("cand-1")
    _raises_exactly(
        ValueError,
        "trials[1].candidate.candidate_id 'cand-1' duplicates trials[0].candidate.candidate_id",
        TrialGroup,
        group_id="g",
        trials=(trial, trial),
    )


def test_conflicting_duplicate_with_different_parameters_is_rejected():
    _raises_exactly(
        ValueError,
        "trials[2].candidate.candidate_id 'cand-1' duplicates trials[0].candidate.candidate_id",
        TrialGroup,
        group_id="g",
        trials=(
            _trial("cand-1", parameters=(("fast", 5),)),
            _trial("cand-2", parameters=(("fast", 7),)),
            _trial("cand-1", parameters=(("fast", 9),)),
        ),
    )


def test_duplicate_message_reports_first_seen_index():
    _raises_exactly(
        ValueError,
        "trials[3].candidate.candidate_id 'b' duplicates trials[1].candidate.candidate_id",
        TrialGroup,
        group_id="g",
        trials=(_trial("a"), _trial("b"), _trial("c"), _trial("b")),
    )


def test_candidate_id_uniqueness_is_case_sensitive():
    group = TrialGroup(group_id="g", trials=(_trial("cand"), _trial("Cand")))
    assert recorded_trial_count(group) == 2


def test_same_parameters_different_candidate_id_is_accepted_and_counted_as_two_records():
    # Two records of the same parameter set under different ids: the group
    # accepts both and counts 2 RECORDS. This is not evidence of two
    # independent strategies (Bölüm 20.5, 20.7) -- the count is a record count.
    parameters = (("fast", 5), ("slow", 20))
    first = _trial("cand-a", parameters=parameters)
    second = _trial("cand-b", parameters=parameters)
    group = TrialGroup(group_id="g", trials=(first, second))
    assert first.candidate.parameters == second.candidate.parameters
    assert recorded_trial_count(group) == 2
    assert group.trials == (first, second)


# ================================================================
# provenance homogeneity (stages 7-12)
# ================================================================


@pytest.mark.parametrize("field_name", LOCKED_FIELD_ORDER)
def test_provenance_mismatch_at_index_1_is_rejected(field_name):
    other = _trial("b", **{field_name: MISMATCH_VALUES[field_name]})
    _raises_exactly(
        ValueError,
        _mismatch_message(1, field_name),
        TrialGroup,
        group_id="g",
        trials=(_trial("a"), other),
    )


@pytest.mark.parametrize("field_name", LOCKED_FIELD_ORDER)
def test_provenance_mismatch_at_later_index_is_rejected(field_name):
    other = _trial("c", **{field_name: MISMATCH_VALUES[field_name]})
    _raises_exactly(
        ValueError,
        _mismatch_message(2, field_name),
        TrialGroup,
        group_id="g",
        trials=(_trial("a"), _trial("b"), other),
    )


@pytest.mark.parametrize(
    ("earlier_field", "later_field"),
    list(itertools.pairwise(LOCKED_FIELD_ORDER)),
)
def test_earlier_field_pass_wins_even_at_a_later_index(earlier_field, later_field):
    # index 1 violates the LATER field; index 2 violates the EARLIER field.
    # A per-trial single loop would raise for index 1; the locked per-field
    # global passes must raise for the earlier field at index 2.
    trials = (
        _trial("a"),
        _trial("b", **{later_field: MISMATCH_VALUES[later_field]}),
        _trial("c", **{earlier_field: MISMATCH_VALUES[earlier_field]}),
    )
    _raises_exactly(
        ValueError,
        _mismatch_message(2, earlier_field),
        TrialGroup,
        group_id="g",
        trials=trials,
    )


def test_first_field_pass_wins_over_last_field_pass():
    trials = (
        _trial("a"),
        _trial("b", config=MISMATCH_VALUES["config"]),
        _trial("c", exchange=MISMATCH_VALUES["exchange"]),
    )
    _raises_exactly(
        ValueError, _mismatch_message(2, "exchange"), TrialGroup, group_id="g", trials=trials
    )


def test_same_instant_as_of_time_with_different_tzinfo_is_accepted():
    plus_three = timezone(timedelta(hours=3))
    same_instant = datetime(2024, 1, 2, 3, 0, tzinfo=plus_three)
    other = _trial("b", as_of_time=same_instant)
    group = TrialGroup(group_id="g", trials=(_trial("a"), other))
    assert group.trials[1].as_of_time is same_instant
    assert group.trials[1].as_of_time.tzinfo is plus_three


def test_equal_valued_config_instances_are_accepted():
    other = _trial("b", config=_config(initial_cash=Decimal("1000.00")))
    group = TrialGroup(group_id="g", trials=(_trial("a"), other))
    assert recorded_trial_count(group) == 2


# ================================================================
# ordered evaluation-window sequence (stage 13)
# ================================================================


@pytest.mark.parametrize(
    "other_windows",
    [
        pytest.param((W0,), id="shorter"),
        pytest.param((W0, W1, W1), id="longer"),
        pytest.param((W1, W0), id="reordered"),
        pytest.param((W0, W_OVERLAP), id="different-value"),
    ],
)
def test_window_sequence_mismatch_is_rejected(other_windows):
    _raises_exactly(
        ValueError,
        "trials[1] evaluation windows do not match trials[0] evaluation windows",
        TrialGroup,
        group_id="g",
        trials=(_trial("a", windows=(W0, W1)), _trial("b", windows=other_windows)),
    )


def test_window_sequence_mismatch_at_later_index_is_rejected():
    _raises_exactly(
        ValueError,
        "trials[2] evaluation windows do not match trials[0] evaluation windows",
        TrialGroup,
        group_id="g",
        trials=(_trial("a"), _trial("b"), _trial("c", windows=(W1,))),
    )


def test_identical_duplicate_and_overlapping_window_sequences_are_accepted():
    windows = (W0, W0, W_OVERLAP, W1)
    group = TrialGroup(
        group_id="g", trials=(_trial("a", windows=windows), _trial("b", windows=windows))
    )
    assert recorded_trial_count(group) == 2


def test_same_instant_windows_with_different_tzinfo_are_accepted():
    plus_two = timezone(timedelta(hours=2))
    shifted = TemporalWindow(
        start=datetime(2024, 1, 1, 10, 0, tzinfo=plus_two),
        end=datetime(2024, 1, 1, 12, 0, tzinfo=plus_two),
    )
    group = TrialGroup(
        group_id="g", trials=(_trial("a", windows=(W0,)), _trial("b", windows=(shifted,)))
    )
    assert group.trials[1].results[0].window is shifted


# ================================================================
# exact global fail-fast order across all 13 stages
# ================================================================


def test_stage1_group_id_type_wins_over_trials_type():
    _raises_exactly(TypeError, "group_id must be a str, got int", TrialGroup, group_id=1, trials=[])


def test_stage2_group_id_content_wins_over_trials_type():
    _raises_exactly(
        ValueError,
        "group_id must not be empty or whitespace-only",
        TrialGroup,
        group_id=" ",
        trials=None,
    )


def test_stage3_trials_type_wins_over_emptiness():
    _raises_exactly(
        TypeError, "trials must be a tuple, got list", TrialGroup, group_id="g", trials=[]
    )


def test_stage5_element_type_at_later_index_wins_over_earlier_duplicate():
    trial = _trial("a")
    _raises_exactly(
        TypeError,
        "trials[2] must be a Trial, got str",
        TrialGroup,
        group_id="g",
        trials=(trial, trial, "not-a-trial"),
    )


def test_stage6_duplicate_at_later_index_wins_over_earlier_provenance_mismatch():
    _raises_exactly(
        ValueError,
        "trials[2].candidate.candidate_id 'a' duplicates trials[0].candidate.candidate_id",
        TrialGroup,
        group_id="g",
        trials=(_trial("a"), _trial("b", exchange="okx"), _trial("a")),
    )


def test_stage6_duplicate_wins_over_window_mismatch():
    _raises_exactly(
        ValueError,
        "trials[1].candidate.candidate_id 'a' duplicates trials[0].candidate.candidate_id",
        TrialGroup,
        group_id="g",
        trials=(_trial("a", windows=(W0,)), _trial("a", windows=(W1,))),
    )


def test_stage12_config_at_later_index_wins_over_earlier_window_mismatch():
    _raises_exactly(
        ValueError,
        _mismatch_message(2, "config"),
        TrialGroup,
        group_id="g",
        trials=(
            _trial("a"),
            _trial("b", windows=(W1,)),
            _trial("c", config=MISMATCH_VALUES["config"]),
        ),
    )


# ================================================================
# order, identity, value semantics, determinism
# ================================================================


def test_single_trial_group_is_legal_and_counts_one():
    group = TrialGroup(group_id="g", trials=(_trial(),))
    count = recorded_trial_count(group)
    assert count == 1
    assert type(count) is int


def test_multi_trial_group_counts_exact_int():
    trials = tuple(_trial(f"cand-{index}") for index in range(5))
    count = recorded_trial_count(TrialGroup(group_id="g", trials=trials))
    assert count == 5
    assert type(count) is int


def test_input_order_is_preserved_not_sorted():
    trials = (_trial("zeta"), _trial("alpha"), _trial("mu"))
    group = TrialGroup(group_id="g", trials=trials)
    assert [trial.candidate.candidate_id for trial in group.trials] == ["zeta", "alpha", "mu"]


def test_construction_does_not_copy_or_mutate_trials():
    first, second = _trial("a"), _trial("b")
    trials = (first, second)
    snapshot = (hash(first), hash(second))
    group = TrialGroup(group_id="g", trials=trials)
    assert group.trials is trials
    assert group.trials[0] is first
    assert group.trials[1] is second
    assert (hash(first), hash(second)) == snapshot
    assert trials == (first, second)


def test_equality_is_value_based():
    assert TrialGroup(group_id="g", trials=(_trial("a"), _trial("b"))) == TrialGroup(
        group_id="g", trials=(_trial("a"), _trial("b"))
    )


def test_equality_is_order_sensitive():
    first, second = _trial("a"), _trial("b")
    assert TrialGroup(group_id="g", trials=(first, second)) != TrialGroup(
        group_id="g", trials=(second, first)
    )


def test_different_group_id_is_not_equal():
    trials = (_trial("a"),)
    assert TrialGroup(group_id="g1", trials=trials) != TrialGroup(group_id="g2", trials=trials)


def test_trial_group_is_hashable_as_set_member_and_dict_key():
    group = TrialGroup(group_id="g", trials=(_trial("a"), _trial("b")))
    equal = TrialGroup(group_id="g", trials=(_trial("a"), _trial("b")))
    assert hash(group) == hash(equal)
    assert len({group, equal}) == 1
    assert {group: "value"}[equal] == "value"


def test_repeated_construction_and_count_are_deterministic():
    trials = (_trial("a"), _trial("b"), _trial("c"))
    groups = [TrialGroup(group_id="g", trials=trials) for _ in range(3)]
    assert groups[0] == groups[1] == groups[2]
    assert len({hash(group) for group in groups}) == 1
    assert [recorded_trial_count(groups[0]) for _ in range(3)] == [3, 3, 3]


def test_same_trials_can_form_separate_groups_without_cross_group_detection():
    trials = (_trial("a"), _trial("b"))
    first = TrialGroup(group_id="g", trials=trials)
    second = TrialGroup(group_id="g", trials=trials)
    assert recorded_trial_count(first) + recorded_trial_count(second) == 4


# ================================================================
# recorded_trial_count input validation
# ================================================================


@pytest.mark.parametrize(
    "value",
    [None, (), [], "g", 3],
    ids=["none", "tuple", "list", "str", "int"],
)
def test_recorded_trial_count_rejects_non_trial_group(value):
    _raises_exactly(
        TypeError,
        f"group must be a TrialGroup, got {type(value).__name__}",
        recorded_trial_count,
        value,
    )


def test_recorded_trial_count_rejects_trial_and_trial_tuple():
    trial = _trial()
    _raises_exactly(TypeError, "group must be a TrialGroup, got Trial", recorded_trial_count, trial)
    _raises_exactly(
        TypeError, "group must be a TrialGroup, got tuple", recorded_trial_count, (trial,)
    )


# ================================================================
# absence of forbidden fields / symbols
# ================================================================


def test_trial_group_has_no_role_score_status_or_effective_field():
    field_names = {field.name for field in dataclasses.fields(TrialGroup)}
    forbidden = {
        "role",
        "score",
        "rank",
        "winner",
        "selected",
        "effective",
        "effective_count",
        "status",
        "failed",
        "complete",
        "is_complete",
        "holdout",
        "total_attempts",
        "declared_total_attempts",
    }
    assert not (field_names & forbidden)


def test_module_defines_no_effective_dsr_selection_persistence_or_registry_symbol():
    forbidden = {
        "effective_trial_count",
        "deflated_sharpe",
        "deflated_sharpe_ratio",
        "select",
        "select_best",
        "rank",
        "score",
        "best",
        "winner",
        "add",
        "append",
        "register",
        "registry",
        "save",
        "load",
        "optimize",
        "search",
    }
    assert not (forbidden & set(dir(trial_group_module)))


# ================================================================
# static import direction / scope
# ================================================================


def _import_lines(module):
    return [
        line.strip()
        for line in inspect.getsource(module).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]


def test_trial_group_module_imports_only_candidate_trial_and_stdlib_dataclass():
    assert _import_lines(trial_group_module) == [
        "from dataclasses import dataclass as _dataclass",
        "from crypto_quant_lab.validation.candidate import Trial as _Trial",
    ]


@pytest.mark.parametrize(
    "module",
    [
        candidate_module,
        rolling_module,
        windows_module,
        metrics_module,
        annualized_metrics_module,
        purging_module,
        validation_package,
    ],
    ids=lambda module: module.__name__,
)
def test_no_existing_validation_module_imports_trial_group(module):
    for line in _import_lines(module):
        assert "trial_group" not in line


def test_trial_group_module_uses_no_decimal_float_clock_or_randomness_import():
    source = inspect.getsource(trial_group_module)
    assert "Decimal" not in source
    assert "float(" not in source
    for line in _import_lines(trial_group_module):
        for forbidden in ("decimal", "random", "time", "datetime", "os", "sqlite3", "metrics"):
            assert forbidden not in line.split()[1].split(".")


# ================================================================
# real rolling-path integration (Bölüm 20.11)
# ================================================================


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


def _run(store, windows, policy_factory):
    return run_rolling_backtest_from_store(
        store,
        windows,
        policy_factory=policy_factory,
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        as_of_time=AS_OF_TIME,
        config=_config(),
        cost_model=ZeroCostModel(),
    )


def _packaged_trial(candidate_id, parameters, results):
    return Trial(
        candidate=Candidate(candidate_id=candidate_id, parameters=parameters),
        results=results,
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        as_of_time=AS_OF_TIME,
        config=_config(),
    )


def test_real_rolling_trials_form_group_and_mismatched_windows_are_rejected(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "candles.db")
    try:
        store.write_batch(
            [
                _make_candle_record(datetime(2024, 1, 1, hour, 0, tzinfo=UTC))
                for hour in range(8, 13)
            ]
        )
        windows = (
            TemporalWindow(
                start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC),
                end=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            ),
            TemporalWindow(
                start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
                end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            ),
        )
        flat = _packaged_trial("flat", (("target", "FLAT"),), _run(store, windows, _FlatPolicy))
        long = _packaged_trial("long", (("target", "LONG"),), _run(store, windows, _LongPolicy))
        group = TrialGroup(group_id="flat-vs-long", trials=(flat, long))
        assert recorded_trial_count(group) == 2
        assert group.trials[0] is flat
        assert group.trials[1] is long

        other_windows = (windows[0],)
        shorter = _packaged_trial(
            "flat-short", (("target", "FLAT"),), _run(store, other_windows, _FlatPolicy)
        )
        _raises_exactly(
            ValueError,
            "trials[2] evaluation windows do not match trials[0] evaluation windows",
            TrialGroup,
            group_id="flat-vs-long",
            trials=(flat, long, shorter),
        )
    finally:
        store.close()
