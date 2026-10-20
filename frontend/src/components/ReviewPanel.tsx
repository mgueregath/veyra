import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type ClassDef, type FrameDetail } from "../api/client";
import type { BoxEditor } from "../editor/BoxEditor";

const nb = { onMouseDown: (e: React.MouseEvent) => e.preventDefault() };

/** Revisión: lo que hizo cada persona con esta imagen, para compararlo en el lienzo y fijar cuál es la final. */
export default function ReviewPanel({ frame, classes, editor, pid, onChanged }: {
  frame: FrameDetail; classes: ClassDef[]; editor: BoxEditor | null; pid: number; onChanged: () => void;
}) {
  const qc = useQueryClient();
  const [shown, setShown] = useState<number | null>(null);
  const anns = useQuery({ queryKey: ["annotations", frame.id, frame.status, frame.n_reviewed], queryFn: () => api.annotations(frame.id) });
  const users = anns.data ?? [];

  useEffect(() => { setShown(null); }, [frame.id]);
  useEffect(() => {
    const a = users.find((u) => u.user_id === shown);
    editor?.setOverlay(a ? { title: a.user, boxes: a.boxes.filter((b) => !b.deleted && b.label).map((b) => ({ bbox: b.bbox, label: b.label })) } : null);
    return () => editor?.setOverlay(null);
  }, [shown, users, editor]);

  const done = async () => {
    await Promise.all([qc.invalidateQueries({ queryKey: ["annotations"] }), qc.invalidateQueries({ queryKey: ["frames", pid] }), qc.invalidateQueries({ queryKey: ["quality", pid] })]);
    onChanged();
  };
  const positive = classes.filter((c) => !c.negative);
  const count = (b: typeof users[number]["boxes"], id: string) => b.filter((x) => !x.deleted && x.label === id).length;

  return (
    <section className="blk" id="s-revision">
      <h3 title="Para revisores: lo que hizo cada persona con esta imagen, para compararlo y decidir cuál es la final">Revisión <span className="sx">{frame.n_reviewed} revisada{frame.n_reviewed === 1 ? "" : "s"}</span></h3>
      {frame.review && (
        <div className="hint" id="rv-final">
          Final: <b>{frame.review.final_user}</b> (decidió {frame.review.reviewer}){frame.review.note ? ` · ${frame.review.note}` : ""}
          <button className="ghost" id="rv-undo" title="Quita la decisión del revisor: la imagen vuelve a su estado anterior (acuerdo o desacuerdo)" onClick={async () => { await api.undoReview(frame.id); await done(); }} {...nb}>Quitar decisión</button>
        </div>
      )}
      {!users.length && <p className="hint">Nadie ha trabajado esta imagen todavía.</p>}
      {users.map((u) => (
        <div className="rvrow" key={u.user_id} data-user={u.user}>
          <span className="rvname">{u.user}{u.reviewed ? " ✓" : " …"}</span>
          <span className="rvcnt">{positive.map((c) => `${count(u.boxes, c.id)} ${c.name.toLowerCase()}`).join(" · ")}</span>
          <button className={shown === u.user_id ? "on" : ""} title="Superponer sus cajas sobre la imagen" onClick={() => setShown(shown === u.user_id ? null : u.user_id)} {...nb}>Ver</button>
          <button title="Dar su versión por final" onClick={async () => { await api.review(frame.id, u.user_id); await done(); }} {...nb}>Final</button>
        </div>
      ))}
    </section>
  );
}
