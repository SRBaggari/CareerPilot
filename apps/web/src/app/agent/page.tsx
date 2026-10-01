import type { Metadata } from "next";

import { AgentPage } from "@/components/agent/AgentPage";

export const metadata: Metadata = { title: "Agent · CareerPilot" };

export default async function Page(props: PageProps<"/agent">) {
  const { job } = await props.searchParams;
  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-6 sm:px-6 sm:py-8">
        <AgentPage initialJobId={typeof job === "string" ? job : ""} />
      </main>
    </div>
  );
}
