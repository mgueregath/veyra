import type { Project } from "../api/client";
import Icon from "./Icon";
import UserMenu from "./UserMenu";

const nb = { onMouseDown: (e: React.MouseEvent) => e.preventDefault() };

/** Cabecera común: logo, ruta (Proyectos / proyecto), secciones, ayuda y menú del usuario. */
export default function TopNav({ project, user, active, onProjects, onLabel, onModels, onAdmin, onHelp, onLogout }: {
  project: Project; user: string; active: "label" | "models" | "admin"; onProjects: () => void; onLabel: () => void; onModels: () => void;
  onAdmin: () => void; onHelp?: () => void; onLogout: () => void;
}) {
  return (
    <header id="top" className="topnav">
      <div className="tn-left">
        <span className="logo" title="LabelHub: etiquetado, entrenamiento y detección">Label<span>Hub</span></span>
        <span className="tn-sep" />
        <nav className="crumbs" aria-label="Ruta">
          <button id="crumb-projects" className="crumb" onClick={onProjects} title="Volver a la lista de proyectos" {...nb}>Proyectos</button>
          <span className="crumb-sl">/</span>
          <b className="crumb-cur" title="Proyecto abierto">{project.name}</b>
        </nav>
      </div>
      <div className="tn-right">
        <button id="navlabel" className={"navtab" + (active === "label" ? " on" : "")} onClick={onLabel} title="Etiquetar imágenes: dibujar, clasificar y revisar cajas" {...nb}>Etiquetar</button>
        <button id="modelsbtn" className={"navtab" + (active === "models" ? " on" : "")} onClick={onModels} title="Modelos entrenados: ver sus indicadores y correrlos sobre un video grabado o una cámara en vivo" {...nb}>Modelos</button>
        {(project.role === "admin" || project.role === "reviewer") && (
          <button id="adminbtn" className={"navtab" + (active === "admin" ? " on" : "")} onClick={onAdmin} title="Gestión del proyecto: calidad, reparto, datasets, entrenamiento, evaluación, equipo y sistema" {...nb}>Gestión</button>
        )}
        {onHelp && <button id="helpbtn" className="iconbtn2" aria-label="Ayuda" title="Atajos y ayuda (?)" onClick={onHelp} {...nb}><Icon name="help" size={18} /></button>}
        <span className="tn-sep" />
        <UserMenu user={user} role={project.role} projectName={project.name} onProjects={onProjects} onLogout={onLogout} />
      </div>
    </header>
  );
}
