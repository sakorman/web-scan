from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import math
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .store import Store, uid
from .frame_order import ordered_frames
from .samples import SAMPLES, prepared_photos, sample_info

ROOT = Path(__file__).resolve().parent.parent
MAX_FILE = 16 * 1024 * 1024
MIN_CAPTURE_FRAMES = 12
Image.MAX_IMAGE_PIXELS = 24_000_000
DOWNLOAD_TYPES = {
    "gaussians.ply": ("model.ply", "application/octet-stream"),
    "preview.splat": ("preview.splat", "application/octet-stream"),
    "cameras.json": ("cameras.json", "application/json"),
    "report.json": ("report.json", "application/json"),
}


class SessionInput(BaseModel):
    name: str = Field(default="未命名采集", min_length=1, max_length=80)


class JobInput(BaseModel):
    session_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    backend: Literal["reference", "cuda", "brush"] = "reference"
    steps: int = Field(default=120, ge=10, le=10000)
    max_points: int = Field(default=384, ge=32, le=500000)
    resolution: int = Field(default=96, ge=32, le=1024)


def valid_id(value):
    if not re.fullmatch("[a-f0-9]{32}", value):
        raise HTTPException(404, "资源不存在")
    return value


def ingest_image(store, sid, raw, metadata):
    """Decode first; serialize final write with finalize through the same DB lock."""
    try:
        with Image.open(io.BytesIO(raw)) as original:
            if original.width * original.height > 24_000_000:
                raise ValueError("图片超过 2400 万像素")
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
            if min(image.size) < 32:
                raise ValueError("图片尺寸过小")
            out = io.BytesIO()
            image.save(out, format="JPEG", quality=94)
            content = out.getvalue()
            width, height = image.size
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
    ) as e:
        raise HTTPException(422, f"无法读取图片：{e}")
    sha = hashlib.sha256(content).hexdigest()
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute(
            "SELECT status,kind FROM sessions WHERE id=?", (sid,)
        ).fetchone()
        if not current:
            raise HTTPException(404, "采集不存在")
        if current["status"] != "capturing" or current["kind"] != "photos":
            raise HTTPException(409, "采集已冻结，不再接受图片")
        previous = db.execute(
            "SELECT id FROM frames WHERE session_id=? AND sha=?", (sid, sha)
        ).fetchone()
        if previous:
            return {"id": previous["id"], "duplicate": True}
        count = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(bytes),0) FROM frames WHERE session_id=?",
            (sid,),
        ).fetchone()
        if count[0] >= 200 or count[1] + len(content) > 512 * 1024 * 1024:
            raise HTTPException(413, "已达到单次采集容量上限（200 张 / 512 MB）")
        fid = uid()
        target = store.session_dir(sid) / "images" / f"{fid}.jpg"
        target.write_bytes(content)
        try:
            db.execute(
                "INSERT INTO frames VALUES(?,?,?,?,?,?,?)",
                (fid, sid, sha, width, height, len(content), json.dumps(metadata)),
            )
        except Exception:
            target.unlink(missing_ok=True)
            raise
    return {"id": fid, "duplicate": False, "width": width, "height": height}


def create_app(
    data: Path | None = None, *, run_worker=True, sample_root: Path | None = None
):
    store = Store(data or Path(os.environ.get("WEBSCAN_DATA", ROOT / ".data")))
    sample_root = sample_root if sample_root is not None else ROOT / ".samples"
    fixture_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        task = None
        if run_worker:
            from .runner import Runner

            runner = Runner(store)
            task = asyncio.create_task(runner.loop())
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Web Scan local API", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[
            "127.0.0.1",
            "localhost",
            "testserver",
            *filter(None, os.environ.get("WEBSCAN_ALLOWED_HOSTS", "").split(",")),
        ],
    )

    @app.middleware("http")
    async def local_origin(request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            allowed = {
                f"{request.url.scheme}://{request.headers.get('host')}",
                "http://127.0.0.1:5173",
                "http://localhost:5173",
            }
            if origin and origin not in allowed:
                return JSONResponse({"detail": "不接受跨站请求"}, status_code=403)
            length = request.headers.get("content-length")
            if length:
                try:
                    if int(length) > MAX_FILE + 1024 * 1024:
                        return JSONResponse({"detail": "请求过大"}, status_code=413)
                except ValueError:
                    return JSONResponse({"detail": "无效请求长度"}, status_code=400)
        return await call_next(request)

    @app.exception_handler(KeyError)
    async def missing(request, error):
        return JSONResponse({"detail": str(error).strip("'")}, status_code=404)

    @app.get("/api/health")
    def health():
        from .brush import executable

        versions = {}
        for name in ["torch", "pycolmap", "gsplat"]:
            try:
                versions[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                versions[name] = None
        return {
            "status": "ok",
            "mode": "local",
            "versions": versions,
            "reference_available": bool(versions["torch"]),
            "cuda_backend_installed": bool(versions["gsplat"]),
            "brush_available": executable() is not None,
            "indoor_sample_available": bool(
                prepared_photos(sample_root, SAMPLES["eth3d-office"])
            ),
            "max_frames": 200,
            "min_capture_frames": MIN_CAPTURE_FRAMES,
        }

    @app.get("/api/sessions")
    def sessions():
        with store.connect() as db:
            result = [
                dict(r)
                for r in db.execute(
                    """SELECT s.*, (SELECT COUNT(*) FROM frames f WHERE f.session_id=s.id) AS frame_count FROM sessions s WHERE s.status != 'deleted' ORDER BY created DESC"""
                )
            ]
            for row in result:
                job = db.execute(
                    "SELECT * FROM jobs WHERE session_id=? ORDER BY created DESC LIMIT 1",
                    (row["id"],),
                ).fetchone()
                row["job"] = store.decode_job(job) if job else None
            return result

    @app.post("/api/sessions", status_code=201)
    def new_session(body: SessionInput):
        return store.create_session(body.name.strip() or "未命名采集")

    @app.get("/api/sessions/{sid}")
    def session(sid: str):
        return store.session(valid_id(sid))

    @app.delete("/api/sessions/{sid}")
    def remove_session(sid: str):
        valid_id(sid)
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT id FROM sessions WHERE id=?", (sid,)).fetchone():
                raise HTTPException(404, "采集不存在")
            if db.execute(
                "SELECT id FROM jobs WHERE session_id=? AND status IN ('queued','running')",
                (sid,),
            ).fetchone():
                raise HTTPException(409, "请先取消正在运行的重建任务")
            db.execute("UPDATE sessions SET status='deleted' WHERE id=?", (sid,))
        return {"deleted": True, "files_preserved": True}

    @app.post("/api/sessions/{sid}/restore")
    def restore_session(sid: str):
        valid_id(sid)
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,kind FROM sessions WHERE id=?", (sid,)
            ).fetchone()
            if not row:
                raise HTTPException(404, "采集不存在")
            if row["status"] == "deleted":
                ready = (store.session_dir(sid) / "manifest.json").exists() or row[
                    "kind"
                ] == "fixture"
                db.execute(
                    "UPDATE sessions SET status=? WHERE id=?",
                    ("ready" if ready else "capturing", sid),
                )
        return store.session(sid)

    @app.post("/api/sessions/{sid}/frames")
    async def frame(sid: str, file: UploadFile = File(...), metadata: str = Form("{}")):
        valid_id(sid)
        if len(metadata) > 16000:
            raise HTTPException(413, "元数据过大")
        try:

            def reject_nonfinite(value):
                raise ValueError(f"Invalid JSON constant: {value}")

            def finite_float(value):
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError("JSON number exceeds finite range")
                return number

            meta = json.loads(
                metadata, parse_constant=reject_nonfinite, parse_float=finite_float
            )
            if not isinstance(meta, dict):
                raise ValueError()
        except (ValueError, TypeError):
            raise HTTPException(422, "元数据必须是 JSON 对象")
        raw = await file.read(MAX_FILE + 1)
        await file.close()
        if len(raw) > MAX_FILE:
            raise HTTPException(413, "单张图片不能超过 16 MB")
        return await asyncio.to_thread(ingest_image, store, sid, raw, meta)

    @app.get("/api/sessions/{sid}/frames/{fid}")
    def frame_image(sid: str, fid: str):
        valid_id(sid)
        valid_id(fid)
        with store.connect() as db:
            if not db.execute(
                "SELECT id FROM frames WHERE id=? AND session_id=?", (fid, sid)
            ).fetchone():
                raise HTTPException(404, "图片不存在")
        return FileResponse(
            store.session_dir(sid) / "images" / f"{fid}.jpg", media_type="image/jpeg"
        )

    @app.post("/api/sessions/{sid}/finalize")
    def finalize(sid: str):
        valid_id(sid)
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            if not row or row["status"] == "deleted":
                raise HTTPException(404, "采集不存在")
            if row["status"] == "ready":
                return {"status": "ready"}
            frames = ordered_frames(
                db.execute(
                    "SELECT * FROM frames WHERE session_id=? ORDER BY rowid", (sid,)
                )
            )
            minimum = 3 if row["kind"] == "fixture" else MIN_CAPTURE_FRAMES
            if len(frames) < minimum:
                raise HTTPException(
                    422,
                    f"至少需要 {minimum} 张不同的照片，还需 {minimum - len(frames)} 张",
                )
            snapshot = store.session_dir(sid) / "manifest.json"
            snapshot.write_text(
                json.dumps({"id": sid, "frames": frames}, ensure_ascii=False, indent=2)
            )
            db.execute("UPDATE sessions SET status='ready' WHERE id=?", (sid,))
        return {"status": "ready"}

    @app.post("/api/fixtures", status_code=201)
    async def fixture():
        if fixture_lock.locked():
            raise HTTPException(409, "示例正在创建，请稍候")
        async with fixture_lock:
            from .reconstruction import create_fixture

            sample = store.create_session("薄荷环 · 校准测试场景", "fixture")
            folder = store.session_dir(sample["id"])
            try:
                meta = await asyncio.to_thread(create_fixture, folder / "images")
                with store.connect() as db:
                    for v in meta["views"]:
                        previous = folder / "images" / v["name"]
                        raw = previous.read_bytes()
                        fid = uid()
                        name = f"{fid}.jpg"
                        previous.rename(folder / "images" / name)
                        v["name"] = name
                        db.execute(
                            "INSERT INTO frames VALUES(?,?,?,?,?,?,?)",
                            (
                                fid,
                                sample["id"],
                                hashlib.sha256(raw).hexdigest(),
                                128,
                                128,
                                len(raw),
                                "{}",
                            ),
                        )
                    (folder / "fixture.json").write_text(json.dumps(meta))
                    db.execute(
                        "UPDATE sessions SET status='ready' WHERE id=?", (sample["id"],)
                    )
                return store.session(sample["id"])
            except Exception:
                with store.connect() as db:
                    db.execute(
                        "UPDATE sessions SET status='failed' WHERE id=?",
                        (sample["id"],),
                    )
                raise

    def known_sample(sample_id):
        if sample_id not in SAMPLES:
            raise HTTPException(404, "示例不存在")
        return SAMPLES[sample_id]

    @app.get("/api/samples")
    def samples():
        return [sample_info(sample_root, sample) for sample in SAMPLES.values()]

    @app.get("/api/samples/{sample_id}/photos/{index}")
    def sample_photo(sample_id: str, index: int):
        photos = prepared_photos(sample_root, known_sample(sample_id))
        if not 0 <= index < len(photos):
            raise HTTPException(404, "示例照片不存在")
        return FileResponse(photos[index], media_type="image/jpeg")

    def read_sample(dataset):
        photos = prepared_photos(sample_root, dataset)
        if not photos:
            raise HTTPException(409, "示例照片尚未完整准备，请查看示例面板中的准备方法")
        manifest = json.loads((sample_root / dataset.id / "source.json").read_text())
        expected = {entry["name"]: entry.get("sha256") for entry in manifest["files"]}
        payload = []
        for photo in photos:
            raw = photo.read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected.get(photo.name):
                raise HTTPException(409, "示例照片已改变，请重新运行准备脚本")
            payload.append((photo.name, raw))
        return payload

    @app.post("/api/samples/{sample_id}/import", status_code=201)
    async def import_sample(sample_id: str):
        dataset = known_sample(sample_id)
        if fixture_lock.locked():
            raise HTTPException(409, "示例正在准备")
        async with fixture_lock:
            photos = await asyncio.to_thread(read_sample, dataset)
            sample = store.create_session(f"ETH3D · {dataset.title}")
            try:
                for index, (name, raw) in enumerate(photos):
                    await asyncio.to_thread(
                        ingest_image,
                        store,
                        sample["id"],
                        raw,
                        {
                            "source": f"ETH3D {dataset.scene}",
                            "original_name": name,
                            "upload_batch": sample["id"],
                            "capture_index": index,
                        },
                    )
                (store.session_dir(sample["id"]) / "source.json").write_text(
                    json.dumps(dataset.provenance(), ensure_ascii=False)
                )
                finalize(sample["id"])
                return store.session(sample["id"])
            except Exception:
                with store.connect() as db:
                    db.execute(
                        "UPDATE sessions SET status='failed' WHERE id=?",
                        (sample["id"],),
                    )
                raise

    @app.post("/api/indoor-sample", status_code=201)
    async def indoor_sample():
        return await import_sample("eth3d-office")

    @app.post("/api/jobs", status_code=201)
    def job(body: JobInput):
        from .brush import executable

        if body.backend == "brush":
            if not executable():
                raise HTTPException(422, "请先安装 Brush GPU 训练器")
            if body.steps < 500 or body.steps % 500:
                raise HTTPException(422, "Brush 步数需为 500 的倍数，至少 500")
            if store.session(body.session_id)["kind"] == "fixture":
                raise HTTPException(
                    422, "校准示例请使用 CPU 或 CUDA 模式；Brush 使用照片重建路径"
                )
        elif body.steps > 1000 or body.max_points > 4096 or body.resolution > 256:
            raise HTTPException(422, "参考 / CUDA 调试模式超过资源预算")
        if body.backend == "reference" and (
            body.max_points > 768 or body.resolution > 128
        ):
            raise HTTPException(422, "本地参考模式最多 768 个高斯、128 像素训练分辨率")
        if body.backend == "cuda":
            import torch

            if (
                not torch.cuda.is_available()
                or importlib.util.find_spec("gsplat") is None
            ):
                raise HTTPException(
                    422, "CUDA 模式需要 NVIDIA GPU 和 gsplat，当前不可用"
                )
        config = body.model_dump(exclude={"session_id"})
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status FROM sessions WHERE id=?", (body.session_id,)
            ).fetchone()
            if not row or row["status"] != "ready":
                raise HTTPException(409, "请先完成采集并冻结照片")
            existing = db.execute(
                "SELECT * FROM jobs WHERE session_id=? AND status IN ('queued','running')",
                (body.session_id,),
            ).fetchone()
            if existing:
                return store.decode_job(existing)
            jid = uid()
            now = time.time()
            db.execute(
                "INSERT INTO jobs(id,session_id,status,stage,message,config,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                (
                    jid,
                    body.session_id,
                    "queued",
                    "queued",
                    "等待本地计算",
                    json.dumps(config),
                    now,
                    now,
                ),
            )
        return store.job(jid)

    @app.get("/api/jobs/{jid}")
    def job_status(jid: str):
        return store.job(valid_id(jid))

    @app.post("/api/jobs/{jid}/cancel")
    def cancel(jid: str):
        valid_id(jid)
        store.job(jid)
        with store.connect() as db:
            db.execute(
                "UPDATE jobs SET status='cancelled',stage='cancelled',message='任务已取消',updated=? WHERE id=? AND status IN ('queued','running')",
                (time.time(), jid),
            )
        return store.job(jid)

    def completed_job(jid):
        valid_id(jid)
        value = store.job(jid)
        if value["status"] != "succeeded":
            raise HTTPException(409, "模型尚未生成")
        return value

    def download_filename(value, name):
        with store.connect() as db:
            row = db.execute(
                "SELECT name FROM sessions WHERE id=?", (value["session_id"],)
            ).fetchone()
        # Keep readable project names, but never use them as filesystem paths.
        title = re.sub(
            r'[\x00-\x1f\x7f<>:"/\\|?*]', "-", row["name"] if row else "web-scan"
        )
        title = title.strip(" .-")[:70] or "web-scan"
        return f"{title}-{value['id'][:8]}-{DOWNLOAD_TYPES[name][0]}"

    @app.get("/api/jobs/{jid}/artifacts")
    def artifacts(jid: str):
        value = completed_job(jid)
        result = []
        for name in DOWNLOAD_TYPES:
            file = store.job_dir(jid) / "artifacts" / name
            if file.is_file():
                result.append(
                    {
                        "name": name,
                        "bytes": file.stat().st_size,
                        "filename": download_filename(value, name),
                        "url": f"/api/jobs/{jid}/artifacts/{name}?download=true",
                    }
                )
        return {"job_id": jid, "files": result}

    @app.get("/api/jobs/{jid}/artifacts/{name}")
    def artifact(jid: str, name: str, download: bool = False):
        valid_id(jid)
        if name not in (
            "gaussians.ply",
            "preview.splat",
            "report.json",
            "thumbnail.jpg",
            "cameras.json",
        ):
            raise HTTPException(404, "产物不存在")
        value = completed_job(jid)
        file = store.job_dir(jid) / "artifacts" / name
        if not file.is_file():
            raise HTTPException(404, "产物不存在")
        as_download = download or name in ("gaussians.ply", "report.json")
        filename = (
            download_filename(value, name)
            if as_download and name in DOWNLOAD_TYPES
            else (name if as_download else None)
        )
        media_type = DOWNLOAD_TYPES[name][1] if name in DOWNLOAD_TYPES else "image/jpeg"
        return FileResponse(file, filename=filename, media_type=media_type)

    @app.get("/api/jobs/{jid}/log")
    def log(jid: str):
        valid_id(jid)
        store.job(jid)
        file = store.job_dir(jid) / "worker.log"
        return {
            "text": file.read_text(errors="replace")[-16000:]
            if file.exists()
            else "任务尚未开始"
        }

    if (ROOT / "dist").is_dir():
        app.mount("/", StaticFiles(directory=ROOT / "dist", html=True), name="web")
    else:

        @app.get("/")
        def no_build():
            return {
                "message": "前端尚未构建：运行 npm install && npm run build，或 npm run dev。",
                "docs": "/docs",
            }

    return app
