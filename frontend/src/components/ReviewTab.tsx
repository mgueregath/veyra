import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type ClassDef, type FrameDetail } from "../api/client";
import type { BoxEditor } from "../editor/BoxEditor";
import Icon from "./Icon";

const nb = { onMouseDown: (e: React.MouseEvent) => e.preventDefault() };
export const initials = (name: string) => {
  const w = name.trim().split(/\s+/).filter(Boolean);
  return (w.length > 1 ? w[0][0] + w[1][0] : name.slice(0, 2)).toUpperCase();
};

/** Revisión: lo que hizo cada persona con esta imagen, para compararlo en el lienzo y fijar cuál es la final. */
export default function ReviewTab({ frame, classes, editor, pid, onChanged, onViewing }: {
  frame: FrameDetail; classes: ClassDef[]; editor: BoxEditor | null; pid: number; onChanged: () => void;
  onViewing: (v: { name: string; text: string } | null) => void;
}) {
  const qc = useQueryClient();
  const [shown, setShown] = useState<number | null>(null);
  const anns = useQuery({ queryKey: ["annotations", frame.id, frame.status, frame.n_reviewed], queryFn: () => api.annotations(frame.id) });
  const users = anns.data ?? [];
  const positive = classes.filter((c) => !c.negative);
  const count = (b: (typeof users)[number]["boxes"], id: string) => b.filter((x) => !x.deleted && x.label === id).length;
  const plural = (c: ClassDef, n: number) => `${n} ${c.name.toLowerCase()}${n === 1 ? "" : "s"}`;
  const label = (u: (typeof users)[number]) => u.display_name || u.user;

  useEffect(() => { setShown(null); }, [frame.id]);
  useEffect(() => {
    const a = users.find((u) => u.user_id === shown);
    editor?.setOverlay(a ? { title: label(a), boxes: a.boxes.filter((b) => !b.deleted && b.label).map((b) => ({ bbox: b.bbox, label: b.label })) } : null);
    onViewing(a ? { name: label(a), text: positive.map((c) => plural(c, count(a.boxes, c.id))).filter((t) => !t.startsWith("0")).join(" · ") || "sin cajas" } : null);
    return () => { editor?.setOverlay(null); onViewing(null); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown, users, editor]);

  const done = async () => {
    await Promise.all([qc.invalidateQueries({ queryKey: ["annotations"] }), qc.invalidateQueries({ queryKey: ["frames", pid] }), qc.invalidateQueries({ queryKey: ["quality", pid] })]);
    onChanged();
  };
  const dis = frame.status === "disagreement", agr = frame.status === "agreed";
  return (
    <section id="s-revision" className="sec2">
      <div className="ph-head">
        <h2 title="Para revisores: lo que hizo cada persona con esta imagen, para compararlo y decidir cuál es la final">Revisión de imagen</h2>
        <span className="pill soft" id="rv-count" title="Personas que marcaron esta imagen como revisada">{frame.n_reviewed} revisi{frame.n_reviewed === 1 ? "ón" : "ones"}</span>
      </div>
      <p className="sub">Compara las anotaciones y elige la versión final.</p>
      {dis && <div className="banner warn" id="rv-banner"><Icon name="info" size={14} /> Las revisiones no coinciden</div>}
      {agr && <div className="banner ok" id="rv-banner"><Icon name="check" size={14} /> Las revisiones coinciden</div>}
      <h4 className="cap">Versiones</h4>
      {!users.length && <p className="hint">Nadie ha trabajado esta imagen todavía.</p>}
      {users.map((u) => (
        <div className={"rvrow" + (shown === u.user_id ? " on" : "")} key={u.user_id} data-user={u.user}>
          <div className="rv-top">
            <span className="avatar big">{initials(label(u))}</span>
            <b className="rvname">{label(u)}</b>
            <span className={"rvstate" + (u.reviewed ? " ok" : "")} title={u.reviewed ? "Marcó la imagen como revisada" : "Tiene trabajo en la imagen pero no la marcó como revisada"}>{u.reviewed ? <><Icon name="check" size={12} /> Revisada</> : "… En curso"}</span>
          </div>
          <div className="rvcnt">{positive.map((c, i) => <span key={c.id}>{i ? <span className="mut"> · </span> : null}<i className="dot" style={{ background: c.color }} /> {plural(c, count(u.boxes, c.id))}</span>)}</div>
          <div className="rv-btns">
            <button className={"btn sm" + (shown === u.user_id ? " blue on" : "")} title="Superponer sus cajas sobre la imagen para compararlas con las tuyas; otro clic las oculta" onClick={() => setShown(shown === u.user_id ? null : u.user_id)} {...nb}><Icon name="eye" size={12} /> {shown === u.user_id ? "Ocultar cajas" : "Ver cajas"}</button>
            <button className="btn sm" title="Dar la versión de esta persona por final: es la que entrará en los datasets" onClick={async () => { await api.review(frame.id, u.user_id); await done(); }} {...nb}><Icon name="flag" size={12} /> Marcar como final</button>
          </div>
        </div>
      ))}
      {shown !== null && users.find((u) => u.user_id === shown) && (
        <div className="viewing" id="rv-viewing" title="Estás viendo sobre la imagen las cajas de esta persona"><i className="dot" style={{ background: "#2563eb" }} /> Viendo cajas de {label(users.find((u) => u.user_id === shown)!)}</div>
      )}
      <h4 className="cap">Decisión final</h4>
      {frame.review ? (
        <div className="final-card" id="rv-final">
          <div><Icon name="flag" size={14} /> Final: <b>{frame.review.final_user}</b> <span className="mut">(decidió {frame.review.reviewer})</span>{frame.review.note ? <div className="mut">{frame.review.note}</div> : null}</div>
          <button className="btn sm" id="rv-undo" title="Quita la decisión del revisor: la imagen vuelve a su estado anterior (acuerdo o desacuerdo)" onClick={async () => { await api.undoReview(frame.id); await done(); }} {...nb}>Quitar decisión</button>
        </div>
      ) : (
        <div className="empty2" id="rv-empty"><Icon name="doc" size={40} /><b>Sin versión final</b><span>Selecciona una revisión para cerrar esta imagen.</span></div>
      )}
    </section>
  );
}
