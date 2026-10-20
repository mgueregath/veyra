import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type Project } from "./api/client";
import AdminPage from "./pages/AdminPage";
import ModelsPage from "./pages/ModelsPage";
import RunPage from "./pages/RunPage";
import LabelPage from "./pages/LabelPage";
import Login from "./pages/Login";
import Projects from "./pages/Projects";

/** El proyecto abierto vive en la URL (#p=ID, y &v=admin en la gestión): recargar no devuelve a la lista. */
const hashProject = () => {
  const m = /(?:^|[#&])p=(\d+)/.exec(window.location.hash);
  return m ? Number(m[1]) : null;
};
type View = "label" | "admin" | "models" | "run";
const hashView = (): View => {
  const m = /(?:^|[#&])v=(admin|models|run)/.exec(window.location.hash);
  return m ? (m[1] as View) : "label";
};
const hashModel = () => {
  const m = /(?:^|[#&])m=(\d+)/.exec(window.location.hash);
  return m ? Number(m[1]) : null;
};

export default function App() {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects, enabled: !!me.data });
  const [pid, setPid] = useState<number | null>(hashProject);
  const [view, setView] = useState(hashView);
  const [mid, setMid] = useState(hashModel);
  useEffect(() => {
    const on = () => { setPid(hashProject()); setView(hashView()); setMid(hashModel()); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const open = (p: Project | null, v: View = "label", model: number | null = null) => {
    window.location.hash = p ? `p=${p.id}${v === "label" ? "" : `&v=${v}`}${v === "run" && model ? `&m=${model}` : ""}` : "";
    setPid(p ? p.id : null); setView(v); setMid(model);
  };

  if (me.isLoading) return <div className="center">Cargando…</div>;
  if (!me.data) return <Login onDone={() => qc.invalidateQueries({ queryKey: ["me"] })} />;
  const logout = async () => {
    await api.logout();
    open(null);
    qc.clear();
    qc.invalidateQueries({ queryKey: ["me"] });
  };
  const who = me.data.display_name ?? me.data.username;
  const project = pid !== null ? projects.data?.find((p) => p.id === pid) ?? null : null;
  if (pid !== null && projects.isLoading) return <div className="center">Cargando…</div>;
  if (project && view === "models") return <ModelsPage key={project.id} project={project} user={who} onBack={() => open(project)} onProjects={() => open(null)} onAdmin={() => open(project, "admin")} onRun={(m) => open(project, "run", m)} onLogout={logout} />;
  if (project && view === "run" && mid !== null) return <RunPage key={`${project.id}-${mid}`} project={project} user={who} modelId={mid} onBack={() => open(project)} onProjects={() => open(null)} onAdmin={() => open(project, "admin")} onModels={() => open(project, "models")} onPickModel={(m) => open(project, "run", m)} onLogout={logout} />;
  if (project && view === "admin") return <AdminPage key={project.id} project={project} user={who} onBack={() => open(project)} onProjects={() => open(null)} onModels={() => open(project, "models")} onLogout={logout} />;
  if (project) return <LabelPage key={project.id} project={project} user={who} onExit={() => open(null)} onLogout={logout} onAdmin={() => open(project, "admin")} onModels={() => open(project, "models")} />;
  return <Projects user={who} onOpen={(p, v) => open(p, v ?? "label")} onLogout={logout} />;
}
