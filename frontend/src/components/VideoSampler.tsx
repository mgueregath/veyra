import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type ProjectVideo } from "../api/client";

/** Formulario del muestreo automático de un video ya cargado. */
export function AutoSampler({ video, onDone, onRun }: { video: ProjectVideo; onDone: () => void; onRun: (id: number) => void }) {
  const qc = useQueryClient();
  const [interval, setInterval_] = useState(5);
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState<string>("");
  const [msg, setMsg] = useState<{ t: string; ok: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const dur = video.duration ?? 0;
  const last = end === "" ? dur : Math.min(+end, dur);
  const est = interval > 0 && last >= start ? Math.floor((last - start) / interval + 1e-9) + 1 : 0;
  const go = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await api.sampleVideo(video.id, { interval, start, end: end === "" ? null : +end });
      setMsg({ t: `${r.frames} imágenes nuevas en camino${r.already ? ` (${r.already} ya estaban)` : ""}: se extraen en segundo plano.`, ok: true });
      if (r.run_id) onRun(r.run_id);
      for (const k of ["frames", "stats", "projects", "videos"]) void qc.invalidateQueries({ queryKey: [k] });
      onDone();
    } catch (e) { setMsg({ t: (e as Error).message, ok: false }); }
    finally { setBusy(false); }
  };
  return (
    <div className="formrow" id={"as-form-" + video.id}>
      <label>Cada <input type="number" className="sa-interval" min={0.04} step={0.5} value={interval} onChange={(e) => setInterval_(+e.target.value)} style={{ width: 70 }} /> s</label>
      <label>Desde <input type="number" className="sa-start" min={0} step={1} value={start} onChange={(e) => setStart(Math.max(0, +e.target.value))} style={{ width: 70 }} /> s</label>
      <label>Hasta <input type="number" className="sa-end" min={0} step={1} placeholder="final" value={end} onChange={(e) => setEnd(e.target.value)} style={{ width: 70 }} /> s</label>
      <span className="mut">≈ {est} imágenes{est > 1000 ? " · se extraen en segundo plano" : ""}</span>
      <button className="primary sa-go" disabled={busy || est < 1 || est > 20000} onClick={() => void go()}>{busy ? "Extrayendo…" : "Muestrear"}</button>
      {msg && <span className={msg.ok ? "mut" : "err"}>{msg.t}</span>}
    </div>
  );
}

/** Avance de un muestreo en segundo plano. */
export function SamplingProgress({ runId, label }: { runId: number; label: string }) {
  const qc = useQueryClient();
  const run = useQuery({
    queryKey: ["run", runId], queryFn: () => api.run(runId),
    refetchInterval: (q) => (q.state.data && (q.state.data.status === "done" || q.state.data.status === "failed") ? false : 1000),
  });
  const r = run.data, p = r?.summary.progress as { done: number; total: number } | null | undefined;
  const fin = r?.status === "done" || r?.status === "failed";
  useEffect(() => { if (fin) for (const k of ["frames", "stats", "projects", "videos"]) void qc.invalidateQueries({ queryKey: [k] }); }, [fin, qc]);
  return (
    <div className="importnote sampling-progress" data-run={runId}>
      <span><b>{label}</b>: {r?.status === "failed" ? <span className="err">falló ({(r as unknown as { log?: string }).log ?? "ver registro"})</span> : fin ? "extracción terminada" : p ? `extrayendo ${p.done} de ${p.total}…` : "en cola…"}</span>
      {!fin && p && <progress value={p.done} max={Math.max(1, p.total)} />}
    </div>
  );
}
