import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { Saver } from "./saver";

afterEach(() => vi.restoreAllMocks());
const ann = (n: number) => ({ reviewed: false, boxes: [{ bbox: [n, n, 10, 10] as [number, number, number, number], label: null, manual: false, sources: [], score: null, deleted: false }] });

describe("Saver", () => {
  it("funde cambios seguidos de una imagen y nunca solapa sus peticiones", async () => {
    const sent: number[] = [];
    let active = 0, maxActive = 0;
    vi.spyOn(api, "save").mockImplementation(async (_id, a) => {
      active++; maxActive = Math.max(maxActive, active);
      await new Promise((r) => setTimeout(r, 10));
      sent.push(a.boxes[0].bbox[0]);
      active--;
      return a;
    });
    const done: boolean[] = [];
    const s = new Saver((_f, ok) => done.push(ok));
    s.queue(1, ann(1)); s.queue(1, ann(2)); s.queue(1, ann(3));
    await vi.waitFor(() => expect(s.busy).toBe(false));
    expect(maxActive).toBe(1);
    expect(sent[sent.length - 1]).toBe(3); // lo último siempre llega
    expect(sent.length).toBeLessThanOrEqual(2); // 1 en vuelo + lo último; el intermedio se funde
    expect(done.every(Boolean)).toBe(true);
  });
  it("imágenes distintas se guardan por separado", async () => {
    const sent: [number, number][] = [];
    vi.spyOn(api, "save").mockImplementation(async (id, a) => { sent.push([id, a.boxes[0].bbox[0]]); return a; });
    const s = new Saver(() => {});
    s.queue(1, ann(5)); s.queue(2, ann(6));
    await vi.waitFor(() => expect(s.busy).toBe(false));
    expect(sent.sort()).toEqual([[1, 5], [2, 6]]);
  });
  it("un error se informa y no bloquea los siguientes guardados", async () => {
    let n = 0;
    vi.spyOn(api, "save").mockImplementation(async (_i, a) => { if (n++ === 0) throw new Error("boom"); return a; });
    const res: [boolean, string | undefined][] = [];
    const s = new Saver((_f, ok, err) => res.push([ok, err]));
    s.queue(1, ann(1));
    await vi.waitFor(() => expect(res.length).toBe(1));
    s.queue(1, ann(2));
    await vi.waitFor(() => expect(res.length).toBe(2));
    expect(res).toEqual([[false, "boom"], [true, undefined]]);
  });
});
