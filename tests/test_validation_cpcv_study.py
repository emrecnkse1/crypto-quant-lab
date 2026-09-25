"""Offline CPCV study end to end (VALIDATION_SPEC.md Bölüm 17.2.26-17.2.33, 28.R).

Real chain: SQLite candles -> rolling backtests (zero-context and context-aware)
-> TrialGroup -> TrialReturnMatrix -> fold model -> input assessment -> CPCV
paths -> summary. Expected purge counts and statuses are derived by hand from
the window layout.
"""

from decimal import Decimal

import pytest
import test_validation_pbo as base

from crypto_quant_lab.backtest.costs import ZeroCostModel
from crypto_quant_lab.backtest.models import BacktestConfig
from crypto_quant_lab.validation import cpcv_study
from crypto_quant_lab.validation.candidate import Candidate, Trial
from crypto_quant_lab.validation.combinatorial_folds import build_combinatorial_fold_model
from crypto_quant_lab.validation.cpcv import compute_cpcv_paths, summarize_cpcv_paths
from crypto_quant_lab.validation.cpcv_study import (
    CpcvInputAssessment,
    CpcvInputCheck,
    assess_cpcv_inputs,
    run_cpcv_study,
    window_aligned_fold_groups,
)
from crypto_quant_lab.validation.return_matrix import build_trial_return_matrix
from crypto_quant_lab.validation.rolling import (
    ContextAwareWindow,
    run_context_aware_rolling_backtest_from_store,
    run_rolling_backtest_with_positions_from_store,
)
from crypto_quant_lab.validation.windows import TemporalWindow

S, H = base.START, base.HOUR
CONFIG = BacktestConfig(initial_cash=Decimal(1000), position_quantity=Decimal(1))
CHECKS = ("provenance.evaluation_windows", "rows.fold_groups",
          "outcome_horizon.backtest_windows", "lookback.context")  # fmt: skip


def windows(*spans):
    return tuple(TemporalWindow(start=S + H * a, end=S + H * b) for a, b in spans)


FOUR = windows((0, 4), (4, 8), (8, 12), (12, 16))


@pytest.fixture
def store(tmp_path):
    candle_store = base.SQLiteHistoricalCandleStore(tmp_path / "candles.db")
    candle_store.write_batch([base._candle(h, p) for h, p in enumerate(base.PRICES)])
    yield candle_store
    candle_store.close()


def context_group(store, context_windows):
    trials = []
    for candidate_id, policy in base.POLICIES:
        results = run_context_aware_rolling_backtest_from_store(
            store, context_windows, policy_factory=policy, exchange=base.EXCHANGE,
            market_type=base.MARKET_TYPE, symbol=base.SYMBOL, timeframe=base.TIMEFRAME,
            as_of_time=base.AS_OF_TIME, config=CONFIG, cost_model=ZeroCostModel(),
        )  # fmt: skip
        trials.append(Trial(candidate=Candidate(candidate_id=candidate_id, parameters=()),
                            results=results, exchange=base.EXCHANGE,
                            market_type=base.MARKET_TYPE, symbol=base.SYMBOL,
                            timeframe=base.TIMEFRAME, as_of_time=base.AS_OF_TIME,
                            config=CONFIG))  # fmt: skip
    return base.TrialGroup(group_id="cpcv-study", trials=tuple(trials))


def zero_context(ws):
    return tuple(ContextAwareWindow(context_start=w.start, evaluation=w) for w in ws)


def statuses(study_or_assessment):
    assessment = getattr(study_or_assessment, "assessment", study_or_assessment)
    return {c.name: c.status for c in assessment.checks}


# ================================================================ window-aligned groups


def test_window_aligned_groups_absorb_gaps_into_the_preceding_group():
    ws = windows((0, 3), (5, 8), (8, 10))
    assert window_aligned_fold_groups(ws) == windows((0, 5), (5, 8), (8, 10))
    with pytest.raises(ValueError, match="at least 2 evaluation windows are required, got 1"):
        window_aligned_fold_groups(ws[:1])
    with pytest.raises(ValueError, match=r"windows\[1\] must start at or after windows\[0\]\.end"):
        window_aligned_fold_groups(windows((0, 3), (2, 5)))
    with pytest.raises(TypeError, match="windows must be a tuple, got list"):
        window_aligned_fold_groups(list(ws))


# ================================================================ end-to-end, fully evaluated


def test_real_chain_zero_context_is_fully_evaluated_and_matches_the_direct_path(store):
    group = base._rolling_group(store, FOUR, base.POLICIES)
    study = run_cpcv_study(group, test_group_count=2, lookback_windows=zero_context(FOUR))
    assert statuses(study) == dict.fromkeys(CHECKS, "passed")
    assert study.assessment.fully_evaluated and study.assessment.runnable
    assert "not a full CPCV validation" in study.scope
    # the study is exactly the direct chain on the same real evidence
    matrix = build_trial_return_matrix(group)
    model = build_combinatorial_fold_model(window_aligned_fold_groups(FOUR), test_group_count=2)
    assert study.matrix == matrix and study.fold_model == model
    assert study.result == compute_cpcv_paths(matrix, model)
    assert study.summary == summarize_cpcv_paths(study.result)
    assert study.result.row_groups == (0,) * 4 + (1,) * 4 + (2,) * 4 + (3,) * 4
    assert study.summary.path_count == 3 and study.summary.observations_per_path == 16
    for path in study.result.paths:  # every path row is its split's selected real return
        for row, g in enumerate(study.result.row_groups):
            split = study.result.splits[path.split_indices[g]]
            values = [matrix.returns[row][matrix.candidate_ids.index(c)]
                      for c in split.selected_candidate_ids]  # fmt: skip
            assert path.returns[row] == (values[0] if len(values) == 1 else
                                         sum(values) / len(values))  # fmt: skip
    assert all(s.purged_train_row_count == 0 for s in study.result.splits)


def test_missing_lookback_is_reported_not_assumed(store):
    study = run_cpcv_study(base._rolling_group(store, FOUR, base.POLICIES), test_group_count=2)
    assert statuses(study)["lookback.context"] == "not_evaluated"
    assert study.assessment.runnable and not study.assessment.fully_evaluated
    assert study.result is not None
    detail = study.assessment.checks[3].detail
    assert "WindowResult/TrialGroup do not record context_start" in detail


# ================================================================ lookback (backward) vs embargo


CONTEXT_WINDOWS = tuple(
    ContextAwareWindow(context_start=S + H * (a - 1), evaluation=TemporalWindow(
        start=S + H * a, end=S + H * (a + 3)))
    for a in (1, 4, 7, 10, 13)
)  # fmt: skip  # 5 windows of 3h, each reading 1h of context


def test_real_lookback_without_embargo_fails_and_blocks_the_study(store):
    group = context_group(store, CONTEXT_WINDOWS)
    study = run_cpcv_study(group, test_group_count=2, lookback_windows=CONTEXT_WINDOWS)
    assert statuses(study)["lookback.context"] == "failed"
    assert study.result is None and study.summary is None and not study.assessment.runnable
    # split 0 tests groups (0, 1) = [1h, 4h), [4h, 7h); training window 2 reads [6h, 7h)
    assert study.assessment.checks[3].detail == (
        "split 0: training window 2 reads 1:00:00 of context that overlaps test group 1; "
        "embargo 0:00:00 does not cover the lookback (max 1:00:00)"
    )


def test_embargo_covering_the_lookback_passes_and_runs(store):
    group = context_group(store, CONTEXT_WINDOWS)
    study = run_cpcv_study(group, test_group_count=2, embargo=H,
                           lookback_windows=CONTEXT_WINDOWS)  # fmt: skip
    assert statuses(study) == dict.fromkeys(CHECKS, "passed")
    assert "max lookback 1:00:00" in study.assessment.checks[3].detail
    # embargo 1h drops the group right after each test group: test (0, 2) -> embargoed 1, 3
    split = study.fold_model.splits[1]
    assert (split.test_groups, split.embargoed_groups, split.train_groups) == ((0, 2), (1, 3), (4,))
    assert study.summary.path_count == 4  # C(4, 1)


def test_lookback_windows_must_describe_the_evaluated_windows(store):
    group = context_group(store, CONTEXT_WINDOWS)
    other = ContextAwareWindow(context_start=S + H * 12, evaluation=windows((13, 15))[0])
    study = run_cpcv_study(group, test_group_count=2, embargo=H,
                           lookback_windows=CONTEXT_WINDOWS[:-1] + (other,))  # fmt: skip
    assert statuses(study)["lookback.context"] == "failed"
    detail = study.assessment.checks[3].detail
    assert detail == "lookback windows do not describe the evaluated windows"


# ================================================================ outcome horizon (forward)


HALVES = windows(*((2 * i, 2 * i + 2) for i in range(8)))  # 8 fold groups, two per window


def test_fold_boundary_inside_a_backtest_window_fails_without_the_purge(store):
    group = base._rolling_group(store, FOUR, base.POLICIES)
    study = run_cpcv_study(group, test_group_count=2, fold_groups=HALVES,
                           lookback_windows=zero_context(FOUR))  # fmt: skip
    assert statuses(study)["outcome_horizon.backtest_windows"] == "failed"
    assert "fold boundaries fall inside backtest window(s) [0, 1, 2, 3]" in (
        study.assessment.checks[2].detail
    )
    assert study.result is None


def test_shared_window_purge_drops_training_rows_sharing_a_window_with_test_rows(store):
    group = base._rolling_group(store, FOUR, base.POLICIES)
    study = run_cpcv_study(group, test_group_count=2, fold_groups=HALVES,
                           lookback_windows=zero_context(FOUR),
                           purge_shared_backtest_windows=True)  # fmt: skip
    assert statuses(study) == dict.fromkeys(CHECKS, "passed")
    by_test = {s.test_groups: s for s in study.result.splits}
    # test (0, 1) = both halves of window 0: nothing else shares window 0 -> 0 purged, 12 rows
    assert (by_test[(0, 1)].purged_train_row_count, by_test[(0, 1)].train_row_count) == (0, 12)
    # test (0, 2) = halves of windows 0 and 1: their other halves (groups 1, 3) are purged
    assert (by_test[(0, 2)].purged_train_row_count, by_test[(0, 2)].train_row_count) == (4, 8)
    # test (0, 7) = windows 0 and 3: groups 1 and 6 purged
    assert (by_test[(0, 7)].purged_train_row_count, by_test[(0, 7)].train_row_count) == (4, 8)
    assert study.summary.path_count == 7  # C(7, 1)


def test_purge_leaving_too_few_training_rows_is_reported_not_raised(store):
    two = windows((0, 6), (8, 14))  # two windows, four fold groups
    group = base._rolling_group(store, two, base.POLICIES)
    halves = windows((0, 3), (3, 8), (8, 11), (11, 14))
    study = run_cpcv_study(group, test_group_count=2, fold_groups=halves,
                           lookback_windows=zero_context(two),
                           purge_shared_backtest_windows=True)  # fmt: skip
    # test (0, 2) touches both windows: every training row is purged
    assert statuses(study)["cpcv.computation"] == "failed"
    assert study.assessment.checks[-1].detail == (
        "split 1 has 0 training row(s); at least 2 are required for a sample standard deviation"
    )
    assert study.result is None and study.summary is None


# ================================================================ provenance and ownership


def test_provenance_mismatch_stops_the_later_checks(store):
    group = base._rolling_group(store, FOUR, base.POLICIES)
    matrix = build_trial_return_matrix(group)
    model = build_combinatorial_fold_model(window_aligned_fold_groups(FOUR), test_group_count=2)
    assessment = assess_cpcv_inputs(matrix, model, evaluation_windows=FOUR[:3])
    assert statuses(assessment) == {
        "provenance.evaluation_windows": "failed",
        "rows.fold_groups": "not_evaluated",
        "outcome_horizon.backtest_windows": "not_evaluated",
        "lookback.context": "not_evaluated",
    }
    assert assessment.checks[0].detail == "matrix has 4 window block(s), evaluation_windows has 3"
    shifted = FOUR[:3] + windows((13, 17))
    assessment = assess_cpcv_inputs(matrix, model, evaluation_windows=shifted)
    assert assessment.checks[0].detail.startswith("row 12 (window block 3) at ")


def test_rows_outside_the_fold_groups_fail_the_ownership_check(store):
    group = base._rolling_group(store, FOUR, base.POLICIES)
    short = windows((0, 4), (4, 8), (8, 12), (12, 15))  # last row at 16h is in no group
    study = run_cpcv_study(group, test_group_count=2, fold_groups=short)
    assert statuses(study)["rows.fold_groups"] == "failed"
    assert study.assessment.checks[1].detail.startswith("row 15 at ")
    assert study.result is None


def test_invalid_arguments_and_check_statuses():
    with pytest.raises(TypeError, match="group must be a TrialGroup, got str"):
        run_cpcv_study("g", test_group_count=2)
    with pytest.raises(ValueError, match="status must be one of"):
        CpcvInputCheck("x", "ok", "")
    assessment = CpcvInputAssessment((CpcvInputCheck("a", "passed", ""),
                                      CpcvInputCheck("b", "not_evaluated", "")))  # fmt: skip
    assert assessment.runnable and not assessment.fully_evaluated
    assert assessment.status("b") == "not_evaluated"


def test_module_is_offline_and_not_exported():
    import crypto_quant_lab.validation as package

    assert not hasattr(package, "run_cpcv_study")
    source = cpcv_study.__loader__.get_source(cpcv_study.__name__)
    for forbidden in ("urllib", "socket", "requests", "random", "float("):
        assert forbidden not in source


# ================================================================ recorded position intervals (§17.2.35)


def positions_group(store, ws):
    trials, intervals = [], {}
    for candidate_id, policy in base.POLICIES:
        results, per_window = run_rolling_backtest_with_positions_from_store(
            store, ws, policy_factory=policy, exchange=base.EXCHANGE,
            market_type=base.MARKET_TYPE, symbol=base.SYMBOL, timeframe=base.TIMEFRAME,
            as_of_time=base.AS_OF_TIME, config=CONFIG, cost_model=ZeroCostModel(),
        )  # fmt: skip
        trials.append(Trial(candidate=Candidate(candidate_id=candidate_id, parameters=()),
                            results=results, exchange=base.EXCHANGE,
                            market_type=base.MARKET_TYPE, symbol=base.SYMBOL,
                            timeframe=base.TIMEFRAME, as_of_time=base.AS_OF_TIME,
                            config=CONFIG))  # fmt: skip
        intervals[candidate_id] = per_window
    return base.TrialGroup(group_id="positions", trials=tuple(trials)), intervals


def independent_purge(matrix, model, intervals, split):
    """Test-side re-derivation of the observation-level purge rule."""
    owners = [next(g for g, w in enumerate(model.groups) if w.start < t <= w.end)
              for t in matrix.observation_times]  # fmt: skip
    purged = 0
    for row, t in enumerate(matrix.observation_times):
        if owners[row] not in split.train_groups:
            continue
        block = matrix.window_indices[row]
        marks = [matrix.observation_times[r] for r in range(len(owners))
                 if matrix.window_indices[r] == block]  # fmt: skip
        hit = False
        for per_window in intervals.values():
            for interval in per_window[block]:
                held = [m for m in marks if interval.entry_time < m
                        and (interval.exit_time is None or m <= interval.exit_time)]  # fmt: skip
                if t in held and any(model.groups[g].start < m <= model.groups[g].end
                                     for m in held for g in split.test_groups):  # fmt: skip
                    hit = True
        purged += hit
    return purged


def test_recorded_intervals_purge_the_real_chain_more_finely_than_the_window_purge(store):
    group, intervals = positions_group(store, FOUR)
    by_intervals = run_cpcv_study(group, test_group_count=2, fold_groups=HALVES,
                                  lookback_windows=zero_context(FOUR),
                                  position_intervals=intervals)  # fmt: skip
    assert statuses(by_intervals) == dict.fromkeys(
        CHECKS + ("outcome_horizon.position_intervals",), "passed")  # fmt: skip
    by_window = run_cpcv_study(group, test_group_count=2, fold_groups=HALVES,
                               lookback_windows=zero_context(FOUR),
                               purge_shared_backtest_windows=True)  # fmt: skip
    finer = 0
    for fine, coarse, split in zip(by_intervals.result.splits, by_window.result.splits,
                                   by_intervals.fold_model.splits, strict=True):  # fmt: skip
        assert fine.purged_train_row_count == independent_purge(
            by_intervals.matrix, by_intervals.fold_model, intervals, split
        )
        assert fine.purged_train_row_count <= coarse.purged_train_row_count
        finer += fine.purged_train_row_count < coarse.purged_train_row_count
    assert finer > 0 and by_intervals.summary.path_count == 7


def test_intervals_that_do_not_reproduce_the_trade_count_fail(store):
    group, intervals = positions_group(store, FOUR)
    tampered = dict(intervals)
    tampered["alternating"] = (intervals["alternating"][0][1:],) + intervals["alternating"][1:]
    study = run_cpcv_study(group, test_group_count=2, fold_groups=HALVES,
                           lookback_windows=zero_context(FOUR),
                           position_intervals=tampered)  # fmt: skip
    check = study.assessment.checks[-1]
    assert (check.name, check.status) == ("outcome_horizon.position_intervals", "failed")
    assert check.detail.startswith("alternating window 0: intervals imply ")
    assert study.result is None
