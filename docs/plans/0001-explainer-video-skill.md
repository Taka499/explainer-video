# Plan 0001: the `explainer-video` skill

## Goal

`/explainer-video <topic>` produces a 3Blue1Brown-style explainer video: Manim Community Edition animation, an ElevenLabs voiceover, and SRT subtitles, written to `~/Movies/explainers/<slug>/`. The production runs in one background subagent, so the main session holds only a short preflight and the final report (paths, length, credits used). Done when the skill installs with one script, a 30–60 second topic renders end to end with nobody watching, and the user has watched the result.

## Shape

1. **Main session** (`skill/SKILL.md`). Preflight first. Is the ElevenLabs key in Keychain? That check runs `tts.py --check` outside the sandbox and never prints the key. Is the toolchain there: LaTeX (`latex`, `xelatex`, `dvisvgm`), `ffmpeg`, `pkg-config`, and the Manim venv? Anything missing → print the exact command for the user to run, then stop. Then the brief: topic, audience, length and narration language, taken from the request with defaults. Ask only when the topic itself is unclear. Then launch one background Agent with a self-contained brief: it sees none of the conversation, so the brief must carry everything, including the resolved skill directory.
2. **Subagent** (`skill/references/production.md`). Storyboard → narration split into segments (`narration.json`) → one `tts.py` run outside the sandbox → scene code → a low-quality render of each scene, with still frames read back as images to catch overlapping text, objects out of frame, and animation out of step with the narration → fix → final render → join the scenes, write the SRT → report.
3. **Audio before animation.** `tts.py` (stdlib only) sends each segment to `POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps`. It stores `<hash>.mp3` plus `<hash>.json` holding the character alignment (`characters`, `character_start_times_seconds`, `character_end_times_seconds`). The hash covers text, voice, model and voice settings. Scenes read durations and word times from that cache offline, through a small helper copied into the work directory. A re-render therefore costs no credits.

## Changes

- `skill/SKILL.md`: preflight, brief, subagent launch, report. `disable-model-invocation: true`.
- `skill/references/production.md`: the subagent's instructions: teaching arc, storyboard, narration, Manim conventions, render-check loop, final assembly.
- `skill/scripts/tts.py`: Keychain read, ElevenLabs call, cache, `--check`. `skill/scripts/voice.py`: the Manim-side helper that reads the cache.
- `tests/`: unit tests for the pure parts (cache key, alignment → word times, SRT formatting, segment timing).
- `install.sh`: links `skill/` to `~/.claude/skills/explainer-video`, and refuses if something that is not that link already sits there. It runs outside the sandbox, because `~/.claude/skills` is write-denied inside it. A toolchain setup script creates the Manim venv under `~/Movies/explainers/.toolchain/`.

## Decision Log (session 2026-10-15)

- **A separate repository, not `Taka499/claude/skills/`.** The user: "create a separate repository that install the skill to .claude".
- **Short plans, no `docs/PLANS.md`.** The user: "Simple is enough" (recorded in `CLAUDE.md` § Plans).
- **Key in macOS Keychain** (service `elevenlabs-api-key`, account `$USER`), read only by `tts.py` running outside the sandbox. The user picked this over an env var in `~/.zshrc` and a `chmod 600` file: both of those can be read by every sandboxed command, while the sandbox already denies the Keychain, `.env` files and the `GITHUB_TOKEN`/`NPM_TOKEN` env vars. Cost: one unsandboxed step per video. An allow rule for exactly that command keeps a run nobody is watching from stalling on its prompt. Running outside the sandbox also means `api.elevenlabs.io` needs no `allowedDomains` entry.
- **The key never passes through the chat.** Setup is a command the user runs in their own terminal: `security add-generic-password -a "$USER" -s elevenlabs-api-key -w` (it prompts without echo). Recommend a key restricted to text-to-speech, with a credit cap.
- **Fully autonomous: no script-approval checkpoint.** This is the user's choice, made after being told that credits and render time are spent before they see the script.
- **Main-session preflight + an Agent-tool subagent, not `context: fork`.** A forked skill sees only its own text and arguments, and subagents never get `AskUserQuestion`, so a fork could not run the key-setup prompt. Sources: code.claude.com/docs/en/skills.md § Run skills in a subagent; sub-agents.md § AskUserQuestion. Permission prompts from a background subagent do surface in the main session (sub-agents.md § Run subagents in foreground or background).
- **Output goes to `~/Movies/explainers/`.** The user adds a sandbox `allowWrite` rule for it. The audio cache and the toolchain live under it too, so they survive restarts; scratch render files can go in `$TMPDIR`.
- **Full MacTeX** (`brew install --cask mactex-no-gui`) over BasicTeX, so Japanese and Chinese inside TeX work with no package hunting.
- **Manim Community Edition 0.22, not Grant's `manimgl`:** better documented, stabler API.
- **Our own `tts.py`, not `manim-voiceover`'s `ElevenLabsService`.** That plugin, when the key is missing, offers to create a `.env` interactively, which hangs a subagent; `.env` is read-denied by the sandbox anyway. Its default model, `eleven_monolingual_v1`, is English-only. Its timing sync needs a local Whisper model. Our script keeps the key in one place and caches by hash. Default model: `eleven_multilingual_v2`. The voice gets chosen at setup.
- **Style, not assets.** No Pi creatures, 3b1b logo or music (not covered by Manim's licence), and no cloned voices of real people.
- **Technical terms on screen stay in English** in every language: the user's rule from the `explainer` skill.
- **Dropped: a free draft voice (macOS `say`) for previews.** The cache already makes re-renders free.

### Implementation (session 2026-10-15)

- **The voice lives in `~/Movies/explainers/config.json`** (`voice_id`, plus optional `model_id`, `output_format` and `voice_settings`). The preflight asks the user for a voice ID ("Copy voice ID" in the ElevenLabs app) and writes the file. `tts.py` does not list voices, because a key limited to text-to-speech may lack `voices_read`.
- **`tts.py` writes `voice_manifest.json` beside `narration.json`**, with paths into the cache, and `voice.py` reads only that manifest. The hash function therefore exists in one script.
- **`tts.py --check` refuses inside the sandbox, which it detects through `SANDBOX_RUNTIME`.** Measured: a sandboxed `security find-generic-password` returns 44 ("could not be found") for any item, so a sandboxed check would report a stored key as missing. A synthesis run whose segments are all cached needs no key and works sandboxed.
- **Credits come from a response header.** The ElevenLabs docs call it `character-cost` on some pages and `x-character-count` on others. `tts.py` reads either, and falls back to counting characters. The first real call will settle which one it is.
- **Subtitles come from our own timeline, not Manim's `add_subcaption`.** Each `say()` records when its segment starts in its scene, and `assemble` adds the scene offsets. Per-scene SRTs would need merging after the join anyway, and the timeline stays correct when an animation runs past its audio.
- **The venv uses Python 3.13.** Every Manim 0.22.0 dependency has a cp313 macOS arm64 wheel except `pycairo`, which builds from source on any Python version. For the `ar` failure under Measured, `setup` exports `AR="$(xcrun --find ar)"`. Measured: inside the sandbox that command prints the right path despite its cache errors. `toolchain.sh run` points `XDG_CACHE_HOME` and `TEXMFVAR` under `.toolchain/`.
- **`install.sh` prints two spellings of the `tts.py` allow rule**, one with the symlink path and one with its target. It is not known which one `${CLAUDE_SKILL_DIR}` expands to, so the first run will show it, and the other rule can then go.
- **Risk of that allow rule:** it lets `tts.py` run outside the sandbox with no prompt, and its code sits in this git checkout. A background security review flagged this. The mitigation depends on the sandbox write-protecting files that a symlink in `~/.claude` points to. That is checked after install under Verification.

## Measured (2026-10-15, Darwin 25.6, uv 0.12.23, inside the Claude Code sandbox)

- uv's default cache `~/.cache/uv` is write-denied (`Failed to initialize cache`). Set `UV_CACHE_DIR` to a writable path.
- `pycairo` 1.29.2 (a Manim dependency) has no macOS wheel and builds with meson. Inside the sandbox, `ar --version` prints `xcrun` cache errors, because `xcrun` cannot write its cache in the Darwin user temp dir. Meson then fails with `Unknown linker(s): … ['ar'] …`. Setting `AR=/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/ar` gets past it.
- With `AR` set, the next failure is `Pkg-config for machine host machine not found`: the user needs `brew install pkgconf`.
- Present: `ffmpeg`, Homebrew `cairo` and `glib`, Python 3.14.3, uv 0.12.23. Absent: LaTeX, `pango`, `pkg-config`, Manim.
- `$TMPDIR` is `/private/tmp/claude-501` inside the sandbox and `/var/folders/…/T` outside it. A file handed between a sandboxed and an unsandboxed command (the narration for `tts.py`, the audio it writes) needs an explicit absolute path.

## Setup the user does once

1. `brew install pkgconf` and `brew install --cask mactex-no-gui`.
2. The Keychain command above, with the ElevenLabs key.
3. `~/.claude/settings.json`: add `"~/Movies/explainers"` to `sandbox.filesystem.allowWrite`, and add an allow rule for the exact `tts.py` command (its string is fixed once the script exists).
4. `./install.sh`, then the toolchain setup script.

## Verification

- Each unit test is shown to fail when its target is broken (invert or delete the rule, watch it go red, restore).
- `tts.py --check` reports present or missing without printing the key, both with and without the Keychain item.
- One 30–60 second topic runs end to end with no input, and the user watches it. Sampled frames from the final MP4 show no overlapping text.
- After install, a sandboxed write into `skill/` (the symlink's target) is refused. Otherwise the `tts.py` allow rule is an unprompted way out of the sandbox.
- Open, not documented: a runtime limit for subagents, and what happens to a background subagent's permission prompt that nobody answers.
