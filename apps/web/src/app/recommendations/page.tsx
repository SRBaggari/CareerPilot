import type { Metadata } from "next";

import { AppHeader } from "@/components/AppHeader";
import { RecommendationsPage } from "@/components/recommendations/RecommendationsPage";

export const metadata: Metadata = { title: "Recommended jobs · CareerPilot" };

export default function Page() {
  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <AppHeader current="/recommendations" />
      <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-8">
        <RecommendationsPage />
      </main>
    </div>
  );
}
