# Record the demo without ProofCall

The working incoming-call demo does not require the ProofCall web app, scheduler,
outbound integration, or operator UI. Its versioned BeSingularity assistant is
already assigned in the isolated TeleExpert database. Do not rebuild, re-enroll,
or reapply a prompt just to record the current working demo.

1. Keep the current local TeleExpert container running and the USB cable
   connected. Open TeleExpert on the gateway phone. Check Device control and
   ARMED. Leave its server and permanent phone credential unchanged.
2. If USB was unplugged or the app restarted, run from ProofCall:

   ```bash
   cd .local-demo/TeleExpert
   ./demo-local usb
   ```

   If the container was stopped, run `./demo-local start` first. These commands
   use the existing images and data; they do not rebuild an APK or place a call.
3. Call the gateway SIM from the allowed test phone and let it answer. Choose a
   language, confirm you are Henok, say you are interested, and let it deliver
   the invitation. Test an interruption or a question if desired. End the call.
4. For a video, use your usual recorder with microphone capture and put the
   caller phone on speaker, so the conversation is audible. Record an actual
   phone conversation; a web dashboard is optional. Check the captured sound
   before recording the final take. Use a consenting test participant.

The backend also records raw caller and assistant WAV channels for completed
calls while `TELEEXPERT_RECORD_CALLS=1`. Retention is seven days in this setup.
From ProofCall, list completed sessions without displaying phone numbers:

```bash
.venv/bin/python demo/besingularity/export-demo-audio.py
```

Export the most recent completed call's two channels:

```bash
.venv/bin/python demo/besingularity/export-demo-audio.py --latest
```

Exports stay under ignored `.local-demo/demo-recordings/` with private file
permissions. They are not committed. `downlink.wav` is caller audio and
`uplink.wav` is generated assistant audio. These raw files do not preserve a
shared conversation timeline: do not overlay them at time zero and represent
that as a synchronized recording. Record the live speakerphone conversation for
an accurate combined demo, or use timeline-aware call-result artifacts when
that integration is verified.

Committing and creating a branch do not change the running container, installed
APK, private environment, credentials, assistant binding or recorded calls.
The Android source fixes are not installed; the currently working setup retains
the permanent-credential and USB-startup workarounds.
