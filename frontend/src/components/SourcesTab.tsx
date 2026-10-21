import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type Project, type SourceImage, type SourceVideo } from "../api/client";
import { useUploads } from "../uploads";
import Icon from "./Icon";
import UploadModal from "./UploadModal";
import { AutoSampler, SamplingProgress } from "./VideoSampler";

const PAGE = 60;
const fmtBytes = (n: number | null) => (n === null ? "—" : n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);
const fmtDur = (s: number | null) => (s ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}` : "—");

/** Gestión → Archivos fuente: los videos (de ellos se muestrea) y las imágenes que no salen de un video. La carga va en una ventana aparte. */
export default function SourcesTab({ project, canImport }: { project: Project; canImport: boolean }) {
  const pid = project.id;
  const [modal, setModal] = useState(false);
  const [autoFor, setAutoFor] = useState<number | null>(null);
  const [runs, setRuns] = useState<{ id: number; label: string }[]>([]);
  const [batch, setBatch] = useState("");
  const [extra, setExtra] = useState<SourceImage[]>([]);
  const [offset, setOffset] = useState(0);
  const jobs = useUploads();
  const vids = useQuery({ queryKey: ["sources", pid, "videos"], queryFn: () => api.sourceVideos(pid) });
  const imgs = useQuery({ queryKey: ["sources", pid, "images", batch, 0], queryFn: () => api.sourceImages(pid, 0, PAGE, batch || undefined) });
  const more = useQuery({ queryKey: ["sources", pid, "images", batch, offset], queryFn: () => api.sourceImages(pid, offset, PAGE, batch || undefined), enabled: offset > 0 });
  useEffect(() => { setExtra([]); setOffset(0); }, [batch, jobs.length]);
  useEffect(() => { if (more.data && offset > 0) setExtra((e) => (e.some((x) => x.id === more.data!.items[0]?.id) ? e : [...e, ...more.data!.items])); }, [more.data, offset]);
  const items = [...(imgs.data?.items ?? []), ...extra];
  const total = imgs.data?.total ?? 0;
  const openManual = (v: SourceVideo) => { window.location.hash = `p=${pid}&v=sample&vid=${v.id}`; };

  return (
    <div id="sources">
      <div className="formrow srchead">
        <p className="mut" style={{ margin: 0 }}>Material de origen del proyecto. De los <b>videos</b> se toman imágenes por muestreo; las <b>imágenes sueltas</b> son las que no salen de un video.</p>
        {canImport && <button className="primary" id="src-upload" onClick={() => setModal(true)}><Icon name="upload" size={16} /> Cargar archivos</button>}
      </div>
      {modal && <UploadModal projectId={pid} onClose={() => setModal(false)} />}

      <div className="panel" id="src-videos-panel">
        <h2>Videos</h2>
        <table className="tbl" id="imp-videos">
          <thead><tr><th>Video</th><th>Origen</th><th>Duración</th><th>Tamaño</th><th>Imágenes</th><th></th></tr></thead>
          <tbody>
            {(vids.data ?? []).map((v) => (
              <tr key={v.id} data-video={v.name}>
                <td><b>{v.name}</b></td><td>{v.origin === "carga" ? "Cargado" : "Importado"}</td><td>{fmtDur(v.duration)}</td><td>{fmtBytes(v.bytes)}</td><td>{v.n_frames}</td>
                <td>{canImport && (v.has_file ? <>
                  <button className="btn imp-auto" onClick={() => setAutoFor(autoFor === v.id ? null : v.id)}>Muestreo automático</button>{" "}
                  <button className="btn imp-manual" disabled={v.first_frame_id === null} onClick={() => openManual(v)}>Muestreo manual</button>
                  {autoFor === v.id && <AutoSampler video={{ id: v.id, name: v.name, has_file: true, n_frames: v.n_frames, first_frame_id: v.first_frame_id, duration: v.duration }} onDone={() => undefined} onRun={(id) => setRuns((a) => [...a, { id, label: v.name }])} />}
                </> : <span className="mut">sin archivo original</span>)}</td>
              </tr>
            ))}
            {!vids.data?.length && <tr><td colSpan={6} className="emptyrow">Aún no hay videos en el proyecto{canImport ? ": usa «Cargar archivos»." : "."}</td></tr>}
          </tbody>
        </table>
        {runs.map((r) => <SamplingProgress key={r.id} runId={r.id} label={r.label} />)}
      </div>

      <div className="panel" id="src-images-panel">
        <div className="formrow" style={{ justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>Imágenes sueltas <small className="mut">({total})</small></h2>
          {!!imgs.data?.batches.length && (
            <label>Lote <select id="src-batch" value={batch} onChange={(e) => setBatch(e.target.value)}>
              <option value="">Todos</option>
              {imgs.data.batches.map((b) => <option key={b.name} value={b.name}>{b.name} · {b.origin} ({b.count})</option>)}
            </select></label>
          )}
        </div>
        <p className="mut">Imágenes cargadas directamente, capturas de sesiones en vivo y las de videos cuyo original no está disponible. Las imágenes que salen de un video con archivo se ven en el etiquetado.</p>
        {!items.length && !imgs.isLoading && <p className="emptyrow" id="src-noimgs">No hay imágenes sueltas{canImport ? ": cárgalas con «Cargar archivos»." : "."}</p>}
        <div className="srcgrid" id="src-images">
          {items.map((i) => (
            <figure key={i.id} className="srcitem" title={`${i.name} · ${i.width}×${i.height} · ${fmtBytes(i.bytes)} · imagen #${i.position + 1} del carrusel`}>
              <img src={api.thumbUrl(i.id)} loading="lazy" alt="" />
              <figcaption><b>{i.name}</b><small>{i.batch} · {i.origin}</small></figcaption>
            </figure>
          ))}
        </div>
        {items.length < total && <div className="formrow"><button className="btn" id="src-more" disabled={more.isFetching} onClick={() => setOffset(items.length)}>{more.isFetching ? "Cargando…" : `Ver más (${items.length} de ${total})`}</button></div>}
      </div>
    </div>
  );
}
