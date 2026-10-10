import io
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from webscan.api import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, run_worker=False)) as client:
        yield client


def photo(color):
    out = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(out, "JPEG")
    return out.getvalue()


def prepared(client):
    sid = client.post("/api/sessions", json={"name": "My room"}).json()["id"]
    for color in [(i * 20, 50, 200 - i * 12) for i in range(12)]:
        response = client.post(
            f"/api/sessions/{sid}/frames",
            files={"file": ("x.jpg", photo(color), "image/jpeg")},
        )
        assert response.status_code == 200, response.text
    assert client.post(f"/api/sessions/{sid}/finalize").status_code == 200
    return sid


def test_duplicate_upload_and_frozen_input(client):
    sid = prepared(client)
    assert len(client.get(f"/api/sessions/{sid}").json()["frames"]) == 12
    assert (
        client.post(
            f"/api/sessions/{sid}/frames", files={"file": ("x.jpg", photo("yellow"))}
        ).status_code
        == 409
    )
    assert client.post(f"/api/sessions/{sid}/finalize").status_code == 200


def test_duplicate_and_invalid_images(client):
    sid = client.post("/api/sessions", json={}).json()["id"]
    payload = {"file": ("x.jpg", photo("red"))}
    a = client.post(f"/api/sessions/{sid}/frames", files=payload).json()
    b = client.post(f"/api/sessions/{sid}/frames", files=payload).json()
    assert a["id"] == b["id"] and b["duplicate"]
    assert (
        client.post(
            f"/api/sessions/{sid}/frames", files={"file": ("x.jpg", b"not a jpeg")}
        ).status_code
        == 422
    )
    assert client.post(f"/api/sessions/{sid}/finalize").status_code == 422


def test_job_idempotency_cancel_and_no_premature_artifacts(client):
    sid = prepared(client)

    def create():
        return client.post("/api/jobs", json={"session_id": sid}).json()

    with ThreadPoolExecutor(3) as pool:
        jobs = list(pool.map(lambda _: create(), range(3)))
    assert len({j["id"] for j in jobs}) == 1
    jid = jobs[0]["id"]
    assert client.get(f"/api/jobs/{jid}/artifacts/gaussians.ply").status_code == 409
    assert client.post(f"/api/jobs/{jid}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/jobs/{jid}/cancel").json()["status"] == "cancelled"
    assert create()["id"] != jid


def test_resource_guards(client):
    assert client.get("/api/jobs/invalid").status_code == 404
    assert (
        client.post(
            "/api/sessions", json={}, headers={"origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.get("/api/health", headers={"host": "evil.example"}).status_code == 400
    )
    sid = prepared(client)
    assert (
        client.post(
            "/api/jobs", json={"session_id": sid, "resolution": 256}
        ).status_code
        == 422
    )
    assert (
        client.post("/api/jobs", json={"session_id": sid, "steps": 100000}).status_code
        == 422
    )
    assert client.get(f"/api/sessions/{sid}/frames/{'a' * 32}").status_code == 404


def test_data_survives_app_restart(tmp_path):
    with TestClient(create_app(tmp_path, run_worker=False)) as first:
        sid = prepared(first)
    with TestClient(create_app(tmp_path, run_worker=False)) as second:
        assert len(second.get(f"/api/sessions/{sid}").json()["frames"]) == 12
        assert second.get(f"/api/sessions/{sid}").json()["status"] == "ready"


def test_remove_preserves_files_and_can_be_restored(client):
    sid = prepared(client)
    first = client.get(f"/api/sessions/{sid}").json()["frames"][0]["id"]
    expected = client.get(f"/api/sessions/{sid}/frames/{first}").content
    assert client.delete(f"/api/sessions/{sid}").json()["files_preserved"] is True
    assert sid not in [s["id"] for s in client.get("/api/sessions").json()]
    assert client.get(f"/api/sessions/{sid}").status_code == 404
    assert client.post(f"/api/sessions/{sid}/finalize").status_code == 404
    assert client.post(f"/api/sessions/{sid}/restore").json()["status"] == "ready"
    assert client.get(f"/api/sessions/{sid}/frames/{first}").content == expected


def test_remove_rejects_active_reconstruction(client):
    sid = prepared(client)
    client.post("/api/jobs", json={"session_id": sid})
    assert client.delete(f"/api/sessions/{sid}").status_code == 409


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_invalid_sensor_metadata_cannot_break_session_reads(client, constant):
    sid = client.post("/api/sessions", json={}).json()["id"]
    response = client.post(
        f"/api/sessions/{sid}/frames",
        files={"file": ("frame.jpg", photo("red"), "image/jpeg")},
        data={"metadata": '{"sensor":{"timestamp":' + constant + "}}"},
    )
    assert response.status_code == 422
    session = client.get(f"/api/sessions/{sid}")
    assert session.status_code == 200
    assert session.json()["frames"] == []


def test_capture_requires_twelve_distinct_photos(client):
    sid = client.post("/api/sessions", json={"name": "room"}).json()["id"]
    for i in range(11):
        client.post(
            f"/api/sessions/{sid}/frames",
            files={"file": ("f.jpg", photo((i * 20, 70, 40)))},
        )
    response = client.post(f"/api/sessions/{sid}/finalize")
    assert response.status_code == 422
    assert "还需 1 张" in response.json()["detail"]
    assert client.get(f"/api/sessions/{sid}").json()["status"] == "capturing"
    client.post(
        f"/api/sessions/{sid}/frames", files={"file": ("f.jpg", photo("white"))}
    )
    assert client.post(f"/api/sessions/{sid}/finalize").status_code == 200
