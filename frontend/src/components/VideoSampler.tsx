import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ProjectVideo } from "../api/client";
import Icon from "./Icon";

const clock = (t: number) => `${Math.floor(t / 60)}:${(t % 60).toFixed(2).padStart(5, "0")}`;

/** Muestreo manual: se reproduce el video completo y la persona toma las instantáneas que quiera (fotograma exacto). */
export function ManualSampler({ video, onClose }: { video: ProjectVideo; onClose: () => void }) {
  const qc = useQueryClient();
  const ref = useRef<HTMLVideoElement>(null);
  const pts = useQuery({ queryKey: ["pts", video.id], queryFn: () => api.videoPts(video.id, 0, 1e9), staleTime: Infinity });
  const [t, setT] = useState(0);
  const [taken, setTaken] = useState<{ id: number; t: number; existing: boolean }[]>([]);
  const [msg, setMsg] = useState<{ t: string; ok: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const list = pts.data?.pts ?? [];

  const nearest = useCallback((c: number) => {
    let lo = 0, hi = list.length - 1;
    while (lo < hi) { const m = (lo + hi) >> 1; if (list[m] < c) lo = m + 1; else hi = m; }
    return lo > 0 && Math.abs(list[lo - 1] - c) <= Math.abs(list[lo] - c) ? lo - 1 : lo;
  }, [list]);
  const step = (k: number) => {
    const v = ref.current;
    if (!v || !list.length) return;
    v.pause();
    const i = Math.max(0, Math.min(list.length - 1, nearest(v.currentTime) + k));
    v.currentTime = list[i] + 0.0005;
  };
  const take = useCallback(async () => {
    const v = ref.current;
    if (!v || !list.length || busy || video.first_frame_id === null) return;
    v.pause();
    const at = list[nearest(v.currentTime)];
    setBusy(true);
    try {
      const r = await api.snapshot(video.first_frame_id, at);
      setTaken((a) => [{ id: r.id, t: at, existing: r.existing }, ...a]);
      setMsg({ t: r.existing ? `El fotograma de ${clock(at)} ya era una imagen del proyecto.` : r.restored ? `Fotograma de ${clock(at)} recuperado de la papelera.` : `Instantánea de ${clock(at)} guardada.`, ok: true });
      for (const k of ["frames", "stats", "projects", "videos"]) void qc.invalidateQueries({ queryKey: [k] });
    } catch (e) { setMsg({ t: (e as Error).message, ok: false }); }
    finally { setBusy(false); }
  }, [list, busy, nearest, video.first_frame_id, qc]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "INPUT") return;
      if (e.key === "s" || e.key === "S") { e.preventDefault(); void take(); }
      else if (e.key === "," || e.key === "ArrowLeft") { e.preventDefault(); step(-1); }
      else if (e.key === "." || e.key === "ArrowRight") { e.preventDefault(); step(1); }
      else if (e.key === " ") { e.preventDefault(); const v = ref.current; if (v) void (v.paused ? v.play() : v.pause()); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <div className="panel" id="sampler">
      <div className="formrow" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>Muestreo manual · {video.name}</h2>
        <button className="btn" id="sm-close" onClick={onClose}>Cerrar</button>
      </div>
      <p className="mut">Reproduce el video y pulsa «Tomar instantánea» (o la tecla <kbd>S</kbd>) en cada instante que quieras etiquetar. Con <kbd>,</kbd> y <kbd>.</kbd> avanzas de a un fotograma; con la barra espaciadora, reproduces o pausas.</p>
      <video ref={ref} id="sm-video" src={api.videoUrl(video.id)} controls preload="auto" className="smvideo" onTimeUpdate={(e) => setT(e.currentTarget.currentTime)} onError={() => setMsg({ t: "Tu navegador no puede reproducir este formato: usa el muestreo automático o sube el video en mp4.", ok: false })} />
      <div className="formrow">
        <button className="btn" id="sm-prev" title="Fotograma anterior (,)" onClick={() => step(-1)}><Icon name="stepBack" size={16} /></button>
        <button className="primary" id="sm-take" disabled={busy || !list.length} title="Guarda el fotograma actual como imagen del proyecto (S)" onClick={() => void take()}><Icon name="camera" size={16} /> Tomar instantánea</button>
        <button className="btn" id="sm-next" title="Fotograma siguiente (.)" onClick={() => step(1)}><Icon name="stepFwd" size={16} /></button>
        <span className="mut" id="sm-time">{clock(t)} / {clock(video.duration ?? 0)}{list.length ? ` · fotograma ${nearest(t) + 1} de ${list.length}` : " · cargando fotogramas…"}</span>
      </div>
      {msg && <p className={msg.ok ? "mut" : "err"} id="sm-msg">{msg.t}</p>}
      {!!taken.length && (
        <div id="sm-taken">
          <h3>Tomadas en esta sesión ({taken.filter((x) => !x.existing).length})</h3>
          <div className="exrow">{taken.map((x, i) => <figure key={i} className="smthumb"><img src={api.thumbUrl(x.id)} height={90} alt="" /><figcaption>{clock(x.t)}{x.existing ? " · ya estaba" : ""}</figcaption></figure>)}</div>
        </div>
      )}
    </div>
  );
}

/** Formulario del muestreo automático de un video ya cargado. */
export function AutoSampler({ video, onDone }: { video: ProjectVideo; onDone: () => void }) {
  const qc = useQueryClient();
  const [interval, setInterval_] = useState(5);
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState<string>("");
  const [msg, setMsg] = useState<{ t: string; ok: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const dur = video.duration ?? 0;
  const last = end === "" ? dur : Math.min(+end, dur);
  const est = interval > 0 && last >= start ? Math.floor((last - start) / interval + 1e-9) + 1 : 0;
  const go = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await api.sampleVideo(video.id, { interval, start, end: end === "" ? null : +end });
      setMsg({ t: `${r.frames} imágenes nuevas${r.already ? ` (${r.already} ya estaban)` : ""}.`, ok: true });
      for (const k of ["frames", "stats", "projects", "videos"]) void qc.invalidateQueries({ queryKey: [k] });
      onDone();
    } catch (e) { setMsg({ t: (e as Error).message, ok: false }); }
    finally { setBusy(false); }
  };
  return (
    <div className="formrow" id={"as-form-" + video.id}>
      <label>Cada <input type="number" className="sa-interval" min={0.04} step={0.5} value={interval} onChange={(e) => setInterval_(+e.target.value)} style={{ width: 70 }} /> s</label>
      <label>Desde <input type="number" className="sa-start" min={0} step={1} value={start} onChange={(e) => setStart(Math.max(0, +e.target.value))} style={{ width: 70 }} /> s</label>
      <label>Hasta <input type="number" className="sa-end" min={0} step={1} placeholder="final" value={end} onChange={(e) => setEnd(e.target.value)} style={{ width: 70 }} /> s</label>
      <span className="mut">≈ {est} imágenes{est > 300 ? " (máximo 300 por vez)" : ""}</span>
      <button className="primary sa-go" disabled={busy || est < 1 || est > 300} onClick={() => void go()}>{busy ? "Extrayendo…" : "Muestrear"}</button>
      {msg && <span className={msg.ok ? "mut" : "err"}>{msg.t}</span>}
    </div>
  );
}
