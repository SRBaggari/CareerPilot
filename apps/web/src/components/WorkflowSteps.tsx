import type { WorkflowStep } from "@/lib/workflow";

type Props = {
  steps: readonly WorkflowStep[];
};

export function WorkflowSteps({ steps }: Props) {
  return (
    <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-label="CareerPilot workflow">
      {steps.map((step, index) => (
        <li
          key={step.id}
          className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950"
        >
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-zinc-900 text-xs font-semibold text-white dark:bg-zinc-100 dark:text-zinc-900">
              {index + 1}
            </span>
            <h3 className="font-medium text-zinc-900 dark:text-zinc-50">{step.title}</h3>
            {step.humanGate ? (
              <span className="ml-auto rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-900 dark:bg-amber-900/40 dark:text-amber-200">
                Requires approval
              </span>
            ) : null}
          </div>
          <p className="mt-2 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
            {step.description}
          </p>
        </li>
      ))}
    </ol>
  );
}
