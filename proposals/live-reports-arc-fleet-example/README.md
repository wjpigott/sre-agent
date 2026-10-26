# Proposal: Live Reports as a sharable Azure SRE Agent recipe asset

**Status: draft proposal, not a finished recipe.** This is not submitted as a pull
request yet. It accompanies a GitHub issue asking the maintainers where (or
whether) this kind of asset fits in `sreagent-templates/`, since the existing
[Recipe Structure](../../sreagent-templates/CONTRIBUTING.md#recipe-structure)
has no category for it today.

## What this is

[Azure SRE Agent Live Reports](https://sre.azure.com/docs) are interactive HTML
dashboards that run inside the agent and call the agent's own read-only
connector tools at view time (for example, a Log Analytics query tool), so the
numbers on screen reflect the agent's current, live access — not a static
export. They're a different mechanism from the `agent.json` /
`connectors.json` / skills / subagents that a recipe deploys: a Live Report is
installed into an existing agent via its `SaveReport` tool, using a portable
`*.livereport.json` bundle.

The example in this folder (`example/arc-fleet-operations.livereport.json`) is
one report — Arc fleet inventory, telemetry coverage, resource pressure,
failures, agent versions, and priority actions — taken from a public, working
demo: [wjpigott/azure-arc-sre-agent-demo](https://github.com/wjpigott/azure-arc-sre-agent-demo/tree/main/livereports).
That demo repo has two more reports (a priority/lifecycle view and a
third-party-software update tracker) plus the full tool used to build and
verify them.

## Why it might be worth including here

The `azmon-lawappinsights` and similar recipes already wire up a Log Analytics
workspace and read access for investigation. A Live Report built against that
same data gives whoever deploys the recipe an immediate, visual payoff —
something to open and look at — on top of the chat-based investigation flow
recipes already provide.

## This bundle is already environment-generic

- Subscription ID, workspace ID, and resource group are `__PLACEHOLDER__`
  tokens filled in at install time (`live_report_bundle.py prepare --set ...`),
  not baked into the file.
- There is no embedded machine inventory, demo hostnames, or fallback data.
  The report queries current Azure resources and current Log Analytics data
  every time it's opened.
- The source repository's CI
  ([`validate-live-reports.yml`](https://github.com/wjpigott/azure-arc-sre-agent-demo/blob/main/.github/workflows/validate-live-reports.yml))
  fails the build if a bundle contains a real GUID, a demo hostname pattern, or
  is missing a required placeholder — this isn't just a one-time manual
  redaction pass.
- `example/live_report_bundle.py verify` and `example/live_report_bundle.py
  bindings` can be run locally against the included bundle to confirm this
  independently.

## What's included here

| Path | Purpose |
|---|---|
| `example/arc-fleet-operations.livereport.json` | One installable report bundle, placeholder-only. |
| `example/live_report_bundle.py` | Standalone install/verify/export tool (Python 3.8+, standard library only). |
| `example/livereport-bundle.schema.json` | JSON Schema for the bundle format (`sreagent.livereport/v1`). |

## What's deliberately not included here

This is **not** shaped as a recipe per
[CONTRIBUTING.md](../../sreagent-templates/CONTRIBUTING.md): there's no
`agent.json`, `connectors.json`, `expected-config.json`, skill, or subagent
here, and it hasn't been run through `verify-agent.sh` or the three deploy
backends. That's intentional — turning this into a full recipe only makes
sense once there's agreement on where a Live Report belongs in the recipe
lifecycle (as its own install step after `deploy.sh`, as a documented example
living under a recipe's `config/`, or as something else).

## Open questions for maintainers

1. Should Live Reports become a new first-class category under
   `sreagent-templates/` (e.g. `sreagent-templates/live-reports/`), with its
   own lightweight contribution bar — separate from the full recipe checklist,
   since it doesn't deploy infrastructure or agent config?
2. If so, should installation be a manual post-`deploy.sh` step (as in the
   source demo repo today), or should `new-agent.sh` / `deploy.sh` learn to
   install a referenced bundle automatically?
3. Is `wjpigott/azure-arc-sre-agent-demo/livereports` an acceptable source to
   point to directly (with attribution), or should accepted reports be
   vendored into this repository?

Feedback welcome on the tracking issue before any of this becomes a pull
request.
