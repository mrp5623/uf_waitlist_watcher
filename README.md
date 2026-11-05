# UF Waitlist Watcher

Checks UF's public Schedule of Courses every 15 minutes and pushes an alert to your phone
when a spot opens on a course's waitlist. It never logs in and never joins anything; you join yourself in ONE.UF.

## How you'll know it's broken

| What goes wrong | How you find out |
|---|---|
| UF changes its data, wrong course/term, network error | Immediate **"Watcher is BROKEN"** push, plus a red X in GitHub Actions |
| Push notifications themselves stop working | The daily **"Watcher OK"** push (around 8 a.m.) stops showing up |
| The script stops running entirely (GitHub disables it, a typo in settings, etc.) | healthchecks.io emails you after ~1 hour with no successful check |

## Setup (about 10 minutes)

**1. Phone alerts (ntfy).** Install the free **ntfy** app (iOS/Android). Subscribe to a topic with a long random name,
e.g. `uf-waitlist-k3v9q2xp7m`. Anyone who knows the name can read or send to it, so treat it like a password.

**2. Dead-man's switch (healthchecks.io).** Make a free account, add a check, set **Period: 15 minutes, Grace: 45 minutes**.
Copy its ping URL (`https://hc-ping.com/...`). It emails you by default when pings stop.

**3. GitHub repo.** Create a new **public** repo and add `watch_waitlist.py` and `.github/workflows/waitlist.yml`.
Public keeps Actions free: a private repo gets 2,000 minutes/month, and 96 runs/day billed at 1 minute each is ~2,900.
Your topic and ping URL go in as secrets, which stay hidden even in a public repo.

**4. Settings.** Repo → Settings → Secrets and variables → Actions:

- Variables tab: `UF_TERM` = `2271` (Spring 2027), `COURSE_CODES` = comma-separated, e.g. `COP4600,PHI3681`.
  Every section of each course is watched by default.
  Optional `CLASS_NUMBERS` = comma-separated class numbers: for any course with a number listed, only those
  sections are watched (use it to drop sections that clash with your schedule). A number that matches nothing
  triggers a "BROKEN" alert, so typos don't silently narrow what you're watching.
- Secrets tab: `NTFY_TOPIC` = your topic name, `HEALTHCHECK_URL` = your ping URL.

**5. Test it.** Actions tab → *Waitlist watcher* → **Run workflow**. Within a minute or two you should get either
"Watcher OK: …" (working) or "Watcher is BROKEN" with the reason. healthchecks.io should show a green ping.

**6. When you're on the waitlist,** turn it off: Actions → *Waitlist watcher* → ⋯ → **Disable workflow**,
and pause the check on healthchecks.io so it doesn't email you.

## Notes

- The "spot open" push repeats every 15 minutes until the waitlist is full again, so it keeps nagging until you act.
- GitHub sometimes starts scheduled runs a few minutes late. Fine for this; don't expect exact 15-minute spacing.
- The data source is UF's public course-search backend, which is unofficial and can change without notice.
  That's exactly what the "BROKEN" alert is for.
- Sections UF labels "Departmentally Controlled" are skipped until the label goes away (they can't be joined
  and their empty waitlists are meaningless). Once released, they're watched like any other section.
- If one course fails to load, the others are still checked and still alert; you get both pushes.
- Test locally: `COURSE_CODES=COP4600,PHI3681 NTFY_TOPIC=your-topic MODE=status python3 watch_waitlist.py`