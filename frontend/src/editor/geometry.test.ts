import { describe, expect, it } from "vitest";
import { boxAt, boxFromPoints, canvasBitmapSize, cursorFor, fitView, grabOffset, handleAt, moveBox, placePopover, resizeBox, toImage, toScreen, zoomAt, type Box } from "./geometry";

const V = { s: 1, x: 0, y: 0 };

describe("vista", () => {
  it("toScreen y toImage son inversas", () => {
    const v = { s: 0.5, x: 20, y: 10 };
    const [sx, sy] = toScreen(v, 300, 200);
    const [ix, iy] = toImage(v, sx, sy);
    expect(ix).toBeCloseTo(300);
    expect(iy).toBeCloseTo(200);
  });
  it("fitView encaja y centra", () => {
    const v = fitView(1920, 1080, 960, 800);
    expect(v.s).toBeCloseTo(0.5);
    expect(v.x).toBeCloseTo(0);
    expect(v.y).toBeCloseTo(130);
  });
  it("fitView con margen deja espacio por todos lados y nunca recorta (contain)", () => {
    const v = fitView(1920, 1080, 960, 800, 24);
    expect(v.s).toBeCloseTo((960 - 48) / 1920); // manda el ancho
    expect(v.x).toBeCloseTo(24);
    expect(v.y).toBeGreaterThanOrEqual(24);
    expect(1920 * v.s + 2 * v.x).toBeCloseTo(960);
    const t = fitView(1080, 1920, 960, 800, 24); // imagen vertical: manda el alto
    expect(t.s).toBeCloseTo((800 - 48) / 1920);
    expect(t.x).toBeGreaterThanOrEqual(24);
    expect(fitView(100, 100, 40, 40, 24).s).toBeGreaterThan(0); // contenedor diminuto: no revienta
  });
  it("zoomAt deja fijo el punto de pantalla y respeta los límites", () => {
    const v = { s: 0.5, x: 20, y: 10 };
    const [ix, iy] = toImage(v, 200, 150);
    const v2 = zoomAt(v, 2, 200, 150);
    const [sx, sy] = toScreen(v2, ix, iy);
    expect(sx).toBeCloseTo(200);
    expect(sy).toBeCloseTo(150);
    expect(zoomAt(v, 1e6, 0, 0).s).toBe(20);
    expect(zoomAt(v, 1e-6, 0, 0).s).toBe(0.05);
  });
});

describe("asas de redimensión", () => {
  const b: Box = [100, 100, 200, 100]; // en pantalla (zoom 1): x 100..300, y 100..200
  it("las cuatro esquinas permiten los dos ejes, también las INFERIORES", () => {
    expect(handleAt(V, [b], -1, 100, 100)).toMatchObject({ corner: true, left: true, top: true });
    expect(handleAt(V, [b], -1, 300, 100)).toMatchObject({ corner: true, right: true, top: true });
    expect(handleAt(V, [b], -1, 300, 200)).toMatchObject({ corner: true, right: true, bottom: true });
    expect(handleAt(V, [b], -1, 100, 200)).toMatchObject({ corner: true, left: true, bottom: true });
  });
  it("clic 6 px fuera de la esquina también redimensiona", () => {
    expect(handleAt(V, [b], -1, 306, 206)).toMatchObject({ corner: true, right: true, bottom: true });
  });
  it("bordes: un solo eje", () => {
    expect(handleAt(V, [b], -1, 200, 100)).toMatchObject({ corner: false, top: true, left: false, right: false });
    expect(handleAt(V, [b], -1, 200, 200)).toMatchObject({ corner: false, bottom: true });
    expect(handleAt(V, [b], -1, 100, 150)).toMatchObject({ corner: false, left: true });
    expect(handleAt(V, [b], -1, 300, 150)).toMatchObject({ corner: false, right: true });
  });
  it("interior y vacío no son asas", () => {
    expect(handleAt(V, [b], -1, 200, 150)).toBeNull();
    expect(handleAt(V, [b], -1, 600, 600)).toBeNull();
  });
  it("la caja seleccionada gana el empate en esquinas compartidas", () => {
    const a: Box = [100, 100, 100, 100], c: Box = [100, 100, 100, 100];
    expect(handleAt(V, [a, c], 1, 100, 100)?.idx).toBe(1);
    expect(handleAt(V, [a, c], 0, 100, 100)?.idx).toBe(0);
  });
  it("en una caja chica el interior sigue siendo accesible para moverla", () => {
    const tiny: Box = [100, 100, 20, 20];
    expect(handleAt(V, [tiny], -1, 110, 110)).toBeNull();
  });
  it("cursores", () => {
    expect(cursorFor({ left: false, right: true, top: false, bottom: true }, true, false)).toBe("nwse-resize");
    expect(cursorFor({ left: false, right: true, top: true, bottom: false }, true, false)).toBe("nesw-resize");
    expect(cursorFor({ left: false, right: true, top: false, bottom: false }, true, false)).toBe("ew-resize");
    expect(cursorFor({ left: false, right: false, top: true, bottom: false }, true, false)).toBe("ns-resize");
    expect(cursorFor(null, true, false)).toBe("move");
    expect(cursorFor(null, false, false)).toBe("grab");
    expect(cursorFor(null, false, true)).toBe("crosshair");
  });
  it("boxAt elige la caja MÁS PEQUEÑA que contiene el punto", () => {
    const big: Box = [0, 0, 400, 400], small: Box = [100, 100, 50, 50];
    expect(boxAt(V, [big, small], 120, 120)).toBe(1);
    expect(boxAt(V, [big, small], 300, 300)).toBe(0);
    expect(boxAt(V, [big, small], 500, 500)).toBe(-1);
  });
});

describe("mover y redimensionar", () => {
  const o: Box = [100, 100, 200, 100];
  const sides = (s: string) => ({ left: s.includes("l"), right: s.includes("r"), top: s.includes("t"), bottom: s.includes("b") });
  it("el borde derecho agranda solo el ancho", () => {
    const off = grabOffset(o, sides("r"), 300, 150);
    expect(resizeBox(o, sides("r"), off, 350, 190, )).toEqual([100, 100, 250, 100]);
  });
  it("el borde izquierdo crece hacia la izquierda con el derecho fijo", () => {
    const off = grabOffset(o, sides("l"), 100, 150);
    expect(resizeBox(o, sides("l"), off, 60, 150)).toEqual([60, 100, 240, 100]);
  });
  it("esquina superior izquierda achica con la opuesta fija", () => {
    const off = grabOffset(o, sides("lt"), 100, 100);
    expect(resizeBox(o, sides("lt"), off, 120, 130)).toEqual([120, 130, 180, 70]);
  });
  it("esquina inferior derecha cambia ancho y alto a la vez", () => {
    const off = grabOffset(o, sides("rb"), 300, 200);
    expect(resizeBox(o, sides("rb"), off, 340, 260)).toEqual([100, 100, 240, 160]);
  });
  it("agarrar a 6 px por dentro no hace saltar la caja", () => {
    const off = grabOffset(o, sides("rb"), 294, 194);
    expect(resizeBox(o, sides("rb"), off, 294, 194)).toEqual(o);
  });
  it("cruzar el borde opuesto invierte el sentido: ancho positivo", () => {
    const off = grabOffset(o, sides("r"), 300, 150);
    expect(resizeBox(o, sides("r"), off, 40, 150)).toEqual([40, 100, 60, 100]);
  });
  it("tamaño mínimo", () => {
    const off = grabOffset(o, sides("r"), 300, 150);
    expect(resizeBox(o, sides("r"), off, 100, 150)[2]).toBe(8);
  });
  it("mover conserva el tamaño", () => {
    expect(moveBox(o, 10.4, -20.6)).toEqual([110, 79, 200, 100]);
  });
});

describe("dibujo, lienzo y panel", () => {
  it("boxFromPoints normaliza el sentido y descarta cajas diminutas", () => {
    expect(boxFromPoints(50, 60, 10, 20)).toEqual([10, 20, 40, 40]);
    expect(boxFromPoints(10, 10, 15, 40)).toBeNull();
  });
  it("bitmap = CSS × DPR", () => {
    expect(canvasBitmapSize(736, 400, 1.25)).toEqual([920, 500]);
    expect(canvasBitmapSize(0, 0, 2)).toEqual([1, 1]);
  });
  it("el panel va arriba de la caja si cabe", () => {
    expect(placePopover(250, 130, [300, 400, 100, 100], [], 1000, 800)).toEqual([300, 400 - 130 - 24]);
  });
  it("el panel no tapa una caja vecina si hay otro sitio", () => {
    const anchor: Box = [300, 400, 100, 100];
    const neighbour: Box = [300, 240, 250, 130]; // justo donde iría arriba
    const [x, y] = placePopover(250, 130, anchor, [neighbour], 1000, 800);
    const overlap = Math.max(0, Math.min(x + 250, 550) - Math.max(x, 300)) * Math.max(0, Math.min(y + 130, 370) - Math.max(y, 240));
    expect(overlap).toBe(0);
  });
  it("el panel siempre queda dentro del lienzo", () => {
    const [x, y] = placePopover(250, 130, [0, 0, 50, 50], [], 400, 300);
    expect(x).toBeGreaterThanOrEqual(0);
    expect(y).toBeGreaterThanOrEqual(0);
    expect(x + 250).toBeLessThanOrEqual(400);
    expect(y + 130).toBeLessThanOrEqual(300);
  });
});
