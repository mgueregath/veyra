import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type FrameDetail, type FrameFilter, type FrameItem, type Project } from "../api/client";
import Carousel from "../components/Carousel";
import HelpModal from "../components/HelpModal";
import Popover from "../components/Popover";
import ReviewTab from "../components/ReviewTab";
import Toolbar from "../components/Toolbar";
import TopNav from "../components/TopNav";
import Icon from "../components/Icon";
import VideoTab from "../components/VideoTab";
import { BoxEditor } from "../editor/BoxEditor";
import { countLabels, docFromFrame, docToAnnotation, positiveClasses, type EditDoc } from "../editor/model";
import { fmtT, VideoPlayer } from "../editor/player";
import { Saver } from "../editor/saver";
import { useTick } from "../editor/useTick";

const LS = {
  get: (k: string) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* sin almacenamiento */ } },
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
  const [tab, setTab] = useState<"video" | "review">(() => (LS.get("sidetab") === "review" ? "review" : "video"));
  const [viewing, setViewing] = useState<{ name: string; text: string } | null>(null);
  const [legendOpen, setLegendOpen] = useState(() => LS.get("legend_open") === "1");
  const [gotoVal, setGotoVal] = useState("");
  const [confirmDel, setConfirmDel] = useState(false);
  const [undo, setUndo] = useState<{ id: number; n: number } | null>(null);
  const trashedRef = useRef(false);
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
        policy: () => ({ alpha: player.alpha(), active: player.boxesActive() && !trashedRef.current }),
        background: () => player.drawable(),
        onChange: () => {
          const id = curRef.current, ed = editorRef.current;
          if (id === null || !ed?.doc || !canWrite || trashedRef.current) return;
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

  // al entrar en la papelera se muestra la primera eliminada; al salir de ella no se queda abierta una eliminada
  useEffect(() => {
    if (list.isLoading || !frames.length) return;
    if (filter === "trash" && !frames.some((f) => f.id === curId)) setCurId(frames[0].id);
    else if (filter !== "trash" && detail?.deleted && !frames.some((f) => f.id === curId)) setCurId(frames[0].id);
  }, [filter, frames, curId, detail, list.isLoading]);

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
  const markRev = useCallback(() => { if (!canWrite || trashedRef.current) return; editorRef.current?.markReviewed(); goIdx(curIdx() + 1); }, [goIdx, canWrite]);
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
      else if (k === "h") ed.setTool("pan");
      else if (k === "v") { ed.setTool("select"); ed.setDrawMode(false); }
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
  const collab = project.config.mode === "collab";
  const canTrash = project.role === "admin" || project.role === "reviewer";
  const trashed = !!detail?.deleted;
  trashedRef.current = trashed;
  const refreshLists = () => { for (const k of ["frames", "stats", "projects"]) void qc.invalidateQueries({ queryKey: [k] }); };
  const neighbour = () => { const fs = framesRef.current, i = curIdx(); return (fs[i + 1] ?? fs[i - 1])?.id ?? null; };
  const trashNow = async () => {
    if (curId === null) return;
    const id = curId, n = cur + 1, next = neighbour();
    setConfirmDel(false);
    try {
      while (saver.busy) await new Promise((r) => setTimeout(r, 100)); // que no llegue un guardado a una captura ya eliminada
      await api.deleteFrame(id);
      qc.removeQueries({ queryKey: ["frame", id] });
      docs.current.delete(id);
      setCurId(next);
      refreshLists();
      setUndo({ id, n });
      window.setTimeout(() => setUndo((u) => (u?.id === id ? null : u)), 12000);
    } catch (e) { playerRef.current?.notice("No se pudo eliminar: " + (e as Error).message, true); }
  };
  const restoreNow = async (id: number, keepHere = false) => {
    try {
      const next = neighbour();
      await api.restoreFrame(id);
      qc.removeQueries({ queryKey: ["frame", id] });
      setDetail((d) => (d && d.id === id ? { ...d, deleted: null } : d));
      setUndo(null);
      refreshLists();
      if (keepHere) setCurId(id); else setCurId(next);
      playerRef.current?.notice("Captura restaurada.", false, true);
    } catch (e) { playerRef.current?.notice("No se pudo restaurar: " + (e as Error).message, true); }
  };
  const canReview = (project.role === "admin" || project.role === "reviewer") && !collab; // en colaborativa no hay revisión: manda la última edición
  const activeTab = canReview ? tab : "video";
  const setTabP = (t: "video" | "review") => { setTab(t); LS.set("sidetab", t); };
  const plural = (n: string) => (n.toLowerCase().endsWith("s") ? n : n + "s");

  useEffect(() => { if (document.activeElement?.id !== "goto" && cur >= 0) setGotoVal(String(cur + 1)); }, [cur]);

  return (
    <div className="lp2">
      <TopNav project={project} user={user} active="label" onProjects={onExit} onLabel={() => undefined} onModels={onModels} onAdmin={onAdmin} onHelp={() => setHelp(true)} onLogout={onLogout} />
      <Toolbar index={cur} detail={detail} editor={ed} ui={eui} classes={classes} activeClass={ed?.activeClass} canWrite={canWrite} lockHolder={lockHolder} collab={collab} canTrash={canTrash} onTrash={() => setConfirmDel(true)} onClass={setClass} />
      <div id="main">
        <div id="stage">
          <canvas id="cv" ref={canvasRef} />
          {ed && eui && <Popover editor={ed} ui={eui} classes={classes} sources={sources} />}
        </div>
        <div id="info" className="ochip bl" title="Video, número de fotograma e instante de la imagen que estás viendo">
          {item && detail ? <>Frame {detail.frame_index}{detail.t != null && <> · {fmtT(detail.t)}</>}<span className="ovid"> · {detail.video.slice(4)}</span></> : "—"}
        </div>
        {viewing && <div id="viewchip" className="ochip tl" title="Cajas de otra persona superpuestas sobre la imagen para compararlas"><i className="dot" style={{ background: "#16a34a" }} /> {viewing.name} · {viewing.text}</div>}
        {trashed && detail?.deleted && (
          <div id="trashbar" className="trashbar" role="status">
            <Icon name="trash" size={16} />
            <span>Esta captura está en la papelera{detail.deleted.by ? ` (la eliminó ${detail.deleted.by})` : ""}: no cuenta en colas, estadísticas ni datasets nuevos.</span>
            {canTrash && <button id="restoreframe" className="btn sm" onClick={() => void restoreNow(detail.id)} onMouseDown={(e) => e.preventDefault()}>Restaurar</button>}
          </div>
        )}
        {filter === "trash" && !frames.length && !list.isLoading && <div id="trash-empty" className="trashbar empty">La papelera está vacía.</div>}
        {undo && <div id="undotoast" className="undotoast" role="status">Captura {undo.n} eliminada<button id="undo-del" className="btn sm" onClick={() => void restoreNow(undo.id, true)} onMouseDown={(e) => e.preventDefault()}>Deshacer</button></div>}
        <div id="vmsg" className={"msg" + (pui?.msg?.ok ? " ok" : "")} hidden={!pui?.msg}>{pui?.msg?.text}</div>
      </div>
      <aside id="side">
        <div className="sidetabs" role="tablist">
          <button id="tab-video" role="tab" aria-selected={activeTab === "video"} className={activeTab === "video" ? "on" : ""} title="Controles del video original alrededor de esta imagen" onClick={() => setTabP("video")}>Video</button>
          {canReview && <button id="tab-review" role="tab" aria-selected={activeTab === "review"} className={activeTab === "review" ? "on" : ""} title="Comparar lo que hizo cada persona con esta imagen y elegir la versión final" onClick={() => setTabP("review")}>Revisión</button>}
        </div>
        <div id="sidescroll">
          {activeTab === "video" && pl && pui && <VideoTab player={pl} ui={pui} />}
          {activeTab === "review" && detail && <ReviewTab frame={detail} classes={classes} editor={ed} pid={project.id} onChanged={() => void refreshMeta(detail.id)} onViewing={setViewing} />}
          <section id="annot" className="sec2">
            <h3 className="h3b" title="Cuántas cajas hay de cada clase en esta imagen">Anotaciones</h3>
            <ul className="alist" id="alist">
              <li title="Cajas propuestas por el detector que todavía no has clasificado"><i className="dot" style={{ background: "#d1d5db" }} /><span>Sin etiquetar</span><b id="cU">{cnt.unlabeled}</b></li>
              {classes.filter((c) => !c.negative || (cnt.byLabel[c.id] ?? 0) > 0).map((c) => (
                <li key={c.id} title={c.negative ? "Cajas que marcaste como «ni piojo ni mosca» (o que se marcaron al revisar)" : `Cajas de clase ${c.name} en esta imagen`}><i className="dot" style={{ background: c.color }} /><span>{c.negative ? c.name : plural(c.name)}</span><b id={"c_" + c.id}>{cnt.byLabel[c.id] ?? 0}</b></li>
              ))}
            </ul>
            <div id="restoreRow" className="restore2" hidden={!eui?.deletedCount}>
              <span id="rtext">{eui?.deletedCount} cuadro{eui && eui.deletedCount > 1 ? "s" : ""} borrado{eui && eui.deletedCount > 1 ? "s" : ""} en esta imagen</span>
              <div className="rbtns">
                <button id="rrlast" className="btn sm" title="Restaurar el último cuadro borrado (Z)" onClick={() => ed?.restore(false)} onMouseDown={(e) => e.preventDefault()}>Restaurar último</button>
                <button id="rrall" className="btn sm" title="Restaurar todos los cuadros borrados de esta imagen" onClick={() => ed?.restore(true)} onMouseDown={(e) => e.preventDefault()}>Restaurar todos</button>
              </div>
            </div>
            <button id="legendbtn" className="linkbtn" aria-expanded={legendOpen} title="Mostrar la leyenda de las cajas, el resumen de todo el proyecto y los atajos" onClick={() => { setLegendOpen(!legendOpen); LS.set("legend_open", legendOpen ? "0" : "1"); }} onMouseDown={(e) => e.preventDefault()}>
              <Icon name={legendOpen ? "down" : "right"} size={12} /> Leyenda y atajos
            </button>
            {legendOpen && (
              <div id="s-leyenda" className="legend2">
                <p className="hint">En cada caja: {Object.entries(sources).map(([k, v], i) => <span key={k}>{i ? ", " : ""}<b>{v.short}</b> = {v.name.toLowerCase()}</span>)}. Línea punteada = sin etiquetar.</p>
                <h4 className="cap">Todo el proyecto</h4>
                <div className="stats" id="stats">
                  <span>{collab ? "Revisadas" : "Revisadas por ti"}</span><span id="pcount">{reviewedN} / {st?.frames ?? frames.length}</span>
                  {classes.map((c) => <Fragment key={c.id}><span>{c.name}</span><span>{st?.by_label[c.id] ?? 0}</span></Fragment>)}
                  <span>Dibujadas a mano</span><span>{st?.manual ?? 0}</span>
                  <span>Sin etiquetar</span><span>{st?.unlabeled ?? 0}</span>
                  <span>Cuadros borrados</span><span>{st?.deleted ?? 0}</span>
                </div>
                <button className="linkbtn" title="Ver todos los atajos de teclado" onClick={() => setHelp(true)} onMouseDown={(e) => e.preventDefault()}>Ver todos los atajos de teclado</button>
              </div>
            )}
          </section>
        </div>
        <div className="sidefoot">
          <button id="skip" className="btn" title="Salta a la siguiente imagen que tú aún no has revisado (S)" onClick={skipPending} onMouseDown={(e) => e.preventDefault()}><Icon name="skipFwd" size={13} /> Saltar imagen</button>
          <button id="rev" className="btn green" disabled={!canWrite} title="Marca la imagen como revisada: las cajas que no tocaste pasan a «ni piojo ni mosca» y se pasa a la siguiente (Espacio)" onClick={markRev} onMouseDown={(e) => e.preventDefault()}>
            <Icon name="check" size={14} /><span className="two"><b>Revisado y siguiente</b><kbd>Espacio</kbd></span>
          </button>
        </div>
      </aside>
      <Carousel frames={frames} curId={curId} filter={filter} setFilter={setFilter} go={setCurId} onPrev={() => goIdx(cur - 1)} onNext={() => goIdx(cur + 1)}
        gotoVal={gotoVal} setGotoVal={setGotoVal} onGoto={gotoNum} reviewed={reviewedN} total={st?.frames ?? frames.length} collab={collab} canTrash={canTrash} />
      {confirmDel && detail && (
        <div id="delmodal" className="modalback" onClick={(e) => { if (e.target === e.currentTarget) setConfirmDel(false); }}>
          <div className="modalbox wide" role="dialog" aria-label="Eliminar captura">
            <div className="hhead"><b>Eliminar la captura {cur + 1}</b><button type="button" id="del-x" title="Cerrar sin eliminar (Esc)" onClick={() => setConfirmDel(false)}>×</button></div>
            <p>Úsalo para capturas que no aportan nada (movidas, repetidas, sin contenido). Irá a la <b>papelera</b>: dejará de aparecer en las colas, las estadísticas y los datasets que crees desde ahora, y podrás restaurarla cuando quieras.</p>
            <ul className="delnote">
              {(detail.annotation?.boxes.filter((b) => !b.deleted && b.label).length ?? 0) > 0 && <li>Tiene {detail.annotation?.boxes.filter((b) => !b.deleted && b.label).length} caja(s) etiquetada(s); no se borran, quedan guardadas.</li>}
              {!!detail.in_datasets?.length && <li>Ya está en {detail.in_datasets.length === 1 ? "la versión" : "las versiones"} de dataset <b>{detail.in_datasets.join(", ")}</b>: {detail.in_datasets.length === 1 ? "esa versión no cambia" : "esas versiones no cambian"} porque son inmutables.</li>}
              <li>El archivo de la imagen no se borra del disco.</li>
            </ul>
            <div className="formrow end"><button type="button" className="btn-outline" id="del-cancel" onClick={() => setConfirmDel(false)}>Cancelar</button><button type="button" className="btn-danger solid" id="delframe-ok" onClick={() => void trashNow()}>Eliminar captura</button></div>
          </div>
        </div>
      )}
      {help && <HelpModal onClose={() => setHelp(false)} />}
    </div>
  );
}
