import io
import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from webscan.api import create_app
from webscan.sfm_cache import cache_key


def test_quality_budget_and_shared_camera_dimension_guard(tmp_path, monkeypatch):
    monkeypatch.setattr("webscan.brush.executable", lambda: Path("/test/brush"))
    app = create_app(tmp_path, run_worker=False)
    with TestClient(app) as client:
        sid = client.post("/api/sessions", json={}).json()["id"]
        for i in range(12):
            raw = io.BytesIO()
            Image.new("RGB", (64 if i else 80, 48), (i * 20, 50, 90)).save(raw, "JPEG")
            client.post(
                f"/api/sessions/{sid}/frames", files={"file": ("p.jpg", raw.getvalue())}
            )
        client.post(f"/api/sessions/{sid}/finalize")
        config = {
            "session_id": sid,
            "backend": "brush",
            "steps": 30000,
            "resolution": 1600,
            "max_points": 300000,
            "camera_mode": "shared",
        }
        assert client.post("/api/jobs", json=config).status_code == 422
        response = client.post("/api/jobs", json={**config, "camera_mode": "auto"})
        assert response.status_code == 201
        assert response.json()["config"]["resolution"] == 1600
        assert (
            client.post("/api/jobs", json={**config, "steps": 30500}).status_code == 422
        )
        assert (
            client.post("/api/jobs", json={**config, "resolution": 1601}).status_code
            == 422
        )


def test_camera_configuration_changes_sfm_cache_key():
    manifest = {"frames": [{"id": "a", "sha": "b", "width": 1600, "height": 1000}]}
    assert cache_key(manifest, {"camera_mode": "auto"}) != cache_key(
        manifest, {"camera_mode": "shared"}
    )


def test_reference_photo_is_job_scoped_and_requires_finished_job(tmp_path):
    app = create_app(tmp_path, run_worker=False)
    store = app.state.store
    s = store.create_session("Reference")
    with store.connect() as db:
        db.execute("UPDATE sessions SET status='ready' WHERE id=?", (s["id"],))
    with TestClient(app) as client:
        job = client.post("/api/jobs", json={"session_id": s["id"]}).json()
        jid = job["id"]
        folder = store.job_dir(jid)
        (folder / "artifacts").mkdir(parents=True)
        (folder / "sfm/undistorted/images").mkdir(parents=True)
        camera = folder / "artifacts/cameras.json"
        camera.write_text(json.dumps({"views": [{"image_name": "001.jpg"}]}))
        (folder / "sfm/undistorted/images/001.jpg").write_bytes(b"local-photo")
        assert client.get(f"/api/jobs/{jid}/views/0").status_code == 409
        store.update(
            jid, stage="complete", progress=1, message="ready", status="succeeded"
        )
        assert client.get(f"/api/jobs/{jid}/views/0").content == b"local-photo"
        for i in [-1, 1]:
            assert client.get(f"/api/jobs/{jid}/views/{i}").status_code == 404
        camera.write_text(json.dumps({"views": [{"image_name": "../../private.jpg"}]}))
        assert client.get(f"/api/jobs/{jid}/views/0").status_code == 404
        camera.write_text(
            json.dumps({"views": [{"K": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}]})
        )
        assert client.get(f"/api/jobs/{jid}/views/0").status_code == 404
