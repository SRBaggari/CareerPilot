import type { Metadata } from "next";

import { AppHeader } from "@/components/AppHeader";
import { ApplicationsPage } from "@/components/applications/ApplicationsPage";

export const metadata: Metadata = { title: "Applications · CareerPilot" };

export default function Page() {
  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <AppHeader current="/applications" />
      <main className="mx-auto w-full max-w-7xl flex-1 px-6 py-8">
        <ApplicationsPage />
      </main>
    </div>
  );
}
