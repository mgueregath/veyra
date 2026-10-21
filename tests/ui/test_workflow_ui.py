#!/usr/bin/env python3
"""Flujo de trabajo en Chrome real: cola, bloqueo, estados, revisión, calidad, asignaciones y datasets (F1).

Un administrador usa el navegador; otras personas (ana, beto, rev) trabajan por API a la vez, como en un equipo real.
Uso: python tests/ui/test_workflow_ui.py     (requiere `npm run build` en frontend/ y la base en marcha)
"""
import json
import shutil
import sys
import time

from harness import ROOT, Env, set_input, wait_for

env = Env("wfui")
res = {}


def check(name, ok, extra=""):
    print(("OK    " if ok else "FALLA ") + name + "  " + str(extra))
    res[name] = bool(ok)


try:
    from app.db import SessionLocal
    from app.legacy_import import DEFAULT_CONFIG, ensure_member, ensure_project, ensure_user
    from app.models import Frame, Proposal, Video
    from app.security import hash_password

    img = next((ROOT.parents[0] / "cv-residue-diff" / "labeling").glob("VID_*/*.jpg"))
    (env.data / "t").mkdir()
    shutil.copy(img, env.data / "t" / "f1.jpg")
    with SessionLocal() as db:
        proj = ensure_project(db, "wf", "Flujo", DEFAULT_CONFIG)
        for name, role, su in (("tester", "admin", True), ("ana", "labeler", False), ("beto", "labeler", False), ("rev", "reviewer", False)):
            u = ensure_user(db, name, hash_password("pw-" + name), is_superuser=su)
            ensure_member(db, u, proj, role)
        v = Video(project_id=proj.id, name="VID_a", path=None, meta={})
        db.add(v)
        db.flush()
        ids = []
        for k in range(8):
            f = Frame(project_id=proj.id, video_id=v.id, frame_index=k + 1, t=float(k), image_path="t/f1.jpg", width=1920, height=1080, position=k, meta={})
            f.proposals = [Proposal(x=300, y=200, w=400, h=300, sources=["blob"], score=7.0), Proposal(x=1000, y=500, w=60, h=60, sources=["motion"], score=4.0)]
            db.add(f)
            db.flush()
            ids.append(f.id)
        db.commit()
        PID = proj.id
    env.start_server()
    ana, beto, rev, root = env.api("ana"), env.api("beto"), env.api("rev"), env.api("tester")

    def save(api, fid, boxes, reviewed=True):
        api.call("PUT", f"/api/frames/{fid}/annotation", {"reviewed": reviewed, "boxes": boxes})

    B = lambda bb, lab="louse": {"bbox": bb, "label": lab, "manual": False, "sources": ["blob"], "score": 7, "deleted": False}
    # imagen 0: acuerdo · imagen 1: desacuerdo · imagen 2: solo ana
    save(ana, ids[0], [B([300, 200, 400, 300])]); save(beto, ids[0], [B([305, 205, 400, 300])])
    save(ana, ids[1], [B([300, 200, 400, 300])]); save(beto, ids[1], [B([300, 200, 400, 300], "fly")])
    save(ana, ids[2], [B([10, 10, 50, 50])])

    c = env.browser("tester")
    c.eval(f"location.hash='p={PID}'")
    check("la página de etiquetado carga", wait_for(c, "!!document.getElementById('cv')&&!!window.__lh&&!!window.__lh.editor&&!!window.__lh.editor.doc"))
    time.sleep(1.0)
    go = lambda i: (set_input(c, "goto", str(i + 1), "input"), time.sleep(0.15), c.eval("(()=>{const e=document.getElementById('goto');e.focus();e.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))})()"), time.sleep(0.8))
    chip = lambda: c.eval("document.getElementById('statuschip').textContent")

    # ---- estado de cada imagen y filtros
    go(0)
    check("imagen con 2 personas de acuerdo: «De acuerdo»", wait_for(c, "document.getElementById('statuschip').textContent.includes('acuerdo')"), chip())
    go(1)
    check("imagen en desacuerdo: lo dice el estado", wait_for(c, "document.getElementById('statuschip').textContent.includes('Desacuerdo')"), chip())
    go(3)
    check("imagen que nadie revisó: «Sin revisar»", wait_for(c, "document.getElementById('statuschip').textContent==='Sin revisar'"), chip())
    set_input(c, "flt", "disagreement", "change", "HTMLSelectElement"); time.sleep(1.0)
    check("el filtro «Con desacuerdo» deja solo esa imagen", c.eval("window.__lh.frames().length") == 1 and c.eval("window.__lh.frames()[0].status") == "disagreement")
    set_input(c, "flt", "needs_review", "change", "HTMLSelectElement"); time.sleep(1.0)
    check("el filtro «Para revisar» muestra las 3 con trabajo", c.eval("window.__lh.frames().length") == 3)
    check("la tira marca con una insignia naranja las imágenes en desacuerdo", c.eval("!!document.querySelector('#strip .ck-dis')"))
    set_input(c, "flt", "all", "change", "HTMLSelectElement"); time.sleep(1.0)

    # ---- panel de revisión: ver el trabajo de cada persona y fijar la final
    go(1)
    c.eval("document.getElementById('tab-review').click()"); time.sleep(0.5)
    check("el revisor (administrador) ve la pestaña Revisión con las dos personas", wait_for(c, "document.querySelectorAll('#s-revision .rvrow').length===2"))
    c.eval("document.querySelector('#s-revision .rvrow[data-user=beto] button').click()"); time.sleep(0.6)
    check("«Ver» superpone en el lienzo las cajas de esa persona", c.eval("!!window.__lh.editor.overlay&&window.__lh.editor.overlay.title==='beto'&&window.__lh.editor.overlay.boxes.length===1"))
    c.eval("document.querySelector('#s-revision .rvrow[data-user=beto] button').click()"); time.sleep(0.4)
    check("un segundo clic en «Ver» quita la superposición", c.eval("window.__lh.editor.overlay===null"))
    c.eval("document.querySelectorAll('#s-revision .rvrow[data-user=beto] button')[1].click()")
    check("«Final» fija la versión: estado Final y aviso con quién decidió", wait_for(c, "document.getElementById('statuschip').textContent==='Final'&&!!document.getElementById('rv-final')") and "beto" in c.eval("document.getElementById('rv-final').textContent"))
    check("el servidor guardó la decisión", root.get(f"/api/frames/{ids[1]}")["review"]["final_user"] == "beto")
    c.eval("document.getElementById('rv-undo').click()")
    check("«Quitar decisión» devuelve el desacuerdo", wait_for(c, "document.getElementById('statuschip').textContent.includes('Desacuerdo')"))

    # ---- cola y bloqueo
    root.call("POST", f"/api/projects/{PID}/assignments", {"users": ["tester", "ana"], "frame_ids": [ids[3], ids[4], ids[5]], "seed": 1})
    mine = [a for a in root.get(f"/api/projects/{PID}/frames?filter=assigned_pending")]
    check("el administrador tiene imágenes en su cola tras el reparto", len(mine) >= 1, f"{len(mine)}")
    time.sleep(0.5)
    c.eval("location.reload()"); time.sleep(2.5)
    wait_for(c, "!!document.getElementById('flt')")
    set_input(c, "flt", "assigned_pending", "change", "HTMLSelectElement"); time.sleep(1.0)
    check("el filtro «Mi cola» del carrusel muestra solo las imágenes asignadas a mí", c.eval("window.__lh.frames().length") == len(mine) and all(f["assigned"] for f in json.loads(c.eval("JSON.stringify(window.__lh.frames())"))))
    go(0)
    cur_id = c.eval("window.__lh.curId()")
    check("la imagen de mi cola pertenece a mi lista", cur_id in [m["id"] for m in mine], f"id={cur_id}")
    time.sleep(1.0)
    lk = beto.call("POST", f"/api/frames/{cur_id}/lock")
    check("al abrirla quedó reservada para mí: otra persona ve que está ocupada y quién la tiene", lk == {"ok": False, "holder": "tester"}, lk)
    check("mientras la tengo abierta, otra persona NO puede tomarla de la cola", beto.call("POST", f"/api/projects/{PID}/queue/next", {"scope": "any"})["frame_id"] != cur_id)
    set_input(c, "flt", "all", "change", "HTMLSelectElement"); time.sleep(1.0)
    # otra persona abre una imagen: yo veo el aviso al abrir la misma
    other = ids[6]
    beto.call("POST", f"/api/frames/{other}/lock")
    go(6)
    check("si otra persona tiene la imagen abierta, se avisa (sin impedir trabajar)", wait_for(c, "!!document.getElementById('lockwarn')") and "beto" in c.eval("document.getElementById('lockwarn').textContent"))

    # ---- marcar revisado cambia el estado a «1 persona»
    go(7)
    wait_for(c, "document.getElementById('statuschip').textContent==='Sin revisar'")
    c.eval("document.activeElement&&document.activeElement.blur()")   # el campo «Ir a» no debe quedarse con el teclado
    c.key(" "); time.sleep(1.8)
    go(7)
    check("tras «Revisado» la imagen pasa a «Revisada por 1 persona»", wait_for(c, "document.getElementById('statuschip').textContent.includes('1 persona')"), chip())

    # ---- gestión: calidad
    c.eval("window.__errs=[];window.addEventListener('error',e=>__errs.push(String(e.message)+' '+((e.error&&e.error.stack)||'').slice(0,300)));const ce=console.error;console.error=(...a)=>{__errs.push(a.map(x=>(x&&x.stack)||String(x)).join(' ').slice(0,500));ce(...a)}")
    c.eval("document.getElementById('adminbtn').click()"); time.sleep(1.2)
    ok_admin = wait_for(c, "!!document.getElementById('tab-quality')")
    check("«Gestión» abre la página de gestión", ok_admin, "" if ok_admin else c.eval("location.hash+' | '+JSON.stringify(window.__errs).slice(0,900)"))
    check("calidad: cuenta imágenes de acuerdo y en desacuerdo", wait_for(c, "document.getElementById('qs-agreed')&&document.getElementById('qs-agreed').textContent==='1'") and c.eval("document.getElementById('qs-disagreement').textContent") == "1")
    check("calidad: imágenes revisadas por 2 o más personas", c.eval("document.getElementById('q-overlap').textContent") == "2")
    # ---- gestión: asignaciones
    c.eval("document.getElementById('tab-assign').click()"); time.sleep(0.8)
    check("asignaciones: lista a los miembros del proyecto", wait_for(c, "document.querySelectorAll('#assign input[type=checkbox]').length>=3"))
    c.eval("[...document.querySelectorAll('#assign input[type=checkbox]')].filter(i=>i.parentElement.textContent.includes('beto')||i.parentElement.textContent.includes('ana')).forEach(i=>i.click())"); time.sleep(0.3)
    c.eval("document.getElementById('assign-go').click()")
    check("asignaciones: reparte y avisa cuántas", wait_for(c, "!!document.getElementById('assign-res')") and "Repartidas" in c.eval("document.getElementById('assign-res').textContent"), c.eval("document.getElementById('assign-res')?.textContent"))
    # ---- gestión: datasets
    c.eval("document.getElementById('tab-datasets').click()"); time.sleep(0.8)
    set_input(c, "ds-name", "v1", "input"); time.sleep(0.3)
    c.eval("document.getElementById('ds-create').click()")
    check("datasets: crea una versión y muestra su detalle por partición", wait_for(c, "!!document.getElementById('ds-detail')"), c.eval("document.getElementById('ds-err')?.textContent"))
    check("datasets: la versión aparece en la lista", "v1" in c.eval("document.getElementById('ds-list').textContent"))
    c.eval("document.getElementById('ds-exp-both').click()")
    check("datasets: exportar muestra la carpeta de destino", wait_for(c, "!!document.getElementById('ds-path')") and "wf" in c.eval("document.getElementById('ds-path').textContent"))
    out = env.data / "exports" / "wf" / "v1"
    man = json.load(open(out / "manifest.json"))
    check("datasets: el manifiesto de lo exportado cuadra con la versión", sum(man["frames"].values()) == c.eval("(()=>{const t=document.querySelectorAll('#ds-detail tbody tr');return [...t].reduce((a,r)=>a+(+r.children[1].textContent),0)})()"))
    c.eval("document.getElementById('ds-incl-discarded').click()"); time.sleep(0.2)
    c.eval("document.getElementById('ds-exp-yolo').click()")
    check("datasets: con «incluir descartadas» se exporta aparte, con las clases adicionales", wait_for(c, "document.getElementById('ds-path')?.textContent.includes('v1+descartadas')") and "descartada" in json.load(open(env.data / "exports" / "wf" / "v1+descartadas" / "manifest.json"))["classes"])
    # ---- gestión: evaluación de una línea base
    c.eval("document.getElementById('tab-eval').click()"); time.sleep(0.8)
    check("evaluación: aparece el formulario con la versión creada", wait_for(c, "!!document.getElementById('ev-go')&&document.getElementById('ev-version').options.length>=1"))
    set_input(c, "ev-split", "all", "change", "HTMLSelectElement"); set_input(c, "ev-thr", "5", "input"); time.sleep(0.3)
    c.eval("document.getElementById('ev-go').click()")
    check("evaluación: evalúa la línea base y muestra el detalle", wait_for(c, "!!document.getElementById('ev-detail')"), c.eval("document.getElementById('ev-err')?.textContent"))
    check("evaluación: la ejecución queda en la lista con sus métricas", c.eval("document.querySelectorAll('#ev-list tbody tr').length") == 1 and "%" in c.eval("document.querySelector('#ev-list tbody tr').textContent"))
    check("evaluación: se muestran falsos positivos y piojos no detectados con miniatura", c.eval("document.querySelectorAll('#ev-fp .exthumb').length+document.querySelectorAll('#ev-fn .exthumb').length") >= 1)
    set_input(c, "ev-iou", "0.1", "input"); time.sleep(0.2)
    c.eval("document.getElementById('ev-go').click()")
    check("evaluación: repetir con otro IoU agrega otra ejecución para comparar", wait_for(c, "document.querySelectorAll('#ev-list tbody tr').length===2"))
    # ---- gestión: equipo
    c.eval("document.getElementById('tab-team').click()"); time.sleep(0.6)
    check("equipo: lista a las personas con su rol", wait_for(c, "document.querySelectorAll('#team-list tbody tr').length===4"))
    set_input(c, "tm-user", "nuevo", "input"); set_input(c, "tm-pass", "clave-larga-1", "input"); set_input(c, "tm-role", "labeler", "change", "HTMLSelectElement"); time.sleep(0.3)
    c.eval("document.getElementById('tm-add').click()")
    check("añadir crea al usuario nuevo y lo muestra", wait_for(c, "document.querySelectorAll('#team-list tbody tr').length===5&&document.getElementById('team-msg').textContent.includes('ya puede entrar')"))
    nuevo = env.api("nuevo", "clave-larga-1")
    check("la persona nueva puede entrar con la contraseña inicial", nuevo.get("/api/me")["username"] == "nuevo")
    c.eval("(()=>{const s=document.querySelector('#team-list tr[data-user=nuevo] select');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(s,'viewer');s.dispatchEvent(new Event('change',{bubbles:true}))})()")
    check("cambiar el rol rige de inmediato", wait_for(c, "document.getElementById('team-msg').textContent.includes('Rol actualizado')") and [m for m in root.get(f"/api/projects/{PID}/team") if m["username"] == "nuevo"][0]["role"] == "viewer")
    c.eval("(()=>{const i=document.querySelector('#team-list tr[data-user=nuevo] input[type=password]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,'otra-clave-22');i.dispatchEvent(new Event('input',{bubbles:true}))})()"); time.sleep(0.3)
    c.eval("document.querySelector('#team-list tr[data-user=nuevo] .pwbtn').click()")
    check("restablecer la contraseña funciona", wait_for(c, "document.getElementById('team-msg').textContent.includes('Contraseña de')") and env.api("nuevo", "otra-clave-22").get("/api/me")["username"] == "nuevo")
    c.eval("document.querySelector('#team-list tr[data-user=tester] .rmbtn').click()")
    check("no deja quitar al último administrador (avisa con claridad)", wait_for(c, "document.getElementById('team-msg').textContent.includes('último administrador')"))
    c.eval("document.querySelector('#team-list tr[data-user=nuevo] .rmbtn').click()")
    check("quitar acceso saca a la persona de la lista", wait_for(c, "document.querySelectorAll('#team-list tbody tr').length===4"))
    # ---- gestión: sistema
    c.eval("document.getElementById('tab-system').click()"); time.sleep(0.6)
    check("sistema: muestra el uso de disco del proyecto", wait_for(c, "!!document.getElementById('usage-tbl')&&document.getElementById('usage-owned').textContent.includes('sin cuota')"))
    set_input(c, "sys-quota", "5", "input"); time.sleep(0.2)
    c.eval("document.getElementById('sys-quota-save').click()")
    check("guardar la cuota la aplica", wait_for(c, "document.getElementById('usage-owned').textContent.includes('de 5.00 GB')"), c.eval("document.getElementById('usage-owned')?.textContent"))
    check("sistema: muestra trabajos y actividad reciente", wait_for(c, "document.getElementById('sys-jobs').textContent.includes('en cola')"))
    href = c.eval("document.getElementById('sys-backup').getAttribute('href')")
    import gzip

    raw = root.op.open(f"http://127.0.0.1:{env.port}{href}", timeout=30).read()
    dump = json.loads(gzip.decompress(raw))
    check("la copia del proyecto se descarga: gzip con el JSON del proyecto", href.endswith("/backup") and dump["project"]["slug"] == "wf" and len(dump["frames"]) == 8 and len(dump["annotations"]) >= 5, f"{len(raw)} bytes")
    check("sistema: un superusuario ve los workers de GPU y el modo de ejecución", wait_for(c, "!!document.getElementById('workers')&&document.getElementById('gpu-mode').textContent.includes('Local')"))
    set_input(c, "wk-name", "gpu-prueba", "input"); time.sleep(0.2)
    c.eval("document.getElementById('wk-create').click()")
    check("registrar un worker muestra su token UNA vez y lo lista como sin conexión", wait_for(c, "!!document.getElementById('wk-token')&&document.getElementById('wk-token').textContent.includes('vw_')&&!!document.querySelector('#workers-list tr[data-worker=gpu-prueba]')") and "Sin conexión" in c.eval("document.querySelector('#workers-list tr[data-worker=gpu-prueba]').textContent"), c.eval("(document.querySelector('#workers-list tr[data-worker=gpu-prueba]')||{}).textContent"))
    tok = c.eval("document.querySelector('#wk-token pre').textContent.match(/vw_[A-Za-z0-9_-]+/)[0]")
    import urllib.request as _u
    rq = _u.Request(f"http://127.0.0.1:{env.port}/api/worker/register", data=b'{"kinds":["train"],"capabilities":{"gpus":[{"name":"RTX demo","memory_mb":16384}],"torch":"2.4"}}', headers={"Authorization": "Bearer " + tok, "content-type": "application/json"}, method="POST")
    _u.urlopen(rq, timeout=10).read()
    check("cuando el worker se registra aparece «En línea» con su GPU", wait_for(c, "document.querySelector('#workers-list tr[data-worker=gpu-prueba]').textContent.includes('En línea')&&document.querySelector('#workers-list tr[data-worker=gpu-prueba]').textContent.includes('RTX demo')", 15), c.eval("document.querySelector('#workers-list')?.textContent"))
    c.eval("window.confirm=()=>true;document.querySelector('#workers-list tr[data-worker=gpu-prueba] button').click()")
    check("revocar lo marca como revocado", wait_for(c, "document.querySelector('#workers-list tr[data-worker=gpu-prueba]').textContent.includes('Revocado')", 15))
    # ---- espacio de trabajo: llaves SSH del usuario y encendido/apagado del espacio del proyecto
    ED = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAdcTYJBw5A2ja8cNd1BUztHAk5zaMGLUwWZO4IU7CRD"
    c.eval("document.getElementById('usermenu').click()"); wait_for(c, "!!document.getElementById('um-keys')")
    c.eval("document.getElementById('um-keys').click()")
    check("«Mis llaves SSH» abre el administrador de llaves", wait_for(c, "!!document.getElementById('sshkeys-modal')"))
    set_input(c, "key-name", "portátil", "input"); set_input(c, "key-text", "esto no es una llave", "input", "HTMLTextAreaElement"); time.sleep(0.2)
    c.eval("document.getElementById('key-add').click()")
    check("una llave inválida se rechaza con el motivo", wait_for(c, "!!document.getElementById('key-err')&&document.getElementById('key-err').textContent.includes('llave')"), c.eval("document.getElementById('key-err')?.textContent"))
    set_input(c, "key-text", ED + " yo@pc", "input", "HTMLTextAreaElement"); time.sleep(0.2)
    c.eval("document.getElementById('key-add').click()")
    check("una llave pública válida se registra y se lista con su huella", wait_for(c, "!!document.querySelector('#key-list tr[data-key=portátil]')&&document.getElementById('key-list').textContent.includes('SHA256:')"))
    c.eval("document.getElementById('sk-x').click()")
    c.eval("document.getElementById('tab-workspace').click()")
    check("el espacio de trabajo avisa que no hay worker disponible y no deja encender", wait_for(c, "!!document.getElementById('ws-noworker')&&document.getElementById('ws-start').disabled"))
    import json as _j, threading as _t
    wk = root.call("POST", "/api/admin/workers", {"name": "gpu-ws"})["token"]
    stop_sim = _t.Event()
    def sim():
        def call(path, body):
            rq = _u.Request(f"http://127.0.0.1:{env.port}/api/worker{path}", data=_j.dumps(body).encode(), headers={"Authorization": "Bearer " + wk, "content-type": "application/json"}, method="POST")
            raw = _u.urlopen(rq, timeout=40).read()
            return _j.loads(raw) if raw else None
        call("/register", {"kinds": ["workspace"], "max_batch": 1, "max_live": 1, "capabilities": {}})
        running, cancelled = [], False
        def beat():
            while not stop_sim.is_set():
                r = call("/heartbeat", {"running_jobs": running})
                if running and running[0] in r["cancel_jobs"]:
                    nonlocal cancelled
                    cancelled = True
                time.sleep(0.3)
        _t.Thread(target=beat, daemon=True).start()
        while not stop_sim.is_set():
            job = call("/jobs/claim", {"kinds": ["workspace"], "wait": 2})
            if job:
                running.append(job["id"])
                call(f"/jobs/{job['id']}/progress", {"host": "10.0.0.4", "port": job["payload"]["port"], "user": "veyra", "workdir": "/workspace/project"})
                while not cancelled and not stop_sim.is_set():
                    time.sleep(0.2)
                call(f"/jobs/{job['id']}/finish", {"status": "cancelled", "result": {"reason": "stopped"}})
                running.clear()
                cancelled = False
    _t.Thread(target=sim, daemon=True).start()
    check("con un worker en línea se habilita «Encender»", wait_for(c, "!document.getElementById('ws-noworker')&&!document.getElementById('ws-start').disabled", 20))
    c.eval("document.getElementById('ws-start').click()")
    check("encender deja el espacio «Encendido» y muestra cómo conectarse desde VS Code", wait_for(c, "document.getElementById('ws-state').textContent.includes('Encendido')&&!!document.getElementById('ws-sshconfig')", 30) and "Host veyra-wf" in c.eval("document.getElementById('ws-sshconfig').textContent") and "Port 9100" in c.eval("document.getElementById('ws-sshconfig').textContent"), c.eval("document.getElementById('ws-state')?.textContent"))
    check("hay un enlace para abrir en VS Code y un botón para copiar la configuración", c.eval("document.getElementById('ws-vscode').getAttribute('href')") == "vscode://vscode-remote/ssh-remote+veyra-wf/workspace/project" and c.eval("!!document.getElementById('ws-copy-config')"))
    c.eval("document.getElementById('ws-stop').click()")
    check("apagar lo deja «Apagado» y se puede volver a encender", wait_for(c, "document.getElementById('ws-state').textContent.includes('Apagado')&&!!document.getElementById('ws-start')", 30))
    stop_sim.set()
    c.eval("document.getElementById('tab-system').click()"); wait_for(c, "!!document.getElementById('pw-old')")
    set_input(c, "pw-old", "mala", "input"); set_input(c, "pw-new", "nueva-clave-33", "input"); time.sleep(0.2)
    c.eval("document.getElementById('pw-go').click()")
    check("cambiar mi contraseña con la actual equivocada se rechaza", wait_for(c, "document.getElementById('sys-msg')&&document.getElementById('sys-msg').textContent.includes('no es correcta')"))
    # ---- gestión: configuración (nombre, descripción y etiquetas)
    def setsel(sel, val, proto="HTMLInputElement"):
        c.eval(f"(()=>{{const e=document.querySelector({json.dumps(sel)});Object.getOwnPropertyDescriptor({proto}.prototype,'value').set.call(e,{json.dumps(val)});e.dispatchEvent(new Event('input',{{bubbles:true}}));e.dispatchEvent(new Event('change',{{bubbles:true}}))}})()")
    c.eval("document.getElementById('tab-config').click()"); time.sleep(0.8)
    check("configuración: muestra nombre y las 3 etiquetas del proyecto", c.eval("document.getElementById('cfg-name').value") == "Flujo" and c.eval("document.querySelectorAll('#cfg-classes tbody tr').length") == 3)
    check("configuración: sin cambios el botón Guardar está inactivo", c.eval("document.getElementById('cfg-save').disabled") is True)
    setsel("#cfg-name", "Flujo renombrado"); setsel("#cfg-desc", "Descripción de prueba", "HTMLTextAreaElement")
    setsel("#cfg-classes tr[data-class=louse] .c-name", "Piojo adulto")
    c.eval("document.getElementById('cfg-add').click()"); time.sleep(0.3)
    setsel("#cfg-classes tr[data-class=new] .c-name", "Ácaro rojo"); setsel("#cfg-classes tr[data-class=new] .c-key", "3"); time.sleep(0.3)
    check("configuración: con cambios aparece «Cambios sin guardar» y Guardar se activa", "sin guardar" in c.eval("document.getElementById('cfg-msg').textContent") and c.eval("document.getElementById('cfg-save').disabled") is False)
    c.eval("document.getElementById('cfg-cancel').click()"); time.sleep(0.3)
    check("configuración: Cancelar descarta los cambios", c.eval("document.getElementById('cfg-name').value") == "Flujo" and c.eval("document.querySelectorAll('#cfg-classes tbody tr').length") == 3)
    setsel("#cfg-name", "Flujo renombrado"); setsel("#cfg-classes tr[data-class=louse] .c-name", "Piojo adulto")
    c.eval("document.getElementById('cfg-add').click()"); time.sleep(0.3)
    setsel("#cfg-classes tr[data-class=new] .c-name", "Ácaro rojo"); setsel("#cfg-classes tr[data-class=new] .c-key", "3"); time.sleep(0.3)
    c.eval("document.getElementById('cfg-save').click()")
    check("configuración: guardar muestra el aviso y la barra queda sin cambios", wait_for(c, "document.getElementById('cfg-msg').textContent==='Cambios guardados.'") and c.eval("document.getElementById('cfg-save').disabled") is True)
    cfg = [p for p in root.call("GET", "/api/projects") if p["id"] == PID][0]
    check("configuración: el servidor guardó el nombre, el id intacto y la clase nueva", cfg["name"] == "Flujo renombrado" and [x["id"] for x in cfg["config"]["classes"]] == ["louse", "fly", "not_louse", "acaro_rojo"] and cfg["config"]["classes"][0]["name"] == "Piojo adulto" and cfg["config"]["classes"][3]["digit"] == "3", [x["id"] for x in cfg["config"]["classes"]])
    check("configuración: la barra superior muestra el nombre nuevo", "Flujo renombrado" in c.eval("document.querySelector('.crumb-cur').textContent"))
    setsel("#cfg-classes tr[data-class=fly] .c-name", "Piojo adulto"); time.sleep(0.2)
    c.eval("document.getElementById('cfg-save').click()")
    check("configuración: un nombre de etiqueta repetido se rechaza con el motivo", wait_for(c, "document.getElementById('cfg-msg').textContent.includes('no pueden repetirse')"), c.eval("document.getElementById('cfg-msg').textContent"))
    c.eval("document.getElementById('cfg-cancel').click()")
    check("configuración: muestra la modalidad actual («con revisión») y las dos opciones", c.eval("document.getElementById('mode-review').getAttribute('aria-checked')") == "true" and c.eval("!!document.getElementById('mode-collab')"))
    c.eval("document.getElementById('mode-collab').click()"); time.sleep(0.3)
    check("elegir «colaborativa» avisa qué pasa con lo ya hecho y activa Guardar", c.eval("!!document.getElementById('cfg-mode-warn')") and c.eval("document.getElementById('cfg-save').disabled") is False)
    c.eval("document.getElementById('cfg-cancel').click()"); time.sleep(0.3)
    check("Cancelar vuelve a «con revisión»", c.eval("document.getElementById('mode-review').getAttribute('aria-checked')") == "true" and c.eval("!document.getElementById('cfg-mode-warn')"))
    # ---- lista de proyectos: crear uno nuevo
    c.eval("document.getElementById('crumb-projects').click()")
    check("proyectos: la lista muestra la tarjeta con su avance y «Gestionar proyecto»", wait_for(c, "!!document.querySelector('#plist .pcard2')") and "Gestionar proyecto" in c.eval("document.querySelector('#plist .pcard2').textContent"))
    setsel("#psearch", "zzz"); time.sleep(0.3)
    check("proyectos: la búsqueda filtra", c.eval("document.querySelectorAll('#plist .pcard2').length") == 0)
    setsel("#psearch", ""); time.sleep(0.2)
    c.eval("document.getElementById('newproject').click()")
    check("proyectos: «Nuevo proyecto» abre el formulario", wait_for(c, "!!document.getElementById('np-name')"))
    check("proyectos: sin nombre no se puede crear", c.eval("document.getElementById('np-go').disabled") is True)
    setsel("#np-name", "flujo RENOMBRADO"); time.sleep(0.2)
    c.eval("document.getElementById('np-go').click()")
    check("proyectos: un nombre repetido se rechaza", wait_for(c, "!!document.getElementById('np-err')") and "Ya existe" in c.eval("document.getElementById('np-err').textContent"))
    setsel("#np-name", "Segundo proyecto"); setsel("#np-desc", "otro estudio", "HTMLTextAreaElement"); time.sleep(0.2)
    c.eval("document.getElementById('mode-collab').click()"); time.sleep(0.2)
    c.eval("document.getElementById('np-go').click()")
    check("proyectos: crear abre la gestión del proyecto nuevo", wait_for(c, "location.hash.includes('v=admin')&&!!document.getElementById('tab-config')") and "Segundo proyecto" in c.eval("document.querySelector('.crumb-cur').textContent"), c.eval("location.hash"))
    mine = [p for p in root.call("GET", "/api/projects") if p["name"] == "Segundo proyecto"]
    check("proyectos: el creador es administrador y quedan las etiquetas por defecto", len(mine) == 1 and mine[0]["role"] == "admin" and len(mine[0]["config"]["classes"]) == 3 and mine[0]["config"]["description"] == "otro estudio" and mine[0]["config"]["mode"] == "collab")
    c.eval("location.hash='p=%d'" % PID); time.sleep(1.5)
    c.eval("document.getElementById('adminbtn').click()"); time.sleep(1)
    c.eval("document.getElementById('tab-quality').click()")
    c.eval("document.getElementById('navlabel').click()"); time.sleep(1.5)
    check("«← Etiquetar» vuelve al editor", wait_for(c, "!!document.getElementById('cv')"))
    # ---- papelera: eliminar una captura que no aporta, deshacer y restaurar desde la papelera
    n0 = c.eval("__lh.frames().length")
    check("el editor ofrece «Eliminar» a quien administra", c.eval("!!document.getElementById('delframe')&&!document.getElementById('delframe').disabled"))
    victim = c.eval("__lh.curId()")
    c.eval("document.getElementById('delframe').click()")
    check("eliminar pide confirmación y explica lo que pasa", wait_for(c, "!!document.getElementById('delmodal')") and "papelera" in c.eval("document.getElementById('delmodal').textContent") and c.eval("__lh.frames().length") == n0)
    c.eval("document.getElementById('del-cancel').click()"); time.sleep(0.2)
    check("cancelar no elimina nada", c.eval("!document.getElementById('delmodal')") and c.eval("__lh.frames().length") == n0)
    c.eval("document.getElementById('delframe').click()"); wait_for(c, "!!document.getElementById('delframe-ok')")
    c.eval("document.getElementById('delframe-ok').click()")
    check("confirmar la saca de la lista, pasa a otra imagen y ofrece «Deshacer»", wait_for(c, "__lh.frames().length===%d&&!!document.getElementById('undotoast')&&__lh.curId()!==%d" % (n0 - 1, victim)), c.eval("__lh.frames().length"))
    check("el servidor la tiene en la papelera y las estadísticas bajan", victim in [f["id"] for f in root.get(f"/api/projects/{PID}/frames?filter=trash")] and root.get(f"/api/projects/{PID}/stats")["frames"] == n0 - 1)
    c.eval("document.getElementById('undo-del').click()")
    check("«Deshacer» la devuelve a la lista y a la imagen actual", wait_for(c, "__lh.frames().length===%d&&__lh.curId()===%d&&!document.getElementById('undotoast')" % (n0, victim)))
    c.eval("document.getElementById('delframe').click()"); wait_for(c, "!!document.getElementById('delframe-ok')"); c.eval("document.getElementById('delframe-ok').click()"); wait_for(c, "__lh.frames().length===%d" % (n0 - 1))
    set_input(c, "flt", "trash", "change", "HTMLSelectElement")
    check("el filtro «Papelera» lista las eliminadas y las muestra bloqueadas con «Restaurar»", wait_for(c, "__lh.frames().length===1&&!!document.getElementById('trashbar')&&!!document.getElementById('restoreframe')&&!!document.getElementById('trashchip')") and c.eval("!document.getElementById('delframe')"))
    c.eval("document.getElementById('restoreframe').click()")
    check("restaurar la saca de la papelera (queda vacía)", wait_for(c, "!!document.getElementById('trash-empty')") and not root.get(f"/api/projects/{PID}/frames?filter=trash"))
    set_input(c, "flt", "all", "change", "HTMLSelectElement")
    check("y vuelve a estar entre las imágenes del proyecto", wait_for(c, "__lh.frames().length===%d" % n0) and root.get(f"/api/projects/{PID}/stats")["frames"] == n0)
    # ---- modalidad colaborativa en el editor: sin revisión, la última edición es la final
    root.call("PATCH", f"/api/projects/{PID}", {"mode": "collab"})
    c.eval("location.reload()"); time.sleep(2.5)
    wait_for(c, "!!document.getElementById('cv')")
    check("colaborativa: el panel no ofrece la pestaña «Revisión»", c.eval("!document.getElementById('tab-review')"))
    check("colaborativa: el filtro de la tira ofrece Todas / Sin revisar / Mi cola / Revisadas (y Papelera a quien administra)", c.eval("[...document.getElementById('flt').options].map(o=>o.textContent).join('|')") == "Todas|Sin revisar|Mi cola|Revisadas|Papelera")
    go(2)
    check("colaborativa: una imagen ya revisada muestra quién la editó por última vez", wait_for(c, "document.getElementById('statuschip').textContent.startsWith('Editada por')"), c.eval("document.getElementById('statuschip').textContent"))
    c.eval("document.getElementById('adminbtn').click()"); wait_for(c, "!!document.getElementById('tab-quality')")
    check("colaborativa: la calidad no muestra desacuerdos sino la explicación de la modalidad", wait_for(c, "!!document.getElementById('q-collab')&&!document.getElementById('q-disagree')"))
    c.eval("document.getElementById('tab-assign').click()"); time.sleep(0.6)
    check("colaborativa: el reparto no ofrece doble etiquetado", c.eval("![...document.querySelectorAll('#assign .fld')].some(e=>e.textContent.includes('Doble etiquetado'))"))
    print("\nRESUMEN: %d/%d OK" % (sum(res.values()), len(res)))
finally:
    env.close()
sys.exit(0 if res and all(res.values()) else 1)
