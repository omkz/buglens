export function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="flex flex-col items-center gap-4 rounded-lg border border-dashed border-zinc-800 px-6 py-16 text-center">
      <p className="text-base font-medium text-zinc-200">No projects yet</p>
      <p className="max-w-sm text-sm text-zinc-500">
        Create your first project to start investigating bugs with BugLens.
      </p>
      <button
        type="button"
        onClick={onCreate}
        className="rounded-full bg-zinc-50 px-4 py-2 text-sm font-medium text-black transition-colors hover:bg-zinc-200"
      >
        Create Project
      </button>
    </div>
  );
}
