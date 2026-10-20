import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, type LiveSession, type LiveStats, type ModelInfo, type Project } from "../api/client";
import UserMenu from "../components/UserMenu";

/** El proceso puede responder antes de haber procesado nada: los campos que falten se completan para no romper la pantalla. */
const EMPTY: LiveStats = {
  frames: 0, fps: 0, latency_ms: 0, size: null, source_fps: 0, dropped: 0, reconnects: 0, open_failures: 0, ended: false, error: "",
  threshold: 0.25, paused: false, recording: false, record_path: "", totals: {}, active: 0, classes: [],
};
const STATE: Record<string, string> = { starting: "iniciando", running: "en vivo", stopped: "detenida", failed: "falló" };
const pct = (x: number | null | undefined) => (x === null || x === undefined ? "—" : (100 * x).toFixed(1) + " %");

export default function ModelsPage({ project, user, onBack, onProjects, onLogout }: { project: Project; user: string; onBack: () => void; onProjects: () => void; onLogout: () => void }) {
  const qc = useQueryClient();
  const operate = project.role === "admin" || project.role === "reviewer";
  const models = useQuery({ queryKey: ["models", project.id], queryFn: () => api.models(project.id) });
  const [selModel, setSelModel] = useState<number | null>(null);
  useEffect(() => { if (selModel === null && models.data?.length) setSelModel(models.data[0].id); }, [models.data, selModel]);
  const model: ModelInfo | undefined = models.data?.find((m) => m.id === selModel);

  return (
    <div className="page" id="modelspage">
      <header className="pagehead">
        <b title="LabelHub">LabelHub</b><span className="mut" title="Dónde estás">· {project.name} · Modelos</span><span className="grow" />
        <button onClick={onBack} title="Volver a la pantalla de etiquetado del proyecto">← Etiquetar</button>
        <UserMenu user={user} role={project.role} projectName={project.name} onProjects={onProjects} onLogout={onLogout} />
      </header>
      <div className="livegrid">
        <aside className="liveside">
          <section className="blk" id="model-list">
            <h3 title="Modelos entrenados en este proyecto">Modelos entrenados</h3>
            {models.isLoading && <p className="hint">Cargando…</p>}
            {!models.isLoading && !models.data?.length && <p className="hint" id="no-models">Todavía no hay modelos entrenados. {operate ? "Entrene uno en Gestión → Entrenamiento." : "Un administrador puede entrenar uno."}</p>}
            {(models.data ?? []).map((m) => (
              <button key={m.id} data-model={m.id} className={"lvitem" + (m.id === selModel ? " on" : "")} title={`Ver los indicadores de «${m.name}» y correrlo`} onClick={() => setSelModel(m.id)}>
                <b>{m.name}</b><small>{m.arch} · mAP50 {pct(m.metrics?.map50)}</small>
              </button>
            ))}
          </section>
        </aside>
        <main className="livemain">
          {model ? <ModelDetail key={model.id} project={project} model={model} operate={operate} qc={qc} /> : models.data?.length ? <div className="center">Elija un modelo de la lista.</div> : null}
        </main>
      </div>
    </div>
  );
}

function ModelDetail({ project, model, operate, qc }: { project: Project; model: ModelInfo; operate: boolean; qc: ReturnType<typeof useQueryClient> }) {
  const versions = useQuery({ queryKey: ["datasets", project.id], queryFn: () => api.datasets(project.id) });
  const runs = useQuery({ queryKey: ["runs", project.id, "evaluation"], queryFn: () => api.runs(project.id, "evaluation"), enabled: operate });
  const evals = (runs.data ?? []).filter((r) => r.params.detector === `model:${model.id}` && r.status === "done");
  const latest = useQuery({ queryKey: ["run", evals[0]?.id], queryFn: () => api.run(evals[0].id), enabled: evals.length > 0 });
  const videos = useQuery({ queryKey: ["videos", project.id], queryFn: () => api.projectVideos(project.id) });
  const sessions = useQuery({ queryKey: ["live", project.id], queryFn: () => api.liveList(project.id), refetchInterval: 2000 });
  const mine = (sessions.data ?? []).filter((s) => s.model_id === model.id);
  const [kind, setKind] = useState<"video" | "url">("video");
  const [videoId, setVideoId] = useState<number | "">("");
  const [url, setUrl] = useState("");
  const [thr, setThr] = useState(0.25);
  const [imgsz, setImgsz] = useState(1280);
  const [sel, setSel] = useState<number | null>(null);
  const [msg, setMsg] = useState<{ t: string; ok: boolean } | null>(null);
  const files = (videos.data ?? []).filter((v) => v.has_file);
  const dsName = (id: number | null) => versions.data?.find((v) => v.id === id)?.name ?? (id ? `#${id}` : "—");

  useEffect(() => { if (sel === null && mine.length) setSel((mine.find((s) => s.status === "running") ?? mine[0]).id); }, [mine, sel]);
  const cur: LiveSession | undefined = mine.find((s) => s.id === sel);
  const st: LiveStats | null = cur?.stats ? { ...EMPTY, ...cur.stats, totals: cur.stats.totals ?? {} } : null;
  const clips = useQuery({ queryKey: ["clips", sel], queryFn: () => api.liveClips(sel!), enabled: sel !== null, refetchInterval: cur?.status === "running" ? 3000 : false });

  const start = useMutation({
    mutationFn: () => api.liveStart(project.id, {
      source_kind: kind, video_id: kind === "video" ? Number(videoId || files[0]?.id) : undefined, url: kind === "url" ? url : undefined,
      model_id: model.id, threshold: thr, imgsz, name: `${model.name} · ${kind === "video" ? files.find((v) => v.id === Number(videoId || files[0]?.id))?.name ?? "video" : "cámara"}`,
    }),
    onSuccess: (s) => { setSel(s.id); setMsg(null); void qc.invalidateQueries({ queryKey: ["live", project.id] }); },
    onError: (e) => setMsg({ t: (e as Error).message, ok: false }),
  });
  const control = useMutation({ mutationFn: (b: { threshold?: number; paused?: boolean; record?: boolean }) => api.liveControl(sel!, b), onSuccess: () => qc.invalidateQueries({ queryKey: ["live", project.id] }), onError: (e) => setMsg({ t: (e as Error).message, ok: false }) });
  const save = useMutation({
    mutationFn: () => api.liveSave(sel!),
    onSuccess: (r) => setMsg({ t: `Fotograma guardado como imagen #${r.position + 1} con ${r.boxes} caja${r.boxes === 1 ? "" : "s"} del modelo: ya está en la cola de etiquetado.`, ok: true }),
    onError: (e) => setMsg({ t: (e as Error).message, ok: false }),
  });
  const stop = useMutation({ mutationFn: () => api.liveStop(sel!), onSuccess: () => qc.invalidateQueries({ queryKey: ["live", project.id] }) });
  const [localThr, setLocalThr] = useState<number | null>(null);
  const timer = useRef(0);
  const onThr = (v: number) => {
    setLocalThr(v);
    clearTimeout(timer.current);
    timer.current = window.setTimeout(() => control.mutate({ threshold: v }, { onSettled: () => setLocalThr(null) }), 250);
  };
  const running = cur?.status === "running";
  const tm = model.metrics;

  return (
    <>
      <section className="blk" id="model-stats">
        <h3 title="Datos del modelo seleccionado">{model.name}</h3>
        <p className="mut" title="Arquitectura, clases que detecta y de qué versión del dataset salió">
          {model.arch} · clases: {model.classes.join(", ")} · entrenado con el dataset «{dsName(model.dataset_version_id)}» por {model.created_by} el {new Date(model.created_at).toLocaleDateString()}
        </p>
        <div className="livestats" id="model-train-stats" title="Medidas sobre la partición de validación al terminar el entrenamiento (con los mejores pesos)">
          <div title="Calidad global del detector con el IoU estándar de 0,5 (1 = perfecto)"><span>mAP50 (validación)</span><b>{pct(tm?.map50)}</b></div>
          <div title="Calidad promediada sobre varios IoU, de 0,5 a 0,95: premia cajas bien ajustadas"><span>mAP50-95</span><b>{pct(tm?.map50_95)}</b></div>
          <div title="De lo que el modelo detecta, qué parte son piojos de verdad"><span>Precisión</span><b>{pct(tm?.precision)}</b></div>
          <div title="De los piojos que hay, qué parte encuentra el modelo"><span>Recall</span><b>{pct(tm?.recall)}</b></div>
        </div>
        {operate ? (
          <>
            <h3 title="Cada evaluación que se hizo de este modelo en Gestión → Evaluación">Evaluaciones de este modelo</h3>
            {!evals.length && <p className="hint" id="no-evals">Todavía no se ha evaluado: hágalo en Gestión → Evaluación para ver su precisión sobre el conjunto de prueba.</p>}
            {!!evals.length && (
              <table className="tbl" id="model-evals">
                <thead><tr><th>Dataset</th><th>Partición</th><th>mAP</th><th>Precisión</th><th>Recall</th><th>F1</th><th title="Puntaje mínimo usado para contar una detección">Umbral</th><th title="Diferencia media entre piojos reales y detectados por video">Error de conteo</th></tr></thead>
                <tbody>{evals.map((r) => (
                  <tr key={r.id}><td>{r.summary.dataset}</td><td>{r.summary.split}</td><td>{pct(r.summary.map)}</td><td>{pct(r.summary.precision)}</td><td>{pct(r.summary.recall)}</td><td>{pct(r.summary.f1)}</td><td>{r.summary.score_threshold?.toFixed(2) ?? "—"}</td><td>{r.summary.count_mae?.toFixed(2) ?? "—"}</td></tr>
                ))}</tbody>
              </table>
            )}
            {latest.data?.metrics && (
              <table className="tbl" id="model-perclass">
                <thead><tr><th>Clase (última evaluación)</th><th>Reales</th><th>AP</th><th>Precisión</th><th>Recall</th></tr></thead>
                <tbody>{Object.entries(latest.data.metrics.per_class).map(([c, x]) => <tr key={c}><td>{c}</td><td>{x.gt}</td><td>{pct(x.ap)}</td><td>{pct(x.precision)}</td><td>{pct(x.recall)}</td></tr>)}</tbody>
              </table>
            )}
          </>
        ) : <p className="hint">Las evaluaciones detalladas las ven administradores y revisores.</p>}
      </section>

      {operate && (
        <section className="blk" id="live-form">
          <h3 title="Probar el modelo sobre un video grabado del proyecto o sobre una cámara en directo">Correr este modelo</h3>
          <div className="formrow">
            <label>Sobre <select id="lv-kind" title="Dónde correr el modelo: un video grabado del proyecto o una cámara de red en directo" value={kind} onChange={(e) => setKind(e.target.value as "video" | "url")}>
              <option value="video">Video grabado</option><option value="url">Cámara en vivo (RTSP / MJPEG)</option></select></label>
            {kind === "video"
              ? <label>Video <select id="lv-video" title="Video grabado del proyecto que se reproducirá como si fuera en directo" value={videoId} onChange={(e) => setVideoId(e.target.value === "" ? "" : Number(e.target.value))}>{files.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}</select></label>
              : <label>URL <input type="text" id="lv-url" title="Dirección de la cámara: rtsp://… o http://… (MJPEG). Puede llevar usuario y clave; la clave no se muestra después" placeholder="rtsp://usuario:clave@192.168.1.20:554/stream" value={url} onChange={(e) => setUrl(e.target.value)} style={{ width: 300 }} /></label>}
            <label>Umbral <input type="number" id="lv-thr" title="Puntaje mínimo para mostrar una detección (se puede cambiar en vivo)" min={0} max={1} step={0.05} value={thr} onChange={(e) => setThr(+e.target.value)} style={{ width: 70 }} /></label>
            <label>Resolución <input type="number" id="lv-imgsz" title="Resolución a la que corre el modelo: más baja va más rápido, más alta ve mejor lo pequeño" min={320} max={2048} step={32} value={imgsz} onChange={(e) => setImgsz(+e.target.value)} style={{ width: 80 }} /></label>
            <button className="primary" id="lv-start" title="Inicia la sesión: arranca el proceso, carga el modelo y abre la fuente (puede tardar unos segundos)" disabled={start.isPending || (kind === "url" && !url) || (kind === "video" && !files.length)} onClick={() => start.mutate()}>{start.isPending ? "Iniciando (carga el modelo)…" : kind === "video" ? "Correr sobre el video" : "Correr en vivo"}</button>
          </div>
        </section>
      )}

      <section className="blk" id="live-list">
        <h3 title="Sesiones de este modelo: cada vez que se corre sobre un video o una cámara">Sesiones de este modelo</h3>
        {!mine.length && <p className="hint">Todavía no se ha corrido este modelo.</p>}
        <div className="formrow">
          {mine.map((s) => (
            <button key={s.id} className={"lvitem" + (s.id === sel ? " on" : "")} data-session={s.id} title={`Ver la sesión «${s.name}»`} onClick={() => setSel(s.id)}>
              <b>{s.name}</b><span className={"lvst lv-" + s.status}>{STATE[s.status]}</span><small>{s.user} · {s.source}</small>
            </button>
          ))}
        </div>
      </section>

      {msg && <div className={"msg" + (msg.ok ? " ok" : "")} id="live-msg">{msg.t}</div>}
      {cur && (
        <>
          <div className="livevideo">
            {running ? <img id="live-img" key={cur.id} src={api.liveStreamUrl(cur.id)} alt="Detección en vivo" /> : <div className="center" id="live-ended">Sesión {STATE[cur.status]}{cur.error ? ` · ${cur.error}` : ""}</div>}
          </div>
          {st && (
            <div className="livestats" id="live-stats">
              <div title="Fotogramas por segundo que el modelo procesa"><span>Velocidad</span><b id="ls-fps">{st.fps.toFixed(1)} fps</b></div>
              <div title="Tiempo desde que llega un fotograma hasta que tiene su resultado"><span>Latencia</span><b id="ls-lat">{st.latency_ms.toFixed(0)} ms</b></div>
              <div title="Tamaño y velocidad de la imagen que entra"><span>Fuente</span><b>{st.size ? `${st.size[0]}×${st.size[1]}` : "—"}{st.source_fps ? ` · ${st.source_fps.toFixed(0)} fps` : ""}</b></div>
              <div title="Fotogramas que se saltaron porque el modelo iba más lento que la fuente (así la latencia no crece)"><span>Descartados</span><b>{st.dropped}</b></div>
              <div title="Veces que se perdió la conexión con la cámara y se reconectó sola"><span>Reconexiones</span><b id="ls-rec">{st.reconnects}</b></div>
              <div title="Objetos que se están siguiendo ahora mismo"><span>Activos ahora</span><b id="ls-active">{st.active}</b></div>
              {Object.entries(st.totals).map(([c, n]) => <div key={c} title="Objetos distintos confirmados desde que empezó la sesión. Es una cota superior: con la cámara en la mano un mismo piojo puede contarse dos veces"><span>Únicos: {c}</span><b>{n}</b></div>)}
            </div>
          )}
          {st?.error && <div className="lockwarn" id="live-warn" title="Problema con la fuente de imagen">{st.error}</div>}
          {running && (
            <div className="livectl" id="live-ctl">
              <label title="Umbral de detección en vivo: más alto muestra solo lo más seguro; más bajo muestra más (y más falsos positivos)">Umbral {(localThr ?? st?.threshold ?? thr).toFixed(2)}
                <input type="range" id="lc-thr" title="Umbral de detección en vivo: más alto muestra solo lo más seguro; más bajo muestra más (y más falsos positivos)" min={0} max={1} step={0.01} value={localThr ?? st?.threshold ?? thr} disabled={!operate} onChange={(e) => onThr(+e.target.value)} /></label>
              <button id="lc-pause" title="Pausa o reanuda la detección (la fuente sigue leyéndose)" disabled={!operate} onClick={() => control.mutate({ paused: !st?.paused })}>{st?.paused ? "▶ Reanudar" : "⏸ Pausar"}</button>
              <button id="lc-rec" title="Graba un clip con las cajas dibujadas; al detener queda disponible para descargar" className={st?.recording ? "on" : ""} disabled={!operate} onClick={() => control.mutate({ record: !st?.recording })}>{st?.recording ? "⏹ Detener grabación" : "⏺ Grabar clip"}</button>
              <button id="lc-save" disabled={!operate || save.isPending} title="Guarda este fotograma como imagen para etiquetar (con lo que detectó el modelo)" onClick={() => save.mutate()}>Guardar fotograma para etiquetar</button>
              <button id="lc-stop" title="Detiene la sesión y libera la GPU. Quedan sus estadísticas finales" className="cancel" disabled={!operate} onClick={() => stop.mutate()}>Detener sesión</button>
            </div>
          )}
          {!!clips.data?.length && (
            <div id="live-clips"><h3 className="mut" title="Clips grabados en esta sesión">Clips grabados</h3>
              {clips.data.map((c) => <a key={c.name} title="Descargar este clip de video" href={api.clipUrl(cur.id, c.name)} download>{c.name} · {(c.bytes / 1e6).toFixed(1)} MB</a>)}</div>
          )}
        </>
      )}
    </>
  );
}
