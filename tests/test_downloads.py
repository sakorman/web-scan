import hashlib
import json
from urllib.parse import unquote

import numpy as np
import pytest
from fastapi.testclient import TestClient

from webscan.api import create_app
from webscan.gaussian import export_gaussians


@pytest.fixture
def model(tmp_path):
    app = create_app(tmp_path, run_worker=False)
    store = app.state.store
    session = store.create_session("办公室 / 第一版")
    with store.connect() as db:
        db.execute("UPDATE sessions SET status='ready' WHERE id=?", (session["id"],))
    with TestClient(app) as client:
        job = client.post("/api/jobs", json={"session_id": session["id"]}).json()
        folder = store.job_dir(job["id"]) / "artifacts"
        export_gaussians(
            folder,
            np.array([[1.0, 2.0, 3.0]]),
            np.array([[0.1, 0.2, 0.3]]),
            np.array([[1.0, 0.0, 0.0, 0.0]]),
            np.array([0.8]),
            np.array([[0.3, 0.5, 0.7]]),
        )
        (folder / "cameras.json").write_text(json.dumps({"views": []}))
        (folder / "report.json").write_text(json.dumps({"gaussians": 1}))
        store.update(
            job["id"],
            stage="complete",
            progress=1,
            message="complete",
            status="succeeded",
        )
        yield client, job["id"], folder, store


def test_model_downloads_have_attachment_names_and_exact_bytes(model):
    client, jid, folder, _ = model
    listing = client.get(f"/api/jobs/{jid}/artifacts")
    assert listing.status_code == 200
    files = listing.json()["files"]
    assert {f["name"] for f in files} == {
        "gaussians.ply",
        "preview.splat",
        "cameras.json",
        "report.json",
    }
    for file in files:
        response = client.get(file["url"])
        expected = (folder / file["name"]).read_bytes()
        assert response.status_code == 200
        assert file["bytes"] == len(expected) == len(response.content)
        assert (
            hashlib.sha256(response.content).digest()
            == hashlib.sha256(expected).digest()
        )
        disposition = unquote(response.headers["content-disposition"])
        assert disposition.startswith("attachment;") and file["filename"] in disposition
        assert "/" not in file["filename"] and "办公室" in file["filename"]


def test_viewer_stays_inline_and_download_can_resume(model):
    client, jid, folder, _ = model
    preview = client.get(f"/api/jobs/{jid}/artifacts/preview.splat")
    assert "content-disposition" not in preview.headers
    assert preview.headers["content-type"] == "application/octet-stream"
    response = client.get(
        f"/api/jobs/{jid}/artifacts/gaussians.ply?download=true",
        headers={"Range": "bytes=0-31"},
    )
    assert response.status_code == 206
    assert response.content == (folder / "gaussians.ply").read_bytes()[:32]
    assert response.headers["content-range"].startswith("bytes 0-31/")


def test_missing_and_incomplete_downloads_are_explicit(model):
    client, jid, folder, store = model
    (folder / "preview.splat").unlink()
    assert "preview.splat" not in {
        f["name"] for f in client.get(f"/api/jobs/{jid}/artifacts").json()["files"]
    }
    assert (
        client.get(f"/api/jobs/{jid}/artifacts/preview.splat?download=true").status_code
        == 404
    )
    assert (
        client.get(f"/api/jobs/{jid}/artifacts/private.txt?download=true").status_code
        == 404
    )
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,))
    assert client.get(f"/api/jobs/{jid}/artifacts").status_code == 409
    assert (
        client.get(f"/api/jobs/{jid}/artifacts/gaussians.ply?download=true").status_code
        == 409
    )
