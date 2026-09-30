import type { Metadata } from "next";

import { AppHeader } from "@/components/AppHeader";
import { ClaimCheckerPage } from "@/components/verification/ClaimCheckerPage";

export const metadata: Metadata = { title: "Check claims · CareerPilot" };

export default function Page() {
  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <AppHeader current="/verify" />
      <main className="mx-auto w-full max-w-4xl flex-1 px-6 py-8">
        <ClaimCheckerPage />
      </main>
    </div>
  );
}
