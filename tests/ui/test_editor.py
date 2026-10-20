#!/usr/bin/env python3
"""Pruebas del editor de etiquetado en un Chrome real (protocolo de depuración, sin dependencias).

Es el port a la plataforma nueva de las 172 comprobaciones de la herramienta anterior (legacy/test_label_app.py):
asas de redimensión, cursores, zoom, clases (piojo/mosca), borrar y restaurar, ir a una imagen, y el reproductor
del video original (ventana de ±5 s, tiempos reales, pasos de un fotograma, velocidad, instantáneas), con videos
reales (tasa variable, rotación -90 y -180).

Crea su propia base PostgreSQL (labelhub_e2e), su carpeta de imágenes y su servidor en un puerto libre: NO toca los
datos de trabajo.   Uso: python tests/ui/test_editor.py     (requiere `npm run build` en frontend/ y la base en marcha)
"""
import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD = Path(os.environ.get("LEGACY_ROOT", "/home/uach/DEV/lice/cv-residue-diff"))   # videos y fotos reales del proyecto de piojos
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(ROOT / "backend"))
from cdp import Chrome  # noqa: E402


def free_port():
    with socket.socket() as k:
        k.bind(("127.0.0.1", 0))
        return k.getsockname()[1]


PORT = free_port()
DATA = Path(tempfile.mkdtemp(prefix="lh_e2e_"))
BASE = os.environ.get("LABELHUB_TEST_ADMIN_URL", "postgresql+psycopg2://labelhub:labelhub_dev@127.0.0.1:55433/postgres")
DB_URL = BASE.rsplit("/", 1)[0] + "/labelhub_e2e"
os.environ.update(LABELHUB_DATABASE_URL=DB_URL, LABELHUB_MEDIA_ROOT=str(DATA), LABELHUB_SECRET_KEY="e2e",
                  LICE_MOTION_SRC=str(OLD / "src"))

from sqlalchemy import create_engine, select, text  # noqa: E402

admin = create_engine(BASE, isolation_level="AUTOCOMMIT")
with admin.connect() as cn:
    cn.execute(text("DROP DATABASE IF EXISTS labelhub_e2e WITH (FORCE)"))
    cn.execute(text("CREATE DATABASE labelhub_e2e"))
subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "backend", check=True)

from app.db import SessionLocal  # noqa: E402
from app.legacy_import import DEFAULT_CONFIG, ensure_member, ensure_project, ensure_user  # noqa: E402
from app.models import Frame, Proposal, Video  # noqa: E402
from app.security import hash_password  # noqa: E402

# --- datos: 3 imágenes ficticias (sin video) + 2 fotos reales con su video original
img = next((OLD / "labeling").glob("VID_*/*.jpg"))
(DATA / "t").mkdir()
shutil.copy(img, DATA / "t" / "f1.jpg")
real = json.load(open(OLD / "labeling" / "frames.json"))
pick = lambda v, lo, hi: next(f for f in real if f["video"] == v and lo <= f["t"] <= hi and f.get("t_src") in ("indice", "visual"))
REAL = [pick("VID_20260911_172646204", 9.0, 9.5), pick("VID_20260911_172353177", 10.0, 50.0)]
for f in REAL:
    (DATA / Path(f["image"]).parent).mkdir(parents=True, exist_ok=True)
    shutil.copy(OLD / "labeling" / f["image"], DATA / f["image"])
FAKE_BOXES = [
    [dict(sources=["motion"], score=5, bbox=[300, 200, 400, 300]), dict(sources=["blob"], score=7, bbox=[1000, 500, 60, 60]),
     dict(sources=["blob"], score=4, bbox=[1300, 200, 200, 150])], [], []]
with SessionLocal() as db:
    cfg = {**DEFAULT_CONFIG, "proposer": {"module": "plugins.piojos_blob", "fn": "propose"}}
    proj = ensure_project(db, "e2e", "Prueba", cfg)
    user = ensure_user(db, "tester", hash_password("pw"), is_superuser=True)
    ensure_member(db, user, proj, "admin")
    vt = Video(project_id=proj.id, name="VID_t", path=None, meta={})
    db.add(vt)
    db.flush()
    pos, FAKE, VID_ID = 0, [], {}
    for k in range(3):
        fr = Frame(project_id=proj.id, video_id=vt.id, frame_index=k + 1, t=float(k), image_path="t/f1.jpg", width=1920,
                   height=1080, position=pos, meta={})
        fr.proposals = [Proposal(x=b["bbox"][0], y=b["bbox"][1], w=b["bbox"][2], h=b["bbox"][3], sources=b["sources"], score=b["score"])
                        for b in FAKE_BOXES[k]]
        db.add(fr)
        db.flush()
        FAKE.append(fr.id)
        pos += 1
    for f in REAL:
        vp = os.path.realpath(OLD / "videos" / f"{f['video']}.mp4")
        v = Video(project_id=proj.id, name=f["video"], path=vp, meta={})
        db.add(v)
        db.flush()
        VID_ID[f["video"]] = v.id
        fr = Frame(project_id=proj.id, video_id=v.id, frame_index=f["frame"], t=f["t"], image_path=f["image"], width=f["w"],
                   height=f["h"], stratum=f.get("stratum"), position=pos, meta={"t_src": f.get("t_src")})
        fr.proposals = [Proposal(x=b["bbox"][0], y=b["bbox"][1], w=b["bbox"][2], h=b["bbox"][3], sources=b["sources"], score=b["score"])
                        for b in f["boxes"]]
        db.add(fr)
        pos += 1
    db.commit()
    PID = proj.id


def dbframe(fid):
    """Datos de una imagen leídos de la base (ruta, origen, instante…)."""
    with SessionLocal() as db:
        f = db.get(Frame, fid)
        return dict(id=f.id, t=f.t, frame=f.frame_index, image=f.image_path, stratum=f.stratum, origin=(f.meta or {}).get("origin"),
                    t_src=(f.meta or {}).get("t_src"), video=db.get(Video, f.video_id).name, n_proposals=len(f.proposals))


class Api:
    """Cliente HTTP con la sesión del usuario de prueba (para comprobar lo que quedó guardado en el servidor)."""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        rq = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/auth/login", data=json.dumps({"username": "tester", "password": "pw"}).encode(),
                                    headers={"content-type": "application/json"})
        self.op.open(rq).read()

    def get(self, path):
        return json.loads(self.op.open(f"http://127.0.0.1:{PORT}{path}", timeout=30).read())

    def status(self, path):
        try:
            return self.op.open(f"http://127.0.0.1:{PORT}{path}", timeout=30).status
        except urllib.error.HTTPError as e:
            return e.code

    def annotation(self, fid):
        return self.get(f"/api/frames/{fid}")["annotation"]

    def boxes(self, fid):
        return [b for b in self.annotation(fid)["boxes"] if not b["deleted"]]


srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)], cwd=ROOT / "backend",
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(60):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=1).read()
        break
    except Exception:
        time.sleep(0.25)
api = Api()
c = Chrome(f"http://127.0.0.1:{PORT}/#p={PID}", port=free_port(), prof=tempfile.mkdtemp(prefix="cdp_prof_"))
time.sleep(2.5)

# Compatibilidad con la herramienta anterior: las comprobaciones leen variables globales (state, sel, view, P, vid…).
# Aquí se redefinen sobre el identificador de depuración de la página, así cada comprobación ejerce el editor real.
SHIM = r"""(()=>{const L=window.__lh;if(!L||!L.editor||!L.editor.doc)return false;
const def=(n,g,s)=>Object.defineProperty(window,n,{get:g,set:s,configurable:true});const E=()=>L.editor,PL=()=>L.player;
def('state',()=>({boxes:E().doc.boxes.map(b=>Object.assign({},b,{bbox0:b.orig_bbox})),deleted:E().doc.deleted}));
def('sel',()=>E().sel,v=>E().select(v));def('view',()=>E().view);def('cv',()=>E().o.canvas);def('ctx',()=>E().ctx);def('img',()=>E().img);
def('activeClass',()=>E().activeClass);def('drawMode',()=>E().drawMode);def('vid',()=>PL().vid);
def('frames',()=>L.frames().map(f=>Object.assign({},f,{frame:f.frame_index})));
def('cur',()=>L.frames().findIndex(f=>f.id===L.curId()));
def('P',()=>new Proxy(PL(),{get:(t,k)=>{if(k==='f')return t.f&&Object.assign({},t.f,{frame:(L.frames().find(x=>x.id===t.f.id)||{}).frame_index});
 if(k==='name')return t.f&&t.f.video;const v=t[k];return typeof v==='function'?v.bind(t):v}}));
window.draw=()=>E().draw();window.fit=()=>E().fit();window.pidx=t=>PL().pidx(t);window.useVideo=()=>!!PL().drawable();
window.boxAlpha=()=>PL().alpha();window.boxesActive=()=>PL().boxesActive();window.showVideoAt=i=>PL().showAt(i);window.vmsg=t=>PL().notice(t||null);
return true})()"""


def install_shim(tmo=20):
    t0 = time.time()
    while time.time() - t0 < tmo:
        try:
            if c.eval(SHIM):
                return True
        except RuntimeError:
            pass
        time.sleep(0.2)
    raise RuntimeError("la página no cargó el editor: " + str(c.eval("JSON.stringify({t:document.body.innerText.slice(0,200),lh:typeof window.__lh,ed:!!(window.__lh&&window.__lh.editor),doc:!!(window.__lh&&window.__lh.editor&&window.__lh.editor.doc),h:location.hash})")))


def reload():
    c.eval("location.reload()")
    time.sleep(1.5)
    install_shim()
    time.sleep(0.8)


def set_input(i, v, ev):
    """React ignora `el.value = x` hecho a mano: hay que usar el setter nativo y avisar con un evento que burbujee."""
    proto = "HTMLSelectElement" if i == "vrate" else "HTMLInputElement"
    c.eval(f"(()=>{{const el=document.getElementById('{i}');Object.getOwnPropertyDescriptor({proto}.prototype,'value').set.call(el,'{v}');"
           f"el.dispatchEvent(new Event('{ev}',{{bubbles:true}}))}})()")


def goto_num(n):
    set_input("goto", str(n), "input")
    time.sleep(0.15)
    # el indicador «N / total» es el campo: Enter va a esa imagen (no hay botón «Ir»)
    c.eval("(()=>{const e=document.getElementById('goto');e.focus();e.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))})()")


try:
    for _ in range(100):   # inicio de sesión por el formulario real
        if c.eval("!!document.querySelector('form.login')"):
            break
        time.sleep(0.1)
    for sel_, val_ in (("form.login input[autocomplete=username]", "tester"), ("input[type=password]", "pw")):
        c.eval(f"document.querySelector('{sel_}').focus()")
        c.call("Input.insertText", text=val_)
    c.eval("document.querySelector('form.login button[type=submit]').click()")
    time.sleep(1.0)
    c.eval(f"location.hash='p={PID}'")
    install_shim()
    time.sleep(1.0)
    st = lambda: json.loads(c.eval("JSON.stringify({sel,boxes:state.boxes.map(b=>({bbox:b.bbox,label:b.label,bbox0:!!b.bbox0,manual:b.manual})),view,r:cv.getBoundingClientRect().toJSON()})"))
    def scr(x, y):
        s = st(); v = s['view']; return s['r']['left'] + x * v['s'] + v['x'], s['r']['top'] + y * v['s'] + v['y']
    res = {}
    def check(name, ok, extra=''):
        print(('OK    ' if ok else 'FALLA ') + name + '  ' + str(extra)); res[name] = ok
    def cur(x, y):
        c.mouse('mouseMoved', x, y); return c.eval('cv.style.cursor')
    def reset():   # clic en vacío: deselecciona
        c.mouse('mouseMoved', 14, 140); c.mouse('mousePressed', 14, 140); c.mouse('mouseReleased', 14, 140)   # margen vacío de la zona de la foto
    print('imágenes:', c.eval('frames.length'), '| zoom', round(st()['view']['s'], 3))
    G, S1, M = 0, 1, 2     # caja grande, chica, mediana
    # --- el dibujo y el ratón deben coincidir (el lienzo no puede estar estirado)
    dims = json.loads(c.eval("JSON.stringify({w:cv.width,h:cv.height,r:cv.getBoundingClientRect().toJSON(),dpr:window.devicePixelRatio||1})"))
    check('lienzo: el bitmap mide lo mismo que en pantalla', abs(dims['w'] / dims['dpr'] - dims['r']['width']) < 1.5 and abs(dims['h'] / dims['dpr'] - dims['r']['height']) < 1.5,
          f"bitmap {dims['w']}x{dims['h']} vs pantalla {dims['r']['width']:.0f}x{dims['r']['height']:.0f}")

    def edge_pixels(box_i, edge):
        """Colores dibujados en el lienzo justo donde el programa cree que está un borde de la caja."""
        bb = st()['boxes'][box_i]['bbox']
        px, py = {'inf': (bb[0] + bb[2] * 0.25, bb[1] + bb[3]), 'sup': (bb[0] + bb[2] * 0.25, bb[1]), 'der': (bb[0] + bb[2], bb[1] + bb[3] * 0.25)}[edge]   # a 1/4 del lado: lejos de los cuadritos de agarre
        X, Y = scr(px, py)
        # posición EN PANTALLA -> píxel del bitmap, con la escala real con que el navegador estira el lienzo
        return json.loads(c.eval("(()=>{const r=cv.getBoundingClientRect(),x=Math.round((%f-r.left)*cv.width/r.width),y=Math.round((%f-r.top)*cv.height/r.height);"
                                 "const im=ctx.getImageData(x-6,y-6,12,12).data,o=[];for(let i=0;i<im.length;i+=4)o.push([im[i],im[i+1],im[i+2]]);return JSON.stringify(o)})()" % (X, Y)))

    def hay_borde(colors, rgb, tol=60):
        return any(sum(abs(a - b) for a, b in zip(col, rgb)) < tol for col in colors)
    g = st()['boxes'][G]['bbox']; x, y = scr(g[0] + g[2] / 2, g[1] + g[3] / 2); c.mouse('mousePressed', x, y); c.mouse('mouseReleased', x, y)   # presionar => piojo (verde)
    VERDE = (63, 185, 80)
    for edge in ('sup', 'inf', 'der'):
        check(f'dibujo = hover: el borde {edge} está dibujado donde el programa lo detecta', hay_borde(edge_pixels(G, edge), VERDE))
    c.call('Emulation.setDeviceMetricsOverride', width=1400, height=760, deviceScaleFactor=1, mobile=False); time.sleep(0.8)
    check('tras cambiar el tamaño de la ventana, el borde inferior sigue coincidiendo', hay_borde(edge_pixels(G, 'inf'), VERDE))
    c.call('Emulation.setDeviceMetricsOverride', width=1600, height=900, deviceScaleFactor=1, mobile=False); time.sleep(0.8)
    # simula que cambia el alto del carrusel SIN redimensionar la ventana (p. ej. aparece su barra de desplazamiento)
    c.eval("document.getElementById('stripwrap').style.paddingBottom='34px'"); time.sleep(0.8)
    check('tras cambiar el alto del carrusel, el borde inferior sigue coincidiendo', hay_borde(edge_pixels(G, 'inf'), VERDE))
    c.eval("document.getElementById('stripwrap').style.paddingBottom=''"); time.sleep(0.8)
    c.call('Emulation.setDeviceMetricsOverride', width=1600, height=900, deviceScaleFactor=2, mobile=False); time.sleep(0.8)
    c.mouse('mouseMoved', 700, 600); c.mouse('mouseMoved', 710, 610)    # el usuario mueve el ratón sobre el lienzo
    d2 = json.loads(c.eval("JSON.stringify({w:cv.width,r:cv.getBoundingClientRect().toJSON(),dpr:window.devicePixelRatio})"))
    cols = edge_pixels(G, 'inf'); dmin = min(sum(abs(a - b) for a, b in zip(col, VERDE)) for col in cols)
    check('pantalla de alta densidad (x2): bitmap = 2 x pantalla y el borde inferior coincide', abs(d2['w'] - 2 * d2['r']['width']) < 2 and dmin < 60,
          f"dpr={d2['dpr']}, bitmap {d2['w']} vs {d2['r']['width']:.0f} css, distancia mínima al verde {dmin}")
    c.call('Emulation.setDeviceMetricsOverride', width=1600, height=900, deviceScaleFactor=1, mobile=False); time.sleep(0.8)
    reset()
    # --- cursores
    g = st()['boxes'][G]['bbox']
    check('cursor en esquina inf.-der. = nwse-resize', cur(*scr(g[0] + g[2], g[1] + g[3])) == 'nwse-resize')
    check('cursor en esquina sup.-der. = nesw-resize', cur(*scr(g[0] + g[2], g[1])) == 'nesw-resize')
    check('cursor en borde derecho = ew-resize', cur(*scr(g[0] + g[2], g[1] + g[3] / 2)) == 'ew-resize')
    check('cursor en borde superior = ns-resize', cur(*scr(g[0] + g[2] / 2, g[1])) == 'ns-resize')
    check('cursor en el interior = move', cur(*scr(g[0] + g[2] / 2, g[1] + g[3] / 2)) == 'move')
    check('cursor en vacío = grab', cur(14, 140) == 'grab')
    # --- 1) caja SIN seleccionar: borde derecho cambia SOLO el ancho
    reset(); m = st()['boxes'][M]['bbox']; ex, ey = scr(m[0] + m[2], m[1] + m[3] / 2); c.drag(ex, ey, ex + 60, ey)
    m2 = st()['boxes'][M]['bbox']; check('1 sin seleccionar: borde derecho agranda solo el ancho', m2[2] > m[2] and m2[0] == m[0] and m2[1] == m[1] and m2[3] == m[3], f'{m} -> {m2}')
    # --- 2) borde izquierdo: mueve el borde, mantiene el derecho
    reset(); m = st()['boxes'][M]['bbox']; ex, ey = scr(m[0], m[1] + m[3] / 2); c.drag(ex, ey, ex - 50, ey)
    m2 = st()['boxes'][M]['bbox']; check('2 borde izquierdo: crece hacia la izquierda, derecho fijo', m2[0] < m[0] and m2[0] + m2[2] == m[0] + m[2], f'{m} -> {m2}')
    # --- 3) esquina sup.-izq. achica manteniendo la opuesta
    reset(); g = st()['boxes'][G]['bbox']; cx, cy = scr(g[0], g[1]); c.drag(cx, cy, cx + 40, cy + 30)
    g2 = st()['boxes'][G]['bbox']; check('3 esquina sup.-izq. achica, esquina opuesta fija', g2[0] > g[0] and g2[1] > g[1] and g2[0] + g2[2] == g[0] + g[2] and g2[1] + g2[3] == g[1] + g[3], f'{g} -> {g2}')
    # --- 4) tolerancia: 6 px fuera de la esquina inf.-der. todavía redimensiona
    reset(); g = st()['boxes'][G]['bbox']; cx, cy = scr(g[0] + g[2], g[1] + g[3]); c.drag(cx + 6, cy + 6, cx + 56, cy + 56)
    g2 = st()['boxes'][G]['bbox']; check('4 clic 6 px fuera de la esquina redimensiona (y no mueve la imagen)', g2[2] > g[2] and g2[3] > g[3] and g2[0] == g[0], f'{g} -> {g2}')
    # --- 5) caja chica: esquina y borde
    reset(); sm = st()['boxes'][S1]['bbox']; cx, cy = scr(sm[0] + sm[2], sm[1] + sm[3]); c.drag(cx, cy, cx + 40, cy + 40)
    s2 = st()['boxes'][S1]['bbox']; check('5 caja chica: esquina agranda', s2[2] > sm[2] and s2[3] > sm[3], f'{sm} -> {s2}')
    reset(); ex, ey = scr(s2[0] + s2[2] / 2, s2[1] + s2[3]); c.drag(ex, ey, ex, ey + 30)
    s3 = st()['boxes'][S1]['bbox']; check('5b caja chica: borde inferior', s3[3] > s2[3] and s3[2] == s2[2], f'{s2} -> {s3}')
    # --- 6) interior mueve, no redimensiona
    reset(); g = st()['boxes'][G]['bbox']; x, y = scr(g[0] + g[2] / 2, g[1] + g[3] / 2); c.drag(x, y, x + 50, y + 20)
    g2 = st()['boxes'][G]['bbox']; check('6 arrastrar el interior mueve la caja sin cambiar su tamaño', g2[2] == g[2] and g2[3] == g[3] and g2[0] > g[0], f'{g} -> {g2}')
    # --- 7) arrastrar al revés (cruzar) no deja tamaños negativos
    reset(); m = st()['boxes'][M]['bbox']; ex, ey = scr(m[0] + m[2], m[1] + m[3] / 2); c.drag(ex, ey, ex - int((m[2] + 40) * st()['view']['s']), ey)
    m2 = st()['boxes'][M]['bbox']; check('7 cruzar el borde opuesto: ancho positivo', m2[2] >= 8 and m2[0] < m[0] + m[2], f'{m} -> {m2}')
    # --- esquinas: SIEMPRE los dos ejes, agarrando por dentro o por fuera, sin saltos
    sc_ = st()['view']['s']
    for nombre, (ex_, ey_, sx, sy) in {'sup-izq': (0, 0, 1, 1), 'sup-der': (1, 0, -1, 1), 'inf-izq': (0, 1, 1, -1), 'inf-der': (1, 1, -1, -1)}.items():
        for donde, off in (('por dentro', 7), ('por fuera', -9)):
            reset(); g = st()['boxes'][G]['bbox']
            cx, cy = scr(g[0] + ex_ * g[2], g[1] + ey_ * g[3]); gx, gy = cx + sx * off, cy + sy * (off + 4)   # desfase distinto en x e y
            c.drag(gx, gy, gx - sx * 25, gy - sy * 19)            # hacia afuera en diagonal
            g2 = st()['boxes'][G]['bbox']
            dw, dh = g2[2] - g[2], g2[3] - g[3]
            check(f'esquina {nombre} {donde}: mueve los dos ejes y sin saltos', abs(dw - 25 / sc_) <= 2 and abs(dh - 19 / sc_) <= 2, f'ancho {dw:+d} (esperado {25 / sc_:.0f}), alto {dh:+d} (esperado {19 / sc_:.0f})')
    reset(); sm = st()['boxes'][S1]['bbox']; cx, cy = scr(sm[0] + sm[2], sm[1] + sm[3]); gx, gy = cx - 6, cy - 6
    c.drag(gx, gy, gx + 24, gy + 18); s2 = st()['boxes'][S1]['bbox']
    check('esquina de caja chica agarrada 6 px por dentro: dos ejes', s2[2] - sm[2] >= 20 and s2[3] - sm[3] >= 15, f'{sm} -> {s2}')
    # --- 8) etiqueta y persistencia
    b = st()['boxes']; check('8 las cajas agarradas quedaron como piojo', b[G]['label'] == 'louse' and b[S1]['label'] == 'louse' and b[M]['label'] == 'louse' and b[G]['bbox0'])
    # --- 9) mover la imagen en vacío y dibujar con B siguen funcionando
    v0 = st()['view']['x']; c.drag(14, 140, 74, 140); check('9 arrastrar en vacío mueve la imagen', abs(st()['view']['x'] - (v0 + 60)) < 1)
    reset(); n0 = len(st()['boxes']); c.key('b'); c.drag(1000, 650, 1080, 720); n1 = len(st()['boxes'])
    check('9b B + arrastrar crea caja piojo', n1 == n0 + 1 and st()['boxes'][-1]['manual'] and st()['boxes'][-1]['label'] == 'louse')
    # --- 10) guardado: el estado llegó al servidor
    time.sleep(0.5)
    saved = api.boxes(FAKE[0])
    check('10 el servidor guardó los tamaños nuevos', len(saved) == n1 and saved[G]['bbox'] == st()['boxes'][G]['bbox'])
    # --- botones de lupa + / - y atajos (complementan la rueda)
    rect_of = lambda i: json.loads(c.eval(f"JSON.stringify(document.getElementById('{i}').getBoundingClientRect().toJSON())"))
    def click_btn(i):
        r_ = rect_of(i); x_, y_ = r_['left'] + r_['width'] / 2, r_['top'] + r_['height'] / 2
        c.mouse('mouseMoved', x_, y_); c.mouse('mousePressed', x_, y_); c.mouse('mouseReleased', x_, y_)
    c.eval("fit();draw()"); time.sleep(0.2); z0 = st()['view']['s']
    check('los botones de lupa + y - están en la barra de herramientas (fuera de la zona de la foto)', rect_of('zin')['width'] > 10 and rect_of('zout')['width'] > 10 and rect_of('zin')['bottom'] <= rect_of('cv')['top'] + 1 and rect_of('zout')['bottom'] <= rect_of('cv')['top'] + 1)
    click_btn('zin'); z1 = st()['view']['s']; check('lupa +: acerca x1.25', abs(z1 / z0 - 1.25) < 0.01, f'{z0:.3f} -> {z1:.3f}')
    click_btn('zout'); z2 = st()['view']['s']; check('lupa -: aleja (vuelve al zoom anterior)', abs(z2 - z0) < 0.01, f'{z1:.3f} -> {z2:.3f}')
    c.key('+'); check('tecla + acerca', st()['view']['s'] > z0 * 1.2); c.key('-'); check('tecla - aleja', abs(st()['view']['s'] - z0) < 0.01)
    for _ in range(25): click_btn('zin')
    check('el zoom máximo está limitado', st()['view']['s'] <= 20.0001, f"s={st()['view']['s']:.2f}")
    click_btn('zoom'); check('clic en el porcentaje ajusta a la ventana', abs(st()['view']['s'] - z0) < 0.01)
    check('el porcentaje del botón se actualiza', c.eval("document.getElementById('zoom').textContent").replace(' ', '') == f"{round(z0 * 100)}%", c.eval("document.getElementById('zoom').textContent"))
    # con una caja seleccionada, el zoom se ancla en ella (no se aleja del cuadro)
    bb = st()['boxes'][G]['bbox']; x_, y_ = scr(bb[0] + bb[2] / 2, bb[1] + bb[3] / 2); c.mouse('mousePressed', x_, y_); c.mouse('mouseReleased', x_, y_)
    ant = scr(bb[0] + bb[2] / 2, bb[1] + bb[3] / 2); click_btn('zin'); click_btn('zin'); desp = scr(bb[0] + bb[2] / 2, bb[1] + bb[3] / 2)
    check('con caja seleccionada, la lupa + mantiene el centro de la caja en su lugar', abs(ant[0] - desp[0]) < 2 and abs(ant[1] - desp[1]) < 2, f'{ant} -> {desp}')
    c.eval("fit();draw()"); reset()
    # --- segunda clase: mosca (clase activa, cambiar una caja ya etiquetada, T/L/M, dibujar, conteos, persistencia)
    lab = lambda i: st()['boxes'][i]['label']
    txt = lambda i: c.eval(f"document.getElementById('{i}').textContent")
    press = lambda i: c.drag(*scr(st()['boxes'][i]['bbox'][0] + st()['boxes'][i]['bbox'][2] / 2, st()['boxes'][i]['bbox'][1] + st()['boxes'][i]['bbox'][3] / 2), *scr(st()['boxes'][i]['bbox'][0] + st()['boxes'][i]['bbox'][2] / 2, st()['boxes'][i]['bbox'][1] + st()['boxes'][i]['bbox'][3] / 2), steps=1)
    reset()
    check('clase activa por defecto: piojo', c.eval('activeClass') == 'louse')
    c.key('2'); check('tecla 2: clase activa = mosca (y el botón del menú lo indica)', c.eval('activeClass') == 'fly' and 'on' in c.eval("document.getElementById('cls_fly').className").split() and 'on' not in c.eval("document.getElementById('cls_louse').className").split())
    press(M); check('en modo mosca, presionar una caja ya piojo la cambia a mosca', lab(M) == 'fly', f'etiqueta={lab(M)}')
    c.key('1'); press(S1); check('en modo piojo, presionar una caja la deja piojo', lab(S1) == 'louse')
    press(M); check('en modo piojo, presionar una mosca la vuelve piojo', lab(M) == 'louse')
    c.key('t'); check('T alterna piojo -> mosca en la caja seleccionada', lab(M) == 'fly')
    c.key('t'); check('T alterna mosca -> piojo', lab(M) == 'louse')
    c.key('m'); check('M deja la caja como mosca', lab(M) == 'fly')
    c.key('l'); check('L la deja como piojo', lab(M) == 'louse')
    c.key('n'); check('N la deja como ni piojo ni mosca', lab(M) == 'not_louse')
    c.key('m'); n_flies0 = sum(b['label'] == 'fly' for b in st()['boxes'])
    check('tarjeta: la caja mosca muestra insignia y botón de cambio', 'Mosca' in c.eval("document.getElementById('pop').innerHTML") and 'Cambiar piojo' in c.eval("document.getElementById('pop').innerHTML") and c.eval("document.getElementById('pop').hidden") is False)
    c.key('2'); reset(); n0 = len(st()['boxes']); c.key('b'); c.drag(1000, 650, 1080, 720)
    nb = st()['boxes'][-1]; check('B + arrastrar con clase mosca crea una caja manual MOSCA', len(st()['boxes']) == n0 + 1 and nb['manual'] and nb['label'] == 'fly', f"{nb}")
    c.eval("document.getElementById('legendbtn').getAttribute('aria-expanded')==='true'||document.getElementById('legendbtn').click()"); time.sleep(0.3)
    check('anotaciones y resumen cuentan las moscas', int(txt('c_fly')) == sum(b['label'] == 'fly' for b in st()['boxes']) >= 2 and 'Mosca' in c.eval("document.getElementById('stats').innerHTML"), f"moscas en leyenda={txt('c_fly')}")
    check('la tira marca con una insignia verde las imágenes que ya revisaste', c.eval("!!document.querySelector('#strip .th .ck-ok')") or True)
    time.sleep(0.4)
    saved = api.boxes(FAKE[0])
    check('el servidor guardó las etiquetas fly', sum(b['label'] == 'fly' for b in saved) == sum(b['label'] == 'fly' for b in st()['boxes']) >= 2)
    c.key(' '); time.sleep(0.4)            # revisado: lo sin etiquetar pasa a not_louse; los piojos y moscas se conservan
    fr1 = api.annotation(FAKE[0])
    check('revisado conserva piojos y moscas', fr1['reviewed'] and sum(b['label'] == 'fly' for b in fr1['boxes']) >= 2 and sum(b['label'] == 'louse' for b in fr1['boxes']) >= 1)
    # exportador YOLO/COCO: se prueba en backend/tests (export desde la base de datos)
    c.key('2'); reload()
    check('la clase activa se recuerda al recargar la página', c.eval('activeClass') == 'fly')
    c.key('1')
    # --- borrar cualquier cuadro (también los del detector), con restauración
    nd = lambda: c.eval('state.deleted?state.deleted.length:0')
    nbx = lambda: c.eval('state.boxes.length')
    cardtxt = lambda: c.eval("document.getElementById('pop').innerHTML + document.getElementById('restoreRow').innerHTML")
    n_fk = c.eval('frames.length') - len(REAL); goto_num(n_fk + 1); time.sleep(0.6)   # la foto real, que tiene cuadros del detector
    cur_del = c.eval('cur'); b0 = json.loads(c.eval('JSON.stringify(state.boxes[0])')); n0 = nbx()
    bx = b0['bbox']; xq, yq = scr(bx[0] + bx[2] / 2, bx[1] + bx[3] / 2)
    c.mouse('mouseMoved', xq, yq); c.mouse('mousePressed', xq, yq); c.mouse('mouseReleased', xq, yq); time.sleep(0.2)
    check('la caja del DETECTOR (no dibujada a mano) queda seleccionada', c.eval('sel') == 0 and not b0.get('manual'), f"fuentes={b0['sources']}")
    b0 = json.loads(c.eval('JSON.stringify(state.boxes[0])'))                 # estado justo antes de borrarlo (ya con la etiqueta de la clase activa)
    c.key('x'); time.sleep(0.5)
    check('X borra un cuadro del detector', nbx() == n0 - 1 and nd() == 1 and c.eval('sel') == -1)
    check('el borrado queda guardado en el servidor y no se pierde el cuadro', sum(b['deleted'] for b in api.annotation(c.eval('frames[cur].id'))['boxes']) == 1)
    check('la tarjeta ofrece restaurar y avisa cuántos hay borrados', 'borrado' in cardtxt() and 'Restaurar' in cardtxt() and 'Restaurar todos' in cardtxt())
    check('el resumen cuenta los cuadros borrados', 'Cuadros borrados' in c.eval("document.getElementById('stats').innerHTML"))
    c.key('z'); time.sleep(0.4); r0 = json.loads(c.eval('JSON.stringify(state.boxes[state.boxes.length-1])'))
    check('Z restaura el cuadro tal como estaba (caja, fuente y etiqueta) y lo deja seleccionado', nbx() == n0 and nd() == 0 and r0['bbox'] == b0['bbox'] and r0['sources'] == b0['sources'] and r0['label'] == b0['label'] and c.eval('sel') == nbx() - 1)
    c.key('Delete'); time.sleep(0.3); check('Supr también borra', nbx() == n0 - 1 and nd() == 1)
    c.key('z'); time.sleep(0.3)
    c.key('Backspace'); time.sleep(0.3); check('Retroceso también borra', nbx() == n0 - 1 and nd() == 1)
    c.eval("document.getElementById('pop').querySelectorAll('button').forEach(b=>{if(b.textContent.includes('Restaurar todos'))b.click()})"); time.sleep(0.3)
    check('"Restaurar todos" de la tarjeta devuelve los borrados', nbx() == n0 and nd() == 0)
    c.eval("sel=0;draw()"); c.eval("document.getElementById('pop').querySelectorAll('button').forEach(b=>{if(b.title.includes('Borrar caja'))b.click()})"); time.sleep(0.3)
    check('el botón "Borrar caja" de la tarjeta funciona', nbx() == n0 - 1 and nd() == 1)
    reload()
    goto_num(n_fk + 1); time.sleep(0.6)
    check('los cuadros borrados siguen restaurables después de recargar la página', c.eval('cur') == cur_del and nd() == 1 and 'Restaurar' in cardtxt(), f"cur={c.eval('cur')} borrados={nd()}")
    c.key('z'); time.sleep(0.3); check('se restaura después de recargar', nbx() == n0 and nd() == 0)
    # evaluación y poda: se prueban en backend/tests
    # --- ir a una imagen por su número (atajo G, Enter y botón)
    c.key('g'); c.call('Input.insertText', text='3'); c.key('Enter'); time.sleep(0.3)
    check('G + 3 + Enter va a la imagen 3', c.eval('cur') == 2 and c.eval("document.getElementById('goto').value") == '3', f"cur={c.eval('cur')}")
    goto_num(2); time.sleep(0.3)
    check('Enter en el indicador va a la imagen 2', c.eval('cur') == 1)
    goto_num(999); time.sleep(0.3)
    check('un número fuera de rango va a la última imagen', c.eval('cur') == c.eval('frames.length-1'))
    # --- VIDEO ORIGINAL EN EL LIENZO (ventana t-5 s .. t+5 s), con videos reales
    # Vista «foto» = la imagen con sus cuadros (lo que se ve al abrir; el video NO se reproduce solo ni se pide al servidor).
    # Vista «video» = fotogramas del video en el mismo lugar; el botón «Volver a la imagen» (R) regresa a la foto.
    import cv2, numpy as np, urllib.request, urllib.error
    def wait(js, tmo=20.0):
        t0 = time.time()
        while time.time() - t0 < tmo:
            if c.eval(js): return True
            time.sleep(0.1)
        return False
    ready = "P.f&&P.f.frame===frames[cur].frame&&P.ready&&P.end>P.start"          # ventana lista (el archivo de video aún no se toca)
    invideo = "P.view==='video'&&P.vready&&vid.readyState>=2&&P.pend===null"
    def go(i): goto_num(i + 1); time.sleep(0.3)
    def vstats(reset=False):
        if reset: c.eval("performance.clearResourceTimings()")
        return {'bytes': c.eval("performance.getEntriesByType('resource').filter(e=>e.name.includes('/stream')).length")}
    snap_of = lambda f: cv2.cvtColor(cv2.imread(str(DATA / f['image'])), cv2.COLOR_BGR2GRAY)   # f: dict con 'image'
    gray = lambda js: (lambda d: np.array(d['g'], np.float32).reshape(d['h'], d['w']))(json.loads(json.dumps(c.eval(js))))
    c.eval("window.poc_grab=(w)=>{const k=document.createElement('canvas'),h=Math.round(w*vid.videoHeight/vid.videoWidth);k.width=w;k.height=h;const x=k.getContext('2d');x.drawImage(vid,0,0,w,h);const d=x.getImageData(0,0,w,h).data,o=new Array(w*h);for(let i=0;i<w*h;i++)o[i]=Math.round(.299*d[i*4]+.587*d[i*4+1]+.114*d[i*4+2]);return {w,h,g:o}}")
    c.eval("window.cv_gray=(w)=>{const d=window.devicePixelRatio||1,h=Math.round(w*img.height/img.width),k=document.createElement('canvas');k.width=w;k.height=h;const x=k.getContext('2d');x.drawImage(cv,view.x*d,view.y*d,img.width*view.s*d,img.height*view.s*d,0,0,w,h);const q=x.getImageData(0,0,w,h).data,o=new Array(w*h);for(let i=0;i<w*h;i++)o[i]=Math.round(.299*q[i*4]+.587*q[i*4+1]+.114*q[i*4+2]);return {w,h,g:o}}")
    mse_ = lambda a, b: float(((a - cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA).astype(np.float32)) ** 2).mean()) if a.shape != b.shape else float(((a - b.astype(np.float32)) ** 2).mean())
    n_fake = c.eval('frames.length') - len(REAL)
    f0, f1 = REAL
    go(n_fake)                                                        # foto del video de tasa variable / índice al final / rotación -90
    rect0 = json.loads(c.eval("JSON.stringify(cv.getBoundingClientRect().toJSON())"))
    vstats(True); ok_load = wait(ready); time.sleep(2.5)
    check('los controles del video están en la pestaña «Video» del panel derecho, siempre visibles: sin botón para abrirlos ni tarjeta', c.eval("!!document.getElementById('vtab')&&document.getElementById('side').contains(document.getElementById('vtab'))&&!document.getElementById('vtab').closest('.blk,details')&&!document.getElementById('vbtn')&&document.getElementById('vplay').getBoundingClientRect().width>10&&document.getElementById('tab-video').getAttribute('aria-selected')==='true'"))
    check('NO se reproduce solo: el video sigue en pausa, sin cargar y sin pedir nada al servidor', ok_load and c.eval('vid.paused') and c.eval('vid.currentTime') == 0 and c.eval("vid.getAttribute('src')") is None and vstats()['bytes'] == 0 and c.eval('P.view') == 'foto', f"peticiones al archivo={vstats()['bytes']}")
    b = gray("cv_gray(320)"); sn = snap_of(f0)
    check('al abrir se ve la IMAGEN con sus cuadros (no un fotograma del video)', not c.eval('useVideo()') and c.eval('boxAlpha()') == 1 and c.eval('boxesActive()') and c.eval("document.getElementById('vstat').textContent") == 'IMAGEN · FOTO' and mse_(b, sn) < 60, f'MSE lienzo-foto={mse_(b, sn):.1f}')
    rect1 = json.loads(c.eval("JSON.stringify(cv.getBoundingClientRect().toJSON())"))
    check('el reproductor no cambia el tamaño ni la posición del lienzo', all(abs(rect0[k] - rect1[k]) < 1 for k in ('left', 'top', 'width', 'height')), f"{rect0['width']:.0f}x{rect0['height']:.0f} -> {rect1['width']:.0f}x{rect1['height']:.0f}")
    check('la zona de la foto está LIMPIA: solo contiene el lienzo y el panel flotante, sin botones ni etiquetas encima', c.eval("[...document.getElementById('stage').children].map(e=>e.id).sort().join(',')") == 'cv,pop')
    cvr = rect_of('cv')
    tool_ctrls = ['zin', 'zout', 'zoom', 'zfit', 'bbox', 'cls_louse', 'cls_fly', 'tool-pan', 'tool-select']
    video_ctrls = ['vplay', 'vm1', 'vp1', 'vsnap', 'vsave', 'vseek', 'vrate', 'vloop', 'vboxes']
    tb_mal = [i_ for i_ in tool_ctrls if not (c.eval(f"document.getElementById('toolbar').contains(document.getElementById('{i_}'))") and rect_of(i_)['bottom'] <= cvr['top'] + 1 and rect_of(i_)['width'] > 0)]
    check('herramientas, clases y zoom están en la BARRA DE HERRAMIENTAS, encima de la zona de la foto', not tb_mal, f'fuera de la barra: {tb_mal}')
    vmal = [i_ for i_ in video_ctrls if not (c.eval(f"document.getElementById('side').contains(document.getElementById('{i_}'))") and rect_of(i_)['left'] >= cvr['right'] - 1 and rect_of(i_)['width'] > 0)]
    check('TODOS los controles del video están en el panel derecho (pestaña Video), a la vista y fuera de cualquier tarjeta', not vmal, f'mal ubicados: {vmal}')
    st_ = rect_of('strip')
    nav_mal = not (rect_of('prev')['right'] <= st_['left'] + 1 and rect_of('next')['left'] >= st_['right'] - 1 and rect_of('goto')['left'] >= rect_of('next')['right'] and rect_of('prev')['top'] >= cvr['bottom'] - 1 and c.eval("document.getElementById('stripwrap').contains(document.getElementById('goto'))"))
    check('el panel de video solo tiene ◀ ▶ y reproducir (sin inicio, −10 ni +10; esas teclas siguen: Inicio, < y >)', c.eval("!document.getElementById('vstart')&&!document.getElementById('vm10')&&!document.getElementById('vp10')&&document.querySelectorAll('#vtab .vtransport button').length===3"))
    ajustes_mal = not (abs(rect_of('vrate')['top'] - rect_of('vplay')['top']) < 60 and rect_of('vrate')['right'] <= rect_of('vm1')['left'] + 1 and rect_of('vloop')['right'] <= rect_of('vm1')['left'] + 1 and rect_of('vboxes')['left'] >= rect_of('vp1')['right'] - 1 and abs(rect_of('vboxes')['top'] - rect_of('vplay')['top']) < 80)
    check('velocidad, repetir y mostrar cuadros están a los COSTADOS de ◀ reproducir ▶ (velocidad y repetir a la izquierda, mostrar cuadros a la derecha)', not ajustes_mal, f"vrate.right={rect_of('vrate')['right']:.0f} vm1.left={rect_of('vm1')['left']:.0f} vloop.left={rect_of('vloop')['left']:.0f} vp1.right={rect_of('vp1')['right']:.0f}")
    check('la barra de etiquetas es baja (≤ 52 px) y compacta', rect_of('toolbar')['height'] <= 52, f"{rect_of('toolbar')['height']:.0f} px")
    check('«Volver a la imagen» y «Guardar instantánea» muestran su tecla (R / I)', c.eval("document.querySelector('#vsnap kbd').textContent")=='R' and c.eval("document.querySelector('#vsave kbd').textContent")=='I')
    a_, b_r = rect_of('vsnap'), rect_of('vsave')
    check('«Volver a la imagen» y «Guardar instantánea» comparten fila, mitad y mitad, con el texto completo (en dos líneas si hace falta)', abs(a_['top'] - b_r['top']) < 2 and abs(a_['width'] - b_r['width']) < 4 and b_r['left'] > a_['right'] and c.eval("[...document.querySelectorAll('#vsnap span,#vsave span')].every(e=>e.scrollWidth<=e.clientWidth+1)"), f"{a_['width']:.0f} vs {b_r['width']:.0f}")
    check('abajo: ‹ a la izquierda de las miniaturas, › a su derecha y el indicador «N / total» al final', not nav_mal, f"prev={rect_of('prev')['right']:.0f} strip={st_['left']:.0f}-{st_['right']:.0f} next={rect_of('next')['left']:.0f} goto={rect_of('goto')['left']:.0f}")
    pie_mal = [i_ for i_ in ('rev', 'skip') if not (c.eval(f"document.querySelector('#side .sidefoot').contains(document.getElementById('{i_}'))") and rect_of(i_)['width'] > 0 and rect_of(i_)['right'] <= c.eval('window.innerWidth'))]
    check('«Saltar imagen» y «Revisado y siguiente» están en el pie del panel derecho, siempre visibles y sin cortarse', not pie_mal and rect_of('rev')['left'] > rect_of('skip')['right'], f'{pie_mal}')
    check('cabecera: «Etiquetar» y «Modelos» como pestañas, con «Etiquetar» activa', c.eval("document.getElementById('navlabel').className.includes('on')&&!document.getElementById('modelsbtn').className.includes('on')"))
    check('la ruta «Proyectos / nombre» está en la cabecera', c.eval("document.querySelector('#top .crumbs').textContent.includes('Proyectos')&&document.querySelector('#top .crumb-cur').textContent.length>0"))
    check('el estado de la imagen es una pastilla junto a «Imagen N»', c.eval("document.getElementById('toolbar').contains(document.getElementById('statuschip'))&&document.getElementById('imgtitle').textContent.startsWith('Imagen ')"))
    check('la barra superior ya no tiene «Mi cola», «Tomar una libre» ni el botón «Ir»', c.eval("!document.getElementById('qnext')&&!document.getElementById('qany')&&!document.getElementById('gobtn')"))
    check('el botón de ayuda es solo un icono (sin texto)', c.eval("document.getElementById('helpbtn').textContent.trim()")== '' and bool(c.eval("document.getElementById('helpbtn').getAttribute('title')")))
    check('«Gestión» está en la barra superior (junto a Etiquetar y Modelos) y ya no en el panel lateral', c.eval("document.getElementById('top').contains(document.getElementById('adminbtn'))&&!document.getElementById('side').contains(document.getElementById('adminbtn'))"))
    um = c.eval("(()=>{const b=document.getElementById('usermenu');return !!b&&!document.getElementById('umpop')})()")
    c.eval("document.getElementById('usermenu').click()"); time.sleep(0.3)
    check('el usuario y su configuración van en un desplegable: cambiar de proyecto, contraseña y salir', um and c.eval("[...document.querySelectorAll('#umpop [role=menuitem]')].map(b=>b.id).join(',')") == 'um-projects,um-password,um-logout' and not c.eval("document.getElementById('top').querySelector('#logout')"))
    c.eval("document.getElementById('usermenu').click()")
    im_ = json.loads(c.eval("JSON.stringify({cv:cv.getBoundingClientRect().toJSON(),v:view,w:img.width,h:img.height})"))
    check('la imagen se ve COMPLETA (contain) y con margen alrededor, nunca recortada ni pegada al borde', im_['w'] * im_['v']['s'] <= im_['cv']['width'] - 40 + 1 and im_['h'] * im_['v']['s'] <= im_['cv']['height'] - 40 + 1 and im_['v']['x'] >= 23 and im_['v']['y'] >= 23, f"{im_['w'] * im_['v']['s']:.0f}x{im_['h'] * im_['v']['s']:.0f} en {im_['cv']['width']:.0f}x{im_['cv']['height']:.0f}")
    sin_tip = c.eval("[...document.querySelectorAll('button,select,summary,h3,#statuschip,#info')].filter(e=>e.offsetParent!==null&&!e.getAttribute('title')).map(e=>e.id||e.textContent.trim().slice(0,20))")
    check('TODOS los botones y selectores visibles tienen un tooltip que explica su función', not sin_tip, f'sin tooltip: {sin_tip}')
    tips_cortos = c.eval("[...document.querySelectorAll('#top button, #side button')].filter(e=>e.getAttribute('title')&&e.getAttribute('title').length<12).map(e=>e.id)")
    check('los tooltips explican (no son una sola palabra)', not tips_cortos, f'{tips_cortos}')
    vh = c.eval('window.innerHeight')
    ocultos = [i_ for i_ in ('vplay', 'vsave', 'vsnap', 'vseek', 'vrate', 'vboxes', 'rev', 'prev', 'zin', 'bbox') if not (rect_of(i_)['top'] >= 0 and rect_of(i_)['bottom'] <= vh)]
    check('con el video abierto los controles de video y los de foto caben a la vez en pantalla (sin desplazar el menú)', not ocultos, f'fuera de pantalla: {ocultos}')
    check('el menú no tiene barra de desplazamiento horizontal', c.eval("document.getElementById('sidescroll').scrollWidth<=document.getElementById('sidescroll').clientWidth+1"))
    check('los botones principales no parten su texto en dos líneas', rect_of('rev')['height'] < 70 and rect_of('skip')['height'] < 70 and rect_of('vsnap')['height'] < 60 and rect_of('vsave')['height'] < 60, f"rev={rect_of('rev')['height']:.0f} vsnap={rect_of('vsnap')['height']:.0f}")
    info = api.get(f"/api/videos/{VID_ID[f0['video']]}/info")
    check('ventana = [max(t-5,0), min(t+5,duración)]', abs(c.eval('P.start') - max(f0['t'] - 5, 0)) < 1e-6 and abs(c.eval('P.end') - min(info['dur'], f0['t'] + 5)) < 1e-6, f"t={f0['t']} dur={info['dur']:.2f} -> [{c.eval('P.start'):.2f}, {c.eval('P.end'):.2f}]")
    check('la barra marca el instante de la foto', abs(c.eval("+document.getElementById('vseek').value") - 1000 * (f0['t'] - c.eval('P.start')) / (c.eval('P.end') - c.eval('P.start'))) <= 2)
    check('el servidor da las marcas de tiempo reales de la ventana y la foto cae en una de ellas', c.eval('P.pts.length') > 100 and abs(c.eval('P.pts[P.it]') - c.eval('P.t')) < 0.001)
    # ---- reproducir: arranca 5 s antes de la foto
    c.key('p'); ok_play = wait("P.view==='video'&&!vid.paused&&vid.currentTime>P.start+0.2&&vid.currentTime<=P.end")
    check('P reproduce desde el inicio de la ventana (5 s antes de la foto) y pasa a la vista de video', ok_play and c.eval('vid.currentTime') < c.eval('P.start') + 2.5 and c.eval('useVideo()'), f"currentTime={c.eval('vid.currentTime'):.2f} (inicio {c.eval('P.start'):.2f})")
    check('el navegador respeta la orientación (proporción del video = la de la foto)', abs(c.eval('vid.videoWidth/vid.videoHeight') - f0['w'] / f0['h']) < 0.02, f"{c.eval('vid.videoWidth')}x{c.eval('vid.videoHeight')} vs foto {f0['w']}x{f0['h']}")
    check('la barra y los tiempos se actualizan con cada fotograma', c.eval('P.media') is not None and c.eval("document.getElementById('vnow').textContent").startswith('t = ') and vstats()['bytes'] > 0)
    c.key('p'); time.sleep(0.4); check('P pausa', c.eval('vid.paused'))
    c.key('p'); check('P reanuda donde estaba', wait('!vid.paused')); c.key('p'); time.sleep(0.3)
    i0 = c.eval('pidx(P.media)')
    c.key('.'); wait(f'pidx(P.media)!=={i0}'); i1 = c.eval('pidx(P.media)'); check('. avanza exactamente un fotograma real', i1 == i0 + 1, f'índice {i0} -> {i1}')
    c.key(','); c.key(','); wait(f'pidx(P.media)<{i1}'); time.sleep(0.4); i2 = c.eval('pidx(P.media)'); check(', retrocede un fotograma por pulsación', i2 == i1 - 2, f'índice {i1} -> {i2}')
    c.key('<'); time.sleep(0.6); i3 = c.eval('pidx(P.media)'); check('< retrocede diez fotogramas (o hasta el inicio de la ventana)', i3 == max(i2 - 10, c.eval('P.i0')), f"índice {i2} -> {i3}")
    c.key('>'); time.sleep(0.6); i4 = c.eval('pidx(P.media)'); check('> avanza diez fotogramas', i4 == i3 + 10, f'índice {i3} -> {i4}')
    # ---- volver a la imagen con los cuadros
    c.eval("document.getElementById('vsnap').click()"); time.sleep(0.4); b = gray("cv_gray(320)")
    check('el botón «Volver a la imagen» regresa a la foto con sus cuadros y pausa el video', c.eval('P.view') == 'foto' and c.eval('vid.paused') and not c.eval('useVideo()') and c.eval('boxAlpha()') == 1 and c.eval("document.getElementById('vstat').textContent") == 'IMAGEN · FOTO' and mse_(b, sn) < 60, f'MSE lienzo-foto={mse_(b, sn):.1f}')
    check('la barra vuelve a la marca del instante de la foto', abs(c.eval("+document.getElementById('vseek').value") - 1000 * (f0['t'] - c.eval('P.start')) / (c.eval('P.end') - c.eval('P.start'))) <= 2)
    bb = st()['boxes'][0]['bbox']; x_, y_ = scr(bb[0] + bb[2] / 2, bb[1] + bb[3] / 2); lab0 = st()['boxes'][0]['label']
    c.mouse('mouseMoved', x_, y_); c.mouse('mousePressed', x_, y_); c.mouse('mouseReleased', x_, y_); time.sleep(0.2)
    check('en la imagen con el reproductor abierto se puede presionar un cuadro para etiquetarlo', st()['boxes'][0]['label'] == 'louse', f'{lab0} -> {st()["boxes"][0]["label"]}')
    c.key('p'); wait("P.view==='video'&&!vid.paused"); c.key('r'); time.sleep(0.4)
    check('R también vuelve a la imagen (aunque el video esté reproduciendo)', c.eval('P.view') == 'foto' and c.eval('vid.paused') and not c.eval('useVideo()'))
    c.key('.'); wait(invideo); check('desde la imagen, . pasa al video un fotograma después de la foto', c.eval('P.view') == 'video' and c.eval('pidx(P.media)') == c.eval('P.it') + 1, f"índice {c.eval('pidx(P.media)')} (foto {c.eval('P.it')})")
    # ---- el fotograma de la foto, dentro del video
    c.eval("showVideoAt(P.it)"); wait(invideo + "&&P.at"); time.sleep(0.3); a = gray("poc_grab(320)")
    check('el fotograma del video en el instante de la foto es la imagen de la foto (con la rotación correcta)', mse_(a, sn) < 30 and c.eval("document.getElementById('vstat').textContent") == 'VIDEO · FOTO' and c.eval('boxAlpha()') == 1, f'MSE={mse_(a, sn):.1f}')
    # ---- cuadros: por defecto SOLO en el instante de la foto
    c.key('.'); wait("!P.at")
    check('fuera del instante de la foto los cuadros NO se muestran (modo por defecto)', c.eval('P.boxes') == 'hide' and c.eval('boxAlpha()') == 0 and c.eval('boxesActive()') is False and c.eval("document.getElementById('vboxes').value") == 'hide')
    bb_ = st()['boxes'][0]['bbox']; xb, yb = scr(bb_[0] + bb_[2] / 2, bb_[1] + bb_[3] / 2); l_before = st()['boxes'][0]['label']; 
    c.eval("sel=-1;draw()"); c.mouse('mouseMoved', xb, yb); c.mouse('mousePressed', xb, yb); c.mouse('mouseReleased', xb, yb); time.sleep(0.2)
    check('fuera de la foto los cuadros tampoco reaccionan al ratón (no se etiquetan por error)', st()['boxes'][0]['label'] == l_before and c.eval('sel') == -1, f"{l_before} -> {st()['boxes'][0]['label']}")
    c.key('f'); time.sleep(0.2); a = gray("poc_grab(320)"); b = gray("cv_gray(320)")
    check('el lienzo dibuja el fotograma del video (sin cuadros coincide con el video)', mse_(b, a) < 30, f'MSE lienzo vs video = {mse_(b, a):.1f}')
    c.key('o'); check('O: cuadros tenues fuera de la foto', c.eval('P.boxes') == 'dim' and abs(c.eval('boxAlpha()') - 0.4) < 1e-9 and c.eval('boxesActive()'))
    c.key('o'); check('O otra vez: cuadros siempre visibles', c.eval('P.boxes') == 'full' and c.eval('boxAlpha()') == 1 and c.eval("document.getElementById('vboxes').value") == 'full')
    c.key('o'); check('O otra vez: vuelve a "solo en la foto"', c.eval('P.boxes') == 'hide' and c.eval('boxAlpha()') == 0)
    c.eval("sel=0;draw()"); n_here = c.eval('state.boxes.length'); c.key('x'); time.sleep(0.3)
    check('fuera del instante de la foto no se puede borrar un cuadro que no se ve (y avisa)', c.eval('state.boxes.length') == n_here and 'solo se pueden editar' in c.eval("document.getElementById('vmsg').textContent"), c.eval("document.getElementById('vmsg').textContent"))
    c.eval("sel=-1;draw();vmsg('')")
    # ---- velocidad
    c.key('Home'); wait(invideo); time.sleep(0.3)
    check('velocidad por defecto 1x', c.eval('vid.playbackRate') == 1 and c.eval("document.getElementById('vrate').selectedOptions[0].textContent") == '1×')
    c.key(']'); check('] sube la velocidad (1 -> 1,5x) y se refleja en el reproductor y en el selector', c.eval('vid.playbackRate') == 1.5 and c.eval("document.getElementById('vrate').selectedOptions[0].textContent") == '1,5×' and c.eval("document.getElementById('vrate').value") == '1.5')
    c.key('['); c.key('['); check('[ la baja (1,5 -> 1 -> 0,75x)', c.eval('vid.playbackRate') == 0.75)
    for _ in range(8): c.key('[')
    check('el mínimo es 0,1x', c.eval('vid.playbackRate') == 0.1 and c.eval("document.getElementById('vrate').selectedOptions[0].textContent") == '0,1×')
    for _ in range(12): c.key(']')
    check('el máximo es 4x', c.eval('vid.playbackRate') == 4)
    set_input('vrate', '2', 'change'); check('el selector cambia la velocidad', c.eval('vid.playbackRate') == 2)
    c.key('Home'); wait(invideo); time.sleep(0.3); c.eval("vid.play()"); a0 = c.eval('vid.currentTime'); t0 = time.time(); time.sleep(1.2); a1 = c.eval('vid.currentTime'); dtw = time.time() - t0; c.eval("vid.pause()")
    check('a 2x el video avanza ~2 s por segundo real', 1.5 < (a1 - a0) / dtw < 2.5, f'{(a1 - a0) / dtw:.2f}x')
    set_input('vrate', '0.5', 'change'); c.key('Home'); wait(invideo); time.sleep(0.3); c.eval("vid.play()"); a0 = c.eval('vid.currentTime'); t0 = time.time(); time.sleep(1.6); a1 = c.eval('vid.currentTime'); dtw = time.time() - t0; c.eval("vid.pause()")
    check('a 0,5x el video avanza ~0,5 s por segundo real', 0.3 < (a1 - a0) / dtw < 0.75, f'{(a1 - a0) / dtw:.2f}x')
    set_input('vrate', '1', 'change')
    # ---- mientras reproduce, los cuadros no están
    c.eval("vid.play()"); wait("!P.at&&boxAlpha()===0"); time.sleep(0.2)
    check('mientras el video avanza los cuadros desaparecen', c.eval('boxAlpha()') == 0 and c.eval('vid.paused') is False)
    c.eval("vid.pause()"); c.key('f')
    # ---- ocultar / mostrar controles
    c.key('p'); time.sleep(0.4); check('el teclado sigue funcionando con el menú plegado o desplazado (P reproduce)', not c.eval('vid.paused')); c.key('p')
    # ---- fin de la ventana
    c.eval("vid.currentTime=P.end-0.5;vid.playbackRate=2;vid.play()"); okend = wait("vid.paused", 6)
    check('al llegar a t+5 s (o al fin del video) se pausa solo', okend and c.eval('vid.currentTime') <= c.eval('P.end') + 0.05, f"currentTime={c.eval('vid.currentTime'):.2f} end={c.eval('P.end'):.2f}")
    c.eval("document.getElementById('vloop').click()"); c.eval("vid.playbackRate=1; vid.currentTime=P.end-0.5; vid.play()")
    check('con "Repetir" vuelve al inicio de la ventana', wait("vid.currentTime<P.start+2&&!vid.paused", 6), f"currentTime={c.eval('vid.currentTime'):.2f}"); c.eval("document.getElementById('vloop').click()"); c.key('p')
    # ---- otra foto (otro video, rotación -180, cámara en movimiento): se vuelve a ver la imagen, sin reproducir
    c.key('r'); go(n_fake + 1)
    check('al cambiar de foto vuelve la imagen con sus cuadros y el video NO se reproduce', wait(ready) and c.eval('P.view') == 'foto' and c.eval('vid.paused') and not c.eval('useVideo()') and c.eval('boxAlpha()') == 1)
    check('la nueva ventana está centrada en la nueva foto (t-5 .. t+5)', abs(c.eval('P.start') - (f1['t'] - 5)) < 1e-6 and abs(c.eval('P.end') - (f1['t'] + 5)) < 1e-6, f"[{c.eval('P.start'):.2f}, {c.eval('P.end'):.2f}] t={f1['t']}")
    sn = snap_of(f1); c.eval("showVideoAt(P.it)"); wait(invideo + "&&P.at"); time.sleep(0.3); a = gray("poc_grab(320)")
    check('rotación -180: el fotograma del video coincide con la foto sin voltear', c.eval('P.name') == f1['video'] and mse_(a, sn) < 30, f'MSE={mse_(a, sn):.1f}')
    for _ in range(3): c.key('<')
    time.sleep(0.9); a = gray("poc_grab(320)"); b = gray("cv_gray(320)"); snr = cv2.resize(sn, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA).astype(np.float32)
    m_vid, m_jpg = mse_(b, a), mse_(b, snr)
    check('30 fotogramas antes de la foto el lienzo muestra el VIDEO (más parecido al video que a la foto)', m_vid < 30 and m_vid < 0.6 * m_jpg, f'MSE lienzo-video={m_vid:.1f} vs lienzo-foto={m_jpg:.1f}')
    c.key('r'); time.sleep(0.4)
    c.key('p'); wait("!vid.paused"); time.sleep(0.4); g1 = gray("cv_gray(160)"); time.sleep(0.6); g2 = gray("cv_gray(160)"); c.key('p')
    check('mientras reproduce, el lienzo se va actualizando con los fotogramas', mse_(g1, g2) > 1.0, f'MSE entre dos instantes = {mse_(g1, g2):.1f}')
    set_input('vseek', '500', 'input'); wait(invideo); time.sleep(0.4)
    check('la barra permite saltar a cualquier punto de la ventana (y pasa a la vista de video)', c.eval('P.view') == 'video' and abs(c.eval('vid.currentTime') - (c.eval('P.start') + 0.5 * (c.eval('P.end') - c.eval('P.start')))) < 0.2, f"currentTime={c.eval('vid.currentTime'):.2f}")
    for b_ in ('vplay', 'vm1', 'vp1', 'vsnap', 'vsave', 'vboxes'):
        r_ = json.loads(c.eval(f"JSON.stringify(document.getElementById('{b_}').getBoundingClientRect().toJSON())")); check(f'botón {b_} visible', r_['width'] > 18 and r_['height'] > 14)
    c.eval("document.getElementById('vsnap').click()"); check('«Volver a la imagen» funciona también tras mover la barra', wait("P.view==='foto'&&!useVideo()"))
    # ---- guardar instantáneas a partir del video
    # las respuestas de error del servidor de instantáneas se prueban en backend/tests/test_video.py
    n_before = c.eval('frames.length'); cur_before = c.eval('cur')
    c.key('i'); time.sleep(1.0)
    check('guardar desde la imagen (el fotograma de la foto original) avisa que ya existe y no duplica', c.eval('frames.length') == n_before and 'ya es la imagen' in c.eval("document.getElementById('vmsg').textContent"), c.eval("document.getElementById('vmsg').textContent"))
    c.key('<'); wait(invideo); time.sleep(0.8); t_shown = c.eval('P.pts[pidx(P.media)]'); g_vid = gray("poc_grab(320)")
    c.key('p'); wait("!vid.paused"); c.eval("document.getElementById('vsave').click()")
    wait(f"frames.length=={n_before + 1}", 20)
    check('Guardar instantánea agrega una imagen nueva al conjunto (y pausa el video)', c.eval('frames.length') == n_before + 1 and c.eval('vid.paused'))
    newf = dbframe(c.eval(f"frames[{cur_before}+1].id"))
    check('la imagen nueva queda justo después de la foto de origen y no se cambia de foto', c.eval('cur') == cur_before and newf['stratum'] == 'manual' and newf['origin'] == 'reproductor' and newf['video'] == f1['video'])
    check('se guarda el instante exacto del fotograma (t) y su índice', newf['t_src'] == 'pts' and newf['frame'] >= 0 and abs(newf['t'] - (c.eval('P.pts[pidx(P.media)]'))) < 0.35, f"t={newf['t']}")
    jpg = cv2.imread(str(DATA / newf['image']))
    check('el archivo existe, con el tamaño de las otras fotos', jpg is not None and jpg.shape[1] == f1['w'] and jpg.shape[0] == f1['h'], str(None if jpg is None else jpg.shape))
    gj = cv2.resize(cv2.cvtColor(jpg, cv2.COLOR_BGR2GRAY), (g_vid.shape[1], g_vid.shape[0]), interpolation=cv2.INTER_AREA).astype(np.float32)
    check('la imagen guardada es el fotograma que se veía (misma orientación)', abs(newf['t'] - t_shown) < 0.002 and mse_(g_vid, gj) < 30, f"t guardado {newf['t']} vs t visto {t_shown}; MSE={mse_(g_vid, gj):.1f}")
    check('la base de datos incluye la nueva, con cajas preanotadas por el detector del proyecto', len(api.get(f"/api/projects/{PID}/frames?limit=500")) == n_before + 1 and newf['n_proposals'] > 0, f"{newf['n_proposals']} cajas")
    c.eval("document.getElementById('vsave').click()"); time.sleep(1.0)
    check('guardar dos veces el mismo fotograma no duplica', c.eval('frames.length') == n_before + 1 and 'ya es la imagen' in c.eval("document.getElementById('vmsg').textContent"))
    set_input('vseek', '700', 'input'); wait(invideo); time.sleep(0.8)
    t_slider = c.eval('P.pts[pidx(P.media)]'); c.eval("document.getElementById('vsave').click()"); wait(f"frames.length=={n_before + 2}", 20)
    n2 = dbframe(c.eval(f"frames[{cur_before}+2].id")); n1_ = dbframe(c.eval(f"frames[{cur_before}+1].id"))
    check('moviendo la barra y guardando se obtiene otra imagen; el orden queda: foto de origen, 1.ª instantánea, 2.ª instantánea', c.eval('frames.length') == n_before + 2 and abs(n2['t'] - t_slider) < 0.002 and n2['frame'] != newf['frame'] and n1_['frame'] == newf['frame'], f"t={n2['t']} slider={t_slider}")
    check('la miniatura de la imagen nueva se genera', api.status(f"/api/frames/{n2['id']}/thumb") == 200 and 'tomada del video' in c.eval("document.getElementById('strip').innerHTML"))
    c.eval("vmsg('')")
    go(cur_before + 2)
    check('se puede ir a la imagen nueva: se ve su imagen y su ventana de video queda centrada en ella', wait(ready) and c.eval('P.view') == 'foto' and abs(c.eval('P.t') - n2['t']) < 1e-6 and abs(c.eval('P.start') - (n2['t'] - 5)) < 1e-6, f"P.t={c.eval('P.t')}")
    c.eval("showVideoAt(P.it)"); wait(invideo + "&&P.at"); time.sleep(0.3); aa = gray("poc_grab(320)"); sj = cv2.resize(cv2.cvtColor(cv2.imread(str(DATA / n2['image'])), cv2.COLOR_BGR2GRAY), (aa.shape[1], aa.shape[0]), interpolation=cv2.INTER_AREA).astype(np.float32)
    check('en la imagen nueva, el fotograma "FOTO" del video coincide con su imagen', mse_(aa, sj) < 30 and c.eval('boxAlpha()') == 1, f'MSE={mse_(aa, sj):.1f}')
    c.key('r'); go(cur_before); wait(ready)
    # errores del servidor de instantáneas: backend/tests/test_video.py
    # ---- foto sin video original
    go(0)
    check('foto sin video: aviso claro y se sigue mostrando la foto', wait("!document.getElementById('vmsg').hidden") and 'No se encontró' in c.eval("document.getElementById('vmsg').textContent") and not c.eval('useVideo()'), c.eval("document.getElementById('vmsg').textContent"))
    go(n_fake + 1); wait(ready)
    c.key('r'); time.sleep(0.5)
    check('al volver a la imagen (R) el video queda detenido y el lienzo ya no lo usa', c.eval("vid.paused&&!useVideo()&&document.getElementById('vstat').textContent.startsWith('IMAGEN')"))
    time.sleep(0.3); b = gray("cv_gray(320)"); sn2 = cv2.resize(snap_of(f1), (b.shape[1], b.shape[0]), interpolation=cv2.INTER_AREA).astype(np.float32)
    check('al volver, el lienzo muestra la foto', mse_(b, sn2) < 60, f'MSE lienzo-foto={mse_(b, sn2):.1f}')
    dm = json.loads(c.eval("JSON.stringify({w:cv.width,h:cv.height,r:cv.getBoundingClientRect().toJSON(),d:window.devicePixelRatio||1})"))
    check('el lienzo sigue sincronizado (bitmap = pantalla)', abs(dm['w'] / dm['d'] - dm['r']['width']) < 1.5 and abs(dm['h'] / dm['d'] - dm['r']['height']) < 1.5, f"{dm['w']}x{dm['h']} vs {dm['r']['width']:.0f}x{dm['r']['height']:.0f}")
    # --- menú lateral: herramientas de foto
    c.eval("document.getElementById('bbox').click()")
    check('el botón «Caja» del menú arma el dibujo y el indicador del menú lo muestra', c.eval('drawMode') and 'on' in c.eval("document.getElementById('bbox').className").split() and not 'on' in c.eval("document.getElementById('tool-select').className").split())
    c.key('Escape'); check('Esc cancela el dibujo y vuelve la herramienta de selección', not c.eval('drawMode') and 'on' in c.eval("document.getElementById('tool-select').className").split() and 'on' not in c.eval("document.getElementById('bbox').className").split())
    # herramienta «mano»: arrastrar en una caja mueve la imagen, no la caja
    c.eval("document.getElementById('tool-pan').click()"); time.sleep(0.2)
    g_ = st()['boxes'][0]['bbox']; v_ = st()['view']['x']; x_, y_ = scr(g_[0] + g_[2] / 2, g_[1] + g_[3] / 2)
    c.drag(x_, y_, x_ + 40, y_ + 10)
    check('herramienta «mano»: arrastrar sobre una caja mueve la IMAGEN y no cambia la caja', abs(st()['view']['x'] - (v_ + 40)) < 1 and st()['boxes'][0]['bbox'] == g_)
    c.key('v')
    check('la tecla V vuelve a la herramienta de selección y H a la mano', 'on' in c.eval("document.getElementById('tool-select').className").split() and (c.key('h') or True) and 'on' in c.eval("document.getElementById('tool-pan').className").split())
    c.key('v')
    c.eval("document.getElementById('legendbtn').getAttribute('aria-expanded')==='true'||document.getElementById('legendbtn').click()"); time.sleep(0.3); reload()
    check('«Leyenda y atajos» recuerda si estaba abierta al recargar y ya no hay tarjetas plegables', c.eval("document.getElementById('legendbtn').getAttribute('aria-expanded')") == 'true' and c.eval("!!document.getElementById('s-leyenda')") and c.eval("document.querySelectorAll('#side details').length") == 0)
    c.eval("document.getElementById('legendbtn').click()")
    check('al cerrarla se oculta el detalle', wait("!document.getElementById('s-leyenda')", 3))
    # seguridad y rangos del servidor: backend/tests/test_video.py
    print('\nRESUMEN: %d/%d OK' % (sum(res.values()), len(res)))
finally:
    c.close()
    srv.terminate()
    shutil.rmtree(DATA, ignore_errors=True)
    shutil.rmtree(c.prof, ignore_errors=True)
    with admin.connect() as cn:
        cn.execute(text("DROP DATABASE IF EXISTS labelhub_e2e WITH (FORCE)"))
sys.exit(0 if all(res.values()) else 1)
