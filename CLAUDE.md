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

<!-- Describe the high-level structure: key directories, how components relate, data flow. Name the main entry points. -->

## Setup and Development

<!-- How to install dependencies, configure the environment, and run the project locally. Include exact commands. -->

## Build and Test

<!-- List the commands to build, test, lint, and format. Show expected output for a clean run. -->

## Code Style

<!-- Language-specific conventions, naming patterns, import ordering, or formatting tools in use. -->

## Commit Discipline

- Follow Git-flow workflow to manage the branches
- Use small, frequent commits rather than large, infrequent ones
- Only add and commit affected files; leave untracked files as they are
- Never add coding agent attribution in commits

