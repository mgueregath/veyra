/**
 * Reproductor del VIDEO ORIGINAL sobre el lienzo, ventana de 5 s antes a 5 s después del instante `t` de la foto.
 *
 * Dos vistas: «foto» (la imagen con sus cajas: es lo que se ve al abrir) y «video» (se dibuja el fotograma del video
 * en el mismo lugar, con el mismo zoom y desplazamiento). NUNCA se reproduce solo: se pasa a «video» al reproducir,
 * mover la barra o avanzar fotogramas, y «Volver a la imagen» regresa a la foto. El archivo de video no se pide al
 * servidor hasta que se necesita. Para mostrar un fotograma exacto se busca la mitad de su intervalo (justo en `t`
 * puede salir el anterior o el siguiente). Se usan los PTS reales del contenedor: en tasa variable no son 1/fps.
 */
import { api, type SnapshotResult, type VideoInfo } from "../api/client";

export const SPEEDS = [0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4];
export type BoxMode = "hide" | "dim" | "full";
export const BOX_MODES: BoxMode[] = ["hide", "dim", "full"];
export const BOX_MODE_LABEL: Record<BoxMode, string> = { hide: "solo en la foto", dim: "tenues", full: "siempre" };

export interface PlayerFrame { id: number; videoId: number; hasVideo: boolean; video: string; t: number | null; w: number; h: number }
export interface PlayerUi {
  open: boolean;
  view: "foto" | "video";
  ready: boolean;
  at: boolean;
  playing: boolean;
  boxes: BoxMode;
  rate: number;
  loop: boolean;
  win: string;
  startLabel: string;
  endLabel: string;
  markPct: number;
  seek: number;
  now: string;
  rel: string;
  chip: string;
  chipOk: boolean;
  msg: { text: string; ok: boolean } | null;
  saving: boolean;
}

export const fmtT = (s: number) => (s < 0 ? "−" : "") + Math.floor(Math.abs(s) / 60) + ":" + (Math.abs(s) % 60).toFixed(2).padStart(5, "0").replace(".", ",");
export const fmtR = (s: number) => (s > 0 ? "+" : s < 0 ? "−" : "") + Math.abs(s).toFixed(2).replace(".", ",") + " s";

const LS = {
  get: (k: string) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* sin almacenamiento */ } },
};

export interface PlayerHost {
  imageSize: () => [number, number];
  redraw: () => void;
  onUi: () => void;
  onSaved: (r: SnapshotResult, t: number) => void;
}

export class VideoPlayer {
  readonly vid: HTMLVideoElement;
  open = false;
  view: "foto" | "video" = "foto";
  f: PlayerFrame | null = null;
  start = 0;
  end = 0;
  t = 0;
  dt = 1 / 30;
  media: number | null = null;
  tok = 0;
  pts: number[] = [];
  it = 0; // índice del fotograma de la foto
  i0 = 0;
  i1 = 0;
  pend: number | null = null;
  ready = false;
  vready = false;
  at = true;
  boxes: BoxMode = "hide";
  rate = 1;
  loop = false;
  saving = false;
  msg: { text: string; ok: boolean } | null = null;
  ui: PlayerUi;
  private name: number | null = null;
  private info = new Map<number, VideoInfo | null>();
  private msgTimer = 0;
  private vfcb = 0;
  private sig = "";
  private cleanup: (() => void)[] = [];

  constructor(private h: PlayerHost) {
    const v = document.createElement("video");
    v.muted = true;
    v.playsInline = true;
    v.preload = "auto"; // el archivo no se pide hasta que se asigna `src` (solo al reproducir o mover la barra)
    v.setAttribute("aria-hidden", "true");
    v.style.cssText = "position:fixed;left:-99999px;top:0;width:320px;pointer-events:none";
    document.body.appendChild(v);
    this.vid = v;
    const m = LS.get("vboxes") as BoxMode | null;
    if (m && BOX_MODES.includes(m)) this.boxes = m;
    const r = parseFloat(LS.get("vrate") ?? "");
    this.setRate(SPEEDS.includes(r) ? r : 1);
    this.loop = LS.get("vloop") === "1";
    const on = (ev: string, fn: () => void) => { v.addEventListener(ev, fn); this.cleanup.push(() => v.removeEventListener(ev, fn)); };
    on("timeupdate", () => { if (!("requestVideoFrameCallback" in v)) { this.media = null; this.refresh(); } });
    on("ended", () => { // la ventana llega al final del archivo
      if (this.open && this.view === "video" && this.loop) { v.currentTime = this.pmid(this.i0); v.play().catch(() => {}); }
    });
    on("play", () => this.refresh());
    on("pause", () => this.refresh());
    on("seeked", () => this.refresh());
    if ("requestVideoFrameCallback" in v) {
      const cb = (_n: number, meta: { mediaTime: number }) => {
        this.media = meta.mediaTime;
        if (this.pend !== null && this.pidx(meta.mediaTime) === this.pend) this.pend = null;
        this.refresh();
        this.vfcb = (v as any).requestVideoFrameCallback(cb);
      };
      this.vfcb = (v as any).requestVideoFrameCallback(cb);
    }
    this.ui = this.buildUi();
  }
  destroy() {
    this.cleanup.forEach((f) => f());
    if (this.vfcb && "cancelVideoFrameCallback" in this.vid) (this.vid as any).cancelVideoFrameCallback(this.vfcb);
    this.vid.pause();
    this.vid.removeAttribute("src");
    this.vid.remove();
    clearTimeout(this.msgTimer);
  }

  // ---------- consultas que usa el lienzo ----------
  /** Marca de tiempo → índice del fotograma que se muestra en ese instante. */
  pidx(t: number) {
    const a = this.pts;
    if (!a.length) return -1;
    let lo = 0, hi = a.length - 1;
    while (lo < hi) { const m = (lo + hi + 1) >> 1; if (a[m] <= t + 1e-4) lo = m; else hi = m - 1; }
    return lo;
  }
  private pmid(i: number) {
    const a = this.pts, nx = a[i + 1] !== undefined ? a[i + 1] : a[i] + this.dt;
    return a[i] + 0.5 * (nx - a[i]);
  }
  /** El fotograma del video solo se usa si tiene la proporción de la foto (la rotación del contenedor ya viene aplicada). */
  drawable(): HTMLVideoElement | null {
    const [w, h] = this.h.imageSize(), v = this.vid;
    return this.open && this.view === "video" && this.vready && v.readyState >= 2 && v.videoHeight > 0 && w > 0 && Math.abs(v.videoWidth / v.videoHeight - w / h) < 0.02 ? v : null;
  }
  /** En la vista «foto» y en el fotograma de la foto los cuadros se ven y se editan; fuera de él, según el modo. */
  alpha() {
    if (!this.open || this.view === "foto" || this.at) return 1;
    return this.boxes === "hide" ? 0 : this.boxes === "dim" ? 0.4 : 1;
  }
  boxesActive() { return !this.open || this.view === "foto" || this.at || this.boxes !== "hide"; }

  // ---------- mensajes ----------
  notice(text: string | null, sticky = false, ok = false) {
    clearTimeout(this.msgTimer);
    this.msg = text ? { text, ok } : null;
    if (text && !sticky) this.msgTimer = window.setTimeout(() => { this.msg = null; this.emit(); }, 4000);
    this.emit();
  }

  // ---------- carga ----------
  private seekTo(t: number) {
    const v = this.vid;
    return new Promise<void>((res) => {
      let done = false;
      const fin = () => { if (!done) { done = true; v.removeEventListener("seeked", fin); res(); } };
      v.addEventListener("seeked", fin);
      setTimeout(fin, 4000);
      v.currentTime = t;
    });
  }
  private async videoInfo(f: PlayerFrame) {
    if (!this.info.has(f.videoId)) {
      try { this.info.set(f.videoId, await api.videoInfo(f.videoId)); } catch { this.info.set(f.videoId, null); }
    }
    return this.info.get(f.videoId) ?? null;
  }
  setRate(r: number) {
    this.rate = r;
    this.vid.defaultPlaybackRate = r;
    this.vid.playbackRate = r;
    LS.set("vrate", String(r));
    this.emit();
  }
  rateStep(d: number) {
    let i = SPEEDS.indexOf(this.rate);
    if (i < 0) i = SPEEDS.indexOf(1);
    this.setRate(SPEEDS[Math.min(Math.max(i + d, 0), SPEEDS.length - 1)]);
  }

  /** Calcula la ventana y los PTS de la foto actual. NO toca el video: la vista inicial es la foto, quieta. */
  async load(f: PlayerFrame | null) {
    this.f = f;
    const tok = ++this.tok;
    this.media = null;
    this.pend = null;
    this.ready = false;
    this.vready = false;
    this.view = "foto";
    this.at = true;
    this.vid.pause();
    if (!this.open || !f) { this.refresh(); return; }
    this.refresh();
    if (!f.hasVideo) { this.notice("No se encontró el video original de esta imagen.", true); return; }
    if (typeof f.t !== "number") { this.notice("Esta foto no tiene guardado el instante t del video.", true); return; }
    const inf = await this.videoInfo(f);
    if (tok !== this.tok) return;
    if (!inf) { this.notice("No se encontró el video original de esta foto.", true); return; }
    this.notice(null);
    this.dt = 1 / inf.fps;
    this.t = f.t;
    this.start = Math.max(0, f.t - 5);
    this.end = Math.min(inf.dur, f.t + 5);
    let pts: number[] = [];
    try { pts = (await api.videoPts(f.videoId, this.start - 1, this.end + 1)).pts; } catch { /* se sintetizan abajo */ }
    if (tok !== this.tok) return;
    if (!pts.length) { const K = Math.ceil(6 / this.dt); for (let k = -K; k <= K; k++) pts.push(f.t + k * this.dt); } // sin PTS: tasa constante
    this.pts = pts;
    let bi = 0;
    pts.forEach((x, i) => { if (Math.abs(x - this.t) < Math.abs(pts[bi] - this.t)) bi = i; });
    this.it = bi;
    this.i0 = 0;
    while (this.i0 < pts.length - 1 && pts[this.i0] < this.start - 1e-4) this.i0++;
    this.i1 = Math.max(this.i0, this.pidx(this.end));
    this.ready = true;
    this.refresh();
  }

  setOpen(open: boolean) {
    this.open = open;
    this.view = "foto";
    LS.set("player", open ? "1" : "0");
    if (open) void this.load(this.f);
    else { this.tok++; this.vid.pause(); this.ready = false; this.vready = false; this.notice(null); this.refresh(); }
  }

  /** Carga el archivo (solo la primera vez que se necesita) y comprueba que tenga la proporción de la foto. */
  private async ensureVideo() {
    if (!this.open || !this.ready || !this.f) return false;
    const tok = this.tok, f = this.f, v = this.vid;
    if (this.name !== f.videoId) {
      this.name = f.videoId;
      this.vready = false;
      const ok = await new Promise<boolean>((res) => { v.onloadedmetadata = () => res(true); v.onerror = () => res(false); v.src = api.videoUrl(f.videoId); });
      if (tok !== this.tok) return false;
      if (!ok) { this.name = null; this.notice("El navegador no pudo abrir el video (¿códec no compatible?).", true); return false; }
    }
    if (!(Math.abs(v.videoWidth / v.videoHeight - f.w / f.h) < 0.02)) {
      this.notice("El video tiene otra proporción que la foto: no se puede mostrar sobre ella.", true);
      return false;
    }
    v.playbackRate = this.rate;
    this.vready = true;
    return true;
  }
  private async showAt(i: number) {
    if (!this.open || !this.ready) return false;
    this.vid.pause();
    this.pend = null;
    if (!(await this.ensureVideo())) return false;
    this.view = "video";
    await this.seekTo(this.pmid(i));
    this.refresh();
    return true;
  }

  // ---------- acciones ----------
  /** Pasos acumulados sobre el fotograma DESTINO pendiente: con pulsaciones rápidas P.media aún no se actualizó. */
  async step(n: number) {
    if (!this.open || !this.ready) return;
    this.vid.pause();
    const cur = this.view === "foto" ? this.it : this.pidx(this.media ?? this.vid.currentTime);
    const base = this.pend !== null ? this.pend : cur;
    const j = Math.min(Math.max(base + n, this.i0), this.i1);
    this.pend = j;
    if (!(await this.ensureVideo())) return;
    this.view = "video";
    await this.seekTo(this.pmid(j));
    setTimeout(() => { if (this.pend === j) this.pend = null; }, 150);
    this.refresh();
  }
  back() { // vuelve a la imagen con sus cuadros
    if (!this.open) return;
    this.vid.pause();
    this.pend = null;
    this.view = "foto";
    this.at = true;
    this.refresh();
  }
  startWindow() { void this.showAt(this.i0); }
  /** Reproducir: desde la vista «foto» arranca 5 s antes de la foto; con el video en pausa continúa donde estaba. */
  async toggle() {
    if (!this.open || !this.ready) return;
    if (this.view === "video" && !this.vid.paused) { this.vid.pause(); return; }
    if (this.view === "foto") { if (!(await this.showAt(this.i0))) return; }
    else if (this.vid.currentTime >= this.end - 1.5 * this.dt) await this.seekTo(this.pmid(this.i0));
    this.vid.play().catch(() => {});
  }
  async seekFraction(frac: number) {
    if (!this.open || !this.ready || !(this.end > this.start)) return;
    this.vid.pause();
    if (!(await this.ensureVideo())) return;
    this.view = "video";
    this.pend = null;
    void this.seekTo(this.start + (this.end - this.start) * frac);
  }
  cycleBoxes() { this.setBoxes(BOX_MODES[(BOX_MODES.indexOf(this.boxes) + 1) % BOX_MODES.length]); }
  setBoxes(m: BoxMode) { this.boxes = m; LS.set("vboxes", m); this.refresh(); }
  setLoop(on: boolean) { this.loop = on; LS.set("vloop", on ? "1" : "0"); this.emit(); }

  /** Guarda como imagen nueva el fotograma que se ve (el servidor lo extrae del ORIGINAL con su instante exacto). */
  async save() {
    if (!this.open || !this.ready || !this.f || !(this.end > this.start)) { this.notice("Abre el video y elige un fotograma antes de guardar."); return; }
    this.vid.pause();
    const ci = this.view === "foto" ? this.it : this.pend !== null ? this.pend : this.pidx(this.media ?? this.vid.currentTime);
    const t = this.pts[ci];
    if (t === undefined) return;
    this.saving = true;
    this.emit();
    try {
      const r = await api.snapshot(this.f.id, t);
      this.h.onSaved(r, t);
    } catch (e) {
      this.notice("No se pudo guardar: " + (e as Error).message);
    } finally {
      this.saving = false;
      this.emit();
    }
  }

  // ---------- estado para la interfaz ----------
  /** Actualiza barra, marcas y vuelve a dibujar el lienzo. */
  refresh() {
    const v = this.vid;
    if (this.open && this.ready && this.end > this.start && this.view === "video") {
      const el = v.currentTime;
      if (!v.paused && el >= this.end - 0.5 * this.dt) {
        if (this.loop) v.currentTime = this.pmid(this.i0);
        else { v.pause(); v.currentTime = Math.max(this.pmid(this.i1), this.start); }
      }
      const t = this.media ?? el, ci = this.pidx(t);
      this.at = ci === this.it;
    } else this.at = true;
    this.h.redraw();
    this.emit();
  }
  private buildUi(): PlayerUi {
    const v = this.vid, can = this.open && this.ready && this.end > this.start;
    let seek = 0, now = "—", rel = "", chip = this.view === "foto" ? "IMAGEN" : "VIDEO", chipOk = false;
    if (can && this.view === "foto") {
      seek = Math.round((1000 * (this.t - this.start)) / (this.end - this.start));
      now = "t = " + fmtT(this.t);
      rel = "FOTO";
      chip = "IMAGEN · FOTO";
      chipOk = true;
    } else if (can) {
      const el = v.currentTime, t = this.media ?? el, ci = this.pidx(t), r = (this.pts[ci] ?? t) - this.t, at = ci === this.it;
      seek = Math.round((1000 * (Math.min(Math.max(el, this.start), this.end) - this.start)) / (this.end - this.start));
      now = "t = " + fmtT(t);
      rel = at ? "FOTO" : fmtR(r);
      chip = at ? "VIDEO · FOTO" : "VIDEO · " + fmtR(r);
      chipOk = at;
    }
    return {
      open: this.open, view: this.view, ready: this.ready, at: this.at, playing: this.view === "video" && !v.paused,
      boxes: this.boxes, rate: this.rate, loop: this.loop,
      win: can ? `ventana ${fmtT(this.start)} – ${fmtT(this.end)} · foto en ${fmtT(this.t)}` : "",
      startLabel: can ? fmtT(this.start) : "—",
      endLabel: can ? fmtT(this.end) : "—",
      markPct: can ? (100 * (this.t - this.start)) / (this.end - this.start) : 0,
      seek, now, rel, chip, chipOk, msg: this.msg, saving: this.saving,
    };
  }
  private emit() {
    const u = this.buildUi(), sig = JSON.stringify(u);
    if (sig !== this.sig) { this.sig = sig; this.ui = u; this.h.onUi(); }
  }
}
