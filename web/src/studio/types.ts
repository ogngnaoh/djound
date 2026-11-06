import type { components } from "../api-schema";

export type Mode = "prototype" | "backend";
export type Scenario =
  | "normal"
  | "empty"
  | "unclear"
  | "unavailable"
  | "creation-failure"
  | "playback-failure"
  | "stale";
export type Stem = "lead" | "bass" | "drums" | "chords";
export type Harmony = components["schemas"]["LocalHarmony"];
export type Chord = Exclude<Harmony["chords"][number], null>;
export type ChordSuggestion = {
  chord: Chord | null;
  ambiguous: boolean;
  alternatives: Chord[];
};
export type MixStrip = components["schemas"]["LocalMixStrip"];
export type Recipe = {
  instrument: components["schemas"]["LocalRecipe"]["instrument"];
  bass: boolean;
  drums: boolean;
  density: "sparse" | "steady";
  backingVolume: "soft" | "normal";
  bpm: number;
  groove?: components["schemas"]["LocalRecipe"]["groove"];
  harmony?: Harmony;
  mix?: Partial<Record<Stem, MixStrip>>;
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
  noteRevision?: number;
};
export type Project = {
  id: string;
  title: string;
  fileName: string;
  updatedAt: string;
  tracks: Track[];
  notes: Note[];
  confirmed: boolean;
  melodyDuration?: number;
  noteRevision: number;
  sourceRevision: number;
  draftRevision: number;
  draft: string;
  basisId: string;
  previewUrl?: string;
  available: boolean;
  freeformAvailable?: boolean;
  freeformUnavailableReason?: string;
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
  chordSuggestions?: ChordSuggestion[];
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
    reviewedPlanId?: string,
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
  return {
    piano: "Piano",
    recorded: "Uploaded recording",
    electric_piano: "Electric piano",
    guitar: "Guitar",
  }[value];
}
export function describeRecipe(r: Recipe) {
  const sounding = r.harmony?.chords.some((chord) => chord !== null);
  const bass = !r.bass
    ? "no bass"
    : sounding === false
      ? "resting bass"
      : r.backingVolume === "soft"
        ? "softer bass"
        : "bass";
  return `${instrumentName(r.instrument)}, ${bass}${r.drums ? " and drums" : ", no drums"}${sounding ? ", chord accompaniment" : ""}`;
}
