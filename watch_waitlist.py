#!/usr/bin/env python3
"""
Watch UF course waitlists and send a Telegram alert when a spot opens in any watched section.

Reads UF's public Schedule of Courses (no login, no GatorLink, no Duo).
It never registers or joins anything for you; it only tells you to go do it.

Three ways you find out if it breaks:
  1. Any error  -> "Watcher is BROKEN" message + the GitHub run fails.
  2. Every morning -> a "still full, still watching" message. If it doesn't show up, something is wrong.
  3. healthchecks.io emails you if no successful check has arrived in ~an hour,
     which catches the case where the script stops running entirely.
"""
import json                    # parse UF's JSON response
import os                      # read settings from environment variables
import sys                     # exit with a failure code on errors
import time                    # timestamps for "when did I last alert about this section"
import urllib.error            # catch HTTP errors to report Telegram's reason
import urllib.parse            # build the query string and form data safely
import urllib.request          # make HTTP requests (no pip installs needed)

SOC_URL = "https://one.ufl.edu/apix/soc/schedule/"   # UF's public course-search backend
USER_AGENT = "personal-waitlist-watcher (a few requests per 15 min)"  # be identifiable and polite


def setting(name, default=None):
    """Read one setting from the environment (an empty string counts as "not set")."""
    return os.environ.get(name) or default


def split_list(text):
    """Turn "COP 4600, PHI3681" into ["COP4600", "PHI3681"] (spaces removed, uppercased, blanks dropped)."""
    return [item.replace(" ", "").upper() for item in (text or "").split(",") if item.strip()]


# ---- Settings (GitHub repo variables/secrets, or exported locally) ----
UF_TERM = setting("UF_TERM", "2271")                 # term code: 2 + YY + (1 Spring, 5 Summer, 8 Fall); 2271 = Spring 2027
COURSES = split_list(setting("COURSE_CODES"))        # comma-separated, e.g. "COP4600,PHI3681"; checked in main()
# Optional, comma-separated class numbers, e.g. "19002,~26355".
#   12345  = watch only the listed sections of that course (other courses are unaffected)
#   ~12345 = never watch this section (e.g. a time that clashes with your schedule)
_NUMBERS = split_list(setting("CLASS_NUMBERS"))
ONLY_SECTIONS = {n for n in _NUMBERS if not n.startswith("~")}
EXCLUDED = {n.lstrip("~") for n in _NUMBERS if n.startswith("~")}
ALL_FOUND = set()      # every class number UF returned for the watched courses, to catch typos in CLASS_NUMBERS
TELEGRAM_TOKEN = setting("TELEGRAM_BOT_TOKEN")       # from @BotFather; anyone with it can control the bot, so keep it secret
TELEGRAM_CHAT = setting("TELEGRAM_CHAT_ID")          # your chat with the bot; messages go only here
HEALTHCHECK_URL = setting("HEALTHCHECK_URL")         # optional healthchecks.io ping URL
MODE = setting("MODE", "check")                      # "check" = alert only on news; "status" = also send the daily OK
STATE_FILE = setting("STATE_FILE", "state.json")     # what was alerted last run; the workflow carries it between runs
REMIND_SECONDS = 2 * 60 * 60                         # repeat an unchanged opening every 2 hours, not every run


def load_state():
    """Return {class_number: {"seen": "11/12", "alerted_at": epoch}} from the last run, or {} on the first run."""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):                    # missing or corrupt file: start fresh (worst case, one extra alert)
        return {}


def save_state(state):
    """Write the state for the next run to pick up."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def get_json(url):
    """GET a URL and return the parsed JSON body."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:   # raises on network errors / HTTP 4xx-5xx
        return json.load(resp)


def notify(title, message):
    """Send a Telegram message from your bot to your chat."""
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT):
        raise RuntimeError("Telegram secrets not set")
    data = urllib.parse.urlencode({                          # Telegram takes ordinary form fields
        "chat_id": TELEGRAM_CHAT,                            # who receives it
        "text": f"{title}\n{message}",                       # plain text: first line acts as the title
    }).encode("utf-8")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        urllib.request.urlopen(url, data=data, timeout=30).close()   # passing data makes this a POST
    except urllib.error.HTTPError as exc:
        # Report Telegram's own reason (e.g. "chat not found") without printing the URL, which holds the token.
        raise RuntimeError(f"Telegram refused the message: {exc.code} {exc.read().decode(errors='replace')[:200]}")


def ping_healthcheck(suffix=""):
    """Tell healthchecks.io the run succeeded ("") or failed ("/fail"). Never let this crash the script."""
    if not HEALTHCHECK_URL:
        return
    try:
        urllib.request.urlopen(HEALTHCHECK_URL.rstrip("/") + suffix, timeout=15).close()
    except Exception as exc:
        print(f"(healthcheck ping failed: {exc})")


def is_held(section):
    """True for sections the department hasn't opened to self-registration yet.
    UF puts "Departmentally Controlled" in the section's display field instead of the course title."""
    return "departmentally controlled" in str(section.get("display", "")).lower()


def read_sections(course):
    """Return [(class_number, cap, total, held, joinable), ...] for the watched sections of one course, or raise."""
    query = urllib.parse.urlencode({
        "category": "CWSP",          # main campus / standard courses
        "term": UF_TERM,
        "course-code": course,       # UF treats this as a prefix, so we filter exactly below
        "last-control-number": 0,    # first page; one course's sections fit on it
    })
    data = get_json(f"{SOC_URL}?{query}")

    courses = data[0]["COURSES"]                                    # response is [ { "COURSES": [...], ... } ]
    matches = [c for c in courses if c.get("code", "").upper() == course]
    if not matches:
        # UF returns an empty list (not an error) for a wrong term or course code, so catch that here.
        raise RuntimeError(f"{course} not found for term {UF_TERM}. Wrong code/term, or UF changed the API.")

    sections = [s for c in matches for s in c.get("sections", [])]  # flatten sections across matches
    if not sections:
        raise RuntimeError(f"{course} has no sections listed.")

    ALL_FOUND.update(str(s.get("classNumber")) for s in sections)  # before filtering, so excluded ones count as found

    picked = [s for s in sections if str(s.get("classNumber")) in ONLY_SECTIONS]
    if picked:
        sections = picked                                           # you named specific sections of this course
    sections = [s for s in sections if str(s.get("classNumber")) not in EXCLUDED]   # drop the ~ ones
    if not sections:
        raise RuntimeError(f"Every section of {course} is excluded by CLASS_NUMBERS. "
                           "Remove it from COURSE_CODES instead, or drop a ~ number.")

    results = []
    for s in sections:
        wl = s.get("waitList")
        if not isinstance(wl, dict) or "cap" not in wl or "total" not in wl:
            raise RuntimeError(f"{course} #{s.get('classNumber')}: no usable waitList data (got {wl!r}). "
                               "UF may have changed the API.")
        # UF's own "can someone join this waitlist now" flag. "N" = not joinable even if the count shows room
        # (e.g. a spot held while the next student decides). Missing flag: trust the count, as before.
        joinable = wl.get("isEligible") != "N"
        results.append((str(s.get("classNumber")), int(wl["cap"]), int(wl["total"]), is_held(s), joinable))
    return results


def main():
    errors = []        # problems to report as BROKEN
    news = []          # (class_number, "11/12", line) for every section with a joinable spot right now
    summary = []       # one line per course for the daily OK

    # Settings problems go through the same BROKEN path as everything else instead of exiting silently.
    if not COURSES:
        errors.append("No courses set: add a COURSE_CODES repo variable (Variables tab, not Secrets).")
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT):
        errors.append("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID secret is missing, so no alerts can be sent.")

    for course in COURSES:
        try:
            sections = read_sections(course)                 # one course failing doesn't hide the others
        except Exception as exc:
            errors.append(str(exc) if course in str(exc) else f"{course}: {exc}")  # always name the course
            continue
        news_before = len(news)                              # to tell whether this course added any news
        held_count = 0                                       # dept-controlled sections, reported in the daily OK
        not_joinable = []                                    # room in the count but UF says not joinable
        for number, cap, total, held, joinable in sections:
            print(f"{course} #{number}: waitlist {total}/{cap}" + ("  [departmentally controlled]" if held else ""))
            if held:
                # Not joinable yet, and its empty waitlist means nothing, so stay quiet. The moment UF drops
                # the "Departmentally Controlled" label, this branch stops matching and the checks below
                # treat it like any other section, so an opening in it alerts you right away.
                held_count += 1
            elif cap == 0:
                # No waitlist capacity listed: either seats opened (waitlist goes away) or it was turned off.
                news.append((number, "0/0", f"{course} #{number}: waitlist shows 0 capacity (seats may be open). Check ONE.UF."))
            elif total < cap and not joinable:
                # The count dipped but UF won't let anyone new join, so no alert; it's noted in the daily OK.
                not_joinable.append(f"#{number} {total}/{cap}")
            elif total < cap:
                news.append((number, f"{total}/{cap}", f"{course} #{number}: {cap - total} waitlist spot(s) open ({total}/{cap})."))
        if len(news) == news_before:
            line = f"{course}: {len(sections) - held_count} section(s), all full"
            if held_count:
                line += f"; {held_count} departmentally controlled (not open yet)"
            if not_joinable:
                line += f"; {', '.join(not_joinable)} shows room but UF says not joinable"
            summary.append(line)

    missing = sorted((ONLY_SECTIONS | EXCLUDED) - ALL_FOUND)  # typos in either kind of entry
    if missing and not errors:                               # only meaningful if every course loaded
        errors.append(f"Class number(s) not found in any watched course: {', '.join(missing)}")

    # Decide which openings to (re)announce: new or changed ones right away, unchanged ones every 2 hours.
    # The daily status run always repeats all current openings.
    state, now = load_state(), time.time()
    due = [(n, seen, line) for n, seen, line in news
           if MODE == "status"
           or state.get(n, {}).get("seen") != seen
           or now - state.get(n, {}).get("alerted_at", 0) >= REMIND_SECONDS]
    for n, seen, _ in news:
        if not any(n == d[0] for d in due):
            print(f"#{n} {seen}: already alerted, next reminder within 2 hours")
    if due:
        try:
            notify("🚨 Waitlist spot open!", "\n".join(line for _, _, line in due) + "\nJoin in ONE.UF now.")
            for n, seen, _ in due:
                state[n] = {"seen": seen, "alerted_at": now}    # only recorded once the alert actually went out
        except Exception as exc:
            errors.append(f"Couldn't send the spot-open alert: {exc}")   # falls through to the BROKEN path
    open_now = {n for n, _, _ in news}
    if not errors or open_now:
        # Forget sections that filled back up, so a later reopening alerts immediately. Skipped when a course
        # failed to load and nothing is open, so a UF hiccup can't wipe the record and cause a repeat alert.
        save_state({n: v for n, v in state.items() if n in open_now})

    if MODE == "status" and not news and not errors:
        # Layer 2: daily proof of life.
        try:
            notify("✅ Watcher OK", "\n".join(summary) + "\nChecking every 15 min.")
        except Exception as exc:
            errors.append(f"Couldn't send the daily OK: {exc}")

    if errors:
        # Layer 1: loud alert, then fail the run so GitHub shows a red X.
        print("ERROR: " + " | ".join(errors))
        try:
            notify("⚠️ Waitlist watcher is BROKEN", "\n".join(errors) + "\nCheck ONE.UF manually until it's fixed.")
        except Exception as notify_exc:
            # Telegram itself is down or misconfigured; the /fail ping below makes healthchecks.io email you.
            print(f"(couldn't send error alert either: {notify_exc})")
        ping_healthcheck("/fail")
        sys.exit(1)

    ping_healthcheck()   # Layer 3: "I ran and it worked"


if __name__ == "__main__":
    main()