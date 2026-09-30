import type { Metadata } from "next";

import { AppHeader } from "@/components/AppHeader";
import { ApplicationQuestionsPage } from "@/components/answers/ApplicationQuestionsPage";

export const metadata: Metadata = { title: "Application questions · CareerPilot" };

export default async function Page(props: PageProps<"/jobs/[id]/questions">) {
  const { id } = await props.params;
  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <AppHeader current="/jobs" />
      <main className="mx-auto w-full max-w-4xl flex-1 px-6 py-8">
        <ApplicationQuestionsPage jobId={id} />
      </main>
    </div>
  );
}
