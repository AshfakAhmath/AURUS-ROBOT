# AURUS MCP Quick Start Guide

> Connect GitHub Copilot to your AURUS rover in 3 steps.

---

## Every Time You Start

### Step 1 — Start MCP Server on Pi

Open a terminal on the Raspberry Pi and run:

```bash
cd ~/AURUS\ Ashfak
source .venv/bin/activate
python3 servers/aurus_mcp_server.py
```

Expected output:
```
[AURUS MCP] Starting server from /home/tt-11/AURUS Ashfak
[AURUS MCP] Hardware will initialize on first tool call.
```

> Leave this terminal running. Do NOT close it.

---

### Step 2 — Connect VS Code to Pi

1. Open **VS Code** on Windows
2. Press `Ctrl+Shift+P`
3. Type: `Remote-SSH: Connect to Host`
4. Select: `10.100.35.56` (your Pi IP)
5. Enter Pi password when asked
6. `File ? Open Folder ? /home/tt-11/AURUS Ashfak`

> ? Bottom-left corner shows: **SSH: 10.100.35.56**

---

### Step 3 — Activate Copilot Agent Mode

1. Press `Ctrl+Alt+I` to open Copilot Chat
2. Click **`Agent`** in the bottom of the chat panel
3. Click **?? Tools** ? check ? both `aurus` entries ? click **OK**
4. Start chatting!

---

## Example Commands

```
Read the AURUS sensors and tell me what you see
```
```
Move AURUS forward for 2 seconds if the path is clear
```
```
Make AURUS say "Hello, I am ready!"
```
```
Get the complete rover status
```
```
Make AURUS perform the wiggle animation
```
```
Remember that the lab door is on the right side
```

---

## Available Tools

| Tool | What it does |
|---|---|
| `read_sensors` | Read all 5 ultrasonic distances |
| `move_rover` | Move in any direction |
| `spin_rover` | Rotate in place |
| `stop_rover` | Emergency stop |
| `perform_animation` | wiggle / shiver / spin |
| `capture_image` | Take a photo |
| `speak_text` | Make AURUS speak |
| `remember_fact` | Save to memory |
| `recall_memories` | Read saved memories |
| `get_rover_status` | Full status report |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| MCP server crashes | `pip install "mcp>=1.0.0,<2.0.0"` then restart |
| Tools not showing in Copilot | `Ctrl+Shift+P` ? `Developer: Reload Window` |
| Can't connect to Pi | Check Pi is on same WiFi, run `hostname -I` on Pi |
| SSH asks for password every time | Set up SSH key (see below) |

---

## One-Time Setup — Skip Password Every Time

Run this **once** on Windows PowerShell to avoid typing Pi password on every connect:

```powershell
ssh-keygen -t rsa -b 4096
ssh-copy-id tt-11@10.100.35.56
```

After this, VS Code connects to Pi automatically with no password prompt.

---

## Files Reference

| File | Purpose |
|---|---|
| `servers/aurus_mcp_server.py` | The MCP server (run this on Pi) |
| `.vscode/mcp.json` | Tells VS Code/Copilot how to start MCP |
| `.mcp.json` | Root-level MCP config (backup) |
| `requirements.txt` | Python dependencies (includes `mcp<2.0.0`) |
