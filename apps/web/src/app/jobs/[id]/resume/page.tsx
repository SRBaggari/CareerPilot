import type { Metadata } from "next";

import { TailoredResumePage } from "@/components/resume/TailoredResumePage";

export const metadata: Metadata = { title: "Tailored resume · CareerPilot" };

export default async function Page(props: PageProps<"/jobs/[id]/resume">) {
  const { id } = await props.params;
  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:px-6 sm:py-8">
        <TailoredResumePage jobId={id} />
      </main>
    </div>
  );
}
