export function AsyncActivity({ label }: { label: string }) {
  return (
    <span
      role="status"
      aria-live="polite"
      className="inline-flex items-center gap-2"
    >
      <span
        aria-hidden="true"
        className="size-3.5 shrink-0 animate-spin rounded-full border-2 border-current border-r-transparent opacity-75 motion-reduce:animate-none"
      />
      <span>{label}</span>
    </span>
  );
}
