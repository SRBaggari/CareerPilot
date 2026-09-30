import type { Metadata } from "next";

import { AppHeader } from "@/components/AppHeader";
import { JobMatchPage } from "@/components/matching/JobMatchPage";

export const metadata: Metadata = { title: "Job match · CareerPilot" };

export default async function Page(props: PageProps<"/jobs/[id]/match">) {
  const { id } = await props.params;
  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <AppHeader current="/jobs" />
      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">
        <JobMatchPage jobId={id} />
      </main>
    </div>
  );
}
