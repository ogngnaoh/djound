export type Mode = "prototype" | "backend";
export type Scenario =
  | "normal"
  | "empty"
  | "unclear"
  | "unavailable"
  | "creation-failure"
  | "playback-failure"
  | "stale";
export type Recipe = {
  instrument: "piano" | "electric_piano" | "guitar";
  bass: boolean;
  drums: boolean;
  density: "sparse" | "steady";
  backingVolume: "soft" | "normal";
  bpm: 100;
};
export type Note = {
  start: number;
  end: number;
  pitch: number;
  velocity: number;
};
export type Track = {
  id: string;
  projectId: string;
  projectTitle?: string;
  title: string;
  description: string;
  url: string;
  duration: number;
  kept: boolean;
  kind: "original" | "version" | "preview";
  recipe: Recipe;
  /** True when settings are authoritative, including the original local starting recipe. */
  recipeKnown?: boolean;
};
export type Project = {
  id: string;
  title: string;
  fileName: string;
  updatedAt: string;
  tracks: Track[];
  notes: Note[];
  confirmed: boolean;
  noteRevision: number;
  sourceRevision: number;
  draftRevision: number;
  draft: string;
  basisId: string;
  previewUrl?: string;
  available: boolean;
  /** An active preparation or creation for this project, separate from engine readiness. */
  processing?: boolean;
  unavailableReason?: string;
};
export type Proposal = {
  id: string;
  projectId: string;
  basisId: string;
  noteRevision: number;
  text: string;
  recipe: Recipe;
  before: Recipe;
  canGenerate: boolean;
  warnings: string[];
};
export type StudioService = {
  list(): Promise<Project[]>;
  get(id: string): Promise<Project>;
  upload(file: File): Promise<Project>;
  sample(): Promise<Project>;
  keep(projectId: string, trackId: string, kept: boolean): Promise<void>;
  saveDraft(project: Project, text: string, basisId: string): Promise<Project>;
  detect(project: Project, signal?: AbortSignal): Promise<Project>;
  saveNotes(
    project: Project,
    notes: Note[],
    confirm: boolean,
  ): Promise<Project>;
  preview(project: Project, signal?: AbortSignal): Promise<Project>;
  propose(
    project: Project,
    text: string,
    basisId: string,
    recipe?: Recipe,
    signal?: AbortSignal,
  ): Promise<Proposal>;
  create(
    project: Project,
    proposal: Proposal,
    signal?: AbortSignal,
  ): Promise<Project>;
  reset?(): void;
};
export const DEFAULT_RECIPE: Recipe = {
  instrument: "electric_piano",
  bass: true,
  drums: true,
  density: "steady",
  backingVolume: "normal",
  bpm: 100,
};
/** Local engine baseline for an uploaded melody; not a description of its waveform. */
export const LOCAL_SOURCE_RECIPE: Recipe = {
  instrument: "piano",
  bass: false,
  drums: false,
  density: "steady",
  backingVolume: "normal",
  bpm: 100,
};
export function instrumentName(value: Recipe["instrument"]) {
  return { piano: "Piano", electric_piano: "Electric piano", guitar: "Guitar" }[
    value
  ];
}
export function describeRecipe(r: Recipe) {
  return `${instrumentName(r.instrument)}${r.bass ? (r.backingVolume === "soft" ? ", softer bass" : ", bass") : ", no bass"}${r.drums ? " and drums" : ", no drums"}`;
}
