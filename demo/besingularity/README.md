# BeSingularity incoming-call demo

Edit `besingularity-prompt.md` for the spoken script and
`besingularity-demo.json` for the fixed demo parameters. Keep the two consistent:
the static script intentionally contains literal names, venue and session times.
Training details are samples, not a confirmed real event.

From the ProofCall repository, apply changes to the **local** TeleExpert server:

```bash
.venv/bin/python demo/besingularity/apply-besingularity.py
```

The script reads the private tenant key from
`.local-demo/TeleExpert/.local-tenant.json`. It creates a versioned assistant,
validates its profile, assigns it to the tenant's default incoming gateway group,
and verifies the assignment. It never places a call. State is saved in the
ignored `.local-demo/besingularity-assistant.json`. No credentials belong here.

Keep the `OPENING — GREET IN AMHARIC, THEN OFFER LANGUAGE OPTIONS` and
`LANGUAGE CHOICE` headings: the opening section becomes the greeting. The selected
assistant uses an explicit initial Amharic language configuration so the caller's
requested language can take priority over automatic language detection.

Wait for “assigned and verified”, then make a new test call with USB connected.
An existing call retains its previous assistant version. No backend restart or
new enrollment is needed for prompt updates. The original local editing paths
remain symlinks to these tracked files.

This configures incoming calls for testing the script. The full ProofCall
outbound demo and results workflow is separate work.
