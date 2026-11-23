# Connect a Printer to AutoPrint After Deployment

AutoPrint sends paid print jobs to a printer through a small **Print Agent**. The agent runs on the computer that can already print to your device. Once it is connected, the owner dashboard shows the printer name, connection status, paper warnings, current jobs, and errors.

```mermaid
flowchart LR
  A[Customer pays] --> B[AutoPrint server]
  B --> C[Print Agent]
  C --> D[USB or Wi-Fi printer]
  D --> E[Real print status]
```

## Before you begin

Make sure you have:

- A deployed AutoPrint website using an HTTPS address.
- A Mac or Linux computer that stays on while the shop is open.
- A USB or network printer supported by that computer.
- The printer’s driver installed, or an IPP-compatible printer added.
- Python 3.11 or newer on the printer computer.
- The owner login for `/owner`.
- A strong `PRINT_AGENT_TOKEN` of at least 32 characters.

AutoPrint’s supplied agent supports **macOS and Linux with CUPS**. Keep the computer and printer powered on. A Windows computer can still be used as the printer’s network host only when another supported Mac/Linux computer runs the agent and can reach that printer.

## 1. Connect a USB printer

1. Turn on the printer and connect its USB cable to the Mac or Linux computer.
2. Open the computer’s printer settings and add the printer when it appears.
3. Select the manufacturer’s driver when offered. If no driver is available, try the printer’s IPP or AirPrint option.
4. Print a local test page from the computer. Fix this local test first; AutoPrint cannot correct a driver or cable problem.
5. Leave the printer connected while the shop is accepting jobs.

## 2. Connect a Wi‑Fi or Ethernet printer

1. Connect the printer to the shop’s Wi‑Fi or wired network.
2. Print the printer’s network/configuration page, or find its IP address in the printer screen or router list.
3. On the Mac/Linux computer, open printer settings and choose **Add Printer**.
4. Select the discovered printer, or enter its IP address using IPP. Choose the correct driver and save it.
5. Print a local test page. If the printer cannot be found, place the computer and printer on the same network and check that guest-network isolation is disabled.

## 3. Install and start the Print Agent

Run these commands on the computer that can print locally:

```sh
python3 -m pip install pycups
export AUTOPRINT_URL="https://your-deployed-domain.example"
export PRINT_AGENT_TOKEN="the-same-token-configured-on-the-server"
python3 print_agent.py
```

The agent also reads `PRINT_AGENT_TOKEN` from the private `.env` when it is on the same computer. Never publish this token. Keep the terminal open while testing. For daily use, run the agent as a background service and set `AGENT_DATA` to a private, persistent folder.

The agent checks the printer about every three seconds over outbound HTTPS. The server marks it offline after about 45 seconds without a heartbeat.

## 4. Assign the printer in AutoPrint

1. Sign in to `https://your-deployed-domain.example/owner`.
2. Open **Printer Management** and wait for the first heartbeat.
3. Confirm the printer name and model. Its status should become online and available.
4. Choose **Use for B&W** for the black-and-white printer and **Use for colour** for the colour printer.
5. Open **Settings** and configure supported paper sizes, prices, single/double-sided pricing, and the maximum upload size.
6. Upload a small test document from `/print`. Complete a test-mode payment, then watch the job in **Live Printing Monitor**.

The job remains queued until the selected printer is online, enabled, available, and payment is verified. The agent reports queued, printing, completed, stopped, and failed states from the printer spooler.

## Helpful troubleshooting

| What you see | Try this |
|---|---|
| Printer does not appear | Print locally first, reconnect USB, or check the printer IP and same network. |
| Dashboard says “Printer offline” | Keep the agent running, check the computer’s internet connection, and wait for the next heartbeat. |
| Printer is online but unavailable | Clear a paper jam, add paper, cancel a stuck local job, and confirm the printer accepts jobs. |
| No B&W or colour route | Assign the device in Printer Management and confirm its mode matches the order. |
| Paper size or duplex is rejected | Install the correct driver and select only options reported by the printer. |
| A job looks stuck after a restart | Check the physical output and spooler history before retrying. This avoids duplicate prints. |
| Agent cannot start | Confirm Python, `pycups`, the HTTPS URL, and a matching token of at least 32 characters. |

If the problem continues, note the printer model, connection type, dashboard message, and failed-job time. Share them with your deployment manager. Also review **Printer Management**, **Notifications**, and the order detail.
