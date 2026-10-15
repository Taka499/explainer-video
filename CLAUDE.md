# CLAUDE.md

This file provides guidance to a coding agent when working with code in this repository.

## Central Configuration

This repository was scaffolded from `Taka499/project-template` without its `docs/PLANS.md` (see § Plans). It carries only the project-scoped scaffold: this CLAUDE.md and `docs/adr/README.md` (ADR convention). Cross-project rules and the generic workflow skills (`grill-me`, `codebase-design`, `adopt-from-sibling`, `harvest-session`, `backlog`) live in the central `Taka499/claude` repository, deployed user-level at `~/.claude` — do not copy them into projects.

## Documentation

Project documentation lives in `docs/`. When creating or updating plans, ExecPlans, or design docs, save them there. Reference existing docs in `docs/` for context on project phases and milestones.

Every document should be self-sufficient: the reader should never need to hunt for context. Explain concepts inline. When a concept is already defined in another checked-in document, you may reference it by file path and section rather than repeating it — but the reference must be precise enough that the reader can find it immediately (e.g., "see `docs/plans/0001-explainer-video-skill.md` § Decision Log"), not vague ("see the architecture doc").

## Plans

This repository uses short plan files, not ExecPlans: the user chose "simple is enough" when it was scaffolded (2026-10-15), and this overrides the global rule that complex features use an ExecPlan. A non-trivial change gets `docs/plans/NNNN-<slug>.md` with four sections — Goal, Changes, Decision Log, Verification — in roughly 40–80 lines, written so a fresh session can continue from it alone. Decisions made in conversation go into the current plan's Decision Log the moment they are made. A merged plan is history: correct it with a new plan or an ADR, never by editing it.

## Durable Decisions (ADRs)

Cross-plan decisions live in `docs/adr/` (convention: `docs/adr/README.md`). This list is an index, not a home — one line per active decision with a pointer to its ADR; full context, provenance, and lifecycle live in the ADR file. Every non-obvious claim elsewhere in this file must cite its source (`per docs/adr/NNNN` or `per docs/plans/<plan>.md`) so this snapshot stays auditable and rebuildable. When an ADR corrects something an old plan asserted, the ADR wins.

<!-- One line per accepted ADR, e.g.:
- `docs/adr/0001-example.md` — **accepted**: one-sentence statement of the decision.
-->

## Project Overview

A Claude Code user-level skill, `explainer-video`, that turns a topic into a 3Blue1Brown-style explainer video: Manim Community Edition animation with an ElevenLabs voiceover and SRT subtitles, finished files in `~/Movies/explainers/`. The skill does a short preflight in the main session (API key, toolchain, brief) and then hands the whole production to one background subagent, so the main session's context only receives the final report. The repository holds the skill and an install script that links it into `~/.claude/skills/explainer-video`. Design and the decisions behind it: `docs/plans/0001-explainer-video-skill.md`.

## Architecture

`skill/` is the whole installed skill. `install.sh` links it to `~/.claude/skills/explainer-video`.

- `skill/SKILL.md` runs in the main session: preflight, then the brief, then the launch of one background `general-purpose` Agent, then the report.
- `skill/references/production.md` is the subagent's manual: storyboard → `narration.json` → `tts.py` → `scenes.py` → low-quality render and still-frame check → final render → `voice.py assemble`.
- `skill/scripts/tts.py` uses only the standard library and must run on Apple's `/usr/bin/python3` 3.9. It reads the key from the Keychain and calls ElevenLabs `POST /v1/text-to-speech/{voice_id}/with-timestamps`. It caches `<hash>.mp3` and `<hash>.json` (character timing) under `~/Movies/explainers/.cache/tts/`, and writes `voice_manifest.json` beside the narration. It is the only command that runs outside the sandbox (per `docs/plans/0001-explainer-video-skill.md` § Decision Log). It still writes where sandboxed commands can plant symlinks, so all of its writes go through `SafeDir`. `SafeDir` refuses any directory outside the output root, opens each path component with `O_NOFOLLOW`, creates temp files with random names and `O_EXCL`, and renames them in place. Keep every new write on that path.
- `skill/scripts/voice.py` is copied into each work directory and imported by the scenes. `narration.say(scene, id)` adds the audio and records its start in `build/timeline/<Scene>.json`; `assemble` joins the scene files and writes the SRT from those timelines. It never imports Manim, so its rules test without it.
- `skill/scripts/toolchain.sh` has three commands. `check` is the preflight. `setup` builds the Manim 0.22.0 venv in `~/Movies/explainers/.toolchain/venv`. `run` sets `PATH`, `XDG_CACHE_HOME` and `TEXMFVAR` so Manim and TeX work inside the sandbox.

The output root `~/Movies/explainers` is overridable with `EXPLAINER_HOME` in every script, so tests and trials can point at a scratch directory.

## Setup and Development

The user does this once:

1. `brew install pkgconf && brew install --cask mactex-no-gui`.
2. Store the key, in the user's own terminal: `security add-generic-password -a "$USER" -s elevenlabs-api-key -w`.
3. In `~/.claude/settings.json`, add `"~/Movies/explainers"` to `sandbox.filesystem.allowWrite`, and allow `tts.py` in `permissions.allow`. `install.sh` prints the exact rule strings.
4. Run `./install.sh` outside the sandbox, then `~/.claude/skills/explainer-video/scripts/toolchain.sh setup` and `… check`.

After that, `/explainer-video <topic>` runs the skill.

## Build and Test

```bash
UV_CACHE_DIR="$PWD/.uv-cache" uv run --group dev pytest -q      # 115 passed; three tests need ffmpeg
```

Inside the sandbox, `UV_CACHE_DIR` must point somewhere writable: uv's default `~/.cache/uv` is write-denied there. The repo-local `.uv-cache/` is gitignored. On 2026-10-15 the shared `$TMPDIR/uv-cache` held a `pytest` entry whose files had disappeared, which made installs fail with `failed to open file … WHEEL`.

`tts.py` must also pass under Apple's Python 3.9:

```bash
UV_CACHE_DIR="$PWD/.uv-cache" uv run --no-project --python /usr/bin/python3 --with pytest pytest -q -p no:cacheprovider tests/test_tts.py
```

## Code Style

Python. Pure rules sit at the top of each script and I/O sits at the edges. Dependencies (subprocess runner, URL opener, sleep, the sandbox flag) are passed in as arguments so that the tests can replace them.

## Commit Discipline

- Follow Git-flow workflow to manage the branches
- Use small, frequent commits rather than large, infrequent ones
- Only add and commit affected files; leave untracked files as they are
- Never add coding agent attribution in commits

