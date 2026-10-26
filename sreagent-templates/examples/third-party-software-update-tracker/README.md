# Third-Party Software Update Tracker

This example is one portable Azure SRE Agent Live Report bundle. It shows
third-party Windows software versions from Change Tracking inventory,
compared with an embedded vendor-version reference.

The bundle contains the saved report HTML and metadata. It has no embedded
machine inventory or fallback snapshot; data is queried live when the report
is opened. Subscription, workspace, and resource-group values are placeholders
that must be supplied when cloning.

To install it, connect this repository to the target SRE Agent and use the
clone utility:

```bash
python3 sreagent-templates/examples/sre-live-report-import/livereportclone.py \
  --source codeRefs/<repo>/sreagent-templates/examples/third-party-software-update-tracker \
  --target /subscriptions/<subscription>/resourceGroups/<resource-group>/providers/Microsoft.App/agents/<agent-name> \
  --reports "Third-Party Software Update Tracker" \
  --set "subscription=<SUBSCRIPTION_ID>" \
  --set "workspace=<WORKSPACE_ID>" \
  --set "resourceGroup=<WORKSPACE_RESOURCE_GROUP>"
```

The target agent must already have the required connector and workspace
permissions. Review and approve any tool-access request in the agent portal.
