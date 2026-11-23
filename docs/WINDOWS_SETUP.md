# Windows agent — experimental, operator-supervised

This adapter is not approved for unattended production. Only A4, one copy, single-sided, one page per sheet are advertised. PDFs/images enter through the existing backend which converts accepted images to PDF and applies page selection. Native printer names are exact queue identifiers; renaming requires owner remapping. No fallback printer is used.

1. Install the manufacturer's Windows driver and print a Windows test page from each device.
2. Copy the project to a private local directory. Use a dedicated Windows user; restrict this directory and AGENT_DATA with Windows Security permissions to that user and administrators. chmod alone is not a Windows ACL.
3. In PowerShell, from the project directory:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-windows.txt
$env:AUTOPRINT_URL='https://YOUR-HOST'
$env:PRINT_AGENT_TOKEN='SAME-PRIVATE-SERVER-TOKEN'
$env:AGENT_DATA="$env:LOCALAPPDATA\AutoPrintAgent"
.\.venv\Scripts\python.exe windows_agent.py
```

Credentials are existing agent-only bearer credentials, not per-device scoped tokens. Do not place them in customer code or scheduled-task command arguments. Configure them securely for the Windows account. Local .env token fallback exists; AUTOPRINT_URL must be supplied in environment.

4. Open /owner → Printer Management. Assign Use for B&W and Use for colour to the exact queues. Customers choose only mode.
5. With operator supervision, submit an authorized paid test document; inspect physical output, orientation, grayscale, scale and selected pages.

Startup: use Windows Task Scheduler → Create Task under the dedicated account, trigger At log on, action the absolute .venv Python executable with argument windows_agent.py, Start in the project directory. Set restart on failure and Do not start a new instance. Logon startup is not a Windows service and does not run before logon. Test shutdown/restart and credential loading before daily use. This guide does not install or modify system tasks automatically.

Recovery: preserve windows-jobs.db. Once submission intent exists, duplicate delivery never submits again. Current implementation deliberately holds every submitted/uncertain job as Awaiting printer confirmation, and does not claim physical completion. Backend owner resolution for these held jobs and spool-ID reconciliation remain incomplete; do not delete journal or blindly retry. The affected printer remains occupied by its active claim.

Update: stop agent, preserve journal and credentials, copy updated source, install compatible requirements, restart with same journal. Never clear the journal to get past a stalled order.

Hardware checks pending: USB/network devices, paper-out/jam, driver option enforcement, interrupted GDI submission, spooler restart, laptop reboot, task restart, lost network, exact route isolation and actual payment verification. Mock tests do not verify hardware.


## Phase 4 incremental lifecycle changes

Windows EnumJobs reconciliation now matches the exact backend-order/claim document title and checks persisted spool ID when available. Missing, ambiguous, errored or spooler-completed jobs require review; disappearance never proves physical success. Present jobs report SPOOLED or Printing. No automatic resubmission occurs after journal intent.

Owner-only POST /api/owner/orders/{id}/confirm-output requires current claim and confirmed=true, paid state, active assignment and review phase. Transaction serialization makes repeated same-claim confirmation idempotent; outcomes and audit record are committed together. Existing order detail exposes a physical-confirmation button; customer/admin callers cannot use the owner-only endpoint. Owner must inspect actual output, not only spooler state.

Still incomplete: dedicated review queue, timestamp/status metadata history, explicit failure/cancellation/controlled reprint workflow, authorized dedicated test jobs, per-device tokens, checksum transport, driver capability validation beyond restricted defaults, bounded exponential reconnect and Windows recovery drills. Do not clear journals or automatically retry uncertain jobs. No unattended production approval.


Logs: current agent writes warnings to its process stderr; redirect to a restricted file when launching if needed. Rotation is not built in. Uninstall: stop scheduled task and agent first, remove the task you created, then archive journal securely. Keep the journal until all submitted jobs are reconciled. No automatic uninstaller is supplied.


## Phase 5 partial follow-up

Implemented owner-only /api/owner/print-review pagination and per-order resolution history, and /api/owner/orders/{id}/resolve with transaction-serialized idempotency keys. Cancellation only affects unclaimed Queued jobs. Failed classification of uncertain jobs records an audit and error while preserving active ownership: it does not imply spooler cancellation or permit an automatic retry. Controlled new-attempt reprinting remains unimplemented. Windows reconnect now backs off exponentially with jitter capped around 61 seconds, resetting after success.

Warning investigation: all 24 warnings originate from installed openpyxl datetime.utcnow calls (18 packaging/core.py, 6 writer/excel.py). External deprecation warnings remain unsuppressed. No dependency upgrade or new vulnerability scan completed in this pass.

Still unresolved: review dashboard UI, full metadata/checksum journal, dedicated owner test jobs, controlled reprint attempts, journal corruption recovery tests, happy-dom vulnerabilities, Python dependency scan and Windows hardware validation. No production approval. API review functionality is partial and must not be described as a completed dashboard.
