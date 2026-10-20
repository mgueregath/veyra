/** Símbolo de Veyra (kit de marca en public/brand). */
export function VeyraMark({ size = 28, inverse = false }: { size?: number; inverse?: boolean }) {
  return <img className="vmark" src={inverse ? "/brand/symbol-inverse.svg" : "/brand/symbol.svg"} alt="" height={size} style={{ height: size, width: "auto" }} />;
}

/** Logo horizontal: `light` para fondos oscuros (versión blanca). Se escala por la altura, sin estirarlo. */
export default function Logo({ size = 30, light = false }: { size?: number; light?: boolean }) {
  return <img className="logo" src={light ? "/brand/logo-inverse.svg" : "/brand/logo.svg"} alt="Veyra" title="Veyra: anotación y visión artificial" style={{ height: size, width: "auto" }} />;
}
