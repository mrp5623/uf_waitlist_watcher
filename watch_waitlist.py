#!/usr/bin/env python3
"""
Watch UF course waitlists and push a phone alert when a spot opens in any watched section.

Reads UF's public Schedule of Courses (no login, no GatorLink, no Duo).
It never registers or joins anything for you; it only tells you to go do it.

Three ways you find out if it breaks:
  1. Any error  -> "Watcher is BROKEN" push to your phone + the GitHub run fails.
  2. Every morning -> a low-priority "still full, still watching" push.
     If that push doesn't show up, something is wrong.
  3. healthchecks.io (optional but recommended) emails you if the script
     hasn't reported a successful check in ~an hour, which catches the case
     where the script stops running entirely.
"""
import json                    # parse UF's JSON response
import os                      # read settings from environment variables
import sys                     # exit with a failure code on errors
import urllib.parse            # build the query string safely
import urllib.request          # make HTTP requests (no pip installs needed)

SOC_URL = "https://one.ufl.edu/apix/soc/schedule/"   # UF's public course-search backend
USER_AGENT = "personal-waitlist-watcher (a few requests per 15 min)"  # be identifiable and polite


def setting(name, default=None, required=False):
    """Read one setting from the environment; stop with a clear message if a required one is missing."""
    value = os.environ.get(name) or default          # empty string counts as "not set"
    if required and not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def split_list(text):
    """Turn "COP 4600, PHI3681" into ["COP4600", "PHI3681"] (spaces removed, uppercased, blanks dropped)."""
    return [item.replace(" ", "").upper() for item in (text or "").split(",") if item.strip()]


# ---- Settings (set these as GitHub repo variables/secrets, or export them locally) ----
UF_TERM = setting("UF_TERM", "2271")                 # term code: 2 + YY + (1 Spring, 5 Summer, 8 Fall); 2271 = Spring 2027
# One or more course codes, comma-separated. COURSE_CODE (singular) still works from the first version.
COURSES = split_list(setting("COURSE_CODES") or setting("COURSE_CODE", required=True))
# Optional, comma-separated class numbers. For any course that has a listed number, only those sections
# are watched (use this to skip sections that clash with your schedule). Courses with none listed: all sections.
ONLY_SECTIONS = set(split_list(setting("CLASS_NUMBERS") or setting("CLASS_NUMBER")))
NTFY_TOPIC = setting("NTFY_TOPIC", required=True)    # your private ntfy.sh topic name (treat it like a password)
HEALTHCHECK_URL = setting("HEALTHCHECK_URL")         # optional healthchecks.io ping URL
MODE = setting("MODE", "check")                      # "check" = alert only on news; "status" = also send the daily OK


def get_json(url):
    """GET a URL and return the parsed JSON body."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:   # raises on network errors / HTTP 4xx-5xx
        return json.load(resp)


def notify(title, message, priority="default", tags=""):
    """Send a push notification through ntfy.sh to the phone app subscribed to NTFY_TOPIC."""
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),                        # the body is the notification text
        headers={"Title": title, "Priority": priority, "Tags": tags},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=30).close()


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
    """Return [(class_number, cap, total, held), ...] for the watched sections of one course, or raise with a reason."""
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

    picked = [s for s in sections if str(s.get("classNumber")) in ONLY_SECTIONS]
    if picked:
        sections = picked                                           # you named specific sections of this course

    results = []
    for s in sections:
        wl = s.get("waitList")
        if not isinstance(wl, dict) or "cap" not in wl or "total" not in wl:
            raise RuntimeError(f"{course} #{s.get('classNumber')}: no usable waitList data (got {wl!r}). "
                               "UF may have changed the API.")
        results.append((str(s.get("classNumber")), int(wl["cap"]), int(wl["total"]), is_held(s)))
    return results


def main():
    errors = []        # problems to report as BROKEN
    news = []          # lines worth an urgent push
    summary = []       # one line per course for the daily OK
    seen = set()       # every class number we found, to catch typos in CLASS_NUMBERS

    for course in COURSES:
        try:
            sections = read_sections(course)                 # one course failing doesn't hide the others
        except Exception as exc:
            errors.append(str(exc) if course in str(exc) else f"{course}: {exc}")  # always name the course
            continue
        news_before = len(news)                              # to tell whether this course added any news
        held_count = 0                                       # dept-controlled sections, reported in the daily OK
        for number, cap, total, held in sections:
            seen.add(number)
            print(f"{course} #{number}: waitlist {total}/{cap}" + ("  [departmentally controlled]" if held else ""))
            if held:
                # Not joinable yet, and its empty waitlist means nothing, so stay quiet. The moment UF drops
                # the "Departmentally Controlled" label, this branch stops matching and the checks below
                # treat it like any other section, so an opening in it alerts you right away.
                held_count += 1
            elif cap == 0:
                # No waitlist capacity listed: either seats opened (waitlist goes away) or it was turned off.
                news.append(f"{course} #{number}: waitlist shows 0 capacity (seats may be open). Check ONE.UF.")
            elif total < cap:
                news.append(f"{course} #{number}: {cap - total} waitlist spot(s) open ({total}/{cap}).")
        if len(news) == news_before:
            line = f"{course}: {len(sections) - held_count} section(s), all full"
            if held_count:
                line += f"; {held_count} departmentally controlled (not open yet)"
            summary.append(line)

    missing = sorted(ONLY_SECTIONS - seen)
    if missing and not errors:                               # only meaningful if every course loaded
        errors.append(f"Class number(s) not found in any watched course: {', '.join(missing)}")

    if news:
        # The thing you care about. Repeats every run until those waitlists are full again.
        notify("Waitlist spot open!", "\n".join(news) + "\nJoin in ONE.UF now.",
               priority="urgent", tags="rotating_light")

    if errors:
        # Layer 1: loud alert, then fail the run so GitHub shows a red X.
        print("ERROR: " + " | ".join(errors))
        try:
            notify("Waitlist watcher is BROKEN", "\n".join(errors) + "\nCheck ONE.UF manually until it's fixed.",
                   priority="high", tags="warning")
        except Exception as notify_exc:
            print(f"(couldn't send error push either: {notify_exc})")
        ping_healthcheck("/fail")
        sys.exit(1)

    if MODE == "status" and not news:
        # Layer 2: daily proof of life.
        notify("Watcher OK", "\n".join(summary) + "\nChecking every 15 min.", priority="low", tags="white_check_mark")

    ping_healthcheck()   # Layer 3: "I ran and it worked"


if __name__ == "__main__":
    main()