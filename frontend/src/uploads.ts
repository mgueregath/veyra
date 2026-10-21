/**
 * Cola de subidas fuera de React: una subida sigue su curso aunque se cierre la ventana de carga, se cambie de sección de la
 * gestión o se abra otra pantalla (solo recargar o cerrar la pestaña la corta; por eso se avisa antes de salir).
 */
import { useSyncExternalStore } from "react";
import { api, type SamplePlan } from "./api/client";

export interface UploadJob {
  id: number;
  projectId: number;
  kind: "images" | "video";
  label: string;
  bytes: number;
  progress: number; // 0..1 de lo subido
  phase: "queued" | "uploading" | "extracting" | "done" | "error";
  text: string; // resultado o motivo del error
  warnings: string[];
  manualVideoId: number | null; // video subido para muestreo manual
  extracted: { done: number; total: number } | null;
}

type Task = () => Promise<void>;
let jobs: UploadJob[] = [];
let nextId = 1;
const subs = new Set<() => void>();
const queue: Task[] = [];
let running = false;
let onFinish: (() => void) | null = null;

const emit = () => { jobs = [...jobs]; subs.forEach((f) => f()); };
const patch = (id: number, p: Partial<UploadJob>) => { jobs = jobs.map((j) => (j.id === id ? { ...j, ...p } : j)); subs.forEach((f) => f()); };
const fmtBytes = (n: number) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${(n / 1e6).toFixed(1)} MB`);
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

async function pump() {
  if (running) return;
  running = true;
  while (queue.length) await queue.shift()!();
  running = false;
}
function enqueue(job: Omit<UploadJob, "id" | "progress" | "phase" | "text" | "warnings" | "manualVideoId" | "extracted">, task: (id: number) => Promise<void>) {
  const id = nextId++;
  jobs = [...jobs, { ...job, id, progress: 0, phase: "queued", text: "", warnings: [], manualVideoId: null, extracted: null }];
  emit();
  queue.push(async () => {
    patch(id, { phase: "uploading" });
    try { await task(id); } catch (e) { patch(id, { phase: "error", text: (e as Error).message }); }
    onFinish?.();
  });
  void pump();
  return id;
}

async function follow(id: number, runId: number) {
  patch(id, { phase: "extracting", progress: 1 });
  for (;;) {
    const r = await api.run(runId);
    const p = r.summary.progress as { done: number; total: number } | null | undefined;
    if (p) patch(id, { extracted: p });
    if (r.status === "done") return patch(id, { phase: "done", text: `${plural(p?.total ?? 0, "imagen extraída", "imágenes extraídas")} del video.` });
    if (r.status === "failed") return patch(id, { phase: "error", text: "La extracción falló: " + ((r as unknown as { log?: string }).log ?? "ver el registro") });
    await new Promise((res) => setTimeout(res, 1000));
    onFinish?.();
  }
}

export const uploads = {
  /** Se llama al terminar cada tarea (para refrescar las listas). */
  onFinish(cb: (() => void) | null) { onFinish = cb; },
  images(projectId: number, files: File[], reviewed: boolean) {
    const bytes = files.reduce((a, f) => a + f.size, 0);
    return enqueue({ projectId, kind: "images", label: `${plural(files.length, "archivo", "archivos")} de imágenes (${fmtBytes(bytes)})`, bytes }, async (id) => {
      const r = await api.importImages(projectId, files, reviewed, (progress) => patch(id, { progress }));
      patch(id, {
        phase: "done", progress: 1, warnings: r.warnings,
        text: `${plural(r.images, "imagen cargada", "imágenes cargadas")} (${plural(r.with_labels, "con etiquetas", "con etiquetas")}, ${plural(r.boxes, "caja", "cajas")}) en el lote «${r.batch}».${r.skipped.length ? ` Omitidas: ${r.skipped.join("; ")}` : ""}`,
      });
    });
  },
  video(projectId: number, file: File, plan: SamplePlan) {
    return enqueue({ projectId, kind: "video", label: `${file.name} (${fmtBytes(file.size)})`, bytes: file.size }, async (id) => {
      const r = await api.importVideo(projectId, file, plan, (progress) => patch(id, { progress }));
      patch(id, { progress: 1, manualVideoId: r.mode === "manual" ? r.video_id : null });
      if (r.run_id) return follow(id, r.run_id);
      patch(id, { phase: "done", text: r.mode === "manual" ? "Subido: abre el muestreo manual para tomar las instantáneas." : `${plural(r.frames, "imagen extraída", "imágenes extraídas")}.` });
    });
  },
  dismiss(id: number) { jobs = jobs.filter((j) => j.id !== id); emit(); },
  clearFinished() { jobs = jobs.filter((j) => j.phase === "queued" || j.phase === "uploading" || j.phase === "extracting"); emit(); },
  active: () => jobs.some((j) => j.phase === "uploading" || j.phase === "queued"),
};

export function useUploads(): UploadJob[] {
  return useSyncExternalStore((cb) => { subs.add(cb); return () => { subs.delete(cb); }; }, () => jobs);
}

if (typeof window !== "undefined") {
  window.addEventListener("beforeunload", (e) => { if (uploads.active()) { e.preventDefault(); e.returnValue = ""; } });
}
