import type { Metadata } from "next";

import { SettingsPage } from "@/components/settings/SettingsPage";

export const metadata: Metadata = { title: "Settings · CareerPilot" };

export default function Page() {
  return <SettingsPage />;
}
