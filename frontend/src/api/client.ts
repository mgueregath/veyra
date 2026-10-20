export interface Me { id: number; username: string; display_name: string | null; is_superuser: boolean }
export interface ClassDef { id: string; name: string; key?: string; digit?: string; color: string; negative?: boolean }
export interface SourceDef { short: string; name: string }
export interface ProjectConfig { classes: ClassDef[]; box_sources?: Record<string, SourceDef> }
export interface Project { id: number; slug: string; name: string; role: string; config: ProjectConfig; n_frames: number; n_reviewed: number }
export interface Proposal { id: number; bbox: [number, number, number, number]; sources: string[]; score: number | null }
export interface AnnBox { id?: number | null; proposal_id?: number | null; bbox: [number, number, number, number]; label: string | null; manual: boolean; orig_bbox?: number[] | null; sources: string[]; score: number | null; deleted: boolean }
export interface Annotation { reviewed: boolean; boxes: AnnBox[] }
export interface ReviewInfo { reviewer: string; final_user: string; final_user_id: number; note: string | null }
export interface FrameDetail { id: number; position: number; video: string; video_id: number; has_video: boolean; frame_index: number; t: number | null; width: number; height: number; stratum: string | null; proposals: Proposal[]; annotation: Annotation | null; n_annotators: number; status: Status; n_reviewed: number; locked_by: string | null; assigned_to_me: boolean; review: ReviewInfo | null }
export type Status = "pending" | "single" | "agreed" | "disagreement" | "final";
export type FrameFilter = "all" | "mine_pending" | "assigned_pending" | "pending" | "single" | "agreed" | "disagreement" | "final" | "needs_review";
export interface FrameItem { id: number; position: number; video: string; frame_index: number; t: number | null; stratum: string | null; reviewed: boolean; origin: string | null; status: Status; assigned: boolean; n_labeled: Record<string, number> }
export interface UserAnnotation { user_id: number; user: string; reviewed: boolean; boxes: AnnBox[] }
export interface Member { username: string; display_name: string | null; role: string }
export interface Quality { frames: number; status: Record<Status, number>; users: { user: string; reviewed: number; boxes: Record<string, number> }[]; agreement: { frames_with_overlap: number; mean_f1: number | null; agree_rate: number | null } }
export interface DatasetSummary { id: number; name: string; n_items: number; rules: { include: string[]; split: number[]; seed: string }; created_at: string }
export interface SplitStats { frames: number; frames_with_positive: number; videos: number; boxes: Record<string, number> }
export interface DatasetStats { id: number; name: string; rules: DatasetSummary["rules"]; n_items: number; splits: Record<"train" | "val" | "test", SplitStats>; warnings: string[] }
export interface EvalExample { frame_id: number; bbox: number[]; label: string; score?: number; w: number; h: number; video: string }
export interface EvalMetrics {
  iou: number; score_threshold: number; threshold_choice: string; map: number | null; split: string; detector: string; dataset: string;
  micro: { tp: number; fp: number; fn: number; precision: number; recall: number; f1: number };
  per_class: Record<string, { gt: number; predictions: number; ap: number | null; tp?: number; fp?: number; fn?: number; precision?: number; recall?: number; f1?: number }>;
  by_video: { video: string; frames: number; gt: number; pred: number }[]; count_mae: number | null; n_frames: number;
  examples: { false_positives: EvalExample[]; false_negatives: EvalExample[] };
}
export interface RunSummary { id: number; kind: string; name: string; status: string; created_at: string; created_by: string; params: Record<string, unknown>;
  summary: { map: number | null; precision: number | null; recall: number | null; f1: number | null; score_threshold: number | null; count_mae: number | null; split: string | null; dataset: string | null; progress?: { epoch: number; epochs: number; metrics: Record<string, number> } | { done: number; total: number } | null; map50?: number | null; boxes?: number | null };
  metrics?: (EvalMetrics & { progress?: { epoch: number; epochs: number; metrics: Record<string, number> } | null; map50?: number }) | null; log?: string | null }
export interface PreProgress { done: number; total: number }
export interface ModelInfo { id: number; name: string; arch: string; classes: string[]; dataset_version_id: number | null; metrics: { map50?: number; map50_95?: number; precision?: number; recall?: number } | null; created_at: string; created_by: string }
export interface UncertainItem { frame_id: number; score: number; uncertainty: number; reasons: string[]; position: number; width: number; height: number; video_id: number }
export interface LiveStats {
  frames: number; fps: number; latency_ms: number; size: [number, number] | null; source_fps: number; dropped: number; reconnects: number;
  open_failures: number; ended: boolean; error: string; threshold: number; paused: boolean; recording: boolean; record_path: string;
  totals: Record<string, number>; active: number; classes: string[]; record_error?: string;
}
export interface LiveSession {
  id: number; name: string; status: "starting" | "running" | "stopped" | "failed"; source_kind: "video" | "url"; source: string; model_id: number | null;
  params: { threshold: number; imgsz: number; loop: boolean }; started_at: string; ended_at: string | null; user: string | null; error: string | null;
  summary: LiveStats | null; stats: LiveStats | null;
}
export interface ProjectVideo { id: number; name: string; has_file: boolean }
export interface TeamMember { username: string; display_name: string | null; active: boolean; role: string }
export interface Usage { images: { files: number; bytes: number }; videos: { files: number; bytes: number }; models: { bytes: number }; exports: { bytes: number }; owned_bytes: number; quota_bytes: number | null; over_quota: boolean }
export interface SystemInfo { jobs: { queued: number; running: number }; live_active: number; recent: { at: string; user: string | null; action: string; target: string | null }[] }
export interface VideoInfo { id: number; name: string; fps: number; dur: number }
export interface ProjectStats { frames: number; reviewed: number; assigned: number; assigned_done: number; by_label: Record<string, number>; manual: number; unlabeled: number; deleted: number }
export interface SnapshotResult { existing: boolean; id: number; position: number }

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { credentials: "same-origin", headers: { "content-type": "application/json" }, ...init });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail ?? msg; } catch { /* sin cuerpo JSON */ }
    throw new ApiError(r.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json() as Promise<T>;
}

export const api = {
  me: () => req<Me>("/api/me"),
  login: (username: string, password: string) => req<Me>("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => req<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  projects: () => req<Project[]>("/api/projects"),
  frames: (pid: number, offset = 0, limit = 500, filter: FrameFilter = "all") => req<FrameItem[]>(`/api/projects/${pid}/frames?offset=${offset}&limit=${limit}&filter=${filter}`),
  /** Carga el listado completo por páginas (la API limita cada petición a 500). */
  allFrames: async (pid: number, filter: FrameFilter = "all") => {
    const out: FrameItem[] = [];
    for (let off = 0; ; off += 500) {
      const page = await api.frames(pid, off, 500, filter);
      out.push(...page);
      if (page.length < 500) return out;
    }
  },
  frame: (id: number) => req<FrameDetail>(`/api/frames/${id}`),
  save: (id: number, a: Annotation) => req<Annotation>(`/api/frames/${id}/annotation`, { method: "PUT", body: JSON.stringify(a) }),
  lock: (id: number) => req<{ ok: boolean; holder: string | null }>(`/api/frames/${id}/lock`, { method: "POST" }),
  queueNext: (pid: number, scope: "assigned" | "any") => req<{ frame_id: number | null; position: number | null }>(`/api/projects/${pid}/queue/next`, { method: "POST", body: JSON.stringify({ scope }) }),
  annotations: (id: number) => req<UserAnnotation[]>(`/api/frames/${id}/annotations`),
  review: (id: number, final_user_id: number, note?: string) => req<{ status: string }>(`/api/frames/${id}/review`, { method: "POST", body: JSON.stringify({ final_user_id, note }) }),
  undoReview: (id: number) => req<{ ok: boolean }>(`/api/frames/${id}/review`, { method: "DELETE" }),
  members: (pid: number) => req<Member[]>(`/api/projects/${pid}/members`),
  assign: (pid: number, body: { users: string[]; overlap_pct: number; seed: number; frame_ids?: number[] }) => req<{ frames: number; created: number; overlap: number }>(`/api/projects/${pid}/assignments`, { method: "POST", body: JSON.stringify(body) }),
  assignments: (pid: number) => req<{ user: string; assigned: number; done: number }[]>(`/api/projects/${pid}/assignments`),
  quality: (pid: number) => req<Quality>(`/api/projects/${pid}/quality`),
  datasets: (pid: number) => req<DatasetSummary[]>(`/api/projects/${pid}/datasets`),
  createDataset: (pid: number, body: { name: string; include: string[]; split: number[]; seed: string }) => req<DatasetStats>(`/api/projects/${pid}/datasets`, { method: "POST", body: JSON.stringify(body) }),
  dataset: (id: number) => req<DatasetStats>(`/api/datasets/${id}`),
  exportDataset: (id: number, format: "yolo" | "coco" | "both") => req<{ path: string; manifest: { frames: Record<string, number> } }>(`/api/datasets/${id}/export`, { method: "POST", body: JSON.stringify({ format }) }),
  createEvaluation: (pid: number, body: { dataset_version_id: number; split: string; detector: string; score_threshold: number | null; iou: number }) => req<RunSummary>(`/api/projects/${pid}/evaluations`, { method: "POST", body: JSON.stringify(body) }),
  runs: (pid: number, kind?: string) => req<RunSummary[]>(`/api/projects/${pid}/runs${kind ? `?kind=${kind}` : ""}`),
  run: (id: number) => req<RunSummary>(`/api/runs/${id}`),
  train: (pid: number, body: { dataset_version_id: number; arch: string; epochs: number; imgsz: number; batch: number; model_name?: string }) => req<{ run_id: number; model_name: string; status: string }>(`/api/projects/${pid}/trainings`, { method: "POST", body: JSON.stringify(body) }),
  cancelRun: (id: number) => req<{ ok: boolean }>(`/api/runs/${id}/cancel`, { method: "POST" }),
  preannotate: (pid: number, body: { model_id: number; scope: "unreviewed" | "all"; min_score: number }) => req<{ run_id: number; status: string }>(`/api/projects/${pid}/preannotations`, { method: "POST", body: JSON.stringify(body) }),
  uncertain: (pid: number, modelId: number, limit: number) => req<UncertainItem[]>(`/api/projects/${pid}/uncertain?model_id=${modelId}&limit=${limit}`),
  models: (pid: number) => req<ModelInfo[]>(`/api/projects/${pid}/models`),
  projectVideos: (pid: number) => req<ProjectVideo[]>(`/api/projects/${pid}/videos`),
  liveList: (pid: number) => req<LiveSession[]>(`/api/projects/${pid}/live`),
  liveStart: (pid: number, body: { source_kind: "video" | "url"; video_id?: number; url?: string; model_id?: number | null; threshold: number; imgsz: number; name?: string }) => req<LiveSession>(`/api/projects/${pid}/live`, { method: "POST", body: JSON.stringify(body) }),
  liveGet: (id: number) => req<LiveSession>(`/api/live/${id}`),
  liveControl: (id: number, body: { threshold?: number; paused?: boolean; record?: boolean }) => req<{ threshold: number; paused: boolean; recording: boolean }>(`/api/live/${id}/control`, { method: "POST", body: JSON.stringify(body) }),
  liveSave: (id: number) => req<{ frame_id: number; position: number; boxes: number }>(`/api/live/${id}/save-frame`, { method: "POST" }),
  liveStop: (id: number) => req<LiveSession>(`/api/live/${id}`, { method: "DELETE" }),
  liveClips: (id: number) => req<{ name: string; bytes: number }[]>(`/api/live/${id}/clips`),
  liveStreamUrl: (id: number) => `/api/live/${id}/stream`,
  clipUrl: (id: number, name: string) => `/api/live/${id}/clips/${name}`,
  team: (pid: number) => req<TeamMember[]>(`/api/projects/${pid}/team`),
  addMember: (pid: number, body: { username: string; role: string; password?: string; display_name?: string }) => req<{ username: string; role: string }>(`/api/projects/${pid}/members`, { method: "POST", body: JSON.stringify(body) }),
  setRole: (pid: number, u: string, role: string) => req<{ role: string }>(`/api/projects/${pid}/members/${encodeURIComponent(u)}`, { method: "PUT", body: JSON.stringify({ role }) }),
  removeMember: (pid: number, u: string) => req<{ ok: boolean }>(`/api/projects/${pid}/members/${encodeURIComponent(u)}`, { method: "DELETE" }),
  resetPassword: (pid: number, u: string, password: string) => req<{ ok: boolean }>(`/api/projects/${pid}/members/${encodeURIComponent(u)}/password`, { method: "POST", body: JSON.stringify({ password }) }),
  changePassword: (old: string, nw: string) => req<{ ok: boolean }>(`/api/me/password`, { method: "POST", body: JSON.stringify({ old, new: nw }) }),
  usage: (pid: number) => req<Usage>(`/api/projects/${pid}/usage`),
  setQuota: (pid: number, gb: number | null) => req<Usage>(`/api/projects/${pid}/quota`, { method: "PUT", body: JSON.stringify({ quota_gb: gb }) }),
  system: (pid: number) => req<SystemInfo>(`/api/projects/${pid}/system`),
  backupUrl: (pid: number) => `/api/projects/${pid}/backup`,
  stats: (pid: number) => req<ProjectStats>(`/api/projects/${pid}/stats`),
  videoInfo: (vid: number) => req<VideoInfo>(`/api/videos/${vid}/info`),
  videoPts: (vid: number, a: number, b: number) => req<{ pts: number[] }>(`/api/videos/${vid}/pts?a=${a}&b=${b}`),
  videoUrl: (vid: number) => `/api/videos/${vid}/stream`,
  snapshot: (frameId: number, t: number) => req<SnapshotResult>(`/api/frames/${frameId}/snapshot`, { method: "POST", body: JSON.stringify({ t }) }),
  thumbUrl: (id: number) => `/api/frames/${id}/thumb`,
  imageUrl: (id: number) => `/api/frames/${id}/image`,
};
