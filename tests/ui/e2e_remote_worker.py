#!/usr/bin/env python3
"""Prueba real de extremo a extremo: API de Veyra en modo remoto + el worker de verdad (repo veyra-worker) con la GPU.

Entrena 1 época, detecta sobre imágenes y corre una sesión en vivo sobre un video, todo ejecutado por el worker.
Uso: python tests/ui/e2e_remote_worker.py     (requiere ../veyra-worker con su .venv, la base en marcha y la GPU)
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from harness import ROOT, Env

WORKER_REPO = ROOT.parent / "veyra-worker"
LEGACY = ROOT.parent / "cv-residue-diff" / "labeling"
env = Env("e2ew")
res = {}
worker = None


def check(name, ok, extra=""):
    print(("OK    " if ok else "FALLA ") + name + "  " + str(extra))
    res[name] = bool(ok)


try:
    env.data.mkdir(exist_ok=True)
    os.environ.update(LABELHUB_GPU_MODE="remote", LABELHUB_MODELS_ROOT=str(env.data / "models"), LABELHUB_RUNS_ROOT=str(env.data / "runs"),
                      LABELHUB_WORKER_OFFLINE_AFTER="30")
    from app.db import SessionLocal
    from app.legacy_import import import_legacy
    from app.models import Frame, Membership, Project, Video
    from app.security import hash_password

    labels = json.loads((LEGACY / "labels.json").read_text())
    frames = json.loads((LEGACY / "frames.json").read_text())
    need = {f"{f['video']}:{f['frame']}": f["image"] for f in frames if f"{f['video']}:{f['frame']}" in labels}
    for img in set(need.values()):
        (env.data / img).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(LEGACY / img, env.data / img)
    with SessionLocal() as db:
        from app.legacy_import import ensure_member, ensure_user

        info = import_legacy(db, LEGACY, "piojos", "Piojos")
        root_u = ensure_user(db, "tester", hash_password("pw-tester"), is_superuser=True)
        ensure_member(db, root_u, db.query(Project).one(), "admin")
        clip = env.data / "videos" / "clip.mp4"
        clip.parent.mkdir()
        src = sorted(Path("/home/uach/DEV/Jupyter/admin/CL/LICE").glob("*.mp4"))[0]
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-t", "6", "-an", "-c:v", "libx264", "-preset", "veryfast", str(clip)], check=True)
        vrow = db.query(Video).first()
        vrow.path = str(clip)
        db.commit()
        PID, VID = db.query(Project).one().id, vrow.id
    env.start_server()
    root = env.api("tester")

    tok = root.call("POST", "/api/admin/workers", {"name": "gpu-e2e"})["token"]
    wenv = {**os.environ, "VEYRA_API_URL": f"http://127.0.0.1:{env.port}", "VEYRA_WORKER_TOKEN": tok, "VEYRA_WORKER_NAME": "gpu-e2e",
            "VEYRA_DATA_ROOT": str(env.data), "VEYRA_PYTHON": "/usr/bin/python3", "VEYRA_ADVERTISE_HOST": "127.0.0.1",
            "VEYRA_PRETRAINED_DIRS": str(ROOT / "backend"), "VEYRA_LIVE_PORTS": "19300-19360", "VEYRA_POLL_WAIT": "5"}
    log = open(env.data / "worker.log", "w")
    worker = subprocess.Popen([str(WORKER_REPO / ".venv" / "bin" / "python"), "-m", "veyra_worker", "run"], env=wenv, stdout=log, stderr=subprocess.STDOUT)

    def until(cond, tmo=60):
        t0 = time.time()
        while time.time() - t0 < tmo:
            try:
                r = cond()
                if r:
                    return r
            except Exception:
                pass
            time.sleep(0.5)
        return None

    w = until(lambda: next((x for x in root.get("/api/admin/workers")["workers"] if x["online"]), None))
    check("el worker se registra y aparece en línea con su GPU", w and w["capabilities"]["gpus"] and "RTX" in w["capabilities"]["gpus"][0]["name"], w and w["capabilities"]["gpus"])

    # --- dataset y entrenamiento (1 época) en el worker
    ds = root.call("POST", f"/api/projects/{PID}/datasets", {"name": "e2e", "include": ["final", "agreed", "single"], "split": [0.7, 0.15, 0.15], "seed": "e2e"})
    run = root.call("POST", f"/api/projects/{PID}/trainings", {"dataset_version_id": ds["id"], "epochs": 1, "imgsz": 320, "batch": 4, "model_name": "e2e-m", "arch": "yolov8n"})
    rid = run["run_id"]
    r = until(lambda: (lambda x: x if x["status"] in ("done", "failed", "cancelled") else None)(root.get(f"/api/runs/{rid}")), 300)
    check("el entrenamiento corre en el worker y termina", r and r["status"] == "done", (r or {}).get("status") and (r["log"] or "")[-300:])
    models = root.get(f"/api/projects/{PID}/models")
    check("el modelo queda registrado con sus métricas y los pesos en la carpeta compartida", models and Path(models[0]["metrics"] and (env.data / "models").rglob("best.pt").__next__()).is_file() and "map50" in models[0]["metrics"], models and models[0]["metrics"])
    job_gpu = (env.data / "worker.log").read_text()
    check("el worker registró el trabajo de entrenamiento", "(train)" in job_gpu and "terminó: done" in job_gpu)

    # --- detección sobre imágenes
    mid = models[0]["id"]
    det = root.call("POST", f"/api/projects/{PID}/detect", {"model_id": mid, "first": 1, "last": 3})
    check("detectar imágenes se ejecuta en el worker y devuelve resultados", len(det["items"]) == 3 and all("dets" in i for i in det["items"]), [len(i["dets"]) for i in det["items"]])

    # --- sesión en vivo
    s = root.call("POST", f"/api/projects/{PID}/live", {"source_kind": "video", "video_id": VID, "model_id": mid, "threshold": 0.05, "imgsz": 640})
    sid = s["id"]
    st = until(lambda: (lambda d: d if d["stats"] and d["stats"]["frames"] > 5 else None)(root.get(f"/api/live/{sid}")), 40)
    check("la sesión en vivo corre en el worker y la API recibe sus estadísticas", st and st["stats"]["fps"] > 5, st and {k: st["stats"][k] for k in ("frames", "fps", "latency_ms")})
    req = urllib.request.Request(f"http://127.0.0.1:{env.port}/api/live/{sid}/stream")
    cj = [h for h in root.op.handlers if hasattr(h, "cookiejar")][0].cookiejar
    req.add_header("Cookie", "; ".join(f"{c.name}={c.value}" for c in cj))
    chunk = urllib.request.urlopen(req, timeout=15).read(60000)
    check("el flujo MJPEG del worker llega al navegador a través de la API", b"--frame" in chunk and b"image/jpeg" in chunk)
    root.call("DELETE", f"/api/live/{sid}")
    ended = until(lambda: root.get(f"/api/live/{sid}")["status"] == "stopped", 30)
    stopped = until(lambda: "terminó: cancelled" in (env.data / "worker.log").read_text(), 30)  # el worker lo sabe en su próximo latido
    check("detener la sesión detiene el proceso en el worker", ended and stopped)
    print("\nRESUMEN: %d/%d OK" % (sum(res.values()), len(res)))
finally:
    if worker:
        worker.terminate()
        try:
            worker.wait(15)
        except subprocess.TimeoutExpired:
            worker.kill()
    env.close()
sys.exit(0 if res and all(res.values()) else 1)
