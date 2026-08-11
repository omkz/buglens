"use client";

import { useState } from "react";
import { CreateProjectForm } from "./_components/create-project-form";
import { EmptyState } from "./_components/empty-state";
import { ProjectCard } from "./_components/project-card";
import type { Project } from "./_components/types";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [isCreating, setIsCreating] = useState(false);

  function handleCreate(project: Project) {
    setProjects((prev) => [...prev, project]);
    setIsCreating(false);
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-8 px-6 py-16">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-50">
          Projects
        </h1>
        {!isCreating && (
          <button
            type="button"
            onClick={() => setIsCreating(true)}
            className="rounded-full bg-zinc-50 px-4 py-2 text-sm font-medium text-black transition-colors hover:bg-zinc-200"
          >
            Create Project
          </button>
        )}
      </div>

      {isCreating ? (
        <CreateProjectForm
          onCreate={handleCreate}
          onCancel={() => setIsCreating(false)}
        />
      ) : projects.length === 0 ? (
        <EmptyState onCreate={() => setIsCreating(true)} />
      ) : (
        <ul className="flex flex-col gap-3">
          {projects.map((project) => (
            <ProjectCard key={project.id} project={project} />
          ))}
        </ul>
      )}
    </div>
  );
}
