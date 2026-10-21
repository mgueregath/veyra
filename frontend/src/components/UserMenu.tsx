import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import SshKeysModal from "./SshKeysModal";

/** Todo lo de la sesión y la configuración de la persona, en un desplegable: cambiar de proyecto, contraseña y salir. */
export default function UserMenu({ user, role, projectName, onProjects, onLogout }: {
  user: string; role?: string; projectName?: string; onProjects?: () => void; onLogout: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [pwd, setPwd] = useState(false);
  const [keys, setKeys] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("mousedown", away);
    window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("mousedown", away); window.removeEventListener("keydown", esc); };
  }, [open]);
  const ROLE: Record<string, string> = { admin: "administrador", reviewer: "revisor", labeler: "etiquetador", viewer: "lector" };
  const item = (id: string, text: string, title: string, fn: () => void) => (
    <button role="menuitem" id={id} title={title} onClick={() => { setOpen(false); fn(); }}>{text}</button>
  );
  return (
    <div className="usermenu" ref={ref}>
      <button id="usermenu" className="ghost umbtn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)} onMouseDown={(e) => e.preventDefault()}
        title="Tu sesión y tu configuración: cambiar de proyecto, cambiar tu contraseña y salir">
        <span className="avatar">{user.slice(0, 1).toUpperCase()}</span><span className="uname">{user}</span><span aria-hidden>▾</span>
      </button>
      {open && (
        <div className="umpop" id="umpop" role="menu">
          <div className="umhead" title="Con quién has entrado y qué puedes hacer en este proyecto">
            <b>{user}</b>
            {role && <span className="mut">{ROLE[role] ?? role}{projectName ? ` · ${projectName}` : ""}</span>}
          </div>
          {onProjects && item("um-projects", "Cambiar de proyecto", "Volver a la lista de proyectos para abrir otro", onProjects)}
          {item("um-keys", "Mis llaves SSH", "Llaves públicas para entrar con VS Code a los espacios de trabajo de tus proyectos", () => setKeys(true))}
          {item("um-password", "Cambiar mi contraseña", "Cambiar la contraseña con la que entras (mínimo 8 caracteres)", () => setPwd(true))}
          {item("um-logout", "Salir", "Cerrar la sesión en este navegador", onLogout)}
        </div>
      )}
      {pwd && <PasswordModal onClose={() => setPwd(false)} />}
      {keys && <SshKeysModal onClose={() => setKeys(false)} />}
    </div>
  );
}

function PasswordModal({ onClose }: { onClose: () => void }) {
  const [old, setOld] = useState("");
  const [nw, setNw] = useState("");
  const [msg, setMsg] = useState<{ t: string; ok: boolean } | null>(null);
  const go = async () => {
    try { await api.changePassword(old, nw); setMsg({ t: "Tu contraseña se cambió.", ok: true }); setOld(""); setNw(""); }
    catch (e) { setMsg({ t: (e as Error).message, ok: false }); }
  };
  return (
    <div id="pwmodal" className="modalback" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modalbox" role="dialog" aria-label="Cambiar mi contraseña">
        <div className="hhead"><b>Cambiar mi contraseña</b><button id="pwm-x" title="Cerrar sin cambiar nada (Esc)" onClick={onClose}>×</button></div>
        <label>Contraseña actual<input type="password" id="pwm-old" title="Tu contraseña de ahora" value={old} onChange={(e) => setOld(e.target.value)} autoFocus /></label>
        <label>Contraseña nueva<input type="password" id="pwm-new" title="Tu contraseña nueva (mínimo 8 caracteres)" value={nw} onChange={(e) => setNw(e.target.value)} /></label>
        {msg && <p className={msg.ok ? "okmsg" : "err"} id="pwm-msg">{msg.t}</p>}
        <button className="primary" id="pwm-go" disabled={!old || nw.length < 8} title="Guardar la contraseña nueva" onClick={go}>Cambiar contraseña</button>
      </div>
    </div>
  );
}
