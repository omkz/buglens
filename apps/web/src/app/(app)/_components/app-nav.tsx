"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/projects", label: "Projects" },
  { href: "/investigations", label: "Investigations" },
];

export function AppNav() {
  const pathname = usePathname();

  return (
    <header className="border-b border-zinc-800">
      <nav className="mx-auto flex w-full max-w-5xl items-center gap-8 px-6 py-4">
        <Link
          href="/projects"
          className="text-sm font-semibold tracking-wide text-zinc-50"
        >
          BugLens
        </Link>
        <ul className="flex items-center gap-6">
          {links.map((link) => {
            const isActive =
              pathname === link.href || pathname?.startsWith(`${link.href}/`);
            return (
              <li key={link.href}>
                <Link
                  href={link.href}
                  className={`text-sm font-medium transition-colors ${
                    isActive
                      ? "text-zinc-50"
                      : "text-zinc-500 hover:text-zinc-200"
                  }`}
                >
                  {link.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </header>
  );
}
