import { Ellipsis } from "lucide-react";
import { Popover } from "radix-ui";
import { clock } from "../studio/Player";
import type { RecordingProject, Rendition } from "./api";

export function TakeDetails({
  project,
  take,
  parent,
}: {
  project: RecordingProject;
  take?: Rendition;
  parent: string;
}) {
  const start = take?.appliedStartSeconds ?? take?.startSeconds;
  const end = take?.appliedEndSeconds ?? take?.endSeconds;
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          type="button"
          className="explorer-button explorer-more"
          aria-label={take ? "Take details" : "Recording details"}
        >
          <Ellipsis aria-hidden="true" />
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          sideOffset={8}
          collisionPadding={16}
          align="end"
          aria-label={take ? "Take details" : "Recording details"}
          className="explorer-popover"
        >
          <strong>{take?.label ?? "Original recording"}</strong>
          {take && (
            <>
              <p>{take.request}</p>
              <p>{take.caption}</p>
              <small>
                From {parent}
                {start != null &&
                  end != null &&
                  " · " + clock(start) + "–" + clock(end)}
              </small>
            </>
          )}
          {!take && (
            <p>
              {project.title} · {clock(project.durationSeconds)}
            </p>
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
