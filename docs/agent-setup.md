# Running jobpilot with AI agents

jobpilot is designed to be driven by an AI agent. The agent reads
`SKILL.md`, follows the workflow, and handles the human-in-the-loop steps
(reviewing fills, providing Greenhouse email codes). Below is how to set
this up on each major agent platform.

The pattern is the same everywhere:

1. Give the agent the repo URL: `https://github.com/summitsingh/ai-job-pilot`
2. Tell it to read `SKILL.md` and follow it.
3. Provide your `facts.json` (or fill in `facts.example.json`).
4. The agent handles the browser, the model calls, and the fill. You
   handle the two human steps: approving submits and pasting Greenhouse
   email codes.

---

## Claude Code (Anthropic)

Claude Code reads `SKILL.md` natively as a skill file.

```bash
# In your project directory
git clone https://github.com/summitsingh/ai-job-pilot.git
cd jobpilot
cp facts.example.json facts.json
# Edit facts.json with your real details
```

Then in Claude Code:

> Read SKILL.md in ./jobpilot and apply to <posting URL> using my
> facts.json. Dry-run first, show me the screenshot before submitting.

Skills in `~/.claude/skills/` are auto-discovered. To install jobpilot
as a persistent skill:

```bash
mkdir -p ~/.claude/skills/jobpilot
cp /path/to/jobpilot/SKILL.md ~/.claude/skills/jobpilot/
```

Claude Code can also run the Chrome + model setup for you; ask it to
follow the Prerequisites section of `SKILL.md`.

## Codex (OpenAI CLI)

Codex works from the repo directory. Clone jobpilot and point Codex at it:

```bash
git clone https://github.com/summitsingh/ai-job-pilot.git
cd jobpilot
cp facts.example.json facts.json
# Edit facts.json with your real details
codex
```

Then:

> Read SKILL.md and follow it to apply to <posting URL>. Use --no-submit
> first and show me the result before submitting.

Codex runs shell commands directly, so it can manage the Chrome debug
instance and run the full pipeline. For repeated use, add the repo path
to your Codex workspace.

## ChatGPT (chat + Custom GPT)

**Option A: Custom GPT.** Create a Custom GPT with the repo as knowledge:

1. Clone the repo locally (or download as ZIP).
2. In the GPT builder, upload `SKILL.md`, `AGENTS.md`, and
   `docs/facts-schema.md` as knowledge files.
3. Set the instructions to: "You are a job-application assistant. When
   the user gives you a posting URL, follow the SKILL.md workflow:
   dry-run with --no-submit, show the result, wait for approval, then
   submit. Never invent applicant facts."

The limitation: ChatGPT cannot run the browser or the scripts itself.
Use it for the planning and review steps: paste a posting URL, have it
walk you through the manual commands, review `result.json` output you
paste back, and decide on skipped fields.

**Option B: Chat with code execution.** Paste `SKILL.md` into the
conversation and say:

> Follow this skill to help me apply to <posting URL>. Walk me through
> each step; I will run the commands and paste results back.

## Muse (Meta)

Muse runs with a real computer: shell, browser, and filesystem. Point it
at the repo:

> Clone https://github.com/summitsingh/ai-job-pilot and read SKILL.md.
> Apply to <posting URL> with my facts at <path to facts.json>.
> Dry-run first and show me the screenshot before submitting.

Muse can manage the entire loop itself: starting Chrome with remote
debugging, running the pipeline, handling the Greenhouse code gate by
asking you for the email code, and verifying the confirmation page.
The two human steps (submit approval, email codes) come to you as
approval prompts.

## Hermes and similar local agents

Hermes, OpenClaw, and other local-first agents follow the same pattern.
The repo ships `AGENTS.md` as a plain-language runbook for exactly this:

1. Clone the repo on the machine where the agent runs.
2. Tell the agent: "Read AGENTS.md in ./jobpilot and apply to <URL>."
3. The agent follows the setup, runs the pipeline, and asks you for
   the human steps.

`AGENTS.md` is written to be self-contained: an agent that has never
seen jobpilot can go from zero to a verified fill by reading that one
file.

---

## The two human steps (all platforms)

No matter which agent you use, two steps need you:

1. **Submit approval.** The agent dry-runs with `--no-submit` and shows
   you what will be filled. You approve before anything is submitted.
2. **Greenhouse email codes.** After submit, Greenhouse emails an
   8-character code. You read it from your email and give it to the
   agent (or type it into `code_gate.py` when prompted).

Everything else, the agent handles.
