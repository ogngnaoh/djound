import { hashKey } from "@tanstack/react-query";
import { activeStems, normalizeMix, UNITY } from "./mixState.ts";
import type { Project, Recipe, Stem, Track } from "./types";

export function auditionUnavailable(
  project: Project,
  basis: Track,
  recipe: Recipe,
  stem: Stem,
) {
  if (basis.kind !== "version" || !basis.recipeKnown)
    return "Choose a saved local version to preview its tone.";
  if (project.basisId !== basis.id)
    return "Wait for the refinement basis to save.";
  if (!project.confirmed || basis.noteRevision !== project.noteRevision)
    return "Create a version with the current melody to preview its tone.";
  if (!project.available || project.processing)
    return "Tone previews are available when the local engine is ready.";
  const parent = basis.recipe;
  if (!activeStems(parent).includes(stem) || parent.mix?.[stem]?.muted)
    return "Choose a track that plays in the saved version.";
  // Preview changes just this track’s tone. Include all other recipe fields in
  // the comparison so later additions cannot accidentally preview the wrong mix.
  const strip = recipe.mix?.[stem] ?? UNITY;
  const comparable = normalizeMix({
    ...recipe,
    mix: {
      ...recipe.mix,
      [stem]: { ...strip, tone: parent.mix?.[stem]?.tone ?? "neutral" },
    },
  });
  if (hashKey([normalizeMix(parent)]) !== hashKey([comparable]))
    return "Review and create a version with your other changes before previewing its tone.";
}

/** Only switching into an audition of this same recording carries its playhead. */
export function auditionStart(
  current: Track | null,
  next: Track,
  time: number,
  ended: boolean,
) {
  const basis = next.auditionBasisId;
  return basis &&
    (current?.id === basis || current?.auditionBasisId === basis) &&
    !ended &&
    Number.isFinite(time) &&
    time > 0 &&
    time < next.duration
    ? time
    : 0;
}
