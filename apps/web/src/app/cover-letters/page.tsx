import type { Metadata } from "next";

import { CoverLettersPage } from "@/components/documents/DocumentsHub";

export const metadata: Metadata = { title: "Cover Letters · CareerPilot" };

export default function Page() {
  return <CoverLettersPage />;
}
