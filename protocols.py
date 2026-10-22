import os
from datetime import datetime
from uuid import uuid4

from openai import OpenAI
from uagents import Context, Protocol
from uagents.experimental.chat_agent.protocol import build_llm_message_history
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    chat_protocol_spec,
)

import http_client
from http_client import search_internships

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

SYSTEM_PROMPT = (
    "You are Internship Finder, a friendly career agent for college students in India. "
    "You take action, not just give advice. RULES: Keep every reply under 200 words. "
    "Show at most 4 listings. For each listing give one line: number, title, company, "
    "location, posting date, then the full URL on its own as plain text (never use "
    "markdown links). If the student shared skills or projects, rank the listings by "
    "fit with one short reason each, and finish your reply completely. Write a cover "
    "letter only when asked, in under 150 words, using only facts the student has "
    "given you, and describe projects only in the words the student used, adding no "
"details about how they were built. If skills are unknown, ask one short "
"question. Never invent "
    "companies, job titles, or links, and never guess facts that are not in the "
    "listing data (such as office locations)."
)

client = OpenAI(
    # Agentverse injects both variables into every hosted agent; no key is hardcoded
    # or asked of the user.
    base_url=os.environ["ASI1_BASE_URL"],
    api_key=os.environ["ASI1_API_KEY"],
)

internship_protocol = Protocol(spec=chat_protocol_spec)


def create_text_chat(text: str) -> ChatMessage:
    content = [TextContent(type="text", text=text)]
    return ChatMessage(timestamp=datetime.utcnow(), msg_id=uuid4(), content=content)


def wants_live_listings(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in LISTING_TRIGGER_WORDS)


def format_listings_context(listings: list) -> str:
    lines = ["Live internship listings fetched just now. Recommend only from these:"]
    for i, j in enumerate(listings, start=1):
        lines.append(
            f"{i}. {j['title']} at {j['company']} "
            f"({j['location']}, posted {j['posted']}) - {j['url']}"
        )
    return "\n".join(lines)


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

    system_content = SYSTEM_PROMPT
    if wants_live_listings(text):
        listings = search_internships(text, limit=4)
        ctx.logger.info(f"Jooble status: {http_client.JOOBLE_STATUS['msg'][:300]}")
        if listings:
            ctx.logger.info(f"Fetched {len(listings)} live listings")
            system_content += "\n\n" + format_listings_context(listings)
        else:
            system_content += (
                "\n\nLive listings could not be fetched right now. Tell the student "
                "this honestly, give practical search advice instead, and do not "
                "invent specific job links."
            )

    messages = [
        {"role": "system", "content": system_content},
        *build_llm_message_history(ctx),
    ]

    try:
        completion = client.chat.completions.create(
            model="asi1",
            messages=messages,
            max_tokens=1200,
        )
        choice = completion.choices[0]
        reply = str(choice.message.content or "")
        ctx.logger.info(
            f"LLM finish_reason={choice.finish_reason}, reply_chars={len(reply)}"
        )
    except Exception as exc:
        ctx.logger.exception("Error querying model")
        reply = (
            "Sorry, something went wrong while preparing your answer. "
            f"Please try again in a moment. ({exc})"
        )

    await ctx.send(sender, create_text_chat(reply))


@internship_protocol.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass
