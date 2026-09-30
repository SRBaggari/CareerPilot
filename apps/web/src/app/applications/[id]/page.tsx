import type { Metadata } from "next";

import { AppHeader } from "@/components/AppHeader";
import { ApplicationDetailPage } from "@/components/applications/ApplicationDetailPage";

export const metadata: Metadata = { title: "Application · CareerPilot" };

export default async function Page(props: PageProps<"/applications/[id]">) {
  const { id } = await props.params;
  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <AppHeader current="/applications" />
      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">
        <ApplicationDetailPage applicationId={id} />
      </main>
    </div>
  );
}
