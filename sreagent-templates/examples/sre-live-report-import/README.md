# Clone Azure SRE Agent Live Reports

`livereportclone.py` copies saved Live Report bundles from a folder available
to the target agent into that target agent. The report bundles stay independent
of this utility.

## Requirements

- Python 3.8+ and Azure CLI, with `az login` completed.
- The target agent's repository connection must expose the source folder and
  this script under `codeRefs/<repo>/`.
- Your identity must be able to start a thread on the target agent. Any
  tool-access approval remains a manual action in the SRE Agent portal.

`--source` is the folder path as the **target agent** sees it. It is not a
local workstation path or a source-agent resource URI. The folder must contain
one or more `*.livereport.json` bundles directly.
By default, the clone utility is expected in the same connected repository;
use `--script-path` if it is in a different connected repository.

## Clone reports

Clone every bundle in a connected repository folder:

```bash
python3 livereportclone.py \
  --source codeRefs/<repo>/sreagent-templates/examples/SRELiveReportImport \
  --target /subscriptions/<subscription>/resourceGroups/<resource-group>/providers/Microsoft.App/agents/<agent-name> \
  --allReports
```

Clone reports by their exact names (CSV syntax supports quoted names):

```bash
python3 livereportclone.py \
  --source codeRefs/<repo>/sreagent-templates/examples/SRELiveReportImport \
  --target /subscriptions/<subscription>/resourceGroups/<resource-group>/providers/Microsoft.App/agents/<agent-name> \
  --reports "Third-Party Software Update Tracker"
```

Use `--set name=value` to replace required bundle bindings, and `--dry-run`
to inspect the target-agent instructions without making API calls. The script
does not automatically approve tool requests. It asks the target agent to
stop if a report with the same name already exists, rather than overwriting it.

The clone command uses the target agent's data-plane chat API to prepare the
bundles from its connected repository and call `SaveReport`. The utility does
not fetch reports from a source agent URI; use a folder of saved report bundles
for this workflow.
