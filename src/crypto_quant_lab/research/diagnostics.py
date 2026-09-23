"""Read-only "why no trade?" diagnostics for funding research trials (FUNDING_RESEARCH_SPEC.md Bölüm 17.5).

Replays the decision instants of an already evaluated `Trial` through the SAME
rule the policy uses (`decide_funding_carry`) — no second strategy, no engine
re-run, no cash. Counts are kept separate on purpose:

- decisions_evaluated: evaluation candles in the window (one decision each, at
  the candle's availability instant, exactly as the replay engine asks);
- signal_visible: decisions where a settled funding event was known;
- fresh_signal: visible and not older than `max_funding_age`;
- threshold_met: fresh and at/beyond an entry threshold (SHORT or LONG);
- target_changes: decisions whose target differs from the position held when
  the decision was made (position starts FLAT at the window start);
- executable_target_changes: those that have a next candle to fill at —
  the engine never fills the last candle's decision (BACKTEST_SPEC.md
  Bölüm 11), reported as `final_decision_unexecuted`;
- engine_fill_count / engine_trade_count: copied from the engine's own
  `BacktestResult`, never derived here.
`consistent_with_engine` is True iff executable_target_changes equals the
engine fill count; a False value is a finding, not something to hide.

Only the reasons the code really distinguishes exist here. There is no risk
filter, no Risk Engine and no execution veto in this repository; none is
invented.
"""

from dataclasses import dataclass
from datetime import timedelta

from crypto_quant_lab.backtest.models import PositionTarget
from crypto_quant_lab.data_quality.feature_availability import feature_availability_time
from crypto_quant_lab.research.funding_carry import (
    LONG_THRESHOLD_MET,
    NO_TRADE_CONTROL_STRATEGY,
    SHORT_THRESHOLD_MET,
    STALE_SIGNAL,
    FundingSignalHistory,
    decide_funding_carry,
    funding_research_policy_factory,
)
from crypto_quant_lab.validation.candidate import Trial
from crypto_quant_lab.validation.windows import TemporalWindow

CONTROL_ALWAYS_FLAT = "control_always_flat"


@dataclass(frozen=True, slots=True)
class WindowDecisionDiagnostics:
    window: TemporalWindow
    decisions_evaluated: int
    signal_visible: int
    fresh_signal: int
    threshold_met: int
    reason_counts: tuple[tuple[str, int], ...]
    target_changes: int
    executable_target_changes: int
    final_decision_unexecuted: bool
    engine_fill_count: int
    engine_trade_count: int
    consistent_with_engine: bool


def diagnose_funding_research_trial(
    candle_store: object, history: FundingSignalHistory, trial: Trial
) -> tuple[WindowDecisionDiagnostics, ...]:
    """Per-window decision diagnostics for a funding-carry or no-trade-control `Trial`."""
    if not isinstance(trial, Trial):
        raise TypeError(f"trial must be a Trial, got {type(trial).__name__}")
    funding_research_policy_factory(trial.candidate, history)  # same consistency checks
    if (trial.exchange, trial.market_type, trial.symbol) != (
        history.exchange,
        history.market_type,
        history.symbol,
    ):
        raise ValueError("trial partition does not match the funding history partition")
    parameters = dict(trial.candidate.parameters)
    strategy = parameters["strategy"]  # validated above: carry or no-trade control

    diagnostics = []
    for window_result in trial.results:
        window = window_result.window
        records = candle_store.query(
            trial.exchange,
            trial.market_type,
            trial.symbol,
            trial.timeframe,
            window.start,
            window.end,
        )
        reasons: dict[str, int] = {}
        visible = fresh = met = changes = executable = 0
        final_unexecuted = False
        held = PositionTarget.FLAT
        for index, record in enumerate(records):
            as_of = feature_availability_time(record.candle)
            if strategy == NO_TRADE_CONTROL_STRATEGY:
                target, reason = PositionTarget.FLAT, CONTROL_ALWAYS_FLAT
            else:
                decision = decide_funding_carry(
                    history,
                    as_of,
                    short_entry_rate=parameters["short_entry_rate"],
                    long_entry_rate=parameters["long_entry_rate"],
                    max_funding_age=timedelta(microseconds=parameters["max_funding_age_us"]),
                )
                target, reason = decision.target, decision.reason
                if decision.event is not None:
                    visible += 1
                    if reason != STALE_SIGNAL:
                        fresh += 1
                if reason in (SHORT_THRESHOLD_MET, LONG_THRESHOLD_MET):
                    met += 1
            reasons[reason] = reasons.get(reason, 0) + 1
            if target is not held:
                changes += 1
                if index == len(records) - 1:
                    final_unexecuted = True
                else:
                    executable += 1
                    held = target
        result = window_result.result
        diagnostics.append(
            WindowDecisionDiagnostics(
                window=window,
                decisions_evaluated=len(records),
                signal_visible=visible,
                fresh_signal=fresh,
                threshold_met=met,
                reason_counts=tuple(sorted(reasons.items())),
                target_changes=changes,
                executable_target_changes=executable,
                final_decision_unexecuted=final_unexecuted,
                engine_fill_count=result.fill_count,
                engine_trade_count=result.trade_count,
                consistent_with_engine=executable == result.fill_count,
            )
        )
    return tuple(diagnostics)
