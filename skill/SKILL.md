---
name: explainer-video
description: Produce a 3Blue1Brown-style explainer video on a topic — Manim Community Edition animation, an ElevenLabs voiceover and SRT subtitles — written to ~/Movies/explainers/<slug>/. A short preflight runs here; the production runs in one background subagent, so this session only receives the final report.
argument-hint: <topic> [for <audience>] [<N> seconds] [in <language>]
disable-model-invocation: true
allowed-tools:
  - Bash(${CLAUDE_SKILL_DIR}/scripts/toolchain.sh check)
  - Bash(${CLAUDE_SKILL_DIR}/scripts/tts.py --check)
---

# Explainer video

The request: $ARGUMENTS

This session does three things: a preflight, a brief, and the launch of one background
subagent that produces the video. The production itself never runs here, so this
context only receives the subagent's short report.

## 1. Preflight

Run both checks, then act on the result:

1. `"${CLAUDE_SKILL_DIR}/scripts/toolchain.sh" check`. It runs inside the sandbox.
2. `"${CLAUDE_SKILL_DIR}/scripts/tts.py" --check`, with `dangerouslyDisableSandbox: true`. It reads
   the macOS Keychain, which the sandbox cannot read, and it never prints the key.

If any line says `MISSING` or `missing`, show the user every fix the two checks printed, as
commands for their own terminal, and stop. Do not try to install anything yourself. Two of
the fixes come with conditions:

- **The ElevenLabs key** must be entered in the user's own terminal with the `security
  add-generic-password … -w` command printed above, which prompts for the key without echoing it. Never ask
  for the key in chat. If the user pastes one anyway, do not use it: tell them to revoke it in the
  ElevenLabs dashboard and create a new one. A key limited to text-to-speech, with a credit cap,
  is enough.
- **The voice** (`voice:   missing`): ask the user for a voice ID. In the ElevenLabs app,
  open Voices, pick a voice and use "Copy voice ID". Do not pick a voice for them, and never use
  a cloned voice of a real person. When they reply, write `~/Movies/explainers/config.json` as
  `{"voice_id": "<id>", "model_id": "eleven_multilingual_v2"}` and run check 2 again.

## 2. Brief

Take every value from the request, and use the default when it gives none. Ask only when
the topic is too vague to storyboard ("math", "something cool").

| Field | Default |
|---|---|
| topic | — (required) |
| audience | a curious adult with high-school maths |
| length | 60 seconds |
| narration language | the language the request is written in |
| notes | anything else the user said about angle, examples or tone |

Technical terms on screen stay in English in every narration language. That is the
user's standing rule, so do not ask about it.

Slug: lowercase ASCII words of the topic joined by `-`, at most 40 characters. If
`~/Movies/explainers/<slug>` exists, append `-2`, `-3`, and so on. Create that directory, and in it write
`brief.json` with the fields above plus `slug`.

## 3. Launch

Launch one Agent, `subagent_type: general-purpose`, in the background. It sees none of this
conversation, so the prompt must carry everything it needs. Fill in the template below
with absolute paths: expand `~` to the real home directory.

```text
Produce an explainer video, fully autonomously: nobody will answer questions while you work.

Brief
- Topic: <topic>
- Audience: <audience>
- Target length: <N> seconds
- Narration language: <language> (technical terms on screen stay in English)
- Notes: <notes or "none">

Paths
- Work directory: <home>/Movies/explainers/<slug>
- Skill directory: ${CLAUDE_SKILL_DIR}

Before anything else, read ${CLAUDE_SKILL_DIR}/references/production.md in full and follow it
step by step. It ends with the report to return.
```

Then tell the user that production is running in the background and they can keep working,
and give the work directory.

## 4. Report

When the subagent finishes, relay its report: the video and subtitle paths, the length, the
credits used, and any problems it lists. Do not paste its full log. The sandbox blocks
`open`, so give the user `! open "<video path>"` to watch the result.

If the subagent failed, say which step failed and quote its error. A re-run costs no credits
for audio that is already cached: `~/Movies/explainers/.cache/tts` keeps every segment that was
synthesized.
