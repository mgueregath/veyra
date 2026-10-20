/**
 * Núcleo del editor de cajas: geometría pura, sin React ni DOM, por eso se puede probar aislada.
 * Todas las cajas viven en coordenadas de IMAGEN; la vista (zoom + desplazamiento) las pasa a pantalla (px CSS).
 */
export type Box = [x: number, y: number, w: number, h: number];
export interface View {
  s: number; // zoom: píxeles de pantalla (CSS) por píxel de imagen
  x: number; // desplazamiento en pantalla del origen de la imagen
  y: number;
}

export const MIN_ZOOM = 0.05;
export const MAX_ZOOM = 20;
export const MIN_BOX = 8;

export const toScreen = (v: View, ix: number, iy: number): [number, number] => [ix * v.s + v.x, iy * v.s + v.y];
export const toImage = (v: View, sx: number, sy: number): [number, number] => [(sx - v.x) / v.s, (sy - v.y) / v.s];

/** Vista que encaja la imagen entera dentro del lienzo (contain, nunca recortada), centrada y con `pad` px de margen. */
export function fitView(imgW: number, imgH: number, cssW: number, cssH: number, pad = 0): View {
  const s = Math.max(0.01, Math.min((cssW - 2 * pad) / imgW, (cssH - 2 * pad) / imgH));
  return { s, x: (cssW - imgW * s) / 2, y: (cssH - imgH * s) / 2 };
}

/** Zoom manteniendo fijo el punto de pantalla (sx, sy): rueda del ratón y lupas. */
export function zoomAt(v: View, factor: number, sx: number, sy: number, min = MIN_ZOOM, max = MAX_ZOOM): View {
  const s = Math.min(max, Math.max(min, v.s * factor));
  const [ix, iy] = toImage(v, sx, sy);
  return { s, x: sx - ix * s, y: sy - iy * s };
}

/** Caja → rectángulo en pantalla [x, y, w, h]. */
export function screenRect(v: View, b: Box): Box {
  const [x, y] = toScreen(v, b[0], b[1]);
  return [x, y, b[2] * v.s, b[3] * v.s];
}

export interface HandleHit {
  idx: number;
  d: number;
  corner: boolean;
  left: boolean;
  right: boolean;
  top: boolean;
  bottom: boolean;
}

const EDGE_TOL = 10;

/**
 * Asas de redimensión de CUALQUIER caja. Esquina = zona cuadrada alrededor de cada vértice (siempre mueve los dos
 * ejes); borde = franja a lo largo de un lado, lejos de las esquinas (mueve un eje). Las zonas se achican en cajas
 * chicas para no tapar su interior, que sirve para moverlas. La caja seleccionada gana los empates.
 */
export function handleAt(v: View, boxes: Box[], sel: number, px: number, py: number): HandleHit | null {
  const R = boxes.map((b, i) => {
    const [x0, y0] = toScreen(v, b[0], b[1]);
    const x1 = x0 + b[2] * v.s, y1 = y0 + b[3] * v.s;
    return { i, x0, y0, x1, y1, m: Math.min(x1 - x0, y1 - y0) };
  });
  let best: HandleHit | null = null;
  for (const r of R) {
    const ct = Math.max(7, Math.min(18, 0.4 * r.m)), sb = r.i === sel ? 0.5 : 0;
    const corners: [number, number, boolean, boolean][] = [
      [r.x0, r.y0, true, true], [r.x1, r.y0, false, true], [r.x0, r.y1, true, false], [r.x1, r.y1, false, false],
    ];
    for (const [cx, cy, l, t] of corners) {
      const d = Math.max(Math.abs(px - cx), Math.abs(py - cy)) - sb;
      if (d <= ct && (!best || d < best.d)) best = { idx: r.i, d, corner: true, left: l, right: !l, top: t, bottom: !t };
    }
  }
  if (best) return best;
  for (const r of R) {
    const et = Math.max(4, Math.min(EDGE_TOL, 0.3 * r.m));
    if (px < r.x0 - et || px > r.x1 + et || py < r.y0 - et || py > r.y1 + et) continue;
    const dl = Math.abs(px - r.x0), dr = Math.abs(px - r.x1), dt = Math.abs(py - r.y0), db = Math.abs(py - r.y1);
    const L = dl <= et, Rr = dr <= et, T = dt <= et, B = db <= et;
    if (!(L || Rr || T || B)) continue;
    const d = Math.min(L ? dl : 1e9, Rr ? dr : 1e9, T ? dt : 1e9, B ? db : 1e9) - (r.i === sel ? 0.5 : 0);
    if (!best || d < best.d) {
      best = { idx: r.i, d, corner: false, left: L && (!Rr || dl <= dr), right: Rr && (!L || dr < dl), top: T && (!B || dt <= db), bottom: B && (!T || db < dt) };
    }
  }
  return best;
}

/** Índice de la caja más pequeña que contiene el punto de pantalla (la de dentro gana a la que la envuelve). */
export function boxAt(v: View, boxes: Box[], px: number, py: number): number {
  const [x, y] = toImage(v, px, py);
  let best = -1, area = Infinity;
  boxes.forEach((b, i) => {
    if (x >= b[0] && x <= b[0] + b[2] && y >= b[1] && y <= b[1] + b[3] && b[2] * b[3] < area) {
      area = b[2] * b[3];
      best = i;
    }
  });
  return best;
}

export function cursorFor(h: Pick<HandleHit, "left" | "right" | "top" | "bottom"> | null, overBox: boolean, drawing: boolean): string {
  if (drawing) return "crosshair";
  if (h) {
    if ((h.left && h.top) || (h.right && h.bottom)) return "nwse-resize";
    if ((h.right && h.top) || (h.left && h.bottom)) return "nesw-resize";
    return h.left || h.right ? "ew-resize" : "ns-resize";
  }
  return overBox ? "move" : "grab";
}

export interface Sides { left: boolean; right: boolean; top: boolean; bottom: boolean }

/** Desfase entre el puntero y el borde agarrado, para que la caja no «salte» al empezar a arrastrar. */
export function grabOffset(b0: Box, h: Sides, ix: number, iy: number): [number, number] {
  return [
    h.left || h.right ? ix - (h.left ? b0[0] : b0[0] + b0[2]) : 0,
    h.top || h.bottom ? iy - (h.top ? b0[1] : b0[1] + b0[3]) : 0,
  ];
}

/** Redimensiona desde la caja original. Cruzar el borde opuesto invierte el sentido (ancho siempre positivo). */
export function resizeBox(b0: Box, h: Sides, off: [number, number], ix: number, iy: number): Box {
  const x = ix - off[0], y = iy - off[1];
  let x0 = b0[0], x1 = b0[0] + b0[2], y0 = b0[1], y1 = b0[1] + b0[3];
  if (h.left) x0 = x;
  if (h.right) x1 = x;
  if (h.top) y0 = y;
  if (h.bottom) y1 = y;
  return [
    Math.round(Math.min(x0, x1)), Math.round(Math.min(y0, y1)),
    Math.max(MIN_BOX, Math.round(Math.abs(x1 - x0))), Math.max(MIN_BOX, Math.round(Math.abs(y1 - y0))),
  ];
}

export function moveBox(b0: Box, dx: number, dy: number): Box {
  return [Math.round(b0[0] + dx), Math.round(b0[1] + dy), b0[2], b0[3]];
}

/** Caja a partir de dos puntos de imagen (dibujar una nueva). Devuelve null si es demasiado pequeña. */
export function boxFromPoints(ax: number, ay: number, bx: number, by: number): Box | null {
  const w = Math.abs(bx - ax), h = Math.abs(by - ay);
  if (w <= MIN_BOX || h <= MIN_BOX) return null;
  return [Math.round(Math.min(ax, bx)), Math.round(Math.min(ay, by)), Math.round(w), Math.round(h)];
}

/** El lienzo debe tener bitmap = tamaño CSS × DPR; si no, lo dibujado se desplaza respecto al ratón. */
export function canvasBitmapSize(cssW: number, cssH: number, dpr: number): [number, number] {
  return [Math.max(1, Math.round(cssW * dpr)), Math.max(1, Math.round(cssH * dpr))];
}

/**
 * Posición del panel flotante junto a una caja: arriba (deja visible su etiqueta), si no abajo, a la derecha o a la
 * izquierda; siempre dentro del lienzo y tapando lo menos posible las otras cajas (rectángulos en pantalla).
 */
export function placePopover(pw: number, ph: number, anchor: Box, others: Box[], cssW: number, cssH: number): [number, number] {
  const [bx, by, bw, bh] = anchor, m = 6, gap = 24;
  const cover = (x: number, y: number) =>
    others.reduce((a, [ox, oy, ow, oh]) => a + Math.max(0, Math.min(x + pw, ox + ow) - Math.max(x, ox)) * Math.max(0, Math.min(y + ph, oy + oh) - Math.max(y, oy)), 0);
  const cands: [number, number][] = [
    [bx, by - ph - gap], [bx, by + bh + m], [bx + bw + m, by], [bx - pw - m, by],
    [bx + bw - pw, by + bh + m], [bx + bw - pw, by - ph - gap], [bx + bw + m, by + bh - ph], [bx - pw - m, by + bh - ph],
  ];
  let best: [number, number] = [0, 0], bs = Infinity;
  for (const [x, y] of cands) {
    const cx = Math.min(Math.max(x, m), cssW - pw - m), cy = Math.min(Math.max(y, m), cssH - ph - m);
    const fits = Math.abs(cx - x) < 1 && Math.abs(cy - y) < 1;
    const sc = cover(cx, cy) + (fits ? 0 : 1e9);
    if (sc < bs) { bs = sc; best = [cx, cy]; }
  }
  return best;
}
