/**
 * Modelo de edición de UNA imagen: lógica pura (sin DOM) de etiquetar, borrar y restaurar cajas.
 *
 * `boxes` son las cajas vivas; `deleted` es la pila de borradas (la última borrada es la primera en restaurarse).
 * Al guardar se envían todas juntas, las borradas con `deleted: true` (borrado suave: se pueden restaurar, también
 * después de recargar, y la evaluación las cuenta como falso positivo del detector).
 */
import type { Annotation, AnnBox, ClassDef, FrameDetail } from "../api/client";
import type { Box } from "./geometry";

export interface EBox {
  bbox: Box;
  label: string | null;
  manual: boolean;
  sources: string[];
  score: number | null;
  proposal_id: number | null;
  orig_bbox: Box | null; // posición original si la caja se movió o redimensionó
}
export interface EditDoc {
  boxes: EBox[];
  deleted: EBox[];
  reviewed: boolean;
}

const toE = (b: AnnBox): EBox => ({
  bbox: [...b.bbox] as Box,
  label: b.label,
  manual: b.manual,
  sources: [...b.sources],
  score: b.score,
  proposal_id: b.proposal_id ?? null,
  orig_bbox: b.orig_bbox ? ([...b.orig_bbox] as Box) : null,
});
const fromE = (b: EBox, deleted: boolean): AnnBox => ({
  proposal_id: b.proposal_id,
  bbox: [...b.bbox] as Box,
  label: b.label,
  manual: b.manual,
  orig_bbox: b.orig_bbox ? [...b.orig_bbox] : null,
  sources: b.sources,
  score: b.score,
  deleted,
});

/** Con anotación propia se usa esa; si no, las propuestas del detector sin etiquetar (aún no se guarda nada). */
export function docFromFrame(f: FrameDetail): EditDoc {
  if (f.annotation) {
    return {
      reviewed: f.annotation.reviewed,
      boxes: f.annotation.boxes.filter((b) => !b.deleted).map(toE),
      deleted: f.annotation.boxes.filter((b) => b.deleted).map(toE),
    };
  }
  return {
    reviewed: false,
    deleted: [],
    boxes: f.proposals.map((p) => ({
      bbox: [...p.bbox] as Box,
      label: null,
      manual: false,
      sources: [...p.sources],
      score: p.score,
      proposal_id: p.id,
      orig_bbox: null,
    })),
  };
}

const iouOf = (a: Box, b: Box) => {
  const iw = Math.min(a[0] + a[2], b[0] + b[2]) - Math.max(a[0], b[0]), ih = Math.min(a[1] + a[3], b[1] + b[3]) - Math.max(a[1], b[1]);
  if (iw <= 0 || ih <= 0) return 0;
  const inter = iw * ih;
  return inter / (a[2] * a[3] + b[2] * b[3] - inter);
};

/**
 * Refresca el etiquetado asistido: quita las cajas del detector (`tag`) que aún no se etiquetaron ni tocaron y agrega las nuevas.
 * Lo que la persona ya hizo (etiquetadas, movidas, manuales, de otras fuentes) se conserva; una propuesta nueva que coincide
 * con una caja ya trabajada (etiquetada, movida, manual o borrada) no se duplica. Devuelve cuántas se quitaron y cuántas se agregaron.
 */
export function applyRefresh(d: EditDoc, tag: string, proposals: { id: number; bbox: Box; sources: string[]; score: number | null }[]) {
  const before = d.boxes.length;
  d.boxes = d.boxes.filter((b) => b.manual || b.label !== null || b.orig_bbox !== null || !b.sources.includes(tag));
  const removed = before - d.boxes.length;
  let added = 0;
  for (const p of proposals) {
    if (d.boxes.some((b) => (b.manual || b.label !== null || b.orig_bbox !== null) && iouOf(b.bbox, p.bbox as Box) >= 0.5) || d.deleted.some((b) => iouOf(b.bbox, p.bbox as Box) >= 0.5)) continue;
    d.boxes.push({ bbox: [...p.bbox] as Box, label: null, manual: false, sources: [...p.sources], score: p.score, proposal_id: p.id, orig_bbox: null });
    added++;
  }
  return { removed, added };
}

export function docToAnnotation(d: EditDoc): Annotation {
  return { reviewed: d.reviewed, boxes: [...d.boxes.map((b) => fromE(b, false)), ...d.deleted.map((b) => fromE(b, true))] };
}

export const negativeClass = (classes: ClassDef[]) => classes.find((c) => c.negative)?.id ?? null;
export const positiveClasses = (classes: ClassDef[]) => classes.filter((c) => !c.negative);

export function newManualBox(bbox: Box, label: string): EBox {
  return { bbox, label, manual: true, sources: ["manual"], score: 0, proposal_id: null, orig_bbox: null };
}

export function setLabel(d: EditDoc, sel: number, label: string | null): boolean {
  const b = d.boxes[sel];
  if (!b || b.label === label) return false;
  b.label = label;
  return true;
}

/** T: alterna entre las clases positivas (piojo ⇄ mosca); si la caja no tiene una, recibe la clase activa. */
export function toggleLabel(d: EditDoc, sel: number, classes: ClassDef[], active: string): boolean {
  const b = d.boxes[sel];
  if (!b) return false;
  const pos = positiveClasses(classes).map((c) => c.id);
  const i = b.label ? pos.indexOf(b.label) : -1;
  return setLabel(d, sel, i >= 0 ? pos[(i + 1) % pos.length] : active);
}

/** Borra la caja `sel` (pasa a la pila de borradas). Devuelve la caja borrada. */
export function deleteBox(d: EditDoc, sel: number): EBox | null {
  if (!d.boxes[sel]) return null;
  const [b] = d.boxes.splice(sel, 1);
  d.deleted.push(b);
  return b;
}

/** Restaura la última borrada (o todas). Devuelve el índice de la caja a seleccionar, o -1. */
export function restoreBoxes(d: EditDoc, all: boolean): number {
  if (!d.deleted.length) return -1;
  if (all) {
    d.boxes.push(...d.deleted);
    d.deleted = [];
    return -1;
  }
  d.boxes.push(d.deleted.pop()!);
  return d.boxes.length - 1;
}

export function resetPosition(d: EditDoc, sel: number): boolean {
  const b = d.boxes[sel];
  if (!b?.orig_bbox) return false;
  b.bbox = [...b.orig_bbox] as Box;
  b.orig_bbox = null;
  return true;
}

/** Tab: siguiente caja sin etiquetar (que no sea dibujada a mano). Devuelve su índice o -1. */
export function nextUnlabeled(d: EditDoc, sel: number): number {
  const n = d.boxes.length;
  for (let k = 1; k <= n; k++) {
    const i = (((sel + k) % n) + n) % n;
    if (!d.boxes[i].label && !d.boxes[i].manual) return i;
  }
  return -1;
}

/** «Revisado»: lo que no se tocó pasa a la clase negativa y la imagen queda marcada como revisada. */
export function markReviewed(d: EditDoc, negative: string | null): void {
  if (negative) d.boxes.forEach((b) => { if (!b.label) b.label = negative; });
  d.reviewed = true;
}

export function countLabels(d: EditDoc): { byLabel: Record<string, number>; unlabeled: number; manual: number } {
  const byLabel: Record<string, number> = {};
  let unlabeled = 0, manual = 0;
  for (const b of d.boxes) {
    if (b.label) byLabel[b.label] = (byLabel[b.label] ?? 0) + 1;
    else unlabeled++;
    if (b.manual) manual++;
  }
  return { byLabel, unlabeled, manual };
}
