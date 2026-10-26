# Third-Party Software Update Tracker

This example is one portable Azure SRE Agent Live Report bundle. It shows
third-party Windows software versions from Change Tracking inventory,
compared with an embedded vendor-version reference.

The bundle contains the saved report HTML and metadata. It has no embedded
machine inventory or fallback snapshot; data is queried live when the report
is opened. Subscription, workspace, and resource-group values are placeholders
that must be supplied when cloning.

To install it, download or receive the `.livereport.json` bundle and use the
clone utility:

```bash
python3 sreagent-templates/examples/sre-live-report-import/livereportclone.py import \
  --bundle ./Third-Party-Software-Update-Tracker-<hash>.livereport.json \
  --target /subscriptions/<subscription>/resourceGroups/<resource-group>/providers/Microsoft.App/agents/<agent-name> \
  --set "subscription=00000000-0000-0000-0000-000000000000" \
  --set "workspace=11111111-1111-1111-1111-111111111111" \
  --set "resourceGroup=your-workspace-resource-group"
```

Replace the example binding values and bundle path with values from your
environment and the shared export.

The target agent must already have the required connector and workspace
permissions. Review and approve any tool-access request in the agent portal.
