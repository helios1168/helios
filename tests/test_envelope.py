import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from helios import envelope as ev

ROOT = Path(__file__).resolve().parents[1]


def _iter_nodes(node):
    """Yield every dict schema node in a JSON Schema tree, node itself included."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_nodes(item)


def _iter_object_schemas(node):
    """Yield every node that declares `properties` (a fixed-shape object schema)."""
    return (n for n in _iter_nodes(node) if isinstance(n.get("properties"), dict))


def finding(**kw):
    base: dict[str, object] = dict(
        id="c1", claim="x", verdict="verified", method="proof", scope="universal"
    )
    base.update(kw)
    return ev.Finding.model_validate(base)


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
        ev.AgentReport(status=ev.WorkStatus.NEEDS_INPUT, summary="s")
    ev.AgentReport(status=ev.WorkStatus.NEEDS_INPUT, summary="s", question="which file?")


def test_overall_verdict_order():
    assert ev.overall_verdict([]) is None
    fs = [finding(id="a"), finding(id="b", verdict="inconclusive")]
    assert ev.overall_verdict(fs) is ev.Verdict.INCONCLUSIVE
    fs.append(finding(id="c", verdict="refuted", method="counterexample"))
    assert ev.overall_verdict(fs) is ev.Verdict.REFUTED


def test_envelope_binds_attempt_and_report():
    common: dict[str, object] = dict(
        task_id="hel-1", attempt=2, kind="impl", harness="fake", started_at="t",
        base_commit="abc", input_hashes={"prompt": "0"},
    )
    with pytest.raises(ValidationError):
        ev.Envelope.model_validate({"attempt_id": "hel-1#1", "execution_status": "crashed", **common})
    with pytest.raises(ValidationError):
        ev.Envelope.model_validate({"attempt_id": "hel-1#2", "execution_status": "completed", **common})
    ev.Envelope.model_validate({"attempt_id": "hel-1#2", "execution_status": "missing_output", **common})


def test_strict_agent_report_objects_are_closed_and_fully_required():
    """Every object schema in the strict agent-report schema, $defs and root included,
    rejects unknown properties and lists every property as required (SPEC §6.3 codex,
    the OpenAI structured-outputs strict-mode contract)."""
    schema = json.loads(ev.schema_text("agent-report"))
    object_schemas = list(_iter_object_schemas(schema))
    assert len(object_schemas) >= 4  # AgentReport, Finding, Checker, TestRun at least
    for node in object_schemas:
        assert node["additionalProperties"] is False
        assert set(node["required"]) == set(node["properties"])


def test_strict_agent_report_has_no_default_keyword():
    schema = json.loads(ev.schema_text("agent-report"))
    for node in _iter_nodes(schema):
        assert "default" not in node


def test_strict_agent_report_has_no_ref_with_siblings():
    schema = json.loads(ev.schema_text("agent-report"))
    for node in _iter_nodes(schema):
        if "$ref" in node:
            assert set(node) == {"$ref"}


def test_strict_report_round_trip_with_explicit_nulls():
    """A strict-mode model must send every property, so a field it has nothing to say
    about arrives as explicit null rather than being omitted or sent as an empty
    container. Validating that null must land on exactly the value the field already
    had, so the round trip is lossless."""
    report = ev.AgentReport(
        status=ev.WorkStatus.DONE,
        summary="did the thing",
        files_changed=["src/a.py"],
        tests=ev.TestRun(command="uv run pytest -q", passed=3, failed=0, exit_code=0),
        findings=[
            ev.Finding(
                id="c1",
                claim="x",
                verdict=ev.Verdict.VERIFIED,
                method=ev.Method.PROOF,
                scope=ev.Scope.UNIVERSAL,
                covers=["R1"],
                assumptions=["a"],
                artifact="tools/verify/u/check.py",
                checker=ev.Checker(command="python check.py", exit_code=0, output_path="out.log"),
                notes="fine",
            )
        ],
        learned=["l1"],
    )
    dumped = report.model_dump(mode="json")
    # These fields are unset (at their default) in `report`; a strict-mode model sends
    # null for them instead of "1" or "[]".
    for key in ("schema_version", "missing_context", "followups", "question"):
        dumped[key] = None
    restored = ev.AgentReport.model_validate(dumped)
    assert restored == report


@pytest.mark.parametrize(
    "field,default",
    [
        ("schema_version", "1"),
        ("files_changed", []),
        ("findings", []),
        ("learned", []),
        ("missing_context", []),
        ("followups", []),
    ],
)
def test_agent_report_null_for_non_nullable_field_gets_default(field, default):
    """`schema_version` and the list fields do not admit None in their pydantic
    annotation, but the strict schema makes them nullable (they were optional). Explicit
    null must validate and fall back to the field's own default."""
    data: dict[str, object] = {"status": "done", "summary": "s", field: None}
    report = ev.AgentReport.model_validate(data)
    assert getattr(report, field) == default


@pytest.mark.parametrize(
    "field,default",
    [
        ("covers", []),
        ("assumptions", []),
        ("notes", ""),
    ],
)
def test_finding_null_for_non_nullable_field_gets_default(field, default):
    data: dict[str, object] = dict(
        id="c1", claim="x", verdict="verified", method="proof", scope="universal"
    )
    data[field] = None
    f = ev.Finding.model_validate(data)
    assert getattr(f, field) == default


def test_strict_agent_report_sample_validates_with_jsonschema():
    jsonschema = pytest.importorskip(
        "jsonschema", reason="jsonschema is not a project dependency"
    )
    schema = json.loads(ev.schema_text("agent-report"))
    sample = ev.AgentReport(status=ev.WorkStatus.DONE, summary="s").model_dump(mode="json")
    sample["missing_context"] = None
    sample["followups"] = None
    jsonschema.validate(sample, schema)


def test_envelope_schema_is_plain_pydantic_schema():
    """schema_text("envelope") must stay the ordinary pydantic schema; only agent-report
    goes through strict_schema (SPEC §6.3 codex)."""
    plain = json.dumps(ev.Envelope.model_json_schema(), indent=2, sort_keys=True) + "\n"
    assert ev.schema_text("envelope") == plain
