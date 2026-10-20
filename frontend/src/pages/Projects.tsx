import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import Icon from "../components/Icon";
import UserMenu from "../components/UserMenu";
import { api, type Project } from "../api/client";

const ROLE_CHIP: Record<string, string> = { admin: "Admin", reviewer: "Revisor", labeler: "Etiquetador", viewer: "Lector" };

/** Lista de proyectos: resumen, búsqueda, tarjetas o lista y alta de proyectos nuevos. */
export default function Projects({ user, onOpen, onLogout }: { user: string; onOpen: (p: Project, v?: "label" | "admin") => void; onLogout: () => void }) {
  const q = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const [search, setSearch] = useState("");
  const [tab, setTab] = useState<"all" | "mine">("all");
  const [view, setView] = useState<"grid" | "list">(() => { try { return localStorage.getItem("lh.pview") === "list" ? "list" : "grid"; } catch { return "grid"; } });
  const [creating, setCreating] = useState(false);
  const setV = (v: "grid" | "list") => { setView(v); try { localStorage.setItem("lh.pview", v); } catch { /* sin almacenamiento */ } };
  const all = q.data ?? [];
  const mine = all.filter((p) => p.role === "admin");
  const shown = (tab === "mine" ? mine : all).filter((p) => p.name.toLowerCase().includes(search.trim().toLowerCase()));
  const images = all.reduce((a, p) => a + p.n_frames, 0), reviewed = all.reduce((a, p) => a + p.n_reviewed, 0);
  return (
    <div className="page home">
      <header id="top" className="topnav">
        <div className="tn-left"><span className="logo" title="LabelHub: etiquetado, entrenamiento y detección">Label<span>Hub</span></span></div>
        <div className="tn-right">
          <button className="navtab on" id="nav-projects" title="Lista de proyectos">Proyectos</button>
          <span className="tn-sep" />
          <UserMenu user={user} onLogout={onLogout} />
        </div>
      </header>
      <main className="home-main">
        <div className="home-head">
          <div><small>Espacio de trabajo</small><h1>Tus proyectos</h1><p>Organiza tus imágenes y continúa donde lo dejaste.</p></div>
          <button className="primary big" id="newproject" title="Crear un proyecto nuevo con sus propias imágenes, etiquetas y equipo" onClick={() => setCreating(true)}><Icon name="plus" size={18} /> Nuevo proyecto</button>
        </div>
        <div className="home-stats">
          <div><span className="static blue"><Icon name="folder" size={26} /></span><b>{all.length}</b><small>{all.length === 1 ? "Proyecto" : "Proyectos"}</small></div>
          <div><span className="static blue"><Icon name="image" size={26} /></span><b>{images}</b><small>Imágenes</small></div>
          <div><span className="static blue"><Icon name="checkCircle" size={26} /></span><b>{reviewed}</b><small>Revisadas por ti</small></div>
        </div>
        <div className="home-bar">
          <label className="searchbox"><Icon name="search" size={18} /><input id="psearch" type="search" placeholder="Buscar proyecto…" value={search} onChange={(e) => setSearch(e.target.value)} /></label>
          <div className="htabs">
            <button id="ptab-all" className={tab === "all" ? "on" : ""} onClick={() => setTab("all")}>Todos ({all.length})</button>
            <button id="ptab-mine" className={tab === "mine" ? "on" : ""} onClick={() => setTab("mine")} title="Proyectos que administras">Mis proyectos ({mine.length})</button>
          </div>
          <div className="seg">
            <button id="pview-grid" aria-label="Ver como tarjetas" title="Tarjetas" className={view === "grid" ? "on" : ""} onClick={() => setV("grid")}><Icon name="grid" size={17} /></button>
            <button id="pview-list" aria-label="Ver como lista" title="Lista" className={view === "list" ? "on" : ""} onClick={() => setV("list")}><Icon name="list" size={17} /></button>
          </div>
        </div>
        {q.isLoading && <div className="center">Cargando…</div>}
        {!q.isLoading && !all.length && <div className="emptyhome" id="noprojects"><Icon name="folder" size={36} /><b>No perteneces a ningún proyecto todavía.</b><span>Crea el primero para empezar a etiquetar.</span></div>}
        {!!all.length && !shown.length && <div className="emptyhome"><b>Ningún proyecto coincide.</b></div>}
        <div className={"pgrid " + view} id="plist">
          {shown.map((p) => <Card key={p.id} p={p} onOpen={onOpen} />)}
        </div>
        <div className="autosave"><Icon name="keyboard" size={26} /><div><b>Tu trabajo se guarda automáticamente</b><span>Puedes retomar la revisión desde cualquier imagen.</span></div></div>
      </main>
      {creating && <NewProject onClose={() => setCreating(false)} onCreated={(p) => { setCreating(false); onOpen(p, "admin"); }} />}
    </div>
  );
}

function Card({ p, onOpen }: { p: Project; onOpen: (p: Project, v?: "label" | "admin") => void }) {
  const pct = p.n_frames ? (100 * p.n_reviewed) / p.n_frames : 0;
  const manage = p.role === "admin" || p.role === "reviewer";
  return (
    <article className="pcard2" data-project={p.id}>
      <div className="pcover">{p.cover_frame_id ? <img src={api.thumbUrl(p.cover_frame_id)} alt="" loading="lazy" /> : <Icon name="image" size={36} />}</div>
      <div className="pbody">
        <div className="ptop"><h2>{p.name}</h2><span className="rolechip">{ROLE_CHIP[p.role] ?? p.role}</span></div>
        <p className="pdesc">{p.config.description || "Sin descripción."}</p>
        <div className="pprog"><span>Tu avance</span><b>{p.n_reviewed} / {p.n_frames} imágenes</b></div>
        <progress value={p.n_reviewed} max={Math.max(1, p.n_frames)} />
        <small className="mut">{pct.toFixed(1).replace(".", ",")} % completado</small>
        <div className="pact">
          <button className="primary" title={`Abrir el proyecto «${p.name}» (tu rol: ${p.role})`} onClick={() => onOpen(p)}>Continuar etiquetando <Icon name="arrowRight" size={17} /></button>
          {manage && <button className="btn-outline" title="Calidad, reparto, datasets, entrenamiento, equipo y configuración" onClick={() => onOpen(p, "admin")}>Gestionar proyecto</button>}
        </div>
      </div>
    </article>
  );
}

function NewProject({ onClose, onCreated }: { onClose: () => void; onCreated: (p: Project) => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  const create = useMutation({
    mutationFn: () => api.createProject({ name: name.trim(), description: desc.trim() || undefined }),
    onSuccess: (p) => { void qc.invalidateQueries({ queryKey: ["projects"] }); onCreated(p); },
  });
  return (
    <div id="npmodal" className="modalback" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <form className="modalbox wide" role="dialog" aria-label="Nuevo proyecto" onSubmit={(e) => { e.preventDefault(); if (name.trim()) create.mutate(); }}>
        <div className="hhead"><b>Nuevo proyecto</b><button type="button" id="np-x" title="Cerrar sin crear nada (Esc)" onClick={onClose}>×</button></div>
        <label>Nombre del proyecto<input id="np-name" type="text" maxLength={128} autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="p. ej. Piojos en lana" /></label>
        <label>Descripción (opcional)<textarea id="np-desc" rows={3} maxLength={1000} value={desc} onChange={(e) => setDesc(e.target.value)} /></label>
        <p className="mut">Empieza con las etiquetas Piojo, Mosca y «Ni piojo ni mosca»; podrás cambiarlas en Configuración. Tú quedas como administrador.</p>
        {create.error && <p className="err" id="np-err">{(create.error as Error).message}</p>}
        <div className="formrow end"><button type="button" className="btn-outline" onClick={onClose}>Cancelar</button><button type="submit" className="primary" id="np-go" disabled={!name.trim() || create.isPending}>Crear proyecto</button></div>
      </form>
    </div>
  );
}
