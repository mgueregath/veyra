import { useEffect, useRef, useState } from "react";
import type { SamplePlan } from "../api/client";
import { uploads } from "../uploads";
import Icon from "./Icon";

const VIDEO_OK = /\.(mp4|mov|m4v|avi|mkv|webm)$/i;
const IMG_OK = /\.(jpe?g|png|bmp|webp)$/i;
const LABEL_OK = /\.(txt|json|ya?ml)$/i;
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const fmt = (n: number) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${(n / 1e6).toFixed(1)} MB`);

function Drop({ id, accept, onFiles, children }: { id: string; accept: string; onFiles: (f: File[]) => void; children: React.ReactNode }) {
  const ref = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div className={"dropzone" + (over ? " over" : "")} id={id + "-drop"}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); onFiles([...e.dataTransfer.files]); }}
      onClick={() => ref.current?.click()}>
      <input ref={ref} id={id} type="file" hidden accept={accept} multiple onChange={(e) => { onFiles([...(e.target.files ?? [])]); e.target.value = ""; }} />
      {children}
    </div>
  );
}

/** Ventana para elegir qué cargar. Las subidas las lleva `uploads` (fuera de la ventana): se puede cerrar sin cancelarlas. */
export default function UploadModal({ projectId, onClose }: { projectId: number; onClose: () => void }) {
  const [kind, setKind] = useState<"images" | "video">("images");
  const [imgs, setImgs] = useState<File[]>([]);
  const [reviewed, setReviewed] = useState(false);
  const [vids, setVids] = useState<File[]>([]);
  const [mode, setMode] = useState<"auto" | "manual">("auto");
  const [interval, setInterval_] = useState(5);
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState("");
  const [sent, setSent] = useState(false);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  const sendImages = () => { uploads.images(projectId, imgs, reviewed); setImgs([]); setSent(true); };
  const sendVideos = () => {
    const plan: SamplePlan = { mode, interval, start, end: end === "" ? null : +end };
    vids.forEach((f) => uploads.video(projectId, f, plan));
    setVids([]); setSent(true);
  };
  const imgBytes = imgs.reduce((a, f) => a + f.size, 0), vidBytes = vids.reduce((a, f) => a + f.size, 0);

  return (
    <div id="upload-modal" className="modalback" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modalbox wide uploadbox" role="dialog" aria-label="Cargar archivos">
        <div className="hhead"><b>Cargar archivos fuente</b><button id="um-x" title="Cerrar (Esc): las subidas en curso continúan" onClick={onClose}>×</button></div>
        <div className="htabs" role="tablist">
          <button id="um-tab-images" role="tab" aria-selected={kind === "images"} className={kind === "images" ? "on" : ""} onClick={() => setKind("images")}>Imágenes</button>
          <button id="um-tab-video" role="tab" aria-selected={kind === "video"} className={kind === "video" ? "on" : ""} onClick={() => setKind("video")}>Videos</button>
        </div>
        {kind === "images" && (
          <div id="import-images">
            <p className="mut">Sube imágenes sueltas o un <b>.zip</b>. Para cargarlas con etiquetas, agrega los archivos de etiquetas: <b>YOLO</b> (un .txt por imagen con el mismo nombre, más <code>classes.txt</code> o <code>data.yaml</code>) o <b>COCO</b> (un .json). Las clases se asignan por nombre a las del proyecto; sin nombres, por el orden de las clases del proyecto.</p>
            <Drop id="imp-files" accept="image/*,.txt,.json,.yaml,.yml,.zip" onFiles={(f) => setImgs([...imgs, ...f])}>
              <Icon name="image" size={26} /><b>Arrastra archivos aquí o haz clic para elegirlos</b>
              <small>jpg, png, bmp, webp · .txt · .json · .yaml · .zip</small>
            </Drop>
            {!!imgs.length && <p id="imp-summary">{plural(imgs.length, "archivo", "archivos")} ({fmt(imgBytes)}) · {plural(imgs.filter((f) => IMG_OK.test(f.name)).length, "imagen", "imágenes")} · {imgs.filter((f) => LABEL_OK.test(f.name)).length} de etiquetas o clases · {imgs.filter((f) => /\.zip$/i.test(f.name)).length} zip</p>}
            <div className="formrow">
              <label title="Marca las imágenes con etiquetas como revisadas (úsalo si ya son confiables)"><input type="checkbox" id="imp-reviewed" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} /> Marcar las que traen etiquetas como revisadas</label>
              <button className="primary" id="imp-send" disabled={!imgs.length} onClick={sendImages}>Cargar al proyecto</button>
              {!!imgs.length && <button className="btn" onClick={() => setImgs([])}>Vaciar</button>}
            </div>
          </div>
        )}
        {kind === "video" && (
          <div id="import-videos">
            <p className="mut">Sube uno o varios videos (mp4, mov, m4v, avi, mkv, webm). Quedan disponibles para el reproductor, las capturas, la ejecución de modelos y los flujos.</p>
            <Drop id="imp-vfiles" accept="video/*,.mkv" onFiles={(f) => setVids([...vids, ...f.filter((x) => VIDEO_OK.test(x.name))])}>
              <Icon name="camera" size={26} /><b>Arrastra videos aquí o haz clic para elegirlos</b>
              <small>mp4, mov, m4v, avi, mkv, webm</small>
            </Drop>
            {!!vids.length && <ul id="imp-vlist" className="mut">{vids.map((f, i) => <li key={i}>{f.name} · {fmt(f.size)}</li>)}</ul>}
            <div className="radios" id="imp-mode">
              <label><input type="radio" name="imp-mode" id="imp-mode-auto" checked={mode === "auto"} onChange={() => setMode("auto")} /> <b>Automático</b>: un fotograma cada cierto intervalo, dentro de un rango</label>
              <label><input type="radio" name="imp-mode" id="imp-mode-manual" checked={mode === "manual"} onChange={() => setMode("manual")} /> <b>Manual</b>: reproduzco el video y tomo las instantáneas yo</label>
            </div>
            {mode === "auto" && (
              <div className="formrow" id="imp-auto">
                <label>Un fotograma cada <input type="number" id="imp-interval" min={0.04} step={0.5} value={interval} onChange={(e) => setInterval_(+e.target.value)} style={{ width: 70 }} /> s</label>
                <label>desde <input type="number" id="imp-start" min={0} step={1} value={start} onChange={(e) => setStart(Math.max(0, +e.target.value))} style={{ width: 70 }} /> s</label>
                <label>hasta <input type="number" id="imp-end" min={0} step={1} placeholder="el final" value={end} onChange={(e) => setEnd(e.target.value)} style={{ width: 70 }} /> s</label>
              </div>
            )}
            {mode === "manual" && <p className="mut" id="imp-manual-note">El video se sube con su primera imagen; al terminar podrás abrir el muestreo manual para tomar las instantáneas.</p>}
            <div className="formrow">
              <button className="primary" id="imp-vsend" disabled={!vids.length} onClick={sendVideos}>{`Cargar ${vids.length || ""} video${vids.length === 1 ? "" : "s"} (${fmt(vidBytes)})`}</button>
            </div>
          </div>
        )}
        {sent && <p className="importnote" id="um-sent">La subida sigue en segundo plano: puedes cerrar esta ventana y seguir trabajando. El avance aparece abajo a la derecha.</p>}
      </div>
    </div>
  );
}
