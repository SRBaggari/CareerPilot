import type { Metadata } from "next";

import { AgentRunPage } from "@/components/agent/AgentRunPage";

export const metadata: Metadata = { title: "Agent run · CareerPilot" };

export default async function Page(props: PageProps<"/agent/[id]">) {
  const { id } = await props.params;
  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:px-6 sm:py-8">
        <AgentRunPage runId={id} />
      </main>
    </div>
  );
}
