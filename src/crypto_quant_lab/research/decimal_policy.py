"""Explicit Decimal context for research runs (FUNDING_RESEARCH_SPEC.md Bölüm 18.1).

The legacy backtest engine (accounting, costs, funding cash) computes in the
process-global Decimal context by contract (COST_MODEL_SPEC.md Bölüm 20:
"konfigüre edilmiş Decimal context"). A research run therefore fixes that
context as an explicit run input: the orchestration boundary builds a FRESH
`Context` from the config and runs the whole computation inside
`decimal.localcontext(...)`, so the caller's context is never used or changed
and flags accumulated by earlier work never leak in. The basis layer and the
validation metrics keep their own private contexts unchanged.

The default is the context under which the engine was specified and its whole
test suite runs — Python's documented `decimal.DefaultContext` — written out
field by field (never read from the ambient process state): prec=28,
ROUND_HALF_EVEN, Emin=-999999, Emax=999999, capitals=1, clamp=0, traps
{InvalidOperation, DivisionByZero, Overflow}. Its precision, rounding and
exponent limits are also those of the validation metrics context
(VALIDATION_SPEC.md Bölüm 15.7).

Only settings that change results are part of the spec (and therefore of the
report fingerprint); flags are state, not configuration, and are excluded.
"""

import decimal
from decimal import Context

DEFAULT_DECIMAL_CONTEXT: dict[str, object] = {
    "prec": 28,
    "rounding": "ROUND_HALF_EVEN",
    "Emin": -999999,
    "Emax": 999999,
    "capitals": 1,
    "clamp": 0,
    "traps": ["DivisionByZero", "InvalidOperation", "Overflow"],
}
ROUNDINGS = (
    "ROUND_05UP",
    "ROUND_CEILING",
    "ROUND_DOWN",
    "ROUND_FLOOR",
    "ROUND_HALF_DOWN",
    "ROUND_HALF_EVEN",
    "ROUND_HALF_UP",
    "ROUND_UP",
)
SIGNALS = (
    "Clamped",
    "DivisionByZero",
    "FloatOperation",
    "Inexact",
    "InvalidOperation",
    "Overflow",
    "Rounded",
    "Subnormal",
    "Underflow",
)


def _int(raw: dict, key: str, low: int, high: int) -> int:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"decimal_context.{key} must be an integer in [{low}, {high}]")
    return value


def normalize_decimal_context(raw: object) -> dict[str, object]:
    """Validate a config `decimal_context` object; None means the documented default."""
    if raw is None:
        return {**DEFAULT_DECIMAL_CONTEXT, "traps": list(DEFAULT_DECIMAL_CONTEXT["traps"])}
    if not isinstance(raw, dict):
        raise ValueError("decimal_context must be an object")  # noqa: TRY004
    expected = set(DEFAULT_DECIMAL_CONTEXT)
    if set(raw) != expected:
        raise ValueError(
            f"decimal_context must have exactly the keys {sorted(expected)}, got {sorted(raw)}"
        )
    if raw["rounding"] not in ROUNDINGS:
        raise ValueError(f"decimal_context.rounding must be one of {ROUNDINGS}")
    traps = raw["traps"]
    if not isinstance(traps, list) or any(t not in SIGNALS for t in traps):
        raise ValueError(f"decimal_context.traps must be a list drawn from {SIGNALS}")
    if len(set(traps)) != len(traps):
        raise ValueError("decimal_context.traps must not repeat a signal")
    return {
        "prec": _int(raw, "prec", 1, decimal.MAX_PREC),
        "rounding": raw["rounding"],
        "Emin": _int(raw, "Emin", decimal.MIN_EMIN, 0),
        "Emax": _int(raw, "Emax", 0, decimal.MAX_EMAX),
        "capitals": _int(raw, "capitals", 0, 1),
        "clamp": _int(raw, "clamp", 0, 1),
        "traps": sorted(traps),
    }


def build_context(spec: dict[str, object]) -> Context:
    """A FRESH Context (all flags clear) from a normalized spec."""
    return Context(
        prec=spec["prec"],
        rounding=getattr(decimal, spec["rounding"]),
        Emin=spec["Emin"],
        Emax=spec["Emax"],
        capitals=spec["capitals"],
        clamp=spec["clamp"],
        flags=[],
        traps=[getattr(decimal, name) for name in spec["traps"]],
    )
