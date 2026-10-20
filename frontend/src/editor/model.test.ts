import { describe, expect, it } from "vitest";
import type { ClassDef, FrameDetail } from "../api/client";
import { deleteBox, docFromFrame, docToAnnotation, markReviewed, negativeClass, newManualBox, nextUnlabeled, resetPosition, restoreBoxes, setLabel, toggleLabel, countLabels } from "./model";

const classes: ClassDef[] = [
  { id: "louse", name: "Piojo", color: "#0f0" },
  { id: "fly", name: "Mosca", color: "#a0f" },
  { id: "not_louse", name: "Ni uno", color: "#f00", negative: true },
];
const frame = (annotation: FrameDetail["annotation"]): FrameDetail => ({
  id: 1, position: 0, video: "v", video_id: 1, has_video: true, frame_index: 0, t: 0, width: 100, height: 80, stratum: null, n_annotators: 0, status: "pending", n_reviewed: 0, locked_by: null, assigned_to_me: false, review: null,
  proposals: [
    { id: 11, bbox: [1, 1, 10, 10], sources: ["blob"], score: 7 },
    { id: 12, bbox: [30, 30, 10, 10], sources: ["motion"], score: 4 },
  ],
  annotation,
});

describe("docFromFrame", () => {
  it("sin anotación propia parte de las propuestas, sin etiquetar", () => {
    const d = docFromFrame(frame(null));
    expect(d.boxes.map((b) => [b.proposal_id, b.label])).toEqual([[11, null], [12, null]]);
    expect(d.reviewed).toBe(false);
  });
  it("con anotación separa vivas y borradas, y ida y vuelta conserva todo", () => {
    const ann = {
      reviewed: true,
      boxes: [
        { bbox: [1, 1, 10, 10] as [number, number, number, number], label: "louse", manual: false, sources: ["blob"], score: 7, proposal_id: 11, deleted: false, orig_bbox: [0, 0, 10, 10] },
        { bbox: [30, 30, 10, 10] as [number, number, number, number], label: null, manual: false, sources: ["motion"], score: 4, proposal_id: 12, deleted: true },
      ],
    };
    const d = docFromFrame(frame(ann));
    expect(d.boxes).toHaveLength(1);
    expect(d.deleted).toHaveLength(1);
    const back = docToAnnotation(d);
    expect(back.boxes.map((b) => b.deleted)).toEqual([false, true]);
    expect(back.boxes[0].orig_bbox).toEqual([0, 0, 10, 10]);
    expect(back.reviewed).toBe(true);
  });
});

describe("etiquetar", () => {
  it("setLabel informa si hubo cambio", () => {
    const d = docFromFrame(frame(null));
    expect(setLabel(d, 0, "louse")).toBe(true);
    expect(setLabel(d, 0, "louse")).toBe(false);
    expect(setLabel(d, 9, "louse")).toBe(false);
    expect(setLabel(d, 0, null)).toBe(true);
  });
  it("T alterna piojo ⇄ mosca y, sin clase positiva, usa la activa", () => {
    const d = docFromFrame(frame(null));
    toggleLabel(d, 0, classes, "fly");
    expect(d.boxes[0].label).toBe("fly");
    toggleLabel(d, 0, classes, "fly");
    expect(d.boxes[0].label).toBe("louse");
    toggleLabel(d, 0, classes, "fly");
    expect(d.boxes[0].label).toBe("fly");
    setLabel(d, 0, "not_louse");
    toggleLabel(d, 0, classes, "louse");
    expect(d.boxes[0].label).toBe("louse");
  });
  it("revisado: lo no tocado pasa a la clase negativa", () => {
    const d = docFromFrame(frame(null));
    setLabel(d, 0, "louse");
    markReviewed(d, negativeClass(classes));
    expect(d.boxes.map((b) => b.label)).toEqual(["louse", "not_louse"]);
    expect(d.reviewed).toBe(true);
  });
  it("Tab salta a la siguiente sin etiquetar y no a las dibujadas a mano", () => {
    const d = docFromFrame(frame(null));
    d.boxes.push(newManualBox([5, 5, 9, 9], "louse"));
    expect(nextUnlabeled(d, -1)).toBe(0);
    setLabel(d, 0, "louse");
    expect(nextUnlabeled(d, 0)).toBe(1);
    setLabel(d, 1, "fly");
    expect(nextUnlabeled(d, 1)).toBe(-1);
  });
  it("cuenta etiquetas, sin etiquetar y a mano", () => {
    const d = docFromFrame(frame(null));
    d.boxes.push(newManualBox([5, 5, 9, 9], "fly"));
    setLabel(d, 0, "louse");
    expect(countLabels(d)).toEqual({ byLabel: { louse: 1, fly: 1 }, unlabeled: 1, manual: 1 });
  });
});

describe("borrar y restaurar", () => {
  it("borrar saca la caja y la guarda; restaurar la deja IGUAL (caja, fuente, etiqueta)", () => {
    const d = docFromFrame(frame(null));
    setLabel(d, 0, "louse");
    const before = JSON.stringify(d.boxes[0]);
    deleteBox(d, 0);
    expect(d.boxes).toHaveLength(1);
    expect(d.deleted).toHaveLength(1);
    const sel = restoreBoxes(d, false);
    expect(sel).toBe(1);
    expect(JSON.stringify(d.boxes[sel])).toBe(before);
    expect(d.deleted).toHaveLength(0);
  });
  it("restaura primero la ÚLTIMA borrada y «todos» devuelve todas", () => {
    const d = docFromFrame(frame(null));
    deleteBox(d, 0);
    deleteBox(d, 0);
    expect(d.boxes).toHaveLength(0);
    expect(restoreBoxes(d, false)).toBe(0);
    expect(d.boxes[0].proposal_id).toBe(12);
    restoreBoxes(d, true);
    expect(d.boxes).toHaveLength(2);
    expect(d.deleted).toHaveLength(0);
    expect(restoreBoxes(d, true)).toBe(-1);
  });
  it("borrar un índice inexistente no hace nada", () => {
    const d = docFromFrame(frame(null));
    expect(deleteBox(d, 5)).toBeNull();
    expect(d.deleted).toHaveLength(0);
  });
  it("el orden de la pila sobrevive al guardado (borradas al final, en orden)", () => {
    const d = docFromFrame(frame(null));
    deleteBox(d, 1);
    deleteBox(d, 0);
    const a = docToAnnotation(d);
    expect(a.boxes.map((b) => [b.proposal_id, b.deleted])).toEqual([[12, true], [11, true]]);
    const d2 = docFromFrame(frame(a));
    expect(d2.deleted.map((b) => b.proposal_id)).toEqual([12, 11]);
    expect(d2.boxes[restoreBoxes(d2, false)].proposal_id).toBe(11);
  });
  it("volver a la posición original", () => {
    const d = docFromFrame(frame(null));
    expect(resetPosition(d, 0)).toBe(false);
    d.boxes[0].orig_bbox = d.boxes[0].bbox;
    d.boxes[0].bbox = [50, 50, 10, 10];
    expect(resetPosition(d, 0)).toBe(true);
    expect(d.boxes[0].bbox).toEqual([1, 1, 10, 10]);
    expect(d.boxes[0].orig_bbox).toBeNull();
  });
});
