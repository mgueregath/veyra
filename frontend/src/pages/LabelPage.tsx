import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type FrameDetail, type FrameFilter, type FrameItem, type Project, type Status } from "../api/client";
import Carousel from "../components/Carousel";
import HelpModal from "../components/HelpModal";
import Popover from "../components/Popover";
import ReviewPanel from "../components/ReviewPanel";
import UserMenu from "../components/UserMenu";
import VideoBar from "../components/VideoBar";
import { BoxEditor } from "../editor/BoxEditor";
import { countLabels, docFromFrame, docToAnnotation, positiveClasses, type EditDoc } from "../editor/model";
import { fmtT, VideoPlayer } from "../editor/player";
import { Saver } from "../editor/saver";
import { useTick } from "../editor/useTick";

const nb = { onMouseDown: (e: React.MouseEvent) => e.preventDefault() };
const LS = {
  get: (k: string) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* sin almacenamiento */ } },
};
const ZoomIcon = ({ plus }: { plus?: boolean }) => (
  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <circle cx="10" cy="10" r="6.5" /><path d={plus ? "M15 15l5.5 5.5M7 10h6M10 7v6" : "M15 15l5.5 5.5M7 10h6"} />
  </svg>
);

const STATUS_TEXT: Record<Status, string> = {
  pending: "Sin revisar", single: "Revisada por 1 persona", agreed: "Varias personas, de acuerdo", disagreement: "Desacuerdo: necesita revisor", final: "Final",
};

export default function LabelPage({ project, user, onExit, onLogout, onAdmin, onModels }: { project: Project; user: string; onExit: () => void; onLogout: () => void; onAdmin: () => void; onModels: () => void }) {
  const qc = useQueryClient();
  const classes = project.config.classes;
  const sources = useMemo(() => project.config.box_sources ?? {}, [project]);
  const canWrite = project.role !== "viewer";
  const [filter, setFilter] = useState<FrameFilter>("all");
  const list = useQuery({ queryKey: ["frames", project.id, filter], queryFn: () => api.allFrames(project.id, filter) });
  const stats = useQuery({ queryKey: ["stats", project.id], queryFn: () => api.stats(project.id) });
  const frames: FrameItem[] = list.data ?? [];

  const [curId, setCurId] = useState<number | null>(null);
  const [detail, setDetail] = useState<FrameDetail | null>(null);
  const [help, setHelp] = useState(false);
  const [gotoVal, setGotoVal] = useState("");
  const tick = useTick();

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const editorRef = useRef<BoxEditor | null>(null);
  const playerRef = useRef<VideoPlayer | null>(null);
  const docs = useRef(new Map<number, EditDoc>()); // lo editado en esta sesión (coherente con los guardados en vuelo)
  const curRef = useRef<number | null>(null);
  const framesRef = useRef<FrameItem[]>([]);
  framesRef.current = frames;
  curRef.current = curId;
  const statsTimer = useRef(0);

  const cur = curId === null ? -1 : frames.findIndex((f) => f.id === curId);
  const item = cur >= 0 ? frames[cur] : null;

  // ---- guardado ----
  const refreshMeta = useCallback(async (id: number) => {
    // estado (revisada / acuerdo / desacuerdo…) y quién tiene la imagen: se actualiza SIN tocar el editor
    try {
      const d = await api.frame(id);
      qc.setQueryData(["frame", id], d);
      if (curRef.current === id) setDetail(d);
    } catch { /* se verá en el siguiente guardado */ }
  }, [qc]);
  const saver = useMemo(
    () => new Saver((id, ok, err) => {
      if (!ok) playerRef.current?.notice("No se pudo guardar: " + err, true);
      clearTimeout(statsTimer.current);
      statsTimer.current = window.setTimeout(() => {
        void qc.invalidateQueries({ queryKey: ["stats", project.id] });
        void qc.invalidateQueries({ queryKey: ["frames", project.id] });
        void refreshMeta(id);
      }, 500);
    }),
    [qc, project.id, refreshMeta],
  );
  const patchItem = useCallback((id: number, doc: EditDoc) => {
    const c = countLabels(doc);
    qc.setQueriesData<FrameItem[]>({ queryKey: ["frames", project.id] }, (old) => old?.map((f) => (f.id === id ? { ...f, reviewed: doc.reviewed, n_labeled: c.byLabel } : f)));
  }, [qc, project.id]);

  // ---- editor y reproductor (se crean una vez) ----
  useEffect(() => {
    const player = new VideoPlayer({
      imageSize: () => [editorRef.current?.imgW ?? 0, editorRef.current?.imgH ?? 0],
      redraw: () => editorRef.current?.draw(),
      onUi: tick,
      onSaved: (r, t) => {
        void qc.invalidateQueries({ queryKey: ["frames", project.id] });
        void qc.invalidateQueries({ queryKey: ["stats", project.id] });
        if (r.existing) player.notice(`Ese fotograma ya es la imagen #${r.position + 1} del conjunto.`);
        else player.notice(`Instantánea guardada como imagen #${r.position + 1} (t = ${fmtT(t)}): aparece justo después de esta en el carrusel.`, false, true);
      },
    });
    playerRef.current = player;
    const saved = LS.get("activeClass");
    const pos = positiveClasses(classes);
    const editor = new BoxEditor(
      {
        canvas: canvasRef.current!,
        classes: () => classes,
        sources: () => sources,
        policy: () => ({ alpha: player.alpha(), active: player.boxesActive() }),
        background: () => player.drawable(),
        onChange: () => {
          const id = curRef.current, ed = editorRef.current;
          if (id === null || !ed?.doc || !canWrite) return;
          docs.current.set(id, ed.doc);
          saver.queue(id, docToAnnotation(ed.doc));
          patchItem(id, ed.doc);
        },
        onUi: tick,
        onNotice: (m, ok) => player.notice(m, false, ok),
      },
      pos.find((c) => c.id === saved)?.id ?? pos[0]?.id ?? classes[0].id,
    );
    editorRef.current = editor;
    player.setOpen(true); // los controles de video están siempre a la vista; el video no se reproduce ni se pide hasta que se usa
    tick();
    return () => { editor.destroy(); player.destroy(); editorRef.current = null; playerRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id]);

  // primera imagen: la primera sin revisar
  useEffect(() => {
    if (curId === null && frames.length) setCurId((frames.find((f) => !f.reviewed) ?? frames[0]).id);
  }, [frames, curId]);

  // ---- cargar la imagen actual ----
  const loadTok = useRef(0);
  useEffect(() => {
    const ed = editorRef.current, pl = playerRef.current;
    if (curId === null || !ed || !pl) return;
    const tok = ++loadTok.current;
    (async () => {
      const d = await qc.fetchQuery({ queryKey: ["frame", curId], queryFn: () => api.frame(curId), staleTime: Infinity });
      if (tok !== loadTok.current) return;
      let doc = docs.current.get(curId);
      if (!doc) { doc = docFromFrame(d); docs.current.set(curId, doc); }
      setDetail(d);
      ed.setFrame(doc, api.imageUrl(curId));
      void pl.load({ id: d.id, videoId: d.video_id, hasVideo: d.has_video, video: d.video, t: d.t, w: d.width, h: d.height });
    })().catch((e) => playerRef.current?.notice("No se pudo cargar la imagen: " + (e as Error).message, true));
  }, [curId, qc]);

  // ---- bloqueo: la cola no entrega esta imagen a otra persona mientras esté abierta ----
  const [lockHolder, setLockHolder] = useState<string | null>(null);
  useEffect(() => {
    if (curId === null || !canWrite) return;
    let alive = true;
    const beat = () => api.lock(curId).then((r) => alive && setLockHolder(r.ok ? null : r.holder)).catch(() => {});
    void beat();
    const t = window.setInterval(beat, 60000);
    return () => { alive = false; clearInterval(t); setLockHolder(null); };
  }, [curId, canWrite]);

  // ---- navegación ----
  const goIdx = useCallback((i: number) => {
    const fs = framesRef.current;
    if (!fs.length) return;
    setCurId(fs[Math.max(0, Math.min(fs.length - 1, i))].id);
  }, []);
  const curIdx = () => framesRef.current.findIndex((f) => f.id === curRef.current);
  const skipPending = useCallback(() => {
    const fs = framesRef.current, c = curIdx();
    for (let k = 1; k <= fs.length; k++) { const f = fs[(c + k) % fs.length]; if (!f.reviewed) { setCurId(f.id); return; } }
  }, []);
  const markRev = useCallback(() => { if (!canWrite) return; editorRef.current?.markReviewed(); goIdx(curIdx() + 1); }, [goIdx, canWrite]);
  const gotoNum = () => { const n = parseInt(gotoVal); if (!isNaN(n)) goIdx(Math.max(1, Math.min(frames.length, n)) - 1); };
  const setClass = (id: string) => { LS.set("activeClass", id); editorRef.current?.setActiveClass(id); };

  // ---- teclado ----
  const helpRef = useRef(false);
  helpRef.current = help;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const ed = editorRef.current, pl = playerRef.current;
      if (!ed || !pl) return;
      const k = e.key.toLowerCase();
      if (helpRef.current) { if (k === "escape" || k === "?") setHelp(false); return; }
      if (k === "?") { setHelp(true); return; }
      const lab = classes.find((c) => c.key && c.key === k);
      const dig = classes.find((c) => c.digit && c.digit === k && !c.negative);
      if (k === "b") ed.setDrawMode(!ed.drawMode);
      else if (k === "escape") ed.setDrawMode(false);
      else if (lab) ed.setLabel(lab.id);
      else if (k === "t") ed.toggleSel();
      else if (k === "u") ed.setLabel(null);
      else if (dig) setClass(dig.id);
      else if (k === "tab") { e.preventDefault(); ed.nextUnlabeled(); }
      else if ((k === "x" || k === "delete" || k === "backspace") && ed.sel >= 0) { e.preventDefault(); ed.deleteSel(); }
      else if (k === "z") ed.restore(false);
      else if (k === "+" || k === "=") ed.zoomBy(1.25);
      else if (k === "-" || k === "_") ed.zoomBy(1 / 1.25);
      else if (k === "f") ed.fit();
      else if (k === "d" || k === "arrowright") goIdx(curIdx() + 1);
      else if (k === "a" || k === "arrowleft") goIdx(curIdx() - 1);
      else if (k === "s") skipPending();
      else if (k === "i") void pl.save();
      else if (k === "p") void pl.toggle();
      else if (k === "r") pl.back();
      else if (k === "home") pl.startWindow();
      else if (k === "[") pl.rateStep(-1);
      else if (k === "]") pl.rateStep(1);
      else if (k === "o") pl.cycleBoxes();
      else if (k === "," || k === ".") void pl.step(k === "." ? 1 : -1);
      else if (k === "<" || k === ">") void pl.step(k === ">" ? 10 : -10);
      else if (k === "g") { e.preventDefault(); const g = document.getElementById("goto") as HTMLInputElement; g.focus(); g.select(); }
      else if (k === " ") { e.preventDefault(); markRev(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [classes, goIdx, skipPending, markRev]);

  // Identificador de depuración (solo lectura): lo usan las pruebas en Chrome real.
  useEffect(() => {
    (window as unknown as Record<string, unknown>).__lh = {
      get editor() { return editorRef.current; },
      get player() { return playerRef.current; },
      frames: () => framesRef.current,
      curId: () => curRef.current,
    };
  });

  const ed = editorRef.current, pl = playerRef.current;
  const eui = ed?.ui, pui = pl?.ui;
  const doc = ed?.doc ?? null;
  const cnt = doc ? countLabels(doc) : { byLabel: {}, unlabeled: 0, manual: 0 };
  const st = stats.data;
  const reviewedN = st?.reviewed ?? 0;
  const pos = positiveClasses(classes);

  useEffect(() => { if (document.activeElement?.id !== "goto" && cur >= 0) setGotoVal(String(cur + 1)); }, [cur]);

  return (
    <div className="lp">
      <header id="top">
        <div className="top-left">
          <div className="brand" title="LabelHub: etiquetado, entrenamiento y detección">LabelHub</div>
          <div id="info" title="Video, número de fotograma e instante de la imagen que estás viendo">{item && detail && <><b>{detail.video.slice(4)}</b> · frame {detail.frame_index}{detail.t != null && <> · t={detail.t} s</>}</>}</div>
        </div>
        <div className="top-center" id="photoctl">
          <button id="prev" title="Imagen anterior (A o ←). No marca la actual como revisada" aria-label="Anterior" onClick={() => goIdx(cur - 1)} {...nb}>←</button>
          <label className="goto" title="Imagen actual y total. Escribe un número y pulsa Enter para ir directamente a esa imagen (atajo G); si te pasas va a la última">
            <input id="goto" type="number" min={1} value={gotoVal} aria-label="Número de imagen actual" onChange={(e) => setGotoVal(e.target.value)}
              onMouseDown={(e) => e.stopPropagation()}
              onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") { gotoNum(); (e.target as HTMLElement).blur(); } else if (e.key === "Escape") (e.target as HTMLElement).blur(); }} />
            <span>/</span> <span id="ntotal">{frames.length}</span>
          </label>
          <button id="next" title="Imagen siguiente (D o →). No marca la actual como revisada" aria-label="Siguiente" onClick={() => goIdx(cur + 1)} {...nb}>→</button>
        </div>
        <div className="top-right">
          <div className="topctl" id="reviewctl">
            <button id="rev" className="primary" disabled={!canWrite} title="Marca la imagen como revisada: las cajas que no tocaste pasan a «ni piojo ni mosca» y se pasa a la siguiente (Espacio)" onClick={markRev} {...nb}>✔ Revisado y siguiente</button>
            <button id="skip" className="ghost" title="Salta a la siguiente imagen que tú aún no has revisado (S)" onClick={skipPending} {...nb}>Sin revisar ⇥</button>
          </div>
          <div className="top-util">
            <button id="modelsbtn" className="ghost" onClick={onModels} title="Modelos entrenados: ver sus indicadores y correrlos sobre un video grabado o una cámara en vivo" {...nb}>Modelos</button>
            <button id="helpbtn" className="ghost iconbtn" aria-label="Ayuda" title="Atajos y ayuda (?)" onClick={() => setHelp(true)} {...nb}>?</button>
            <UserMenu user={user} role={project.role} projectName={project.name} onProjects={onExit} onLogout={onLogout} />
          </div>
        </div>
      </header>
      <div id="main">
        <div id="stage">
          <canvas id="cv" ref={canvasRef} />
          {ed && eui && <Popover editor={ed} ui={eui} classes={classes} sources={sources} />}
        </div>
        {pl && pui && <VideoBar player={pl} ui={pui} />}
      </div>
      <aside id="side">
        <div id="vmsg" className={"msg" + (pui?.msg?.ok ? " ok" : "")} hidden={!pui?.msg}>{pui?.msg?.text}</div>
        <div id="sidescroll">
          <section className="blk" id="s-foto">
            <h3 title="Estado de la imagen actual y de tu avance">Foto <span className="sx" id="pcount" title="Imágenes que tú has marcado como revisadas, sobre el total del proyecto">{reviewedN} / {st?.frames ?? frames.length} revisadas</span></h3>
            <div className="rfoto2" id="statusrow">
              <span className={"chip st-" + (detail?.status ?? "pending")} id="statuschip" title="Estado de esta imagen: sin revisar, revisada por una persona, de acuerdo entre varias, en desacuerdo (necesita revisor) o final">{STATUS_TEXT[detail?.status ?? "pending"]}</span>
              {detail?.assigned_to_me && <span className="mut" title="Esta imagen está en tu cola">en tu cola</span>}
            </div>
            {lockHolder && <div className="lockwarn" id="lockwarn" title="Otra persona tiene abierta esta misma imagen; puedes seguir, pero conviene coordinarse">{lockHolder} también tiene esta imagen abierta</div>}
            <div className="prog" title="Avance: imágenes que has revisado"><div className="bar"><div id="bar" style={{ width: `${st?.frames ? (100 * reviewedN) / st.frames : 0}%` }} /></div></div>
            <div id="restoreRow" hidden={!eui?.deletedCount}>
              <span id="rtext">{eui?.deletedCount} cuadro{eui && eui.deletedCount > 1 ? "s" : ""} borrado{eui && eui.deletedCount > 1 ? "s" : ""} en esta imagen</span>
              <div className="r2">
                <button id="rrlast" title="Restaurar el último cuadro borrado (Z)" onClick={() => ed?.restore(false)} {...nb}>Restaurar último <kbd>Z</kbd></button>
                <button id="rrall" title="Restaurar todos los cuadros borrados de esta imagen" onClick={() => ed?.restore(true)} {...nb}>Restaurar todos</button>
              </div>
            </div>
          </section>
          <section className="blk" id="s-vista">
            <h3 title="Zoom y herramientas de dibujo">Vista <span className={"sx chip" + (eui?.drawMode ? " draw" : "")} id="mode" title="Modo actual del ratón: mover la imagen o dibujar una caja nueva">{eui?.drawMode ? "Dibujando · Esc" : "Mover"}</span></h3>
            <div className="zrow">
              <button id="zout" title="Alejar la imagen (tecla −)" aria-label="Alejar" onClick={() => ed?.zoomBy(1 / 1.25)} {...nb}><ZoomIcon /></button>
              <button id="zoom" title="Ajustar a la ventana (F)" onClick={() => ed?.fit()} {...nb}>{eui?.zoomPct ?? 100} %</button>
              <button id="zin" title="Acercar la imagen (tecla +); con una caja seleccionada el zoom se centra en ella" aria-label="Acercar" onClick={() => ed?.zoomBy(1.25)} {...nb}><ZoomIcon plus /></button>
              <button id="zfit" className="ghost" title="Ajustar la imagen a la ventana (F)" onClick={() => ed?.fit()} {...nb}>Ajustar</button>
            </div>
            <div className="cls r3">
              {pos.map((c) => (
                <button key={c.id} id={"cls_" + c.id} className={ed?.activeClass === c.id ? "on" : ""} title={`Clase ${c.name} (${c.digit}): lo que recibe una caja al presionarla, moverla o dibujarla`} onClick={() => setClass(c.id)} {...nb}>
                  <span className="dot" style={{ background: c.color }} />{c.name}<kbd>{c.digit}</kbd>
                </button>
              ))}
              <button id="bbox" className={eui?.drawMode ? "on" : ""} disabled={!canWrite} title="Dibujar una caja nueva sobre la imagen (B); recibe la clase activa" onClick={() => ed?.setDrawMode(!ed.drawMode)} {...nb}>＋ Caja<kbd>B</kbd></button>
            </div>
          </section>
          {detail && (project.role === "admin" || project.role === "reviewer") && (
            <ReviewPanel frame={detail} classes={classes} editor={ed} pid={project.id} onChanged={() => void refreshMeta(detail.id)} />
          )}
          <details className="sec" id="s-leyenda" open={LS.get("sec_s-leyenda") === "1"} onToggle={(e) => LS.set("sec_s-leyenda", (e.target as HTMLDetailsElement).open ? "1" : "0")}>
            <summary title="Qué significa cada color de caja y cuántas hay en esta imagen y en todo el proyecto">Leyenda y resumen</summary>
            <div className="body">
              <div className="leg">
                <div><span className="sw unl" style={{ borderColor: "var(--unl)" }} /><span>Sin etiquetar<small>propuesta del detector</small></span><span className="n" id="cU">{cnt.unlabeled}</span></div>
                {classes.map((c) => (
                  <div key={c.id}><span className="sw" style={{ borderColor: c.color }} /><span>{c.name}{c.negative && <small>cajas sin tocar al revisar</small>}</span><span className="n" id={"c_" + c.id}>{cnt.byLabel[c.id] ?? 0}</span></div>
                ))}
              </div>
              <p className="hint">Los números son de esta imagen. En cada caja: {Object.entries(sources).map(([k, v], i) => <span key={k}>{i ? ", " : ""}<b>{v.short}</b> = {v.name.toLowerCase()}</span>)}.</p>
              <h2 style={{ margin: "6px 0 2px", fontSize: 11, letterSpacing: ".08em", textTransform: "uppercase", color: "var(--mut)" }}>Todo el set</h2>
              <div className="stats" id="stats">
                {classes.map((c) => <Fragment key={c.id}><span>{c.name}</span><span>{st?.by_label[c.id] ?? 0}</span></Fragment>)}
                <span>Dibujadas a mano</span><span>{st?.manual ?? 0}</span>
                <span>Sin etiquetar</span><span>{st?.unlabeled ?? 0}</span>
                <span>Cuadros borrados</span><span>{st?.deleted ?? 0}</span>
              </div>
            </div>
          </details>
          <section className="blk" id="s-proyecto">
            <h3 title="Proyecto abierto y tu rol en él">Proyecto <span className="sx">{project.role}</span></h3>
            <div className="pjname" title="Proyecto abierto"><b>{project.name}</b></div>
            {(project.role === "admin" || project.role === "reviewer") && (
              <button id="adminbtn" className="full" onClick={onAdmin} title="Gestión del proyecto: calidad, reparto, datasets, entrenamiento, evaluación, equipo y sistema" {...nb}>⚙ Gestión del proyecto</button>
            )}
          </section>
        </div>
      </aside>
      <Carousel frames={frames} curId={curId} classes={classes} filter={filter} setFilter={setFilter} go={setCurId} />
      {help && <HelpModal onClose={() => setHelp(false)} />}
    </div>
  );
}
