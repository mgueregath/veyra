/**
 * Controlador del lienzo de etiquetado. Independiente de React: dibuja la imagen (o el fotograma del video) con sus
 * cajas, gestiona ratón y rueda, y expone las operaciones de etiquetado. La UI se entera de los cambios por callbacks.
 */
import type { ClassDef, SourceDef } from "../api/client";
import {
  boxAt, boxFromPoints, canvasBitmapSize, cursorFor, fitView, grabOffset, handleAt, moveBox, resizeBox, screenRect, toImage, toScreen, zoomAt,
  type Box, type View,
} from "./geometry";
import * as M from "./model";

export interface Policy { alpha: number; active: boolean }
export interface EditorOptions {
  canvas: HTMLCanvasElement;
  classes: () => ClassDef[];
  sources: () => Record<string, SourceDef>;
  /** Visibilidad (0..1) y si las cajas reaccionan al ratón: el reproductor las oculta fuera del instante de la foto. */
  policy: () => Policy;
  /** Si devuelve algo, se dibuja en lugar de la imagen (fotograma del video con el mismo zoom y desplazamiento). */
  background: () => CanvasImageSource | null;
  onChange: () => void; // el documento cambió: hay que guardarlo
  onUi: () => void; // cambió algo que la interfaz muestra
  onNotice: (msg: string, ok?: boolean) => void;
}

export interface EditorUi {
  rev: number; // sube con cada cambio del documento
  sel: number;
  drawMode: boolean;
  tool: "select" | "pan";
  activeClass: string;
  zoomPct: number;
  deletedCount: number;
  anchor: Box | null; // caja (en pantalla) a la que se ancla el panel flotante
  others: Box[]; // las demás cajas, en pantalla
  undo: boolean;
  cssW: number;
  cssH: number;
}

type Drag =
  | { mode: "pan"; px: number; py: number; vx: number; vy: number; moved: boolean; empty: boolean }
  | { mode: "box"; x0: number; y0: number; x1: number; y1: number }
  | { mode: "move"; idx: number; mx: number; my: number; b0: Box; px: number; py: number; moved: boolean; dirty: boolean }
  | { mode: "resize"; idx: number; sides: { left: boolean; right: boolean; top: boolean; bottom: boolean }; b0: Box; off: [number, number]; moved: boolean; dirty: boolean };

const NO_LABEL = "#e3b341";
/** Margen (px) que deja el ajuste alrededor de la imagen: el área de la foto respira y nada queda pegado al borde. */
export const FIT_PAD = 30;

export class BoxEditor {
  doc: M.EditDoc | null = null;
  img: HTMLImageElement | null = null;
  view: View = { s: 1, x: 0, y: 0 };
  sel = -1;
  hov = -1;
  drawMode = false;
  /** «select»: editar cajas (y mover la imagen arrastrando en vacío). «pan»: arrastrar siempre mueve la imagen. */
  tool: "select" | "pan" = "select";
  activeClass: string;
  cssW = 1;
  cssH = 1;
  ui: EditorUi;
  /** Cajas de solo lectura de otra persona (el revisor las compara con las suyas). */
  overlay: { boxes: { bbox: Box; label: string | null }[]; title: string } | null = null;
  private rev = 0;
  private fitted = true;
  private drag: Drag | null = null;
  private undoUntil = 0;
  private undoBox: Box | null = null;
  private undoTimer = 0;
  private sig = "";
  private ctx: CanvasRenderingContext2D;
  private ro?: ResizeObserver;
  private off: (() => void)[] = [];

  constructor(private o: EditorOptions, activeClass: string) {
    this.activeClass = activeClass;
    this.ctx = o.canvas.getContext("2d")!;
    this.ui = this.buildUi();
    this.attach();
  }

  // ---------- ciclo de vida ----------
  private attach() {
    const cv = this.o.canvas;
    const on = (t: EventTarget, ev: string, fn: (e: any) => void, opt?: AddEventListenerOptions) => {
      t.addEventListener(ev, fn, opt);
      this.off.push(() => t.removeEventListener(ev, fn, opt));
    };
    on(cv, "contextmenu", (e: Event) => e.preventDefault());
    on(cv, "mousemove", (e: MouseEvent) => this.onHover(e));
    on(cv, "mousedown", (e: MouseEvent) => this.onDown(e));
    on(window, "mousemove", (e: MouseEvent) => this.onDrag(e));
    on(window, "mouseup", () => this.onUp());
    on(cv, "wheel", (e: WheelEvent) => this.onWheel(e), { passive: false });
    on(window, "resize", () => this.resize());
    if (typeof ResizeObserver !== "undefined") {
      // el alto del contenedor cambia sin que cambie la ventana (carrusel, fuentes…)
      this.ro = new ResizeObserver(() => this.resize());
      this.ro.observe(cv.parentElement!);
    }
    this.watchDpr();
    this.resize();
  }
  private dprListener?: () => void;
  private watchDpr() {
    if (typeof matchMedia !== "function") return;
    const mq = matchMedia(`(resolution: ${this.dpr()}dppx)`);
    this.dprListener = () => { this.resize(); this.watchDpr(); };
    mq.addEventListener("change", this.dprListener, { once: true });
  }
  destroy() {
    this.off.forEach((f) => f());
    this.ro?.disconnect();
    clearTimeout(this.undoTimer);
  }
  private dpr = () => window.devicePixelRatio || 1;

  /**
   * El bitmap del lienzo debe medir SIEMPRE lo mismo que su caja en pantalla × densidad de píxeles; si no, el
   * navegador lo estira y lo dibujado se desfasa del ratón.
   */
  resize() {
    const cv = this.o.canvas, r = cv.parentElement!.getBoundingClientRect(), d = this.dpr();
    this.cssW = r.width;
    this.cssH = r.height;
    [cv.width, cv.height] = canvasBitmapSize(this.cssW, this.cssH, d);
    if (this.fitted) this.fit(false);
    this.draw();
  }

  // ---------- datos ----------
  setFrame(doc: M.EditDoc, imageUrl: string) {
    this.doc = doc;
    this.sel = -1;
    this.hov = -1;
    this.undoBox = null;
    this.rev++;
    const img = new Image();
    this.img = img;
    img.onload = () => { if (this.img === img) { this.fit(false); this.draw(); } };
    img.src = imageUrl;
    this.draw();
  }
  /** Cambia las cajas de la imagen actual sin mover el zoom ni recargar la imagen. */
  replaceDoc(doc: M.EditDoc) {
    this.doc = doc;
    this.sel = -1;
    this.hov = -1;
    this.undoBox = null;
    this.rev++;
    this.draw();
  }
  get imgW() { return this.img?.naturalWidth ?? 0; }
  get imgH() { return this.img?.naturalHeight ?? 0; }

  fit(redraw = true) {
    this.fitted = true;
    if (this.imgW) this.view = fitView(this.imgW, this.imgH, this.cssW, this.cssH, FIT_PAD);
    if (redraw) this.draw();
  }
  zoomBy(k: number) {
    if (!this.imgW) return;
    let ax = this.cssW / 2, ay = this.cssH / 2; // ancla: la caja seleccionada si se ve; si no, el centro
    const b = this.doc?.boxes[this.sel];
    if (b) {
      const [cx, cy] = toScreen(this.view, b.bbox[0] + b.bbox[2] / 2, b.bbox[1] + b.bbox[3] / 2);
      if (cx > 0 && cx < this.cssW && cy > 0 && cy < this.cssH) { ax = cx; ay = cy; }
    }
    this.view = zoomAt(this.view, k, ax, ay);
    this.fitted = false;
    this.draw();
  }
  setActiveClass(id: string) { this.activeClass = id; this.emit(); }
  setTool(t: "select" | "pan") {
    this.tool = t;
    if (t === "pan") { this.drawMode = false; this.sel = -1; }
    this.o.canvas.style.cursor = "grab";
    this.draw();
  }
  setDrawMode(on: boolean) {
    if (on) this.tool = "select";
    this.drawMode = on;
    this.o.canvas.style.cursor = on ? "crosshair" : "grab";
    this.draw();
  }
  select(i: number) { this.sel = i; this.draw(); }
  setOverlay(o: BoxEditor["overlay"]) { this.overlay = o; this.draw(); }

  private pol(): Policy { return this.o.policy(); }
  private color = (l: string | null) => this.o.classes().find((c) => c.id === l)?.color ?? NO_LABEL;
  private tag(b: M.EBox): string {
    const src = this.o.sources();
    const s = b.manual ? (src.manual?.short ?? "manual") : b.sources.map((x) => src[x]?.short ?? x).join("+");
    const c = this.o.classes().find((k) => k.id === b.label);
    return s + (c ? " · " + (c.negative ? c.name.toLowerCase() : c.name.toUpperCase()) : "");
  }

  // ---------- dibujo ----------
  draw() {
    const cv = this.o.canvas, ctx = this.ctx, d = this.dpr();
    const r = cv.parentElement!.getBoundingClientRect();
    const [bw, bh] = canvasBitmapSize(this.cssW, this.cssH, d); // misma fórmula que resize(): si no, un contenedor de 0×0 las haría llamarse sin fin
    if (Math.abs(r.width - this.cssW) > 0.5 || Math.abs(r.height - this.cssH) > 0.5 || cv.width !== bw || cv.height !== bh) {
      this.resize(); // red de seguridad: cambió el contenedor o la densidad de píxeles
      return;
    }
    if (r.width < 1 || r.height < 1) return; // el lienzo no se ve (p. ej. se salió de la página): nada que dibujar
    ctx.setTransform(d, 0, 0, d, 0, 0);
    ctx.clearRect(0, 0, this.cssW, this.cssH);
    if (!this.img || !this.imgW || !this.doc) { this.emit(); return; }
    const bg = this.o.background() ?? this.img;
    ctx.drawImage(bg, this.view.x, this.view.y, this.imgW * this.view.s, this.imgH * this.view.s);
    const { alpha } = this.pol();
    if (alpha > 0) {
      this.doc.boxes.forEach((b, i) => {
        ctx.globalAlpha = i === this.sel ? 1 : alpha;
        const [x, y, w, h] = screenRect(this.view, b.bbox), col = this.color(b.label);
        ctx.lineWidth = i === this.sel ? 4 : 2.5;
        ctx.strokeStyle = col;
        ctx.setLineDash(b.label ? [] : [7, 4]);
        ctx.strokeRect(x, y, w, h);
        ctx.setLineDash([]);
        if (i === this.sel) { ctx.strokeStyle = "#fff"; ctx.lineWidth = 1; ctx.strokeRect(x - 3, y - 3, w + 6, h + 6); }
        if (i === this.sel || i === this.hov) {
          ctx.fillStyle = "#fff"; ctx.strokeStyle = "#000"; ctx.lineWidth = 1;
          for (const [cx, cy] of [[x, y], [x + w, y], [x, y + h], [x + w, y + h], [x + w / 2, y], [x + w / 2, y + h], [x, y + h / 2], [x + w, y + h / 2]]) {
            ctx.fillRect(cx - 4, cy - 4, 8, 8);
            ctx.strokeRect(cx - 4, cy - 4, 8, 8);
          }
        }
        const t = this.tag(b);
        ctx.font = "600 12px system-ui";
        ctx.fillStyle = "#000c";
        ctx.fillRect(x, y - 19, ctx.measureText(t).width + 10, 19);
        ctx.fillStyle = col;
        ctx.fillText(t, x + 5, y - 6);
      });
      ctx.globalAlpha = 1;
    }
    if (this.overlay) {
      ctx.setLineDash([3, 3]);
      ctx.lineWidth = 2;
      for (const ob of this.overlay.boxes) {
        const [x, y, w, h] = screenRect(this.view, ob.bbox);
        ctx.strokeStyle = this.color(ob.label);
        ctx.strokeRect(x, y, w, h);
        ctx.font = "600 11px system-ui";
        ctx.fillStyle = "#000c";
        const t = `${this.overlay.title}${ob.label ? " · " + (this.o.classes().find((k) => k.id === ob.label)?.name ?? ob.label) : ""}`;
        ctx.fillRect(x, y + h, ctx.measureText(t).width + 8, 16);
        ctx.fillStyle = this.color(ob.label);
        ctx.fillText(t, x + 4, y + h + 12);
      }
      ctx.setLineDash([]);
    }
    const dr = this.drag;
    if (dr && dr.mode === "box") {
      const [x0, y0] = toScreen(this.view, dr.x0, dr.y0), [x1, y1] = toScreen(this.view, dr.x1, dr.y1);
      ctx.strokeStyle = "#4cc9f0"; ctx.lineWidth = 2;
      ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);
    }
    this.emit();
  }

  private buildUi(): EditorUi {
    const doc = this.doc, pol = this.o.policy();
    const showPop = !!doc && !this.drag && !!this.imgW;
    let anchor: Box | null = null;
    if (showPop) {
      if (this.sel >= 0 && doc!.boxes[this.sel] && pol.active) anchor = screenRect(this.view, doc!.boxes[this.sel].bbox);
      else if (this.sel < 0 && this.undoBox && Date.now() < this.undoUntil && doc!.deleted.length) anchor = screenRect(this.view, this.undoBox);
    }
    return {
      rev: this.rev,
      sel: this.sel,
      drawMode: this.drawMode,
      tool: this.tool,
      activeClass: this.activeClass,
      zoomPct: Math.round(this.view.s * 100),
      deletedCount: doc?.deleted.length ?? 0,
      anchor,
      others: doc ? doc.boxes.filter((_, i) => i !== this.sel).map((b) => screenRect(this.view, b.bbox)) : [],
      undo: this.sel < 0 && !!anchor,
      cssW: this.cssW,
      cssH: this.cssH,
    };
  }
  private emit() {
    const u = this.buildUi();
    const sig = JSON.stringify([u.rev, u.sel, u.drawMode, u.tool, u.activeClass, u.zoomPct, u.deletedCount, u.anchor?.map(Math.round), u.undo, Math.round(u.cssW), Math.round(u.cssH)]);
    if (sig !== this.sig) { this.sig = sig; this.ui = u; this.o.onUi(); }
  }

  // ---------- ratón ----------
  private pos(e: MouseEvent): [number, number] {
    const r = this.o.canvas.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }
  private boxes(): Box[] { return this.pol().active && this.doc ? this.doc.boxes.map((b) => b.bbox) : []; }
  private onHover(e: MouseEvent) {
    if (this.drag || !this.doc) return;
    if (this.o.canvas.width !== canvasBitmapSize(this.cssW, this.cssH, this.dpr())[0]) this.resize();
    if (this.tool === "pan" && !this.drawMode) { this.o.canvas.style.cursor = "grab"; return; }
    const [px, py] = this.pos(e), bx = this.boxes();
    const h = handleAt(this.view, bx, this.sel, px, py), over = boxAt(this.view, bx, px, py);
    this.o.canvas.style.cursor = cursorFor(h, over >= 0, this.drawMode);
    const n = h ? h.idx : over;
    if (n !== this.hov) { this.hov = n; this.draw(); }
  }
  private onDown(e: MouseEvent) {
    if (!this.doc) return;
    const [px, py] = this.pos(e);
    if (e.button === 2 || e.shiftKey || (this.tool === "pan" && !this.drawMode)) { this.drag = { mode: "pan", px, py, vx: this.view.x, vy: this.view.y, moved: false, empty: false }; return; }
    if (this.drawMode) { // con B armado se dibuja, aunque el clic caiga sobre otra caja
      const [x, y] = toImage(this.view, px, py);
      this.drag = { mode: "box", x0: x, y0: y, x1: x, y1: y };
      this.sel = -1;
      return;
    }
    const bx = this.boxes(), hd = handleAt(this.view, bx, this.sel, px, py);
    if (hd) {
      this.sel = hd.idx;
      const b = this.doc.boxes[hd.idx], [ix, iy] = toImage(this.view, px, py);
      const dirty = this.takeClass(b);
      this.drag = { mode: "resize", idx: hd.idx, sides: hd, b0: [...b.bbox], off: grabOffset(b.bbox, hd, ix, iy), moved: false, dirty };
      this.draw();
      return;
    }
    const h = boxAt(this.view, bx, px, py);
    if (h >= 0) {
      this.sel = h;
      const b = this.doc.boxes[h], [x, y] = toImage(this.view, px, py);
      const dirty = this.takeClass(b);
      this.drag = { mode: "move", idx: h, mx: x, my: y, b0: [...b.bbox], px, py, moved: false, dirty };
      this.draw();
      return;
    }
    this.drag = { mode: "pan", px, py, vx: this.view.x, vy: this.view.y, moved: false, empty: true };
    this.o.canvas.style.cursor = "grabbing";
  }
  /** Agarrar o mover una caja = recibe la clase activa. */
  private takeClass(b: M.EBox): boolean {
    if (b.label === this.activeClass) return false;
    b.label = this.activeClass;
    return true;
  }
  private onDrag(e: MouseEvent) {
    const dr = this.drag;
    if (!dr || !this.doc) return;
    const [px, py] = this.pos(e);
    if (dr.mode === "pan") {
      if (Math.hypot(px - dr.px, py - dr.py) > 3) { dr.moved = true; this.fitted = false; }
      this.view = { ...this.view, x: dr.vx + px - dr.px, y: dr.vy + py - dr.py };
    } else if (dr.mode === "move") {
      if (!dr.moved && Math.hypot(px - dr.px, py - dr.py) < 4) return;
      dr.moved = true;
      const [x, y] = toImage(this.view, px, py), b = this.doc.boxes[dr.idx];
      b.orig_bbox ??= [...dr.b0] as Box;
      b.bbox = moveBox(dr.b0, x - dr.mx, y - dr.my);
    } else if (dr.mode === "resize") {
      const [ix, iy] = toImage(this.view, px, py), b = this.doc.boxes[dr.idx];
      b.orig_bbox ??= [...dr.b0] as Box;
      dr.moved = true;
      b.bbox = resizeBox(dr.b0, dr.sides, dr.off, ix, iy);
    } else {
      [dr.x1, dr.y1] = toImage(this.view, px, py);
    }
    this.draw();
  }
  private onUp() {
    const dr = this.drag;
    if (!dr || !this.doc) return;
    let changed = false;
    if (dr.mode === "box") {
      const nb = boxFromPoints(dr.x0, dr.y0, dr.x1, dr.y1);
      if (nb) { this.doc.boxes.push(M.newManualBox(nb, this.activeClass)); this.sel = this.doc.boxes.length - 1; changed = true; }
      this.setDrawMode(false);
    } else if (dr.mode === "pan") {
      if (dr.empty && !dr.moved) this.sel = -1;
      this.o.canvas.style.cursor = this.drawMode ? "crosshair" : "grab";
    } else if (dr.moved || dr.dirty) changed = true;
    this.drag = null;
    if (changed) this.changed();
    this.draw();
  }
  private onWheel(e: WheelEvent) {
    e.preventDefault();
    const [px, py] = this.pos(e);
    this.view = zoomAt(this.view, e.deltaY < 0 ? 1.2 : 1 / 1.2, px, py);
    this.fitted = false;
    this.draw();
  }

  // ---------- operaciones de etiquetado (teclado y panel) ----------
  private changed() { this.rev++; this.o.onChange(); }
  setLabel(label: string | null) {
    if (this.sel < 0 || !this.doc) return;
    M.setLabel(this.doc, this.sel, label);
    this.changed();
    this.draw();
  }
  toggleSel() {
    if (this.sel < 0 || !this.doc) return;
    M.toggleLabel(this.doc, this.sel, this.o.classes(), this.activeClass);
    this.changed();
    this.draw();
  }
  deleteSel(): boolean {
    if (!this.doc || this.sel < 0 || !this.doc.boxes[this.sel]) return false;
    if (!this.pol().active) { this.o.onNotice("Los cuadros solo se pueden editar en el fotograma de la foto (tecla R)."); return false; }
    const b = M.deleteBox(this.doc, this.sel)!;
    this.sel = -1;
    this.undoBox = [...b.bbox] as Box;
    this.undoUntil = Date.now() + 10000;
    clearTimeout(this.undoTimer);
    this.undoTimer = window.setTimeout(() => this.draw(), 10100);
    this.changed();
    this.draw();
    const n = this.doc.deleted.length;
    this.o.onNotice(`Cuadro borrado. Z o «Restaurar» lo recuperan (${n} borrado${n > 1 ? "s" : ""} en esta imagen).`, true);
    return true;
  }
  restore(all: boolean) {
    if (!this.doc || !this.doc.deleted.length) return;
    this.undoBox = null;
    this.sel = M.restoreBoxes(this.doc, all);
    this.changed();
    this.draw();
  }
  resetPos() {
    if (this.doc && M.resetPosition(this.doc, this.sel)) { this.changed(); this.draw(); }
  }
  nextUnlabeled() {
    if (!this.doc) return;
    const i = M.nextUnlabeled(this.doc, this.sel);
    if (i >= 0) { this.sel = i; this.draw(); }
  }
  markReviewed() {
    if (!this.doc) return;
    M.markReviewed(this.doc, M.negativeClass(this.o.classes()));
    this.changed();
    this.draw();
  }
  /** Para pruebas y la interfaz: el documento actual. */
  get selectedBox() { return this.doc?.boxes[this.sel] ?? null; }
}
