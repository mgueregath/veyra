import { useState } from "react";
import { api } from "../api/client";
import Icon from "../components/Icon";
import Logo, { VeyraMark } from "../components/Logo";

/** Cajas reales de la imagen de muestra (marcadas por una persona), en píxeles de la imagen de 880×380. */
const SAMPLE = { w: 880, h: 380, boxes: [[639, 298, 53, 49], [829, 36, 47, 56]] };

export default function Login({ onDone }: { onDone: () => void }) {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [show, setShow] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try { await api.login(u, p); onDone(); } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="loginpage">
      <section className="login-hero" aria-hidden>
        <span className="corner tl" /><span className="corner tr" /><span className="corner bl" />
        <Logo size={56} light />
        <h2>Cada imagen cuenta<i>.</i></h2>
        <p>Anota, revisa y entrena modelos en un mismo espacio.</p>
        <div className="login-card">
          <div className="lc-bar"><span><Icon name="image" size={18} /> Revisión de imágenes</span><span className="lc-tools"><i className="on"><Icon name="square" size={15} /></i><i><Icon name="plus" size={15} /></i><i><Icon name="tag" size={15} /></i><b /><i><Icon name="more" size={15} /></i></span></div>
          <div className="lc-img">
            <img src="/login/sample.jpg" alt="" />
            <svg viewBox={`0 0 ${SAMPLE.w} ${SAMPLE.h}`} preserveAspectRatio="none">
              {SAMPLE.boxes.map(([x, y, w, h], i) => <g key={i}><rect x={x} y={y} width={w} height={h} fill="none" stroke="#22b573" strokeWidth={3} /><rect x={Math.min(x, SAMPLE.w - 64)} y={y - 24} width={64} height={24} fill="#22b573" /><text x={Math.min(x, SAMPLE.w - 64) + 8} y={y - 7} fontSize={16} fill="#fff" fontWeight={600}>Piojo</text></g>)}
            </svg>
            <span className="lc-ok"><Icon name="checkCircle" size={16} /> Anotación revisada</span>
          </div>
          <div className="lc-thumbs">{[0, 1, 2, 3, 4].map((i) => <img key={i} className={i === 1 ? "on" : ""} src={`/login/t${i}.jpg`} alt="" />)}</div>
        </div>
        <ul className="login-tags"><li>Anotación precisa</li><li>Revisión en equipo</li><li>Modelos de visión</li></ul>
      </section>
      <section className="login-side">
        <form className="login" onSubmit={submit}>
          <span className="login-badge"><VeyraMark size={50} /></span>
          <small className="login-kicker">Tu espacio de trabajo</small>
          <h1>Inicia sesión</h1>
          <p className="login-sub">Accede a tus proyectos y continúa tu trabajo.</p>
          <label className="lf">Usuario
            <span className="lfield"><Icon name="user" size={19} /><input title="Tu nombre de usuario" placeholder="Tu nombre de usuario" autoComplete="username" value={u} onChange={(e) => setU(e.target.value)} autoFocus /></span></label>
          <label className="lf">Contraseña
            <span className="lfield"><Icon name="lock" size={19} /><input title="Tu contraseña" placeholder="Introduce tu contraseña" type={show ? "text" : "password"} autoComplete="current-password" value={p} onChange={(e) => setP(e.target.value)} />
              <button type="button" className="eye" aria-label={show ? "Ocultar contraseña" : "Mostrar contraseña"} title={show ? "Ocultar contraseña" : "Mostrar contraseña"} onClick={() => setShow(!show)}><Icon name={show ? "eyeOff" : "eye"} size={19} /></button></span></label>
          {err && <p className="err" id="login-err">{err}</p>}
          <button type="submit" className="primary big" disabled={!u || !p || busy} title="Entrar con este usuario y contraseña">Entrar a Veyra <Icon name="arrowRight" size={20} /></button>
          <hr />
          <p className="login-help">¿Necesitas acceso? Contacta al administrador.</p>
        </form>
        <footer className="login-foot">Veyra · Anotación y visión artificial</footer>
      </section>
    </div>
  );
}
