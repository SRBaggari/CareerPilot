import type { Metadata } from "next";

import { ResumeBuilderPage } from "@/components/documents/DocumentsHub";

export const metadata: Metadata = { title: "Resume Builder · CareerPilot" };

export default function Page() {
  return <ResumeBuilderPage />;
}
