import type { Project } from "./types";

export function ProjectCard({ project }: { project: Project }) {
  return (
    <li className="flex flex-col gap-1 rounded-lg border border-zinc-800 px-5 py-4">
      <span className="text-sm font-medium text-zinc-50">{project.name}</span>
      <span className="text-sm text-zinc-500">{project.githubRepo}</span>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-600">
        <span>Branch: {project.defaultBranch}</span>
        {project.appUrl && <span>App URL: {project.appUrl}</span>}
      </div>
    </li>
  );
}
