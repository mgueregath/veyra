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
    check("imagen con 2 personas de acuerdo: «Varias personas, de acuerdo»", wait_for(c, "document.getElementById('statuschip').textContent.includes('de acuerdo')"), chip())
    go(1)
    check("imagen en desacuerdo: lo dice el estado", wait_for(c, "document.getElementById('statuschip').textContent.includes('Desacuerdo')"), chip())
    go(3)
    check("imagen que nadie revisó: «Sin revisar»", wait_for(c, "document.getElementById('statuschip').textContent==='Sin revisar'"), chip())
    set_input(c, "flt", "disagreement", "change", "HTMLSelectElement"); time.sleep(1.0)
    check("el filtro «Con desacuerdo» deja solo esa imagen", c.eval("window.__lh.frames().length") == 1 and c.eval("window.__lh.frames()[0].status") == "disagreement")
    set_input(c, "flt", "needs_review", "change", "HTMLSelectElement"); time.sleep(1.0)
    check("el filtro «Para revisar» muestra las 3 con trabajo", c.eval("window.__lh.frames().length") == 3)
    check("el carrusel marca el estado de cada imagen", "≠" in c.eval("document.getElementById('strip').innerHTML") and "✓✓" in c.eval("document.getElementById('strip').innerHTML"))
    set_input(c, "flt", "all", "change", "HTMLSelectElement"); time.sleep(1.0)

    # ---- panel de revisión: ver el trabajo de cada persona y fijar la final
    go(1)
    check("el revisor (administrador) ve el panel de Revisión con las dos personas", wait_for(c, "document.querySelectorAll('#s-revision .rvrow').length===2"))
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
    set_input(c, "pw-old", "mala", "input"); set_input(c, "pw-new", "nueva-clave-33", "input"); time.sleep(0.2)
    c.eval("document.getElementById('pw-go').click()")
    check("cambiar mi contraseña con la actual equivocada se rechaza", wait_for(c, "document.getElementById('sys-msg')&&document.getElementById('sys-msg').textContent.includes('no es correcta')"))
    c.eval("document.getElementById('tab-quality').click()")
    c.eval("document.querySelector('.pagehead button').click()"); time.sleep(1.5)
    check("«← Etiquetar» vuelve al editor", wait_for(c, "!!document.getElementById('cv')"))
    print("\nRESUMEN: %d/%d OK" % (sum(res.values()), len(res)))
finally:
    env.close()
sys.exit(0 if res and all(res.values()) else 1)
