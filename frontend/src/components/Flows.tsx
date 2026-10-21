import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type Flow, type FlowGroup, type FlowLeaf, type FlowNode, type Project } from "../api/client";
import Icon from "./Icon";

const isGroup = (n: FlowNode): n is FlowGroup => "op" in n;
const BASE_NAMES: Record<string, string> = { "baseline:appearance": "Manchas por apariencia", "proposals:motion": "Movimiento (propuestas guardadas)", "proposals:blob": "Manchas guardadas en las imágenes" };

/** Fuentes que puede usar un flujo: modelos entrenados, detectores del proyecto y procedimientos de su espacio de trabajo. */
export function useCatalogue(project: Project) {
  const models = useQuery({ queryKey: ["models", project.id], queryFn: () => api.models(project.id) });
  const canProc = project.role === "admin" || project.role === "reviewer";
  const procs = useQuery({ queryKey: ["procedures", project.id], queryFn: () => api.procedures(project.id), enabled: canProc, staleTime: 60_000, retry: false });
  const names: Record<string, string> = { ...BASE_NAMES };
  (models.data ?? []).forEach((m) => { names[`model:${m.id}`] = m.name; });
  (procs.data?.procedures ?? []).forEach((p) => { names[`procedure:${p.name}`] = p.name; });
  const groups = [
    { label: "Modelos entrenados", options: (models.data ?? []).map((m) => ({ value: `model:${m.id}`, label: m.name })) },
    { label: "Detectores del proyecto", options: [...(project.config && (project.config as { proposer?: unknown }).proposer ? [{ value: "baseline:appearance", label: BASE_NAMES["baseline:appearance"] }] : []), { value: "proposals:motion", label: BASE_NAMES["proposals:motion"] }, { value: "proposals:blob", label: BASE_NAMES["proposals:blob"] }] },
    { label: "Procedimientos del espacio de trabajo", options: (procs.data?.procedures ?? []).map((p) => ({ value: `procedure:${p.name}`, label: p.name })) },
  ];
  return { models: models.data ?? [], procs: procs.data, names, groups };
}

export function describe(n: FlowNode, names: Record<string, string>): string {
  if (!isGroup(n)) return names[n.source] ?? n.source;
  const inner = n.inputs.map((c) => describe(c, names)).join(n.op === "or" ? " O " : " Y ");
  return n.inputs.length > 1 ? `(${inner})` : inner;
}

type Cat = ReturnType<typeof useCatalogue>;

function LeafEditor({ leaf, onChange, onRemove, cat }: { leaf: FlowLeaf; onChange: (l: FlowLeaf) => void; onRemove?: () => void; cat: Cat }) {
  const isProc = leaf.source.startsWith("procedure:");
  const [txt, setTxt] = useState(JSON.stringify(leaf.params ?? {}));
  const [bad, setBad] = useState(false);
  const known = cat.groups.some((g) => g.options.some((o) => o.value === leaf.source));
  return (
    <div className="fleaf">
      <select className="f-source" value={leaf.source} onChange={(e) => onChange({ source: e.target.value, min_score: leaf.min_score })} aria-label="Fuente de detecciones">
        {!known && <option value={leaf.source}>{cat.names[leaf.source] ?? leaf.source}</option>}
        {cat.groups.filter((g) => g.options.length).map((g) => <optgroup key={g.label} label={g.label}>{g.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</optgroup>)}
      </select>
      <label className="fmin" title="Puntaje mínimo de esta fuente (en su propia escala: los modelos van de 0 a 1)">mín.<input className="f-min" type="number" min={0} step={0.05} value={leaf.min_score ?? 0} onChange={(e) => onChange({ ...leaf, min_score: +e.target.value })} /></label>
      {isProc && <input className="f-ref" type="text" placeholder="versión: último commit" title="Etiqueta o commit del repositorio del espacio de trabajo; vacío = el último commit" value={leaf.ref ?? ""} onChange={(e) => onChange({ ...leaf, ref: e.target.value || null })} />}
      {isProc && <input className={"f-params" + (bad ? " bad" : "")} type="text" placeholder='parámetros {"k": 8}' title="Parámetros del procedimiento (JSON); reemplazan a los del manifiesto" value={txt} onChange={(e) => { setTxt(e.target.value); try { onChange({ ...leaf, params: JSON.parse(e.target.value || "{}") }); setBad(false); } catch { setBad(true); } }} />}
      {onRemove && <button className="iconbtn3" type="button" title="Quitar esta fuente" aria-label="Quitar fuente" onClick={onRemove}><Icon name="trash" size={15} /></button>}
    </div>
  );
}

function NodeEditor({ node, onChange, onRemove, cat, depth }: { node: FlowNode; onChange: (n: FlowNode) => void; onRemove?: () => void; cat: Cat; depth: number }) {
  if (!isGroup(node)) return <LeafEditor leaf={node} onChange={onChange} onRemove={onRemove} cat={cat} />;
  const first = cat.groups.flatMap((g) => g.options)[0]?.value ?? "proposals:motion";
  const set = (inputs: FlowNode[]) => onChange({ ...node, inputs });
  return (
    <div className={"fgroup " + node.op}>
      <div className="fhead">
        <div className="segmented sm" role="group" aria-label="Cómo se combinan">
          <button type="button" className={"f-or" + (node.op === "or" ? " on" : "")} onClick={() => onChange({ ...node, op: "or" })} title="O: junta las detecciones de todas las fuentes; las que se solapan se funden en una">Cualquiera (O)</button>
          <button type="button" className={"f-and" + (node.op === "and" ? " on" : "")} onClick={() => onChange({ ...node, op: "and" })} title="Y: deja solo las detecciones que TODAS las fuentes confirman">Todas (Y)</button>
        </div>
        <label className="fmin" title="Cuánto deben solaparse dos cajas para considerarse la misma (IoU)">solape<input className="f-iou" type="number" min={0.05} max={1} step={0.05} value={node.iou ?? 0.3} onChange={(e) => onChange({ ...node, iou: +e.target.value })} /></label>
        {onRemove && <button className="iconbtn3" type="button" title="Quitar este grupo" aria-label="Quitar grupo" onClick={onRemove}><Icon name="trash" size={15} /></button>}
      </div>
      {node.inputs.map((c, i) => (
        <NodeEditor key={i} node={c} cat={cat} depth={depth + 1} onChange={(n) => set(node.inputs.map((x, j) => (j === i ? n : x)))} onRemove={node.inputs.length > 1 ? () => set(node.inputs.filter((_, j) => j !== i)) : undefined} />
      ))}
      <div className="fadd">
        <button className="btn-outline sm f-addsrc" type="button" onClick={() => set([...node.inputs, { source: first }])}><Icon name="plus" size={13} /> Fuente</button>
        {depth < 3 && <button className="btn-outline sm f-addgrp" type="button" onClick={() => set([...node.inputs, { op: node.op === "or" ? "and" : "or", inputs: [{ source: first }] }])}><Icon name="plus" size={13} /> Grupo</button>}
      </div>
    </div>
  );
}

/** Crea, edita y borra los flujos de detección del proyecto: modelos, filtros y procedimientos combinados con O / Y. */
export default function FlowsModal({ project, onClose, onPick }: { project: Project; onClose: () => void; onPick?: (id: number) => void }) {
  const qc = useQueryClient();
  const cat = useCatalogue(project);
  const flows = useQuery({ queryKey: ["flows", project.id], queryFn: () => api.flows(project.id) });
  const canEdit = project.role === "admin" || project.role === "reviewer";
  const [edit, setEdit] = useState<{ id?: number; name: string; spec: FlowNode } | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") (edit ? setEdit(null) : onClose()); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [edit, onClose]);
  const save = useMutation({
    mutationFn: () => (edit!.id ? api.updateFlow(project.id, edit!.id, { name: edit!.name.trim(), spec: edit!.spec }) : api.createFlow(project.id, { name: edit!.name.trim(), spec: edit!.spec })),
    onSuccess: (f) => { void qc.invalidateQueries({ queryKey: ["flows", project.id] }); setEdit(null); setErr(""); if (onPick) { onPick(f.id); onClose(); } },
    onError: (e) => setErr((e as Error).message),
  });
  const del = useMutation({ mutationFn: (id: number) => api.deleteFlow(project.id, id), onSuccess: () => qc.invalidateQueries({ queryKey: ["flows", project.id] }) });
  const firstSrc = cat.groups.flatMap((g) => g.options)[0]?.value ?? "proposals:motion";
  return (
    <div id="flows-modal" className="modalback" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modalbox wide flowsbox" role="dialog" aria-label="Flujos de detección">
        <div className="hhead"><b>{edit ? (edit.id ? "Editar flujo" : "Nuevo flujo") : "Flujos de detección"}</b><button type="button" id="fl-x" title="Cerrar (Esc)" onClick={() => (edit ? setEdit(null) : onClose())}>×</button></div>
        {!edit && (
          <>
            <p className="mut">Un flujo combina modelos, detectores y procedimientos de tu espacio de trabajo: con <b>O</b> se juntan todas sus detecciones; con <b>Y</b> solo quedan las que todas confirman. Se pueden anidar grupos, por ejemplo «(modelo O manchas) Y filtro».</p>
            <table className="tbl" id="fl-list"><tbody>
              {(flows.data ?? []).map((f: Flow) => (
                <tr key={f.id} data-flow={f.name}><td><b>{f.name}</b><br /><small className="mut">{describe(f.spec, cat.names)}</small></td>
                  <td className="num">{onPick && <button className="btn-outline sm" onClick={() => { onPick(f.id); onClose(); }}>Usar</button>} {canEdit && <><button className="btn-outline sm fl-edit" onClick={() => setEdit({ id: f.id, name: f.name, spec: f.spec })}>Editar</button> <button className="btn-outline sm fl-del" onClick={() => { if (window.confirm(`¿Eliminar el flujo «${f.name}»?`)) del.mutate(f.id); }}>Eliminar</button></>}</td></tr>
              ))}
              {!flows.data?.length && <tr><td className="emptyrow">Aún no hay flujos en este proyecto.</td></tr>}
            </tbody></table>
            {canEdit && <div className="formrow end"><button className="primary" id="fl-new" onClick={() => setEdit({ name: "", spec: { op: "or", inputs: [{ source: firstSrc }] } })}><Icon name="plus" size={16} /> Nuevo flujo</button></div>}
          </>
        )}
        {edit && (
          <>
            <label className="fld">Nombre<input id="fl-name" type="text" maxLength={64} placeholder="p. ej. modelo y manchas" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></label>
            {cat.procs && !cat.procs.available && <p className="note"><Icon name="info" size={15} /> Para usar procedimientos del espacio de trabajo hace falta un worker en línea.</p>}
            {cat.procs?.error && <p className="note warnt"><Icon name="alert" size={15} /> {cat.procs.error}</p>}
            <div id="fl-tree"><NodeEditor node={edit.spec} cat={cat} depth={1} onChange={(n) => setEdit({ ...edit, spec: n })} /></div>
            <p className="flowtext" id="fl-text"><b>Resultado:</b> {describe(edit.spec, cat.names)}</p>
            {err && <p className="err" id="fl-err">{err}</p>}
            <div className="formrow end"><button type="button" className="btn-outline" id="fl-cancel" onClick={() => { setEdit(null); setErr(""); }}>Cancelar</button><button type="button" className="primary" id="fl-save" disabled={!edit.name.trim() || save.isPending} onClick={() => save.mutate()}>Guardar flujo</button></div>
          </>
        )}
      </div>
    </div>
  );
}

/** Elige qué corre: un modelo entrenado o un flujo guardado (con acceso a gestionarlos). */
export function DetectorSelect({ project, value, onChange, id, disabled }: { project: Project; value: string; onChange: (v: string) => void; id?: string; disabled?: boolean }) {
  const models = useQuery({ queryKey: ["models", project.id], queryFn: () => api.models(project.id) });
  const flows = useQuery({ queryKey: ["flows", project.id], queryFn: () => api.flows(project.id) });
  const [open, setOpen] = useState(false);
  return (
    <span className="detsel">
      <select id={id} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} title="Un modelo entrenado o un flujo que combina modelos, filtros y procedimientos con O / Y">
        <optgroup label="Modelos entrenados">{(models.data ?? []).map((m) => <option key={m.id} value={`model:${m.id}`}>{m.name}</option>)}</optgroup>
        {!!flows.data?.length && <optgroup label="Flujos (O / Y)">{flows.data.map((f) => <option key={f.id} value={`flow:${f.id}`}>{f.name}</option>)}</optgroup>}
      </select>
      <button type="button" className="btn-outline sm" id={id ? id + "-flows" : undefined} title="Crear o editar flujos de detección" disabled={disabled} onClick={() => setOpen(true)}>Flujos…</button>
      {open && <FlowsModal project={project} onClose={() => setOpen(false)} onPick={(fid) => onChange(`flow:${fid}`)} />}
    </span>
  );
}
