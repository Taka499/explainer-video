# explainer-video

A [Claude Code](https://code.claude.com) skill that turns a topic into a 3Blue1Brown-style explainer video. It produces [Manim Community Edition](https://www.manim.community) animation, an [ElevenLabs](https://elevenlabs.io) voiceover and SRT subtitles. You type `/explainer-video how OAuth works`. A background agent produces the video while you keep working, and the finished files land in `~/Movies/explainers/`.

It runs on macOS only: the API key lives in the macOS Keychain, and the scripts rely on Homebrew, MacTeX and Apple's Python.

## What you get

Each run creates its own directory, `~/Movies/explainers/<slug>/`, where `<slug>` comes from the topic (`how-oauth-works`):

| File | What it is |
|---|---|
| `<slug>.mp4` | The video, 1080p at 60 fps, with the voiceover |
| `<slug>.srt` | Subtitles timed to the voiceover |
| `storyboard.md` | The scene plan |
| `narration.json` | The script, one entry per spoken line |
| `scenes.py` | The Manim scenes, so you can edit and re-render them |
| `build/` | Renders, per-scene timelines and preview stills |

Synthesized audio is cached in `~/Movies/explainers/.cache/tts/`. Re-running a video costs no ElevenLabs credits for lines that have not changed.

## How it works

The skill keeps the expensive work out of your Claude Code session:

1. **Preflight, in your session** (`skill/SKILL.md`). It checks the toolchain and the ElevenLabs key, then writes a brief: topic, audience, length, narration language.
2. **Production, in one background subagent** (`skill/references/production.md`). The subagent works through these steps:
   - It plans a teaching arc and a storyboard, and writes the narration.
   - `tts.py` synthesizes each line and returns ElevenLabs' character-level timing.
   - It writes Manim scenes timed against that audio, renders them at low quality and checks still frames for layout problems.
   - It renders at 1080p60 and joins the scenes with the voiceover and subtitles.
3. **Report.** Your session receives only the subagent's short report: file paths, length, credits used, and any problems. You can keep working while the video is made.

The design and the reasons behind it are in `docs/plans/0001-explainer-video-skill.md`.

## Requirements

- macOS with [Homebrew](https://brew.sh)
- Claude Code, with its [sandbox](https://code.claude.com/docs/en/sandboxing) enabled
- [uv](https://docs.astral.sh/uv/), ffmpeg, pkgconf, cairo, and MacTeX (`mactex-no-gui`). `toolchain.sh check` prints the install command for anything that is missing.
- An ElevenLabs account and API key. See [ElevenLabs credits and voices](#elevenlabs-credits-and-voices) for what the free plan allows.

## Setup

Once per machine:

1. Clone this repository, then run `./install.sh` in your own terminal. Inside the Claude Code sandbox, `~/.claude/skills` cannot be written. The script links `skill/` to `~/.claude/skills/explainer-video`. If something else is already at that path, it refuses rather than overwrite it.
2. Install the system tools:

   ```bash
   brew install ffmpeg pkgconf cairo && brew install --cask mactex-no-gui
   ```

3. Store the ElevenLabs key in the Keychain, in your own terminal. The command prompts for the key without echoing it, so the key never appears in a chat or a shell history:

   ```bash
   security add-generic-password -a "$USER" -s elevenlabs-api-key -w
   ```

4. In `~/.claude/settings.json`, let the sandbox write the output directory, and allow `tts.py` to run. `install.sh` prints the exact rule strings for your paths:

   ```text
   sandbox.filesystem.allowWrite  += "~/Movies/explainers"
   permissions.allow              += "Bash(<home>/.claude/skills/explainer-video/scripts/tts.py *)"
                                     "Bash(<this repository>/skill/scripts/tts.py *)"
   ```

5. Build the Manim environment and check everything:

   ```bash
   ~/.claude/skills/explainer-video/scripts/toolchain.sh setup
   ~/.claude/skills/explainer-video/scripts/toolchain.sh check
   ```

   `setup` installs Manim 0.22.0 into its own virtual environment, `~/Movies/explainers/.toolchain/venv`.

On the first run, the skill asks for an ElevenLabs voice ID. In the ElevenLabs app, open Voices, pick a voice and use "Copy voice ID". The skill saves it in `~/Movies/explainers/config.json` as `{"voice_id": "…", "model_id": "eleven_multilingual_v2"}`.

## Usage

```text
/explainer-video <topic> [for <audience>] [<N> seconds] [in <language>]
```

For example, `/explainer-video how OAuth works`, or `/explainer-video eigenvectors for programmers 90 seconds in Japanese`.

When the request leaves something out, these defaults apply:

| Field | Default |
|---|---|
| audience | a curious adult with high-school maths |
| length | 60 seconds |
| narration language | the language the request is written in |

Technical terms on screen stay in English in every narration language. The Claude Code sandbox blocks `open`, so watch the result from your own terminal, or type `! open "<video path>"` in Claude Code.

## ElevenLabs credits and voices

- With `eleven_multilingual_v2`, ElevenLabs bills one credit per character. A 60-second video needs roughly 650 to 850 credits: the first video, on OAuth, used 846.
- If you set a credit limit on the API key, leave headroom above that. Otherwise synthesis stops with `quota_exceeded`.
- The free plan cannot use Voice Library voices through the API (`HTTP 402 … Free users cannot use library voices via the API`). Pick one of ElevenLabs' default voices or a voice you created yourself.
- Never use a cloned voice of a real person.
- A key limited to text-to-speech, with a credit cap, is enough.

## Security model

`tts.py` is the one command that runs outside the Claude Code sandbox. It has to, because the sandbox cannot read the Keychain. It runs under the allow rule from step 4, with no prompt, so it is written to resist whatever a sandboxed command could plant under `~/Movies/explainers`:

- Every read and write goes through a pinned directory descriptor (`SafeDir`). That refuses links at any level and directories outside the output root.
- Temporary files get random names, created with `O_EXCL`.
- No subprocess is handed a path under the output root.
- The key is never printed, logged, cached or written.

`tts.py` also refuses `--check` inside the sandbox, because there a stored key would look missing. The reasoning, and the review findings behind it, are in `docs/plans/0001-explainer-video-skill.md` § Implementation and `docs/plans/0002-confined-reads-and-readme.md`.

## Development

```text
skill/                 the installed skill (install.sh links it)
  SKILL.md             main-session preflight, brief, launch, report
  references/production.md   the subagent's manual
  scripts/tts.py       ElevenLabs synthesis; standard library only, runs on /usr/bin/python3 3.9
  scripts/voice.py     narration timing for scenes, scene join and SRT; never imports Manim
  scripts/toolchain.sh check | setup | run
tests/                 pytest suite for tts.py and voice.py
docs/plans/            one short plan per change: goal, changes, decision log, verification
```

Run the tests:

```bash
UV_CACHE_DIR="$PWD/.uv-cache" uv run --group dev pytest -q
UV_CACHE_DIR="$PWD/.uv-cache" uv run --no-project --python /usr/bin/python3 --with pytest pytest -q -p no:cacheprovider tests/test_tts.py
```

The second command checks that `tts.py` still runs under Apple's Python 3.9. Inside the sandbox, `UV_CACHE_DIR` must point somewhere writable, because uv's default cache directory is write-denied there. Four tests need ffmpeg.

Every script reads `EXPLAINER_HOME` in place of `~/Movies/explainers`, so tests and trial runs can use a scratch directory.
