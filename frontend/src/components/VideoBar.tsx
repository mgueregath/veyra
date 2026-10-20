import { BOX_MODE_LABEL, SPEEDS, type PlayerUi, type VideoPlayer } from "../editor/player";

const nb = { onMouseDown: (e: React.MouseEvent) => e.preventDefault() };

/**
 * Controles del video original: SIEMPRE visibles, en una barra bajo la imagen (no en una tarjeta que se abre y se cierra).
 * El video no se reproduce solo ni se pide al servidor hasta que se pulsa reproducir, mover la barra o avanzar un fotograma.
 */
export default function VideoBar({ player, ui }: { player: VideoPlayer; ui: PlayerUi }) {
  const off = !ui.ready;
  return (
    <div id="vbar">
      <div className="vbrow">
        <span className={"chip" + (ui.chipOk ? " ok" : "")} id="vstat" title="Qué estás viendo: la imagen con sus cajas o un fotograma del video, y a cuántos segundos de la foto">{ui.chip}</span>
        <span id="vwin" title="Ventana de video disponible: 5 segundos antes y 5 después del instante de la imagen">{ui.win}</span>
        <div className="vtl">
          <input id="vseek" type="range" min={0} max={1000} step={1} value={ui.seek} disabled={off} aria-label="Posición dentro de la ventana de 10 s"
            title="Mueve la posición dentro de los 10 segundos de video; la marca verde es el instante de la imagen"
            onChange={(e) => { void player.seekFraction(+e.target.value / 1000); }} onMouseUp={(e) => (e.target as HTMLElement).blur()} />
          <div id="vmark" title="Instante de la imagen dentro de la ventana" style={{ left: `calc(${ui.markPct.toFixed(2)}% - 1px)` }} />
        </div>
        <div className="vtime" title="Instante del fotograma que se ve y su distancia a la imagen (FOTO = es la imagen)"><span id="vnow">{ui.now}</span><span id="vrel">{ui.rel}</span></div>
      </div>
      <div className="vbrow">
        <div className="vtransport">
          <button id="vstart" disabled={off} title="Ir al inicio de la ventana: 5 segundos antes de la imagen (Inicio)" onClick={() => player.startWindow()} {...nb}>⏮</button>
          <button id="vm10" disabled={off} title="Retroceder 10 fotogramas (<)" onClick={() => void player.step(-10)} {...nb}>−10</button>
          <button id="vm1" disabled={off} title="Retroceder 1 fotograma (,)" onClick={() => void player.step(-1)} {...nb}>◀</button>
          <button id="vplay" disabled={off} title="Reproducir o pausar el video (P). Al reproducir desde la imagen arranca 5 s antes" onClick={() => void player.toggle()} {...nb}>{ui.playing ? "⏸" : "▶"}</button>
          <button id="vp1" disabled={off} title="Avanzar 1 fotograma (.)" onClick={() => void player.step(1)} {...nb}>▶</button>
          <button id="vp10" disabled={off} title="Avanzar 10 fotogramas (>)" onClick={() => void player.step(10)} {...nb}>+10</button>
        </div>
        <button id="vsnap" className={ui.view === "foto" ? "on" : ""} disabled={off} title="Volver a la imagen con sus cajas y pausar el video (R)" onClick={() => player.back()} {...nb}>↩ Volver a la imagen</button>
        <button id="vsave" disabled={off || ui.saving} title="Guardar el fotograma que se ve como una imagen nueva del proyecto para etiquetarla (I)" onClick={() => void player.save()} {...nb}>Guardar instantánea</button>
        <div className="vspd" title="Velocidad de reproducción">
          <span>Velocidad</span>
          <button id="vslow" disabled={off} title="Reproducir más lento ([)" onClick={() => player.rateStep(-1)} {...nb}>−</button>
          <select id="vrate" title="Velocidad de reproducción: de 0,1× a 4×" value={String(ui.rate)} onChange={(e) => { player.setRate(parseFloat(e.target.value) || 1); e.target.blur(); }}>
            {SPEEDS.map((s) => <option key={s}>{s}</option>)}
          </select>
          <span id="vspd">{String(ui.rate).replace(".", ",")}×</span>
          <button id="vfast" disabled={off} title="Reproducir más rápido (])" onClick={() => player.rateStep(1)} {...nb}>+</button>
        </div>
        <label className="vloop"><input type="checkbox" id="vloop" title="Al llegar al final de la ventana de 10 s, volver al principio y seguir reproduciendo" checked={ui.loop} onChange={(e) => { player.setLoop(e.target.checked); e.target.blur(); }} /> Repetir</label>
        <button id="vboxes" title="Cuándo se muestran las cajas durante el video (O). Por defecto, solo en el fotograma de la imagen" onClick={() => player.cycleBoxes()} {...nb}>Cuadros: {BOX_MODE_LABEL[ui.boxes]}</button>
      </div>
    </div>
  );
}
