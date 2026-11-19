import { useEffect, useRef, useState } from "react";
import { pendingCommand, PendingCommandConflict } from "../recovery";
import { RenditionError } from "./api";

// Persist the original payload and idempotency key before sending. The server
// may already have saved it even if the browser never received its response.
export function useRecoverableRequest<T extends { key: string }>(
  slot: string,
  send: (command: T) => Promise<void>,
) {
  const [pending, setPending] = useState<T>();
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  useEffect(() => {
    let mounted = true;
    pendingCommand<T>(slot)
      .then((saved) => {
        if (mounted) setPending(saved);
      })
      .catch((e: Error) => {
        if (mounted) setError(e.message);
      })
      .finally(() => {
        if (mounted) setBusy(false);
      });
    return () => {
      mounted = false;
    };
  }, [slot]);
  async function submit(command: T) {
    if (inFlight.current || busy) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      await pendingCommand(slot, command);
      setPending(command);
      await send(command);
      await pendingCommand(slot, null, command.key);
      setPending(undefined);
    } catch (e) {
      if (e instanceof PendingCommandConflict) {
        setPending(e.command as T);
      } else if (
        e instanceof RenditionError &&
        e.status >= 400 &&
        e.status < 500 &&
        e.status !== 408 &&
        e.status !== 429
      ) {
        await pendingCommand(slot, null, command.key);
        setPending(undefined);
      }
      setError(
        e instanceof Error ? e.message : "Could not complete this request.",
      );
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  return { pending, busy, error, submit };
}
