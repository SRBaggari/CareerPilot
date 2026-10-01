import type { Metadata } from "next";

import { ClaimCheckerPage } from "@/components/verification/ClaimCheckerPage";

export const metadata: Metadata = { title: "Check claims · CareerPilot" };

export default function Page() {
  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-6 sm:px-6 sm:py-8">
        <ClaimCheckerPage />
      </main>
    </div>
  );
}
