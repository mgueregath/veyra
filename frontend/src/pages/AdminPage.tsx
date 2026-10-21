import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import Icon, { type IconName } from "../components/Icon";
import ModePicker from "../components/ModePicker";
import { DetectorSelect } from "../components/Flows";
import SshKeysModal from "../components/SshKeysModal";
import TopNav from "../components/TopNav";
import { api, type DatasetStats, type EvalExample, type Project, type RunSummary, type Status, type WorkMode } from "../api/client";

const STATUS_LABEL: Record<Status, string> = {
  pending: "Nadie la revisó", single: "Revisada por 1 persona", agreed: "Varias, de acuerdo", disagreement: "Varias, en desacuerdo", final: "Final (decidió el revisor)",
};

type Tab = "quality" | "assign" | "datasets" | "train" | "assist" | "eval" | "workspace" | "team" | "config" | "system";
const SECTIONS: { k: Tab; label: string; icon: IconName; title: string; hint: string; sub: (n: string) => string }[] = [
  { k: "quality", label: "Calidad", icon: "shield", title: "Calidad del etiquetado", hint: "Estado del etiquetado, acuerdo entre personas y trabajo de cada una", sub: (n) => `Resumen del estado del etiquetado, acuerdo entre anotadores y trabajo por persona en el proyecto ${n}.` },
  { k: "assign", label: "Asignaciones", icon: "users", title: "Asignaciones", hint: "Reparto de imágenes entre el equipo", sub: () => "Reparte imágenes pendientes y organiza el trabajo del equipo." },
  { k: "datasets", label: "Datasets", icon: "database", title: "Versiones del dataset", hint: "Versiones congeladas del dataset y su exportación", sub: () => "Instantáneas congeladas de las anotaciones y divisiones estables basadas en video." },
  { k: "train", label: "Entrenamiento", icon: "cpu", title: "Entrenamiento", hint: "Entrenar modelos en la GPU", sub: () => "Entrena un detector y compara sus resultados." },
  { k: "assist", label: "Asistido", icon: "sparkles", title: "Etiquetado asistido", hint: "Preanotar con un modelo y buscar las imágenes más inciertas", sub: () => "Usa el modelo para proponer cajas y priorizar imágenes." },
  { k: "eval", label: "Evaluación", icon: "chart", title: "Evaluación de detectores", hint: "Medir detectores y modelos sobre un dataset", sub: () => "Compara resultados sobre una misma versión y partición." },
  { k: "workspace", label: "Espacio de trabajo", icon: "terminal", title: "Espacio de trabajo", hint: "Entorno de desarrollo con GPU para los procesos de visión de este proyecto, que abres desde VS Code", sub: () => "Un entorno propio de este proyecto, con GPU y versionado, para crear y probar los procesos de visión desde VS Code." },
  { k: "team", label: "Equipo", icon: "users", title: "Equipo del proyecto", hint: "Personas y roles del proyecto", sub: (n) => `Administra las personas y sus permisos en ${n}.` },
  { k: "config", label: "Configuración", icon: "gear", title: "Configuración del proyecto", hint: "Nombre, descripción y etiquetas del proyecto", sub: () => "Define la identidad del proyecto y las clases de anotación." },
  { k: "system", label: "Sistema", icon: "server", title: "Sistema y almacenamiento", hint: "Uso de disco, estado del servicio, copia del proyecto y tu contraseña", sub: () => "Consulta el uso del proyecto y la actividad del servicio." },
];

export default function AdminPage({ project, user, superuser, onBack, onProjects, onModels, onLogout }: { project: Project; user: string; superuser: boolean; onBack: () => void; onProjects: () => void; onModels: () => void; onLogout: () => void }) {
  const [tab, setTab] = useState<Tab>("quality");
  const [preset, setPreset] = useState<string | null>(null);
  const isAdmin = project.role === "admin";
  const sec = SECTIONS.find((x) => x.k === tab)!;
  return (
    <div className="page adm-page">
      <TopNav project={project} user={user} active="admin" onProjects={onProjects} onLabel={onBack} onModels={onModels} onAdmin={() => undefined} onLogout={onLogout} />
      <div className="adm">
        <nav className="adm-side" aria-label="Gestión del proyecto">
          <div className="adm-cap">Gestión del proyecto</div>
          {SECTIONS.filter((x) => x.k !== "workspace" || isAdmin).map((x) => (
            <span key={x.k} className="adm-grp">
              {x.k === "team" && <hr />}
              <button title={x.hint} id={"tab-" + x.k} className={"adm-item" + (tab === x.k ? " on" : "")} aria-current={tab === x.k ? "page" : undefined} onClick={() => setTab(x.k)}><Icon name={x.icon} size={19} />{x.label}</button>
            </span>
          ))}
        </nav>
        <main className="adm-main">
          <div className="adm-head">
            <div><h1>{sec.title}</h1><p>{sec.sub(project.name)}</p></div>
            {tab === "quality" && <button className="btn-outline" id="adm-golabel" onClick={onBack}>Ir a etiquetar <Icon name="external" size={16} /></button>}
          </div>
          {tab === "quality" && <Quality project={project} />}
          {tab === "assign" && <Assign project={project} isAdmin={isAdmin} />}
          {tab === "datasets" && <Datasets project={project} isAdmin={isAdmin} />}
          {tab === "train" && <Training project={project} onEvaluate={(id) => { setPreset(`model:${id}`); setTab("eval"); }} />}
          {tab === "assist" && <Assisted project={project} isAdmin={isAdmin} />}
          {tab === "eval" && <Evaluation project={project} preset={preset} />}
          {tab === "workspace" && <WorkspaceTab project={project} me={user} />}
          {tab === "team" && <Team project={project} isAdmin={isAdmin} me={user} />}
          {tab === "config" && <Settings project={project} isAdmin={isAdmin} />}
          {tab === "system" && <System project={project} isAdmin={isAdmin} superuser={superuser} />}
        </main>
      </div>
    </div>
  );
}

function Quality({ project }: { project: Project }) {
  const q = useQuery({ queryKey: ["quality", project.id], queryFn: () => api.quality(project.id) });
  if (q.isLoading) return <div className="center">Cargando…</div>;
  if (!q.data) return <div className="center err">{(q.error as Error)?.message ?? "Sin datos"}</div>;
  const d = q.data, cls = project.config.classes.filter((c) => !c.negative);
  const pct = (x: number | null) => (x === null ? "—" : (100 * x).toFixed(1).replace(".", ",") + " %");
  const reviewed = d.frames - d.status.pending;
  const collab = project.config.mode === "collab";
  const labels: Record<Status, string> = collab ? { ...STATUS_LABEL, pending: "Sin revisar", final: "Revisada (vale la última edición)" } : STATUS_LABEL;
  const keys = (Object.keys(STATUS_LABEL) as Status[]).filter((k) => !collab || k === "pending" || k === "final");
  const bar: Record<Status, string> = { pending: "#ded5dc", single: "#78455f", agreed: "#237653", disagreement: "#b83d52", final: "#66586e" };
  return (
    <div id="quality" className="stack">
      <div className="stats4">
        <div className="statc"><span className="static blue"><Icon name="image" size={24} /></span><div><b>{d.frames}</b><small>Imágenes</small></div></div>
        <div className="statc"><span className="static purple"><Icon name="users" size={24} /></span><div><b>{reviewed}</b><small>{collab ? "Revisadas" : "Con revisión"}</small></div></div>
        {collab ? (<>
          <div className="statc"><span className="static green"><Icon name="checkCircle" size={24} /></span><div><b id="q-pending">{d.status.pending}</b><small>Sin revisar</small></div></div>
          <div className="statc"><span className="static red"><Icon name="users" size={24} /></span><div><b>{d.users.length}</b><small>Personas que editaron</small></div></div>
        </>) : (<>
          <div className="statc"><span className="static green"><Icon name="checkCircle" size={24} /></span><div><b>{pct(d.agreement.agree_rate)}</b><small>Acuerdo total</small></div></div>
          <div className="statc"><span className="static red"><Icon name="alertCircle" size={24} /></span><div><b id="q-disagree">{d.status.disagreement}</b><small>Desacuerdo</small></div></div>
        </>)}
      </div>
      <div className="grid2">
        <section className="card">
          <h2><span className="hic"><Icon name="image" size={17} /></span><span>Estado del etiquetado<small>Distribución de las imágenes según su estado de revisión.</small></span></h2>
          <div className="stackbar" aria-hidden>{keys.map((s) => d.status[s] > 0 && <i key={s} style={{ flex: d.status[s], background: bar[s] }} />)}</div>
          <table className="tbl"><thead><tr><th>Estado</th><th className="num">Imágenes</th><th className="num">Porcentaje</th></tr></thead><tbody>
            {keys.map((s) => <tr key={s}><td><i className="dot" style={{ background: bar[s] }} /> {labels[s]}</td><td className="num" id={"qs-" + s}>{d.status[s]}</td><td className="num">{d.frames ? (100 * d.status[s] / d.frames).toFixed(1).replace(".", ",") : "0,0"} %</td></tr>)}
          </tbody></table>
        </section>
        {collab ? (
          <section className="card" id="q-collab">
            <h2><span className="hic"><Icon name="users" size={17} /></span><span>Modalidad colaborativa<small>Todas las personas editan las mismas etiquetas.</small></span></h2>
            <p className="srsum">Para cada imagen vale la última versión editada: no hay revisor ni acuerdo entre anotadores que medir. Si necesitas esa medición, cambia la modalidad en Configuración.</p>
          </section>
        ) : (
          <section className="card">
            <h2><span className="hic"><Icon name="users" size={17} /></span><span>Acuerdo entre anotadores<small>{d.agreement.frames_with_overlap} imágenes con dos o más revisiones.</small></span></h2>
            <p className="srsum">Imágenes revisadas por 2 o más personas: <b id="q-overlap">{d.agreement.frames_with_overlap}</b></p>
            <div className="bignums"><div><b>{pct(d.agreement.agree_rate)}</b><span>coinciden del todo</span><small>de {d.agreement.frames_with_overlap} imágenes</small></div><div><b>{pct(d.agreement.mean_f1)}</b><span>F1 medio</span><small>IoU ≥ 0,5 · misma clase</small></div></div>
            {d.status.disagreement > 0 && <div className="callout warnc"><Icon name="alert" size={18} /><span>{d.status.disagreement} {d.status.disagreement === 1 ? "imagen necesita" : "imágenes necesitan"} revisión.</span></div>}
          </section>
        )}
      </div>
      <section className="card">
        <h2><span className="hic"><Icon name="users" size={17} /></span><span>Trabajo por persona<small>Imágenes revisadas y objetos etiquetados por cada miembro del equipo.</small></span></h2>
        <table className="tbl">
          <thead><tr><th>Usuario</th><th className="num">Revisadas</th>{cls.map((c) => <th className="num" key={c.id}>{c.name}</th>)}</tr></thead>
          <tbody>{d.users.map((u) => <tr key={u.user}><td>{u.user}</td><td className="num">{u.reviewed}</td>{cls.map((c) => <td className="num" key={c.id}>{u.boxes[c.id] ?? 0}</td>)}</tr>)}</tbody>
        </table>
      </section>
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
    <div className="grid2 assign-grid" id="assign">
      <section className="card">
        <h2><span>Repartir imágenes</span></h2>
        <p className="mut">Reparte entre las personas elegidas las imágenes que aún no tienen dueño ni revisión. {project.config.mode === "collab" ? "Como el proyecto es colaborativo, cualquiera puede editar cualquier imagen: el reparto solo ordena la cola de cada persona." : "«Doble etiquetado» asigna además ese % a una segunda persona, para medir el acuerdo."}</p>
        <h3>Personas del equipo</h3>
        <div className="people">
          {workers.map((m) => (
            <label key={m.username} className="person"><input title="Incluir a esta persona en el reparto de imágenes" type="checkbox" checked={sel.includes(m.username)} onChange={(e) => setSel(e.target.checked ? [...sel, m.username] : sel.filter((x) => x !== m.username))} /><i className="avatar sm">{m.username.slice(0, 1).toUpperCase()}</i>{m.username}<span className="rolechip">{ROLE_TEXT[m.role] ?? m.role}</span></label>
          ))}
        </div>
        <div className="formrow">
          {project.config.mode !== "collab" && <label className="fld">Doble etiquetado<span className="withunit"><input title="Porcentaje de imágenes que además se asignan a una segunda persona, para medir el acuerdo entre quienes etiquetan" type="number" min={0} max={100} value={overlap} onChange={(e) => setOverlap(+e.target.value)} /><b>%</b></span></label>}
          <label className="fld">Semilla<input title="Semilla del reparto: con la misma semilla el reparto sale igual" type="number" value={seed} onChange={(e) => setSeed(+e.target.value)} /></label>
        </div>
        <div className="formrow">
          <button title="Reparte entre las personas elegidas las imágenes que aún no tienen dueño ni revisión" className="primary" id="assign-go" disabled={!isAdmin || !sel.length || run.isPending} onClick={() => run.mutate()}>Repartir imágenes</button>
          {!isAdmin && <span className="mut">Solo un administrador puede repartir.</span>}
        </div>
        {run.data && <p className="okmsg" id="assign-res">Repartidas {run.data.frames} imágenes ({run.data.created} asignaciones nuevas, {run.data.overlap} con doble etiquetado).</p>}
        {run.error && <p className="err">{(run.error as Error).message}</p>}
      </section>
      <aside className="card how">
        <h2><span>Cómo funciona</span></h2>
        <ol>
          <li><b>1</b><div><strong>Selecciona personas</strong><span>Elige los miembros del equipo que recibirán imágenes para etiquetar.</span></div></li>
          <li><b>2</b><div><strong>Reparte las pendientes</strong><span>Se asignarán automáticamente las imágenes pendientes entre las personas seleccionadas.</span></div></li>
          <li><b>3</b><div><strong>Mide el acuerdo</strong><span>Un porcentaje de las imágenes se asignará también a una segunda persona para evaluar la consistencia de las etiquetas.</span></div></li>
        </ol>
      </aside>
      <section className="card wide">
        <h2><span>Carga de trabajo</span></h2>
        <table className="tbl">
          <thead><tr><th>Persona</th><th className="num">Asignadas</th><th className="num">Hechas</th><th className="num">Pendientes</th></tr></thead>
          <tbody>{(summary.data ?? []).map((r) => <tr key={r.user}><td>{r.user}</td><td className="num">{r.assigned}</td><td className="num">{r.done}</td><td className="num">{r.assigned - r.done}</td></tr>)}
            {!summary.data?.length && <tr><td colSpan={4} className="emptyrow"><Icon name="users" size={20} /><br />Aún no hay asignaciones.<small>Reparte imágenes para ver aquí la carga de trabajo del equipo.</small></td></tr>}</tbody>
        </table>
      </section>
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
        {(["final", "agreed", "single"] as const).filter((s) => project.config.mode !== "collab" || s === "final").map((s) => (
          <label key={s}><input title="Qué imágenes entran en la versión según su estado: finales (decidió el revisor), de acuerdo (varias personas coinciden) o de una sola persona" type="checkbox" checked={include.includes(s)} onChange={(e) => setInclude(e.target.checked ? [...include, s] : include.filter((x) => x !== s))} /> {s === "final" ? (project.config.mode === "collab" ? "revisadas (última versión)" : "finales") : s === "agreed" ? "de acuerdo" : "de una persona"}</label>
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
      <div className="distbar" aria-label="Distribución del dataset"><i style={{ flex: split[0], background: "#78455f" }} /><i style={{ flex: split[1], background: "#22b573" }} /><i style={{ flex: split[2], background: "#fb923c" }} /></div>
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
        <rect x={ex.bbox[0]} y={ex.bbox[1]} width={ex.bbox[2]} height={ex.bbox[3]} fill="none" stroke={ex.score !== undefined ? "#b83d52" : "#e3b341"} strokeWidth={Math.max(3, ex.w / 250)} />
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
  const flowsQ = useQuery({ queryKey: ["flows", project.id], queryFn: () => api.flows(project.id) });
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
          {(flowsQ.data ?? []).map((f) => <option key={f.id} value={`flow:${f.id}`}>Flujo: {f.name}</option>)}
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
  const [detector, setDetector] = useState("");
  const [scope, setScope] = useState<"unreviewed" | "all">("unreviewed");
  const [minScore, setMinScore] = useState("0.15");
  const [limit, setLimit] = useState(20);
  const [picked, setPicked] = useState<number[]>([]);
  const [who, setWho] = useState("");
  const det = detector || (models.data?.[0] ? `model:${models.data[0].id}` : "");
  const isFlow = det.startsWith("flow:");
  const mid = det.startsWith("model:") ? Number(det.slice(6)) : 0;
  const pre = useMutation({
    mutationFn: () => api.preannotate(project.id, { ...(isFlow ? { flow_id: Number(det.slice(5)) } : { model_id: mid }), scope, min_score: Number(minScore) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runs", project.id] }),
  });
  const find = useMutation({ mutationFn: () => api.uncertain(project.id, mid, limit), onSuccess: (rows) => setPicked(rows.map((r) => r.frame_id)) });
  const workers = (members.data ?? []).filter((m) => m.role === "labeler" || m.role === "reviewer" || m.role === "admin");
  const assign = useMutation({
    mutationFn: () => api.assign(project.id, { users: [who || workers[0]?.username], overlap_pct: 0, seed: 0, frame_ids: picked }),
  });
  const noFlows = !useQuery({ queryKey: ["flows", project.id], queryFn: () => api.flows(project.id) }).data?.length;
  if (!models.data?.length && noFlows) return <div className="panel" id="assist"><p className="mut">Todavía no hay modelos entrenados ni flujos. Entrene un modelo en «Entrenamiento» o cree un flujo con detectores del proyecto.</p><DetectorSelect project={project} id="as-detector" value="" onChange={() => undefined} /></div>;
  return (
    <div className="panel" id="assist">
      <h2>Ciclo de mejora con el modelo</h2>
      <p className="mut">1) <b>Preanotar</b>: el modelo propone cajas en las imágenes sin revisar (aparecen como cajas sin etiquetar con la fuente «modelo»). 2) <b>Buscar inciertas</b>: las imágenes donde el modelo duda o discrepa del detector de apariencia, que conviene que mire una persona primero. 3) <b>Asignar</b> esas imágenes a quien etiquete, reentrenar y medir de nuevo en la misma partición test.</p>
      <div className="formrow">
        <label>Detector <DetectorSelect project={project} id="as-model" value={det} onChange={setDetector} /></label>
        <label>Alcance <select title="Solo las imágenes que nadie ha revisado, o todas (reemplaza las propuestas anteriores de ese modelo)" id="as-scope" value={scope} onChange={(e) => setScope(e.target.value as "unreviewed" | "all")}><option value="unreviewed">solo sin revisar</option><option value="all">todas</option></select></label>
        <label>Puntaje mínimo <input title="Puntaje mínimo de las cajas que el modelo propone. Más bajo = más cajas y más ruido. Un flujo trae el mínimo de cada fuente" disabled={isFlow} type="text" id="as-min" value={minScore} onChange={(e) => setMinScore(e.target.value)} style={{ width: 60 }} /></label>
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
        <button title={isFlow ? "La búsqueda de imágenes inciertas necesita un modelo (no un flujo)" : "Busca las imágenes sin revisar donde el modelo duda o discrepa del detector de apariencia: son las que más aportan al etiquetarlas"} id="as-find" disabled={find.isPending || isFlow} onClick={() => find.mutate()}>Buscar inciertas</button>
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

interface Row { id?: string; name: string; color: string; shortcut: string; negative: boolean; key?: string; digit?: string }
const rowsOf = (p: Project): Row[] => p.config.classes.map((c) => ({ id: c.id, name: c.name, color: c.color, shortcut: (c.digit ?? c.key ?? "").toUpperCase(), negative: !!c.negative, key: c.key, digit: c.digit }));
const NEW_COLORS = ["#f59e0b", "#06b6d4", "#ec4899", "#84cc16", "#6366f1", "#14b8a6"];

/** Nombre, descripción y etiquetas (clases) del proyecto. Las etiquetas existentes conservan su id al renombrarse. */
function Settings({ project, isAdmin }: { project: Project; isAdmin: boolean }) {
  const qc = useQueryClient();
  const [name, setName] = useState(project.name);
  const [desc, setDesc] = useState(project.config.description ?? "");
  const [rows, setRows] = useState<Row[]>(() => rowsOf(project));
  const [mode, setMode] = useState<WorkMode>(project.config.mode ?? "review");
  const [msg, setMsg] = useState<{ t: string; ok: boolean } | null>(null);
  const reset = () => { setName(project.name); setDesc(project.config.description ?? ""); setRows(rowsOf(project)); setMode(project.config.mode ?? "review"); };
  useEffect(() => { reset(); setMsg((m) => m); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [project]);
  const dirty = mode !== (project.config.mode ?? "review") || name !== project.name || desc !== (project.config.description ?? "") || JSON.stringify(rows) !== JSON.stringify(rowsOf(project));
  const set = (i: number, patch: Partial<Row>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const save = useMutation({
    mutationFn: () => api.updateProject(project.id, {
      name: name.trim(), description: desc, mode,
      classes: rows.map((r) => {
        const same = r.id && r.shortcut === (r.digit ?? r.key ?? "").toUpperCase();
        const sc = r.shortcut.trim().toLowerCase();
        return { id: r.id, name: r.name.trim(), color: r.color, negative: r.negative,
          ...(same ? { key: r.key, digit: r.digit } : /^[1-9]$/.test(sc) ? { digit: sc } : sc ? { key: sc.slice(0, 1) } : {}) };
      }),
    }),
    onSuccess: () => { setMsg({ t: "Cambios guardados.", ok: true }); void qc.invalidateQueries({ queryKey: ["projects"] }); },
    onError: (e) => setMsg({ t: (e as Error).message, ok: false }),
  });
  const bad = !name.trim() || rows.some((r) => !r.name.trim()) || !rows.some((r) => !r.negative);
  const dis = !isAdmin;
  return (
    <div id="config" className="stack">
      {dis && <p className="mut">Solo un administrador puede cambiar la configuración del proyecto.</p>}
      <div className="grid2 cfg-top">
        <section className="card">
          <h2><span>Información general</span></h2>
          <label className="fld">Nombre del proyecto<input id="cfg-name" type="text" maxLength={128} disabled={dis} value={name} onChange={(e) => setName(e.target.value)} /></label>
          <label className="fld">Descripción<textarea id="cfg-desc" rows={4} maxLength={1000} disabled={dis} value={desc} onChange={(e) => setDesc(e.target.value)} /></label>
        </section>
        <section className="card cover">
          <h2><span>Portada del proyecto</span></h2>
          {project.cover_frame_id ? <img src={api.thumbUrl(project.cover_frame_id)} alt="Primera imagen del proyecto" /> : <div className="nocover"><Icon name="image" size={32} /></div>}
          <small className="mut">Se usa la primera imagen del proyecto.</small>
        </section>
      </div>
      <section className="card" id="cfg-mode">
        <h2><span>Modalidad de trabajo<small>Cómo se decide cuál es la versión definitiva de las etiquetas de cada imagen.</small></span></h2>
        <ModePicker value={mode} onChange={setMode} disabled={dis} />
        {mode !== (project.config.mode ?? "review") && <p className="note warnt" id="cfg-mode-warn"><Icon name="alert" size={15} />{mode === "collab"
          ? "Al pasar a colaborativa, para cada imagen valdrá la última versión editada; las decisiones del revisor se conservan pero dejan de aplicarse."
          : "Al volver a «con revisión», la última versión de cada persona vuelve a contar como una revisión propia: pueden aparecer desacuerdos por resolver."}</p>}
      </section>
      <div className="grid2 cfg-labels">
        <section className="card">
          <h2><span>Etiquetas de anotación<small>Estas clases estarán disponibles al dibujar y clasificar cajas.</small></span></h2>
          <table className="tbl" id="cfg-classes">
            <thead><tr><th>Etiqueta</th><th>Color</th><th>Atajo</th><th>Tipo</th><th /></tr></thead>
            <tbody>{rows.map((r, i) => (
              <tr key={r.id ?? `n${i}`} data-class={r.id ?? "new"}>
                <td><input className="c-name" type="text" maxLength={40} disabled={dis} value={r.name} onChange={(e) => set(i, { name: e.target.value })} aria-label="Nombre de la etiqueta" /></td>
                <td><label className="colorfld"><input className="c-color" type="color" disabled={dis} value={r.color} onChange={(e) => set(i, { color: e.target.value })} aria-label="Color de la etiqueta" /><code>{r.color.toUpperCase()}</code></label></td>
                <td><input className="c-key" type="text" maxLength={1} disabled={dis} value={r.shortcut} onChange={(e) => set(i, { shortcut: e.target.value.toUpperCase() })} aria-label="Atajo de teclado" title="Una tecla: un dígito (1–9) o una letra" /></td>
                <td><select className="c-neg" disabled={dis} value={r.negative ? "neg" : "obj"} onChange={(e) => set(i, { negative: e.target.value === "neg" })} aria-label="Tipo de etiqueta" title="Objeto: lo que se detecta. Negativa: marca una caja como «no es» y descarta la propuesta"><option value="obj">Objeto</option><option value="neg">Negativa</option></select></td>
                <td><button className="iconbtn3 c-del" disabled={dis || rows.length < 2} title="Quitar esta etiqueta (si ya hay cajas con ella, el servidor no lo permite)" aria-label="Quitar etiqueta" onClick={() => setRows(rows.filter((_, j) => j !== i))}><Icon name="trash" size={16} /></button></td>
              </tr>
            ))}</tbody>
          </table>
          <button className="btn-outline add" id="cfg-add" disabled={dis || rows.length >= 12} onClick={() => setRows([...rows, { name: "", color: NEW_COLORS[rows.length % NEW_COLORS.length], shortcut: "", negative: false }])}><Icon name="plus" size={16} /> Añadir etiqueta</button>
          <p className="note"><Icon name="info" size={15} /> Los nombres y colores se muestran en el editor. La clase negativa descarta propuestas.</p>
          <p className="note warnt"><Icon name="alert" size={15} /> Las etiquetas en uso conservarán su identificador al cambiar de nombre.</p>
        </section>
        <section className="card">
          <h2><span>Vista previa</span></h2>
          <div className="chips">{rows.filter((r) => r.name.trim()).map((r, i) => <span key={i} className="chip2" style={{ background: r.color + "26", color: "#2b2029" }}><i className="dot" style={{ background: r.color }} />{r.name}{r.shortcut && <kbd>{r.shortcut}</kbd>}</span>)}</div>
        </section>
      </div>
      <div className={"savebar" + (dirty ? " dirty" : "")} id="cfg-bar">
        <span className={msg ? (msg.ok ? "okmsg" : "err") : dirty ? "warnt" : "mut"} id="cfg-msg">{msg ? msg.t : dirty ? "Cambios sin guardar" : "Sin cambios"}</span>
        <span className="grow" />
        <button className="btn-outline" id="cfg-cancel" disabled={!dirty || save.isPending} onClick={() => { reset(); setMsg(null); }}>Cancelar</button>
        <button className="primary" id="cfg-save" disabled={dis || !dirty || bad || save.isPending} onClick={() => save.mutate()}>Guardar cambios</button>
      </div>
    </div>
  );
}

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
    <div className="grid2 team-grid" id="team">
     <div className="stack">
      <div className="card">
      <h2><span>Miembros</span></h2>
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
      </div>
      <div className="card">
      <h2><span>Añadir persona<small>Si el usuario no existe se crea con la contraseña inicial (que debería cambiar al entrar). Si ya existe, solo se le da acceso a este proyecto.</small></span></h2>
      <div className="formrow">
        <input title="Nombre de usuario (2 a 64 caracteres: letras, números, punto, guion o guion bajo)" type="text" id="tm-user" placeholder="usuario" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} />
        <input title="Nombre para mostrar (opcional)" type="text" id="tm-name" placeholder="nombre (opcional)" value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} />
        <input title="Contraseña inicial, obligatoria si el usuario no existe (mínimo 8 caracteres). Si ya existe, déjala vacía" type="password" id="tm-pass" placeholder="contraseña inicial" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
        <select title="Rol: administrador (todo), revisor (decide etiquetas finales y opera), etiquetador (etiqueta) o lector (solo mira)" id="tm-role" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{Object.entries(ROLE_TEXT).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <button title="Da acceso al proyecto; si el usuario no existe lo crea" className="primary" id="tm-add" disabled={!f.username || add.isPending} onClick={() => add.mutate()}>Añadir al proyecto</button>
      </div>
      </div>
     </div>
     <aside className="card how">
      <h2><span>Roles del proyecto</span></h2>
      <ol className="roles">
        <li><div><strong>Administrador</strong><span>Gestiona el proyecto, sus miembros y la configuración.</span></div></li>
        <li><div><strong>Revisor</strong><span>{project.config.mode === "collab" ? "Etiqueta y opera modelos, datasets y entrenamientos." : "Compara anotaciones y decide versiones finales."}</span></div></li>
        <li><div><strong>Etiquetador</strong><span>Anota y revisa sus imágenes.</span></div></li>
        <li><div><strong>Lector</strong><span>Consulta el proyecto sin editar.</span></div></li>
      </ol>
     </aside>
    </div>
  );
}

function System({ project, isAdmin, superuser }: { project: Project; isAdmin: boolean; superuser: boolean }) {
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
    <div className="stack" id="system">
      {msg && <p className={msg.ok ? "okmsg" : "err"} id="sys-msg">{msg.t}</p>}
      {isAdmin && (<>
        <div className="grid2">
          <section className="card">
            <h2><span>Almacenamiento del proyecto</span></h2>
            {u && <p className="bigsize">{mb(u.owned_bytes)} <span className={"pill " + (u.over_quota ? "warn" : "ok")}>{u.quota_bytes ? (u.over_quota ? "Cuota superada" : `Cuota ${mb(u.quota_bytes)}`) : "Sin límite"}</span></p>}
            {u && (
              <table className="tbl" id="usage-tbl"><tbody>
                <tr><td>Imágenes · {u.images.files}</td><td className="num">{mb(u.images.bytes)}</td></tr>
                <tr><td>Modelos</td><td className="num">{mb(u.models.bytes)}</td></tr>
                <tr><td>Exportaciones</td><td className="num">{mb(u.exports.bytes)}</td></tr>
                <tr><td><b>Cuenta para la cuota</b></td><td className="num" id="usage-owned"><b>{mb(u.owned_bytes)}</b>{u.quota_bytes ? ` de ${mb(u.quota_bytes)}` : " (sin cuota)"}{u.over_quota ? " — SUPERADA" : ""}</td></tr>
              </tbody></table>
            )}
            {u && <div className="callout infoc"><Icon name="info" size={18} /><span><b>Videos originales externos · {u.videos.files} · {mb(u.videos.bytes)}</b><br />No cuentan para la cuota del proyecto.</span></div>}
            <label className="fld">Cuota del proyecto (GB, vacío = sin límite)
              <span className="inline"><input title="Límite de disco que puede ocupar lo que esta plataforma escribe para el proyecto (imágenes, modelos, exportaciones). Vacío: sin límite" type="text" id="sys-quota" placeholder="Sin límite" value={quota} onChange={(e) => setQuota(e.target.value)} />
                <button className="btn-outline" title="Guardar la cuota; al superarla no se aceptan imágenes nuevas" id="sys-quota-save" onClick={() => saveQuota.mutate()}>Guardar cuota</button></span></label>
          </section>
          <section className="card">
            <h2><span>Estado del servicio</span></h2>
            <div className="tiles3">
              <div><b>{sys.data?.jobs.queued ?? 0}</b><span>En cola</span></div>
              <div><b>{sys.data?.jobs.running ?? 0}</b><span>En curso</span></div>
              <div><b>{sys.data?.live_active ?? 0}</b><span>Sesiones en vivo</span></div>
            </div>
            {sys.data && <p className="mut" id="sys-jobs">Trabajos en cola: <b>{sys.data.jobs.queued}</b> · en curso: <b>{sys.data.jobs.running}</b> · sesiones en vivo activas: <b>{sys.data.live_active}</b></p>}
            {sys.data && !sys.data.jobs.queued && !sys.data.jobs.running && <div className="callout okc"><Icon name="checkCircle" size={20} /><b>Sin trabajos activos</b></div>}
            <h3>Copia del proyecto</h3>
            <p className="mut">Descarga todo lo etiquetado (etiquetas, revisiones, asignaciones, datasets, equipo) en un archivo. No incluye imágenes ni videos. Se restaura con <code>python -m app.cli restore-project</code>.</p>
            <p><a className="btn-outline linkbtn" id="sys-backup" href={api.backupUrl(project.id)} download>Descargar copia del proyecto</a></p>
          </section>
        </div>
        <section className="card">
          <h2><span>Actividad reciente</span></h2>
          <table className="tbl"><thead><tr><th>Cuándo</th><th>Usuario</th><th>Acción</th></tr></thead>
            <tbody>{(sys.data?.recent ?? []).map((r, i) => <tr key={i}><td>{new Date(r.at).toLocaleString()}</td><td>{r.user ?? "—"}</td><td>{r.action}{r.target ? ` · ${r.target}` : ""}</td></tr>)}</tbody></table>
        </section>
      </>)}
      {superuser && <Workers />}
      <section className="card">
        <h2><span>Mi contraseña</span></h2>
        <div className="formrow">
          <input title="Tu contraseña actual" type="password" id="pw-old" placeholder="actual" value={pw.old} onChange={(e) => setPw({ ...pw, old: e.target.value })} />
          <input title="Tu nueva contraseña (mínimo 8 caracteres)" type="password" id="pw-new" placeholder="nueva (mín. 8)" value={pw.nw} onChange={(e) => setPw({ ...pw, nw: e.target.value })} />
          <button title="Cambiar tu contraseña" id="pw-go" disabled={!pw.old || pw.nw.length < 8} onClick={() => chg.mutate()}>Cambiar mi contraseña</button>
        </div>
      </section>
    </div>
  );
}

const ago = (iso: string | null) => {
  if (!iso) return "nunca";
  const m = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  return m < 1 ? "hace instantes" : m < 60 ? `hace ${m} min` : m < 1440 ? `hace ${Math.round(m / 60)} h` : `hace ${Math.round(m / 1440)} d`;
};

/** Equipos con GPU que ejecutan entrenamientos, predicciones y sesiones en vivo (solo administración del sistema). */
function Workers() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["workers"], queryFn: api.workers, refetchInterval: 5000 });
  const [name, setName] = useState("");
  const [created, setCreated] = useState<{ name: string; token: string } | null>(null);
  const [err, setErr] = useState("");
  const create = useMutation({
    mutationFn: () => api.createWorker(name.trim()),
    onSuccess: (w) => { setCreated({ name: w.name, token: w.token }); setName(""); setErr(""); void qc.invalidateQueries({ queryKey: ["workers"] }); },
    onError: (e) => setErr((e as Error).message),
  });
  const revoke = useMutation({ mutationFn: (id: number) => api.revokeWorker(id), onSuccess: () => qc.invalidateQueries({ queryKey: ["workers"] }) });
  const mode = q.data?.mode ?? "local";
  const MODE: Record<string, string> = { local: "Local: la GPU es la de este servidor", remote: "Remoto: solo workers registrados", auto: "Automático: un worker si hay uno en línea; si no, este servidor" };
  const env = created ? `VEYRA_API_URL=${window.location.origin}\nVEYRA_WORKER_NAME=${created.name}\nVEYRA_WORKER_TOKEN=${created.token}` : "";
  return (
    <section className="card" id="workers">
      <h2><span className="hic"><Icon name="cpu" size={17} /></span><span>Workers de GPU<small>Equipos que ejecutan los trabajos pesados. Modo de ejecución: <b id="gpu-mode">{MODE[mode] ?? mode}</b> (se cambia con LABELHUB_GPU_MODE).</small></span></h2>
      <table className="tbl" id="workers-list">
        <thead><tr><th>Nombre</th><th>Estado</th><th>GPU</th><th>Software</th><th className="num">Trabajos</th><th>Visto</th><th /></tr></thead>
        <tbody>{(q.data?.workers ?? []).map((w) => (
          <tr key={w.id} data-worker={w.name}>
            <td>{w.name}</td>
            <td><span className={"pill " + (!w.active ? "" : w.online ? "ok" : "warn")}><i className="dot" />{!w.active ? "Revocado" : w.online ? "En línea" : "Sin conexión"}</span></td>
            <td>{w.capabilities.gpus?.[0] ? `${w.capabilities.gpus[0].name} · ${Math.round(w.capabilities.gpus[0].memory_mb / 1024)} GB` : "—"}</td>
            <td>{w.capabilities.torch ? `torch ${w.capabilities.torch}` : "—"}{w.capabilities.ultralytics ? ` · ultralytics ${w.capabilities.ultralytics}` : ""}</td>
            <td className="num">{w.running_jobs}</td>
            <td>{ago(w.last_seen)}</td>
            <td>{w.active && <button className="btn-outline" title="Revoca su token: deja de poder conectarse y sus trabajos en curso se dan por perdidos" onClick={() => { if (window.confirm(`¿Revocar el worker «${w.name}»?`)) revoke.mutate(w.id); }}>Revocar</button>}</td>
          </tr>
        ))}
        {!q.data?.workers.length && <tr><td colSpan={7} className="emptyrow">Aún no hay workers registrados.</td></tr>}</tbody>
      </table>
      <div className="formrow">
        <label className="fld">Registrar un worker<input id="wk-name" type="text" placeholder="p. ej. gpu-rtx4080" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <button className="primary" id="wk-create" disabled={name.trim().length < 2 || create.isPending} onClick={() => create.mutate()}>Registrar</button>
      </div>
      {err && <p className="err" id="wk-err">{err}</p>}
      {created && (
        <div className="callout warnc" id="wk-token" style={{ flexDirection: "column", alignItems: "stretch" }}>
          <b>Token de «{created.name}»: cópialo ahora, no se vuelve a mostrar.</b>
          <pre style={{ margin: "6px 0", whiteSpace: "pre-wrap", wordBreak: "break-all", fontSize: 12 }}>{env}</pre>
          <span className="formrow"><button className="btn-outline" onClick={() => void navigator.clipboard?.writeText(env)}>Copiar</button><button className="btn-outline" onClick={() => setCreated(null)}>Ya lo guardé</button></span>
        </div>
      )}
    </section>
  );
}

const IDLE_CHOICES: [number, string][] = [[15, "15 min"], [30, "30 min"], [45, "45 min"], [60, "1 h"], [120, "2 h"], [240, "4 h"], [0, "Nunca"]];

function Copy({ text, id }: { text: string; id?: string }) {
  const [ok, setOk] = useState(false);
  return <button id={id} className="btn-outline sm" onClick={() => { void navigator.clipboard?.writeText(text); setOk(true); window.setTimeout(() => setOk(false), 1500); }}><Icon name={ok ? "check" : "copy"} size={14} /> {ok ? "Copiado" : "Copiar"}</button>;
}

/** Espacio de trabajo del proyecto: un contenedor con SSH en el equipo con GPU, para abrirlo desde VS Code. */
function WorkspaceTab({ project, me }: { project: Project; me: string }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["workspace", project.id], queryFn: () => api.workspace(project.id), refetchInterval: 3000 });
  const [idle, setIdle] = useState<number | null>(null);
  const [cpus, setCpus] = useState<number | null>(null);
  const [mem, setMem] = useState<string | null>(null);
  const [keys, setKeys] = useState(false);
  const [err, setErr] = useState("");
  const w = q.data;
  const start = useMutation({
    mutationFn: () => api.startWorkspace(project.id, { idle_minutes: idle ?? w?.idle_minutes, cpus: cpus ?? w?.cpus, memory: mem ?? w?.memory }),
    onSuccess: (d) => { setErr(""); qc.setQueryData(["workspace", project.id], d); },
    onError: (e) => setErr((e as Error).message),
  });
  const stop = useMutation({ mutationFn: () => api.stopWorkspace(project.id), onSuccess: (d) => qc.setQueryData(["workspace", project.id], d) });
  if (q.isLoading) return <div className="center">Cargando…</div>;
  if (!w) return <div className="center err">{(q.error as Error)?.message ?? "Sin datos"}</div>;
  const busy = w.status === "starting" || start.isPending;
  const noKey = w.keys?.without_key.includes(me);
  return (
    <div id="workspace" className="stack">
      <section className="card">
        <h2><span className="hic"><Icon name="terminal" size={17} /></span><span>Espacio de {project.name}<small>Contenedor en el equipo con GPU: tu código en <code>/workspace/project</code> (git), los datos de Veyra en <code>/data</code> (solo lectura).</small></span>
          <span id="ws-state" className={"pill " + (w.status === "running" ? "ok" : w.status === "failed" ? "warn" : "")} style={{ marginLeft: "auto" }}><i className="dot" />{{ stopped: "Apagado", starting: "Arrancando…", running: "Encendido", failed: "Falló" }[w.status]}</span></h2>
        {!w.worker_available && w.status === "stopped" && <div className="callout warnc" id="ws-noworker"><Icon name="alert" size={18} /><span>No hay ningún worker de GPU en línea que ofrezca espacios de trabajo.</span></div>}
        {noKey && <div className="callout warnc" id="ws-nokey"><Icon name="key" size={18} /><span>Aún no tienes una llave SSH registrada: sin ella no podrás entrar.</span><button className="btn-outline" id="ws-addkey" onClick={() => setKeys(true)}>Registrar mi llave</button></div>}
        {w.status === "failed" && <div className="callout warnc" id="ws-error"><Icon name="alert" size={18} /><span>{w.error || "No se pudo encender el espacio."}</span></div>}
        {w.status === "stopped" && w.last_stop && <p className="note" id="ws-last"><Icon name="info" size={15} /> {w.last_stop === "idle" ? "Se apagó solo por inactividad." : "Se apagó."} {w.autosave && !w.autosave.endsWith("sin cambios") ? "Tu código quedó guardado en una copia automática (git)." : ""}</p>}
        {w.status === "stopped" || w.status === "failed" ? (
          <>
            <div className="formrow">
              <label className="fld">Se apaga tras<select id="ws-idle" value={idle ?? w.idle_minutes} onChange={(e) => setIdle(+e.target.value)}>{IDLE_CHOICES.map(([v, l]) => <option key={v} value={v}>{l === "Nunca" ? "Nunca (lo apago yo)" : `${l} sin uso`}</option>)}</select></label>
              <label className="fld">CPU<input id="ws-cpus" type="number" min={1} max={16} step={1} value={cpus ?? w.cpus} onChange={(e) => setCpus(+e.target.value)} style={{ width: 80 }} /></label>
              <label className="fld">Memoria<input id="ws-mem" type="text" value={mem ?? w.memory} onChange={(e) => setMem(e.target.value)} style={{ width: 80 }} /></label>
              <button className="primary" id="ws-start" disabled={busy || !w.worker_available || noKey} onClick={() => start.mutate()} title="Crea el contenedor (o lo retoma) y deja listo el acceso por SSH"><Icon name="power" size={16} /> Encender</button>
            </div>
            <p className="note"><Icon name="info" size={15} /> «Sin uso» = ninguna ventana de VS Code conectada ni procesos con carga. Al apagarse se guarda una copia de tu código y todo lo de <code>/workspace</code> se conserva.</p>
          </>
        ) : (
          <div className="formrow">
            {w.status === "starting" && <span className="mut" id="ws-starting"><Icon name="info" size={15} /> Preparando el contenedor… la primera vez tarda un poco más.</span>}
            <button className="btn-danger solid" id="ws-stop" onClick={() => stop.mutate()}><Icon name="power" size={16} /> Apagar</button>
          </div>
        )}
        {err && <p className="err" id="ws-err">{err}</p>}
      </section>

      {w.status === "running" && (
        <section className="card" id="ws-connect">
          <h2><span>Cómo conectarte</span></h2>
          <ol className="steps">
            <li><b>Tu llave.</b> Debe estar registrada en <button className="linkish" onClick={() => setKeys(true)}>Mis llaves SSH</button> antes de encender (si la añades ahora, apaga y vuelve a encender).</li>
            <li><b>Configuración de SSH.</b> Pega esto en tu <code>~/.ssh/config</code> y cambia <code>TU_LLAVE_PRIVADA</code> por el nombre de tu archivo de llave privada:
              <pre id="ws-sshconfig">{w.ssh_config}</pre><Copy text={w.ssh_config ?? ""} id="ws-copy-config" /></li>
            <li><b>VS Code.</b> Con la extensión <i>Remote - SSH</i>: «Remote-SSH: Connect to Host…» → <code>veyra-{w.slug}</code> y abre la carpeta <code>{w.workdir}</code>. O desde un terminal: <code id="ws-cmd">{w.vscode_cmd}</code> <Copy text={w.vscode_cmd ?? ""} /> · <a id="ws-vscode" href={w.vscode_url}>abrir en VS Code <Icon name="external" size={13} /></a></li>
            <li><b>Dependencias nuevas.</b> <code>pip install paquete</code> (queda en el entorno del proyecto) y <code>veyra-apt install paquete</code> para paquetes del sistema. Anótalas en <code>requirements.txt</code> y haz commit.</li>
            <li><b>Claude.</b> Instala la extensión o la CLI de Claude Code dentro de esa ventana remota: trabajará sobre los mismos archivos y la misma GPU.</li>
          </ol>
          <p className="note"><Icon name="info" size={15} /> Apagado automático tras {w.idle_minutes ? `${w.idle_minutes} min` : "—(nunca)"} sin uso. Acceso: {w.keys?.with_key.join(", ") || "—"}{w.keys?.without_key.length ? ` · sin llave: ${w.keys.without_key.join(", ")}` : ""}.</p>
        </section>
      )}
      {keys && <SshKeysModal onClose={() => setKeys(false)} />}
    </div>
  );
}
