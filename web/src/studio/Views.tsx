import { Link } from "react-router-dom";
import { LoaderCircle } from "lucide-react";
import { Button } from "../components/ui/button";

export function Loading() {
  return (
    <div className="standalone loading-state" role="status">
      <LoaderCircle className="spin" />
      Loading your workspace…
    </div>
  );
}
export function Failure({
  message,
  retry,
}: {
  message: string;
  retry: () => void;
}) {
  return (
    <div className="standalone">
      <h1>Couldn’t load this workspace</h1>
      <p role="alert">{message}</p>
      <Button onClick={retry}>Try again</Button>
      <Link className="text-action" to="/">
        Back to Library
      </Link>
      <p className="subtle">
        Use Workspace options to switch to the clickable prototype.
      </p>
    </div>
  );
}
