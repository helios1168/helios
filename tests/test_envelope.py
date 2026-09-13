from pathlib import Path

import pytest
from pydantic import ValidationError

from helios import envelope as ev

ROOT = Path(__file__).resolve().parents[1]


def finding(**kw):
    base = dict(id="c1", claim="x", verdict="verified", method="proof", scope="universal")
    base.update(kw)
    return ev.Finding(**base)


def test_committed_schemas_match_models():
    for name in ev.SCHEMAS:
        committed = (ROOT / "schemas" / f"{name}.schema.json").read_text()
        assert committed == ev.schema_text(name), f"regenerate: uv run python -m helios.envelope"


def test_bounded_scope_needs_bound():
    with pytest.raises(ValidationError):
        finding(scope="bounded", method="exhaustive_finite_check")
    finding(scope="bounded", method="exhaustive_finite_check", bound={"k": "<=6"})


def test_exhaustive_check_is_never_universal():
    with pytest.raises(ValidationError):
        finding(method="exhaustive_finite_check")


def test_empirical_cannot_verify_universal():
    with pytest.raises(ValidationError):
        finding(method="empirical_support")


def test_needs_input_requires_question():
    with pytest.raises(ValidationError):
        ev.AgentReport(status="needs_input", summary="s")
    ev.AgentReport(status="needs_input", summary="s", question="which file?")


def test_overall_verdict_order():
    assert ev.overall_verdict([]) is None
    fs = [finding(id="a"), finding(id="b", verdict="inconclusive")]
    assert ev.overall_verdict(fs) is ev.Verdict.INCONCLUSIVE
    fs.append(finding(id="c", verdict="refuted", method="counterexample"))
    assert ev.overall_verdict(fs) is ev.Verdict.REFUTED


def test_envelope_binds_attempt_and_report():
    common = dict(
        task_id="hel-1", attempt=2, kind="impl", harness="fake", started_at="t",
        base_commit="abc", input_hashes={"prompt": "0"},
    )
    with pytest.raises(ValidationError):
        ev.Envelope(attempt_id="hel-1#1", execution_status="crashed", **common)
    with pytest.raises(ValidationError):
        ev.Envelope(attempt_id="hel-1#2", execution_status="completed", **common)
    ev.Envelope(attempt_id="hel-1#2", execution_status="missing_output", **common)
