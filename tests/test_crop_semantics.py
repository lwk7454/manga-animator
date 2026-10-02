"""Synthetic semantic VLM replies; no remote page, network, or GPU required."""

import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from koma.application.crop_jobs import (
    confirm_page,
    enqueue_chapter,
    enqueue_page,
    local_client,
    process_pending,
    retry_page,
)
from koma.application.crops import replay_page
from koma.clients import LLMError, OpenAICompatClient
from koma.steps.crop_semantics import adjudicate
from koma.storage.crop_store import CropStore
from koma.storage.errors import BlockedPrerequisite
from koma.web.api import AppFactory
from tests import test_mask_crops as fixtures


@pytest.fixture(name="annotated")
def source_fixture(tmp_path):
    return fixtures.annotated.__wrapped__(tmp_path)


@pytest.fixture(name="crop_db")
def database_fixture(annotated, tmp_path):
    yield from fixtures.crop_db.__wrapped__(annotated, tmp_path)


class FakeVision:
    def __init__(self, mutate=None):
        self.calls = []
        self.closed = False
        self.mutate = mutate

    def chat_json(self, model, messages, **kwargs):
        prompt = json.loads(messages[-1]["content"])
        self.calls.append({"prompt": prompt, "model": model,
                           "sizes": [Image.open(p).size for p in kwargs["images"]]})
        assert kwargs["temperature"] == 0
        assert len(kwargs["images"]) <= 6
        if prompt["task"] == "reading_order":
            reply = {"reading_order": list(reversed(prompt["panel_ids"])),
                     "needs_review": False, "rationale": "Page flow and dialogue sequence"}
        else:
            reply = {"decisions": [{"object_id": c["id"], "owner_panel": "right",
                "kind": "bubble" if c["id"] == "bubble" else "not_object",
                "needs_review": False, "rationale": "Tail points to the right scene"}
                for c in prompt["objects"]]}
        return self.mutate(prompt, reply) if self.mutate else reply

    def close(self):
        self.closed = True


def test_legacy_annotation_hash_is_stable(annotated):
    from koma.crops import Annotation

    _, _, annotation = annotated
    payload = annotation.model_dump(mode="json")
    payload.pop("semantic")
    original = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode()
    assert Annotation.model_validate_json(original).canonical_bytes() == original


def test_full_page_semantic_order_and_displaced_bubble(annotated):
    page, _, annotation = annotated
    fake = FakeVision()
    result = adjudicate(page, annotation, fake, "local-vlm",
                        selected_panels=annotation.reading_order, job_id="job")
    assert result.reading_order == ["left", "right"]
    assert result.components[0].owner_panel == "right"
    assert result.ownership()["right"][3, 2]
    assert not result.ownership()["left"][3, 2]
    assert result.needs_review  # semantic confidence cannot certify boundary quality
    assert result.semantic["job_id"] == "job"
    assert len(fake.calls) == 2
    assert len(fake.calls[0]["sizes"]) == 1  # single page for order, avoids duplicate context
    assert len(fake.calls[1]["sizes"]) == 3  # overview, labels, native detail
    assert annotation.reading_order == ["right", "left"]  # no mutation before commit


def test_verbatim_prompt_echo_is_valid_but_requires_review(annotated):
    from koma.steps.crop_semantics import OrderReply, OwnershipReply

    def echo(prompt, reply):
        response = prompt["response"]
        schema = OrderReply if prompt["task"] == "reading_order" else OwnershipReply
        schema.model_validate_json(json.dumps(response))
        return response

    page, _, annotation = annotated
    result = adjudicate(page, annotation, FakeVision(echo), "local",
                        selected_panels=annotation.reading_order)
    assert result.reading_order == annotation.reading_order
    assert result.order_needs_review
    assert all(c.needs_review for c in result.components)


@pytest.mark.parametrize("defect", ["missing_owner", "unknown_owner", "duplicate", "bad_order"])
def test_invalid_vlm_replies_fail_closed(annotated, defect):
    def mutate(prompt, reply):
        if prompt["task"] == "reading_order" and defect == "bad_order":
            reply["reading_order"] = ["left", "left"]
        if prompt["task"] == "ownership":
            if defect == "missing_owner":
                reply["decisions"] = []
            elif defect == "unknown_owner":
                reply["decisions"][0]["owner_panel"] = "invented"
            elif defect == "duplicate":
                reply["decisions"] *= 2
        return reply
    page, _, annotation = annotated
    with pytest.raises(ValueError):
        adjudicate(page, annotation, FakeVision(mutate), "local",
                   selected_panels=annotation.reading_order)


def test_selective_rework_does_not_reorder_page(annotated):
    page, _, annotation = annotated
    fake = FakeVision()
    result = adjudicate(page, annotation, fake, "local", selected_panels=["right"],
                        note="Retain the entire bubble and tail")
    assert result.reading_order == annotation.reading_order
    assert [c["prompt"]["task"] for c in fake.calls] == ["ownership"]
    assert fake.calls[0]["prompt"]["reviewer_request"] == "Retain the entire bubble and tail"


def test_queue_process_resume_and_approval(crop_db):
    store, page, _ = crop_db
    fake = FakeVision()
    job = enqueue_chapter(store, "sample", 1)[0]
    assert enqueue_chapter(store, "sample", 1)[0]["id"] == job["id"]
    with pytest.raises(BlockedPrerequisite):
        confirm_page(store, page, 1, "reviewer")
    results = process_pending(store, client_factory=lambda: (fake, "local"))
    assert results[0]["state"] == "completed", results
    assert fake.closed
    current = CropStore(store.db).latest(page)
    assert current["decision_status"] == "open"
    assert enqueue_chapter(store, "sample", 1) == []  # no implicit rerun
    calls = len(fake.calls)
    assert process_pending(store, client_factory=lambda: (fake, "local")) == []
    assert len(fake.calls) == calls
    revision = confirm_page(store, page, current["revision"], "reviewer")
    assert CropStore(store.db).latest(page)["decision_status"] == "approved"
    enqueue_page(store, page, ["right"], revision, "Revisit bubble")
    with pytest.raises(BlockedPrerequisite, match="rework"):
        CropStore(store.db).require_approved(["right"])


def test_failure_persists_and_never_approves(crop_db):
    store, page, _ = crop_db
    job = enqueue_chapter(store, "sample", 1)[0]
    def offline():
        raise LLMError("local server offline")
    result = process_pending(store, client_factory=offline)
    assert result[0]["state"] == "failed"
    with store.db.transaction() as conn:
        saved = conn.execute("SELECT * FROM crop_jobs WHERE id=?", (job["id"],)).fetchone()
    assert saved["state"] == "failed" and "offline" in saved["error"]
    assert CropStore(store.db).latest(page)["revision"] == 1
    with pytest.raises(BlockedPrerequisite):
        CropStore(store.db).require_approved(["right"])
    enqueue_page(store, page, ["right"], 1, "Retry")
    assert process_pending(store, client_factory=lambda: (FakeVision(), "local"))[0]["state"] == (
        "completed"
    )


def test_failed_rework_blocks_previously_approved_page(crop_db):
    store, page, _ = crop_db
    revision = confirm_page(store, page, 1, "reviewer")
    enqueue_page(store, page, ["right"], revision, "Recheck tail")

    def offline():
        raise LLMError("offline")

    assert process_pending(store, client_factory=offline)[0]["state"] == "failed"
    with pytest.raises(BlockedPrerequisite):
        CropStore(store.db).require_approved(["right"])
    reviewed = confirm_page(store, page, revision, "reviewer")
    assert reviewed > revision
    CropStore(store.db).require_approved(["right"])


def test_refinement_preserves_existing_masks_and_proposes_faint_ink(annotated):
    import numpy as np

    from koma.crops import digest
    from koma.steps.crop_proposals import add_overfill_candidates

    page, _, annotation = annotated
    pixels = np.full((12, 16), 255, dtype=np.uint8)
    pixels[8, 5:11] = 235  # crosses the gutter, only the refinement threshold sees it
    Image.fromarray(pixels).save(page)
    annotation.source_sha256 = digest(page.read_bytes())
    initial = add_overfill_candidates(page, annotation)
    assert len(initial.components) == len(annotation.components)
    refined = add_overfill_candidates(page, initial, selected=["right"],
                                      refinement_key="request")
    assert len(refined.components) == len(initial.components) + 1
    assert refined.panels == initial.panels
    assert refined.components[-1].candidates == ["left", "right"]


def test_manual_edit_fences_pending_model_result(crop_db):
    store, page, annotation = crop_db
    enqueue_chapter(store, "sample", 1)
    annotation.order_rationale = "human edit in another browser tab"
    CropStore(store.db).submit(page, annotation, expected_revision=1)
    def must_not_call():
        raise AssertionError("stale job must not reach the model")
    results = process_pending(store, client_factory=must_not_call)
    assert results[0]["state"] == "conflict"
    assert CropStore(store.db).latest(page)["revision"] == 2


def test_recover_crash_after_result_commit_without_inference(crop_db):
    store, page, annotation = crop_db
    job = enqueue_chapter(store, "sample", 1)[0]
    annotation.semantic = {"job_id": job["id"]}
    CropStore(store.db).submit(page, annotation, expected_revision=1)
    with store.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE crop_jobs SET state='running' WHERE id=?", (job["id"],))
    def must_not_call():
        raise AssertionError("committed result must only replay")
    result = process_pending(store, client_factory=must_not_call)
    assert result[0]["state"] == "completed"
    assert result[0]["revision"] == 2


def test_local_vlm_uses_existing_config_and_public_urls_rejected(monkeypatch):
    monkeypatch.setenv("KOMA_VISION_BASE_URL", "https://example.com")
    with pytest.raises(LLMError, match="local"):
        local_client()


@pytest.mark.parametrize("suffix", ["", "/", "/v1", "/v1/"])
def test_real_adapter_sends_local_data_urls(annotated, respx_mock, suffix):
    import httpx

    page, _, annotation = annotated
    def response(request):
        body = json.loads(request.content)
        content = body["messages"][-1]["content"]
        assert all(item["image_url"]["url"].startswith("data:image/png;base64,")
                   for item in content[1:])
        prompt = json.loads(content[0]["text"])
        if prompt["task"] == "reading_order":
            reply = {"reading_order": ["right", "left"], "needs_review": False,
                     "rationale": "RTL"}
        else:
            reply = {"decisions": [{"object_id": "bubble", "owner_panel": "right",
                "kind": "bubble", "needs_review": False, "rationale": "tail"}]}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})
    route = respx_mock.post("http://127.0.0.1:11434/v1/chat/completions").mock(side_effect=response)
    client = OpenAICompatClient(base_url="http://127.0.0.1:11434" + suffix)
    try:
        adjudicate(page, annotation, client, "local", selected_panels=annotation.reading_order)
    finally:
        client.close()
    assert route.call_count == 2


def test_explicit_retry_keeps_failed_job_and_checks_revision(crop_db):
    from koma.storage.errors import RevisionConflict

    store, page, annotation = crop_db
    original = enqueue_page(store, page, ["right"], 1, "Keep the tail")

    def offline():
        raise LLMError("offline")

    process_pending(store, client_factory=offline)
    new_job = retry_page(store, page)
    assert new_job["id"] != original["id"]
    assert new_job["note"] == "Keep the tail"
    assert json.loads(new_job["selected_panels"]) == ["right"]
    with pytest.raises(RevisionConflict, match="pending"):
        retry_page(store, page)
    process_pending(store, client_factory=offline)
    annotation.order_rationale = "new human edit"
    CropStore(store.db).submit(page, annotation, expected_revision=1)
    with pytest.raises(RevisionConflict, match="stale"):
        retry_page(store, page)


def test_cli_explicit_region_upgrade_is_revisioned_and_queued(crop_db):
    from typer.testing import CliRunner

    from koma.cli import app
    from koma.crops import Annotation

    store, page, annotation = crop_db
    annotation.proposals = {p.id: p.mask for p in annotation.panels}
    CropStore(store.db).submit(page, annotation, expected_revision=1)
    before = CropStore(store.db).latest(page)
    reply = CliRunner().invoke(app, ["crop-retry", page, "--rebuild-regions", "--queue-only",
                                    "--data-dir", str(store.db.data_dir)])
    assert reply.exit_code == 0, reply.output
    after = CropStore(store.db).latest(page)
    assert after["revision"] == before["revision"] + 1
    upgraded = Annotation.model_validate_json(after["payload"])
    assert "region-proposals-v2" in upgraded.detector
    assert upgraded.needs_review
    assert all(p.id in upgraded.proposals for p in upgraded.panels)
    with store.db.transaction() as conn:
        job = conn.execute("SELECT * FROM crop_jobs WHERE page_id=?", (page,)).fetchone()
    assert job["expected_revision"] == after["revision"]
    assert job["state"] == "pending"


def test_web_review_rework_csrf_revision_and_media(crop_db):
    store, page, _ = crop_db
    replay_page(store, "sample", 1, page)
    app = AppFactory(store.db.data_dir).build()
    runner = app.state.crop_runner
    runner.client_factory = lambda: (FakeVision(), "local")
    with TestClient(app, base_url="http://127.0.0.1") as web:
        assert web.get("/crops").status_code == 200
        assert "Rework selected panels" in web.get("/crops").text
        path = f"/api/v1/crops/pages/{page}"
        rework = {"expected_revision": 1, "panel_ids": ["right"], "note": "tail"}
        assert web.post(path+"/rework", json=rework).status_code == 403
        csrf = {"x-koma-csrf": web.get("/api/v1/csrf").json()["token"]}
        assert web.post(path+"/rework", json={**rework, "expected_revision": 99},
                        headers=csrf).status_code == 409
        reply = web.post(path+"/rework", json=rework, headers=csrf)
        assert reply.status_code == 200, reply.text
        runner.thread.join(timeout=10)
        assert not runner.thread.is_alive()
        pages = web.get("/api/v1/crops/chapters/sample/1").json()["pages"]
        assert pages[0]["jobs"][0]["state"] == "completed", pages
        assert web.get(pages[0]["panels"][0]["url"]).headers["content-type"] == "image/png"
        assert web.get(path+"/source").status_code == 200
        body = {"expected_revision": pages[0]["revision"], "actor": "Reviewer"}
        assert web.post(path+"/approve", json=body, headers=csrf).status_code == 400
        assert web.post(path+"/approve", json={**body, "inspected_masks": True},
                        headers=csrf).status_code == 200
        assert web.post(path+"/approve", json={**body, "inspected_masks": True},
                        headers=csrf).status_code == 409
