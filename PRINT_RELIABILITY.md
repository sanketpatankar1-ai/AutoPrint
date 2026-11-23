# Print reliability

Server claims join orders to the owner-configured BW/Colour route. Customer mode is not a printer identifier. Claims require paid state, retained file and an enabled online available printer. Existing active assignments are redelivered using their durable claim IDs.

Agent records submission intent before calling CUPS, then records spool ID. After interruption it searches spool history for the unique order/claim title. Uncertain submissions become failed with an instruction to inspect output before retry. Exactly-once physical printing is not guaranteed. No new per-printer configurable concurrency feature was implemented in this initial pass.

CUPS completion is spooler-reported completion; physical delivery is driver-dependent. Windows spooler support and explicit REQUIRES_REVIEW state remain gaps. Never interpret a frontend animation as hardware confirmation.

Shop checklist: verify each device locally; map BW and Colour; test both modes, paper/duplex/orientation, offline reconnect, paper jam, power loss and agent crash around submission. Inspect physical output before any retry. These physical checks have not run here.


## Follow-up verification — 23 November 2026

Fixed in this pass: browser JSON requests now abort after 30 seconds; uploads after 120 seconds, including response-body parsing. Abort listeners/timers are cleaned up. Checkout script loading fails after 20 seconds and removes the failed script for retry. Expired active-draft cache is invalidated on submit error. Remaining document selectors stay visible for single remaining files; fresh documents reset orientation/layout/paper.

Executed results: backend pytest 19 passed, 24 warnings, 5.41s; browser simulated DOM/checkout suite 6 passed, 0 failed, 2.43s; customer.js and icons.js syntax checks passed. Existing browser tests do not yet cover the new timeout boundaries, so those fixes are implemented but not fully regression-verified.

Dependency scan: npm audit executed and returned high-severity happy-dom findings (development test dependency), including GHSA-834v-8h66-3h79 and GHSA-c8p4-j97h-3vg8; fixAvailable was false. Do not process untrusted HTML using this test runtime. Python pip_audit unavailable (module not installed). npm package lookup hit cache permission error; no system cache permissions were changed. Scans are not clean.

Highest unresolved risk: native Windows submission/reconciliation is absent. No Windows agent or startup service was implemented in this pass. Existing CUPS agent must not be advertised as Windows-compatible. A browser owner dashboard cannot itself deliver silent physical printing.

Status classification: existing functional workflows fixed and verified where covered by the executed tests; timeout/cache changes implemented with limited regression coverage; Windows printing unresolved; physical printer and provider integration verification requires external hardware/accounts. Load matrix 10/50/100/500/1000 remains unexecuted: no benchmark numbers or capacity claim. Database outage/restart and Windows crash drills were not added. Preview remains a visual approximation, not driver output. Production readiness remains NOT APPROVED.


## Phase 3 Windows adapter

Added windows_agent.py, requirements-windows.txt, docs/WINDOWS_SETUP.md and tests/test_windows_agent.py. Native GDI rendering uses pywin32 plus PDFium/Pillow; exact printer queue names and existing authenticated backend claims are preserved. Journal intent is committed before submission; duplicates never automatically resubmit. Supported advertised settings deliberately limited to A4/simplex/one copy/one-up. All submitted jobs remain Awaiting printer confirmation: spool-history reconciliation and owner resolution/test-print workflow are still unresolved. This is an experimental supervised adapter, not complete native production support. Windows ACL setup is manual; credentials remain existing agent-scoped bearer, not device-scoped. Hardware, dependency compatibility on Windows and startup task behavior unverified. No load benchmark performed. Production approval withheld.


## Phase 4 incremental lifecycle changes

Windows EnumJobs reconciliation now matches the exact backend-order/claim document title and checks persisted spool ID when available. Missing, ambiguous, errored or spooler-completed jobs require review; disappearance never proves physical success. Present jobs report SPOOLED or Printing. No automatic resubmission occurs after journal intent.

Owner-only POST /api/owner/orders/{id}/confirm-output requires current claim and confirmed=true, paid state, active assignment and review phase. Transaction serialization makes repeated same-claim confirmation idempotent; outcomes and audit record are committed together. Existing order detail exposes a physical-confirmation button; customer/admin callers cannot use the owner-only endpoint. Owner must inspect actual output, not only spooler state.

Still incomplete: dedicated review queue, timestamp/status metadata history, explicit failure/cancellation/controlled reprint workflow, authorized dedicated test jobs, per-device tokens, checksum transport, driver capability validation beyond restricted defaults, bounded exponential reconnect and Windows recovery drills. Do not clear journals or automatically retry uncertain jobs. No unattended production approval.


## Phase 5 partial follow-up

Implemented owner-only /api/owner/print-review pagination and per-order resolution history, and /api/owner/orders/{id}/resolve with transaction-serialized idempotency keys. Cancellation only affects unclaimed Queued jobs. Failed classification of uncertain jobs records an audit and error while preserving active ownership: it does not imply spooler cancellation or permit an automatic retry. Controlled new-attempt reprinting remains unimplemented. Windows reconnect now backs off exponentially with jitter capped around 61 seconds, resetting after success.

Warning investigation: all 24 warnings originate from installed openpyxl datetime.utcnow calls (18 packaging/core.py, 6 writer/excel.py). External deprecation warnings remain unsuppressed. No dependency upgrade or new vulnerability scan completed in this pass.

Still unresolved: review dashboard UI, full metadata/checksum journal, dedicated owner test jobs, controlled reprint attempts, journal corruption recovery tests, happy-dom vulnerabilities, Python dependency scan and Windows hardware validation. No production approval. API review functionality is partial and must not be described as a completed dashboard.
