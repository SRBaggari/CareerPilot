import { WorkflowSteps } from "@/components/WorkflowSteps";
import { WORKFLOW_STEPS } from "@/lib/workflow";

const PRINCIPLES = [
  {
    title: "Evidence, not invention",
    body: "Every line in a generated resume or letter traces back to something you actually provided.",
  },
  {
    title: "You stay in control",
    body: "Applications are never submitted without your explicit approval.",
  },
  {
    title: "Respectful automation",
    body: "No bypassing logins or CAPTCHAs, and no scraping sites that prohibit it.",
  },
] as const;

export default function Home() {
  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <header className="border-b border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <span className="text-lg font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            CareerPilot
          </span>
          <span className="rounded-full border border-zinc-300 px-3 py-1 text-xs text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">
            Early preview
          </span>
        </div>
      </header>

      <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-16">
        <section className="max-w-2xl">
          <h1 className="text-4xl font-semibold tracking-tight text-balance text-zinc-900 dark:text-zinc-50">
            Your personal AI co-pilot for job applications
          </h1>
          <p className="mt-4 text-lg leading-8 text-zinc-600 dark:text-zinc-400">
            CareerPilot helps students and job seekers find relevant roles, tailor applications from
            their real experience, and apply faster, with a human approving every step that matters.
          </p>
        </section>

        <section className="mt-12 grid gap-4 sm:grid-cols-3" aria-label="Principles">
          {PRINCIPLES.map((p) => (
            <div key={p.title}>
              <h2 className="font-medium text-zinc-900 dark:text-zinc-50">{p.title}</h2>
              <p className="mt-1 text-sm leading-6 text-zinc-600 dark:text-zinc-400">{p.body}</p>
            </div>
          ))}
        </section>

        <section className="mt-16">
          <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">How it works</h2>
          <div className="mt-6">
            <WorkflowSteps steps={WORKFLOW_STEPS} />
          </div>
        </section>
      </main>

      <footer className="border-t border-zinc-200 py-6 text-center text-sm text-zinc-500 dark:border-zinc-800">
        CareerPilot: human-in-the-loop by design.
      </footer>
    </div>
  );
}
