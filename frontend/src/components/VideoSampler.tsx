import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Project, type ProjectVideo } from "../api/client";
import { BoxEditor } from "../editor/BoxEditor";
import { docFromFrame, docToAnnotation, positiveClasses } from "../editor/model";
import { Saver } from "../editor/saver";
import { useTick } from "../editor/useTick";
import Icon from "./Icon";
import Popover from "./Popover";

const clock = (t: number) => `${Math.floor(t / 60)}:${(t % 60).toFixed(2).padStart(5, "0")}`;

/** Etiquetado al instante: la imagen recién tomada (con las cajas del detector asistido, si hay) para etiquetarla sin salir del muestreo. */
function QuickLabel({ project, frameId, onSaved }: { project: Project; frameId: number; onSaved: () => void }) {
  const tick = useTick();
  const canvas = useRef<HTMLCanvasElement>(null);
  const ed = useRef<BoxEditor | null>(null);
  const cur = useRef(frameId);
  const saver = useRef<Saver | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const classes = project.config.classes;
  const sources = project.config.box_sources ?? {};
  useEffect(() => {
    const pos = positiveClasses(classes);
    saver.current = new Saver((_id, ok, e) => { if (!ok) setErr("No se pudo guardar: " + e); else { setErr(null); onSaved(); } });
    const e = new BoxEditor({
      canvas: canvas.current!, classes: () => classes, sources: () => sources, policy: () => ({ alpha: 1, active: true }), background: () => null,
      onChange: () => { if (e.doc) saver.current?.queue(cur.current, docToAnnotation(e.doc)); },
      onUi: tick, onNotice: (m) => setErr(m),
    }, pos[0]?.id ?? classes[0].id);
    ed.current = e;
    tick();
    const onKey = (ev: KeyboardEvent) => {
      if ((ev.target as HTMLElement)?.tagName === "INPUT") return;
      const k = ev.key.toLowerCase();
      const dig = classes.find((c) => c.digit === k && !c.negative);
      if (dig) e.setActiveClass(dig.id);
      else if (k === "b") e.setDrawMode(!e.drawMode);
      else if (k === "escape") e.setDrawMode(false);
      else if (k === "u") e.setLabel(null);
      else if (k === "t") e.toggleSel();
      else if ((k === "x" || k === "delete" || k === "backspace") && e.sel >= 0) { ev.preventDefault(); e.deleteSel(); }
      else if (k === "z") e.restore(false);
      else if (k === "r") e.markReviewed();
    };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); e.destroy(); ed.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    cur.current = frameId;
    let alive = true;
    api.frame(frameId).then((d) => { if (alive && ed.current) { setErr(null); ed.current.setFrame(docFromFrame(d), api.imageUrl(frameId)); } }).catch((e) => setErr((e as Error).message));
    return () => { alive = false; };
  }, [frameId]);
  const e = ed.current, ui = e?.ui;
  return (
    <div id="quicklabel">
      <div className="formrow">
        {positiveClasses(classes).map((c) => (
          <button key={c.id} className={"tbtn chipc" + (ui?.activeClass === c.id ? " on" : "")} id={"ql-cls-" + c.id} title={`Clase ${c.name} (tecla ${c.digit}): la reciben las cajas que presiones o dibujes`} onClick={() => e?.setActiveClass(c.id)}>
            <i className="dot" style={{ background: c.color }} />{c.name}<kbd>{c.digit}</kbd>
          </button>
        ))}
        <button className={"tbtn" + (ui?.drawMode ? " on" : "")} id="ql-draw" title="Dibujar una caja nueva (B)" onClick={() => e?.setDrawMode(!e.drawMode)}>Dibujar caja</button>
        <button className="tbtn" id="ql-reviewed" title="Marca la imagen como revisada: sin etiqueta, las cajas pasan a «ni piojo ni mosca» (R)" onClick={() => e?.markReviewed()}><Icon name="check" size={15} /> Revisada</button>
      </div>
      <div className="qlstage"><canvas ref={canvas} id="ql-cv" />{e && ui && <Popover editor={e} ui={ui} classes={classes} sources={sources} />}</div>
      {err && <p className="err" id="ql-err">{err}</p>}
    </div>
  );
}

/** Muestreo manual: se reproduce el video completo y la persona toma las instantáneas que quiera (fotograma exacto). */
export function ManualSampler({ video, project, onClose }: { video: ProjectVideo; project: Project; onClose: () => void }) {
  const qc = useQueryClient();
  const ref = useRef<HTMLVideoElement>(null);
  const pts = useQuery({ queryKey: ["pts", video.id], queryFn: () => api.videoPts(video.id, 0, 1e9), staleTime: Infinity });
  const [t, setT] = useState(0);
  const [taken, setTaken] = useState<{ id: number; t: number; existing: boolean }[]>([]);
  const [msg, setMsg] = useState<{ t: string; ok: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const list = pts.data?.pts ?? [];
  const assisted = project.config.assisted;
  const [pre, setPre] = useState(true);
  const [cur, setCur] = useState<number | null>(null);

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
      let extra = "";
      if (assisted && pre && !r.existing) {
        try { const a = await api.refreshAssisted(r.id); extra = ` Detector asistido: ${a.proposals.length} cajas.`; }
        catch (e) { extra = ` No se pudo correr el detector asistido (${(e as Error).message}).`; }
      }
      setCur(r.id);
      setMsg({ t: (r.existing ? `El fotograma de ${clock(at)} ya era una imagen del proyecto.` : r.restored ? `Fotograma de ${clock(at)} recuperado de la papelera.` : `Instantánea de ${clock(at)} guardada.`) + extra, ok: true });
      for (const k of ["frames", "stats", "projects", "videos"]) void qc.invalidateQueries({ queryKey: [k] });
    } catch (e) { setMsg({ t: (e as Error).message, ok: false }); }
    finally { setBusy(false); }
  }, [list, busy, nearest, video.first_frame_id, qc, assisted, pre]);

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
      <p className="mut">Reproduce el video y pulsa «Tomar instantánea» (o la tecla <kbd>S</kbd>) en cada instante que quieras. La imagen se abre al lado, con las cajas del detector asistido si está configurado, para etiquetarla en el momento (teclas <kbd>1</kbd>, <kbd>2</kbd>… eligen la clase; <kbd>R</kbd> la marca revisada). Con <kbd>,</kbd> y <kbd>.</kbd> avanzas de a un fotograma; con la barra espaciadora, reproduces o pausas.</p>
      <div className="smgrid">
      <div>
      <video ref={ref} id="sm-video" src={api.videoUrl(video.id)} controls preload="auto" className="smvideo" onTimeUpdate={(e) => setT(e.currentTarget.currentTime)} onError={() => setMsg({ t: "Tu navegador no puede reproducir este formato: usa el muestreo automático o sube el video en mp4.", ok: false })} />
      <div className="formrow">
        <button className="btn" id="sm-prev" title="Fotograma anterior (,)" onClick={() => step(-1)}><Icon name="stepBack" size={16} /></button>
        <button className="primary" id="sm-take" disabled={busy || !list.length} title="Guarda el fotograma actual como imagen del proyecto (S)" onClick={() => void take()}><Icon name="camera" size={16} /> Tomar instantánea</button>
        <button className="btn" id="sm-next" title="Fotograma siguiente (.)" onClick={() => step(1)}><Icon name="stepFwd" size={16} /></button>
        <span className="mut" id="sm-time">{clock(t)} / {clock(video.duration ?? 0)}{list.length ? ` · fotograma ${nearest(t) + 1} de ${list.length}` : " · cargando fotogramas…"}</span>
      </div>
      <label className="mut" id="sm-pre-row" title={assisted ? "Al tomar la instantánea se corre el modelo o flujo del etiquetado asistido sobre la imagen nueva" : "Define el detector en Gestión → Asistido y pulsa «Usar en el editor»"}>
        <input type="checkbox" id="sm-pre" disabled={!assisted} checked={!!assisted && pre} onChange={(e) => setPre(e.target.checked)} /> Preetiquetar con el detector asistido del proyecto{assisted ? "" : " (no hay uno definido: Gestión → Asistido → «Usar en el editor»)"}
      </label>
      {msg && <p className={msg.ok ? "mut" : "err"} id="sm-msg">{msg.t}</p>}
      </div>
      <div>{cur === null ? <p className="mut" id="ql-empty">Toma una instantánea y aparecerá aquí para etiquetarla al instante.</p> : <QuickLabel project={project} frameId={cur} onSaved={() => { for (const k of ["frames", "stats", "projects"]) void qc.invalidateQueries({ queryKey: [k] }); }} />}</div>
      </div>
      {!!taken.length && (
        <div id="sm-taken">
          <h3>Tomadas en esta sesión ({taken.filter((x) => !x.existing).length})</h3>
          <div className="exrow">{taken.map((x, i) => <figure key={i} className={"smthumb" + (x.id === cur ? " on" : "")} onClick={() => setCur(x.id)} title="Abrir para etiquetar"><img src={api.thumbUrl(x.id)} height={90} alt="" /><figcaption>{clock(x.t)}{x.existing ? " · ya estaba" : ""}</figcaption></figure>)}</div>
        </div>
      )}
    </div>
  );
}

/** Formulario del muestreo automático de un video ya cargado. */
export function AutoSampler({ video, onDone, onRun }: { video: ProjectVideo; onDone: () => void; onRun: (id: number) => void }) {
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
      setMsg({ t: `${r.frames} imágenes nuevas en camino${r.already ? ` (${r.already} ya estaban)` : ""}: se extraen en segundo plano.`, ok: true });
      if (r.run_id) onRun(r.run_id);
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
      <span className="mut">≈ {est} imágenes{est > 1000 ? " · se extraen en segundo plano" : ""}</span>
      <button className="primary sa-go" disabled={busy || est < 1 || est > 20000} onClick={() => void go()}>{busy ? "Extrayendo…" : "Muestrear"}</button>
      {msg && <span className={msg.ok ? "mut" : "err"}>{msg.t}</span>}
    </div>
  );
}

/** Avance de un muestreo en segundo plano. */
export function SamplingProgress({ runId, label }: { runId: number; label: string }) {
  const qc = useQueryClient();
  const run = useQuery({
    queryKey: ["run", runId], queryFn: () => api.run(runId),
    refetchInterval: (q) => (q.state.data && (q.state.data.status === "done" || q.state.data.status === "failed") ? false : 1000),
  });
  const r = run.data, p = r?.summary.progress as { done: number; total: number } | null | undefined;
  const fin = r?.status === "done" || r?.status === "failed";
  useEffect(() => { if (fin) for (const k of ["frames", "stats", "projects", "videos"]) void qc.invalidateQueries({ queryKey: [k] }); }, [fin, qc]);
  return (
    <div className="importnote sampling-progress" data-run={runId}>
      <span><b>{label}</b>: {r?.status === "failed" ? <span className="err">falló ({(r as unknown as { log?: string }).log ?? "ver registro"})</span> : fin ? "extracción terminada" : p ? `extrayendo ${p.done} de ${p.total}…` : "en cola…"}</span>
      {!fin && p && <progress value={p.done} max={Math.max(1, p.total)} />}
    </div>
  );
}
