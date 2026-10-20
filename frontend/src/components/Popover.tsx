import { useLayoutEffect, useRef, useState } from "react";
import type { ClassDef } from "../api/client";
import type { BoxEditor, EditorUi } from "../editor/BoxEditor";
import { placePopover } from "../editor/geometry";

const nb = { onMouseDown: (e: React.MouseEvent) => e.preventDefault() };
const SRC_LONG: Record<string, string> = {};

/** Controles de etiquetado: un panel FLOTANTE sobre la imagen, junto a la caja seleccionada (y, tras borrar, un aviso para restaurar). */
export default function Popover({ editor, ui, classes, sources }: {
  editor: BoxEditor; ui: EditorUi; classes: ClassDef[]; sources: Record<string, { short: string; name: string }>;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<[number, number]>([0, 0]);
  const b = ui.sel >= 0 ? editor.doc?.boxes[ui.sel] : null;
  useLayoutEffect(() => {
    if (!ui.anchor || !ref.current) return;
    setPos(placePopover(ref.current.offsetWidth || 252, ref.current.offsetHeight || 130, ui.anchor, ui.others, ui.cssW, ui.cssH));
  }, [ui, b?.label, b?.orig_bbox]);
  if (!ui.anchor) return <div id="pop" hidden />;

  if (ui.undo) {
    return (
      <div id="pop" ref={ref} style={{ left: pos[0], top: pos[1] }}>
        <div className="ph"><b>Cuadro borrado</b><span className="pk">{ui.deletedCount} en esta imagen</span></div>
        <div className="pb2">
          <button title="Restaurar el último (Z)" onClick={() => editor.restore(false)} {...nb}>Restaurar <kbd>Z</kbd></button>
          <button title="Restaurar todos los borrados de esta imagen" onClick={() => editor.restore(true)} {...nb}>Restaurar todos</button>
        </div>
      </div>
    );
  }
  if (!b) return <div id="pop" hidden />;
  const cls = classes.find((c) => c.id === b.label);
  const color = cls?.color ?? "var(--unl)";
  const srcs = (b.manual ? ["manual"] : b.sources).map((x) => sources[x]?.name ?? SRC_LONG[x] ?? x).join(" + ");
  return (
    <div id="pop" ref={ref} style={{ left: pos[0], top: pos[1] }}>
      <div className="ph">
        <span className="badge" style={{ color, borderColor: color }}>{cls ? cls.name : "Sin etiquetar"}</span>
        <span className="pk">{srcs}{b.manual ? "" : " · puntaje " + (+(b.score ?? 0)).toFixed(1)}</span>
      </div>
      <div className="pk">{b.bbox[2]} × {b.bbox[3]} px{b.orig_bbox ? " · movida" : ""}</div>
      <div className="pb">
        {classes.map((c) => (
          <button key={c.id} className={b.label === c.id ? "on" : ""} title={`${c.name}${c.key ? ` (${c.key.toUpperCase()})` : ""}`} onClick={() => editor.setLabel(c.id)} {...nb}>
            {c.negative ? "Ni uno" : c.name} {c.key && <kbd>{c.key.toUpperCase()}</kbd>}
          </button>
        ))}
      </div>
      <div className="pb">
        <button title="Cambiar piojo ⇄ mosca (T)" onClick={() => editor.toggleSel()} {...nb}>⇄ <kbd>T</kbd></button>
        <button title="Quitar etiqueta (U)" onClick={() => editor.setLabel(null)} {...nb}>Quitar <kbd>U</kbd></button>
        <button title="Borrar caja (X)" onClick={() => editor.deleteSel()} {...nb}>Borrar <kbd>X</kbd></button>
      </div>
      {b.orig_bbox && <button title="Volver a la posición original" onClick={() => editor.resetPos()} {...nb}>↩ Posición original</button>}
    </div>
  );
}
