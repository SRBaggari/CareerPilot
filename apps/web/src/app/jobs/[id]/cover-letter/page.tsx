import type { Metadata } from "next";

import { CoverLetterPage } from "@/components/coverLetter/CoverLetterPage";

export const metadata: Metadata = { title: "Cover letter · CareerPilot" };

export default async function Page(props: PageProps<"/jobs/[id]/cover-letter">) {
  const { id } = await props.params;
  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:px-6 sm:py-8">
        <CoverLetterPage jobId={id} />
      </main>
    </div>
  );
}
