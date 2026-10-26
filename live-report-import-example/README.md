# Proposal: Live Reports as a sharable Azure SRE Agent recipe asset

**Status: draft proposal, not a finished recipe, and not a pull request yet.**
It accompanies a GitHub issue asking the maintainers where (or whether) this
kind of asset fits in `sreagent-templates/`, since the existing
[Recipe Structure](../sreagent-templates/CONTRIBUTING.md#recipe-structure)
has no category for it today.

## What this is

Azure SRE Agent **Live Reports** are interactive HTML dashboards that run
inside the agent and call the agent's own read-only connector tools at view
time (here, a Log Analytics query tool), so the numbers on screen reflect the
agent's current, live access — not a static export. That's a different
mechanism from the `agent.json` / `connectors.json` / skills / subagents a
recipe deploys: a Live Report is installed into an *already-deployed* agent
via its `SaveReport` tool, using a portable `*.livereport.json` bundle.

This folder is a complete, self-contained copy of one full example — not a
link out to another repository. It includes the report bundle, its editable
HTML/KQL/test source, and the standalone tool used to build, verify, and
install it, so everything needed to actually try a Live Report publish is
here.

## What's included

| Path | Purpose |
|---|---|
| `reports/third-party-software-update-tracker.livereport.json` | Installable report bundle — third-party Windows application versions from Change Tracking, compared against an embedded vendor-version reference. No embedded machine data; works against any workspace. |
| `live_report_bundle.py` | Standalone install/verify/export/clean tool (Python 3.8+, standard library only). |
| `livereport-bundle.schema.json` | JSON Schema for the bundle format (`sreagent.livereport/v1`). |
| `templates/third-party-software-tracker/` | Editable source: the report HTML, its KQL queries, and offline regression tests (Node 18+, no dependencies). |

This is the richest of the three reports in the source demo (the other two are
an Arc fleet-inventory view and a priority/lifecycle view built from a shared
template) and is explicitly the one its own documentation recommends for a new
environment, since it has no built-in/fallback data of any kind.

## This bundle is already environment-generic

- Subscription ID, workspace ID, and resource group are `__PLACEHOLDER__`
  tokens filled in at install time (`live_report_bundle.py prepare --set ...`),
  not baked into the file. Confirmed locally:
  ```
  $ python3 live_report_bundle.py verify reports/third-party-software-update-tracker.livereport.json
  PASS  reports/third-party-software-update-tracker.livereport.json  [Third-Party Software Update Tracker]

  $ python3 live_report_bundle.py bindings reports/third-party-software-update-tracker.livereport.json
     resourceGroup    __RESOURCE_GROUP__   x1   clean placeholder
     subscription     __SUBSCRIPTION_ID__  x1   clean placeholder
     workspace        __WORKSPACE__        x1   clean placeholder
  ```
- There is no embedded machine inventory or fallback snapshot. The report
  queries live Change Tracking / software-inventory data every time it's
  opened, and explicitly shows an unavailable state rather than a cached demo
  result when a query fails.
- The template's own offline test suite (`node tests/run-tests.js`, 30+
  checks) passes unchanged in this location, confirming the static HTML
  checks, response-shape parsing, version-classification logic, and
  failure-path handling all still hold after moving the files here.

## Prerequisites

1. **An Azure SRE Agent** with the **SRE Agent Administrator** or
   **Standard User** role.
2. **A Log Analytics workspace** receiving `ConfigurationData` from Change
   Tracking & Inventory, with the agent's managed identity granted
   **Log Analytics Reader** (or Reader) on the workspace or its resource
   group.
3. **The report-callable query tool.** The agent must expose
   `system-mcp-monitor_monitor_workspace_log_query` to Live Reports — confirm
   this is report-callable (via `ListConnectorTools`), not just available in
   chat, before installing.
4. **These values from your environment** (run
   `python3 live_report_bundle.py bindings reports/*.livereport.json` to see
   exactly which ones a bundle needs):

   | Value | Where to find it | `--set` name |
   |---|---|---|
   | Subscription ID | Workspace > Overview > Subscription ID | `subscription` |
   | Workspace ID (customer ID GUID) | Workspace > Overview > Workspace ID | `workspace` |
   | Resource group of the workspace | Workspace > Overview > Resource group | `resourceGroup` |

## Install — Option A: in chat (simplest)

1. Open the SRE Agent and start a new chat.
2. Attach `live_report_bundle.py` and
   `reports/third-party-software-update-tracker.livereport.json`.
3. Send, filling in your values:

   > Run `python3 live_report_bundle.py prepare third-party-software-update-tracker.livereport.json
   > --set subscription=<SUBSCRIPTION_ID> --set workspace=<WORKSPACE_ID> --set resourceGroup=<RESOURCE_GROUP>
   > --out-dir tmp/livereport-install`.
   > If it reports FAIL, stop and show me the output. Otherwise, for each entry in
   > tmp/livereport-install/install-plan.json call SaveReport with htmlPath, name, description and allowedTools
   > exactly as listed. Do not edit or regenerate the HTML.

4. Approve the one-time **tool-access** prompt.
5. Open the report from **Live Reports**.

`prepare` refuses to continue while any `__PLACEHOLDER__` is unset, so a
report can't be installed half-configured.

## Install — Option B: scripted, from your workstation

Use this once this repository (or your fork of it) is connected to the target
agent as a code repository, so the agent sees it under `codeRefs/<repo>/`.
Requires the Azure CLI and `az login`.

```bash
python3 live_report_bundle.py install \
  --agent-id /subscriptions/<agent-sub>/resourceGroups/<agent-rg>/providers/Microsoft.App/agents/<agent-name> \
  --repo-path codeRefs/<repo>/live-report-import-example/reports \
  --script-path codeRefs/<repo>/live-report-import-example/live_report_bundle.py \
  --bundle third-party-software-update-tracker.livereport.json \
  --set subscription=<SUBSCRIPTION_ID> --set workspace=<WORKSPACE_ID> --set resourceGroup=<RESOURCE_GROUP>
```

- Add `--dry-run` first to see the exact API calls and the message sent to the
  agent, without making any calls.
- This calls the documented SRE Agent data-plane API: it starts a thread,
  polls it, and lists pending approvals (token audience `https://azuresre.dev`).
  Tool-access approvals are **not** automated — approve them in the portal on
  the thread the script prints.

## Install — Option C: prepare locally, then attach the HTML

```bash
python3 live_report_bundle.py prepare reports/third-party-software-update-tracker.livereport.json \
  --set subscription=<SUBSCRIPTION_ID> --set workspace=<WORKSPACE_ID> --set resourceGroup=<RESOURCE_GROUP> \
  --out-dir out/
```

Attach `out/<name>.html` in chat and ask the agent to save it as a Live
Report, using the name, description, and `allowedTools` from
`out/install-plan.json`, and telling it not to modify the HTML. **The
prepared HTML contains your IDs — do not commit `out/`.**

## Updating a report that's already installed

Pass the existing report ID so a new **version** is saved instead of a second
report: ask the agent to "list my live reports" to get the ID, then add
`--report-id <id>` to the `prepare` (or `install`) command above.

## Verify bundles before installing or merging

```bash
python3 live_report_bundle.py verify "reports/*.livereport.json"     # checksum, CSP nonce, allowed tools, SRI
python3 live_report_bundle.py bindings "reports/*.livereport.json"   # values to --set at install
```

`verify` fails if the HTML was changed after export (checksum mismatch), if it
looks like a downloaded/rendered copy (missing `{REPORT_NONCE}`), or if it
calls a tool that isn't in its `allowedTools`.

## After installing — check it works

- The report opens without a red error box.
- The header shows **Data: LIVE**. *Data: UNAVAILABLE* means the query failed
  or inventory is unavailable — no demo snapshot should ever appear instead.
- If inventory hasn't populated yet, that's a valid "deployed, data validation
  pending" state; an empty result must not be reported as proof of no
  installed software.

## Adding your own reports this same way

Do these steps inside your own SRE Agent workspace:

1. Ask the agent to check out each report: *"Run ListReports and GetReport for
   my reports, and write their metadata to reports-meta.json."*
2. Export them: `python3 live_report_bundle.py export --meta reports-meta.json --out bundles/`
   (bundles here contain real values — keep this directory out of source
   control).
3. Write a local redaction map (`original => replacement` per line) for any
   machine names, resource groups, domains, or other values that appear in
   text.
4. Clean the bundles:
   `python3 live_report_bundle.py clean "bundles/*.livereport.json" --map redaction-map.local.txt --out reports/`.
   This turns bound values into `__PLACEHOLDER__`s, replaces mapped text, and
   fails if any original value survives or the JavaScript no longer parses.
5. Review the diff and re-run `verify`.

Sanitizing identifiers isn't enough on its own to make a report portable:
pseudonymized machine names are still sample data. Prefer scoped live queries
and an explicit unavailable state over any embedded snapshot, as this example
does.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `FAIL ... required values not set` | Add the missing `--set name=value`. Run `bindings` to see which values the bundle needs. |
| `htmlSha256 mismatch` | The bundle was edited by hand. Re-export it, or restore it from git. |
| `No {REPORT_NONCE} placeholder found` | You exported a downloaded or rendered copy. Export from `GetReport`'s `htmlPath` instead. |
| Report shows "Query error" or "Failed to load" | Check the workspace ID and the agent's Log Analytics Reader role, and run the query directly in Log Analytics. |
| Tracker shows few machines | Run `templates/third-party-software-tracker/queries/inventory-coverage.kql` and check the Change Tracking DCR and AMA. |
| Repository path is missing under `codeRefs` | Connect the repository inside the SRE Agent itself (not just your editor); verify authentication and clone/index readiness. |
| `install` returns 401 | The token must have audience `https://azuresre.dev`, and you need thread-write access on the agent. |
| Everything in the tracker is "outdated" | The embedded reference versions are stale — see `templates/third-party-software-tracker/README.md` for how to refresh them. |

## What's deliberately not included here

This is **not** shaped as a recipe per
[CONTRIBUTING.md](../sreagent-templates/CONTRIBUTING.md): there's no
`agent.json`, `connectors.json`, `expected-config.json`, skill, or subagent
here, and it hasn't been run through `verify-agent.sh` or the three deploy
backends. That's intentional — turning this into a full recipe only makes
sense once there's agreement on where a Live Report belongs in the recipe
lifecycle.

## Open questions for maintainers

1. Should Live Reports become a new first-class category under
   `sreagent-templates/` (e.g. `sreagent-templates/live-reports/`), with its
   own lightweight contribution bar — separate from the full recipe checklist,
   since it doesn't deploy infrastructure or agent config?
2. If so, should installation be a manual post-`deploy.sh` step (as shown
   above), or should `new-agent.sh` / `deploy.sh` learn to install a
   referenced bundle automatically?
3. This example is one of three reports originally developed in
   [wjpigott/azure-arc-sre-agent-demo](https://github.com/wjpigott/azure-arc-sre-agent-demo);
   would the other two (an Arc fleet-inventory view and a priority/lifecycle
   view) be worth adding here too, following the same pattern?

Feedback welcome on the tracking issue before any of this becomes a pull
request.
