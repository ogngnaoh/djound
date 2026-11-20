import { correspondingPosition } from "../web/src/renditions/songTime.ts";
import { parentTimeMap } from "../server/src/main/resources/demo/evening-study.json";

export function demoPosition(seconds, from, to, previous) {
  return correspondingPosition(
    seconds,
    from === "edit" ? parentTimeMap : undefined,
    to === "edit" ? parentTimeMap : undefined,
    previous,
  );
}
