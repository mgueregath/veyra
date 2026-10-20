"""Humo en Chrome real contra un servidor ya levantado: login -> proyectos -> visor con imagen y cajas."""
import base64, socket, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cdp import Chrome  # noqa: E402

url, user, pw, shot = sys.argv[1:5]
with socket.socket() as k:
    k.bind(("127.0.0.1", 0)); port = k.getsockname()[1]
c = Chrome(url, port=port, size=(1500, 850), prof=tempfile.mkdtemp(prefix="lh_prof_"))
ok = []
def check(name, cond): ok.append(cond); print("OK   " if cond else "FALLA", name)
def wait(js, t=10):
    for _ in range(int(t * 10)):
        if c.eval(js): return True
        time.sleep(0.1)
    return False
try:
    check("aparece el formulario de entrada", wait("!!document.querySelector('form.login')"))
    inputs = ["form.login input[autocomplete=username]", "input[type=password]"]
    for sel, val in zip(inputs, (user, pw)):
        c.eval(f"document.querySelector('{sel}').focus()"); c.call("Input.insertText", text=val)
    c.eval("document.querySelector('form.login button[type=submit]').click()")
    check("tras entrar se listan los proyectos", wait("!!document.querySelector('.card')"))
    check("el proyecto muestra 1236 imágenes", "1236" in c.eval("document.querySelector('.card').textContent"))
    c.eval("document.querySelector('.card').click()")
    check("el visor muestra la imagen del frame", wait("!!document.querySelector('.stage svg image')"))
    check("la imagen se descarga bien (200)", wait("(()=>{const i=new Image();i.src=document.querySelector('.stage svg image').getAttribute('href');window.__ok=0;i.onload=()=>window.__ok=i.naturalWidth;return false})()", 0.2) or wait("window.__ok>0", 10))
    check("hay cajas dibujadas", c.eval("document.querySelectorAll('.stage svg rect').length") > 0)
    first = c.eval("document.querySelector('aside').textContent")
    c.key("ArrowRight"); time.sleep(0.8)
    check("→ avanza a la siguiente imagen", c.eval("document.querySelector('aside').textContent") != first)
    time.sleep(0.5)
    open(shot, "wb").write(base64.b64decode(c.call("Page.captureScreenshot", format="png")["data"]))
finally:
    c.close()
print(f"RESUMEN: {sum(ok)}/{len(ok)} OK"); sys.exit(0 if all(ok) else 1)
