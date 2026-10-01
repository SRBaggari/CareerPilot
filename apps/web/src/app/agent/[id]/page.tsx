import type { Metadata } from "next";

import { AppHeader } from "@/components/AppHeader";
import { AgentRunPage } from "@/components/agent/AgentRunPage";

export const metadata: Metadata = { title: "Agent run · CareerPilot" };

export default async function Page(props: PageProps<"/agent/[id]">) {
  const { id } = await props.params;
  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <AppHeader current="/agent" />
      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">
        <AgentRunPage runId={id} />
      </main>
    </div>
  );
}
