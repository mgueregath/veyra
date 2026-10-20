import type { ClassDef, FrameDetail, Status } from "../api/client";
import type { BoxEditor, EditorUi } from "../editor/BoxEditor";
import Icon from "./Icon";

const nb = { onMouseDown: (e: React.MouseEvent) => e.preventDefault() };
export const STATUS_TEXT: Record<Status, string> = {
  pending: "Sin revisar", single: "Revisada por 1 persona", agreed: "De acuerdo", disagreement: "Desacuerdo", final: "Final",
};
const STATUS_TIP: Record<Status, string> = {
  pending: "Nadie ha revisado esta imagen todavía", single: "Una persona la revisó", agreed: "Varias personas la revisaron y coinciden",
  disagreement: "Varias personas la revisaron y NO coinciden: necesita un revisor", final: "Un revisor fijó la versión final",
};

const ago = (iso: string) => {
  const m = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  return m < 1 ? "ahora" : m < 60 ? `hace ${m} min` : m < 1440 ? `hace ${Math.round(m / 60)} h` : `hace ${Math.round(m / 1440)} d`;
};

/** Barra bajo la cabecera: imagen actual y su estado, herramientas, clase activa y zoom. */
export default function Toolbar({ index, detail, editor, ui, classes, activeClass, canWrite, lockHolder, collab, onClass }: {
  index: number; detail: FrameDetail | null; editor: BoxEditor | null; ui: EditorUi | undefined; classes: ClassDef[]; activeClass: string | undefined;
  canWrite: boolean; lockHolder: string | null; collab: boolean; onClass: (id: string) => void;
}) {
  const status: Status = detail?.status ?? "pending";
  const pos = classes.filter((c) => !c.negative);
  const sel = ui?.tool !== "pan";
  return (
    <div id="toolbar" className="toolbar">
      <div className="tb-title">
        <h1 id="imgtitle" title="Número de la imagen actual dentro de la lista">Imagen {index >= 0 ? index + 1 : "—"}</h1>
        <span id="statuschip" className={"pill st-" + status} title={(collab && status === "final" ? "Colaborativo: la última modificación es la versión final" : STATUS_TIP[status]) + (detail?.assigned_to_me ? " · está en tu cola" : "")}><i className="dot" />{collab && status === "final" && detail?.last_edit ? `Editada por ${detail.last_edit.user} · ${ago(detail.last_edit.at)}` : collab && status === "final" ? "Revisada" : STATUS_TEXT[status]}</span>
        {lockHolder && <span id="lockwarn" className="pill warn" title="Otra persona tiene abierta esta misma imagen; puedes seguir, pero conviene coordinarse"><Icon name="alert" size={13} />{lockHolder} también la tiene abierta</span>}
      </div>
      <div className="tb-tools">
        <button id="tool-pan" className={"tbtn sq" + (!sel ? " on" : "")} title="Mano: arrastrar mueve la imagen (H). Con la rueda del ratón se hace zoom" onClick={() => editor?.setTool("pan")} {...nb}><Icon name="hand" size={17} /></button>
        <button id="tool-select" className={"tbtn sq" + (sel && !ui?.drawMode ? " on" : "")} title="Seleccionar: presionar una caja la selecciona y la etiqueta; arrastrar la mueve o cambia su tamaño (V)" onClick={() => { editor?.setTool("select"); editor?.setDrawMode(false); }} {...nb}><Icon name="square" size={17} /></button>
        <button id="bbox" className={"tbtn" + (ui?.drawMode ? " on" : "")} disabled={!canWrite} title="Dibujar una caja nueva sobre la imagen (B); recibe la clase activa" onClick={() => editor?.setDrawMode(!editor.drawMode)} {...nb}><Icon name="plus" size={15} /> Caja <kbd>B</kbd></button>
        <span className="tb-div" />
        {pos.map((c) => (
          <button key={c.id} id={"cls_" + c.id} className={"tbtn chipc" + (activeClass === c.id ? " on" : "")} title={`Clase ${c.name} (tecla ${c.digit}): es lo que recibe una caja al presionarla, moverla o dibujarla`} onClick={() => onClass(c.id)} {...nb}>
            <i className="dot" style={{ background: c.color }} />{c.name}<kbd>{c.digit}</kbd>
          </button>
        ))}
      </div>
      <div className="tb-zoom">
        <div className="zgroup" title="Zoom de la imagen">
          <button id="zout" className="zbtn" title="Alejar la imagen (tecla −)" aria-label="Alejar" onClick={() => editor?.zoomBy(1 / 1.25)} {...nb}><Icon name="minus" size={15} /></button>
          <button id="zoom" className="zval" title="Clic para ajustar la imagen a la ventana (F)" onClick={() => editor?.fit()} {...nb}>{ui?.zoomPct ?? 100} %</button>
          <button id="zin" className="zbtn" title="Acercar la imagen (tecla +); con una caja seleccionada el zoom se centra en ella" aria-label="Acercar" onClick={() => editor?.zoomBy(1.25)} {...nb}><Icon name="plus" size={15} /></button>
        </div>
        <button id="zfit" className="tbtn sq" title="Ajustar la imagen a la ventana (F)" aria-label="Ajustar" onClick={() => editor?.fit()} {...nb}><Icon name="fit" size={18} /></button>
      </div>
    </div>
  );
}
