import type { ReactNode } from "react";
import { AppNav } from "./_components/app-nav";

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-full flex-1 flex-col">
      <AppNav />
      <main className="flex flex-1 flex-col">{children}</main>
    </div>
  );
}
