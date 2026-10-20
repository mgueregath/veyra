#!/usr/bin/env python3
"""Página de modelos en Chrome real: indicadores del modelo, correrlo sobre un video grabado o una cámara en vivo, controles.

El detector es el simulado del propio proceso (LABELHUB_LIVE_DETECTOR=fake): se prueba el flujo completo sin GPU.
Uso: python tests/ui/test_live_ui.py     (requiere `npm run build` en frontend/ y la base en marcha)
"""
import os
import shutil
import subprocess
import sys
import time

from harness import ROOT, Env, set_input, wait_for

env = Env("liveui")
os.environ.update(LABELHUB_LIVE_DETECTOR="fake", LABELHUB_LIVE_PYTHON=sys.executable, LABELHUB_RUNS_ROOT=str(env.data / "runs"))
res = {}


def check(name, ok, extra=""):
    print(("OK    " if ok else "FALLA ") + name + "  " + str(extra))
    res[name] = bool(ok)


try:
    from app.db import SessionLocal
    from app.legacy_import import DEFAULT_CONFIG, ensure_member, ensure_project, ensure_user
    from app.models import Frame, Model, Video
    from app.security import hash_password

    vid = env.data / "cam.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x192:rate=25:duration=4", "-pix_fmt", "yuv420p", str(vid)], check=True)
    img = next((ROOT.parents[0] / "cv-residue-diff" / "labeling").glob("VID_*/*.jpg"))
    (env.data / "t").mkdir()
    shutil.copy(img, env.data / "t" / "f1.jpg")
    with SessionLocal() as db:
        proj = ensure_project(db, "lv", "En vivo", DEFAULT_CONFIG)
        for name, role, su in (("tester", "admin", True), ("lector", "viewer", False)):
            ensure_member(db, ensure_user(db, name, hash_password("pw-" + name), is_superuser=su), proj, role)
        v = Video(project_id=proj.id, name="camara-patio", path=str(vid), meta={})
        db.add(v)
        db.flush()
        db.add(Frame(project_id=proj.id, video_id=v.id, frame_index=1, t=0.0, image_path="t/f1.jpg", width=1920, height=1080, position=0, meta={}))
        mdl = Model(project_id=proj.id, name="demo", path="/no/importa.pt", arch="yolov8n", classes=["louse", "fly"], params={}, created_by=1,
                    metrics={"map50": 0.5, "map50_95": 0.3, "precision": 0.6, "recall": 0.7})
        db.add(mdl)
        db.flush()
        from app.models import Run

        db.add(Run(project_id=proj.id, kind="evaluation", name="eval demo", params={"detector": f"model:{mdl.id}"}, status="done", created_by=1,
                   metrics={"map": 0.42, "split": "test", "dataset": "v1", "score_threshold": 0.31, "count_mae": 1.5,
                            "micro": {"precision": 0.55, "recall": 0.66, "f1": 0.6, "tp": 5, "fp": 4, "fn": 3},
                            "per_class": {"louse": {"gt": 8, "ap": 0.42, "precision": 0.55, "recall": 0.66}, "fly": {"gt": 0, "ap": None}}}))
        MID = mdl.id
        db.commit()
        PID = proj.id
    env.start_server()
    root = env.api("tester")
    n_frames = lambda: len(root.get(f"/api/projects/{PID}/frames?limit=500"))

    c = env.browser("tester")
    c.eval(f"location.hash='p={PID}'")
    wait_for(c, "!!document.getElementById('modelsbtn')")
    check("el editor tiene el botón «Modelos» (ya no «En vivo»)", c.eval("!document.getElementById('livebtn')"))
    c.eval("document.getElementById('modelsbtn').click()")
    check("«Modelos» abre la lista de modelos entrenados con el primero ya elegido", wait_for(c, "document.querySelectorAll('#model-list [data-model]').length===1&&!!document.getElementById('model-stats')"))
    check("se ven los indicadores del entrenamiento del modelo (mAP50, mAP50-95, precisión, recall)", wait_for(c, "document.getElementById('model-train-stats').textContent.includes('50.0 %')&&document.getElementById('model-train-stats').textContent.includes('70.0 %')"), c.eval("document.getElementById('model-train-stats')?.textContent"))
    check("se ven sus evaluaciones (mAP, precisión, recall, F1, umbral, error de conteo) y el detalle por clase", wait_for(c, "!!document.getElementById('model-evals')&&document.querySelectorAll('#model-evals tbody tr').length===1&&document.getElementById('model-evals').textContent.includes('42.0 %')&&document.getElementById('model-perclass').textContent.includes('louse')"), c.eval("document.getElementById('model-evals')?.textContent"))
    check("el formulario permite correr el modelo sobre un video grabado o una cámara en vivo", wait_for(c, "!!document.getElementById('lv-start')&&document.getElementById('lv-video').options.length===1&&[...document.getElementById('lv-kind').options].map(o=>o.value).join()==='video,url'"))
    check("la página no pide elegir modelo en el formulario (ya está elegido en la lista)", c.eval("!document.getElementById('lv-model')"))
    c.eval("window.__errs=[];window.addEventListener('error',e=>__errs.push(String(e.message)+' '+((e.error&&e.error.stack)||'').slice(0,400)));const ce=console.error;console.error=(...a)=>{__errs.push(a.map(x=>(x&&x.stack)||String(x)).join(' ').slice(0,600));ce(...a)}")
    c.eval("document.getElementById('lv-start').click()")
    ok_start = wait_for(c, "!!document.querySelector('#live-list .lvitem .lv-running')", 30)
    check("iniciar crea la sesión y aparece «en vivo» en la lista", ok_start, "" if ok_start else (c.eval("document.getElementById('live-msg')?.textContent") or "") + " | " + c.eval("JSON.stringify(window.__errs).slice(0,1200)"))
    check("el navegador recibe el flujo MJPEG (la imagen se carga)", wait_for(c, "!!document.getElementById('live-img')&&document.getElementById('live-img').naturalWidth===320", 20), c.eval("document.getElementById('live-img')?.naturalWidth"))
    check("las estadísticas se actualizan: velocidad, latencia y reconexiones", wait_for(c, "parseFloat(document.getElementById('ls-fps').textContent)>0&&document.getElementById('ls-lat').textContent.endsWith('ms')&&document.getElementById('ls-rec').textContent==='0'"), c.eval("document.getElementById('live-stats')?.textContent"))
    sid = root.get(f"/api/projects/{PID}/live")[0]["id"]
    # umbral
    set_input(c, "lc-thr", "0.95", "input"); time.sleep(0.9)
    check("mover el umbral lo aplica en el proceso (no queda solo en pantalla)", root.get(f"/api/live/{sid}")["params"]["threshold"] == 0.95 and root.get(f"/api/live/{sid}")["stats"]["threshold"] == 0.95)
    # pausa
    c.eval("document.getElementById('lc-pause').click()")
    check("Pausar detiene el procesamiento y el botón pasa a «Reanudar»", wait_for(c, "document.getElementById('lc-pause').textContent.includes('Reanudar')") and root.get(f"/api/live/{sid}")["stats"]["paused"])
    c.eval("document.getElementById('lc-pause').click()")
    check("Reanudar lo vuelve a activar", wait_for(c, "document.getElementById('lc-pause').textContent.includes('Pausar')"))
    # grabar un clip
    c.eval("document.getElementById('lc-rec').click()")
    check("Grabar clip: el botón cambia a «Detener grabación»", wait_for(c, "document.getElementById('lc-rec').textContent.includes('Detener')"))
    time.sleep(1.5)
    c.eval("document.getElementById('lc-rec').click()")
    check("el clip grabado aparece en la lista para descargar", wait_for(c, "document.querySelectorAll('#live-clips a').length===1", 15), c.eval("document.getElementById('live-clips')?.textContent"))
    # guardar fotograma para etiquetar
    n0 = n_frames()
    set_input(c, "lc-thr", "0.05", "input"); time.sleep(0.9)
    c.eval("document.getElementById('lc-save').click()")
    check("«Guardar fotograma» avisa y la imagen queda en la cola de etiquetado", wait_for(c, "!!document.getElementById('live-msg')&&document.getElementById('live-msg').textContent.includes('cola de etiquetado')") and n_frames() == n0 + 1)
    last = root.get(f"/api/projects/{PID}/frames?limit=500")[-1]
    check("la imagen guardada trae las cajas del modelo como propuestas", len(root.get(f"/api/frames/{last['id']}")["proposals"]) == 2 and last["video"] == f"live-{sid}")
    # detener
    c.eval("document.getElementById('lc-stop').click()")
    check("Detener sesión: queda «detenida» y se corta el flujo", wait_for(c, "!!document.getElementById('live-ended')&&!document.getElementById('live-img')"))
    check("al detener quedan las estadísticas finales guardadas", (root.get(f"/api/live/{sid}")["summary"] or {}).get("frames", 0) > 0)
    # URL peligrosa
    set_input(c, "lv-kind", "url", "change", "HTMLSelectElement"); time.sleep(0.3)
    set_input(c, "lv-url", "file:///etc/passwd", "input"); time.sleep(0.2)
    c.eval("document.getElementById('lv-start').click()")
    check("una URL peligrosa (file://) se rechaza con un aviso claro", wait_for(c, "!!document.getElementById('live-msg')&&document.getElementById('live-msg').textContent.includes('rtsp://')"), c.eval("document.getElementById('live-msg')?.textContent"))
    # cámara que no responde
    set_input(c, "lv-url", "rtsp://usuario:secreto@127.0.0.1:1/camara", "input"); time.sleep(0.2)
    c.eval("document.getElementById('lv-start').click()")
    check("una cámara que no responde queda «en vivo» y avisa que no puede abrir la fuente", wait_for(c, "!!document.getElementById('live-warn')&&document.getElementById('live-warn').textContent.includes('no se pudo abrir')", 30), c.eval("document.getElementById('live-warn')?.textContent"))
    shown = c.eval("document.getElementById('live-list').textContent")
    check("la contraseña de la cámara NO se muestra en pantalla", "secreto" not in shown and "usuario:****@" in shown, shown[:200])
    cam = [s for s in root.get(f"/api/projects/{PID}/live") if s["source_kind"] == "url"][0]
    root.call("DELETE", f"/api/live/{cam['id']}")
    # sesión viva para el lector
    run = root.call("POST", f"/api/projects/{PID}/live", {"source_kind": "video", "video_id": root.get(f"/api/projects/{PID}/videos")[0]["id"], "model_id": MID, "threshold": 0.25, "imgsz": 640, "no_realtime": True})
    wait_for(c, "true")
    c.close()
    shutil.rmtree(c.prof, ignore_errors=True)
    c2 = env.browser("lector")
    c2.eval(f"location.hash='p={PID}&v=models'")
    check("un lector ve la sesión y el video, pero no el formulario ni los controles", wait_for(c2, "!!document.getElementById('live-img')&&document.getElementById('live-img').naturalWidth>0", 25) and c2.eval("!document.getElementById('live-form')") and c2.eval("document.getElementById('lc-save').disabled&&document.getElementById('lc-pause').disabled&&document.getElementById('lc-stop').disabled&&document.getElementById('lc-thr').disabled"))
    root.call("DELETE", f"/api/live/{run['id']}")
    print("\nRESUMEN: %d/%d OK" % (sum(res.values()), len(res)))
finally:
    from app import live

    live.stop_all()
    env.close()
sys.exit(0 if res and all(res.values()) else 1)
