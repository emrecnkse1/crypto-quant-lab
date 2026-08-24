import inspect
from dataclasses import fields
from datetime import UTC, datetime, timedelta

import pytest

import crypto_quant_lab.validation as validation_package
import crypto_quant_lab.validation.annualized_metrics as annualized_metrics_module
import crypto_quant_lab.validation.candidate as candidate_module
import crypto_quant_lab.validation.metrics as metrics_module
import crypto_quant_lab.validation.purging as purging_module
import crypto_quant_lab.validation.rolling as rolling_module
import crypto_quant_lab.validation.windows as windows_module
from crypto_quant_lab.validation.candidate import Candidate, ParameterValue, Trial
from crypto_quant_lab.validation.purging import (
    embargo_boundary,
    purge_in_sample_windows,
    windows_overlap,
)
from crypto_quant_lab.validation.rolling import WindowResult
from crypto_quant_lab.validation.windows import TemporalSplit, TemporalWindow

T0 = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)


def _window(start_hour: int, end_hour: int) -> TemporalWindow:
    return TemporalWindow(
        start=T0 + timedelta(hours=start_hour), end=T0 + timedelta(hours=end_hour)
    )


def _import_lines(module) -> list[str]:
    lines = []
    for line in inspect.getsource(module).splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            lines.append(stripped)
    return lines


# =====================================================================
# API / value-object surface (criterion 1)
# =====================================================================


def test_three_functions_exist_at_locked_module_path():
    assert purging_module.__name__ == "crypto_quant_lab.validation.purging"
    assert callable(purging_module.windows_overlap)
    assert callable(purging_module.embargo_boundary)
    assert callable(purging_module.purge_in_sample_windows)


def test_windows_overlap_exact_signature():
    sig = inspect.signature(windows_overlap)
    params = list(sig.parameters.values())
    assert [p.name for p in params] == ["first", "second"]
    assert params[0].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params[1].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params[0].default is inspect.Parameter.empty
    assert params[1].default is inspect.Parameter.empty


def test_embargo_boundary_exact_signature():
    sig = inspect.signature(embargo_boundary)
    params = list(sig.parameters.values())
    assert params[0].name == "out_of_sample"
    assert params[0].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params[0].default is inspect.Parameter.empty
    assert sig.parameters["embargo"].kind == inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["embargo"].default is inspect.Parameter.empty
    assert set(sig.parameters) == {"out_of_sample", "embargo"}


def test_purge_in_sample_windows_exact_signature():
    sig = inspect.signature(purge_in_sample_windows)
    params = list(sig.parameters.values())
    assert params[0].name == "in_sample_windows"
    assert params[0].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params[0].default is inspect.Parameter.empty
    assert sig.parameters["out_of_sample"].kind == inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["out_of_sample"].default is inspect.Parameter.empty
    assert sig.parameters["embargo"].kind == inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["embargo"].default == timedelta(0)
    assert set(sig.parameters) == {"in_sample_windows", "out_of_sample", "embargo"}


def test_windows_overlap_returns_bare_bool():
    result = windows_overlap(_window(0, 10), _window(20, 30))
    assert type(result) is bool


def test_embargo_boundary_returns_bare_datetime():
    result = embargo_boundary(_window(0, 10), embargo=timedelta(hours=1))
    assert type(result) is datetime


def test_purge_in_sample_windows_returns_bare_tuple():
    result = purge_in_sample_windows((), out_of_sample=_window(0, 10))
    assert type(result) is tuple


# =====================================================================
# validation/__init__.py + no new fields on existing models (criterion 2)
# =====================================================================


def test_validation_package_does_not_export_purging_functions():
    # Submodule import elsewhere in this process legitimately makes
    # `validation.purging` resolvable as an import-machinery side effect —
    # only the three public functions being absent from the package root is
    # the actual re-export claim (same precedent as
    # test_validation_package_root_unchanged in
    # test_validation_annualized_metrics.py).
    assert not hasattr(validation_package, "windows_overlap")
    assert not hasattr(validation_package, "embargo_boundary")
    assert not hasattr(validation_package, "purge_in_sample_windows")


def test_temporal_window_gains_no_purge_embargo_fields():
    field_names = {f.name for f in fields(TemporalWindow)}
    assert field_names == {"start", "end"}


def test_temporal_split_gains_no_purge_embargo_fields():
    field_names = {f.name for f in fields(TemporalSplit)}
    assert field_names == {"in_sample", "out_of_sample", "timeframe"}


def test_window_result_gains_no_purge_embargo_fields():
    field_names = {f.name for f in fields(WindowResult)}
    assert field_names == {"window", "result"}


def test_candidate_and_trial_gain_no_purge_embargo_fields():
    candidate_field_names = {f.name for f in fields(Candidate)}
    trial_field_names = {f.name for f in fields(Trial)}
    assert "embargo" not in candidate_field_names
    assert "purge" not in candidate_field_names
    assert "embargo" not in trial_field_names
    assert "purge" not in trial_field_names


# =====================================================================
# Existing windows/rolling/metrics/candidate/annualized_metrics modules
# unchanged (criterion 3) — public API still present/callable
# =====================================================================


def test_existing_public_api_across_validation_modules_still_present():
    assert hasattr(windows_module, "TemporalWindow")
    assert hasattr(windows_module, "TemporalSplit")
    assert hasattr(rolling_module, "WindowResult")
    assert hasattr(rolling_module, "run_rolling_backtest_from_store")
    assert hasattr(rolling_module, "run_context_aware_rolling_backtest_from_store")
    assert hasattr(metrics_module, "compute_stage1_metrics")
    assert hasattr(metrics_module, "compute_stage2_metrics")
    assert hasattr(metrics_module, "compute_periodic_returns")
    assert hasattr(candidate_module, "Candidate")
    assert hasattr(candidate_module, "Trial")
    assert hasattr(candidate_module, "ParameterValue")
    assert hasattr(annualized_metrics_module, "compute_annualized_sharpe_ratio")
    assert hasattr(annualized_metrics_module, "compute_sortino_ratio")
    assert hasattr(annualized_metrics_module, "compute_cagr")
    assert hasattr(annualized_metrics_module, "compute_calmar_ratio")
    assert ParameterValue is not None


# =====================================================================
# windows_overlap: type validation (criterion 4)
# =====================================================================


def test_windows_overlap_rejects_non_window_first():
    with pytest.raises(TypeError, match="first"):
        windows_overlap("bad", _window(0, 10))


def test_windows_overlap_rejects_non_window_second():
    with pytest.raises(TypeError, match="second"):
        windows_overlap(_window(0, 10), "bad")


# =====================================================================
# windows_overlap: exact half-open formula, touching, symmetry (criterion 5)
# =====================================================================


def test_windows_overlap_touching_is_not_overlap():
    a = _window(0, 10)
    b = _window(10, 20)
    assert windows_overlap(a, b) is False
    assert windows_overlap(b, a) is False


def test_windows_overlap_full_overlap_identical_windows():
    a = _window(0, 10)
    b = _window(0, 10)
    assert windows_overlap(a, b) is True
    assert windows_overlap(b, a) is True


def test_windows_overlap_partial_overlap_both_directions():
    a = _window(0, 10)
    b = _window(5, 15)
    assert windows_overlap(a, b) is True
    assert windows_overlap(b, a) is True


def test_windows_overlap_disjoint():
    a = _window(0, 10)
    b = _window(20, 30)
    assert windows_overlap(a, b) is False
    assert windows_overlap(b, a) is False


def test_windows_overlap_full_containment_both_directions():
    outer = _window(0, 20)
    inner = _window(5, 10)
    assert windows_overlap(outer, inner) is True
    assert windows_overlap(inner, outer) is True


def test_windows_overlap_symmetric_across_several_pairs():
    pairs = [
        (_window(0, 10), _window(10, 20)),
        (_window(0, 10), _window(5, 15)),
        (_window(0, 20), _window(5, 10)),
        (_window(0, 10), _window(0, 10)),
        (_window(0, 10), _window(100, 200)),
    ]
    for first, second in pairs:
        assert windows_overlap(first, second) == windows_overlap(second, first)


# =====================================================================
# embargo_boundary: type/value validation (criterion 6)
# =====================================================================


def test_embargo_boundary_rejects_non_window_out_of_sample():
    with pytest.raises(TypeError, match="out_of_sample"):
        embargo_boundary("bad", embargo=timedelta(hours=1))


def test_embargo_boundary_rejects_non_timedelta_embargo():
    with pytest.raises(TypeError, match="embargo"):
        embargo_boundary(_window(0, 10), embargo="bad")


def test_embargo_boundary_rejects_negative_embargo():
    with pytest.raises(ValueError, match="embargo"):
        embargo_boundary(_window(0, 10), embargo=timedelta(hours=-1))


# =====================================================================
# embargo_boundary: exact formula (criterion 7)
# =====================================================================


def test_embargo_boundary_exact_formula():
    window = _window(0, 10)
    embargo = timedelta(hours=3)
    assert embargo_boundary(window, embargo=embargo) == window.end + embargo


def test_embargo_boundary_zero_embargo_equals_out_of_sample_end():
    window = _window(0, 10)
    assert embargo_boundary(window, embargo=timedelta(0)) == window.end


def test_embargo_boundary_overflow_propagates_pythons_own_error():
    near_max_end = datetime.max.replace(tzinfo=UTC)
    near_max_window = TemporalWindow(start=near_max_end - timedelta(hours=1), end=near_max_end)
    with pytest.raises(OverflowError, match="date value out of range"):
        embargo_boundary(near_max_window, embargo=timedelta(days=1))


# =====================================================================
# purge_in_sample_windows: exact validation/fail-fast order (criterion 8)
# =====================================================================


def test_purge_rejects_non_window_out_of_sample():
    with pytest.raises(TypeError, match="out_of_sample"):
        purge_in_sample_windows((), out_of_sample="bad")


def test_purge_rejects_non_timedelta_embargo():
    with pytest.raises(TypeError, match="embargo"):
        purge_in_sample_windows((), out_of_sample=_window(0, 10), embargo="bad")


def test_purge_rejects_negative_embargo():
    with pytest.raises(ValueError, match="embargo"):
        purge_in_sample_windows((), out_of_sample=_window(0, 10), embargo=timedelta(hours=-1))


def test_purge_rejects_non_tuple_in_sample_windows():
    with pytest.raises(TypeError, match="in_sample_windows"):
        purge_in_sample_windows([_window(0, 5)], out_of_sample=_window(20, 30))


def test_purge_rejects_invalid_element_type_at_index_0():
    with pytest.raises(TypeError, match=r"in_sample_windows\[0\]"):
        purge_in_sample_windows(("bad", _window(0, 5)), out_of_sample=_window(20, 30))


def test_purge_rejects_invalid_element_type_at_later_index():
    with pytest.raises(TypeError, match=r"in_sample_windows\[2\]"):
        purge_in_sample_windows(
            (_window(0, 5), _window(5, 10), "bad"), out_of_sample=_window(20, 30)
        )


def test_purge_fail_fast_order_out_of_sample_type_before_embargo_type():
    with pytest.raises(TypeError, match="out_of_sample"):
        purge_in_sample_windows((), out_of_sample="bad", embargo="also_bad")


def test_purge_fail_fast_order_embargo_type_before_embargo_value():
    with pytest.raises(TypeError, match="embargo"):
        purge_in_sample_windows((), out_of_sample=_window(0, 10), embargo="bad")


def test_purge_fail_fast_order_embargo_value_before_in_sample_windows_type():
    with pytest.raises(ValueError, match="embargo"):
        purge_in_sample_windows(
            "not_a_tuple", out_of_sample=_window(0, 10), embargo=timedelta(hours=-1)
        )


def test_purge_fail_fast_order_in_sample_windows_type_before_element_type():
    with pytest.raises(TypeError, match="in_sample_windows"):
        purge_in_sample_windows(["bad_element"], out_of_sample=_window(0, 10))


def test_purge_global_element_pass_reports_first_invalid_index_regardless_of_later_validity():
    with pytest.raises(TypeError, match=r"in_sample_windows\[1\]"):
        purge_in_sample_windows(
            (_window(0, 5), "bad", _window(10, 15)), out_of_sample=_window(20, 30)
        )


# =====================================================================
# purge vs. embargo precedence and exact operation order (criterion 9)
# =====================================================================


def test_direct_overlap_is_rejected_without_computing_embargo_zone():
    is_window = _window(5, 15)
    oos_window = _window(10, 20)
    # An embargo large enough to overflow embargo_boundary if it were ever
    # computed — proves the embargo test never runs once purge rejects.
    huge_embargo = timedelta(days=999_999_999)
    result = purge_in_sample_windows((is_window,), out_of_sample=oos_window, embargo=huge_embargo)
    assert result == ()


def test_zero_embargo_never_constructs_a_temporal_window_for_the_embargo_zone():
    # If a TemporalWindow(start=X, end=X) were ever attempted for an empty
    # embargo zone, TemporalWindow.__post_init__ would raise ValueError.
    touching_window = _window(10, 20)
    oos_window = _window(0, 10)
    result = purge_in_sample_windows(
        (touching_window,), out_of_sample=oos_window, embargo=timedelta(0)
    )
    assert result == (touching_window,)


def test_embargo_zone_rejection_only_applies_when_not_already_purged_and_embargo_positive():
    is_window = _window(11, 15)
    oos_window = _window(0, 10)
    rejected = purge_in_sample_windows(
        (is_window,), out_of_sample=oos_window, embargo=timedelta(hours=5)
    )
    assert rejected == ()
    accepted = purge_in_sample_windows((is_window,), out_of_sample=oos_window, embargo=timedelta(0))
    assert accepted == (is_window,)


# =====================================================================
# input order preserved, duplicates not deduplicated (criterion 10)
# =====================================================================


def test_output_preserves_original_input_order():
    oos_window = _window(100, 110)
    a = _window(0, 5)
    b = _window(50, 55)
    c = _window(20, 25)
    result = purge_in_sample_windows((a, b, c), out_of_sample=oos_window)
    assert result == (a, b, c)


def test_duplicate_in_sample_windows_are_not_deduplicated():
    oos_window = _window(100, 110)
    a = _window(0, 5)
    result = purge_in_sample_windows((a, a, a), out_of_sample=oos_window)
    assert result == (a, a, a)
    assert len(result) == 3


# =====================================================================
# empty input / all-purged behavior (criterion 11)
# =====================================================================


def test_empty_in_sample_windows_returns_empty_tuple():
    result = purge_in_sample_windows((), out_of_sample=_window(0, 10))
    assert result == ()


def test_all_windows_purged_returns_empty_tuple():
    oos_window = _window(0, 10)
    a = _window(2, 8)
    b = _window(0, 10)
    result = purge_in_sample_windows((a, b), out_of_sample=oos_window)
    assert result == ()


# =====================================================================
# no mechanical chronology enforcement (criterion 12)
# =====================================================================


def test_in_sample_after_out_of_sample_is_not_mechanically_rejected():
    oos_window = _window(0, 10)
    later_is_window = _window(20, 30)
    result = purge_in_sample_windows((later_is_window,), out_of_sample=oos_window)
    assert result == (later_is_window,)


# =====================================================================
# default embargo is exact timedelta(0) (criterion 13)
# =====================================================================


def test_default_embargo_degenerates_to_purge_only_behavior():
    oos_window = _window(0, 10)
    touching_window = _window(10, 20)
    default_call = purge_in_sample_windows((touching_window,), out_of_sample=oos_window)
    explicit_zero_call = purge_in_sample_windows(
        (touching_window,), out_of_sample=oos_window, embargo=timedelta(0)
    )
    assert default_call == explicit_zero_call == (touching_window,)


# =====================================================================
# purge and embargo are distinct rejection reasons (criterion 14)
# =====================================================================


def test_purge_and_embargo_are_distinct_independent_rejection_reasons():
    oos_window = _window(10, 20)
    directly_overlapping = _window(15, 25)  # rejected by purge, embargo irrelevant
    only_in_embargo_zone = _window(21, 23)  # not overlapping OOS, but inside embargo zone
    clean = _window(30, 40)  # rejected by neither

    result = purge_in_sample_windows(
        (directly_overlapping, only_in_embargo_zone, clean),
        out_of_sample=oos_window,
        embargo=timedelta(hours=5),
    )
    assert result == (clean,)

    # With no embargo, only the direct-overlap reason applies.
    result_no_embargo = purge_in_sample_windows(
        (directly_overlapping, only_in_embargo_zone, clean),
        out_of_sample=oos_window,
        embargo=timedelta(0),
    )
    assert result_no_embargo == (only_in_embargo_zone, clean)


# =====================================================================
# label/outcome-horizon purging is out of scope (criterion 15)
# =====================================================================


def test_purging_module_defines_no_horizon_label_outcome_concept():
    for bad_name in ("horizon", "label", "outcome", "event_horizon", "label_horizon"):
        assert not hasattr(purging_module, bad_name)

    all_params: set[str] = set()
    for fn in (windows_overlap, embargo_boundary, purge_in_sample_windows):
        all_params.update(inspect.signature(fn).parameters)
    for bad_param in ("horizon", "label", "outcome"):
        assert bad_param not in all_params


# =====================================================================
# purity / determinism / no mutation / no I/O / no orchestration coupling
# (criterion 16)
# =====================================================================


def test_purge_does_not_mutate_input_tuple_or_window_identities():
    oos_window = _window(100, 110)
    a = _window(0, 5)
    b = _window(20, 25)
    original = (a, b)
    result = purge_in_sample_windows(original, out_of_sample=oos_window)
    assert original == (a, b)
    assert original[0] is a
    assert original[1] is b
    assert result[0] is a
    assert result[1] is b


def test_purge_is_deterministic_across_repeated_calls():
    oos_window = _window(100, 110)
    windows = (_window(0, 5), _window(50, 55), _window(90, 95))
    first_call = purge_in_sample_windows(
        windows, out_of_sample=oos_window, embargo=timedelta(hours=2)
    )
    second_call = purge_in_sample_windows(
        windows, out_of_sample=oos_window, embargo=timedelta(hours=2)
    )
    assert first_call == second_call


def test_purging_module_import_lines_reference_only_windows_and_stdlib():
    import_lines = _import_lines(purging_module)
    assert import_lines == [
        "from datetime import datetime, timedelta",
        "from crypto_quant_lab.validation.windows import TemporalWindow",
    ]


def test_purging_module_uses_no_wallclock_or_randomness():
    source = inspect.getsource(purging_module)
    for forbidden in ("datetime.now(", ".utcnow(", "import random", "time.time(", "uuid."):
        assert forbidden not in source


def test_purging_functions_do_not_reference_rolling_candidate_metrics_orchestration():
    source = inspect.getsource(purging_module)
    forbidden_substrings = [
        "run_rolling_backtest_from_store",
        "run_context_aware_rolling_backtest_from_store",
        "compute_stage1_metrics",
        "compute_stage2_metrics",
        "compute_periodic_returns",
        "compute_annualized_sharpe_ratio",
        "Candidate",
        "Trial",
        "BacktestPolicy",
    ]
    for substring in forbidden_substrings:
        assert substring not in source


# =====================================================================
# import-direction acyclicity: no existing module imports purging (criterion 17)
# =====================================================================


def _has_no_purging_import(module) -> bool:
    for line in inspect.getsource(module).splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "purging" not in stripped
    return True


def test_windows_module_does_not_import_purging_module():
    assert _has_no_purging_import(windows_module)


def test_rolling_module_does_not_import_purging_module():
    assert _has_no_purging_import(rolling_module)


def test_metrics_module_does_not_import_purging_module():
    assert _has_no_purging_import(metrics_module)


def test_candidate_module_does_not_import_purging_module():
    assert _has_no_purging_import(candidate_module)


def test_annualized_metrics_module_does_not_import_purging_module():
    assert _has_no_purging_import(annualized_metrics_module)


# =====================================================================
# no float/Decimal arithmetic (criterion 18)
# =====================================================================


def test_purging_module_uses_no_float_or_decimal_arithmetic():
    source = inspect.getsource(purging_module)
    assert "float(" not in source
    assert "Decimal" not in source


# =====================================================================
# works identically on directly-constructed windows and TemporalSplit-
# derived windows; no TemporalSplit coupling (criterion 19)
# =====================================================================


def test_purging_module_does_not_import_temporal_split():
    assert not hasattr(purging_module, "TemporalSplit")


def test_purge_works_identically_on_temporal_split_derived_windows():
    is_window = TemporalWindow(start=T0, end=T0 + timedelta(hours=10))
    oos_window = TemporalWindow(start=T0 + timedelta(hours=10), end=T0 + timedelta(hours=20))
    split = TemporalSplit(in_sample=is_window, out_of_sample=oos_window, timeframe="1h")

    other_is = _window(30, 35)
    via_split = purge_in_sample_windows(
        (split.in_sample, other_is), out_of_sample=split.out_of_sample
    )
    via_direct = purge_in_sample_windows((is_window, other_is), out_of_sample=oos_window)
    assert via_split == via_direct == (split.in_sample, other_is)
