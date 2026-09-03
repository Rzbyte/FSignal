# Submission copy

## FSignal

**Find founder-announced accelerator companies before the official directory catches up — then tell GTM who deserves attention first and why.**

FSignal is a persistent Slack launch-intelligence monitor for early GTM outreach. It monitors the YC Directory, the configured Speedrun company directory, X, and public LinkedIn signals. Founder announcements are extracted and cross-checked against official directory snapshots.

X is monitored native-first: X's own recent search when the deployment has a paid plan, and publicly indexed X URLs when it does not, so the source reports either way. Which path answered is carried through `/health`, persisted on every signal, and rendered in the alert as `Source: X (indexed search)` — an indexed result is never presented as a native one.

A company newly listed in either official directory raises a **NEW YC COMPANY** / **NEW SPEEDRUN COMPANY** alert. More importantly, if a social announcement identifies a company and no official match exists, the bot raises an **EARLY SIGNAL** — and stores the receipt behind it: which snapshot was checked, how large it was, when it was taken, the batch scope, and every match method attempted. That line is rendered in Slack, so a reader can verify the claim on ycombinator.com in seconds. Everything rejected is recorded too, with a reason code, so precision is demonstrable rather than asserted.

Every signal has a persistent timeline. If the company later appears in an official directory, its lifecycle changes from **GHOST -> CONFIRMED**, Slack reports the measured early-detection lead time, and the timeline preserves when the signal was first detected, classified, alerted, and confirmed.

The service runs continuously, records source health, persists deduplication and Slack retry state in SQLite, and exposes Pond Protocol V1 actions for `scan_now`, `get_status`, `list_ghosts`, and `get_timeline`.

### Why it is useful

A normal directory watcher tells a GTM team what has already been published. FSignal is designed around the earlier moment: **the founder has publicly announced acceptance, but the directory has not caught up yet**. The intelligence layer then answers two practical questions: **how credible is this signal?** and **which early signal should I review first?**

### Evidence

The repository includes an offline rehearsal that replays **real captured** search payloads
through the unmodified production pipeline, plus a CI-enforced precision gate measured on
that corpus. Live Slack screenshots, source health, and Pond proof are collected separately
per `docs/EVIDENCE.md`; rehearsal output is never presented as live delivery.

### Future upgradability

The monitoring stack uses a source-adapter boundary: new discovery platforms feed normalized records into the existing extraction, intelligence, matching, persistent lifecycle, Slack delivery, and Pond layers. Product Hunt, Bluesky, Reddit, Hacker News, Threads, or approved native LinkedIn access can therefore be added without redesigning Ghost -> Confirmed behavior.
