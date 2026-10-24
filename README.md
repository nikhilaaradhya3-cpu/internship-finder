![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

# Internship Finder

An AI agent on Fetch.ai Agentverse that helps Indian college students find **live internships**, spot suspicious listings, and **track their applications with follow-up reminders**, all inside an ASI:One chat. Built for Fetch-A-Thon 3.0.

| | |
|---|---|
| **Agent name** | Internship Finder |
| **ASI:One handle** | `@internship-finder` |
| **Agent address** | `agent1qdqnzy9gu4849tn7jxzkranpfk60mptjlff3gz7vvlsyvearwdzf7qf4gn0` |
| **Agentverse profile** | https://agentverse.ai/agents/details/agent1qdqnzy9gu4849tn7jxzkranpfk60mptjlff3gz7vvlsyvearwdzf7qf4gn0/profile |
| **Shared ASI:One chat (demo)** | https://asi1.ai/chat/888ae4b5-6113-49a0-a8c2-0036fb5d85a0?from=profile&handle=internship-finder |
| **Demo video** | `<ADD VIDEO LINK>` |

## The problem

Students applying for internships face three problems: finding *real, recent* openings, avoiding scam or unpaid "internships" that ask for fees, and keeping track of where they applied so they remember to follow up. Most tools stop at a list of links.

## Target users

Indian college students (especially 1st to 3rd year) looking for their first internship.

## What the agent does

1. **Finds live listings.** Searches the Jooble jobs API for the student's skills and merges several related searches.
2. **Cleans the results.** Hides senior or manager-level roles and listings older than 90 days, and shows real internships first.
3. **Ranks by fit.** Uses the ASI:One LLM (`asi1`) to give a one-line reason why each listing does or does not fit the student.
4. **Flags red flags.** Rule-based checks warn about listings that mention registration fees or deposits, "certificate only" or unpaid roles, or contact only through WhatsApp, Telegram or free email.
5. **Shows results as an Interactive Card carousel** with one-click **I applied** buttons inside ASI:One. A full text list is always sent too, so it still works if cards are not supported.
6. **Tracks applications.** Tapping **I applied** (or typing `I applied to #1`) saves the application.
7. **Reminds the student to follow up** after 5 days and drafts a short follow-up email using only the tracked details.
8. **Writes cover letters on request**, using only facts the student has given.

**Outcome produced:** a ranked, filtered shortlist of real internships, a personal application tracker, and ready-to-send follow-up messages.

## Try it

In ASI:One (https://asi1.ai) or from the Agentverse profile, start your message with `@internship-finder`:

| Say this | What happens |
|---|---|
| `@internship-finder Find me Python internships, I'm a 2nd-year CSE student` | Live listings with fit reasons, red-flag warnings, and a card carousel |
| Tap **I applied** on a card, or `@internship-finder I applied to #1` | Saves the application |
| `@internship-finder my applications` | Shows the tracker and follow-up status |
| `@internship-finder fast forward` | Demo helper: moves applications back 6 days so the reminder appears |
| `@internship-finder draft a follow-up` | Writes the follow-up email for a due application |
| `@internship-finder write a cover letter for #1` | Cover letter based on what you told the agent |

## How it is built

| File | Purpose |
|---|---|
| `agent.py` | Creates the uAgent and includes the chat protocol with `publish_manifest=True` |
| `protocols.py` | Agent Chat Protocol handlers, search, filtering, red-flag rules, Interactive Cards, application tracker |
| `http_client.py` | Jooble search client (`search_internships`) |

- **Framework:** uAgents, hosted on Agentverse
- **Protocol:** Agent Chat Protocol (ACP), discoverable and usable through ASI:One
- **LLM:** ASI:One `asi1` model via the OpenAI-compatible client, used for search keyword extraction, fit reasons, cover letters and follow-up drafts
- **Cards:** ASI:One Interactive Cards (`carousel`), sent as a `MetadataContent` block with a text fallback
- **Data:** live listings from the Jooble API
- **Storage:** per-user results and applications kept in the agent's storage

### Reliability and error handling

- Each search variant is wrapped so one failed request does not stop the others
- If the keyword or fit-reason model call fails, the agent falls back to the original text or no reason
- If no suitable listings are found, the agent says so honestly instead of inventing links
- If a card cannot be sent, the full text list is still delivered
- Replies are built in code so titles, dates and links are never altered by the model

## Run or test it yourself

**Easiest: use the live agent** (hosted on Agentverse). Open the Agentverse profile link above and use *Chat with Agent*, or send `@internship-finder` in ASI:One.

**To run your own copy:**

1. Create a new agent on https://agentverse.ai and paste in `agent.py`, `protocols.py` and `http_client.py`.
2. Get a free Jooble API key (https://jooble.org) and add it where `http_client.py` reads it. **Never commit the key to GitHub.**
3. Agentverse injects `ASI1_BASE_URL` and `ASI1_API_KEY` into hosted agents, so no LLM key is needed in the code.
4. Dependencies: `uagents`, `openai`.
5. Save, **Restart** the agent, then chat with it in ASI:One.

## Extra resources

- Jooble jobs API: https://jooble.org
- ASI:One Interactive Cards docs: https://innovationlab.fetch.ai/resources/docs/interactive-cards/asi-interactive-cards
- Agentverse: https://agentverse.ai

## Limitations and next steps

- Red-flag checks are simple keyword rules, so they are a warning and not proof that a listing is fake. Always verify a listing on the company's own site before applying.
- Planned: a second **vetter agent** for deeper listing checks (multi-agent collaboration) and the Payment Protocol for a premium feature.
