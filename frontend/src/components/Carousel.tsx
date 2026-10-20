import { useEffect, useRef } from "react";
import { api, type FrameFilter, type FrameItem } from "../api/client";
import Icon from "./Icon";

const nb = { onMouseDown: (e: React.MouseEvent) => e.preventDefault() };
export const FILTERS: { id: FrameFilter; label: string }[] = [
  { id: "all", label: "Todas" },
  { id: "mine_pending", label: "Sin revisar (yo)" },
  { id: "assigned_pending", label: "Mi cola" },
  { id: "needs_review", label: "Para revisar" },
  { id: "disagreement", label: "Con desacuerdo" },
  { id: "final", label: "Finales" },
  { id: "pending", label: "Nadie la revisó" },
];

export const FILTERS_COLLAB: { id: FrameFilter; label: string }[] = [
  { id: "all", label: "Todas" },
  { id: "mine_pending", label: "Sin revisar" },
  { id: "assigned_pending", label: "Mi cola" },
  { id: "final", label: "Revisadas" },
];

/** Tira inferior: filtro, ‹ miniaturas ›, e índice «N / total» (escribe un número y Enter para ir a esa imagen). */
export default function Carousel({ frames, curId, filter, setFilter, go, onPrev, onNext, gotoVal, setGotoVal, onGoto, reviewed, total, collab }: {
  frames: FrameItem[]; curId: number | null; filter: FrameFilter; setFilter: (v: FrameFilter) => void; go: (id: number) => void;
  onPrev: () => void; onNext: () => void; gotoVal: string; setGotoVal: (v: string) => void; onGoto: () => void; reviewed: number; total: number; collab: boolean;
}) {
  const strip = useRef<HTMLDivElement>(null);
  const pos = Math.max(0, frames.findIndex((f) => f.id === curId));
  const lo = Math.max(0, pos - 40), hi = Math.min(frames.length, pos + 60); // ventana: con miles de imágenes no se dibujan todas
  useEffect(() => { strip.current?.querySelector(".cur")?.scrollIntoView({ inline: "center", block: "nearest", behavior: "instant" as ScrollBehavior }); }, [curId, filter, frames.length]);
  return (
    <footer id="stripwrap" className="strip2">
      <div className="stripprog" id="pbar" title={`Avance: ${reviewed} de ${total} imágenes revisadas por ti`}><div id="bar" style={{ width: `${total ? (100 * reviewed) / total : 0}%` }} /></div>
      <select id="flt" className="fsel" title="Qué imágenes mostrar en la tira y al navegar: todas, las que te faltan, tu cola, las que necesitan revisión, con desacuerdo o finales" value={filter} onChange={(e) => { setFilter(e.target.value as FrameFilter); e.target.blur(); }}>
        {(collab ? FILTERS_COLLAB : FILTERS).map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
      </select>
      <button id="prev" className="navbtn" title="Imagen anterior (A o ←). No marca la actual como revisada" aria-label="Anterior" onClick={onPrev} {...nb}><Icon name="left" size={17} /></button>
      <div id="strip" ref={strip}>
        {lo > 0 && <div className="more">‹ {lo}</div>}
        {frames.slice(lo, hi).map((f, k) => {
          const i = lo + k;
          const badge = f.status === "disagreement" ? "dis" : f.reviewed ? "ok" : f.status === "final" ? "fin" : "";
          return (
            <div key={f.id} className={"th" + (f.id === curId ? " cur" : "") + (f.reviewed ? " rev" : "")} onClick={() => go(f.id)}
              title={`Imagen ${i + 1} · ${f.video.slice(4)} · frame ${f.frame_index}${f.stratum ? " · " + f.stratum : ""}${f.origin === "reproductor" ? " · tomada del video" : ""}`}>
              <img loading="lazy" src={api.thumbUrl(f.id)} alt="" />
              <span className="i">{i + 1}</span>
              {badge && <span className={"ck ck-" + badge} title={badge === "dis" ? "Desacuerdo entre revisiones" : badge === "ok" ? "Revisada por ti" : "Versión final"}>{badge === "fin" ? "★" : "✓"}</span>}
            </div>
          );
        })}
        {hi < frames.length && <div className="more">{frames.length - hi} ›</div>}
      </div>
      <button id="next" className="navbtn" title="Imagen siguiente (D o →). No marca la actual como revisada" aria-label="Siguiente" onClick={onNext} {...nb}><Icon name="right" size={17} /></button>
      <label className="idx" title="Imagen actual y total. Escribe un número y pulsa Enter para ir directamente a esa imagen (atajo G); si te pasas va a la última">
        <input id="goto" type="number" min={1} value={gotoVal} aria-label="Número de imagen actual" onChange={(e) => setGotoVal(e.target.value)} onMouseDown={(e) => e.stopPropagation()}
          onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") { onGoto(); (e.target as HTMLElement).blur(); } else if (e.key === "Escape") (e.target as HTMLElement).blur(); }} />
        <span className="idxtot">/ <span id="ntotal">{frames.length}</span></span>
      </label>
    </footer>
  );
}
