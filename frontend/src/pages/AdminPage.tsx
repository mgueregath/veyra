import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import UserMenu from "../components/UserMenu";
import { api, type DatasetStats, type EvalExample, type Project, type RunSummary, type Status } from "../api/client";

const STATUS_LABEL: Record<Status, string> = {
  pending: "Nadie la revisó", single: "Revisada por 1 persona", agreed: "Varias, de acuerdo", disagreement: "Varias, en desacuerdo", final: "Final (decidió el revisor)",
};

export default function AdminPage({ project, user, onBack, onProjects, onLogout }: { project: Project; user: string; onBack: () => void; onProjects: () => void; onLogout: () => void }) {
  const [tab, setTab] = useState<"quality" | "assign" | "datasets" | "train" | "assist" | "eval" | "team" | "system">("quality")
  const [preset, setPreset] = useState<string | null>(null);
  const isAdmin = project.role === "admin";
  return (
    <div className="page">
      <header className="pagehead">
        <b>LabelHub</b>
        <span className="mut">· {project.name} · Gestión</span>
        <span className="grow" />
        <button title="Volver a la pantalla de etiquetado del proyecto" onClick={onBack}>← Etiquetar</button>
        <UserMenu user={user} role={project.role} projectName={project.name} onProjects={onProjects} onLogout={onLogout} />
      </header>
      <div className="tabs">
        {([["quality", "Calidad"], ["assign", "Asignaciones"], ["datasets", "Datasets"], ["train", "Entrenamiento"], ["assist", "Asistido"], ["eval", "Evaluación"], ["team", "Equipo"], ["system", "Sistema"]] as const).map(([k, l]) => (
          <button title={{ quality: "Estado del etiquetado, acuerdo entre personas y trabajo de cada una", assign: "Reparto de imágenes entre el equipo", datasets: "Versiones congeladas del dataset y su exportación", train: "Entrenar modelos en la GPU", assist: "Preanotar con un modelo y buscar las imágenes más inciertas", eval: "Medir detectores y modelos sobre un dataset", team: "Personas y roles del proyecto", system: "Uso de disco, estado del servicio, copia del proyecto y tu contraseña" }[k]} key={k} id={"tab-" + k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      {tab === "quality" && <Quality project={project} />}
      {tab === "assign" && <Assign project={project} isAdmin={isAdmin} />}
      {tab === "datasets" && <Datasets project={project} isAdmin={isAdmin} />}
      {tab === "train" && <Training project={project} onEvaluate={(id) => { setPreset(`model:${id}`); setTab("eval"); }} />}
      {tab === "assist" && <Assisted project={project} isAdmin={isAdmin} />}
      {tab === "eval" && <Evaluation project={project} preset={preset} />}
      {tab === "team" && <Team project={project} isAdmin={isAdmin} me={user} />}
      {tab === "system" && <System project={project} isAdmin={isAdmin} />}
    </div>
  );
}

function Quality({ project }: { project: Project }) {
  const q = useQuery({ queryKey: ["quality", project.id], queryFn: () => api.quality(project.id) });
  if (q.isLoading) return <div className="center">Cargando…</div>;
  if (!q.data) return <div className="center err">{(q.error as Error)?.message ?? "Sin datos"}</div>;
  const d = q.data, cls = project.config.classes.filter((c) => !c.negative);
  const pct = (x: number | null) => (x === null ? "—" : (100 * x).toFixed(1) + " %");
  return (
    <div className="panel" id="quality">
      <h2>Estado del etiquetado ({d.frames} imágenes)</h2>
      <table className="tbl"><tbody>
        {(Object.keys(STATUS_LABEL) as Status[]).map((s) => <tr key={s}><td>{STATUS_LABEL[s]}</td><td id={"qs-" + s}>{d.status[s]}</td></tr>)}
      </tbody></table>
      <h2>Acuerdo entre anotadores</h2>
      <p className="mut">Imágenes revisadas por 2 o más personas: <b id="q-overlap">{d.agreement.frames_with_overlap}</b> · coinciden del todo: <b>{pct(d.agreement.agree_rate)}</b> · F1 medio entre pares (IoU ≥ 0,5, misma clase): <b>{pct(d.agreement.mean_f1)}</b></p>
      <h2>Trabajo por persona</h2>
      <table className="tbl">
        <thead><tr><th>Usuario</th><th>Revisadas</th>{cls.map((c) => <th key={c.id}>{c.name}</th>)}</tr></thead>
        <tbody>{d.users.map((u) => <tr key={u.user}><td>{u.user}</td><td>{u.reviewed}</td>{cls.map((c) => <td key={c.id}>{u.boxes[c.id] ?? 0}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

function Assign({ project, isAdmin }: { project: Project; isAdmin: boolean }) {
  const qc = useQueryClient();
  const members = useQuery({ queryKey: ["members", project.id], queryFn: () => api.members(project.id) });
  const summary = useQuery({ queryKey: ["assignments", project.id], queryFn: () => api.assignments(project.id) });
  const [sel, setSel] = useState<string[]>([]);
  const [overlap, setOverlap] = useState(10);
  const [seed, setSeed] = useState(0);
  const run = useMutation({
    mutationFn: () => api.assign(project.id, { users: sel, overlap_pct: overlap, seed }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["assignments", project.id] }),
  });
  const workers = (members.data ?? []).filter((m) => m.role !== "viewer");
  return (
    <div className="panel" id="assign">
      <h2>Repartir imágenes</h2>
      <p className="mut">Reparte entre las personas elegidas las imágenes que aún no tienen dueño ni revisión. «Doble etiquetado» asigna además ese % a una segunda persona, para medir el acuerdo.</p>
      <div className="formrow">
        {workers.map((m) => (
          <label key={m.username}><input title="Incluir a esta persona en el reparto de imágenes" type="checkbox" checked={sel.includes(m.username)} onChange={(e) => setSel(e.target.checked ? [...sel, m.username] : sel.filter((x) => x !== m.username))} /> {m.username} <span className="mut">({m.role})</span></label>
        ))}
      </div>
      <div className="formrow">
        <label>Doble etiquetado <input title="Porcentaje de imágenes que además se asignan a una segunda persona, para medir el acuerdo entre quienes etiquetan" type="number" min={0} max={100} value={overlap} onChange={(e) => setOverlap(+e.target.value)} style={{ width: 70 }} /> %</label>
        <label>Semilla <input title="Semilla del reparto: con la misma semilla el reparto sale igual" type="number" value={seed} onChange={(e) => setSeed(+e.target.value)} style={{ width: 70 }} /></label>
        <button title="Reparte entre las personas elegidas las imágenes que aún no tienen dueño ni revisión" className="primary" id="assign-go" disabled={!isAdmin || !sel.length || run.isPending} onClick={() => run.mutate()}>Repartir</button>
        {!isAdmin && <span className="mut">Solo un administrador puede repartir.</span>}
      </div>
      {run.data && <p className="okmsg" id="assign-res">Repartidas {run.data.frames} imágenes ({run.data.created} asignaciones nuevas, {run.data.overlap} con doble etiquetado).</p>}
      {run.error && <p className="err">{(run.error as Error).message}</p>}
      <table className="tbl">
        <thead><tr><th>Usuario</th><th>Asignadas</th><th>Hechas</th></tr></thead>
        <tbody>{(summary.data ?? []).map((r) => <tr key={r.user}><td>{r.user}</td><td>{r.assigned}</td><td>{r.done}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

function Datasets({ project, isAdmin }: { project: Project; isAdmin: boolean }) {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["datasets", project.id], queryFn: () => api.datasets(project.id) });
  const [name, setName] = useState("");
  const [include, setInclude] = useState<string[]>(["final", "agreed", "single"]);
  const [split, setSplit] = useState<[number, number, number]>([70, 15, 15]);
  const [seed, setSeed] = useState("labelhub");
  const [open, setOpen] = useState<number | null>(null);
  const [exported, setExported] = useState<string>("");
  const detail = useQuery({ queryKey: ["dataset", open], queryFn: () => api.dataset(open!), enabled: open !== null });
  const create = useMutation({
    mutationFn: () => api.createDataset(project.id, { name, include, split: split.map((x) => x / 100), seed }),
    onSuccess: (d: DatasetStats) => { void qc.invalidateQueries({ queryKey: ["datasets", project.id] }); setOpen(d.id); setName(""); },
  });
  const exp = useMutation({ mutationFn: (v: { id: number; f: "yolo" | "coco" | "both" }) => api.exportDataset(v.id, v.f), onSuccess: (r) => setExported(r.path) });
  const cls = project.config.classes.filter((c) => !c.negative);
  const sum = split[0] + split[1] + split[2];
  return (
    <div className="panel" id="datasets">
      <h2>Versiones de dataset</h2>
      <p className="mut">Una versión congela las etiquetas finales de ese momento: no cambia aunque se siga etiquetando. El reparto train/val/test es por video y no cambia entre versiones.</p>
      <div className="formrow">
        <input title="Nombre de la versión del dataset (letras, números, punto, guion y guion bajo). No se puede repetir" type="text" id="ds-name" placeholder="Nombre (p. ej. v1)" value={name} onChange={(e) => setName(e.target.value)} />
        {(["final", "agreed", "single"] as const).map((s) => (
          <label key={s}><input title="Qué imágenes entran en la versión según su estado: finales (decidió el revisor), de acuerdo (varias personas coinciden) o de una sola persona" type="checkbox" checked={include.includes(s)} onChange={(e) => setInclude(e.target.checked ? [...include, s] : include.filter((x) => x !== s))} /> {s === "final" ? "finales" : s === "agreed" ? "de acuerdo" : "de una persona"}</label>
        ))}
      </div>
      <div className="formrow">
        <span className="mut">Reparto %</span>
        {(["train", "val", "test"] as const).map((n, i) => (
          <label key={n}>{n} <input title="Porcentaje de videos que van a esta partición. Deben sumar 100; el reparto es por video" type="number" min={0} max={100} value={split[i]} style={{ width: 64 }} onChange={(e) => { const s = [...split] as [number, number, number]; s[i] = +e.target.value; setSplit(s); }} /></label>
        ))}
        <label>semilla <input title="Semilla del reparto train/val/test por video: misma semilla, mismo reparto en todas las versiones" type="text" value={seed} onChange={(e) => setSeed(e.target.value)} style={{ width: 110 }} /></label>
        <button title="Congela las etiquetas finales de este momento en una versión que ya no cambia, aunque se siga etiquetando" className="primary" id="ds-create" disabled={!isAdmin || !name || !include.length || sum !== 100 || create.isPending} onClick={() => create.mutate()}>Crear versión</button>
        {sum !== 100 && <span className="warn">El reparto debe sumar 100 (suma {sum})</span>}
        {!isAdmin && <span className="mut">Solo un administrador puede crear versiones.</span>}
      </div>
      {create.error && <p className="err" id="ds-err">{(create.error as Error).message}</p>}
      <table className="tbl" id="ds-list">
        <thead><tr><th>Versión</th><th>Imágenes</th><th>Creada</th><th /></tr></thead>
        <tbody>{(list.data ?? []).map((v) => (
          <tr key={v.id}><td>{v.name}</td><td>{v.n_items}</td><td>{new Date(v.created_at).toLocaleString()}</td><td><button title="Ver el detalle de esta versión: imágenes y cajas por partición" onClick={() => setOpen(v.id)}>Ver</button></td></tr>
        ))}</tbody>
      </table>
      {detail.data && (
        <div id="ds-detail">
          <h2>{detail.data.name}</h2>
          <table className="tbl">
            <thead><tr><th>Partición</th><th>Imágenes</th><th>Con cajas</th><th>Videos</th>{cls.map((c) => <th key={c.id}>{c.name}</th>)}</tr></thead>
            <tbody>{(["train", "val", "test"] as const).map((s) => {
              const x = detail.data!.splits[s];
              return <tr key={s}><td>{s}</td><td>{x.frames}</td><td>{x.frames_with_positive}</td><td>{x.videos}</td>{cls.map((c) => <td key={c.id}>{x.boxes[c.id] ?? 0}</td>)}</tr>;
            })}</tbody>
          </table>
          {detail.data.warnings.map((w) => <p key={w} className="warn">⚠ {w}</p>)}
          <div className="formrow">
            {(["yolo", "coco", "both"] as const).map((f) => (
              <button title="Escribe esta versión en disco en el formato elegido (las imágenes son enlaces a los originales)" key={f} id={"ds-exp-" + f} onClick={() => exp.mutate({ id: detail.data!.id, f })} disabled={exp.isPending || project.role === "labeler" || project.role === "viewer"}>Exportar {f === "both" ? "YOLO + COCO" : f.toUpperCase()}</button>
            ))}
          </div>
          {exported && <p className="okmsg" id="ds-path">Exportado en <code>{exported}</code></p>}
          {exp.error && <p className="err">{(exp.error as Error).message}</p>}
        </div>
      )}
    </div>
  );
}

const pct = (x: number | null | undefined) => (x === null || x === undefined ? "—" : (100 * x).toFixed(1) + " %");

function Thumb({ ex }: { ex: EvalExample }) {
  const h = 150, w = Math.round((ex.w * h) / ex.h);
  return (
    <div className="exthumb" title={`${ex.video} · imagen ${ex.frame_id}`} style={{ width: w, height: h }}>
      <img src={api.thumbUrl(ex.frame_id)} alt="" width={w} height={h} />
      <svg viewBox={`0 0 ${ex.w} ${ex.h}`} width={w} height={h}>
        <rect x={ex.bbox[0]} y={ex.bbox[1]} width={ex.bbox[2]} height={ex.bbox[3]} fill="none" stroke={ex.score !== undefined ? "#f85149" : "#e3b341"} strokeWidth={Math.max(3, ex.w / 250)} />
      </svg>
      {ex.score !== undefined && <span className="exscore">{ex.score.toFixed(1)}</span>}
    </div>
  );
}

function Evaluation({ project, preset }: { project: Project; preset: string | null }) {
  const qc = useQueryClient();
  const versions = useQuery({ queryKey: ["datasets", project.id], queryFn: () => api.datasets(project.id) });
  const runs = useQuery({ queryKey: ["runs", project.id, "evaluation"], queryFn: () => api.runs(project.id, "evaluation") });
  const models = useQuery({ queryKey: ["models", project.id], queryFn: () => api.models(project.id) });
  const [version, setVersion] = useState<number | "">("");
  const [split, setSplit] = useState("test");
  const [detector, setDetector] = useState(preset ?? "baseline:appearance");
  const [thr, setThr] = useState("");
  const [iou, setIou] = useState("0.5");
  const [open, setOpen] = useState<number | null>(null);
  const detail = useQuery({ queryKey: ["run", open], queryFn: () => api.run(open!), enabled: open !== null });
  const run = useMutation({
    mutationFn: () => api.createEvaluation(project.id, { dataset_version_id: Number(version || versions.data?.[0]?.id), split, detector, score_threshold: thr === "" ? null : Number(thr), iou: Number(iou) }),
    onSuccess: (r: RunSummary) => { void qc.invalidateQueries({ queryKey: ["runs", project.id] }); setOpen(r.id); },
  });
  const m = detail.data?.metrics;
  const cls = project.config.classes.filter((c) => !c.negative);
  return (
    <div className="panel" id="evalpanel">
      <h2>Evaluar un detector</h2>
      <p className="mut">Mide un detector sobre una partición de una versión del dataset. Acierta si su caja coincide con la etiquetada (mismo IoU y clase). Sin umbral de puntaje se usa el de mejor F1 y se informa cuál.</p>
      <div className="formrow">
        <label>Versión <select title="Versión del dataset sobre la que se mide el detector" id="ev-version" value={version} onChange={(e) => setVersion(e.target.value === "" ? "" : Number(e.target.value))}>
          {(versions.data ?? []).map((v) => <option key={v.id} value={v.id}>{v.name} ({v.n_items})</option>)}
        </select></label>
        <label>Partición <select title="Partición a evaluar: test es la que no se usa para entrenar ni elegir modelo; all junta todas (solo descriptivo)" id="ev-split" value={split} onChange={(e) => setSplit(e.target.value)}>{["test", "val", "train", "all"].map((x) => <option key={x}>{x}</option>)}</select></label>
        <label>Detector <select title="Qué detector medir: una línea base (apariencia o movimiento) o un modelo entrenado" id="ev-detector" value={detector} onChange={(e) => setDetector(e.target.value)}>
          <option value="baseline:appearance">Línea base: apariencia</option>
          <option value="baseline:motion">Línea base: movimiento</option>
          {(models.data ?? []).map((md) => <option key={md.id} value={`model:${md.id}`}>Modelo: {md.name}</option>)}
        </select></label>
        <label>Umbral de puntaje <input title="Puntaje mínimo para contar una detección. Vacío: se usa el que da el mejor F1 y se informa cuál" type="text" id="ev-thr" placeholder="mejor F1" value={thr} onChange={(e) => setThr(e.target.value)} style={{ width: 80 }} /></label>
        <label>IoU <input title="Cuánto debe solaparse la caja detectada con la etiquetada para contar como acierto (IoU, de 0 a 1). 0,5 es el estándar; 0,3 es más permisivo con piojos pequeños" type="text" id="ev-iou" value={iou} onChange={(e) => setIou(e.target.value)} style={{ width: 56 }} /></label>
        <button title="Mide el detector sobre la partición elegida y guarda el resultado en la lista para compararlo" className="primary" id="ev-go" disabled={run.isPending || !versions.data?.length} onClick={() => run.mutate()}>Evaluar</button>
      </div>
      {run.error && <p className="err" id="ev-err">{(run.error as Error).message}</p>}
      <table className="tbl" id="ev-list">
        <thead><tr><th>Ejecución</th><th>mAP</th><th>Precisión</th><th>Recall</th><th>F1</th><th>Umbral</th><th>Error de conteo</th><th /></tr></thead>
        <tbody>{(runs.data ?? []).map((r) => (
          <tr key={r.id} className={r.status === "failed" ? "warn" : ""}>
            <td>{r.name}</td><td>{pct(r.summary.map)}</td><td>{pct(r.summary.precision)}</td><td>{pct(r.summary.recall)}</td><td>{pct(r.summary.f1)}</td>
            <td>{r.summary.score_threshold ?? "—"}</td><td>{r.summary.count_mae === null ? "—" : r.summary.count_mae.toFixed(2)}</td>
            <td><button title="Ver las métricas, el conteo por video y los ejemplos de errores de esta ejecución" onClick={() => setOpen(r.id)}>Ver</button></td>
          </tr>
        ))}</tbody>
      </table>
      {m && (
        <div id="ev-detail">
          <h2>{detail.data!.name}</h2>
          <p className="mut">IoU ≥ {m.iou} · umbral {m.score_threshold} ({m.threshold_choice}) · {m.n_frames} imágenes · TP {m.micro.tp} · FP {m.micro.fp} · FN {m.micro.fn}</p>
          <table className="tbl"><thead><tr><th>Clase</th><th>Reales</th><th>AP</th><th>Precisión</th><th>Recall</th><th>F1</th></tr></thead>
            <tbody>{cls.map((c) => { const x = m.per_class[c.id]; return <tr key={c.id}><td>{c.name}</td><td>{x.gt}</td><td>{pct(x.ap)}</td><td>{pct(x.precision)}</td><td>{pct(x.recall)}</td><td>{pct(x.f1)}</td></tr>; })}</tbody></table>
          <h2>Conteo por video (reales / detectados)</h2>
          <table className="tbl"><thead><tr><th>Video</th><th>Imágenes</th><th>Reales</th><th>Detectados</th></tr></thead>
            <tbody>{m.by_video.map((v) => <tr key={v.video}><td>{v.video}</td><td>{v.frames}</td><td>{v.gt}</td><td>{v.pred}</td></tr>)}</tbody></table>
          <h2>Falsos positivos más seguros</h2>
          <div className="exrow" id="ev-fp">{m.examples.false_positives.map((e, i) => <Thumb key={i} ex={e} />)}{!m.examples.false_positives.length && <span className="mut">Ninguno</span>}</div>
          <h2>Piojos que no detectó</h2>
          <div className="exrow" id="ev-fn">{m.examples.false_negatives.map((e, i) => <Thumb key={i} ex={e} />)}{!m.examples.false_negatives.length && <span className="mut">Ninguno</span>}</div>
        </div>
      )}
    </div>
  );
}

const RUN_ACTIVE = ["queued", "running"];
const STATE_TEXT: Record<string, string> = { queued: "en cola", running: "entrenando", done: "terminado", failed: "falló", cancelled: "cancelado" };

function Training({ project, onEvaluate }: { project: Project; onEvaluate: (modelId: number) => void }) {
  const qc = useQueryClient();
  const versions = useQuery({ queryKey: ["datasets", project.id], queryFn: () => api.datasets(project.id) });
  const runs = useQuery({
    queryKey: ["runs", project.id, "training"], queryFn: () => api.runs(project.id, "training"),
    refetchInterval: (q) => (q.state.data?.some((r) => RUN_ACTIVE.includes(r.status)) ? 2000 : false),
  });
  const models = useQuery({ queryKey: ["models", project.id], queryFn: () => api.models(project.id), refetchInterval: runs.data?.some((r) => RUN_ACTIVE.includes(r.status)) ? 3000 : false });
  const doneCount = (runs.data ?? []).filter((r) => r.status === "done").length;
  useEffect(() => { void qc.invalidateQueries({ queryKey: ["models", project.id] }); }, [doneCount, qc, project.id]); // al terminar un entrenamiento aparece su modelo
  const [version, setVersion] = useState<number | "">("");
  const [arch, setArch] = useState("yolov8n");
  const [epochs, setEpochs] = useState(50);
  const [imgsz, setImgsz] = useState(1280);
  const [batch, setBatch] = useState(8);
  const [name, setName] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const detail = useQuery({ queryKey: ["run", open], queryFn: () => api.run(open!), enabled: open !== null, refetchInterval: (q) => (q.state.data && RUN_ACTIVE.includes(q.state.data.status) ? 1500 : false) });
  const start = useMutation({
    mutationFn: () => api.train(project.id, { dataset_version_id: Number(version || versions.data?.[0]?.id), arch, epochs, imgsz, batch, model_name: name || undefined }),
    onSuccess: (r) => { void qc.invalidateQueries({ queryKey: ["runs", project.id] }); setOpen(r.run_id); setName(""); },
  });
  const cancel = useMutation({ mutationFn: (id: number) => api.cancelRun(id), onSuccess: () => qc.invalidateQueries({ queryKey: ["runs", project.id] }) });
  return (
    <div className="panel" id="trainpanel">
      <h2>Entrenar un detector</h2>
      <p className="mut">Entrena con la partición train de una versión del dataset y elige el mejor modelo con val. Corre en la GPU del servidor, de a un entrenamiento por vez; los demás esperan en cola.</p>
      <div className="formrow">
        <label>Versión <select title="Versión del dataset con la que se entrena (partición train) y se elige el mejor modelo (val)" id="tr-version" value={version} onChange={(e) => setVersion(e.target.value === "" ? "" : Number(e.target.value))}>
          {(versions.data ?? []).map((v) => <option key={v.id} value={v.id}>{v.name} ({v.n_items})</option>)}
        </select></label>
        <label>Arquitectura <select title="Tamaño del modelo YOLOv8: n es el más rápido y pequeño, m el más preciso y lento" id="tr-arch" value={arch} onChange={(e) => setArch(e.target.value)}>{["yolov8n", "yolov8s", "yolov8m"].map((a) => <option key={a}>{a}</option>)}</select></label>
        <label>Épocas <input title="Cuántas pasadas completas por los datos. Se detiene antes si deja de mejorar" type="number" id="tr-epochs" min={1} max={300} value={epochs} onChange={(e) => setEpochs(+e.target.value)} style={{ width: 70 }} /></label>
        <label>Resolución <input title="Resolución a la que se redimensiona cada imagen para entrenar. Más alta ve mejor los piojos pequeños pero tarda más y usa más memoria de la GPU" type="number" id="tr-imgsz" min={320} max={2048} step={32} value={imgsz} onChange={(e) => setImgsz(+e.target.value)} style={{ width: 80 }} /></label>
        <label>Lote <input title="Imágenes por paso. Si la GPU se queda sin memoria, baja este número" type="number" id="tr-batch" min={1} max={64} value={batch} onChange={(e) => setBatch(+e.target.value)} style={{ width: 60 }} /></label>
        <label>Nombre <input title="Nombre del modelo resultante. Vacío: se genera uno automático" type="text" id="tr-name" placeholder="automático" value={name} onChange={(e) => setName(e.target.value)} style={{ width: 130 }} /></label>
        <button title="Lanza el entrenamiento en la GPU del servidor; si ya hay uno en marcha espera en cola" className="primary" id="tr-go" disabled={start.isPending || !versions.data?.length || project.role === "labeler"} onClick={() => start.mutate()}>Entrenar</button>
      </div>
      {start.error && <p className="err" id="tr-err">{(start.error as Error).message}</p>}
      <table className="tbl" id="tr-list">
        <thead><tr><th>Ejecución</th><th>Estado</th><th>Avance</th><th>mAP50 (val)</th><th /></tr></thead>
        <tbody>{(runs.data ?? []).map((r) => {
          const p = r.summary.progress as { epoch: number; epochs: number; metrics: Record<string, number> } | null | undefined;
          return (
            <tr key={r.id} data-run={r.id}>
              <td>{r.name}</td><td className={"tr-" + r.status}>{STATE_TEXT[r.status] ?? r.status}</td>
              <td>{p ? `${p.epoch} / ${p.epochs}` : "—"}</td>
              <td>{p?.metrics["metrics/mAP50(B)"] !== undefined ? pct(p.metrics["metrics/mAP50(B)"]) : "—"}</td>
              <td><button title="Ver la salida del proceso de esta ejecución" onClick={() => setOpen(r.id)}>Registro</button>{RUN_ACTIVE.includes(r.status) && <button title="Detener esta ejecución (si estaba entrenando, se interrumpe y no se guarda ningún modelo)" className="cancel" onClick={() => cancel.mutate(r.id)}>Cancelar</button>}</td>
            </tr>
          );
        })}</tbody>
      </table>
      {detail.data && <div><h2>Registro: {detail.data.name}</h2><pre className="logbox" id="tr-log">{detail.data.log || "(sin salida todavía)"}</pre></div>}
      <h2>Modelos</h2>
      <table className="tbl" id="tr-models">
        <thead><tr><th>Nombre</th><th>Arquitectura</th><th>mAP50 (val)</th><th>Precisión</th><th>Recall</th><th /></tr></thead>
        <tbody>{(models.data ?? []).map((m) => (
          <tr key={m.id}><td>{m.name}</td><td>{m.arch}</td><td>{pct(m.metrics?.map50)}</td><td>{pct(m.metrics?.precision)}</td><td>{pct(m.metrics?.recall)}</td>
            <td><button title="Ir a la pestaña Evaluación con este modelo ya elegido" onClick={() => onEvaluate(m.id)}>Evaluar</button></td></tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function Assisted({ project, isAdmin }: { project: Project; isAdmin: boolean }) {
  const qc = useQueryClient();
  const models = useQuery({ queryKey: ["models", project.id], queryFn: () => api.models(project.id) });
  const members = useQuery({ queryKey: ["members", project.id], queryFn: () => api.members(project.id) });
  const runs = useQuery({
    queryKey: ["runs", project.id, "preannotation"], queryFn: () => api.runs(project.id, "preannotation"),
    refetchInterval: (q) => (q.state.data?.some((r) => RUN_ACTIVE.includes(r.status)) ? 1500 : false),
  });
  const [model, setModel] = useState<number | "">("");
  const [scope, setScope] = useState<"unreviewed" | "all">("unreviewed");
  const [minScore, setMinScore] = useState("0.15");
  const [limit, setLimit] = useState(20);
  const [picked, setPicked] = useState<number[]>([]);
  const [who, setWho] = useState("");
  const mid = Number(model || models.data?.[0]?.id || 0);
  const pre = useMutation({
    mutationFn: () => api.preannotate(project.id, { model_id: mid, scope, min_score: Number(minScore) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runs", project.id] }),
  });
  const find = useMutation({ mutationFn: () => api.uncertain(project.id, mid, limit), onSuccess: (rows) => setPicked(rows.map((r) => r.frame_id)) });
  const workers = (members.data ?? []).filter((m) => m.role === "labeler" || m.role === "reviewer" || m.role === "admin");
  const assign = useMutation({
    mutationFn: () => api.assign(project.id, { users: [who || workers[0]?.username], overlap_pct: 0, seed: 0, frame_ids: picked }),
  });
  if (!models.data?.length) return <div className="panel" id="assist"><p className="mut">Todavía no hay modelos entrenados. Entrene uno en la pestaña «Entrenamiento».</p></div>;
  return (
    <div className="panel" id="assist">
      <h2>Ciclo de mejora con el modelo</h2>
      <p className="mut">1) <b>Preanotar</b>: el modelo propone cajas en las imágenes sin revisar (aparecen como cajas sin etiquetar con la fuente «modelo»). 2) <b>Buscar inciertas</b>: las imágenes donde el modelo duda o discrepa del detector de apariencia, que conviene que mire una persona primero. 3) <b>Asignar</b> esas imágenes a quien etiquete, reentrenar y medir de nuevo en la misma partición test.</p>
      <div className="formrow">
        <label>Modelo <select title="Modelo que propondrá las cajas" id="as-model" value={model} onChange={(e) => setModel(e.target.value === "" ? "" : Number(e.target.value))}>
          {(models.data ?? []).map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select></label>
        <label>Alcance <select title="Solo las imágenes que nadie ha revisado, o todas (reemplaza las propuestas anteriores de ese modelo)" id="as-scope" value={scope} onChange={(e) => setScope(e.target.value as "unreviewed" | "all")}><option value="unreviewed">solo sin revisar</option><option value="all">todas</option></select></label>
        <label>Puntaje mínimo <input title="Puntaje mínimo de las cajas que el modelo propone. Más bajo = más cajas y más ruido" type="text" id="as-min" value={minScore} onChange={(e) => setMinScore(e.target.value)} style={{ width: 60 }} /></label>
        <button title="Ejecuta el modelo sobre las imágenes y guarda sus cajas como propuestas, que aparecen sin etiquetar con la fuente «modelo»" className="primary" id="as-pre" disabled={!isAdmin && project.role !== "reviewer" || pre.isPending} onClick={() => pre.mutate()}>Preanotar</button>
      </div>
      {pre.error && <p className="err" id="as-err">{(pre.error as Error).message}</p>}
      <table className="tbl" id="as-runs">
        <thead><tr><th>Preanotación</th><th>Estado</th><th>Avance</th><th>Cajas propuestas</th></tr></thead>
        <tbody>{(runs.data ?? []).map((r) => {
          const p = r.summary.progress as { done: number; total: number } | null | undefined;
          return <tr key={r.id}><td>{r.name}</td><td className={"tr-" + r.status}>{STATE_TEXT[r.status] ?? r.status}</td><td>{p ? `${p.done} / ${p.total}` : "—"}</td><td>{r.summary.boxes ?? "—"}</td></tr>;
        })}</tbody>
      </table>
      <h2>Imágenes que conviene etiquetar primero</h2>
      <div className="formrow">
        <label>Cuántas <input title="Cuántas imágenes mostrar en la lista" type="number" id="as-limit" min={1} max={200} value={limit} onChange={(e) => setLimit(+e.target.value)} style={{ width: 70 }} /></label>
        <button title="Busca las imágenes sin revisar donde el modelo duda o discrepa del detector de apariencia: son las que más aportan al etiquetarlas" id="as-find" disabled={find.isPending} onClick={() => find.mutate()}>Buscar inciertas</button>
      </div>
      {find.error && <p className="err">{(find.error as Error).message}</p>}
      {find.data && (
        <div id="as-found">
          {!find.data.length && <p className="mut">No hay imágenes sin revisar con cajas de este modelo: preanote primero.</p>}
          <div className="exrow">
            {find.data.map((r) => {
              const h = 110, w = Math.round((r.width * h) / r.height);
              return (
                <label key={r.frame_id} className="asitem" title={r.reasons.join(" · ") || "sin motivo destacado"}>
                  <input title="Incluir esta imagen en la asignación" type="checkbox" checked={picked.includes(r.frame_id)} onChange={(e) => setPicked(e.target.checked ? [...picked, r.frame_id] : picked.filter((x) => x !== r.frame_id))} />
                  <img src={api.thumbUrl(r.frame_id)} width={w} height={h} alt="" />
                  <span className="exscore">{r.score.toFixed(2)}</span>
                  <small>{r.reasons[0] ?? "—"}</small>
                </label>
              );
            })}
          </div>
          {!!find.data.length && (
            <div className="formrow">
              <span>{picked.length} elegidas</span>
              <label>Asignar a <select title="Persona a la que se asignan las imágenes elegidas" id="as-who" value={who} onChange={(e) => setWho(e.target.value)}>{workers.map((m) => <option key={m.username}>{m.username}</option>)}</select></label>
              <button title="Pone las imágenes elegidas en la cola de esa persona" className="primary" id="as-assign" disabled={!isAdmin || !picked.length || assign.isPending} onClick={() => assign.mutate()}>Asignar</button>
              {!isAdmin && <span className="mut">Solo un administrador puede asignar.</span>}
            </div>
          )}
          {assign.data && <p className="okmsg" id="as-assigned">Asignadas {assign.data.created} imágenes.</p>}
          {assign.error && <p className="err">{(assign.error as Error).message}</p>}
        </div>
      )}
    </div>
  );
}

const ROLE_TEXT: Record<string, string> = { admin: "administrador", reviewer: "revisor", labeler: "etiquetador", viewer: "lector" };
const mb = (n: number) => (n >= 1e9 ? (n / 1e9).toFixed(2) + " GB" : (n / 1e6).toFixed(1) + " MB");

function Team({ project, isAdmin, me }: { project: Project; isAdmin: boolean; me: string }) {
  const qc = useQueryClient();
  const team = useQuery({ queryKey: ["team", project.id], queryFn: () => api.team(project.id), enabled: isAdmin });
  const [f, setF] = useState({ username: "", display_name: "", password: "", role: "labeler" });
  const [msg, setMsg] = useState<{ t: string; ok: boolean } | null>(null);
  const [pw, setPw] = useState<Record<string, string>>({});
  const ok = (t: string) => { setMsg({ t, ok: true }); void qc.invalidateQueries({ queryKey: ["team", project.id] }); void qc.invalidateQueries({ queryKey: ["members", project.id] }); };
  const fail = (e: unknown) => setMsg({ t: (e as Error).message, ok: false });
  const add = useMutation({ mutationFn: () => api.addMember(project.id, { ...f, password: f.password || undefined, display_name: f.display_name || undefined }), onSuccess: () => { ok(`«${f.username}» ya puede entrar como ${ROLE_TEXT[f.role]}.`); setF({ ...f, username: "", display_name: "", password: "" }); }, onError: fail });
  const role = useMutation({ mutationFn: (v: { u: string; r: string }) => api.setRole(project.id, v.u, v.r), onSuccess: () => ok("Rol actualizado."), onError: fail });
  const remove = useMutation({ mutationFn: (u: string) => api.removeMember(project.id, u), onSuccess: () => ok("Acceso quitado (su trabajo se conserva)."), onError: fail });
  const reset = useMutation({ mutationFn: (u: string) => api.resetPassword(project.id, u, pw[u] ?? ""), onSuccess: (_d, u) => { ok(`Contraseña de «${u}» cambiada.`); setPw({ ...pw, [u]: "" }); }, onError: fail });
  if (!isAdmin) return <div className="panel"><p className="mut">Solo un administrador puede gestionar el equipo.</p></div>;
  return (
    <div className="panel" id="team">
      <h2>Equipo del proyecto</h2>
      {msg && <p className={msg.ok ? "okmsg" : "err"} id="team-msg">{msg.t}</p>}
      <table className="tbl" id="team-list">
        <thead><tr><th>Usuario</th><th>Rol</th><th>Nueva contraseña</th><th /></tr></thead>
        <tbody>{(team.data ?? []).map((m) => (
          <tr key={m.username} data-user={m.username}>
            <td>{m.username}{m.display_name ? <span className="mut"> · {m.display_name}</span> : null}{m.username === me ? <span className="mut"> (tú)</span> : null}</td>
            <td><select title="Rol de esta persona en el proyecto; el cambio rige de inmediato" value={m.role} onChange={(e) => role.mutate({ u: m.username, r: e.target.value })}>{Object.entries(ROLE_TEXT).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></td>
            <td><input title="Nueva contraseña para esta persona (mínimo 8 caracteres)" type="password" placeholder="mín. 8 caracteres" value={pw[m.username] ?? ""} onChange={(e) => setPw({ ...pw, [m.username]: e.target.value })} style={{ width: 150 }} />{" "}
              <button title="Cambiar la contraseña de esta persona" className="pwbtn" disabled={(pw[m.username] ?? "").length < 8} onClick={() => reset.mutate(m.username)}>Cambiar</button></td>
            <td><button title="Quitarle el acceso al proyecto. Su trabajo ya hecho se conserva" className="cancel rmbtn" onClick={() => remove.mutate(m.username)}>Quitar acceso</button></td>
          </tr>
        ))}</tbody>
      </table>
      <h2>Añadir persona</h2>
      <p className="mut">Si el usuario no existe se crea con la contraseña inicial (que debería cambiar al entrar). Si ya existe, solo se le da acceso a este proyecto.</p>
      <div className="formrow">
        <input title="Nombre de usuario (2 a 64 caracteres: letras, números, punto, guion o guion bajo)" type="text" id="tm-user" placeholder="usuario" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} />
        <input title="Nombre para mostrar (opcional)" type="text" id="tm-name" placeholder="nombre (opcional)" value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} />
        <input title="Contraseña inicial, obligatoria si el usuario no existe (mínimo 8 caracteres). Si ya existe, déjala vacía" type="password" id="tm-pass" placeholder="contraseña inicial" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
        <select title="Rol: administrador (todo), revisor (decide etiquetas finales y opera), etiquetador (etiqueta) o lector (solo mira)" id="tm-role" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{Object.entries(ROLE_TEXT).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <button title="Da acceso al proyecto; si el usuario no existe lo crea" className="primary" id="tm-add" disabled={!f.username || add.isPending} onClick={() => add.mutate()}>Añadir</button>
      </div>
    </div>
  );
}

function System({ project, isAdmin }: { project: Project; isAdmin: boolean }) {
  const qc = useQueryClient();
  const usage = useQuery({ queryKey: ["usage", project.id], queryFn: () => api.usage(project.id), enabled: isAdmin });
  const sys = useQuery({ queryKey: ["system", project.id], queryFn: () => api.system(project.id), enabled: isAdmin, refetchInterval: 5000 });
  const [quota, setQuota] = useState("");
  const [pw, setPw] = useState({ old: "", nw: "" });
  const [msg, setMsg] = useState<{ t: string; ok: boolean } | null>(null);
  const saveQuota = useMutation({ mutationFn: () => api.setQuota(project.id, quota === "" ? null : Number(quota)), onSuccess: (u) => { qc.setQueryData(["usage", project.id], u); setMsg({ t: "Cuota guardada.", ok: true }); }, onError: (e) => setMsg({ t: (e as Error).message, ok: false }) });
  const chg = useMutation({ mutationFn: () => api.changePassword(pw.old, pw.nw), onSuccess: () => { setMsg({ t: "Tu contraseña se cambió.", ok: true }); setPw({ old: "", nw: "" }); }, onError: (e) => setMsg({ t: (e as Error).message, ok: false }) });
  const u = usage.data;
  return (
    <div className="panel" id="system">
      {msg && <p className={msg.ok ? "okmsg" : "err"} id="sys-msg">{msg.t}</p>}
      {isAdmin && (<>
        <h2>Uso de disco</h2>
        {u && (
          <table className="tbl" id="usage-tbl"><tbody>
            <tr><td>Imágenes</td><td>{u.images.files}</td><td>{mb(u.images.bytes)}</td></tr>
            <tr><td>Videos originales (externos)</td><td>{u.videos.files}</td><td>{mb(u.videos.bytes)}</td></tr>
            <tr><td>Modelos</td><td /><td>{mb(u.models.bytes)}</td></tr>
            <tr><td>Exportaciones</td><td /><td>{mb(u.exports.bytes)}</td></tr>
            <tr><td><b>Cuenta para la cuota</b></td><td /><td id="usage-owned"><b>{mb(u.owned_bytes)}</b>{u.quota_bytes ? ` de ${mb(u.quota_bytes)}` : " (sin cuota)"}{u.over_quota ? " — SUPERADA" : ""}</td></tr>
          </tbody></table>
        )}
        <div className="formrow"><label>Cuota (GB, vacío = sin límite) <input title="Límite de disco que puede ocupar lo que esta plataforma escribe para el proyecto (imágenes, modelos, exportaciones). Vacío: sin límite" type="text" id="sys-quota" value={quota} onChange={(e) => setQuota(e.target.value)} style={{ width: 90 }} /></label>
          <button title="Guardar la cuota; al superarla no se aceptan imágenes nuevas" id="sys-quota-save" onClick={() => saveQuota.mutate()}>Guardar cuota</button></div>
        <h2>Servicio</h2>
        {sys.data && <p className="mut" id="sys-jobs">Trabajos en cola: <b>{sys.data.jobs.queued}</b> · en curso: <b>{sys.data.jobs.running}</b> · sesiones en vivo activas: <b>{sys.data.live_active}</b></p>}
        <table className="tbl"><thead><tr><th>Cuándo</th><th>Quién</th><th>Qué</th></tr></thead>
          <tbody>{(sys.data?.recent ?? []).map((r, i) => <tr key={i}><td>{new Date(r.at).toLocaleString()}</td><td>{r.user ?? "—"}</td><td>{r.action}{r.target ? ` · ${r.target}` : ""}</td></tr>)}</tbody></table>
        <h2>Copia del proyecto</h2>
        <p className="mut">Descarga todo lo etiquetado (etiquetas, revisiones, asignaciones, datasets, equipo) en un archivo. No incluye imágenes ni videos, que se referencian por ruta. Se restaura con <code>python -m app.cli restore-project</code>.</p>
        <p><a id="sys-backup" href={api.backupUrl(project.id)} download>Descargar copia del proyecto</a></p>
      </>)}
      <h2>Mi contraseña</h2>
      <div className="formrow">
        <input title="Tu contraseña actual" type="password" id="pw-old" placeholder="actual" value={pw.old} onChange={(e) => setPw({ ...pw, old: e.target.value })} />
        <input title="Tu nueva contraseña (mínimo 8 caracteres)" type="password" id="pw-new" placeholder="nueva (mín. 8)" value={pw.nw} onChange={(e) => setPw({ ...pw, nw: e.target.value })} />
        <button title="Cambiar tu contraseña" id="pw-go" disabled={!pw.old || pw.nw.length < 8} onClick={() => chg.mutate()}>Cambiar mi contraseña</button>
      </div>
    </div>
  );
}
