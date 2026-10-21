import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { api, type ImportImagesResult, type ImportVideoResult } from "../api/client";
import Icon from "./Icon";

const VIDEO_OK = /\.(mp4|mov|m4v|avi|mkv|webm)$/i;
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const fmt = (n: number) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${(n / 1e6).toFixed(1)} MB`);

function Drop({ id, accept, multiple, onFiles, children }: { id: string; accept: string; multiple: boolean; onFiles: (f: File[]) => void; children: React.ReactNode }) {
  const ref = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div className={"dropzone" + (over ? " over" : "")} id={id + "-drop"}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); onFiles([...e.dataTransfer.files]); }}
      onClick={() => ref.current?.click()}>
      <input ref={ref} id={id} type="file" hidden accept={accept} multiple={multiple} onChange={(e) => { onFiles([...(e.target.files ?? [])]); e.target.value = ""; }} />
      {children}
    </div>
  );
}

/** Gestión → Datos: agregar imágenes (con o sin etiquetas) y videos al proyecto. */
export default function ImportTab({ projectId, canImport }: { projectId: number; canImport: boolean }) {
  const qc = useQueryClient();
  const refresh = () => { for (const k of ["frames", "stats", "projects", "videos"]) void qc.invalidateQueries({ queryKey: [k] }); };
  const [imgs, setImgs] = useState<File[]>([]);
  const [reviewed, setReviewed] = useState(false);
  const [imgBusy, setImgBusy] = useState<number | null>(null);
  const [imgRes, setImgRes] = useState<ImportImagesResult | null>(null);
  const [imgErr, setImgErr] = useState<string | null>(null);
  const [vids, setVids] = useState<File[]>([]);
  const [samples, setSamples] = useState(8);
  const [vidBusy, setVidBusy] = useState<{ i: number; frac: number } | null>(null);
  const [vidRes, setVidRes] = useState<(ImportVideoResult | { name: string; error: string })[]>([]);

  const sendImages = async () => {
    setImgErr(null); setImgRes(null); setImgBusy(0);
    try { setImgRes(await api.importImages(projectId, imgs, reviewed, setImgBusy)); setImgs([]); refresh(); }
    catch (e) { setImgErr((e as Error).message); }
    finally { setImgBusy(null); }
  };
  const sendVideos = async () => {
    setVidRes([]);
    const out: (ImportVideoResult | { name: string; error: string })[] = [];
    for (let i = 0; i < vids.length; i++) {
      setVidBusy({ i, frac: 0 });
      try { out.push(await api.importVideo(projectId, vids[i], samples, (frac) => setVidBusy({ i, frac }))); }
      catch (e) { out.push({ name: vids[i].name, error: (e as Error).message }); }
      setVidRes([...out]);
    }
    setVidBusy(null); setVids([]); refresh();
  };
  const imgBytes = imgs.reduce((a, f) => a + f.size, 0), vidBytes = vids.reduce((a, f) => a + f.size, 0);

  if (!canImport) return <div className="panel"><p className="mut">Solo administración y revisión pueden cargar imágenes y videos.</p></div>;
  return (
    <div id="import">
      <div className="panel" id="import-images">
        <h2>Imágenes</h2>
        <p className="mut">Sube imágenes sueltas o un <b>.zip</b>. Si quieres cargarlas con etiquetas, agrega junto a ellas los archivos de etiquetas:
          <b> YOLO</b> (un .txt por imagen con el mismo nombre, más <code>classes.txt</code> o <code>data.yaml</code> con los nombres de clase) o <b>COCO</b> (un .json). Las clases se asignan por nombre
          a las del proyecto; sin lista de nombres, por el orden de las clases del proyecto. Quedan como tu trabajo y se pueden corregir en el editor.</p>
        <Drop id="imp-files" accept="image/*,.txt,.json,.yaml,.yml,.zip" multiple onFiles={(f) => setImgs([...imgs, ...f])}>
          <Icon name="image" size={26} /><b>Arrastra archivos aquí o haz clic para elegirlos</b>
          <small>jpg, png, bmp, webp · .txt · .json · .yaml · .zip</small>
        </Drop>
        {!!imgs.length && <p id="imp-summary">{imgs.length} archivos ({fmt(imgBytes)}) · {plural(imgs.filter((f) => /\.(jpe?g|png|bmp|webp)$/i.test(f.name)).length, "imagen", "imágenes")} · {imgs.filter((f) => /\.(txt|json|ya?ml)$/i.test(f.name)).length} de etiquetas o clases · {imgs.filter((f) => /\.zip$/i.test(f.name)).length} zip</p>}
        <div className="formrow">
          <label title="Marca las imágenes con etiquetas como revisadas (úsalo si las etiquetas ya son confiables)"><input type="checkbox" id="imp-reviewed" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} /> Marcar las que traen etiquetas como revisadas</label>
          <button className="primary" id="imp-send" disabled={!imgs.length || imgBusy !== null} onClick={() => void sendImages()}>{imgBusy !== null ? `Subiendo… ${Math.round(imgBusy * 100)} %` : "Cargar al proyecto"}</button>
          {!!imgs.length && imgBusy === null && <button className="btn" onClick={() => setImgs([])}>Vaciar</button>}
        </div>
        {imgErr && <p className="err" id="imp-err">{imgErr}</p>}
        {imgRes && (
          <div className="importnote" id="imp-result">
            <b>{plural(imgRes.images, "imagen cargada", "imágenes cargadas")}</b> ({plural(imgRes.with_labels, "con etiquetas", "con etiquetas")}, {plural(imgRes.boxes, "caja", "cajas")}) en el lote «{imgRes.batch}».
            {imgRes.warnings.map((w) => <div key={w}><Icon name="alert" size={14} /> {w}</div>)}
            {imgRes.skipped.map((w) => <div key={w} className="mut">Omitida: {w}</div>)}
          </div>
        )}
      </div>
      <div className="panel" id="import-videos">
        <h2>Videos</h2>
        <p className="mut">Sube uno o varios videos (mp4, mov, m4v, avi, mkv, webm). Quedan disponibles para el reproductor, las capturas, la ejecución de modelos y los flujos.
          Se extraen algunos fotogramas repartidos en el video como primeras imágenes para etiquetar.</p>
        <Drop id="imp-vfiles" accept="video/*,.mkv" multiple onFiles={(f) => setVids([...vids, ...f.filter((x) => VIDEO_OK.test(x.name))])}>
          <Icon name="camera" size={26} /><b>Arrastra videos aquí o haz clic para elegirlos</b>
          <small>mp4, mov, m4v, avi, mkv, webm</small>
        </Drop>
        {!!vids.length && <ul id="imp-vlist" className="mut">{vids.map((f, i) => <li key={i}>{f.name} · {fmt(f.size)}{vidBusy?.i === i ? ` · subiendo ${Math.round(vidBusy.frac * 100)} %` : ""}</li>)}</ul>}
        <div className="formrow">
          <label title="Cuántos fotogramas repartidos en el video se agregan como imágenes para etiquetar (0 = solo el primero)">Fotogramas a extraer <input type="number" id="imp-samples" min={0} max={200} value={samples} onChange={(e) => setSamples(Math.max(0, Math.min(200, +e.target.value)))} style={{ width: 70 }} /></label>
          <button className="primary" id="imp-vsend" disabled={!vids.length || vidBusy !== null} onClick={() => void sendVideos()}>{vidBusy ? `Procesando ${vidBusy.i + 1} de ${vids.length}…` : `Cargar ${vids.length || ""} video${vids.length === 1 ? "" : "s"} (${fmt(vidBytes)})`}</button>
        </div>
        {vidRes.map((r) => "error" in r
          ? <p key={r.name} className="err">{r.name}: {r.error}</p>
          : <div key={r.video_id} className="importnote"><b>{r.name}</b>: {r.frames} imágenes extraídas · {Math.round(r.duration)} s a {r.fps.toFixed(1)} fps.</div>)}
      </div>
    </div>
  );
}
