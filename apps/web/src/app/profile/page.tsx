import type { Metadata } from "next";

import { ProfileDashboard } from "@/components/profile/ProfileDashboard";

export const metadata: Metadata = {
  title: "Master profile · CareerPilot",
};

export default function ProfilePage() {
  return <ProfileDashboard />;
}
