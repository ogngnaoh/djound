import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "../components/ui/button";
import { projectKey, recordingList, renditionRequest } from "./api";
import { LibraryReturnLink } from "./libraryNavigation";

export function RestoreRecording({ id }: { id: string }) {
  const client = useQueryClient();
  const restore = useMutation({
    mutationFn: () => renditionRequest(`/${id}/trash`, { method: "DELETE" }),
    onSuccess: () =>
      Promise.all([
        client.invalidateQueries({ queryKey: projectKey(id) }),
        client.invalidateQueries({ queryKey: recordingList.queryKey }),
      ]),
  });
  return (
    <section className="standalone">
      <h1>This recording is in Trash</h1>
      <p>Restore it to reopen the original, its takes, and your favorite.</p>
      {restore.isError && <p role="alert">{restore.error.message}</p>}
      <Button onClick={() => restore.mutate()} disabled={restore.isPending}>
        {restore.isPending ? "Restoring…" : "Restore recording"}
      </Button>
      <LibraryReturnLink />
    </section>
  );
}
