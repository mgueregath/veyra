import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type Project } from "../api/client";
import Icon from "../components/Icon";
import Popover from "../components/Popover";
import TopNav from "../components/TopNav";
import { BoxEditor } from "../editor/BoxEditor";
import { docFromFrame, docToAnnotation, positiveClasses, type EditDoc } from "../editor/model";
import { Saver } from "../editor/saver";
import { useTick } from "../editor/useTick";

const clock = (t: number) => `${Math.floor(t / 60)}:${(t % 60).toFixed(2).padStart(5, "0")}`;
const RATES = [0.25, 0.5, 1, 2];
type Mode = "video" | "image";
const EMPTY = (): EditDoc => ({ boxes: [], deleted: [], reviewed: false });

/**
 * Muestreo manual de un video: el video y la imagen comparten el mismo lienzo. Al tomar una instantánea el video queda en pausa
 * y la imagen (con las cajas del detector asistido, si hay uno) se muestra encima, lista para etiquetar en el mismo lugar;
 * al reproducir, se vuelve al video desde ese mismo instante.
 */
export default function SamplePage({ project, user, videoId, onBack, onProjects, onModels, onAdmin, onLogout }: {
  project: Project; user: string; videoId: number; onBack: () => void; onProjects: () => void; onModels: () => void; onAdmin: () => void; onLogout: () => void;
}) {
  const qc = useQueryClient();
  const tick = useTick();
  const classes = project.config.classes;
  const sources = useMemo(() => project.config.box_sources ?? {}, [project]);
  const assisted = project.config.assisted;
  const videos = useQuery({ queryKey: ["videos", project.id], queryFn: () => api.projectVideos(project.id) });
  const video = videos.data?.find((v) => v.id === videoId) ?? null;
  const pts = useQuery({ queryKey: ["pts", videoId], queryFn: () => api.videoPts(videoId, 0, 1e9), staleTime: Infinity });
  const list = useMemo(() => pts.data?.pts ?? [], [pts.data]);

  const canvas = useRef<HTMLCanvasElement>(null);
  const vref = useRef<HTMLVideoElement>(null);
  const edRef = useRef<BoxEditor | null>(null);
  const saver = useRef<Saver | null>(null);
  const [mode, setModeState] = useState<Mode>("video");
  const modeRef = useRef<Mode>("video");
  const setMode = (m: Mode) => { modeRef.current = m; setModeState(m); edRef.current?.draw(); };
  const [cur, setCur] = useState<number | null>(null);
  const curRef = useRef<number | null>(null);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const [pre, setPre] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ t: string; ok: boolean } | null>(null);
  const [taken, setTaken] = useState<{ id: number; t: number; existing: boolean }[]>([]);
  const baseSet = useRef(false);
  const canWrite = project.role !== "viewer";

  const nearest = useCallback((c: number) => {
    let lo = 0, hi = list.length - 1;
    while (lo < hi) { const m = (lo + hi) >> 1; if (list[m] < c) lo = m + 1; else hi = m; }
    return lo > 0 && Math.abs(list[lo - 1] - c) <= Math.abs(list[lo] - c) ? lo - 1 : lo;
  }, [list]);

  // ---- editor: el fondo es el video mientras se reproduce; la imagen y sus cajas, al tomar la instantánea ----
  useEffect(() => {
    const pos = positiveClasses(classes);
    saver.current = new Saver((_id, ok, e) => {
      if (!ok) setMsg({ t: "No se pudo guardar: " + e, ok: false });
      else for (const k of ["frames", "stats", "projects"]) void qc.invalidateQueries({ queryKey: [k] });
    });
    const e = new BoxEditor({
      canvas: canvas.current!, classes: () => classes, sources: () => sources,
      policy: () => ({ alpha: modeRef.current === "image" ? 1 : 0, active: modeRef.current === "image" && canWrite }),
      background: () => (modeRef.current === "video" ? vref.current : null),
      onChange: () => { if (e.doc && curRef.current !== null && canWrite) saver.current?.queue(curRef.current, docToAnnotation(e.doc)); },
      onUi: tick, onNotice: (m, ok) => setMsg({ t: m, ok: !!ok }),
    }, pos[0]?.id ?? classes[0].id);
    edRef.current = e;
    tick();
    return () => { e.destroy(); edRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id]);

  // imagen base (primer fotograma del video): da al lienzo su tamaño; se ve el video por encima mientras está en modo video
  useEffect(() => {
    if (!video || video.first_frame_id === null || baseSet.current || !edRef.current) return;
    baseSet.current = true;
    edRef.current.setFrame(EMPTY(), api.imageUrl(video.first_frame_id));
  }, [video]);

  // el video redibuja el lienzo mientras se reproduce o se mueve
  useEffect(() => {
    const v = vref.current;
    if (!v) return;
    let raf = 0;
    const loop = () => { edRef.current?.draw(); if (!v.paused) raf = requestAnimationFrame(loop); };
    const onPlay = () => { setPlaying(true); raf = requestAnimationFrame(loop); };
    const onPause = () => { setPlaying(false); edRef.current?.draw(); };
    const onSeek = () => { setT(v.currentTime); edRef.current?.draw(); };
    const onTime = () => setT(v.currentTime);
    v.addEventListener("play", onPlay); v.addEventListener("pause", onPause); v.addEventListener("seeked", onSeek);
    v.addEventListener("timeupdate", onTime); v.addEventListener("loadeddata", onSeek);
    return () => { cancelAnimationFrame(raf); for (const [n, f] of [["play", onPlay], ["pause", onPause], ["seeked", onSeek], ["timeupdate", onTime], ["loadeddata", onSeek]] as const) v.removeEventListener(n, f); };
  }, [video]);

  const goVideo = () => { if (modeRef.current !== "video") setMode("video"); };
  const play = () => { const v = vref.current; if (!v) return; goVideo(); void v.play(); };
  const pause = () => vref.current?.pause();
  const toggle = () => { const v = vref.current; if (v) (v.paused ? play() : pause()); };
  const seek = (to: number) => { const v = vref.current; if (!v) return; goVideo(); v.currentTime = Math.max(0, Math.min(video?.duration ?? to, to)); };
  const step = (k: number) => {
    const v = vref.current;
    if (!v || !list.length) return;
    v.pause(); goVideo();
    v.currentTime = list[Math.max(0, Math.min(list.length - 1, nearest(v.currentTime) + k))] + 0.0005;
  };

  const openFrame = useCallback(async (id: number, keepView = true) => {
    const d = await api.frame(id);
    curRef.current = id;
    setCur(id);
    edRef.current?.setFrame(docFromFrame(d), api.imageUrl(id), keepView);
    setMode("image");
  }, []);

  const take = useCallback(async () => {
    const v = vref.current;
    if (!v || !list.length || busy || !video || video.first_frame_id === null) return;
    v.pause();
    const at = list[nearest(v.currentTime)];
    v.currentTime = at + 0.0005;
    setBusy(true); setMsg(null);
    try {
      const r = await api.snapshot(video.first_frame_id, at, true);
      let extra = "";
      if (assisted && pre && !r.existing) {
        try { const a = await api.refreshAssisted(r.id); extra = ` Detector asistido: ${a.proposals.length} cajas.`; }
        catch (e) { extra = ` No se pudo correr el detector asistido (${(e as Error).message}).`; }
      }
      setTaken((a) => (a.some((x) => x.id === r.id) ? a : [{ id: r.id, t: at, existing: r.existing }, ...a]));
      await openFrame(r.id);
      setMsg({ t: (r.existing ? `El fotograma de ${clock(at)} ya era una imagen del proyecto.` : r.restored ? `Fotograma de ${clock(at)} recuperado de la papelera.` : `Instantánea de ${clock(at)} guardada al final del carrusel (imagen #${r.position + 1}).`) + extra + " Etiquétala aquí y pulsa reproducir para seguir.", ok: true });
      for (const k of ["frames", "stats", "projects", "videos"]) void qc.invalidateQueries({ queryKey: [k] });
    } catch (e) { setMsg({ t: (e as Error).message, ok: false }); }
    finally { setBusy(false); }
  }, [list, busy, video, nearest, assisted, pre, openFrame, qc]);

  const showTaken = async (x: { id: number; t: number }) => {
    const v = vref.current;
    if (v) { v.pause(); v.currentTime = x.t + 0.0005; }
    try { await openFrame(x.id); } catch (e) { setMsg({ t: (e as Error).message, ok: false }); }
  };

  const setClass = (id: string) => edRef.current?.setActiveClass(id);
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      const tg = ev.target as HTMLElement;
      if (tg?.tagName === "INPUT" && (tg as HTMLInputElement).type !== "range" && (tg as HTMLInputElement).type !== "checkbox") return;
      if (tg?.tagName === "SELECT") return;
      const e = edRef.current;
      if (!e) return;
      const k = ev.key.toLowerCase();
      const dig = classes.find((c) => c.digit === k && !c.negative);
      if (k === " ") { ev.preventDefault(); toggle(); }
      else if (k === "s") { ev.preventDefault(); void take(); }
      else if (k === "," || k === "arrowleft") { ev.preventDefault(); step(-1); }
      else if (k === "." || k === "arrowright") { ev.preventDefault(); step(1); }
      else if (k === "<") seek((vref.current?.currentTime ?? 0) - 5);
      else if (k === ">") seek((vref.current?.currentTime ?? 0) + 5);
      else if (dig) setClass(dig.id);
      else if (k === "b") e.setDrawMode(!e.drawMode);
      else if (k === "escape") e.setDrawMode(false);
      else if (k === "u") e.setLabel(null);
      else if (k === "t") e.toggleSel();
      else if ((k === "x" || k === "delete" || k === "backspace") && e.sel >= 0) { ev.preventDefault(); e.deleteSel(); }
      else if (k === "z") e.restore(false);
      else if (k === "r") e.markReviewed();
      else if (k === "f") e.fit();
      else if (k === "+" || k === "=") e.zoomBy(1.25);
      else if (k === "-" || k === "_") e.zoomBy(1 / 1.25);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const e = edRef.current, ui = e?.ui, dur = video?.duration ?? 0;
  const imageMode = mode === "image";
  return (
    <div className="lp2 samplepage">
      <TopNav project={project} user={user} active="admin" onProjects={onProjects} onLabel={onBack} onModels={onModels} onAdmin={onAdmin} onLogout={onLogout} />
      <div id="toolbar" className="toolbar smbar">
        <div className="tb-title">
          <h1 title="Video que estás muestreando">Muestreo manual · {video?.name ?? "…"}</h1>
          <span id="sm-mode" className={"pill " + (imageMode ? "st-single" : "st-pending")} title={imageMode ? "Estás sobre la instantánea: las cajas se pueden editar. Reproduce para seguir con el video" : "Estás viendo el video: pausa en el instante que quieras y toma la instantánea"}>{imageMode ? "Instantánea (video en pausa)" : "Video"}</span>
        </div>
        <div className="tb-tools">
          {positiveClasses(classes).map((c) => (
            <button key={c.id} id={"cls_" + c.id} className={"tbtn chipc" + (ui?.activeClass === c.id ? " on" : "")} disabled={!imageMode} title={`Clase ${c.name} (tecla ${c.digit}): la reciben las cajas que presiones o dibujes`} onClick={() => setClass(c.id)}>
              <i className="dot" style={{ background: c.color }} />{c.name}<kbd>{c.digit}</kbd>
            </button>
          ))}
          <span className="tb-div" />
          <button id="sm-draw" className={"tbtn" + (ui?.drawMode ? " on" : "")} disabled={!imageMode} title="Dibujar una caja nueva (B)" onClick={() => e?.setDrawMode(!e.drawMode)}>Dibujar caja</button>
          <button id="sm-reviewed" className="tbtn" disabled={!imageMode} title="Marca la imagen como revisada (R): las cajas sin clase pasan a «ni piojo ni mosca»" onClick={() => e?.markReviewed()}><Icon name="check" size={15} /> Revisada</button>
        </div>
        <div className="tb-zoom">
          <button className="tbtn" id="sm-back" onClick={onAdmin} title="Volver a la gestión del proyecto">Terminar</button>
        </div>
      </div>
      <div className="smmain">
        <div className="smstage" id="sm-stage">
          <canvas id="cv" ref={canvas} />
          {e && ui && imageMode && <Popover editor={e} ui={ui} classes={classes} sources={sources} />}
          {video && <video ref={vref} id="sm-video" src={api.videoUrl(videoId)} preload="auto" muted playsInline className="smhidden" onError={() => setMsg({ t: "Tu navegador no puede reproducir este formato: sube el video en mp4 o usa el muestreo automático.", ok: false })} />}
          {!video && !videos.isLoading && <p className="err" style={{ padding: 20 }}>No se encontró el video en este proyecto.</p>}
        </div>
        <div className="smside">
          <h3>Tomadas ({taken.filter((x) => !x.existing).length})</h3>
          {!taken.length && <p className="mut">Pausa el video en el instante que quieras y pulsa «Tomar instantánea» (S). La imagen queda sobre el video para etiquetarla; al reproducir, sigues con el video.</p>}
          <div className="smthumbs">
            {taken.map((x) => (
              <figure key={x.id} className={"smthumb" + (x.id === cur && imageMode ? " on" : "")} onClick={() => void showTaken(x)} title="Volver a esta instantánea para etiquetarla">
                <img src={api.thumbUrl(x.id)} width={150} alt="" /><figcaption>{clock(x.t)}{x.existing ? " · ya estaba" : ""}</figcaption>
              </figure>
            ))}
          </div>
        </div>
      </div>
      <div className="smtransport" id="sm-transport">
        <button className="btn" id="sm-prev" title="Fotograma anterior (,)" onClick={() => step(-1)}><Icon name="stepBack" size={16} /></button>
        <button className="primary" id="sm-play" title="Reproducir o pausar (barra espaciadora)" onClick={toggle}><Icon name={playing ? "pause" : "play"} size={16} /> {playing ? "Pausar" : "Reproducir"}</button>
        <button className="btn" id="sm-next" title="Fotograma siguiente (.)" onClick={() => step(1)}><Icon name="stepFwd" size={16} /></button>
        <input type="range" id="sm-seek" min={0} max={dur || 1} step={0.01} value={Math.min(t, dur || 1)} onChange={(ev) => seek(+ev.target.value)} aria-label="Posición en el video" />
        <span className="mut" id="sm-time">{clock(t)} / {clock(dur)}{list.length ? ` · fotograma ${nearest(t) + 1} de ${list.length}` : " · cargando fotogramas…"}</span>
        <select id="sm-rate" value={rate} title="Velocidad de reproducción" onChange={(ev) => { setRate(+ev.target.value); if (vref.current) vref.current.playbackRate = +ev.target.value; }}>{RATES.map((r) => <option key={r} value={r}>{r}×</option>)}</select>
        <button className="primary" id="sm-take" disabled={busy || !list.length || !canWrite} title="Guarda el fotograma actual como imagen del proyecto (S) y deja el video en pausa para etiquetarlo" onClick={() => void take()}><Icon name="camera" size={16} /> {busy ? "Guardando…" : "Tomar instantánea"}</button>
        <label className="mut" id="sm-pre-row" title={assisted ? "Al tomar la instantánea se corre el modelo o flujo del etiquetado asistido sobre la imagen nueva" : "Define el detector en Gestión → Asistido y pulsa «Usar en el editor»"}>
          <input type="checkbox" id="sm-pre" disabled={!assisted} checked={!!assisted && pre} onChange={(ev) => setPre(ev.target.checked)} /> Preetiquetar{assisted ? "" : " (sin detector asistido)"}
        </label>
      </div>
      {msg && <p className={"smmsg " + (msg.ok ? "mut" : "err")} id="sm-msg">{msg.t}</p>}
    </div>
  );
}
