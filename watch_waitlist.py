#!/usr/bin/env python3
"""
Watch one UF course section's waitlist and push a phone alert when a spot opens.

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
USER_AGENT = "personal-waitlist-watcher (1 request per 15 min)"  # be identifiable and polite


def setting(name, default=None, required=False):
    """Read one setting from the environment; stop with a clear message if a required one is missing."""
    value = os.environ.get(name) or default          # empty string counts as "not set"
    if required and not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


# ---- Settings (set these as GitHub repo variables/secrets, or export them locally) ----
UF_TERM = setting("UF_TERM", "2271")                 # term code: 2 + YY + (1 Spring, 5 Summer, 8 Fall); 2271 = Spring 2027
COURSE = setting("COURSE_CODE", required=True).replace(" ", "").upper()  # e.g. "COP 4600" -> "COP4600"
CLASS_NUMBER = setting("CLASS_NUMBER")               # optional: the 5-digit class number, if the course lists more than one section
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


def read_waitlist():
    """Return (cap, total, class_number) for the watched section, or raise with a readable reason."""
    query = urllib.parse.urlencode({
        "category": "CWSP",          # main campus / standard courses
        "term": UF_TERM,
        "course-code": COURSE,       # UF treats this as a prefix, so we filter exactly below
        "last-control-number": 0,    # first (and only) page
    })
    data = get_json(f"{SOC_URL}?{query}")

    courses = data[0]["COURSES"]                                    # response is [ { "COURSES": [...], ... } ]
    matches = [c for c in courses if c.get("code", "").upper() == COURSE]
    if not matches:
        # UF returns an empty list (not an error) for a wrong term or course code, so catch that here.
        raise RuntimeError(f"{COURSE} not found for term {UF_TERM}. Wrong code/term, or UF changed the API.")

    sections = [s for c in matches for s in c.get("sections", [])]  # flatten sections across matches
    if CLASS_NUMBER:
        sections = [s for s in sections if str(s.get("classNumber")) == str(CLASS_NUMBER)]
    if len(sections) != 1:
        found = [s.get("classNumber") for c in matches for s in c.get("sections", [])]
        raise RuntimeError(f"Expected 1 section, found {len(sections)}. Set CLASS_NUMBER to one of: {found}")

    section = sections[0]
    waitlist = section.get("waitList")
    if not isinstance(waitlist, dict) or "cap" not in waitlist or "total" not in waitlist:
        raise RuntimeError(f"No usable waitList data (got {waitlist!r}). UF may have changed the API.")

    return int(waitlist["cap"]), int(waitlist["total"]), section.get("classNumber")


def main():
    try:
        cap, total, class_number = read_waitlist()
    except Exception as exc:
        # Layer 1: loud alert on any failure, then fail the run so GitHub shows a red X.
        print(f"ERROR: {exc}")
        try:
            notify("Waitlist watcher is BROKEN", f"{COURSE}: {exc}\nCheck ONE.UF manually until it's fixed.",
                   priority="high", tags="warning")
        except Exception as notify_exc:
            print(f"(couldn't send error push either: {notify_exc})")
        ping_healthcheck("/fail")
        sys.exit(1)

    open_spots = cap - total
    print(f"{COURSE} #{class_number}: waitlist {total}/{cap}, open spots: {open_spots}")

    if cap == 0:
        # No waitlist capacity listed: either seats opened (waitlist goes away) or the waitlist was turned off.
        notify(f"{COURSE}: waitlist shows 0 capacity",
               "Seats may have opened, or the waitlist was turned off. Check ONE.UF now.",
               priority="urgent", tags="rotating_light")
    elif open_spots > 0:
        # The thing you actually care about. Repeats every run until you join (then total hits cap again).
        notify(f"{COURSE} waitlist spot open!",
               f"{total}/{cap} on the waitlist ({open_spots} open). Join in ONE.UF now.",
               priority="urgent", tags="rotating_light")
    elif MODE == "status":
        # Layer 2: daily proof of life.
        notify(f"Watcher OK: {COURSE}", f"Still full ({total}/{cap}). Checking every 15 min.",
               priority="low", tags="white_check_mark")

    ping_healthcheck()   # Layer 3: "I ran and it worked"


if __name__ == "__main__":
    main()