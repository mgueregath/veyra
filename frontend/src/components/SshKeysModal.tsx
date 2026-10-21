import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api/client";

/** Llaves públicas SSH de la persona: dan acceso a los espacios de trabajo de los proyectos que administra. */
export default function SshKeysModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const keys = useQuery({ queryKey: ["sshkeys"], queryFn: api.sshKeys });
  const [name, setName] = useState("");
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  const add = useMutation({
    mutationFn: () => api.addSshKey(name.trim(), text.trim()),
    onSuccess: () => { setName(""); setText(""); setErr(""); void qc.invalidateQueries({ queryKey: ["sshkeys"] }); void qc.invalidateQueries({ queryKey: ["workspace"] }); },
    onError: (e) => setErr((e as Error).message),
  });
  const del = useMutation({ mutationFn: (id: number) => api.deleteSshKey(id), onSuccess: () => { void qc.invalidateQueries({ queryKey: ["sshkeys"] }); void qc.invalidateQueries({ queryKey: ["workspace"] }); } });
  return (
    <div id="sshkeys-modal" className="modalback" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modalbox wide" role="dialog" aria-label="Mis llaves SSH">
        <div className="hhead"><b>Mis llaves SSH</b><button id="sk-x" title="Cerrar (Esc)" onClick={onClose}>×</button></div>
        <p className="mut">Sirven para entrar con VS Code a los espacios de trabajo de los proyectos que administras. Pega aquí tu llave <b>pública</b> (el archivo que termina en <code>.pub</code>); la privada nunca sale de tu equipo.</p>
        <table className="tbl" id="key-list">
          <tbody>{(keys.data ?? []).map((k) => (
            <tr key={k.id} data-key={k.name}><td><b>{k.name}</b><br /><small className="mut">{k.type} · {k.fingerprint}</small></td><td><button className="btn-outline" onClick={() => del.mutate(k.id)}>Quitar</button></td></tr>
          ))}
          {!keys.data?.length && <tr><td className="emptyrow">Aún no tienes llaves registradas.</td></tr>}</tbody>
        </table>
        <label className="fld">Nombre<input id="key-name" type="text" maxLength={64} placeholder="p. ej. portátil de la oficina" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="fld">Llave pública<textarea id="key-text" rows={3} placeholder="ssh-ed25519 AAAA… usuario@equipo" value={text} onChange={(e) => setText(e.target.value)} /></label>
        <small className="mut">Si no tienes una: <code>ssh-keygen -t ed25519</code> y luego <code>cat ~/.ssh/id_ed25519.pub</code>.</small>
        {err && <p className="err" id="key-err">{err}</p>}
        <div className="formrow end"><button className="primary" id="key-add" disabled={!name.trim() || !text.trim() || add.isPending} onClick={() => add.mutate()}>Agregar llave</button></div>
      </div>
    </div>
  );
}
