import createClient from "openapi-fetch";
import type { components, paths } from "./api-schema";
export type Recording = components["schemas"]["Recording"];
export type Exploration = components["schemas"]["Exploration"];
export const api = createClient<paths>();
export async function getWorkspace() {
  const { data, error } = await api.GET("/api/workspace");
  if (error || !data) throw new Error("Could not connect to your workspace.");
  return data;
}
export async function getLibrary() {
  const { data, error } = await api.GET("/api/explorations");
  if (error || !data) throw new Error("Could not load your library.");
  return data.items;
}
export async function getExploration(id: string) {
  const { data, error } = await api.GET("/api/explorations/{id}", {
    params: { path: { id } },
  });
  if (error || !data)
    throw new Error(error?.detail || "Could not load this exploration.");
  return data;
}
