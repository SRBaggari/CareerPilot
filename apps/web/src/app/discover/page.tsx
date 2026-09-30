import type { Metadata } from "next";

import { AppHeader } from "@/components/AppHeader";
import { DiscoveryPage } from "@/components/discovery/DiscoveryPage";

export const metadata: Metadata = { title: "Discover jobs · CareerPilot" };

export default function Page() {
  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <AppHeader current="/discover" />
      <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-8">
        <DiscoveryPage />
      </main>
    </div>
  );
}
