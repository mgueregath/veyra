import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { uploads, useUploads } from "../uploads";

/** Subidas en curso, visibles en cualquier pantalla: cerrar la ventana de carga o cambiar de sección no las detiene. */
export default function UploadDock() {
  const qc = useQueryClient();
  const jobs = useUploads();
  const [open, setOpen] = useState(true);
  useEffect(() => {
    uploads.onFinish(() => { for (const k of ["frames", "stats", "projects", "videos", "sources"]) void qc.invalidateQueries({ queryKey: [k] }); });
    return () => uploads.onFinish(null);
  }, [qc]);
  if (!jobs.length) return null;
  const busy = jobs.filter((j) => j.phase === "uploading" || j.phase === "queued" || j.phase === "extracting").length;
  return (
    <aside id="upload-dock" className="udock" aria-live="polite">
      <div className="udock-head">
        <b>{busy ? `Subiendo (${busy})…` : "Subidas"}</b>
        <span>
          <button className="udock-btn" id="ud-toggle" title={open ? "Ocultar la lista" : "Mostrar la lista"} onClick={() => setOpen(!open)}>{open ? "−" : "+"}</button>
          {!busy && <button className="udock-btn" id="ud-clear" title="Quitar las subidas terminadas" onClick={() => uploads.clearFinished()}>×</button>}
        </span>
      </div>
      {open && jobs.map((j) => (
        <div key={j.id} className={"udock-item " + j.phase} data-phase={j.phase}>
          <div className="udock-label" title={j.label}>{j.label}</div>
          {(j.phase === "uploading" || j.phase === "queued") && <><progress value={j.progress} max={1} /><small>{j.phase === "queued" ? "en espera" : `subiendo ${Math.round(j.progress * 100)} %`}</small></>}
          {j.phase === "extracting" && <><progress value={j.extracted?.done ?? 0} max={Math.max(1, j.extracted?.total ?? 1)} /><small>extrayendo imágenes {j.extracted ? `${j.extracted.done} de ${j.extracted.total}` : "…"}</small></>}
          {j.phase === "done" && <small className="ok">{j.text}</small>}
          {j.phase === "error" && <small className="err">{j.text}</small>}
          {j.warnings.map((w) => <small key={w} className="warn">{w}</small>)}
          {j.manualVideoId !== null && j.phase === "done" && (
            <button className="btn" onClick={() => { window.location.hash = `p=${j.projectId}&v=sample&vid=${j.manualVideoId}`; uploads.dismiss(j.id); }}>Abrir muestreo manual</button>
          )}
          {(j.phase === "done" || j.phase === "error") && <button className="udock-x" title="Quitar" onClick={() => uploads.dismiss(j.id)}>×</button>}
        </div>
      ))}
    </aside>
  );
}
