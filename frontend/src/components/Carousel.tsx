import { useEffect, useRef } from "react";
import { api, type ClassDef, type FrameFilter, type FrameItem } from "../api/client";

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
const STATUS_MARK: Record<string, string> = { single: "1", agreed: "✓✓", disagreement: "≠", final: "★" };

export default function Carousel({ frames, curId, classes, filter, setFilter, go }: {
  frames: FrameItem[]; curId: number | null; classes: ClassDef[]; filter: FrameFilter; setFilter: (v: FrameFilter) => void; go: (id: number) => void;
}) {
  const strip = useRef<HTMLDivElement>(null);
  const idx = frames.map((f, i) => ({ f, i }));
  const pos = Math.max(0, idx.findIndex(({ f }) => f.id === curId));
  const lo = Math.max(0, pos - 40), hi = Math.min(idx.length, pos + 60); // ventana: con miles de imágenes no se dibujan todas
  useEffect(() => { strip.current?.querySelector(".cur")?.scrollIntoView({ inline: "center", block: "nearest", behavior: "instant" as ScrollBehavior }); }, [curId, filter, frames.length]);
  const positive = classes.filter((c) => !c.negative);
  return (
    <footer id="stripwrap">
      <button className="sbtn ghost" id="sl" title="Desplazar el carrusel hacia la izquierda" onClick={() => strip.current?.scrollBy({ left: -480 })} {...nb}>‹</button>
      <div id="strip" ref={strip}>
        {lo > 0 && <div className="more">‹ {lo}<br />antes</div>}
        {idx.slice(lo, hi).map(({ f, i }) => (
          <div key={f.id} className={"th" + (f.id === curId ? " cur" : "") + (f.reviewed ? " rev" : "")} onClick={() => go(f.id)}
            title={`${f.video.slice(4)} · frame ${f.frame_index}${f.stratum ? " · " + f.stratum : ""}`}>
            <img loading="lazy" src={api.thumbUrl(f.id)} alt="" />
            <span className="i">{i + 1}</span>
            {f.reviewed && <span className="ck">✓</span>}
            {STATUS_MARK[f.status] && <span className={"st st-" + f.status} title={f.status}>{STATUS_MARK[f.status]}</span>}
            {f.origin === "reproductor" && <span className="vb" title="Tomada del video">▶ video</span>}
            <span className="c">
              {positive.map((c) => (f.n_labeled[c.id] ? <span key={c.id} style={{ background: c.color }}>{f.n_labeled[c.id]} {c.name.toLowerCase()}{f.n_labeled[c.id] > 1 ? "s" : ""}</span> : null))}
            </span>
          </div>
        ))}
        {hi < idx.length && <div className="more">{idx.length - hi}<br />más ›</div>}
      </div>
      <button className="sbtn ghost" id="sr" title="Desplazar el carrusel hacia la derecha" onClick={() => strip.current?.scrollBy({ left: 480 })} {...nb}>›</button>
      <label className="filter">Mostrar{" "}
        <select id="flt" title="Qué imágenes mostrar en el carrusel y al navegar: todas, las que te faltan, tu cola, las que necesitan revisión, con desacuerdo o finales" value={filter} onChange={(e) => { setFilter(e.target.value as FrameFilter); e.target.blur(); }}>
          {FILTERS.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
        </select>
      </label>
    </footer>
  );
}
