"use client";

import { useCallback, useState } from "react";
import { API_BASE_URL } from "@/lib/config";

export type GithubStatus = "loading" | "connected" | "disconnected";

export type GithubConnectionInfo = {
  status: GithubStatus;
  accountLogin: string | null;
};

export function GithubConnection({ info }: { info: GithubConnectionInfo }) {
  const [isRedirecting, setIsRedirecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleConnect = useCallback(async () => {
    setError(null);
    setIsRedirecting(true);
    try {
      const response = await fetch(`${API_BASE_URL}/github/install-url`, {
        credentials: "include",
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as {
          detail?: string;
        } | null;
        throw new Error(body?.detail ?? "Failed to start GitHub connection.");
      }
      const data = (await response.json()) as { url: string };
      window.location.href = data.url;
    } catch (err) {
      setIsRedirecting(false);
      setError(
        err instanceof Error
          ? err.message
          : "Failed to start GitHub connection.",
      );
    }
  }, []);

  if (info.status === "loading") {
    return (
      <div className="rounded-lg border border-zinc-800 px-5 py-4 text-sm text-zinc-500">
        Checking GitHub connection…
      </div>
    );
  }

  if (info.status === "connected") {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-zinc-800 px-5 py-4 text-sm text-emerald-400">
        <span aria-hidden>●</span>
        GitHub connected
        {info.accountLogin && (
          <span className="text-zinc-500">as @{info.accountLogin}</span>
        )}
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
          Buglensa needs access to your repositories through the Buglensa
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
      {error && <p className="text-sm text-red-400">{error}</p>}
    </div>
  );
}
