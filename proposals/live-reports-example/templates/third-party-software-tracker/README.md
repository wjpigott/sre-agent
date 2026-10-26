# Third-Party Software Update Tracker - editable template

This is the editable source of the third-party tracker. If you only want to install
it, use `reports/third-party-software-update-tracker-cleaned.livereport.json`
and follow the root [README](../../README.md).

| Path | Purpose |
|---|---|
| `report/third-party-software-tracker.html` | Report source. Its `CONFIG` block holds `<SUBSCRIPTION_ID>`, `<WORKSPACE_RESOURCE_GROUP>` and `<WORKSPACE_CUSTOMER_ID>`. |
| `queries/report-inventory.kql` | The exact query the report runs. A test keeps it in sync with the HTML. |
| `queries/third-party-software-status.kql` | Standalone status query for Log Analytics, workbooks or alerts. |
| `queries/inventory-coverage.kql` | Checks that Change Tracking is sending software inventory. |
| `tests/run-tests.js` | Offline tests (Node 18+, no dependencies): `node tests/run-tests.js` |

## Deploying the template directly
1. Copy `report/third-party-software-tracker.html` and fill in the `CONFIG` values. Do not commit that copy.
2. In your SRE Agent, attach the file and ask:
   > Save the attached file as a Live Report named "Third-Party Software Update Tracker" with allowedTools
   > ["system-mcp-monitor_monitor_workspace_log_query"]. Do not modify the HTML.
3. Approve the tool-access prompt, then open the report. The header should show **Data: LIVE**.

## Updating reference versions (monthly, or when the report shows a banner)
"Latest" versions live in `VERSION_DB`, `PY_LATEST` and `NODE_LATEST` (dated by `VERSION_DB_DATE`).
The report shows a banner when the reference is more than 30 days old, or when a machine has a newer
version than the reference.

To update:
1. Check the vendor sources: the Oracle Java 8 CPU, 7-zip.org, the Chrome Releases blog, Adobe Acrobat
   Continuous release notes, python.org and nodejs.org.
2. Update those constants and `VERSION_DB_DATE`.
3. Update the `Latest` values in `queries/third-party-software-status.kql`.
4. Run the tests.

For Chrome, `latestVer` is the lower Windows build and `refMax` the higher one (e.g. `.79` and `.80`).

The reference was last verified on 2026-10-15:
- Java 8u503 (next Oracle CPU 2026-10-20)
- 7-Zip 26.04
- Chrome 155.0.8059.79/.80
- Acrobat 26.002.22031
- Python 3.13.16 / 3.14.8
- Node.js 24.22.0 / 26.12.0
