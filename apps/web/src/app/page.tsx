export default function Home() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center px-6 py-24">
      <main className="flex w-full max-w-2xl flex-col items-center gap-8 text-center">
        <span className="text-sm font-medium tracking-wide text-zinc-400">
          BugLens
        </span>

        <h1 className="text-4xl font-semibold leading-tight tracking-tight text-zinc-50 sm:text-5xl">
          Show the bug. BugLens investigates it.
        </h1>

        <p className="max-w-lg text-lg leading-8 text-zinc-400">
          Turn bug recordings into reproduced and actionable engineering
          issues.
        </p>

        <button
          type="button"
          className="rounded-full bg-zinc-50 px-6 py-3 text-sm font-medium text-black transition-colors hover:bg-zinc-200"
        >
          Report a bug
        </button>
      </main>
    </div>
  );
}
