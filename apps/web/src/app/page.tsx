import Link from "next/link";

const steps = [
  {
    number: "01",
    title: "Evidence",
    description: "Screen recording, optional voice context, and logs.",
  },
  {
    number: "02",
    title: "Investigate",
    description:
      "Gemini and the Buglensa agent analyze the evidence and inspect the connected GitHub repository.",
  },
  {
    number: "03",
    title: "Reproduce",
    description:
      "Buglensa builds a constrained Playwright plan and attempts to reproduce the bug.",
  },
  {
    number: "04",
    title: "Route",
    description:
      "Review evidence, duplicate candidates, and an actionable result before choosing to create a GitHub issue.",
  },
];

const boundaries = [
  "GitHub App repository access",
  "Gemini + Google ADK investigation",
  "Constrained Playwright reproduction",
  "Explicit approval before issue creation",
];

const githubUrl = "https://github.com/omkz/buglensa";

function Wordmark() {
  return (
    <span className="flex items-center gap-2.5">
      <span
        aria-hidden="true"
        className="flex size-7 items-center justify-center rounded-md border border-zinc-700 bg-zinc-900 font-mono text-xs font-semibold text-zinc-100"
      >
        B
      </span>
      <span className="text-sm font-semibold tracking-tight text-zinc-50">
        Buglensa
      </span>
    </span>
  );
}

export default function Home() {
  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <header className="border-b border-zinc-900">
        <nav className="mx-auto flex h-16 w-full max-w-5xl items-center justify-between px-5 sm:px-6">
          <Link href="/" aria-label="Buglensa home">
            <Wordmark />
          </Link>

          <div className="flex items-center gap-5 sm:gap-6">
            <a
              href={githubUrl}
              target="_blank"
              rel="noreferrer"
              className="text-sm font-medium text-zinc-400 transition-colors hover:text-zinc-100"
            >
              GitHub <span aria-hidden="true">↗</span>
            </a>
            <Link
              href="/projects"
              className="rounded-md bg-zinc-100 px-3.5 py-2 text-sm font-medium text-zinc-950 transition-colors hover:bg-white"
            >
              Open app
            </Link>
          </div>
        </nav>
      </header>

      <main>
        <section className="mx-auto w-full max-w-5xl px-5 py-24 sm:px-6 sm:py-32 lg:py-36">
          <div className="max-w-3xl">
            <p className="mb-5 font-mono text-xs font-medium uppercase tracking-[0.18em] text-zinc-500">
              Autonomous bug investigation
            </p>
            <h1 className="text-balance text-4xl font-semibold leading-[1.08] tracking-[-0.035em] text-zinc-50 sm:text-6xl lg:text-7xl">
              Show the bug.
              <br />
              Buglensa investigates, reproduces, and routes it.
            </h1>
            <p className="mt-7 max-w-2xl text-lg leading-8 text-zinc-400 sm:text-xl">
              Turn screen recordings, voice context, and logs into reproduced,
              actionable engineering issues.
            </p>
            <div className="mt-9 flex flex-wrap items-center gap-3">
              <Link
                href="/projects"
                className="rounded-md bg-zinc-100 px-5 py-2.5 text-sm font-medium text-zinc-950 transition-colors hover:bg-white"
              >
                Start investigating
              </Link>
              <a
                href={githubUrl}
                target="_blank"
                rel="noreferrer"
                className="rounded-md border border-zinc-800 px-5 py-2.5 text-sm font-medium text-zinc-300 transition-colors hover:border-zinc-700 hover:bg-zinc-900 hover:text-zinc-50"
              >
                View on GitHub <span aria-hidden="true">↗</span>
              </a>
            </div>
          </div>
        </section>

        <section className="border-y border-zinc-900">
          <div className="mx-auto w-full max-w-5xl px-5 py-20 sm:px-6 sm:py-24">
            <div className="mb-12 max-w-xl">
              <p className="font-mono text-xs uppercase tracking-[0.18em] text-zinc-500">
                How it works
              </p>
              <h2 className="mt-3 text-2xl font-semibold tracking-tight text-zinc-100 sm:text-3xl">
                From evidence to an actionable result.
              </h2>
            </div>

            <ol className="grid gap-x-8 gap-y-10 sm:grid-cols-2 lg:grid-cols-4">
              {steps.map((step) => (
                <li key={step.number} className="border-t border-zinc-800 pt-5">
                  <span className="font-mono text-xs text-zinc-600">
                    {step.number}
                  </span>
                  <h3 className="mt-5 text-base font-medium text-zinc-100">
                    {step.title}
                  </h3>
                  <p className="mt-2 text-sm leading-6 text-zinc-500">
                    {step.description}
                  </p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="mx-auto w-full max-w-5xl px-5 py-20 sm:px-6 sm:py-24">
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/30 p-6 sm:p-8">
            <div className="grid gap-8 lg:grid-cols-[0.8fr_1.2fr] lg:items-end">
              <div>
                <p className="font-mono text-xs uppercase tracking-[0.18em] text-zinc-500">
                  Agentic, with boundaries
                </p>
                <h2 className="mt-3 text-2xl font-semibold tracking-tight text-zinc-100">
                  Investigation is autonomous. Publishing is not.
                </h2>
                <p className="mt-3 max-w-lg text-sm leading-6 text-zinc-500">
                  Buglensa can inspect, reason, and reproduce within defined tool
                  boundaries. You stay in control of what reaches GitHub.
                </p>
              </div>

              <ul className="grid gap-3 sm:grid-cols-2">
                {boundaries.map((boundary) => (
                  <li
                    key={boundary}
                    className="flex items-center gap-3 text-sm text-zinc-300"
                  >
                    <span
                      aria-hidden="true"
                      className="size-1.5 shrink-0 rounded-full bg-zinc-500"
                    />
                    {boundary}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-zinc-900">
        <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 px-5 py-7 text-sm sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <p className="text-zinc-500">
            <span className="font-medium text-zinc-300">Buglensa</span>
            <span className="mx-2 text-zinc-700">·</span>
            Built for the All Things Agentic Hackathon.
          </p>
          <div className="flex items-center gap-5">
            <a
              href={githubUrl}
              target="_blank"
              rel="noreferrer"
              className="text-zinc-500 transition-colors hover:text-zinc-200"
            >
              GitHub <span aria-hidden="true">↗</span>
            </a>
            <Link
              href="/projects"
              className="text-zinc-500 transition-colors hover:text-zinc-200"
            >
              Open app
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
