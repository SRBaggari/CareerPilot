import type { Metadata } from "next";

import { ApplicationsPage } from "@/components/applications/ApplicationsPage";

export const metadata: Metadata = { title: "Applications · CareerPilot" };

export default function Page() {
  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6 sm:py-8">
        <ApplicationsPage />
      </main>
    </div>
  );
}
