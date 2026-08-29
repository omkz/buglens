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
    <header className="border-b border-zinc-800/80 bg-zinc-950">
      <nav className="mx-auto flex min-h-16 w-full max-w-5xl flex-wrap items-center px-4 py-3 sm:flex-nowrap sm:px-6 sm:py-0">
        <Link
          href="/"
          className="flex items-center gap-2.5 sm:border-r sm:border-zinc-800 sm:pr-5"
          aria-label="Buglensa home"
        >
          <span
            aria-hidden="true"
            className="flex size-7 items-center justify-center rounded-md border border-zinc-700 bg-zinc-900 font-mono text-xs font-semibold text-zinc-100"
          >
            B
          </span>
          <span className="text-sm font-semibold tracking-tight text-zinc-50">
            Buglensa
          </span>
        </Link>

        <ul className="order-3 mt-3 flex w-full items-center gap-1 border-t border-zinc-900 pt-3 sm:order-none sm:mt-0 sm:w-auto sm:border-0 sm:pl-4 sm:pt-0">
          {links.map((link) => {
            const isActive =
              pathname === link.href || pathname?.startsWith(`${link.href}/`);
            return (
              <li key={link.href}>
                <Link
                  href={link.href}
                  aria-current={isActive ? "page" : undefined}
                  className={`rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                    isActive
                      ? "bg-zinc-900 text-zinc-50 ring-1 ring-inset ring-zinc-800"
                      : "text-zinc-500 hover:bg-zinc-900/60 hover:text-zinc-200"
                  }`}
                >
                  {link.label}
                </Link>
              </li>
            );
          })}
        </ul>

        <a
          href="https://github.com/omkz/buglensa"
          target="_blank"
          rel="noreferrer"
          className="ml-auto text-sm font-medium text-zinc-500 transition-colors hover:text-zinc-200"
        >
          GitHub <span aria-hidden="true">↗</span>
        </a>
      </nav>
    </header>
  );
}
