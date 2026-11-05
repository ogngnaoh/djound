import { createContext, useContext } from "react";
import type { Mode, Scenario, StudioService } from "./types";

export function storage(key: string, fallback: string) {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}
export function remember(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {}
}
export const Context = createContext<{
  mode: Mode;
  scenario: Scenario;
  service: StudioService;
  setMode: (v: Mode) => void;
  setScenario: (v: Scenario) => void;
} | null>(null);
export function useStudio() {
  return useContext(Context)!;
}
