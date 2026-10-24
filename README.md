![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

**Agent name:** Internship Finder (@internship-finder)

**Agent address:** agent1qdqnzy9gu4849tn7jxzkranpfk60mptjlff3gz7vvlsyvearwdzf7qf4gn0
**Extra resource needed:** a free Jooble API key (jooble.org/api/about)

# Internship Finder

An ASI:One agent that turns "find me an internship" into action: live listings,
honest filtering, resume-based ranking and cover letter drafting. Built for
Fetch-A-Thon 3.0.

## Problem
Students waste hours on job boards full of senior roles, stale posts and
irrelevant listings.

## Target users
Indian college students looking for AI/ML and data science internships.

## What the agent does
1. Searches the Jooble API for live listings (several keyword variants, deduplicated)
2. Labels each result as Internship or Entry-level and removes senior titles
3. Falls back to the Remotive API if Jooble fails or returns nothing
4. Ranks results against the student's skills and drafts a short cover letter

## Try it
- Agentverse profile: https://agentverse.ai/agents/details/agent1qdqnzy9gu4849tn7jxzkranpfk60mptjlff3gz7vvlsyvearwdzf7qf4gn0/profile
- ASI:One shared chat: https://asi1.ai/shared-chat/2873aaf7-2e17-440c-9775-1d3f2bd9d414

Example prompts in ASI:One:
- "Find me AI/ML internships in Bengaluru"
- "My skills: Python, PyTorch, scikit-learn. Which listing fits best?"
- "Write a short cover letter for listing 1."

## Run it yourself
1. Create a hosted agent on Agentverse and add `agent.py`, `protocols.py`
   and `http_client.py`.
2. Get a free API key at jooble.org/api/about and add `JOOBLE_API_KEY=...`
   to the agent's `.env` (see `Example.env`).
3. Start the agent and chat with it from ASI:One.

## Protocols and tools
Agent Chat Protocol, Jooble API, Remotive API.

## Known limitations
Free job feeds contain few true internships, and Jooble's city filter returned
no Bengaluru results for this key, so the agent searches India-wide and puts
Bangalore listings first when present. The agent says this openly.
