# UF Waitlist Watcher

Checks UF's public Schedule of Courses every 15 minutes and sends a Telegram alert when a spot opens
on a watched course's waitlist. It never logs in and never joins anything; you join yourself in ONE.UF.

## How you'll know it's broken

| What goes wrong | How you find out |
|---|---|
| UF changes its data, wrong course/term, missing setting, network error | Immediate **"Watcher is BROKEN"** message, plus a red X in GitHub Actions |
| Telegram alerts stop working | The daily **"Watcher OK"** message stops showing up, and healthchecks.io emails you |
| Checks stop running entirely (cron-job.org down, token expired, etc.) | healthchecks.io emails you after ~1 hour with no successful check |

## Setup

**1. Telegram bot.** Message **@BotFather**, send `/newbot`, copy the token. Send your new bot any message,
then get your chat ID from `https://api.telegram.org/botYOUR_TOKEN/getUpdates` (`"chat":{"id":NUMBER`).

**2. healthchecks.io.** Add a check with **Period: 15 minutes, Grace: 45 minutes** and copy its ping URL.

**3. GitHub repo (public, so Actions is free).** Add `watch_waitlist.py` and `.github/workflows/waitlist.yaml`.
Then Settings → Secrets and variables → Actions:

- Variables: `UF_TERM` = `2271` (Spring 2027), `COURSE_CODES` = e.g. `COP4600,PHI3681`.
  Optional `CLASS_NUMBERS`: for any course with a number listed, only those sections are watched
  (to drop sections that clash with your schedule). A number that matches nothing triggers a "BROKEN" alert.
- Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `HEALTHCHECK_URL`.

**4. Trigger (cron-job.org).** GitHub's own scheduler proved unreliable, so cron-job.org starts each run.
Make a fine-grained GitHub token limited to this repo with *Actions: Read and write*, then two jobs:

- URL: `https://api.github.com/repos/OWNER/REPO/actions/workflows/waitlist.yaml/dispatches`, method **POST**
- Headers: `Authorization: Bearer TOKEN`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`
- Every 15 minutes, body `{"ref":"main","inputs":{"mode":"check"}}` (silent unless there's news)
- Daily at 8:00 a.m., body `{"ref":"main","inputs":{"mode":"status"}}` (sends "Watcher OK")

**5. Test.** Actions → *Waitlist watcher* → **Run workflow** should send "✅ Watcher OK".

**6. Done?** Disable both cron-job.org jobs and pause the healthchecks.io check.

## Notes

- The spot-open alert repeats every 15 minutes until that waitlist is full again.
- Sections labeled "Departmentally Controlled" are skipped until UF removes the label; then they're watched normally.
- If one course fails to load, the others are still checked; you get both the spot-open and BROKEN alerts.
- The data source is UF's unofficial public course-search backend and can change without notice.
  That's what the "BROKEN" alert is for.
- The GitHub token expires (60 days if you followed the setup); healthchecks.io will email you when it does.
- Test locally: `COURSE_CODES=PHI3681 TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... MODE=status python3 watch_waitlist.py`