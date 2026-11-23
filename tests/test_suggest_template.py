"""Guess the built-in template a photo of a piece looks like (the workshop PC answers)."""

from app.models.scan_job import ScanJob
from app.services import lab_templates
from tests.test_lab_areas import photo
from tests.test_scans import PC, claim


def ask(client, people, item, role="owner", data=None, ctype="image/jpeg"):
    return client.post(
        f"/lab/items/{item['id']}/suggest-template",
        headers=people[role]["h"],
        files={"photo": ("bed.jpg", photo() if data is None else data, ctype)},
    )


def answer(client, people, job_id, picks, worker=PC):
    return client.post(
        f"/scans/{job_id}/answer",
        headers=people["owner"]["h"],
        json={"worker": worker, "picks": picks},
    )


def suggestions(client, people, item):
    r = client.get(f"/lab/items/{item['id']}/suggestions", headers=people["owner"]["h"])
    assert r.status_code == 200, r.text
    return r.json()


def test_every_template_can_be_told_apart_in_words():
    labels = [c["label"] for c in lab_templates.choices()]
    assert len(labels) == len(lab_templates.all_templates()) == len(set(labels))
    assert set(lab_templates.LOOKS) == set(lab_templates.all_templates())


def test_a_photo_is_queued_with_the_choices_and_the_pc_answers_it(
    client, people, chair, db, store
):
    r = ask(client, people, chair)
    assert r.status_code == 201, r.text
    job = db.get(ScanJob, r.json()["id"])
    assert job.status == "uploaded" and job.params["shape"] == "suggest"
    assert job.params["choices"] == lab_templates.choices()
    key = job.source_keys[0]
    assert key in store.objects
    assert suggestions(client, people, chair)[0]["status"] == "uploaded"

    assert claim(client, people["owner"]["h"], job.id).status_code == 200
    first, second = [c["id"] for c in job.params["choices"][:2]]
    ok = answer(
        client,
        people,
        job.id,
        [{"id": first, "score": 0.7}, {"id": second, "score": 0.2}],
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "done" and ok.json()["files"] == 0
    assert key not in store.objects  # the photo only served the question
    got = suggestions(client, people, chair)[0]
    assert got["status"] == "done"
    assert [p["template"] for p in got["picks"]] == [first, second]
    assert got["picks"][0]["name"] and got["picks"][0]["size_hint"]
    # a retried call is harmless
    assert (
        answer(client, people, job.id, [{"id": first, "score": 0.7}]).status_code == 200
    )


def test_the_answer_must_be_one_of_the_choices_and_from_the_claiming_pc(
    client, people, chair, db, store
):
    job = db.get(ScanJob, ask(client, people, chair).json()["id"])
    first = job.params["choices"][0]["id"]
    pick = [{"id": first, "score": 0.5}]
    assert answer(client, people, job.id, pick).status_code == 409  # not claimed yet
    claim(client, people["owner"]["h"], job.id)
    assert answer(client, people, job.id, pick, worker="other-pc").status_code == 409
    bad = answer(client, people, job.id, [{"id": "a-sofa", "score": 0.5}])
    assert bad.status_code == 422
    assert (
        answer(client, people, job.id, [{"id": first, "score": 1.5}]).status_code == 422
    )
    db.expire_all()
    done = db.get(ScanJob, job.id)
    assert (
        done.status == "processing" and "picks" not in done.params
    )  # nothing was recorded


def test_only_a_suggest_job_takes_an_answer(client, people, chair, db, store):
    from tests.test_design_from_scan import finished_scan

    job, _ = finished_scan(db, store, item_id=chair["id"], status="processing")
    job.claimed_by = f"{people['owner']['id']}|{PC}"
    db.commit()
    assert (
        answer(
            client, people, job.id, [{"id": "stool-4-leg", "score": 0.5}]
        ).status_code
        == 409
    )


def test_who_may_ask_and_what_it_accepts(client, people, chair, store):
    for role in ("achari", "worker", "artisan", "sales"):
        assert ask(client, people, chair, role=role).status_code == 403, role
    assert ask(client, people, chair, role="manager").status_code == 201
    assert ask(client, people, chair, data=b"not a photo").status_code == 400
    gif = ask(client, people, chair, data=b"GIF89a", ctype="image/gif")
    assert gif.status_code == 415


def test_a_suggestion_is_not_a_scan_to_make_a_design_from(client, people, chair, store):
    ask(client, people, chair)
    r = client.get(f"/lab/items/{chair['id']}/scans", headers=people["owner"]["h"])
    assert r.status_code == 200 and r.json() == []


def test_without_file_storage_it_says_so(client, people, chair):
    r = ask(client, people, chair)
    assert r.status_code == 503 and "storage" in r.json()["detail"]
