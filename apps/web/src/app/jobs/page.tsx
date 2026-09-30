import type { Metadata } from "next";

import { JobsPage } from "@/components/jobs/JobsPage";

export const metadata: Metadata = { title: "Jobs · CareerPilot" };

export default function Page() {
  return <JobsPage />;
}
