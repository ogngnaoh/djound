import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DropdownMenu } from "radix-ui";
import { MoreHorizontal, Trash2 } from "lucide-react";
import {
  createContext,
  type ReactNode,
  useContext,
  useId,
  useRef,
  useState,
} from "react";
import { Button } from "../components/ui/button";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "../components/ui/sheet";
import {
  projectKey,
  recordingList,
  renditionRequest,
  type Recording,
} from "./api";

type Change = {
  recording: Pick<Recording, "id" | "title">;
  moveToTrash: boolean;
};

const TrashContext = createContext<{
  busy: boolean;
  request: (change: Change, opener: HTMLButtonElement) => void;
} | null>(null);

// Keep the confirmation and Undo above the rows. A successful move removes its row.
export function RecordingTrashControls({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const [confirmation, setConfirmation] = useState<Change | null>(null);
  const [completed, setCompleted] = useState<Change | null>(null);
  const opener = useRef<HTMLButtonElement | null>(null);
  const feedback = useRef<HTMLDivElement>(null);
  const currentRecordings = useQuery({
    ...recordingList,
    enabled: !!confirmation,
  });
  const current = currentRecordings.data?.find(
    (row) => row.id === confirmation?.recording.id,
  );
  const working = !!current && current.inFlightCount > 0;
  const mutation = useMutation({
    mutationFn: ({ recording, moveToTrash }: Change) =>
      renditionRequest(`/${recording.id}/trash`, {
        method: moveToTrash ? "PUT" : "DELETE",
      }),
    onSuccess: (_response, change) => {
      setCompleted(change);
      setConfirmation(null);
    },
    onSettled: async (_response, error, { recording, moveToTrash }) => {
      await Promise.all([
        client.invalidateQueries({ queryKey: recordingList.queryKey }),
        client.invalidateQueries({ queryKey: projectKey(recording.id) }),
      ]);
      if (!error && !moveToTrash) {
        requestAnimationFrame(() => {
          if (
            document.activeElement === document.body ||
            document.activeElement === opener.current
          )
            feedback.current?.focus();
        });
      }
    },
  });

  function request(change: Change, button: HTMLButtonElement) {
    opener.current = button;
    mutation.reset();
    if (change.moveToTrash) setConfirmation(change);
    else mutation.mutate(change);
  }

  return (
    <TrashContext.Provider value={{ busy: mutation.isPending, request }}>
      {children}
      <div ref={feedback} tabIndex={-1} className="recording-trash-feedback">
        {completed && (
          <>
            <span role="status">
              {completed.recording.title}{" "}
              {completed.moveToTrash
                ? "moved to Trash."
                : "restored to Library."}
            </span>
            {completed.moveToTrash && (
              <Button
                type="button"
                variant="link"
                size="sm"
                disabled={mutation.isPending}
                onClick={(event) =>
                  request(
                    { recording: completed.recording, moveToTrash: false },
                    event.currentTarget,
                  )
                }
              >
                Undo
              </Button>
            )}
          </>
        )}
        {!confirmation && mutation.isError && (
          <p role="alert">{mutation.error.message}</p>
        )}
      </div>
      <Sheet
        open={confirmation !== null}
        onOpenChange={(open) => {
          if (!open && !mutation.isPending) {
            setConfirmation(null);
            mutation.reset();
          }
        }}
      >
        <SheetContent
          side="bottom"
          showCloseButton={false}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            const button = opener.current;
            if (button?.isConnected && !button.disabled) button.focus();
            else feedback.current?.focus();
          }}
        >
          <SheetHeader>
            <SheetTitle>
              Move {confirmation?.recording.title} to Trash?
            </SheetTitle>
            <SheetDescription>
              You can restore this recording and all its takes from Trash.
            </SheetDescription>
          </SheetHeader>
          <SheetFooter>
            {mutation.isError ? (
              <p role="alert">{mutation.error.message}</p>
            ) : (
              working && (
                <p role="status">Wait for the current edit to finish.</p>
              )
            )}
            <SheetClose asChild>
              <Button
                type="button"
                variant="outline"
                disabled={mutation.isPending}
              >
                Cancel
              </Button>
            </SheetClose>
            <Button
              type="button"
              variant="destructive"
              disabled={mutation.isPending || working}
              onClick={() => confirmation && mutation.mutate(confirmation)}
            >
              {mutation.isPending ? "Moving…" : "Move to Trash"}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </TrashContext.Provider>
  );
}

export function RecordingTrashAction({
  recording,
  inTrash = false,
}: {
  recording: Pick<Recording, "id" | "title" | "inFlightCount">;
  inTrash?: boolean;
}) {
  const controls = useContext(TrashContext);
  const reasonId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const openingConfirmation = useRef(false);
  if (!controls)
    throw new Error("RecordingTrashAction needs RecordingTrashControls.");
  const working = !inTrash && recording.inFlightCount > 0;
  if (!inTrash)
    return (
      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild>
          <button
            className="collection-menu-trigger"
            type="button"
            ref={trigger}
            aria-label={`Options for ${recording.title}`}
          >
            <MoreHorizontal size={19} aria-hidden="true" />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content
            align="end"
            sideOffset={5}
            className="collection-menu-content"
            onCloseAutoFocus={(event) => {
              if (openingConfirmation.current) event.preventDefault();
              openingConfirmation.current = false;
            }}
          >
            <DropdownMenu.Item
              disabled={controls.busy || working}
              aria-describedby={working ? reasonId : undefined}
              onSelect={() => {
                if (!trigger.current) return;
                openingConfirmation.current = true;
                controls.request(
                  { recording, moveToTrash: true },
                  trigger.current,
                );
              }}
            >
              <Trash2 size={15} /> Move to Trash
            </DropdownMenu.Item>
            {working && (
              <small id={reasonId}>
                Available when the current edit finishes.
              </small>
            )}
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    );
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      aria-label={`Restore ${recording.title}`}
      disabled={controls.busy}
      onClick={(event) =>
        controls.request({ recording, moveToTrash: false }, event.currentTarget)
      }
    >
      Restore
    </Button>
  );
}
