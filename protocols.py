import json
import os
import re
from datetime import datetime, timedelta
from urllib.parse import quote_plus  # CARD: for placeholder image text
from uuid import uuid4

from openai import OpenAI
from uagents import Context, Model, Protocol
from uagents.experimental.chat_agent.protocol import build_llm_message_history
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    MetadataContent,  # CARD: needed to send interactive cards
    TextContent,
    chat_protocol_spec,
)

import http_client
from http_client import search_internships
from payment_proto import request_payment, set_delivery_handler  # PAYMENT

# PAYMENT: True = ask for payment before searching.
# Change to False to search immediately, like before.
PAYMENT_ENABLED = True

LISTING_TRIGGER_WORDS = (
    "listing",
    "openings",
    "open roles",
    "vacancies",
    "positions",
    "job board",
    "find me",
    "show me",
    "hiring",
    "internship",
    "intern",
    "internship opportunities",
)

# If the message is about one of these, do NOT run a new job search.
NON_SEARCH_PHRASES = (
    "cover letter",
    "resume",
    "follow up",
    "follow-up",
    "draft",
    "applications",
)

# Titles containing these are aimed at experienced people: hide them.
SENIOR_WORDS = (
    "senior",
    "sr.",
    "sr ",
    "lead",
    "principal",
    "manager",
    "head of",
    "director",
    "architect",
    "staff ",
    "vp ",
)
INTERN_WORDS = ("intern", "trainee", "apprentice", "graduate engineer trainee")

# Simple red-flag rules for suspicious listings.
RED_FLAGS = {
    "asks for a fee or deposit": (
        "registration fee",
        "security deposit",
        "pay to join",
        "training fee",
        "refundable deposit",
    ),
    "certificate only, no pay": ("certificate only", "unpaid", "no stipend"),
    "contact only via chat apps or free email": ("whatsapp", "telegram", "@gmail.com"),
}

# Pasted text containing any of these (and longer than 40 characters) is sent
# to the Fake Internship Detector instead of the normal chat AI.
SCAM_CHECK_TRIGGERS = (
    "check:",
    "registration fee",
    "security deposit",
    "pay to join",
    "pay rs",
    "guaranteed job",
    "no interview",
    "urgent hiring",
    "whatsapp",
    "telegram",
    "is this fake",
    "is this legit",
    "is this real",
    "scam",
)

STALE_DAYS = 90
MAX_SHOWN = 4
FOLLOWUP_DAYS = 5  # remind the student to follow up this many days after applying

# Token budgets for the chat model. The model may spend part of the budget on
# reasoning, so these are generous to avoid replies that stop mid-sentence.
LLM_MAX_TOKENS = 3000
LLM_RETRY_MAX_TOKENS = 6000

APPLICATION_VIEW_PHRASES = (
    "my applications",
    "application status",
    "applications so far",
    "what have i applied",
    "show applications",
    "tracker",
)

SYSTEM_PROMPT = (
    "You are Internship Finder, a friendly career agent for college students in India. "
    "You take action, not just give advice. RULES: Keep every reply under 200 words, "
    "except when writing follow-up emails: then keep each email under 100 words and "
    "write one for every tracked application, finishing every email completely. "
    "Show at most 4 listings. For each listing give one line: number, title, company, "
    "location, posting date, then the full URL on its own as plain text (never use "
    "markdown links). If the student shared skills or projects, rank the listings by "
    "fit with one short reason each, and finish your reply completely. Write a cover "
    "letter only when asked, in under 150 words, using only facts the student has "
    "given you, and describe projects only in the words the student used, adding no "
    "details about how they were built. If asked for a follow-up message, write a "
    "polite email under 100 words using only the tracked application details given "
    "to you, mention how many days ago the student applied, and use [Your Name] as a "
    "placeholder. You have a built-in application tracker, so never say you cannot "
    "track applications. Never mention word counts, and never offer anything you "
    "cannot do (such as finding contact details or sending emails). "
    "If skills are unknown, ask one short "
    "question. Never invent "
    "companies, job titles, or links, and never guess facts that are not in the "
    "listing data (such as office locations or posting dates)."
)

client = OpenAI(
    # Agentverse injects both variables into every hosted agent; no key is hardcoded
    # or asked of the user.
    base_url=os.environ["ASI1_BASE_URL"],
    api_key=os.environ["ASI1_API_KEY"],
)

internship_protocol = Protocol(spec=chat_protocol_spec)


# --------------------------------------------------------------------------
# Fake Internship Detector (agent-to-agent)
# These two classes must match the detector's classes exactly.
# --------------------------------------------------------------------------
class ScamCheckRequest(Model):
    listings: str  # JSON list of {"id", "title", "company", "snippet"}


class ScamCheckResponse(Model):
    results: str  # JSON list of {"id", "risk", "score", "reasons"}


# PASTE YOUR DETECTOR'S ADDRESS HERE (starts with agent1q, copy it from the
# detector's profile page).
DETECTOR = "agent1qfuegduzs7w60yk5tz2x0jej86qwlp67wxpltms2cqxndj57sen4vqcky59"

# Receives the detector's answers. This protocol must be included in the agent
# (see the agent.include line in agent.py).
scam_result_protocol = Protocol(name="internship_scam_check", version="0.1.0")


def detector_ready() -> bool:
    return DETECTOR.startswith("agent1") and "..." not in DETECTOR


async def check_with_detector(ctx: Context, sender: str, shown: list):
    """Send the shown listings to the detector. The reply arrives in
    handle_scam_result, which messages the student."""
    if not detector_ready():
        ctx.logger.info("Detector address not set yet, skipping scam check")
        return
    items = [
        {
            "id": f"{sender}|{i}",
            "title": str(j.get("title", "")),
            "company": str(j.get("company", "")),
            "snippet": listing_text(j)[:1500],
        }
        for i, j in enumerate(shown, start=1)
    ]
    try:
        await ctx.send(DETECTOR, ScamCheckRequest(listings=json.dumps(items)))
    except Exception:
        ctx.logger.exception("Could not reach detector")


@scam_result_protocol.on_message(model=ScamCheckResponse)
async def handle_scam_result(ctx: Context, sender: str, msg: ScamCheckResponse):
    # Only accept answers from our own detector.
    if detector_ready() and sender != DETECTOR:
        ctx.logger.warning("Ignored scam result from unknown sender")
        return
    try:
        results = json.loads(msg.results)
    except ValueError:
        ctx.logger.exception("Bad scam result from detector")
        return

    # Group by student: ids look like "<student address>|<tag>".
    by_user = {}
    for r in results:
        user, _, tag = str(r.get("id", "")).partition("|")
        if user.startswith("agent1"):
            by_user.setdefault(user, []).append((tag, r))

    for user, rows in by_user.items():
        ctx.logger.info(f"Detector verdicts for {user[:14]}: {len(rows)}")
        lines = []
        pasted = [r for tag, r in rows if tag == "paste"]
        if pasted:
            r = pasted[0]
            lines.append(
                f"🔍 Fake Internship Detector: {r['risk']} (score {r['score']}/100)"
            )
            lines += [f"- {x}" for x in r["reasons"]]
            lines.append(
                "Tip: verify the company on its official website and never pay a fee."
            )
        else:
            last = load_json(ctx, f"last_listings_{user}", [])
            lines.append(
                "🔍 Fake Internship Detector, scam check on your listings "
                "(numbers match the list):"
            )
            for tag, r in rows:
                name = ""
                if tag.isdigit() and 1 <= int(tag) <= len(last):
                    name = f" ({last[int(tag) - 1].get('company', '')})"
                lines.append(
                    f"#{tag}{name}: {r['risk']} (score {r['score']}) - "
                    f"{r['reasons'][0]}"
                )
            lines.append("This is a quick pattern check, not a guarantee.")
        try:
            await ctx.send(user, create_text_chat("\n".join(lines)))
        except Exception:
            ctx.logger.exception("Could not send verdict to student")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def create_text_chat(text: str) -> ChatMessage:
    content = [TextContent(type="text", text=text)]
    return ChatMessage(timestamp=datetime.utcnow(), msg_id=uuid4(), content=content)


def trim_to_complete(text: str) -> str:
    """If a reply was cut off mid-sentence, drop the unfinished tail."""
    text = text.rstrip()
    if not text or text[-1] in ".!?)\"'":
        return text
    cut = max(text.rfind(". "), text.rfind(".\n"), text.rfind("!\n"), text.rfind("?\n"))
    if cut > len(text) // 3:
        return text[: cut + 1].rstrip()
    return text


# --------------------------------------------------------------------------
# CARD: ASI:One Interactive Cards (carousel with an "I applied" button)
# --------------------------------------------------------------------------
def create_card_chat(text: str, card_kind: str, card_payload: dict) -> ChatMessage:
    """One message = a text bubble (also the fallback) + one card declaration.

    Every metadata value must be a string, so the payload is JSON-encoded.
    """
    return ChatMessage(
        timestamp=datetime.utcnow(),
        msg_id=uuid4(),
        content=[
            TextContent(type="text", text=text),
            MetadataContent(
                type="metadata",
                metadata={
                    "card_protocol_version": "1",
                    "requires_card_interaction": "true",
                    "card_kind": card_kind,
                    "card_payload": json.dumps(card_payload),
                    "preferred_drawer_width_px": "540",
                },
            ),
        ],
    )


def build_internships_card(shown: list) -> dict:
    """Carousel payload. Card N gets id 'int_N', matching '#N' in the text list."""
    items = []
    for i, j in enumerate(shown, start=1):
        company = str(j.get("company") or "Company")
        location = str(j.get("location") or "Location not listed")
        posted = str(j.get("posted") or "date not listed")[:10]
        badges = [
            {
                "label": "Internship" if j["_is_intern"] else "Check level",
                "variant": "success" if j["_is_intern"] else "info",
            }
        ]
        if j["_flags"]:
            badges.append({"label": "Check carefully", "variant": "warning"})
        items.append(
            {
                "id": f"int_{i}",
                "image": "https://placehold.co/400x300?text="
                + quote_plus(company[:24]),
                "title": str(j.get("title") or "Untitled")[:80],
                "subtitle": f"{company} · {location}",
                "badges": badges,
                "secondary_text": f"Posted {posted}",
                "primary_cta": {
                    "label": "I applied",
                    "selection": {"action": "applied", "internship_id": f"int_{i}"},
                },
            }
        )
    return {
        "title": "Internships for you",
        "subtitle": "Tap I applied on the ones you've applied to",
        "style": "slide",
        "items": items,
    }


def parse_card_selection(text: str, count: int):
    """Return the listing number (1-based) if this message is an 'I applied' tap.

    ASI:One sends the tap back either as JSON text (direct @mention) or as a
    sentence that mentions the id. Handle both. Returns None otherwise.
    """
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            if isinstance(data.get("selection"), dict):
                data = data["selection"]  # unwrap {"selection": {...}}
            match = re.fullmatch(r"int_(\d+)", str(data.get("internship_id", "")))
            if data.get("action") == "applied" and match:
                number = int(match.group(1))
                return number if 1 <= number <= count else None
            return None
    except (ValueError, TypeError):
        pass
    match = re.search(r"\bint_(\d+)\b", text)  # sentence form
    if match and 1 <= int(match.group(1)) <= count:
        return int(match.group(1))
    return None


def wants_live_listings(text: str) -> bool:
    lowered = text.lower()
    if any(phrase in lowered for phrase in NON_SEARCH_PHRASES):
        return False
    return any(word in lowered for word in LISTING_TRIGGER_WORDS)


def extract_search_query(text: str) -> str:
    """Turn the student's message into a short keyword query for Jooble."""
    try:
        completion = client.chat.completions.create(
            model="asi1",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract job-search keywords from the student's message. "
                        'Reply with ONLY JSON like {"keywords": "python intern"}. '
                        "Use 2-4 words: the main skill or role plus 'intern'."
                    ),
                },
                {"role": "user", "content": text},
            ],
            max_tokens=60,
        )
        raw = completion.choices[0].message.content or ""
        data = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
        return str(data.get("keywords", "")).strip() or text
    except Exception:
        return text  # fall back to the original text, never crash


def parse_date(value) -> datetime | None:
    """Parse the first 10 characters as YYYY-MM-DD, or return None."""
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except Exception:
        return None


def listing_text(listing: dict) -> str:
    """All text fields of a listing joined, for keyword checks."""
    return " ".join(str(v) for v in listing.values() if isinstance(v, str)).lower()


def red_flags_for(listing: dict) -> list:
    text = listing_text(listing)
    return [
        label
        for label, words in RED_FLAGS.items()
        if any(word in text for word in words)
    ]


def clean_title(title) -> tuple:
    """Split a leading tag like '[Internship]' off a title: (title, tag)."""
    raw = str(title or "").strip()
    match = re.match(r"^\[([^\]]+)\]\s*(.*)$", raw)
    if match:
        return (match.group(2).strip() or raw), match.group(1).strip().lower()
    return raw, ""


def looks_like_internship(item: dict) -> bool:
    title, tag = clean_title(item.get("title", ""))
    lowered = title.lower() + " "
    return tag == "internship" or any(word in lowered for word in INTERN_WORDS)


def search_many(query: str, logger) -> list:
    """Run up to 3 related searches, merge and de-duplicate the results."""
    variants = list(dict.fromkeys([query, f"{query} internship", f"{query} trainee"]))
    seen = set()
    merged = []
    for variant in variants:
        try:
            batch = search_internships(variant, limit=10)
        except Exception:
            logger.exception(f"Listing search failed for: {variant}")
            continue
        for item in batch or []:
            key = item.get("url") or item.get("title")
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        if sum(1 for m in merged if looks_like_internship(m)) >= MAX_SHOWN:
            break  # enough real internships, stop searching
    return merged


def prepare_listings(listings: list) -> list:
    """Drop stale or senior listings, tag the rest, and put internships first."""
    now = datetime.utcnow()
    prepared = []
    for item in listings:
        title, tag = clean_title(item.get("title", ""))
        lowered = title.lower() + " "
        if any(word in lowered for word in SENIOR_WORDS):
            continue
        posted = parse_date(item.get("posted"))
        if posted is not None and (now - posted).days > STALE_DAYS:
            continue
        is_intern = looks_like_internship(item)
        if is_intern:
            level = "Internship"
        elif tag == "entry-level":
            level = "Entry-level role (usually for graduates)"
        else:
            level = "Level not stated - check requirements"
        entry = dict(item)
        entry["title"] = title
        entry["_level"] = level
        entry["_is_intern"] = is_intern
        entry["_posted_dt"] = posted
        entry["_flags"] = red_flags_for(item)
        prepared.append(entry)

    prepared.sort(
        key=lambda e: (
            0 if e["_is_intern"] else 1,
            -(e["_posted_dt"].timestamp() if e["_posted_dt"] else 0),
        )
    )
    return prepared[:MAX_SHOWN]


def get_fit_reasons(student_text: str, shown: list) -> dict:
    """Ask ASI:One for a short 'why this fits' per listing. Never raises."""
    try:
        lines = [
            f"{i}. {j.get('title', '')} at {j.get('company', '')} "
            f"({j.get('location', '')}) - {j['_level']}"
            for i, j in enumerate(shown, start=1)
        ]
        completion = client.chat.completions.create(
            model="asi1",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You match students to internships. Using ONLY the student's "
                        "message and the listing lines, write one short reason "
                        "(max 15 words) why each listing does or does not fit. "
                        "Do not invent details about the company or role. "
                        "If the role looks aimed at experienced people, say so. "
                        'Reply with ONLY JSON like {"1": "reason", "2": "reason"}.'
                    ),
                },
                {
                    "role": "user",
                    "content": f"Student: {student_text}\n\nListings:\n"
                    + "\n".join(lines),
                },
            ],
            max_tokens=400,
        )
        raw = completion.choices[0].message.content or ""
        data = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
        return {str(k): str(v) for k, v in data.items()}
    except Exception:
        return {}


def build_listings_reply(shown: list, reasons: dict) -> str:
    """Build the reply in code so dates, titles and links are never altered."""
    out = ["Here are live listings that match what you told me:", ""]
    for i, j in enumerate(shown, start=1):
        out.append(
            f"{i}. {j.get('title', 'Untitled')} at {j.get('company', 'Unknown')}"
        )
        out.append(
            f"   {j.get('location', 'Location not listed')} | "
            f"posted {j.get('posted', 'date not listed')} | {j['_level']}"
        )
        out.append(f"   {j.get('url', '')}")
        reason = reasons.get(str(i))
        if reason:
            out.append(f"   Why: {reason}")
        if j["_flags"]:
            out.append(f"   Check carefully: {', '.join(j['_flags'])}")
        out.append("")
    out.append(
        "Once you apply, tap I applied on its card or tell me (for example: "
        "'I applied to #1') and I will track it and remind you to follow up. "
        "I can also draft a cover letter. "
        "Always verify a listing on the company's own site before you apply."
    )
    return "\n".join(out)


# --------------------------------------------------------------------------
# Application tracker
# --------------------------------------------------------------------------
def load_json(ctx: Context, key: str, default):
    try:
        raw = ctx.storage.get(key)
        return json.loads(raw) if raw else default
    except Exception:
        ctx.logger.exception(f"Could not load {key}")
        return default


def save_json(ctx: Context, key: str, value) -> None:
    try:
        ctx.storage.set(key, json.dumps(value, default=str))
    except Exception:
        ctx.logger.exception(f"Could not save {key}")


def detect_applied(text: str, previous: list):
    """Return a listing dict, 'ask' (which one?), 'no_listings', or None."""
    lowered = text.lower()
    strict = re.search(r"\bapplied\s+(?:to|for|at|on)\b", lowered) or re.search(
        r"\b(?:i(?:'ve| have)?|just)\s+applied\s*(?:#?\d\b|[.!]?$)", lowered
    )
    if not strict:
        return None
    if not previous:
        return "no_listings"
    for j in previous:
        company = str(j.get("company", "")).lower().strip()
        title = str(j.get("title", "")).lower().strip()
        if (len(company) > 2 and company in lowered) or (
            len(title) > 3 and title in lowered
        ):
            return j
    number = re.search(r"(?<![\w])#?([1-9])(?![\w])", lowered)
    if number and 1 <= int(number.group(1)) <= len(previous):
        return previous[int(number.group(1)) - 1]
    return "ask"


def days_since(application: dict) -> int:
    applied = parse_date(application.get("applied_on"))
    return (datetime.utcnow() - applied).days if applied else 0


def due_followups(apps: list) -> list:
    return [
        a
        for a in apps
        if days_since(a) >= FOLLOWUP_DAYS and not a.get("followed_up")
    ]


def record_application(apps: list, listing: dict) -> bool:
    """Add the application. Returns False if it was already tracked."""
    url = listing.get("url", "")
    if url and any(a.get("url") == url for a in apps):
        return False
    apps.append(
        {
            "title": listing.get("title", "Untitled"),
            "company": listing.get("company", "Unknown"),
            "url": url,
            "applied_on": datetime.utcnow().strftime("%Y-%m-%d"),
            "followed_up": False,
        }
    )
    return True


def build_applications_reply(apps: list) -> str:
    if not apps:
        return (
            "You have not told me about any applications yet. After you apply, say "
            "'I applied to #2' and I will track it and remind you to follow up."
        )
    lines = ["Your tracked applications:", ""]
    for i, a in enumerate(apps, start=1):
        d = days_since(a)
        if a.get("followed_up"):
            status = "follow-up sent"
        elif d >= FOLLOWUP_DAYS:
            status = f"FOLLOW-UP DUE (applied {d} days ago)"
        else:
            status = f"applied {d} days ago, follow up in {FOLLOWUP_DAYS - d} days"
        lines.append(f"{i}. {a['title']} at {a['company']} - {status}")
        lines.append(f"   {a['url']}")
    lines += ["", "Say 'draft a follow-up' and I will write the email for you."]
    return "\n".join(lines)


def reminder_text(apps: list) -> str:
    due = due_followups(apps)
    if not due:
        return ""
    names = ", ".join(f"{a['title']} at {a['company']}" for a in due[:2])
    return (
        f"\n\nReminder: a follow-up is due for {names}. "
        "Say 'draft a follow-up' and I will write it."
    )


def applications_context(apps: list) -> str:
    lines = ["Tracked applications for this student:"]
    for i, a in enumerate(apps, start=1):
        d = days_since(a)
        if a.get("followed_up"):
            status = "follow-up already sent"
        elif d >= FOLLOWUP_DAYS:
            status = "follow-up is due"
        else:
            status = "follow-up not due yet"
        lines.append(
            f"{i}. {a['title']} at {a['company']}, applied {a['applied_on']} "
            f"({d} days ago), {status} - {a['url']}"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# PAYMENT: the search itself. Called directly when payments are off, or by
# payment_proto.py (through set_delivery_handler) after a payment is verified.
# --------------------------------------------------------------------------
async def run_listing_search(ctx: Context, sender: str, text: str):
    listings_key = f"last_listings_{sender}"
    apps = load_json(ctx, f"applications_{sender}", [])

    query = extract_search_query(text)
    ctx.logger.info(f"Search query: {query}")
    listings = search_many(query, ctx.logger)
    ctx.logger.info(f"Jooble status: {http_client.JOOBLE_STATUS['msg'][:300]}")
    ctx.logger.info(f"RAW LISTINGS: {listings}")

    shown = prepare_listings(listings) if listings else []
    if shown:
        ctx.logger.info(f"Showing {len(shown)} of {len(listings)} listings")
        reasons = get_fit_reasons(text, shown)
        reply = build_listings_reply(shown, reasons)
        # Remember what we showed so "I applied to #2" and "write a cover
        # letter for #2" can use the real listing data.
        save_json(
            ctx,
            listings_key,
            [{k: v for k, v in j.items() if not k.startswith("_")} for j in shown],
        )
        # CARD: send the text list plus a carousel. If the card cannot be
        # built or ASI:One rejects it, the student still sees the full text.
        try:
            await ctx.send(
                sender,
                create_card_chat(
                    reply + reminder_text(apps),
                    "carousel",
                    build_internships_card(shown),
                ),
            )
        except Exception:
            ctx.logger.exception("Card send failed, sending text only")
            await ctx.send(sender, create_text_chat(reply + reminder_text(apps)))
        # Ask the Fake Internship Detector about these listings. Its answer
        # is sent to the student by handle_scam_result.
        await check_with_detector(ctx, sender, shown)
        return

    # Nothing found: answer honestly with the model.
    try:
        completion = client.chat.completions.create(
            model="asi1",
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                    + "\n\nNo suitable live listings could be found right now. "
                    "Tell the student this honestly, give practical search advice "
                    "instead, and do not invent specific job links.",
                },
                {"role": "user", "content": text},
            ],
            max_tokens=LLM_MAX_TOKENS,
        )
        reply = trim_to_complete(str(completion.choices[0].message.content or ""))
    except Exception:
        ctx.logger.exception("Error querying model")
        reply = "I couldn't find suitable listings right now. Please try again later."
    await ctx.send(sender, create_text_chat(reply + reminder_text(apps)))


# PAYMENT: tell payment_proto.py which function delivers the paid work.
set_delivery_handler(run_listing_search)


# --------------------------------------------------------------------------
# Message handling
# --------------------------------------------------------------------------
@internship_protocol.on_message(ChatMessage)
async def handle_message(ctx: Context, sender: str, msg: ChatMessage):
    # Always acknowledge incoming chat messages first.
    await ctx.send(
        sender,
        ChatAcknowledgement(timestamp=datetime.now(), acknowledged_msg_id=msg.msg_id),
    )

    text = msg.text()
    if not text:
        return

    lowered = text.lower()
    listings_key = f"last_listings_{sender}"
    apps_key = f"applications_{sender}"
    previous = load_json(ctx, listings_key, [])
    apps = load_json(ctx, apps_key, [])

    async def say(body: str, reminder: bool = True):
        extra = reminder_text(apps) if reminder else ""
        await ctx.send(sender, create_text_chat(body + extra))

    # DEBUG: proves which version of the file is running.
    ctx.logger.info(f"VERSION payments-v7 | PAYMENT_ENABLED={PAYMENT_ENABLED}")

    # --- Pasted listing: send it to the Fake Internship Detector ---
    # Checked first, so a pasted scam text that contains the word "internship"
    # does not start a new job search.
    if len(text) > 40 and any(t in lowered for t in SCAM_CHECK_TRIGGERS):
        if not detector_ready():
            await say(
                "The scam detector is not connected yet. Please try again later.",
                reminder=False,
            )
            return
        try:
            await ctx.send(
                DETECTOR,
                ScamCheckRequest(
                    listings=json.dumps(
                        [
                            {
                                "id": f"{sender}|paste",
                                "title": text[:200],
                                "company": "pasted",
                                "snippet": text,
                            }
                        ]
                    )
                ),
            )
            await say(
                "Checking this listing with the Fake Internship Detector...",
                reminder=False,
            )
        except Exception:
            ctx.logger.exception("Could not reach detector")
            await say(
                "I could not reach the scam detector right now. Please try again "
                "in a moment.",
                reminder=False,
            )
        return

    # --- Demo helper: clear saved listings and applications for a clean demo ---
    if "reset demo" in lowered:
        save_json(ctx, apps_key, [])
        save_json(ctx, listings_key, [])
        save_json(ctx, f"pay_pending_{sender}", None)
        await ctx.send(
            sender,
            create_text_chat(
                "Demo reset: your saved listings and applications are cleared. "
                "Ask me to find internships to start fresh."
            ),
        )
        return

    # --- Demo helper: pretend 6 days have passed so the reminder can be shown ---
    if "fast forward" in lowered:
        for a in apps:
            applied = parse_date(a.get("applied_on"))
            if applied:
                a["applied_on"] = (applied - timedelta(days=6)).strftime("%Y-%m-%d")
        save_json(ctx, apps_key, apps)
        await say(
            "Demo mode: moved your applications back by 6 days.\n\n"
            + build_applications_reply(apps),
            reminder=False,
        )
        return

    # --- Student says they already sent the follow-up ---
    if re.search(r"\bfollowed up\b|\bsent (?:the |a )?follow", lowered):
        marked = 0
        for a in due_followups(apps):
            a["followed_up"] = True
            marked += 1
        save_json(ctx, apps_key, apps)
        await say(
            "Marked as followed up. Good luck!"
            if marked
            else "You have no follow-ups pending right now.",
            reminder=False,
        )
        return

    # --- Student says they applied (card tap OR typed message) ---
    # CARD: a tap on "I applied" is checked first. Without this, the tap text
    # (which contains the word "internship") would start a new job search.
    tap = parse_card_selection(text, len(previous))
    if tap:
        applied = previous[tap - 1]
    elif re.search(r"\bint_\d+\b", text):
        await say(
            "Those results have expired. Ask me to find internships again, then "
            "tap I applied on a new card."
        )
        return
    else:
        applied = detect_applied(text, previous)

    if applied == "no_listings":
        await say(
            "I do not have a listing saved for you yet. Ask me to find internships "
            "first, then tell me which one you applied to."
        )
        return
    if applied == "ask":
        await say(
            "Which one did you apply to? Say it like 'I applied to #2' or name the "
            "company."
        )
        return
    if isinstance(applied, dict):
        is_new = record_application(apps, applied)
        save_json(ctx, apps_key, apps)
        name = f"{applied.get('title')} at {applied.get('company')}"
        await say(
            (
                f"Saved: {name}. I will remind you to follow up in {FOLLOWUP_DAYS} "
                "days. Say 'my applications' any time to see your tracker."
            )
            if is_new
            else f"{name} is already in your tracker."
        )
        return

    # --- Student asks to see the tracker ---
    if any(phrase in lowered for phrase in APPLICATION_VIEW_PHRASES):
        await say(build_applications_reply(apps), reminder=False)
        return

    # --- Follow-up requested but nothing is tracked yet: guide, never guess ---
    if re.search(r"follow[- ]?up", lowered) and not apps:
        await say(
            "I cannot draft a follow-up yet because no application is tracked for "
            "you. Tell me which one you applied to, for example 'I applied to #1' "
            "(search for internships first if you have not), then ask again.",
            reminder=False,
        )
        return

    system_content = SYSTEM_PROMPT

    # DEMO ONLY: lets a tester skip the payment card. Remove before going live.
    if lowered.strip() in ("demo pay", "paid", "i paid") or lowered.strip().endswith(
        "demo pay"
    ):
        pending = load_json(ctx, f"pay_pending_{sender}", None)
        if not pending:
            await say(
                "There is no pending payment. Ask me to find internships first.",
                reminder=False,
            )
            return
        save_json(ctx, f"pay_pending_{sender}", None)
        await run_listing_search(ctx, sender, pending["job"]["text"])
        return

    # PAYMENT: searching for listings is the paid action.
    #  - PAYMENT_ENABLED True  -> ask for payment; the search runs after
    #    payment_proto.py verifies it.
    #  - PAYMENT_ENABLED False -> search immediately, exactly like before.
    if wants_live_listings(text):
        ctx.logger.info(f"SEARCH REQUEST | PAYMENT_ENABLED={PAYMENT_ENABLED}")
        if PAYMENT_ENABLED:
            await request_payment(ctx, sender, {"text": text})
        else:
            await run_listing_search(ctx, sender, text)
        return

    # Follow-up message: give the model the real data it should write from.
    if apps and "follow" in lowered:
        system_content += "\n\n" + applications_context(apps)
        if len(apps) > 1:
            system_content += (
                "\n\nWrite one short follow-up email (under 80 words) for each "
                "tracked application whose follow-up is due, and finish every email."
            )
    elif previous:
        system_content += "\n\nListings shown earlier to this student:\n" + (
            "\n".join(
                f"{i}. {j.get('title')} at {j.get('company')} "
                f"({j.get('location')}, posted {j.get('posted')}) - "
                f"{j.get('url')}"
                for i, j in enumerate(previous, start=1)
            )
        )

    messages = [
        {"role": "system", "content": system_content},
        *build_llm_message_history(ctx),
    ]

    try:
        completion = client.chat.completions.create(
            model="asi1",
            messages=messages,
            max_tokens=LLM_MAX_TOKENS,
        )
        choice = completion.choices[0]
        reply = str(choice.message.content or "")
        ctx.logger.info(
            f"LLM finish_reason={choice.finish_reason}, reply_chars={len(reply)}"
        )
        # If the reply was cut off by the token limit, ask once more with a
        # bigger budget instead of sending half an email.
        if choice.finish_reason == "length":
            ctx.logger.info("Reply hit the token limit, retrying with a bigger budget")
            retry = client.chat.completions.create(
                model="asi1",
                messages=messages,
                max_tokens=LLM_RETRY_MAX_TOKENS,
            )
            retry_choice = retry.choices[0]
            reply = str(retry_choice.message.content or "") or reply
            ctx.logger.info(
                f"Retry finish_reason={retry_choice.finish_reason}, "
                f"reply_chars={len(reply)}"
            )
            if retry_choice.finish_reason == "length":
                reply = trim_to_complete(reply)
    except Exception:
        ctx.logger.exception("Error querying model")
        reply = (
            "Sorry, I hit a problem while preparing your answer. "
            "Please try again in a moment."
        )

    # Do not nag about a follow-up in the same message that drafts it.
    await say(reply, reminder=not re.search(r"follow[- ]?up", lowered))


@internship_protocol.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass
