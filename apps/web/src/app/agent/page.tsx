import type { Metadata } from "next";

import { AppHeader } from "@/components/AppHeader";
import { AgentPage } from "@/components/agent/AgentPage";

export const metadata: Metadata = { title: "Agent · CareerPilot" };

export default async function Page(props: PageProps<"/agent">) {
  const { job } = await props.searchParams;
  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <AppHeader current="/agent" />
      <main className="mx-auto w-full max-w-4xl flex-1 px-6 py-8">
        <AgentPage initialJobId={typeof job === "string" ? job : ""} />
      </main>
    </div>
  );
}
