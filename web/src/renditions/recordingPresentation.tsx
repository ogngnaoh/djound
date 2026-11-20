const sleeves = ["fold-copper", "loop-olive", "arc-cobalt"];

export function recordingName(title: string) {
  return title.replace(/\.(?:wav|mp3|m4a)$/i, "").trim() || title;
}

export function RecordingArtwork({
  id,
  className,
}: {
  id: string;
  title?: string;
  className?: string;
}) {
  // IDs are stable across takes, favorites and restores. Each cover stays with its recording.
  const slot = Number.parseInt(id.slice(0, 2), 16) % sleeves.length;
  return (
    <img
      className={className}
      src={"/art/" + (sleeves[slot] ?? "loop-olive") + ".jpg"}
      width={680}
      height={680}
      alt=""
      draggable={false}
    />
  );
}
