import type { Metadata } from "next";

import { AppHeader } from "@/components/AppHeader";
import { CoverLetterPage } from "@/components/coverLetter/CoverLetterPage";

export const metadata: Metadata = { title: "Cover letter · CareerPilot" };

export default async function Page(props: PageProps<"/jobs/[id]/cover-letter">) {
  const { id } = await props.params;
  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <AppHeader current="/jobs" />
      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">
        <CoverLetterPage jobId={id} />
      </main>
    </div>
  );
}
