import type { Metadata } from "next";

import { RecommendationsPage } from "@/components/recommendations/RecommendationsPage";

export const metadata: Metadata = { title: "Recommended jobs · CareerPilot" };

export default function Page() {
  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-6 sm:px-6 sm:py-8">
        <RecommendationsPage />
      </main>
    </div>
  );
}
