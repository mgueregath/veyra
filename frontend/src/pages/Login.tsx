import { useState } from "react";
import { api } from "../api/client";

export default function Login({ onDone }: { onDone: () => void }) {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try { await api.login(u, p); onDone(); } catch (x) { setErr((x as Error).message); }
  };
  return (
    <form className="login" onSubmit={submit}>
      <h1>LabelHub</h1>
      <input title="Tu nombre de usuario" placeholder="Usuario" value={u} onChange={(e) => setU(e.target.value)} autoFocus />
      <input title="Tu contraseña" placeholder="Contraseña" type="password" value={p} onChange={(e) => setP(e.target.value)} />
      <button className="primary" disabled={!u || !p} title="Entrar con este usuario y contraseña">Entrar</button>
      {err && <p className="err">{err}</p>}
    </form>
  );
}
