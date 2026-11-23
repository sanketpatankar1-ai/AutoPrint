# AutoPrint owner dashboard

AutoPrint runs a private owner workspace at **/owner** and an anonymous customer upload and checkout page at **/print**. A FastAPI service stores orders, payment events, staff sessions, audit records and retained documents in SQLite. A separate agent connects the service to the shop’s CUPS printers.

## Run locally

Use Python 3.11 or later. Run these commands from the project directory.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py owner@example.com
python -m uvicorn autoprint.app:app --host 127.0.0.1 --port 8000
```

Use your own email address in the provisioning command. It prompts privately for a password. Open [the owner workspace](http://127.0.0.1:8000/owner) to sign in. Open [the customer portal](http://127.0.0.1:8000/print) to start a temporary customer session. For the shop QR code, use the publicly reachable HTTPS URL ending in `/print`.

Create an administrator with `python manage.py operator@example.com --role admin`. Owners and administrators can monitor operations, export reports and operate printers. Only owners can update business settings and read the audit log. Running the provisioning command for an existing email changes its password and role and revokes its sessions.

## Connect the shop

For a beginner-friendly setup walkthrough, see the [Printer connection guide](docs/PRINTER_CONNECTION_GUIDE.md).

Set these variables in the server process environment before starting the service. The server loads an optional private `.env` next to this README on startup. Exported environment variables take priority. The source archive excludes this file.

| Variable                  | Purpose                                                              |
| ------------------------- | -------------------------------------------------------------------- |
| `PUBLIC_ORIGIN`           | Exact public HTTPS origin, for example `https://print.example.com`   |
| `AUTOPRINT_DATA`          | Persistent private directory for SQLite. Defaults to `./data`        |
| `RAZORPAY_KEY_ID`         | Checkout publishable key                                             |
| `RAZORPAY_KEY_SECRET`     | Server-side order creation credential                                |
| `RAZORPAY_WEBHOOK_SECRET` | Separate secret configured on the Razorpay webhook                   |
| `PRINT_AGENT_TOKEN`       | Random shared credential, at least 32 characters, for the shop agent |
| `PRINT_AGENT_ID`          | Stable shop agent name. Defaults to `shop-agent`                     |

Generate an agent credential locally with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Store the same value in the server and agent environments. Use one agent installation for this shop.

### Test checkout on localhost

Set `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` in the private `.env` using your test-mode credentials. Restart the server and open `/print`. Choose a one-page file and select **Upload & Pay**. The portal validates and stages the document privately, calculates its total, then opens Razorpay Checkout on the first **Upload & Pay** click. A customer can reopen the same pending checkout without uploading the document again. The order card includes a downloadable receipt after capture, an optional pickup QR that opens the authenticated owner order view, and an explicit new paid order for a retained-file reprint. An SVG printer animation appears after backend verification. Printer progress comes from the local CUPS agent and customer orders refresh every 10 seconds while the page is visible. Temporary document drafts expire after 30 minutes. If you close Checkout, use **Pay now** on the pending order to resume. With test keys the backend verifies the signed checkout response and fetches the order’s payments directly from Razorpay. It releases a job only for a captured, unrefunded payment matching the stored order, amount and INR currency. **Check payment** repeats the server lookup after a delayed capture or a page refresh. Local test checkout can run without a public webhook. Use a dedicated live database when switching to real payments.

Monitor **Live Printing Monitor** at `/owner`. A paid job stays queued until an enabled, online printer is assigned for its print type. For a printer on the same computer, run `python3 print_agent.py` in a second terminal with pycups installed. It can read the pairing token from the local `.env`. A remote printer computer needs only the agent token and server URL.

### Payments with webhooks

1. Configure a Razorpay account with captured payments enabled. Start with test keys, a test webhook and a separate test data directory.
2. Register the HTTPS endpoint `/api/webhooks/razorpay` for `payment.authorized`, `payment.captured`, `payment.failed`, `refund.created`, `refund.processed` and `refund.failed`. Set the same webhook secret on both sides.
3. Upload a document on the customer portal and review the server-calculated price. Checkout creates a Razorpay order for that amount. A matching, signed capture unlocks the printing queue. Live checkout receives payment state through the registered webhook.
4. Before switching to live credentials, verify a complete test checkout, webhook delivery and refund in the Razorpay dashboard. Use a separate live database to keep test payments out of business reports.

Webhook verification uses HMAC-SHA256 over the raw request body. Captures must match the stored gateway order, currency and amount. Duplicate deliveries are idempotent. Refunds arriving before capture are retained and applied when the payment arrives. Processed refunds put unprinted orders on hold.

[Razorpay webhook validation](https://razorpay.com/docs/webhooks/validate-test/) · [Razorpay payment events](https://razorpay.com/docs/webhooks/payments/)

### Printer computer

The supplied agent supports **macOS and Linux with CUPS**. Install the vendor’s CUPS driver or configure a compatible IPP printer, then confirm a local test page prints. Install Python and `pycups` on that computer. Building pycups can require a C compiler and the CUPS development headers.

```sh
python3 -m pip install pycups
export AUTOPRINT_URL="https://print.example.com"
# Supply PRINT_AGENT_TOKEN through the agent process environment.
python3 print_agent.py
```

The agent polls over outbound HTTPS and stores its submission journal under `./agent-data`. Set `AGENT_DATA` to a persistent private directory when running it as a service. The same host may use a loopback HTTP URL for local testing.

1. Open **Printer Management** after the first heartbeat. Assign the monochrome device using **Use for B&W** and the colour device using **Use for colour**.
2. Configure paper sizes and prices in **Settings**. Use a test document to check each device’s driver, orientation, media, colour and duplex support. An unsupported CUPS option can fall back to a driver default.
3. Pausing or disabling a printer holds new assignments. Jobs already submitted continue under spooler control. The agent reports completed work when CUPS reports a terminal successful job state. Individual devices differ in how closely spooler completion tracks physical output.
4. Review errors in **Printer Management**, **Notifications** and the order detail. After an uncertain submission, check the printed output before retrying. The agent persists submission intent before sending the document and reconciles it with the spooler after interruption.
5. Health reflects the latest CUPS report. Hardware paper alerts depend on the printer driver. After 45 seconds without a heartbeat, the service marks the agent and its printers offline.

## Business rules

- Amounts are integer paise, displayed in INR. Configure the initial prices before opening for business. Single-sided and double-sided rates are both per printed page. The order total is the greater of the minimum charge and pages × copies × the selected rate. Existing orders keep their quoted amount.
- Reports use India Standard Time. Gross revenue sums verified captures by event date. Refunds use their processed event date. Net revenue deducts those refunds. Average order value divides gross captures by distinct paid orders in the period. Failed attempts are recorded separately.
- A document is counted when its print job completes. Printed pages count document pages × copies, including duplex pages. Failed attempts remain in performance reports after a successful retry.
- The dashboard displays active customer sessions seen within five minutes. Anonymous customer sessions expire after an hour of inactivity or 24 hours total. A session cookie scopes the customer’s own order history.
- Customer settings refresh automatically while the portal is open. A live authenticated WebSocket updates the owner views. The notification sound switch is local to the browser and may require a user gesture after reopening.
- Document retention is in hours. Cleanup expires abandoned uploads and terminal jobs, and preserves files still queued or printing. Order metadata and financial records remain available after cleanup. The dashboard returns document names and order details. Only the authenticated print agent with the current job claim can retrieve a paid job’s document.
- The six report types support CSV and XLSX, with date ranges, business totals and detail rows. Transaction exports list individual refund events. Printer reports count outcomes in the selected period. Printing activity detail follows order creation dates.

## Hosting and verification

Deploy the web process on a persistent host with HTTPS termination and WebSocket forwarding. Keep SQLite and agent journals on durable local storage. Restrict the data directory, configure the reverse proxy’s request size limit to accommodate the selected maximum upload size, and back up SQLite using its backup API. Use a trusted proxy configuration for the real client IP so sign-in limits apply per client. Secure cookies require HTTPS outside loopback.

Run the isolated workflow tests from the project directory.

```sh
python -m pip install pytest httpx
python -m pytest -q
```

To run the customer interaction tests, use Node.js 22 or later and run `npm install --prefix tests/portal` followed by `npm test --prefix tests/portal`. These tests exercise the portal form and callbacks in a simulated DOM with a simulated Razorpay SDK, and the new authenticated draft, capture, preview, receipt, cancellation and pickup paths with SQLite TestClient fixtures.

Tests exercise anonymous access, staff roles, session isolation, rate limits, live WebSocket changes, signed events, duplicate delivery, refund ordering, idempotent job claims, retries, cleanup, disconnection, financial totals and downloadable spreadsheets. They create temporary databases and use a checkout adapter in place of a live gateway account. Real merchant capture and physical printer output require the shop integrations above.
