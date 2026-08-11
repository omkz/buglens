"use client";

import { useCallback, useState } from "react";
import { API_BASE_URL } from "@/lib/config";

export type GithubStatus = "loading" | "connected" | "disconnected";

export function GithubConnection({ status }: { status: GithubStatus }) {
  const [isRedirecting, setIsRedirecting] = useState(false);

  const handleConnect = useCallback(async () => {
    setIsRedirecting(true);
    try {
      const response = await fetch(`${API_BASE_URL}/github/install-url`);
      if (!response.ok) {
        throw new Error("Failed to start GitHub connection");
      }
      const data = (await response.json()) as { url: string };
      window.location.href = data.url;
    } catch {
      setIsRedirecting(false);
    }
  }, []);

  if (status === "loading") {
    return (
      <div className="rounded-lg border border-zinc-800 px-5 py-4 text-sm text-zinc-500">
        Checking GitHub connection…
      </div>
    );
  }

  if (status === "connected") {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-zinc-800 px-5 py-4 text-sm text-emerald-400">
        <span aria-hidden>●</span>
        GitHub connected
      </div>
    );
  }

  return (
    <div className="flex flex-col items-start gap-4 rounded-lg border border-dashed border-zinc-800 px-6 py-10">
      <div className="flex flex-col gap-1">
        <p className="text-base font-medium text-zinc-200">
          Connect GitHub to get started
        </p>
        <p className="max-w-sm text-sm text-zinc-500">
          BugLens needs access to your repositories through the BugLens
          GitHub App before you can create a project.
        </p>
      </div>
      <button
        type="button"
        onClick={handleConnect}
        disabled={isRedirecting}
        className="rounded-full bg-zinc-50 px-4 py-2 text-sm font-medium text-black transition-colors hover:bg-zinc-200 disabled:opacity-60"
      >
        {isRedirecting ? "Redirecting…" : "Connect GitHub"}
      </button>
    </div>
  );
}
