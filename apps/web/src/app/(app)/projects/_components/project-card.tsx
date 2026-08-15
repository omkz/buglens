import Link from "next/link";
import type { Project } from "./types";

export function ProjectCard({ project }: { project: Project }) {
  return (
    <li className="flex flex-col gap-1 rounded-lg border border-zinc-800 px-5 py-4">
      <span className="text-sm font-medium text-zinc-50">{project.name}</span>
      <span className="text-sm text-zinc-500">
        {project.github_repository_full_name}
      </span>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-600">
        <span>Branch: {project.default_branch}</span>
        {project.app_url && <span>App URL: {project.app_url}</span>}
      </div>
      <div className="mt-3 flex justify-end">
        <Link
          href={`/projects/${project.id}/report`}
          className="rounded-full border border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-200 transition-colors hover:border-zinc-500 hover:bg-zinc-800"
        >
          Report Bug
        </Link>
      </div>
    </li>
  );
}
