import { useQuery } from "@tanstack/react-query";
import { api, type Project } from "../api/client";

export default function Projects({ onOpen }: { onOpen: (p: Project) => void }) {
  const q = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  if (q.isLoading) return <div className="center">Cargando…</div>;
  if (!q.data?.length) return <div className="center">No perteneces a ningún proyecto todavía.</div>;
  return (
    <main className="cards">
      {q.data.map((p) => (
        <button key={p.id} className="pcard" title={`Abrir el proyecto «${p.name}» (tu rol: ${p.role})`} onClick={() => onOpen(p)}>
          <h2>{p.name}</h2>
          <p className="mut">Rol: {p.role}</p>
          <progress value={p.n_reviewed} max={Math.max(1, p.n_frames)} />
          <p>{p.n_reviewed} / {p.n_frames} revisadas por ti</p>
        </button>
      ))}
    </main>
  );
}
