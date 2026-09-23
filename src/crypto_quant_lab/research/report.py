"""Deterministic research report + manifest contract (FUNDING_RESEARCH_SPEC.md Bölüm 17.3).

A report is one JSON document plus a Markdown rendering of it:

    {
      "schema_version": "crypto-quant-lab/research-report/v1",
      "run_kind": ...,
      "status": "succeeded" | "failed",
      "deterministic": {...},          # everything derived from config + inputs
      "deterministic_sha256": ...,     # SHA-256 of canonical JSON of "deterministic"
      "run_metadata": {...}            # wall clock, git revision, runtime — NOT hashed
    }

Serialization rules: Decimal -> string (never float), datetime -> ISO-8601 in
UTC with explicit "+00:00" (naive rejected), float/NaN rejected, keys sorted,
compact separators, ASCII-escaped. Identical config + identical logical
inputs therefore give byte-identical `deterministic` and the same hash on
every run; the clock only lives in `run_metadata`.

Input fingerprints are LOGICAL: SHA-256 over the canonical JSON of the rows
the production store APIs return for the requested range (plus provenance and
coverage), never over the SQLite file bytes — a live database file's bytes
(page layout, free pages, WAL/journal state) change without any change in
content, and hashing it while open is not a consistent snapshot.

Output bundles are written into a fresh staging directory next to the target
and renamed into place only when complete; an existing target is never
overwritten. A failed run still produces a bundle, with status "failed" and
its errors — never a successful-looking empty result.
"""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from decimal import Decimal, getcontext
from pathlib import Path

from crypto_quant_lab import __version__
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us

REPORT_SCHEMA_VERSION = "crypto-quant-lab/research-report/v1"
REPORT_JSON = "report.json"
REPORT_MARKDOWN = "report.md"
CHECK_STATUSES = ("passed", "failed", "skipped")


def to_jsonable(value: object) -> object:
    """Convert to JSON-safe primitives under the report rules (raises on floats etc.)."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"non-finite Decimal cannot be reported: {value}")
        return str(value)
    if isinstance(value, datetime):
        datetime_to_epoch_us(value)  # rejects naive
        return value.astimezone(UTC).isoformat()
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        converted = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"report keys must be str, got {type(key).__name__}")
            converted[key] = to_jsonable(item)
        return converted
    raise TypeError(f"{type(value).__name__} is not allowed in a research report")


def canonical_json(value: object) -> str:
    return json.dumps(to_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fingerprint(value: object) -> str:
    """SHA-256 of the canonical JSON of `value`."""
    return sha256_hex(canonical_json(value))


def check(name: str, status: str, detail: str) -> dict[str, str]:
    if status not in CHECK_STATUSES:
        raise ValueError(f"check status must be one of {CHECK_STATUSES}, got {status!r}")
    return {"name": name, "status": status, "detail": detail}


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def run_metadata(created_at: datetime) -> dict[str, object]:
    """Non-deterministic run context. Git dirtiness counts tracked files only."""
    repo = Path(__file__).resolve().parents[3]
    revision = _git(["rev-parse", "HEAD"], repo)
    status = _git(["status", "--porcelain", "--untracked-files=no"], repo)
    context = getcontext()
    return {
        "created_at": created_at,
        "git_revision": revision,
        "git_tracked_changes": None if status is None else bool(status),
        "package_version": __version__,
        "python_version": platform.python_version(),
        "decimal_context": {
            "prec": context.prec,
            "rounding": context.rounding,
            "note": "engine accounting/costs use the process decimal context "
            "(COST_MODEL_SPEC.md); the CLI never changes it",
        },
    }


def build_report(
    *,
    run_kind: str,
    deterministic: dict[str, object],
    created_at: datetime,
) -> dict[str, object]:
    """Assemble a report; status is "failed" iff there are errors or failed checks."""
    for key in ("checks", "errors", "limitations", "does_not_prove"):
        if key not in deterministic:
            raise ValueError(f"deterministic payload must contain {key!r}")
    failed = bool(deterministic["errors"]) or any(
        item["status"] == "failed" for item in deterministic["checks"]
    )
    payload = to_jsonable(deterministic)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_kind": run_kind,
        "status": "failed" if failed else "succeeded",
        "deterministic": payload,
        "deterministic_sha256": fingerprint(payload),
        "run_metadata": to_jsonable(run_metadata(created_at)),
    }


def render_markdown(report: dict[str, object]) -> str:
    det = report["deterministic"]
    lines = [
        f"# Research report: {report['run_kind']}",
        "",
        f"- Status: **{report['status']}**",
        f"- Schema: `{report['schema_version']}`",
        f"- Deterministic SHA-256: `{report['deterministic_sha256']}`",
        f"- Config SHA-256: `{det.get('config_sha256')}`",
        (
            f"- Git revision: `{report['run_metadata'].get('git_revision')}` "
            f"(tracked changes: {report['run_metadata'].get('git_tracked_changes')})"
        ),
        f"- Created at: {report['run_metadata'].get('created_at')}",
        "",
        "## Checks",
        "",
    ]
    lines += [f"- [{c['status']}] {c['name']}: {c['detail']}" for c in det["checks"]] or ["- none"]
    lines += ["", "## Errors", ""]
    lines += [f"- {e}" for e in det["errors"]] or ["- none"]
    lines += ["", "## Results", "", "```json"]
    lines.append(json.dumps(det.get("results"), sort_keys=True, indent=2, ensure_ascii=True))
    lines += ["```", "", "## Limitations", ""]
    lines += [f"- {item}" for item in det["limitations"]] or ["- none"]
    lines += ["", "## What this does NOT prove", ""]
    lines += [f"- {item}" for item in det["does_not_prove"]] or ["- none"]
    return "\n".join(lines) + "\n"


class OutputBundle:
    """A staging directory that becomes `target` only on `commit()`; never overwrites."""

    def __init__(self, target: Path) -> None:
        target = Path(target)
        if target.exists():
            raise FileExistsError(
                f"output directory already exists, refusing to overwrite: {target}"
            )
        if not target.parent.is_dir():
            raise FileNotFoundError(f"parent directory does not exist: {target.parent}")
        self.target = target
        self.staging = target.parent / f".{target.name}.partial-{uuid.uuid4().hex[:12]}"
        self.staging.mkdir()

    def write_text(self, name: str, text: str) -> None:
        path = self.staging / name
        temporary = path.with_name(f".{path.name}.tmp")
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def write_report(self, report: dict[str, object]) -> None:
        self.write_text(REPORT_MARKDOWN, render_markdown(report))
        self.write_text(REPORT_JSON, json.dumps(report, sort_keys=True, indent=2) + "\n")

    def commit(self) -> Path:
        if self.target.exists():
            raise FileExistsError(f"output directory appeared meanwhile: {self.target}")
        os.rename(self.staging, self.target)
        return self.target

    def discard(self) -> None:
        shutil.rmtree(self.staging, ignore_errors=True)
