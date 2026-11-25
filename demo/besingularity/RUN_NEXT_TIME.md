# Run the existing BeSingularity demo next time

These instructions are for the current laptop and already configured Android
phone. Keys, phone enrollment, Docker images, databases and the Python virtual
environment are already saved. Normal startup does not require cloning,
building, reinstalling, wireless pairing, tenant creation or enrollment.

## 1. Connect the gateway phone

- Connect the TECNO gateway phone with a data-capable USB cable.
- Enable **Developer options → USB debugging**. Approve **Allow USB debugging**
  if Android asks. Keep the phone unlocked during setup.
- Open the installed TeleExpert app. Keep its existing permanent credential.
  Its local server must remain `ws://127.0.0.1:8765/call`.
- Do not start Wireless debugging pairing. This demo uses USB for the laptop
  connection and for starting the privileged device-control service.

## 2. Start TeleExpert — terminal 1

```bash
cd /home/ephrame/Documents/iCog/iCog/ProofCall/.local-demo/TeleExpert
./demo-local start
./demo-local status
./demo-local usb
```

The start command runs the existing backend image in Docker. It returns to the
terminal; the backend keeps running. The image name contains `diagnostic`, but
the saved environment selects **Gemini Live**, not the fixed test greeting.

The USB command should print `USB_OK`, reverse mappings for both `8765` and
`8080`, and a privileged-service message. Wait a few seconds, then check the
phone: **Device control: Connected** and **ARMED**. Tap **Arm gateway** if it
is disarmed. “Connected to the backend” alone is not enough for call audio.

Non-UI call control was granted previously. If Android has reset it, restore it:

```bash
"$HOME/Android/Sdk/platform-tools/adb" -d shell cmd appops set --user 0 org.teleexpert.gateway MANAGE_ONGOING_CALLS allow
"$HOME/Android/Sdk/platform-tools/adb" -d shell cmd appops get --user 0 org.teleexpert.gateway MANAGE_ONGOING_CALLS
```

Expected: `MANAGE_ONGOING_CALLS: allow`. Approve any phone permission prompts in
the app. Do not uninstall it: that would discard enrollment and device settings.

Check the backend:

```bash
curl -fsS http://127.0.0.1:8080/healthz
```

Expected: `"ok": true`, `"gateway_connected": true`, `"gateway_count": 1`.

## 3. Start ProofCall — terminal 2

This is the separate outbound worktree/branch, not the original ProofCall
working directory:

```bash
cd /home/ephrame/Documents/iCog/iCog/ProofCall/.local-demo/ProofCall-outbound
.venv/bin/python scripts/run_recruitment_demo.py
```

Leave this terminal running. Expected address:
`http://127.0.0.1:8767`. The branch is `feat/proofcall-local-outbound-demo`.
The launcher reads the private `.env` and uses this worktree's own SQLite DB.
The `.venv` points to the existing original ProofCall virtual environment.

If it says the port is already in use, check
`curl -fsS http://127.0.0.1:8767/health`. If that returns `{"status":"ok"}`,
the dashboard is already running; do not start another copy.

A startup warning about the legacy local intelligence engine does not block
the recruitment flow: TeleExpert supplies its conversation and analysis.

## 4. Sign in and select the campaign

Open **http://127.0.0.1:8767/login** and sign in with the BeSingularity company
username and password. On first login, choose a new password before opening the
workspace. The initial credentials are saved locally in the private ignored file
`.company-credentials/besingularity.json`; after changing the password, use your
chosen password rather than that initial value.

Administrators manage companies at **http://127.0.0.1:8767/** using the token from
`/home/ephrame/Documents/iCog/iCog/ProofCall/.local-demo/ProofCall-outbound/.env.operator-token`
in **Administrator token**. The source of
truth is `PROOFCALL_RECRUITMENT_OPERATOR_TOKEN` in this worktree's `.env`.
This is not the Gemini API key, TeleExpert API key or phone gateway credential.

Select **BeSingularity — Henok demo**, **BeSingularity — Bezawit demo**, or
**BeSingularity — Standard recruitment**. The standard campaign uses the
selected contact and configured training variables. Redundant campaigns are
available through **Show archived campaigns**. Campaigns, contacts and call history survive normal restarts.
Do not create a new campaign each time. A prepared assistant stays prepared
until its campaign changes.

If the sample campaign does not exist, run in another terminal from this worktree:

```bash
.venv/bin/python scripts/prepare_outbound_demo.py
```

This creates/prepares the named campaign without calling anyone or overwriting
existing campaign edits. Re-select it in the dashboard afterward.
It requires an already provisioned BeSingularity workspace; see
`../../COMPANY_WORKSPACES.md` for initial onboarding and other company logins.

## 5. Review the invitation and voice

Review the prompt, opening, Henok's name, sample venue/date and Ethiopian session
times. Sample training details are not a confirmed real event.

Current voices: Kore and Aoede (Female); Puck, Charon and Fenrir (Male).
The default is Kore. Use **Check phone connection** to refresh the enabled list.

After changing any campaign setting, including its voice:

1. **Save campaign**.
2. **Prepare assistant**.
3. Confirm the page says the current revision is prepared.

Changing a saved assistant does not change an existing active call. The next call
uses its newly prepared version. No TeleExpert restart or new enrollment is
needed for campaign/prompt/voice-selection edits.

For outbound prompt edits, use the dashboard. The outbound seed files are
`demo/besingularity/outbound-prompt.md` and `outbound-campaign.json`; rerunning
the preparation script does not overwrite an already saved campaign.

## 6. Place one test call

1. The connection message must say **Phone connected and idle**.
2. Select an existing permitted test contact, or add one with the second phone's
   full international number (`+251…`), truthful contact source and permission.
   Do not enter the gateway phone's own SIM number.
3. Select the contact's radio button below the contact form.
4. Choose **Review and place one call**, verify the confirmation and accept it.
5. The USB-connected gateway phone dials the second phone. Answer on the second
   phone, select a language, and speak with the agent. Keep USB connected.
6. End the call normally. The dashboard may show `processing` while results are
   prepared. A completed conversation is separate from verified recruitment
   analysis or attendance agreement.

Calls are never started by launching the apps or preparing an assistant. There
is no automatic redial. Repeat calls require another explicit confirmation and
a fresh request ID. A provider-confirmed ended call releases its contact lock,
even when its result still needs review. Active/uncertain calls remain blocked.

## 7. Troubleshooting

| Symptom | Action |
| --- | --- |
| Button disabled | Read the reason below it. Check phone readiness, select the contact, and save/prepare any unsaved campaign changes. |
| Phone offline | Check USB authorization, open TeleExpert, rerun `./demo-local usb`, and check ARMED. Then use **Check phone connection**. |
| Wireless debugging unavailable | The installed APK still tries its original wireless startup. Rerun the USB command to start device control; this does not mean the backend reverted. |
| USB unplugged/app restarted | Reconnect/open the app and rerun the USB command. Normal recovery does not require enrollment or pairing again. |
| No automatic answering in fallback | Check the allowlist, ARMED, Device control, phone permissions and `MANAGE_ONGOING_CALLS`. Do not manually answer that diagnostic/fallback call. |
| Call completed, analysis unavailable | The conversation can be saved while extraction fails. **Retry analysis (no call)** reprocesses the saved transcript through Google Gemini without dialing again. |

Inspect backend logs without changing the running service:

```bash
cd /home/ephrame/Documents/iCog/iCog/ProofCall/.local-demo/TeleExpert
./demo-local logs
```

## 8. Independent recording fallback

If the ProofCall dashboard fails, the existing incoming demo still works
independently. With TeleExpert ARMED and USB connected, call the gateway SIM
from a number in its **Allowed numbers** list and let it answer automatically.
Record the actual conversation with your usual recorder if making a video.

See `FALLBACK.md` for audio exports. Completed call recordings are kept locally
for seven days in this setup. Export them before that retention period expires.
Raw caller/assistant WAV files are separate channels, not a synchronized mix.

## 9. Stop safely

End/cancel any active call first. Press **Ctrl+C** in the ProofCall terminal.
If stopping the backend too:

```bash
cd /home/ephrame/Documents/iCog/iCog/ProofCall/.local-demo/TeleExpert
./demo-local stop
```

Normal stop/start preserves the databases and credentials. Avoid `down -v`,
Docker volume pruning, deleting `.env` files or uninstalling the APK. Those are
not routine startup/recovery steps.
