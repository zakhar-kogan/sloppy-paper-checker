import hashlib
import json
from types import SimpleNamespace

import pytest

from sloppy_checker.core.config import AppSettings
from sloppy_checker.core.database import AnalysisRow, DocumentRow, SessionLocal, create_schema
from sloppy_checker.core.schemas import (
    ContentLevel,
    PaperDocument,
    PaperIdentity,
    RubricGrade,
    RubricProfile,
    SourceFormat,
)
from sloppy_checker.core.storage import get_document_store
from sloppy_checker.workflows import analysis as analysis_module
from sloppy_checker.workflows.analysis import (
    _analysis_notes,
    _await_active_reviewer,
    _coerce_worker_evidence,
    _parse_final_assessment,
    _reviewer_evidence_payload,
    adjudicate_assessment,
    baseline_findings,
    classify_profile,
    execute_analysis,
    llm_evidence,
)


def test_profile_routing():
    assert classify_profile("We conducted a randomized controlled trial") == RubricProfile.RANDOMIZED
    assert classify_profile("A systematic review and meta-analysis") == RubricProfile.SYSTEMATIC_REVIEW
    assert classify_profile("A neural network simulation study") == RubricProfile.COMPUTATIONAL
    assert classify_profile("A theoretical perspective") == RubricProfile.COMMON_CORE


def test_baseline_is_conservative_and_traceable():
    findings = baseline_findings(
        "Abstract. Methods. Participants. Statistical analysis used confidence intervals. Results. Discussion. Data availability: repository.",
        RubricProfile.OBSERVATIONAL,
    )
    assert len(findings) == 28
    assert all(f.severity.value == "info" for f in findings)
    assert all(not f.paper_spans and f.grade.value == "not_assessed" for f in findings)


def test_worker_output_accepts_common_openai_compatible_array_shape_without_grading():
    output = _coerce_worker_evidence(
        [
            {
                "rubric_item": "design_identification",
                "judgment": "critical_concern",
                "evidence": "We conducted a randomized controlled trial.",
                "reasoning": "The study design is named explicitly.",
            }
        ],
        "design",
        ["design_identification"],
        "Methods. We conducted a randomized controlled trial. Results.",
    )
    assert output.items[0].quotes == ["We conducted a randomized controlled trial."]
    assert "grade" not in output.items[0].model_dump()


def test_malformed_worker_output_is_preserved_as_text():
    output = _coerce_worker_evidence(
        "Useful notes, but definitely not JSON: funding is described in acknowledgments.",
        "record",
        ["identity_and_version"],
        "Title and abstract only.",
    )
    assert not output.items
    assert output.raw_text.startswith("Useful notes")


def test_empty_worker_output_is_not_mistaken_for_evidence():
    output = _coerce_worker_evidence(None, "record", ["identity_consistency"], "Paper")
    assert output.raw_text == ""
    assert not output.items


def test_progress_notes_are_bounded_and_only_keep_grounded_quotes():
    paper = "Verified quote from the normalized paper."
    evidence = _coerce_worker_evidence(
        {
            "evidence": [
                {
                    "rubric_item": "study_design",
                    "observation": "x" * 700,
                    "quotes": [paper, "invented quote", paper],
                }
            ]
        },
        "design",
        ["study_design"],
        paper,
    )
    notes = _analysis_notes([evidence])
    assert len(notes[0].observation) == 500
    assert notes[0].quotes == [paper]
    assert len(notes[0].quotes) <= 2


def test_reviewer_payload_is_valid_bounded_json_without_raw_worker_text():
    evidence = analysis_module.WorkerEvidence(
        module="design",
        items=[
            analysis_module.EvidenceNote(
                rubric_item="study_design",
                observation="A concise observation.",
                quotes=["Grounded quote."],
            )
        ],
        raw_text="RAW MODEL RESPONSE MUST NOT LEAK",
    )
    payload = _reviewer_evidence_payload([evidence], 10_000)
    serialized = json.dumps(payload)
    assert len(serialized) <= 10_000
    assert "RAW MODEL RESPONSE" not in serialized
    assert "Grounded quote." in serialized


def test_final_assessment_is_the_only_source_of_grades_and_checks_quotes():
    output = _parse_final_assessment(
        {
            "assessments": [
                {
                    "rubric_item": "study_design",
                    "grade": "no_concern",
                    "explanation": "The paper identifies its design.",
                    "confidence": 0.9,
                    "evidence_quotes": ["The study used a randomized design."],
                },
                {
                    "rubric_item": "sampling",
                    "grade": "major_concern",
                    "explanation": "This quote is absent.",
                    "confidence": 0.8,
                    "evidence_quotes": ["Not in the normalized paper."],
                },
            ],
            "summary": ["Two items were returned."],
        },
        "The study used a randomized design.",
    )
    by_item = {finding.rubric_item: finding for finding in output.findings}
    assert by_item["study_design"].grade == RubricGrade.NO_CONCERN
    assert by_item["sampling"].grade == RubricGrade.NOT_ASSESSED
    assert output.assessed_attempts == 2
    assert output.grounded_assessed == 1
    assert "comparators" in output.missing_item_ids


def test_final_assessment_reports_duplicate_and_unknown_item_ids():
    decision = {
        "rubric_item": "study_design",
        "grade": "no_concern",
        "explanation": "The design is stated.",
        "confidence": 0.8,
        "evidence_quotes": ["Randomized study."],
    }
    unknown = {**decision, "rubric_item": "invented_item"}
    output = _parse_final_assessment(
        {"assessments": [decision, decision, unknown], "summary": []},
        "Randomized study.",
    )
    assert len(output.findings) == 1
    assert any("Duplicate" in warning for warning in output.validation_warnings)
    assert any("Unknown" in warning for warning in output.validation_warnings)


@pytest.mark.asyncio
async def test_final_assessment_gets_one_format_repair_and_preserves_partial_result(monkeypatch):
    valid_partial = json.dumps(
        {
            "assessments": [
            {
                    "rubric_item": "study_design",
                    "grade": "no_concern",
                    "explanation": "The paper identifies its design.",
                    "confidence": 0.9,
                    "evidence_quotes": ["The study used a randomized design."],
                }
            ],
            "summary": ["Partial but usable assessment."],
        }
    )
    responses = iter(
        [
            SimpleNamespace(content="{malformed", metrics=None),
            SimpleNamespace(content=valid_partial, metrics=None),
        ]
    )

    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        async def arun(self, prompt):
            return next(responses)

    monkeypatch.setattr(analysis_module, "Agent", FakeAgent)
    result = await adjudicate_assessment(
        "The study used a randomized design.",
        RubricProfile.RANDOMIZED,
        ContentLevel.FULL_TEXT,
        [],
        {"reviewer_model": "reviewer", "worker_model": "worker"},
        "unused-key",
    )
    assert result.repaired_output
    assert result.findings[0].grade == RubricGrade.NO_CONCERN
    assert len(result.missing_item_ids) == 27


@pytest.mark.asyncio
async def test_reviewer_request_is_compact_and_has_no_hidden_retries(monkeypatch):
    captured: list[dict] = []

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        async def arun(self, prompt):
            captured[-1]["prompt"] = prompt
            return SimpleNamespace(content={"assessments": [], "summary": []}, metrics=None)

    monkeypatch.setattr(analysis_module, "Agent", FakeAgent)
    paper_text = "FULL PAPER SENTINEL THAT MUST NOT ENTER THE REVIEWER PROMPT"
    await adjudicate_assessment(
        paper_text,
        RubricProfile.COMMON_CORE,
        ContentLevel.FULL_TEXT,
        [],
        {"reviewer_model": "reviewer", "worker_model": "worker"},
        "unused-key",
    )
    assert paper_text not in captured[0]["prompt"]
    assert captured[0]["model"].retries == 0
    assert captured[0]["model"].max_completion_tokens == 6144
    assert captured[0]["model"].reasoning_effort == "low"


@pytest.mark.asyncio
async def test_module_progress_reports_completed_and_skipped_categories(monkeypatch):
    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        async def arun(self, prompt):
            return SimpleNamespace(content="Evidence note", metrics=None)

    monkeypatch.setattr(analysis_module, "Agent", FakeAgent)
    events = []
    await llm_evidence(
        "Title and abstract",
        RubricProfile.COMMON_CORE,
        ContentLevel.METADATA,
        {"worker_model": "worker"},
        "unused-key",
        False,
        lambda *event: events.append(event),
    )
    assert len(events) == 8
    assert sum(event[2] == "running" for event in events) == 2
    assert sum(event[2] == "completed" for event in events) == 2
    assert sum(event[2] == "skipped" for event in events) == 4


@pytest.mark.asyncio
async def test_reviewer_wait_has_a_total_deadline():
    row = SimpleNamespace(cancel_requested=False)
    db = SimpleNamespace(refresh=lambda *args, **kwargs: None)

    async def never_finishes():
        await analysis_module.asyncio.sleep(60)

    with pytest.raises(TimeoutError, match="total deadline"):
        await _await_active_reviewer(never_finishes(), row, db, 0.01)


@pytest.mark.asyncio
async def test_reviewer_failure_completes_a_provisional_report(monkeypatch, tmp_path):
    create_schema()
    text = "Methods and participants. Results and discussion."
    document = PaperDocument(
        identity=PaperIdentity(title="Reviewer failure fixture"),
        content_level=ContentLevel.FULL_TEXT,
        source_format=SourceFormat.PDF,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        parser_name="test",
        parser_version="1",
        text=text,
    )
    settings = AppSettings(
        document_store="filesystem",
        document_store_path=tmp_path,
        analysis_dispatcher="inline",
        reviewer_deadline_seconds=30,
    )
    object_key = get_document_store(settings).put(document)

    async def worker_evidence(*args, **kwargs):
        callback = args[-1]
        worker = analysis_module.WorkerEvidence(
            module="design",
            items=[
                analysis_module.EvidenceNote(
                    rubric_item="study_design",
                    observation="The design evidence is incomplete.",
                    quotes=["Methods and participants."],
                )
            ],
        )
        for module in analysis_module.load_methodology().definition.modules:
            module_notes = _analysis_notes([worker]) if module.key == "design" else []
            callback(module.key, module.label, "completed", len(module_notes), None, module_notes)
        return [worker], {}, {}

    async def failed_reviewer(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(analysis_module, "llm_evidence", worker_evidence)
    monkeypatch.setattr(analysis_module, "adjudicate_assessment", failed_reviewer)
    with SessionLocal() as db:
        document_row = DocumentRow(
            object_key=object_key,
            sha256=document.sha256,
            content_level=document.content_level.value,
            source_format=document.source_format.value,
        )
        db.add(document_row)
        db.flush()
        row = AnalysisRow(source={"kind": "document", "value": document_row.id}, request={})
        db.add(row)
        db.commit()
        await execute_analysis(
            row.id,
            db,
            settings,
            {"api_key": "test", "worker_model": "worker", "reviewer_model": "reviewer"},
        )
        db.refresh(row)
        assert row.state == "completed"
        assert row.report["coverage"]["provisional"] is True
        assert any("Final reviewer did not complete" in item for item in row.report["execution_warnings"])
        assert not any("No final adjudicator model" in item for item in row.report["execution_warnings"])
        assert all(item["grade"] == "not_assessed" for item in row.report["findings"])
        assert row.report["evidence_notes"][0]["observation"] == "The design evidence is incomplete."
        module_events = [event for event in row.events if event["kind"] == "module"]
        assert [event["state"] for event in module_events[:6]] == ["pending"] * 6
        assert {event["key"] for event in module_events[:6]} == {
            module.key for module in analysis_module.load_methodology().definition.modules
        }
        db.delete(row)
        db.commit()
