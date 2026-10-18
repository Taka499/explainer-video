# 0002 — Confined reads in tts.py, and a README

## Goal

On 2026-10-18 a `/codex-review` covered the merged skill, PR #1 (`b06b3bc...2a8b225`). It found that `tts.py` confined its writes to the output root but not its reads. `tts.py` runs outside the sandbox, under a permission allow rule with no prompt, while sandboxed commands can write anywhere under `~/Movies/explainers`, including links. This plan closes that gap, and adds a README for someone arriving at the repository.

## Changes

- `load_config` reads `config.json` through `SafeDir` on the root, with `O_NOFOLLOW`. A link or any other non-regular file there is refused with "does not exist or is not a regular file".
- `synthesize` opens the work directory through `SafeDir` first and reads `narration.json` through it, so a narration outside the root is refused before any of it is read. Before, two leaks were possible:
  - The content of an outside file reached the parser, whose errors echo field values (`got 'SECRET VALUE'` in the test).
  - A narration linked to an outside file was synthesized and sent to ElevenLabs.
- `probe_duration` takes the audio bytes and runs `ffprobe` on a private copy in `tempfile.TemporaryDirectory()`. Before, `ffprobe` got `<root>/.cache/tts/<hash>.mp3`. A sandboxed process could redirect that by swapping `.cache` for a link after `SafeDir`'s walk.
- `SafeDir`'s docstring and messages now cover reads as well as writes. CLAUDE.md § Architecture states the rule: every read and write goes through `SafeDir`, and no subprocess gets a path under the root.
- `README.md`: what the skill does, what it needs, setup, usage, where files go, development.
- Four new tests in `tests/test_tts.py`, each seen failing on the old code.

## Decision Log (session 2026-10-18)

- **Rejected Codex's high-rated finding** that the printed allow rule `Bash(<skill>/scripts/tts.py *)` lets `tts.py --check; <anything>` run unsandboxed. Claude Code's permissions documentation (https://code.claude.com/docs/en/permissions, read 2026-10-18) says otherwise:
  - § Compound commands: it splits a command on `&&`, `||`, `;`, `|`, `|&`, `&` and newlines, and an allow rule must match each subcommand.
  - § Redirections: a redirect target is checked against the file rules.
  - § Wrappers: an allow rule does not match past a leading assignment of an unknown variable, such as `EXPLAINER_HOME=…`.

  What the `*` does admit is any argument to `tts.py` itself. That is why a narration path is now checked before it is read.
- **`ffprobe` gets a temp copy, not a pipe.** From stdin, `ffprobe` reports the duration as `N/A` (measured on three cached segments). The copy lives in a fresh `mkdtemp` directory under the unsandboxed `$TMPDIR` (`/var/folders/…/T`, per plan 0001 § Measured), which sandboxed commands cannot write.
- **Not addressed: a FIFO in place of a file.** `O_NOFOLLOW` refuses links but still opens a FIFO. `is_file` checks guard `config.json` and cache entries, but two cases remain: a swap between the check and the open, and a FIFO at `narration.json`. Either would make `tts.py` block. That is a hang, not a read outside the root, so it is recorded here rather than fixed.

## Verification

- `uv run --group dev pytest -q`: 121 passed. `tests/test_tts.py` under `/usr/bin/python3` 3.9: 68 passed.
- Checks on the real output root:
  - `tts.py` on the cached OAuth narration (12 cached, 0 credits billed) rewrote an identical `voice_manifest.json`.
  - `tts.py --check` read the real `config.json`.
  - `probe_duration` on a cached clip's bytes returned 3.668753 s, the same as `ffprobe` on the file.
- Codex re-review of this change (2026-10-18, uncommitted diff, snapshot): `CODEX VERDICT: LGTM`, no findings. Codex also reported that the four new tests fail against the previous `HEAD`.
