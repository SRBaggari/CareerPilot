import type { Metadata } from "next";

import { ReviewHubPage } from "@/components/applications/ReviewHubPage";

export const metadata: Metadata = { title: "Application Review · CareerPilot" };

export default function Page() {
  return <ReviewHubPage />;
}
