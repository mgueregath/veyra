import type { WorkMode } from "../api/client";
import Icon from "./Icon";

export const MODES: { id: WorkMode; title: string; text: string; icon: "shield" | "users" }[] = [
  { id: "review", title: "Con revisión", icon: "shield", text: "Cada persona etiqueta por su cuenta. Un revisor compara las versiones y confirma cuál es la definitiva. Sirve cuando importa medir el acuerdo entre personas." },
  { id: "collab", title: "Colaborativa", icon: "users", text: "Todas las personas editan las mismas etiquetas y la última modificación es la versión final. Es más ágil; no hay revisor ni acuerdo que medir." },
];

/** Elige la modalidad de trabajo del proyecto (dos tarjetas excluyentes). */
export default function ModePicker({ value, onChange, disabled, compact }: { value: WorkMode; onChange: (m: WorkMode) => void; disabled?: boolean; compact?: boolean }) {
  return (
    <div className={"modepick" + (compact ? " compact" : "")} id="mode-picker" role="radiogroup" aria-label="Modalidad de trabajo">
      {MODES.map((m) => (
        <button key={m.id} type="button" role="radio" aria-checked={value === m.id} id={"mode-" + m.id} className={"modeopt" + (value === m.id ? " on" : "")} disabled={disabled} onClick={() => onChange(m.id)}>
          <span className="hic"><Icon name={m.icon} size={17} /></span>
          <span><b>{m.title}</b><small>{m.text}</small></span>
        </button>
      ))}
    </div>
  );
}
