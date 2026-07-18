import hashlib
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

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
    _bounded_summary_text,
    _evidence_registry,
    _parse_final_assessment,
    _parse_worker_evidence,
    _reviewer_evidence_payload,
    _safe_module_failure,
    adjudicate_assessment,
    baseline_findings,
    classify_profile,
    execute_analysis,
    llm_evidence,
)


def paper_document(text: str, source_format: SourceFormat = SourceFormat.PDF) -> PaperDocument:
    return PaperDocument(
        content_level=ContentLevel.FULL_TEXT,
        source_format=source_format,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        parser_name="test",
        parser_version="1",
        text=text,
    )


def test_profile_routing():
    assert classify_profile("We conducted a randomized controlled trial") == RubricProfile.RANDOMIZED
    assert classify_profile("A systematic review and meta-analysis") == RubricProfile.SYSTEMATIC_REVIEW
    assert classify_profile("A neural network simulation study") == RubricProfile.COMPUTATIONAL
    assert classify_profile("A transformer language model benchmark") == RubricProfile.COMPUTATIONAL
    assert (
        classify_profile(
            "Efficacy trial. Participants were randomized. Prior work included a systematic review."
        )
        == RubricProfile.RANDOMIZED
    )
    assert classify_profile("A theoretical perspective") == RubricProfile.COMMON_CORE


def test_baseline_is_conservative_and_traceable():
    findings = baseline_findings(
        "Abstract. Methods. Participants. Statistical analysis used confidence intervals. Results. Discussion. Data availability: repository.",
        RubricProfile.OBSERVATIONAL,
    )
    assert len(findings) == 24
    assert all(f.severity.value == "info" for f in findings)
    assert all(not f.paper_spans and f.grade.value == "not_assessed" for f in findings)
    assert all(not finding.title.lower().endswith("not assessed") for finding in findings)


def test_summary_text_truncates_at_a_word_boundary():
    text = "A grounded methodological explanation " * 20
    shortened = _bounded_summary_text(text)
    assert len(shortened) <= 281
    assert shortened.endswith("…")
    assert text.startswith(shortened[:-1])
    assert shortened[-2].isalnum()
    assert _bounded_summary_text("Already concise.") == "Already concise."


def test_worker_output_requires_the_exact_schema_without_grading():
    output = _parse_worker_evidence(
        {
            "evidence": [
            {
                "rubric_item": "study_question_design",
                "observation": "The study design is named explicitly.",
                "quotes": ["We conducted a randomized controlled trial."],
                "evidence_state": "observed",
            }
        ]},
        "design",
        ("study_question_design",),
        "Methods. We conducted a randomized controlled trial. Results.",
    )
    assert output.items[0].quotes == ["We conducted a randomized controlled trial."]
    assert "grade" not in output.items[0].model_dump()


def test_malformed_worker_output_fails_instead_of_becoming_evidence():
    with pytest.raises(ValueError):
        _parse_worker_evidence(
            "Useful notes, but definitely not JSON.",
            "claims",
            ("claim_strength",),
            "Title and abstract only.",
        )


def test_legacy_worker_field_aliases_are_rejected():
    with pytest.raises(ValueError):
        _parse_worker_evidence(
            {"items": [{"item": "claim_strength", "notes": "legacy"}]},
            "claims",
            ("claim_strength",),
            "Paper",
        )


def test_module_validation_diagnostics_expose_only_schema_locations():
    with pytest.raises(ValidationError) as caught:
        analysis_module.WorkerEvidenceOutput.model_validate(
            {"evidence": [{"rubric_item": "claim_strength", "unexpected": "secret"}]}
        )
    diagnostic = _safe_module_failure(caught.value)
    assert "evidence.0" in diagnostic
    assert "secret" not in diagnostic


def test_progress_notes_are_bounded_and_only_keep_grounded_quotes():
    paper = "Verified quote from the normalized paper."
    evidence = _parse_worker_evidence(
        {
            "evidence": [
                {
                    "rubric_item": "study_question_design",
                    "observation": "x" * 700,
                    "quotes": [paper, "invented quote"],
                    "evidence_state": "observed",
                }
            ]
        },
        "design",
        ("study_question_design",),
        paper,
    )
    notes = _analysis_notes([evidence])
    assert len(notes[0].observation) == 500
    assert notes[0].quotes == [paper]
    assert len(notes[0].quotes) <= 2


def test_worker_quotes_remain_internal_while_public_notes_keep_two_previews():
    paper = "First exact quote. Second exact quote. Third exact quote."
    evidence = _parse_worker_evidence(
        {
            "evidence": [
                {
                    "rubric_item": "claim_strength",
                    "observation": "Several passages support the extraction.",
                    "quotes": [
                        "First exact quote.",
                        "Second exact quote.",
                        "Third exact quote.",
                    ],
                    "evidence_state": "observed",
                }
            ]
        },
        "claims",
        ("claim_strength",),
        paper,
    )
    assert evidence.items[0].quotes == [
        "First exact quote.",
        "Second exact quote.",
        "Third exact quote.",
    ]
    assert _analysis_notes([evidence])[0].quotes == [
        "First exact quote.",
        "Second exact quote.",
    ]


def test_not_found_is_an_unreviewed_note_without_evidence_ids():
    evidence = _parse_worker_evidence(
        {
            "evidence": [
                {
                    "rubric_item": "conflict_statement",
                    "observation": "No explicit conflict statement was found.",
                    "quotes": [],
                    "evidence_state": "not_found",
                }
            ]
        },
        "disclosures",
        ("conflict_statement",),
        "Methods only.",
    )
    assert evidence.items[0].evidence_state == "not_found"
    assert evidence.items[0].evidence_ids == []


def test_unmatched_observed_quote_is_downgraded_without_failing_the_module():
    evidence = _parse_worker_evidence(
        {
            "evidence": [
                {
                    "rubric_item": "claim_strength",
                    "observation": "A paraphrased claim.",
                    "quotes": ["This quote is not in the paper."],
                    "evidence_state": "observed",
                }
            ]
        },
        "claims",
        ("claim_strength",),
        "The canonical paper has different text.",
    )
    assert evidence.items[0].evidence_state == "ambiguous"
    assert evidence.items[0].evidence_ids == []


def test_verified_evidence_ids_are_bound_to_module_item_and_state():
    document = paper_document("A shared exact quote.")
    design = _parse_worker_evidence(
        {"evidence": [{"rubric_item": "study_question_design", "observation": "Observed.", "quotes": [document.text], "evidence_state": "observed"}]},
        "design",
        ("study_question_design",),
        document,
    )
    claims = _parse_worker_evidence(
        {"evidence": [{"rubric_item": "claim_strength", "observation": "Observed.", "quotes": [document.text], "evidence_state": "observed"}]},
        "claims",
        ("claim_strength",),
        document,
    )
    assert design.items[0].evidence_ids != claims.items[0].evidence_ids

    registry = _evidence_registry([design, claims], document)
    result = _parse_final_assessment(
        {
            "assessments": [
                {
                    "rubric_item": "study_question_design",
                    "grade": "major_concern",
                    "explanation": "Cross-item evidence must not support this judgment.",
                    "evidence_ids": claims.items[0].evidence_ids,
                }
            ]
        },
        document,
        registry,
    )
    assert result.findings[0].grade == RubricGrade.NOT_ASSESSED


def test_reviewer_payload_is_valid_bounded_json_with_only_structured_worker_context():
    document = paper_document("Grounded quote.")
    evidence = _parse_worker_evidence(
        {"evidence": [{"rubric_item": "study_question_design", "observation": "A concise observation.", "quotes": ["Grounded quote."], "evidence_state": "observed"}]},
        "design",
        ("study_question_design",),
        document,
    )
    payload = _reviewer_evidence_payload(
        [evidence], RubricProfile.GENERAL_EMPIRICAL, 10_000, _evidence_registry([evidence], document)
    )
    serialized = json.dumps(payload)
    assert len(serialized) <= 10_000
    assert "Grounded quote." in serialized
    assert "A concise observation." in serialized


def test_final_assessment_is_the_only_source_of_grades_and_checks_quotes():
    document = paper_document("The study used a randomized design.")
    evidence = _parse_worker_evidence(
        {"evidence": [{"rubric_item": "study_question_design", "observation": "Design stated.", "quotes": [document.text], "evidence_state": "observed"}]},
        "design",
        ("study_question_design",),
        document,
    )
    registry = _evidence_registry([evidence], document)
    output = _parse_final_assessment(
        {
            "assessments": [
                {
                    "rubric_item": "study_question_design",
                    "grade": "no_concern",
                    "explanation": "The paper identifies its design.",
                    "evidence_ids": evidence.items[0].evidence_ids,
                },
                {
                    "rubric_item": "sampling_eligibility",
                    "grade": "major_concern",
                    "explanation": "This quote is absent.",
                    "evidence_ids": ["span-unknown"],
                },
            ],
        },
        document,
        registry,
    )
    by_item = {finding.rubric_item: finding for finding in output.findings}
    assert by_item["study_question_design"].grade == RubricGrade.NO_CONCERN
    assert by_item["study_question_design"].title == "Study Question Design"
    assert by_item["sampling_eligibility"].grade == RubricGrade.NOT_ASSESSED
    assert output.assessed_attempts == 2
    assert output.grounded_assessed == 1
    assert "comparators_controls" in output.missing_item_ids


def test_final_assessment_reports_duplicate_and_unknown_item_ids():
    document = paper_document("Randomized study.")
    evidence = _parse_worker_evidence(
        {"evidence": [{"rubric_item": "study_question_design", "observation": "Design stated.", "quotes": [document.text], "evidence_state": "observed"}]},
        "design",
        ("study_question_design",),
        document,
    )
    registry = _evidence_registry([evidence], document)
    decision = {
        "rubric_item": "study_question_design",
        "grade": "no_concern",
        "explanation": "The design is stated.",
        "evidence_ids": evidence.items[0].evidence_ids,
    }
    unknown = {**decision, "rubric_item": "invented_item"}
    output = _parse_final_assessment(
        {"assessments": [decision, decision, unknown]},
        document,
        registry,
    )
    assert len(output.findings) == 1
    assert any("Duplicate" in warning for warning in output.validation_warnings)
    assert any("Unknown" in warning for warning in output.validation_warnings)


@pytest.mark.asyncio
async def test_final_assessment_gets_one_format_repair_and_preserves_partial_result(monkeypatch):
    document = paper_document("The study used a randomized design.")
    evidence = _parse_worker_evidence(
        {"evidence": [{"rubric_item": "randomization_process", "observation": "Design stated.", "quotes": [document.text], "evidence_state": "observed"}]},
        "design",
        ("randomization_process",),
        document,
    )
    valid_partial = json.dumps(
        {
            "assessments": [
            {
                    "rubric_item": "randomization_process",
                    "grade": "no_concern",
                    "explanation": "The paper identifies its design.",
                    "evidence_ids": evidence.items[0].evidence_ids,
                }
            ],
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
        document,
        RubricProfile.RANDOMIZED,
        ContentLevel.FULL_TEXT,
        [evidence],
        {"reviewer_model": "reviewer", "worker_model": "worker"},
        "unused-key",
    )
    assert result.repaired_output
    assert result.findings[0].grade == RubricGrade.NO_CONCERN
    assert len(result.missing_item_ids) == 23


@pytest.mark.asyncio
async def test_reviewer_request_is_compact_and_has_no_hidden_retries(monkeypatch):
    captured: list[dict] = []

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        async def arun(self, prompt):
            captured[-1]["prompt"] = prompt
            return SimpleNamespace(content={"assessments": []}, metrics=None)

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
    assert captured[0]["model"].reasoning_effort is None


@pytest.mark.asyncio
async def test_module_progress_reports_completed_and_skipped_categories(monkeypatch):
    captured: list[dict] = []

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        async def arun(self, prompt):
            return SimpleNamespace(content={"evidence": []}, metrics=None)

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
    assert len(events) == 6
    assert sum(event[2] == "running" for event in events) == 1
    assert sum(event[2] == "completed" for event in events) == 1
    assert sum(event[2] == "skipped" for event in events) == 4
    assert all(item["structured_outputs"] for item in captured)
    assert all(item["output_schema"] is analysis_module.WorkerEvidenceOutput for item in captured)


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
                    rubric_item="study_question_design",
                    observation="The design evidence is incomplete.",
                    quotes=["Methods and participants."],
                    evidence_state="observed",
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
        assert [event["state"] for event in module_events[:5]] == ["pending"] * 5
        assert {event["key"] for event in module_events[:5]} == {
            module.key for module in analysis_module.load_methodology().definition.modules
        }
        db.delete(row)
        db.commit()
