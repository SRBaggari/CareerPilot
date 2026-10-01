import type { Metadata } from "next";

import { JobAnalysisPage } from "@/components/jobs/JobsPage";

export const metadata: Metadata = { title: "Job Analysis · CareerPilot" };

export default function Page() {
  return <JobAnalysisPage />;
}
