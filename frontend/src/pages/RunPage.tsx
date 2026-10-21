import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type DetectResult, type LiveSession, type LiveStats, type Project } from "../api/client";
import { DetectorSelect } from "../components/Flows";
import Icon from "../components/Icon";
import TopNav from "../components/TopNav";

/** El proceso puede responder antes de haber procesado nada: los campos que falten se completan para no romper la pantalla. */
const EMPTY: LiveStats = {
  frames: 0, fps: 0, latency_ms: 0, size: null, source_fps: 0, dropped: 0, reconnects: 0, open_failures: 0, ended: false, error: "",
  threshold: 0.25, paused: false, recording: false, record_path: "", totals: {}, active: 0, classes: [],
};
const STATE: Record<string, string> = { starting: "iniciando", running: "en vivo", stopped: "detenida", failed: "falló" };
type Source = "video" | "url" | "images";
const SOURCE_LABEL: Record<Source, string> = { video: "Video", url: "Cámara", images: "Imágenes" };
const clock = (s: number) => [Math.floor(s / 3600), Math.floor(s / 60) % 60, Math.floor(s) % 60].map((x) => String(x).padStart(2, "0")).join(":");

/** Pantalla propia de ejecución: corre un modelo sobre un video, una cámara en vivo o imágenes y muestra el resultado en grande. */
export default function RunPage({ project, user, modelId, onBack, onProjects, onAdmin, onModels, onPickModel, onLogout }: {
  project: Project; user: string; modelId: number; onBack: () => void; onProjects: () => void; onAdmin: () => void; onModels: () => void; onPickModel: (id: number) => void; onLogout: () => void;
}) {
  const qc = useQueryClient();
  const operate = project.role === "admin" || project.role === "reviewer";
  const models = useQuery({ queryKey: ["models", project.id], queryFn: () => api.models(project.id) });
  const model = models.data?.find((m) => m.id === modelId);
  const videos = useQuery({ queryKey: ["videos", project.id], queryFn: () => api.projectVideos(project.id) });
  const sessions = useQuery({ queryKey: ["live", project.id], queryFn: () => api.liveList(project.id), refetchInterval: 2000 });
  const mine = useMemo(() => (sessions.data ?? []).filter((s) => s.model_id === modelId), [sessions.data, modelId]);
  const files = (videos.data ?? []).filter((v) => v.has_file);
  const cdef = (id: string) => project.config.classes.find((c) => c.id === id);

  const [source, setSource] = useState<Source>("video");
  const [videoId, setVideoId] = useState<number | "">("");
  const [url, setUrl] = useState("");
  const [thr, setThr] = useState(0.25);
  const [imgsz, setImgsz] = useState(1280);
  const [sel, setSel] = useState<number | null>(null);
  const [msg, setMsg] = useState<{ t: string; ok: boolean } | null>(null);
  const [flowId, setFlowId] = useState<number | null>(null); // en imágenes se puede correr un flujo (modelos, filtros y procedimientos con O / Y)
  const [imgMode, setImgMode] = useState<"project" | "upload">("project");
  const [first, setFirst] = useState(1);
  const [last, setLast] = useState<number | "">("");
  const [upload, setUpload] = useState<File[]>([]);
  const [result, setResult] = useState<DetectResult | null>(null);
  const [idx, setIdx] = useState(0);
  const [showBoxes, setShowBoxes] = useState(true);
  const [showConf, setShowConf] = useState(true);
  const [hidden, setHidden] = useState<string[]>([]);
  const [now, setNow] = useState(Date.now());
  const viewRef = useRef<HTMLDivElement>(null);

  useEffect(() => { if (model?.params?.imgsz) setImgsz(model.params.imgsz); }, [model?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const picked = useRef(false); // solo al entrar: si no, al elegir otra fuente se volvería a la última sesión
  useEffect(() => { if (!picked.current && mine.length) { picked.current = true; const s = mine.find((x) => x.status === "running") ?? mine[0]; setSel(s.id); setSource(s.source_kind); } }, [mine]);
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(t); }, []);

  const cur: LiveSession | undefined = source === "images" ? undefined : mine.find((s) => s.id === sel);
  const st: LiveStats | null = cur?.stats ? { ...EMPTY, ...cur.stats, totals: cur.stats.totals ?? {} } : null;
  const running = cur?.status === "running";
  const clips = useQuery({ queryKey: ["clips", sel], queryFn: () => api.liveClips(sel!), enabled: sel !== null && source !== "images", refetchInterval: running ? 3000 : false });

  const start = useMutation({
    mutationFn: () => api.liveStart(project.id, {
      source_kind: source === "url" ? "url" : "video", video_id: source === "video" ? Number(videoId || files[0]?.id) : undefined, url: source === "url" ? url : undefined,
      ...(flowId !== null ? { flow_id: flowId } : { model_id: modelId }), threshold: thr, imgsz, name: `${flowId !== null ? "flujo" : model?.name ?? "modelo"} · ${source === "video" ? files.find((v) => v.id === Number(videoId || files[0]?.id))?.name ?? "video" : "cámara"}`,
    }),
    onSuccess: (s) => { setSel(s.id); setMsg(null); void qc.invalidateQueries({ queryKey: ["live", project.id] }); },
    onError: (e) => setMsg({ t: (e as Error).message, ok: false }),
  });
  const detect = useMutation({
    mutationFn: () => (imgMode === "upload"
      ? api.detectUpload(project.id, flowId !== null ? { flow_id: flowId } : { model_id: modelId }, upload)
      : api.detectFrames(project.id, { ...(flowId !== null ? { flow_id: flowId } : { model_id: modelId }), first, last: last === "" ? first : last })),
    onSuccess: (r) => { setResult(r); setIdx(0); setMsg(null); },
    onError: (e) => setMsg({ t: (e as Error).message, ok: false }),
  });
  const control = useMutation({
    mutationFn: (b: Parameters<typeof api.liveControl>[1]) => api.liveControl(sel!, b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["live", project.id] }),
    onError: (e) => setMsg({ t: (e as Error).message, ok: false }),
  });
  const save = useMutation({
    mutationFn: () => api.liveSave(sel!),
    onSuccess: (r) => setMsg({ t: `Fotograma guardado como imagen #${r.position + 1} con ${r.boxes} caja${r.boxes === 1 ? "" : "s"} del modelo: ya está en la cola de etiquetado.`, ok: true }),
    onError: (e) => setMsg({ t: (e as Error).message, ok: false }),
  });
  const stop = useMutation({ mutationFn: () => api.liveStop(sel!), onSuccess: () => qc.invalidateQueries({ queryKey: ["live", project.id] }) });
  const [localThr, setLocalThr] = useState<number | null>(null);
  const timer = useRef(0);
  const onThr = (v: number) => {
    if (!(running && source !== "images")) { setThr(v); return; }
    setLocalThr(v);
    clearTimeout(timer.current);
    timer.current = window.setTimeout(() => control.mutate({ threshold: v }, { onSettled: () => setLocalThr(null) }), 250);
  };

  const live = source !== "images";
  const blank = !!result?.items.some((i) => i.dets.some((d) => !d.label)); // detecciones sin clase (apariencia, procedimientos…)
  const classIds = flowId !== null && result ? [...result.classes, ...(blank ? [""] : [])] : model?.classes ?? [];
  const nameOf = (c: string) => cdef(c)?.name ?? (c || "Detección");
  // visualización: en vivo la dibuja el proceso (se le pide por control); con imágenes se dibuja aquí
  const vBoxes = live && running ? st?.show_boxes ?? true : showBoxes;
  const vConf = live && running ? st?.show_conf ?? true : showConf;
  const vHidden = live && running ? (st?.show_classes ? classIds.filter((_, i) => !st.show_classes!.includes(i)) : []) : hidden;
  const toggleBoxes = (v: boolean) => (live && running ? control.mutate({ show_boxes: v }) : setShowBoxes(v));
  const toggleConf = (v: boolean) => (live && running ? control.mutate({ show_conf: v }) : setShowConf(v));
  const toggleClass = (c: string) => {
    const nextHidden = vHidden.includes(c) ? vHidden.filter((x) => x !== c) : [...vHidden, c];
    if (live && running) control.mutate({ show_classes: nextHidden.length ? classIds.map((k, i) => (nextHidden.includes(k) ? -1 : i)).filter((i) => i >= 0) : null });
    else setHidden(nextHidden);
  };
  const thrNow = live && running ? localThr ?? st?.threshold ?? thr : thr;
  const item = result?.items[idx];
  const shownDets = (item?.dets ?? []).filter((d) => d.score >= thr && !hidden.includes(d.label));
  const counts: Record<string, number> = live ? (st?.totals ?? {}) : shownDets.reduce<Record<string, number>>((a, d) => ({ ...a, [d.label]: (a[d.label] ?? 0) + 1 }), {});
  const busy = (live && (running || start.isPending)) || detect.isPending;
  const elapsed = cur && running ? (now - new Date(cur.started_at).getTime()) / 1000 : 0;
  const fullscreen = () => { void viewRef.current?.requestFullscreen?.(); };
  const stateText = !live ? (detect.isPending ? "Detectando…" : result ? "Resultado listo" : "Sin ejecutar")
    : !cur ? "Sin sesión" : cur.status !== "running" ? `Sesión ${STATE[cur.status]}` : st?.paused ? "En pausa" : st?.error ? "Sin imágenes" : "Recibiendo imágenes";
  const stateCls = !live ? "" : running && !st?.paused && !st?.error ? "ok" : "";

  return (
    <div className="page run-page" id="runpage">
      <TopNav project={project} user={user} active="models" onProjects={onProjects} onLabel={onBack} onModels={onModels} onAdmin={onAdmin} onLogout={onLogout} />
      <div className="run-head">
        <button className="linkish back" id="run-back" title="Volver a la lista de modelos" onClick={onModels}><Icon name="goBack" size={18} /> Modelos</button>
        <h1>Ejecución en tiempo real</h1>
      </div>
      {models.data && !model && <div className="center">Ese modelo ya no existe en este proyecto.</div>}
      {model && (
        <div className="run-grid">
          <div className="stack run-left">
            {msg && <div className={"msg" + (msg.ok ? " ok" : "")} id="live-msg">{msg.t}</div>}
            {live ? (
              <section className="card runcard" id="live-card">
                <div className="run-title"><h2>{cur ? cur.name : `${SOURCE_LABEL[source]} · sin sesión`}</h2>{cur && <span className={"runst" + (running ? " on" : "")}><i className="dot" />{running ? (st?.paused ? "En pausa" : "En ejecución") : STATE[cur.status]}</span>}</div>
                <p className="mut">{model.name} · {model.arch.toUpperCase().replace("YOLOV", "YOLOv")}</p>
                <div className="runview" ref={viewRef}>
                  {running ? <img id="live-img" key={cur!.id} src={api.liveStreamUrl(cur!.id)} alt="Detección en vivo" />
                    : cur ? <div className="runempty" id="live-ended">Sesión {STATE[cur.status]}{cur.error ? ` · ${cur.error}` : ""}</div>
                    : <div className="runempty" id="live-idle"><Icon name="play" size={34} /><b>Elige la fuente y pulsa «Iniciar ejecución»</b><span>{operate ? "Aquí verás las detecciones en tiempo real." : "Un administrador o revisor puede iniciar una ejecución."}</span></div>}
                  {running && <span className="livebadge"><i className="dot" />{source === "url" ? "EN VIVO" : "VIDEO"}</span>}
                  {running && <span className="runclock">{clock(elapsed)}</span>}
                  {running && <button className="fsbtn" title="Pantalla completa" aria-label="Pantalla completa" onClick={fullscreen}><Icon name="fit" size={18} /></button>}
                </div>
                {running && (
                  <div className="runctl" id="live-ctl">
                    <button id="lc-pause" className="btn-outline" title="Pausa o reanuda la detección (la fuente sigue leyéndose)" disabled={!operate} onClick={() => control.mutate({ paused: !st?.paused })}><Icon name={st?.paused ? "play" : "pause"} size={16} /> {st?.paused ? "Reanudar" : "Pausar"}</button>
                    <button id="lc-stop" className="btn-danger" title="Detiene la sesión y libera la GPU. Quedan sus estadísticas finales" disabled={!operate} onClick={() => stop.mutate()}><Icon name="square" size={15} /> Detener sesión</button>
                    <button id="lc-rec" className={"btn-outline" + (st?.recording ? " on" : "")} title="Graba un clip con las cajas dibujadas; al detener queda disponible para descargar" disabled={!operate} onClick={() => control.mutate({ record: !st?.recording })}>{st?.recording ? "Detener grabación" : "Grabar clip"}</button>
                    <button id="lc-save" className="btn-outline" disabled={!operate || save.isPending} title="Guarda este fotograma como imagen para etiquetar (con lo que detectó el modelo)" onClick={() => save.mutate()}><Icon name="camera" size={16} /> Guardar fotograma</button>
                    <span className="grow" />
                    {st && <span className="runfps"><b id="ls-fps">{st.fps.toFixed(1)} fps</b><span>Latencia <b id="ls-lat">{st.latency_ms.toFixed(0)} ms</b></span></span>}
                  </div>
                )}
                {st && (
                  <div className="runstats" id="live-stats">
                    <span title="Tamaño y velocidad de la imagen que entra">Fuente <b>{st.size ? `${st.size[0]}×${st.size[1]}` : "—"}{st.source_fps ? ` · ${st.source_fps.toFixed(0)} fps` : ""}</b></span>
                    <span title="Fotogramas que se saltaron porque el modelo iba más lento que la fuente (así la latencia no crece)">Descartados <b>{st.dropped}</b></span>
                    <span title="Veces que se perdió la conexión con la cámara y se reconectó sola">Reconexiones <b id="ls-rec">{st.reconnects}</b></span>
                    <span title="Objetos que se están siguiendo ahora mismo">Activos ahora <b id="ls-active">{st.active}</b></span>
                  </div>
                )}
                {st?.error && <div className="lockwarn" id="live-warn" title="Problema con la fuente de imagen">{st.error}</div>}
                {!!clips.data?.length && (
                  <div id="live-clips"><h3>Clips grabados</h3>
                    {clips.data.map((c) => <a key={c.name} title="Descargar este clip de video" href={api.clipUrl(cur!.id, c.name)} download>{c.name} · {(c.bytes / 1e6).toFixed(1)} MB</a>)}</div>
                )}
              </section>
            ) : (
              <section className="card runcard" id="img-card">
                <div className="run-title"><h2>{item ? item.name : "Imágenes"}</h2>{result && <span className="runst on"><i className="dot" />{result.items.length} {result.items.length === 1 ? "imagen" : "imágenes"}</span>}</div>
                <p className="mut">{model.name} · {model.arch.toUpperCase().replace("YOLOV", "YOLOv")}</p>
                <div className="runview" ref={viewRef}>
                  {item ? (
                    <div className="imgwrap" style={{ aspectRatio: `${item.width}/${item.height}` }} id="img-view">
                      <img src={item.image_url} alt={item.name} />
                      <svg viewBox={`0 0 ${item.width} ${item.height}`} preserveAspectRatio="none">
                        {showBoxes && shownDets.map((d, i) => {
                          const col = cdef(d.label)?.color ?? "#78455f", fs = Math.max(14, item.width / 55);
                          const label = `${nameOf(d.label)}${showConf ? ` ${d.score.toFixed(2).replace(".", ",")}` : ""}`;
                          return <g key={i} className="det"><rect x={d.bbox[0]} y={d.bbox[1]} width={d.bbox[2]} height={d.bbox[3]} fill="none" stroke={col} strokeWidth={Math.max(2, item.width / 500)} /><rect x={d.bbox[0]} y={Math.max(0, d.bbox[1] - fs * 1.5)} width={label.length * fs * 0.6 + 10} height={fs * 1.5} fill={col} /><text x={d.bbox[0] + 5} y={Math.max(fs, d.bbox[1] - fs * 0.4)} fontSize={fs} fill="#fff" fontWeight={600}>{label}</text></g>;
                        })}
                      </svg>
                    </div>
                  ) : <div className="runempty" id="img-idle"><Icon name="image" size={34} /><b>{detect.isPending ? "Detectando…" : "Elige las imágenes y pulsa «Detectar»"}</b><span>Una sola imagen o un conjunto: del proyecto o subidas desde tu equipo.</span></div>}
                  {item && <button className="fsbtn" title="Pantalla completa" aria-label="Pantalla completa" onClick={fullscreen}><Icon name="fit" size={18} /></button>}
                </div>
                {result && result.items.length > 1 && (
                  <div className="imgnav" id="img-nav">
                    <button className="btn-outline" id="img-prev" aria-label="Imagen anterior" disabled={idx === 0} onClick={() => setIdx(idx - 1)}><Icon name="left" size={16} /></button>
                    <span id="img-pos">{idx + 1} / {result.items.length}</span>
                    <button className="btn-outline" id="img-next" aria-label="Imagen siguiente" disabled={idx === result.items.length - 1} onClick={() => setIdx(idx + 1)}><Icon name="right" size={16} /></button>
                  </div>
                )}
                {result && result.items.length > 1 && (
                  <div className="thumbs" id="img-thumbs">{result.items.map((it, i) => <button key={it.index} className={i === idx ? "on" : ""} title={it.name} onClick={() => setIdx(i)}><img src={it.image_url} alt="" loading="lazy" /><small>{it.dets.filter((d) => d.score >= thr && !hidden.includes(d.label)).length}</small></button>)}</div>
                )}
              </section>
            )}
            {!live && item && (
              <section className="card">
                <h2><span>Detecciones</span></h2>
                <table className="tbl" id="img-dets">
                  <thead><tr><th>Clase</th><th>Confianza</th><th>Caja (x, y, ancho, alto)</th></tr></thead>
                  <tbody>{shownDets.map((d, i) => <tr key={i}><td><i className="dot" style={{ background: cdef(d.label)?.color ?? "#78455f" }} /> {nameOf(d.label)}</td><td>{(100 * d.score).toFixed(0)} %</td><td>{d.bbox.join(", ")}</td></tr>)}
                    {!shownDets.length && <tr><td colSpan={3} className="emptyrow">Sin detecciones con este umbral. Bájalo para ver más.</td></tr>}</tbody>
                </table>
              </section>
            )}
            <section className="card" id="live-list">
              <h2><span>Sesiones de este modelo<small>Cada vez que se corre sobre un video o una cámara.</small></span></h2>
              {!mine.length && <p className="hint">Todavía no se ha corrido este modelo en vivo.</p>}
              <div className="sessions">
                {mine.map((s) => (
                  <button key={s.id} className={"lvitem" + (s.id === sel && live ? " on" : "")} data-session={s.id} title={`Ver la sesión «${s.name}»`} onClick={() => { setSel(s.id); setSource(s.source_kind); }}>
                    <b>{s.name}</b><span className={"lvst lv-" + s.status}>{STATE[s.status]}</span><small>{s.user} · {s.source}</small>
                  </button>
                ))}
              </div>
            </section>
          </div>

          <aside className="card cfg runcfg" id={operate ? "live-form" : undefined}>
            <h2><span>Configuración de ejecución</span></h2>
            {operate && (
              <>
                <label className="fld">Detector
                  <DetectorSelect project={project} id="lv-model" disabled={busy} value={flowId !== null ? `flow:${flowId}` : `model:${modelId}`} onChange={(v) => { if (v.startsWith("flow:")) { setFlowId(Number(v.slice(5))); setResult(null); } else { setFlowId(null); if (Number(v.slice(6)) !== modelId) onPickModel(Number(v.slice(6))); } }} /></label>
                <div className="fld">Fuente de entrada
                  <div className="segmented" role="group" aria-label="Fuente de entrada">
                    {(["video", "url", "images"] as const).map((k) => <button key={k} id={"lv-kind-" + k} className={source === k ? "on" : ""} disabled={busy && source !== k} title={{ video: "Un video grabado del proyecto, reproducido como si fuera en directo", url: "Una cámara de red en directo (RTSP o MJPEG)", images: "Una imagen o un conjunto de imágenes" }[k]} onClick={() => { setSource(k); setMsg(null); if (k !== "images") setSel(null); }}>{SOURCE_LABEL[k]}</button>)}
                  </div></div>
                {source === "video" && <label className="fld">Video<select id="lv-video" disabled={busy} title="Video grabado del proyecto que se reproducirá como si fuera en directo" value={videoId} onChange={(e) => setVideoId(e.target.value === "" ? "" : Number(e.target.value))}>{files.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}</select></label>}
                {source === "url" && <label className="fld">Dirección de la cámara<input type="text" id="lv-url" disabled={busy} title="Dirección de la cámara: rtsp://… o http://… (MJPEG). Puede llevar usuario y clave; la clave no se muestra después" placeholder="rtsp://usuario:clave@192.168.1.20:554/stream" value={url} onChange={(e) => setUrl(e.target.value)} /></label>}
                {source === "images" && (
                  <div className="fld">Imágenes
                    <div className="segmented sm" role="group" aria-label="Origen de las imágenes">
                      <button id="im-project" className={imgMode === "project" ? "on" : ""} onClick={() => setImgMode("project")}>Del proyecto</button>
                      <button id="im-upload" className={imgMode === "upload" ? "on" : ""} onClick={() => setImgMode("upload")}>Subir</button>
                    </div>
                    {imgMode === "project" ? (
                      <div className="rangefld">
                        <label>Desde la imagen<input id="im-first" type="number" min={1} max={Math.max(1, project.n_frames)} value={first} onChange={(e) => setFirst(Math.max(1, +e.target.value || 1))} /></label>
                        <label>Hasta (opcional)<input id="im-last" type="number" min={first} max={Math.max(1, project.n_frames)} placeholder={String(first)} value={last} onChange={(e) => setLast(e.target.value === "" ? "" : Math.max(first, +e.target.value))} /></label>
                        <small className="mut">El proyecto tiene {project.n_frames} imágenes (máximo 200 por vez). Deja «Hasta» vacío para una sola.</small>
                      </div>
                    ) : (
                      <div className="rangefld">
                        <input id="im-files" type="file" accept="image/*" multiple onChange={(e) => setUpload(Array.from(e.target.files ?? []))} />
                        <small className="mut">{upload.length ? `${upload.length} ${upload.length === 1 ? "imagen elegida" : "imágenes elegidas"}` : "Elige una o varias (JPG, PNG…). No se agregan al proyecto."}</small>
                      </div>
                    )}
                  </div>
                )}
              </>
            )}
            <label className="fld">Umbral de confianza
              <span className="thr"><input type="range" id="lc-thr" title="Umbral de detección: más alto muestra solo lo más seguro; más bajo muestra más (y más falsos positivos)" min={0} max={1} step={0.01} value={thrNow} disabled={!operate || (live && flowId !== null)} onChange={(e) => onThr(+e.target.value)} /><output>{thrNow.toFixed(2).replace(".", ",")}</output></span></label>
            {operate && (
              <label className="fld">Resolución de entrada
                <select id="lv-imgsz" disabled={busy || source === "images"} title="Resolución a la que corre el modelo: más baja va más rápido, más alta ve mejor lo pequeño" value={imgsz} onChange={(e) => setImgsz(+e.target.value)}>{[...new Set([640, 960, 1280, 1600, 1920, imgsz])].sort((a, b) => a - b).map((v) => <option key={v} value={v}>{v}</option>)}</select></label>
            )}
            {operate && live && flowId !== null && <p className="note" id="flow-live-note"><Icon name="info" size={15} /> El flujo se evalúa en cada fotograma: cada fuente usa su propio puntaje mínimo y el umbral de arriba no se aplica. Solo admite modelos y procedimientos del espacio del proyecto.</p>}
            {operate && running && live && <p className="note"><Icon name="info" size={15} /> Detén la sesión para cambiar la fuente o la resolución.</p>}
            {operate && (source === "images"
              ? <button className="primary big" id="lv-start" disabled={detect.isPending || (imgMode === "upload" && !upload.length)} onClick={() => detect.mutate()}>{detect.isPending ? "Detectando…" : "Detectar imágenes"}</button>
              : !running && <button className="primary big" id="lv-start" title="Inicia la sesión: arranca el proceso, carga el modelo y abre la fuente (puede tardar unos segundos)" disabled={start.isPending || (source === "url" && !url) || (source === "video" && !files.length)} onClick={() => start.mutate()}>{start.isPending ? "Iniciando (carga el modelo)…" : source === "video" ? "Iniciar ejecución" : "Iniciar en vivo"}</button>)}

            <hr />
            <h3>{live ? "Únicos en la sesión" : "En esta imagen"}<small>{live ? "Objetos distintos confirmados." : "Con el umbral actual."}</small></h3>
            <div className="countlist" id="run-counts">
              {classIds.map((c) => <div key={c}><span><i className="dot" style={{ background: cdef(c)?.color ?? "#78455f" }} />{nameOf(c)}</span><b>{counts[c] ?? 0}</b></div>)}
            </div>
            <hr />
            <h3>Visualización</h3>
            <label className="togrow">Mostrar cajas<span className="switch"><input type="checkbox" id="vz-boxes" disabled={!operate} checked={vBoxes} onChange={(e) => toggleBoxes(e.target.checked)} /><i /></span></label>
            <label className="togrow">Mostrar confianza<span className="switch"><input type="checkbox" id="vz-conf" disabled={!operate} checked={vConf} onChange={(e) => toggleConf(e.target.checked)} /><i /></span></label>
            <div className="fld">Clases a mostrar
              <div className="chips" id="vz-classes">{classIds.map((c) => <button key={c} className={"chip2 toggle" + (vHidden.includes(c) ? " off" : "")} disabled={!operate} aria-pressed={!vHidden.includes(c)} title="Mostrar u ocultar las cajas de esta clase" onClick={() => toggleClass(c)}><i className="dot" style={{ background: cdef(c)?.color ?? "#78455f" }} />{nameOf(c)}</button>)}</div></div>
            <hr />
            <h3>Estado</h3>
            <p className={"runstate " + stateCls} id="run-state"><i className="dot" />{stateText}</p>
          </aside>
        </div>
      )}
    </div>
  );
}
