import type { Metadata } from "next";

import { AppHeader } from "@/components/AppHeader";
import { AssistPage } from "@/components/applications/AssistPage";

export const metadata: Metadata = { title: "Application review · CareerPilot" };

export default async function Page(props: PageProps<"/applications/[id]/assist">) {
  const { id } = await props.params;
  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <AppHeader current="/applications" />
      <main className="mx-auto w-full max-w-4xl flex-1 px-6 py-8">
        <AssistPage applicationId={id} />
      </main>
    </div>
  );
}
