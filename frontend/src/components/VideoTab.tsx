import { BOX_MODE_LABEL, BOX_MODES, SPEEDS, type PlayerUi, type VideoPlayer } from "../editor/player";
import Icon from "./Icon";

/**
 * Controles del video original en el panel derecho. El video no se reproduce solo ni se pide al servidor hasta que se
 * pulsa reproducir, se mueve la barra o se avanza un fotograma; «Volver a la imagen» regresa a la imagen con sus cajas.
 */
export default function VideoTab({ player, ui }: { player: VideoPlayer; ui: PlayerUi }) {
  const off = !ui.ready;
  return (
    <section id="vtab" className="sec2">
      <div className="ph-head">
        <h2 title="Reproduce el video original alrededor del instante de la imagen">Video original</h2>
        <span id="vplaystate" className={"pill " + (ui.playing ? "ok" : "warn")} title={ui.playing ? "El video se está reproduciendo" : "El video está en pausa"}>
          <Icon name={ui.playing ? "play" : "pause"} size={10} />{ui.playing ? "Reproduciendo" : "En pausa"}
        </span>
      </div>
      <div className="sub vsubrow">
        <span id="vwin" title={ui.win || "Ventana de video disponible: 5 segundos antes y 5 después del instante de la imagen"}>Ventana de la imagen · ±5 s</span>
        <span id="vstat" className={"vchip" + (ui.chipOk ? " ok" : "")} title="Qué estás viendo: la imagen con sus cajas o un fotograma del video, y a cuántos segundos de la imagen">{ui.chip}</span>
      </div>
      <div className="vslider">
        <input id="vseek" type="range" min={0} max={1000} step={1} value={ui.seek} disabled={off} aria-label="Posición dentro de la ventana de 10 s"
          title="Mueve la posición dentro de los 10 segundos de video; la marca verde es el instante de la imagen"
          onChange={(e) => { void player.seekFraction(+e.target.value / 1000); }} onMouseUp={(e) => (e.target as HTMLElement).blur()} />
        <div id="vmark" title="Instante de la imagen dentro de la ventana" style={{ left: `calc(${ui.markPct.toFixed(2)}% - 1px)` }} />
      </div>
      <div className="vlabels" title="Inicio de la ventana, instante que se ve y fin de la ventana">
        <span id="vstart-t">{ui.startLabel}</span>
        <span className="vcenter"><b id="vnow">{ui.now}</b><span id="vrel" title="FOTO: es el fotograma de la imagen; si no, a cuántos segundos de ella está">{ui.rel}</span></span>
        <span id="vend-t">{ui.endLabel}</span>
      </div>
      <div className="vplayrow">
        <div className="vside">
          <label className="mini" title="Velocidad de reproducción: de 0,1× a 4× (teclas [ y ])"><span>Velocidad</span>
            <select id="vrate" value={String(ui.rate)} onChange={(e) => { player.setRate(parseFloat(e.target.value) || 1); e.target.blur(); }} title="Velocidad de reproducción: de 0,1× a 4×">
              {SPEEDS.map((s) => <option key={s} value={s}>{String(s).replace(".", ",")}×</option>)}
            </select></label>
          <label className="mini" title="Al llegar al final de la ventana de 10 s, volver al principio y seguir reproduciendo"><span>Repetir ventana</span>
            <span className="switch"><input type="checkbox" id="vloop" title="Al llegar al final de la ventana de 10 s, volver al principio y seguir reproduciendo" checked={ui.loop} onChange={(e) => { player.setLoop(e.target.checked); e.target.blur(); }} /><i /></span></label>
        </div>
        <div className="vtransport">
          <button id="vm1" className="tbtn sq" disabled={off} title="Retroceder 1 fotograma (,). Con la tecla < retrocede 10" onClick={() => void player.step(-1)} onMouseDown={(e) => e.preventDefault()}><Icon name="stepBack" size={13} /></button>
          <button id="vplay" className="playbtn" disabled={off} title="Reproducir o pausar el video (P). Al reproducir desde la imagen arranca 5 s antes" onClick={() => void player.toggle()} onMouseDown={(e) => e.preventDefault()}><Icon name={ui.playing ? "pause" : "play"} size={19} /></button>
          <button id="vp1" className="tbtn sq" disabled={off} title="Avanzar 1 fotograma (.). Con la tecla > avanza 10" onClick={() => void player.step(1)} onMouseDown={(e) => e.preventDefault()}><Icon name="stepFwd" size={13} /></button>
        </div>
        <div className="vside right">
          <label className="mini" title="Cuándo se muestran las cajas durante el video (tecla O). Por defecto, solo en el fotograma de la imagen"><span>Mostrar cuadros</span>
            <select id="vboxes" value={ui.boxes} onChange={(e) => { player.setBoxes(e.target.value as typeof BOX_MODES[number]); e.target.blur(); }} title="Cuándo se muestran las cajas durante el video: solo en la foto, tenues o siempre">
              {BOX_MODES.map((m) => <option key={m} value={m}>{BOX_MODE_LABEL[m][0].toUpperCase() + BOX_MODE_LABEL[m].slice(1)}</option>)}
            </select></label>
        </div>
      </div>
      <div className="vactions">
        <button id="vsnap" className={"btn blue" + (ui.view === "foto" ? " on" : "")} disabled={off} title="Volver a la imagen con sus cajas y pausar el video (tecla R)" onClick={() => player.back()} onMouseDown={(e) => e.preventDefault()}><Icon name="back" size={14} /> <span>Volver a la imagen</span> <kbd>R</kbd></button>
        <button id="vsave" className="btn" disabled={off || ui.saving} title="Guardar el fotograma que se ve como una imagen nueva del proyecto para etiquetarla (tecla I)" onClick={() => void player.save()} onMouseDown={(e) => e.preventDefault()}><Icon name="camera" size={14} /> <span>Guardar instantánea</span> <kbd>I</kbd></button>
      </div>
    </section>
  );
}
