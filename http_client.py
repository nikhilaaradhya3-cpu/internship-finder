import os
import re
import requests
from concurrent.futures import ThreadPoolExecutor

CITIES = ["bengaluru", "bangalore", "mumbai", "delhi", "hyderabad", "pune", "chennai"]
_cache = {}
JOOBLE_STATUS = {"msg": "not tried"}

INTERN_RE = re.compile(
    r"\b(intern|interns|internship|trainee|apprentice|graduate|fresher|freshers|campus)\b",
    re.I,
)
SENIOR_RE = re.compile(
    r"\b(senior|sr|lead|principal|staff|manager|director|head|architect|vp|ii|iii|iv)\b",
    re.I,
)


def _jooble_call(keywords: str, location: str):
    key = os.environ.get("JOOBLE_API_KEY", "")
    if not key:
        return None, "no JOOBLE_API_KEY"
    try:
        r = requests.post(
            f"https://jooble.org/api/{key}",
            json={"keywords": keywords, "location": location},
            timeout=8,
        )
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        jobs = r.json().get("jobs", [])
        return jobs, f"{len(jobs)}"
    except Exception as e:
        return None, f"error: {e}"


def _jooble(where: str, role_kind: str, limit: int) -> list:
    if where.lower() in ("bengaluru", "bangalore"):
        locs = ["Bangalore, Karnataka", "Bangalore", "India"]
        city_words = ["bangalore", "bengaluru", "karnataka"]
    elif where:
        locs = [f"{where}", "India"]
        city_words = [where.lower()]
    else:
        locs = ["India"]
        city_words = []

    if role_kind == "data":
        kws = ["data science intern", "data analyst intern", "data scientist fresher"]
    else:
        kws = ["machine learning intern", "AI intern", "machine learning engineer fresher"]

    combos = [(k, l) for k in kws for l in locs]
    with ThreadPoolExecutor(max_workers=6) as ex:
        outs = list(ex.map(lambda c: _jooble_call(*c), combos))

    seen, pool, infos = set(), [], []
    for (k, l), (jobs, info) in zip(combos, outs):
        infos.append(f"[{k}/{l}: {info}]")
        for j in jobs or []:
            url = j.get("link", "")
            if url and url not in seen:
                seen.add(url)
                pool.append(j)

    def in_city(j):
        loc = (j.get("location") or "").lower()
        return any(w in loc for w in city_words)

    interns = [j for j in pool if INTERN_RE.search(j.get("title", ""))]
    entry = [
        j for j in pool
        if j not in interns and not SENIOR_RE.search(j.get("title", ""))
    ]
    interns.sort(key=lambda j: 0 if in_city(j) else 1)
    entry.sort(key=lambda j: 0 if in_city(j) else 1)

    JOOBLE_STATUS["msg"] = (
        f"{len(interns)} internships, {len(entry)} entry-level, "
        f"of {len(pool)} unique " + " ".join(infos)
    )

    out = []
    for tag, group in (("Internship", interns), ("Entry-level", entry)):
        for j in group:
            out.append(
                {
                    "title": f"[{tag}] {j.get('title', '')}",
                    "company": j.get("company", "Unknown"),
                    "location": j.get("location", ""),
                    "posted": (j.get("updated") or "")[:10],
                    "url": j.get("link", ""),
                }
            )
    return out[:limit]


def _remotive(role: str, limit: int) -> list:
    try:
        r = requests.get(
            "https://remotive.com/api/remote-jobs",
            params={"search": role, "limit": limit},
            timeout=10,
        )
        r.raise_for_status()
        return [
            {
                "title": j.get("title", ""),
                "company": j.get("company_name", "Unknown"),
                "location": j.get("candidate_required_location", "Remote"),
                "posted": (j.get("publication_date") or "")[:10],
                "url": j.get("url", ""),
            }
            for j in r.json().get("jobs", [])[:limit]
        ]
    except Exception:
        return []


def search_internships(text: str, limit: int = 6) -> list:
    lowered = text.lower()
    where = next((c for c in CITIES if c in lowered), "bengaluru")
    if "remote" in lowered:
        where = ""
    role_kind = "data" if "data" in lowered else "ml"

    key = (where, role_kind)
    if key in _cache:
        return _cache[key]

    results = _jooble(where, role_kind, limit)
    if not results:
        results = _remotive("data science" if role_kind == "data" else "machine learning", limit)
    if results:
        _cache[key] = results
    return results
