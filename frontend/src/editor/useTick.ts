import { useCallback, useReducer } from "react";

/** Fuerza un nuevo render cuando un controlador (editor o reproductor) avisa de que cambió algo visible. */
export function useTick(): () => void {
  const [, force] = useReducer((n: number) => n + 1, 0);
  return useCallback(() => force(), []);
}
