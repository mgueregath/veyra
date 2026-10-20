import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type ModelInfo, type Project } from "../api/client";
import Icon from "../components/Icon";
import TopNav from "../components/TopNav";

const pct = (x: number | null | undefined) => (x === null || x === undefined ? "—" : (100 * x).toFixed(1).replace(".", ",") + " %");

/** Modelos entrenados: lista a la izquierda, indicadores y evaluaciones del elegido a la derecha. Correrlos es otra pantalla. */
export default function ModelsPage({ project, user, onBack, onProjects, onAdmin, onRun, onLogout }: { project: Project; user: string; onBack: () => void; onProjects: () => void; onAdmin: () => void; onRun: (modelId: number) => void; onLogout: () => void }) {
  const operate = project.role === "admin" || project.role === "reviewer";
  const models = useQuery({ queryKey: ["models", project.id], queryFn: () => api.models(project.id) });
  const [selModel, setSelModel] = useState<number | null>(null);
  const [q, setQ] = useState("");
  useEffect(() => { if (selModel === null && models.data?.length) setSelModel(models.data[0].id); }, [models.data, selModel]);
  const model: ModelInfo | undefined = models.data?.find((m) => m.id === selModel);
  const shown = (models.data ?? []).filter((m) => m.name.toLowerCase().includes(q.trim().toLowerCase()));

  return (
    <div className="page models-page" id="modelspage">
      <TopNav project={project} user={user} active="models" onProjects={onProjects} onLabel={onBack} onModels={() => undefined} onAdmin={onAdmin} onLogout={onLogout} />
      <div className="mdl">
        <aside className="mdl-side">
          <section id="model-list">
            <div className="adm-cap">Modelos</div>
            <label className="searchbox sm"><Icon name="search" size={16} /><input id="msearch" type="search" placeholder="Buscar modelo…" value={q} onChange={(e) => setQ(e.target.value)} /></label>
            {models.isLoading && <p className="hint">Cargando…</p>}
            {!models.isLoading && !models.data?.length && <p className="hint" id="no-models">Todavía no hay modelos entrenados. {operate ? "Entrene uno en Gestión → Entrenamiento." : "Un administrador puede entrenar uno."}</p>}
            <div className="mdl-items">
              {shown.map((m) => (
                <button key={m.id} data-model={m.id} className={"mdl-item" + (m.id === selModel ? " on" : "")} title={`Ver los indicadores de «${m.name}»`} onClick={() => setSelModel(m.id)}>
                  <b>{m.name}</b><span className="rolechip">{m.arch.toUpperCase().replace("YOLOV", "YOLOv")}</span><small>mAP50 · {pct(m.metrics?.map50)}</small>
                </button>
              ))}
            </div>
          </section>
          {operate && <button className="btn-outline mdl-train" id="to-training" title="Entrenar otro modelo" onClick={onAdmin}><Icon name="cpu" size={17} /> Ir a entrenamiento</button>}
        </aside>
        <main className="mdl-main">
          {model ? <ModelDetail key={model.id} project={project} model={model} operate={operate} onRun={onRun} onEval={onAdmin} /> : models.data?.length ? <div className="center">Elija un modelo de la lista.</div> : null}
        </main>
      </div>
    </div>
  );
}

function ModelDetail({ project, model, operate, onRun, onEval }: { project: Project; model: ModelInfo; operate: boolean; onRun: (id: number) => void; onEval: () => void }) {
  const versions = useQuery({ queryKey: ["datasets", project.id], queryFn: () => api.datasets(project.id) });
  const runs = useQuery({ queryKey: ["runs", project.id, "evaluation"], queryFn: () => api.runs(project.id, "evaluation"), enabled: operate });
  const evals = (runs.data ?? []).filter((r) => r.params.detector === `model:${model.id}` && r.status === "done");
  const latest = useQuery({ queryKey: ["run", evals[0]?.id], queryFn: () => api.run(evals[0].id), enabled: evals.length > 0 });
  const sessions = useQuery({ queryKey: ["live", project.id], queryFn: () => api.liveList(project.id), refetchInterval: 4000 });
  const active = (sessions.data ?? []).filter((s) => s.model_id === model.id && s.status === "running").length;
  const dsName = (id: number | null) => versions.data?.find((v) => v.id === id)?.name ?? (id ? `#${id}` : "—");
  const cdef = (id: string) => project.config.classes.find((c) => c.id === id);
  const tm = model.metrics;
  const stat = (label: string, v: number | undefined, tip: string, id?: string) => <div className="statbox" title={tip} id={id}><span>{label}</span><b>{pct(v)}</b></div>;
  return (
    <>
      <section className="card" id="model-stats">
        <div className="mdl-head">
          <div>
            <h1>{model.name}</h1>
            <p className="mut" title="Arquitectura, de qué versión del dataset salió y quién lo entrenó">{model.arch.toUpperCase().replace("YOLOV", "YOLOv")} · Dataset {dsName(model.dataset_version_id)} · Entrenado por {model.created_by}</p>
          </div>
          <span className="mdl-date">{new Date(model.created_at).toLocaleDateString("es", { day: "numeric", month: "short", year: "numeric" })}</span>
          {(operate || active > 0) && <button className="primary big" id="run-model" title="Abrir la pantalla de ejecución: correr el modelo sobre un video, una cámara o imágenes" onClick={() => onRun(model.id)}>{operate ? "Ejecutar modelo" : "Ver ejecuciones"} <Icon name="external" size={18} /></button>}
        </div>
        <div className="chips">{model.classes.map((c) => { const d = cdef(c); return <span key={c} className="chip2 plain"><i className="dot" style={{ background: d?.color ?? "#999" }} />{d?.name ?? c}</span>; })}</div>
        <div className="statboxes" id="model-train-stats">
          {stat("mAP50 · validación", tm?.map50, "Calidad global del detector con el IoU estándar de 0,5 (1 = perfecto), medida en la partición de validación")}
          {stat("mAP50–95", tm?.map50_95, "Calidad promediada sobre varios IoU, de 0,5 a 0,95: premia cajas bien ajustadas")}
          {stat("Precisión", tm?.precision, "De lo que el modelo detecta, qué parte es correcta")}
          {stat("Recall", tm?.recall, "De lo que hay, qué parte encuentra el modelo")}
        </div>
      </section>

      {operate ? (
        <section className="card">
          <h2><span>Evaluaciones del modelo</span><button className="linkish" id="new-eval" title="Evaluar este modelo sobre una versión del dataset (Gestión → Evaluación)" onClick={onEval}>Nueva evaluación <Icon name="external" size={15} /></button></h2>
          {!evals.length && <p className="hint" id="no-evals">Todavía no se ha evaluado: hágalo en Gestión → Evaluación para ver su precisión sobre el conjunto de prueba.</p>}
          {!!evals.length && (
            <table className="tbl" id="model-evals">
              <thead><tr><th>Dataset</th><th>Partición</th><th>mAP</th><th>Precisión</th><th>Recall</th><th>F1</th><th title="Puntaje mínimo usado para contar una detección">Umbral</th><th title="Diferencia media entre objetos reales y detectados por video">Error conteo</th></tr></thead>
              <tbody>{evals.map((r) => (
                <tr key={r.id}><td>{r.summary.dataset}</td><td>{r.summary.split}</td><td>{pct(r.summary.map)}</td><td>{pct(r.summary.precision)}</td><td>{pct(r.summary.recall)}</td><td>{pct(r.summary.f1)}</td><td>{r.summary.score_threshold?.toFixed(2).replace(".", ",") ?? "—"}</td><td>{r.summary.count_mae?.toFixed(2).replace(".", ",") ?? "—"}</td></tr>
              ))}</tbody>
            </table>
          )}
          {latest.data?.metrics && (
            <>
              <h3>Por clase · última evaluación</h3>
              <table className="tbl" id="model-perclass">
                <thead><tr><th>Clase</th><th>Reales</th><th>AP</th><th>Precisión</th><th>Recall</th></tr></thead>
                <tbody>{Object.entries(latest.data.metrics.per_class).map(([c, x]) => { const d = cdef(c); return <tr key={c}><td><i className="dot" style={{ background: d?.color ?? "#999" }} /> {d?.name ?? c}<span className="sr"> {c}</span></td><td>{x.gt}</td><td>{pct(x.ap)}</td><td>{pct(x.precision)}</td><td>{pct(x.recall)}</td></tr>; })}</tbody>
              </table>
            </>
          )}
        </section>
      ) : <p className="hint">Las evaluaciones detalladas las ven administradores y revisores.</p>}

      <div className="autosave"><Icon name="cpu" size={26} /><div><b>Visualiza las detecciones en una pantalla dedicada</b><span>Abre «Ejecutar modelo» para usar un video, una cámara en tiempo real o imágenes.</span></div></div>
    </>
  );
}
