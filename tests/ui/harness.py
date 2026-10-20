"""Entorno común de las pruebas en Chrome real: base PostgreSQL propia, servidor en un puerto libre y navegador.

No toca los datos de trabajo. Cada prueba crea su base (`labelhub_<nombre>`), su carpeta de imágenes y la borra al terminar.
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
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(ROOT / "backend"))
from cdp import Chrome  # noqa: E402

BASE = os.environ.get("LABELHUB_TEST_ADMIN_URL", "postgresql+psycopg2://labelhub:labelhub_dev@127.0.0.1:55433/postgres")


def free_port():
    with socket.socket() as k:
        k.bind(("127.0.0.1", 0))
        return k.getsockname()[1]


class Api:
    """Cliente HTTP con la sesión de un usuario (para preparar datos y comprobar lo guardado)."""

    def __init__(self, port, user, pw):
        self.port = port
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.call("POST", "/api/auth/login", {"username": user, "password": pw})

    def call(self, method, path, body=None):
        rq = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method, headers={"content-type": "application/json"},
                                    data=json.dumps(body).encode() if body is not None else None)
        try:
            r = self.op.open(rq, timeout=60)
            return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            detail = e.read()[:1500].decode("utf-8", "replace")
            raise RuntimeError(f"{method} {path} -> {e.code} {detail}") from None

    def get(self, path):
        return self.call("GET", path)


class Env:
    def __init__(self, name):
        from sqlalchemy import create_engine, text

        self.name = name
        self.port = free_port()
        self.data = Path(tempfile.mkdtemp(prefix=f"lh_{name}_"))
        self.db_name = f"labelhub_{name}"
        self.url = BASE.rsplit("/", 1)[0] + "/" + self.db_name
        os.environ.update(LABELHUB_DATABASE_URL=self.url, LABELHUB_MEDIA_ROOT=str(self.data), LABELHUB_SECRET_KEY="e2e",
                          LABELHUB_EXPORT_ROOT=str(self.data / "exports"))
        self.admin = create_engine(BASE, isolation_level="AUTOCOMMIT")
        with self.admin.connect() as cn:
            cn.execute(text(f"DROP DATABASE IF EXISTS {self.db_name} WITH (FORCE)"))
            cn.execute(text(f"CREATE DATABASE {self.db_name}"))
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "backend", check=True)
        self.srv = None
        self.chrome = None

    def start_server(self):
        self.srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.port)],
                                    cwd=ROOT / "backend", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(80):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/health", timeout=1).read()
                return
            except Exception:
                time.sleep(0.25)
        raise RuntimeError("el servidor no arrancó")

    def api(self, user, pw=None):
        return Api(self.port, user, pw or "pw-" + user)

    def browser(self, user, pw=None, size=(1700, 950)):
        """Abre Chrome y entra por el formulario real."""
        c = Chrome(f"http://127.0.0.1:{self.port}/", port=free_port(), size=size, prof=tempfile.mkdtemp(prefix="cdp_prof_"))
        self.chrome = c
        for _ in range(100):
            if c.eval("!!document.querySelector('form.login')"):
                break
            time.sleep(0.1)
        for sel, val in (("form.login input[autocomplete=username]", user), ("input[type=password]", pw or "pw-" + user)):
            c.eval(f"document.querySelector('{sel}').focus()")
            c.call("Input.insertText", text=val)
        c.eval("document.querySelector('form.login button[type=submit]').click()")
        time.sleep(1.0)
        return c

    def close(self):
        from sqlalchemy import text

        if self.chrome:
            self.chrome.close()
            shutil.rmtree(self.chrome.prof, ignore_errors=True)
        if self.srv:
            self.srv.terminate()
        shutil.rmtree(self.data, ignore_errors=True)
        with self.admin.connect() as cn:
            cn.execute(text(f"DROP DATABASE IF EXISTS {self.db_name} WITH (FORCE)"))


def set_input(c, i, v, ev, proto="HTMLInputElement"):
    """React ignora `el.value = x` hecho a mano: hay que usar el setter nativo y avisar con un evento que burbujee."""
    c.eval(f"(()=>{{const el=document.getElementById('{i}');Object.getOwnPropertyDescriptor({proto}.prototype,'value').set.call(el,'{v}');"
           f"el.dispatchEvent(new Event('{ev}',{{bubbles:true}}))}})()")


def wait_for(c, js, tmo=15.0):
    t0 = time.time()
    while time.time() - t0 < tmo:
        try:
            if c.eval(js):
                return True
        except RuntimeError:
            pass
        time.sleep(0.1)
    return False
