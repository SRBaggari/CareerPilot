/**
 * The CareerPilot pipeline, in order. Displayed on the landing page and used as the
 * shared vocabulary for later phases. `humanGate` marks steps that must never proceed
 * without explicit user approval.
 */
export type WorkflowStep = {
  id: string;
  title: string;
  description: string;
  humanGate?: boolean;
};

export const WORKFLOW_STEPS: readonly WorkflowStep[] = [
  {
    id: "profile",
    title: "Master profile",
    description: "Your experience, skills, and evidence: the single source of truth.",
  },
  {
    id: "discovery",
    title: "Job discovery",
    description: "Find openings from sources that permit automated access.",
  },
  {
    id: "analysis",
    title: "JD analysis",
    description: "Extract requirements, responsibilities, and signals from each posting.",
  },
  {
    id: "matching",
    title: "Semantic matching",
    description: "Compare your evidence against each role using embeddings.",
  },
  {
    id: "gaps",
    title: "Skill-gap analysis",
    description: "See what a role asks for that your evidence does not yet show.",
  },
  {
    id: "resume",
    title: "Tailored resume",
    description: "Reorder and rephrase real experience for the role. Nothing is invented.",
  },
  {
    id: "cover-letter",
    title: "Tailored cover letter",
    description: "A role-specific letter grounded in your stored evidence.",
  },
  {
    id: "verification",
    title: "Claim verification",
    description: "Every generated claim is traced back to a piece of your evidence.",
  },
  {
    id: "approval",
    title: "Your approval",
    description: "Nothing is sent until you review and explicitly approve it.",
    humanGate: true,
  },
  {
    id: "apply",
    title: "Assisted application",
    description:
      "The browser fills forms and stops before submit; it submits only after you confirm the exact review.",
    humanGate: true,
  },
  {
    id: "tracking",
    title: "Tracking",
    description: "Follow the status of every application in one place.",
  },
];
