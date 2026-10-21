#!/usr/bin/env python3
"""Entrenamiento y evaluación de modelos en Chrome real (F3), con procesos de entrenamiento simulados (sin GPU).

Uso: python tests/ui/test_training_ui.py     (requiere `npm run build` en frontend/ y la base en marcha)
"""
import os
import shutil
import sys
import time

from harness import ROOT, Env, set_input, wait_for

HERE = ROOT / "backend" / "tests"
env = Env("trui")
os.environ.update(LABELHUB_TRAIN_PYTHON=sys.executable, LABELHUB_TRAIN_SCRIPT=str(HERE / "fake_train.py"),
                  LABELHUB_PREDICT_SCRIPT=str(HERE / "fake_predict.py"), LABELHUB_MODELS_ROOT=str(env.data / "models"),
                  LABELHUB_RUNS_ROOT=str(env.data / "runs"))
res = {}


def check(name, ok, extra=""):
    print(("OK    " if ok else "FALLA ") + name + "  " + str(extra))
    res[name] = bool(ok)


try:
    from app.db import SessionLocal
    from app.legacy_import import DEFAULT_CONFIG, ensure_member, ensure_project, ensure_user
    from app.models import Frame, Video
    from app.security import hash_password

    img = next((ROOT.parents[0] / "cv-residue-diff" / "labeling").glob("VID_*/*.jpg"))
    (env.data / "t").mkdir()
    shutil.copy(img, env.data / "t" / "f1.jpg")
    with SessionLocal() as db:
        proj = ensure_project(db, "tr", "Entrenar", DEFAULT_CONFIG)
        for name, role, su in (("tester", "admin", True), ("ana", "labeler", False)):
            ensure_member(db, ensure_user(db, name, hash_password("pw-" + name), is_superuser=su), proj, role)
        ids = []
        for k in range(30):
            v = Video(project_id=proj.id, name=f"VID_{k}", path=None, meta={})
            db.add(v)
            db.flush()
            f = Frame(project_id=proj.id, video_id=v.id, frame_index=1, t=0.0, image_path="t/f1.jpg", width=1920, height=1080, position=k, meta={})
            db.add(f)
            db.flush()
            ids.append(f.id)
        vx = Video(project_id=proj.id, name="VID_sin_revisar", path=None, meta={})
        db.add(vx)
        db.flush()
        UNREV = []
        for k in range(5):  # imágenes que nadie revisó: es donde ayuda la preanotación
            fx = Frame(project_id=proj.id, video_id=vx.id, frame_index=k + 1, t=float(k), image_path="t/f1.jpg", width=1920, height=1080, position=100 + k, meta={})
            db.add(fx)
            db.flush()
            UNREV.append(fx.id)
        db.commit()
        PID = proj.id
    env.start_server()
    ana, root = env.api("ana"), env.api("tester")
    for fid in ids:
        ana.call("PUT", f"/api/frames/{fid}/annotation", {"reviewed": True, "boxes": [{"bbox": [10, 10, 20, 20], "label": "louse", "manual": False, "sources": [], "score": None, "deleted": False}]})
    root.call("POST", f"/api/projects/{PID}/datasets", {"name": "d1", "split": [0.6, 0.4, 0], "seed": "t"})

    c = env.browser("tester")
    c.eval(f"location.hash='p={PID}&v=admin'")
    check("abre la gestión", wait_for(c, "!!document.getElementById('tab-train')"))
    c.eval("document.getElementById('tab-train').click()")
    check("entrenamiento: aparece el formulario con la versión", wait_for(c, "!!document.getElementById('tr-go')&&document.getElementById('tr-version').options.length>=1"))
    set_input(c, "tr-epochs", "3", "input"); set_input(c, "tr-imgsz", "320", "input"); set_input(c, "tr-batch", "2", "input"); set_input(c, "tr-name", "modelo-a", "input")
    time.sleep(0.3)
    c.eval("document.getElementById('tr-go').click()")
    check("entrenar crea la ejecución y la muestra en la lista", wait_for(c, "document.querySelectorAll('#tr-list tbody tr').length===1"), c.eval("document.getElementById('tr-err')?.textContent"))
    check("la ejecución termina y se marca «terminado»", wait_for(c, "!!document.querySelector('#tr-list tbody tr td.tr-done')", 30))
    check("muestra el avance de épocas y el mAP50 de validación", "3 / 3" in c.eval("document.querySelector('#tr-list tbody tr').textContent") and "30.0 %" in c.eval("document.querySelector('#tr-list tbody tr').textContent"), c.eval("document.querySelector('#tr-list tbody tr').textContent"))
    check("el registro muestra la salida del proceso", wait_for(c, "!!document.getElementById('tr-log')&&document.getElementById('tr-log').textContent.includes('cargando datos')"))
    check("el modelo queda registrado con sus métricas", wait_for(c, "document.querySelectorAll('#tr-models tbody tr').length===1") and "50.0 %" in c.eval("document.querySelector('#tr-models tbody tr').textContent"), c.eval("document.querySelector('#tr-models tbody tr')?.textContent"))
    check("los pesos quedaron en disco", (env.data / "models" / "tr" / "modelo-a" / "best.pt").is_file())
    # evaluar el modelo desde su botón
    c.eval("document.querySelector('#tr-models tbody tr button').click()")
    check("«Evaluar» lleva a la evaluación con el modelo ya elegido", wait_for(c, "!!document.getElementById('ev-detector')") and "model:" in c.eval("document.getElementById('ev-detector').value"), c.eval("document.getElementById('ev-detector')?.value"))
    set_input(c, "ev-split", "all", "change", "HTMLSelectElement"); set_input(c, "ev-thr", "0.5", "input"); time.sleep(0.2)
    c.eval("document.getElementById('ev-go').click()")
    check("la evaluación del modelo muestra su detalle", wait_for(c, "!!document.getElementById('ev-detail')"))
    check("el modelo simulado acierta todo lo etiquetado (30 TP, 0 FP, 0 FN)", wait_for(c, "!!document.getElementById('ev-detail')&&document.getElementById('ev-detail').textContent.includes('TP 30 · FP 0 · FN 0')"), c.eval("document.getElementById('ev-detail')?.textContent.slice(0,200)"))
    # un entrenamiento que falla y otro que se cancela
    c.eval("document.getElementById('tab-train').click()"); wait_for(c, "!!document.getElementById('tr-go')")
    set_input(c, "tr-epochs", "7", "input"); set_input(c, "tr-name", "falla", "input"); time.sleep(0.2)
    c.eval("document.getElementById('tr-go').click()")
    check("un entrenamiento que falla queda «falló» con su registro", wait_for(c, "!!document.querySelector('#tr-list tbody tr td.tr-failed')", 20) and "código 3" in c.eval("document.getElementById('tr-log').textContent"))
    check("el que falló no registra ningún modelo", c.eval("document.querySelectorAll('#tr-models tbody tr').length") == 1)
    set_input(c, "tr-epochs", "9", "input"); set_input(c, "tr-name", "lento", "input"); time.sleep(0.2)
    c.eval("document.getElementById('tr-go').click()")
    check("un entrenamiento largo aparece «entrenando» con botón Cancelar", wait_for(c, "!!document.querySelector('#tr-list tbody tr td.tr-running')&&!!document.querySelector('#tr-list tbody tr button.cancel')", 20))
    c.eval("document.querySelector('#tr-list tbody tr button.cancel').click()")
    check("Cancelar lo detiene y queda «cancelado»", wait_for(c, "!![...document.querySelectorAll('#tr-list tbody tr td')].some(t=>t.textContent==='cancelado')", 20))
    set_input(c, "tr-name", "modelo-a", "input"); set_input(c, "tr-epochs", "2", "input"); time.sleep(0.2)
    c.eval("document.getElementById('tr-go').click()")
    check("un nombre de modelo repetido se rechaza con un aviso claro", wait_for(c, "!!document.getElementById('tr-err')") and "Ya existe" in c.eval("document.getElementById('tr-err').textContent"))
    # ---- etiquetado asistido: preanotar, buscar inciertas, asignar
    c.eval("document.getElementById('tab-assist').click()")
    check("asistido: lista los modelos y ofrece preanotar", wait_for(c, "!!document.getElementById('as-pre')&&document.getElementById('as-model').options.length>=1"))
    c.eval("document.getElementById('as-pre').click()")
    check("preanotar termina y muestra cuántas cajas propuso", wait_for(c, "!![...document.querySelectorAll('#as-runs tbody tr td')].some(t=>t.textContent==='terminado')", 30) and "10" in c.eval("document.querySelector('#as-runs tbody tr').textContent"), c.eval("document.querySelector('#as-runs tbody tr')?.textContent"))
    boxes = root.get(f"/api/frames/{UNREV[0]}")["proposals"]
    check("las cajas del modelo quedaron como propuestas con su fuente", any("model" in p["sources"] for p in boxes), boxes)
    c.eval("document.getElementById('as-find').click()")
    check("buscar inciertas muestra las imágenes sin revisar con su motivo", wait_for(c, "document.querySelectorAll('#as-found .asitem').length===5"), c.eval("document.getElementById('as-found')?.textContent"))
    c.eval("document.getElementById('as-assign').click()")
    check("asignar deja las imágenes en la cola de la persona elegida", wait_for(c, "!!document.getElementById('as-assigned')") and len(ana.get(f"/api/projects/{PID}/frames?filter=assigned_pending")) == 5)
    # ---- flujos de detección: combinar modelos y detectores con O / Y y usarlos en el asistido
    JS_SEL = "(sel,val)=>{const e=document.querySelector(sel);Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(e,val);e.dispatchEvent(new Event('change',{bubbles:true}))}"
    c.eval("document.getElementById('as-model-flows').click()")
    check("«Flujos…» abre el administrador de flujos", wait_for(c, "!!document.getElementById('flows-modal')&&!!document.getElementById('fl-new')"))
    c.eval("document.getElementById('fl-new').click()"); wait_for(c, "!!document.getElementById('fl-name')")
    set_input(c, "fl-name", "modelo y manchas", "input")
    c.eval("document.querySelector('.f-addsrc').click()"); time.sleep(0.3)
    c.eval(f"({JS_SEL})('#fl-tree .fleaf:nth-of-type(3) .f-source','proposals:blob')"); time.sleep(0.3)
    check("el constructor combina las fuentes y muestra el resultado en palabras", c.eval("document.getElementById('fl-text').textContent").count(" O ") == 1 and "manchas guardadas" in c.eval("document.getElementById('fl-text').textContent").lower(), c.eval("document.getElementById('fl-text')?.textContent"))
    c.eval("document.querySelector('.f-and').click()"); time.sleep(0.3)
    check("cambiar a «Todas (Y)» actualiza la descripción", " Y " in c.eval("document.getElementById('fl-text').textContent"))
    c.eval("document.querySelector('.f-or').click()"); time.sleep(0.2)
    c.eval("document.getElementById('fl-save').click()")
    check("guardar el flujo lo deja elegido como detector del asistido", wait_for(c, "!document.getElementById('flows-modal')&&document.getElementById('as-model').value.startsWith('flow:')"), c.eval("document.getElementById('as-model')?.value"))
    fl = root.get(f"/api/projects/{PID}/flows")
    check("el servidor guardó el flujo con su especificación", len(fl) == 1 and fl[0]["spec"]["op"] == "or" and len(fl[0]["spec"]["inputs"]) == 2 and fl[0]["spec"]["inputs"][1]["source"] == "proposals:blob", fl)
    check("con un flujo, el puntaje mínimo y la búsqueda de inciertas se desactivan", c.eval("document.getElementById('as-min').disabled&&document.getElementById('as-find').disabled"))
    n_runs = c.eval("document.querySelectorAll('#as-runs tbody tr').length")
    c.eval("document.getElementById('as-pre').click()")
    check("preanotar con el flujo termina y las cajas quedan marcadas con el flujo", wait_for(c, f"document.querySelectorAll('#as-runs tbody tr').length>{n_runs}&&[...document.querySelectorAll('#as-runs tbody tr')].some(r=>r.textContent.includes('flujo modelo y manchas')&&r.textContent.includes('terminado'))", 40) and any(f"flow:{fl[0]['id']}" in p["sources"] for p in root.get(f"/api/frames/{UNREV[0]}")["proposals"]))
    # el etiquetador ve esas cajas del modelo en el editor
    c.eval(f"location.hash='p={PID}'"); time.sleep(0.5); c.eval("location.reload()"); time.sleep(2.5)
    wait_for(c, "!!window.__lh&&!!window.__lh.editor&&!!window.__lh.editor.doc")
    c.eval(f"(()=>{{const i=window.__lh.frames().findIndex(f=>f.id=={UNREV[0]});const g=document.getElementById('goto');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(g,String(i+1));g.dispatchEvent(new Event('input',{{bubbles:true}}));g.focus();g.dispatchEvent(new KeyboardEvent('keydown',{{key:'Enter',bubbles:true}}))}})()")
    check("en el editor, las cajas del modelo aparecen sin etiquetar y marcadas «modelo»", wait_for(c, "window.__lh.editor.doc&&window.__lh.editor.doc.boxes.some(b=>b.sources.includes('model'))&&window.__lh.editor.doc.boxes.every(b=>b.label===null)", 10))
    c.eval("document.getElementById('refresh-assisted').click()")
    check("«Refrescar asistido» sin detector elegido avisa dónde definirlo", wait_for(c, "document.body.textContent.includes('Gestión → Asistido')", 10))
    c.eval("fetch('/api/projects/%d/assisted',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({flow_id:%d})})" % (PID, fl[0]["id"])); time.sleep(1)
    c.eval("(()=>{const d=window.__lh.editor.doc;d.boxes=d.boxes.filter(b=>!b.sources.some(s=>s.startsWith('flow:')));})()")  # simula que no hay cajas del flujo
    n0 = c.eval("window.__lh.editor.doc.boxes.length")
    c.eval("document.getElementById('refresh-assisted').click()")
    check("«Refrescar asistido» vuelve a proponer las cajas del detector elegido sobre la imagen actual", wait_for(c, f"window.__lh.editor.doc.boxes.length>{n0}&&window.__lh.editor.doc.boxes.some(b=>b.sources.some(s=>s.startsWith('flow:')))&&!document.getElementById('refresh-assisted').disabled", 40), c.eval("window.__lh.editor.doc.boxes.length"))
    # cargar imágenes con etiquetas desde Gestión → Cargar datos
    import base64

    b64 = base64.b64encode(img.read_bytes()).decode()
    c.eval(f"location.hash='p={PID}&v=admin'"); time.sleep(0.5); c.eval("location.reload()"); time.sleep(2.5)
    wait_for(c, "!!document.getElementById('tab-import')")
    c.eval("document.getElementById('tab-import').click()")
    check("la gestión ofrece cargar imágenes y videos", wait_for(c, "!!document.getElementById('imp-files')&&!!document.getElementById('imp-vfiles')"))
    n_before = len(root.get(f"/api/projects/{PID}/frames"))
    c.eval("""(()=>{const b=atob('%s'),u=new Uint8Array(b.length);for(let i=0;i<b.length;i++)u[i]=b.charCodeAt(i);
      const dt=new DataTransfer();dt.items.add(new File([u],'nueva.jpg',{type:'image/jpeg'}));dt.items.add(new File(['0 0.5 0.5 0.1 0.1\\n'],'nueva.txt',{type:'text/plain'}));dt.items.add(new File(['Piojo\\nMosca\\n'],'classes.txt',{type:'text/plain'}));
      const i=document.getElementById('imp-files');i.files=dt.files;i.dispatchEvent(new Event('change',{bubbles:true}))})()""" % b64)
    check("elegir archivos resume cuántas imágenes y archivos de etiquetas hay", wait_for(c, "document.getElementById('imp-summary')?.textContent.includes('1 imagen ')&&document.getElementById('imp-summary').textContent.includes('2 de etiquetas')"), c.eval("document.getElementById('imp-summary')?.textContent"))
    c.eval("document.getElementById('imp-send').click()")
    check("cargar al proyecto informa imágenes y cajas", wait_for(c, "document.getElementById('imp-result')?.textContent.includes('1 imagen cargada')&&document.getElementById('imp-result').textContent.includes('1 caja')", 20), c.eval("document.getElementById('imp-result')?.textContent||document.getElementById('imp-err')?.textContent"))
    check("la imagen cargada aparece en el proyecto con su etiqueta", len(root.get(f"/api/projects/{PID}/frames")) == n_before + 1)
    print("\nRESUMEN: %d/%d OK" % (sum(res.values()), len(res)))
finally:
    from app import training

    for rid in list(training._procs):
        training.cancel(rid)
    env.close()
sys.exit(0 if res and all(res.values()) else 1)
