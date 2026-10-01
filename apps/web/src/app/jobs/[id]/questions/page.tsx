import type { Metadata } from "next";

import { ApplicationQuestionsPage } from "@/components/answers/ApplicationQuestionsPage";

export const metadata: Metadata = { title: "Application questions · CareerPilot" };

export default async function Page(props: PageProps<"/jobs/[id]/questions">) {
  const { id } = await props.params;
  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-6 sm:px-6 sm:py-8">
        <ApplicationQuestionsPage jobId={id} />
      </main>
    </div>
  );
}
