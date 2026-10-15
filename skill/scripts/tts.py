#!/usr/bin/env python3
"""ElevenLabs voiceover for the explainer-video skill, cached by content hash.

    tts.py --check
        Report whether the API key and the voice config are present. Never prints the key.
    tts.py /abs/path/narration.json
        Synthesize every segment that is not cached yet and write voice_manifest.json
        beside narration.json. A segment already in the cache costs no credits.

Run it outside the Claude Code sandbox. The key lives in the macOS Keychain
(service "elevenlabs-api-key", account $USER), which the sandbox cannot read. Inside it,
a Keychain lookup reports "not found" even when the item exists, so this script refuses
to answer there rather than misreport.

The output root defaults to ~/Movies/explainers (override: EXPLAINER_HOME). It holds
config.json (the voice) and .cache/tts/<hash>.mp3 + <hash>.json (audio and character
timing). Standard library only, Python 3.9+ (it may run under the system python3).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

KEYCHAIN_SERVICE = "elevenlabs-api-key"
API_BASE = "https://api.elevenlabs.io"
DEFAULT_MODEL = "eleven_multilingual_v2"
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"
SECURITY_NOT_FOUND = 44  # exit status of `security find-generic-password` for a missing item
SEGMENT_ID = re.compile(r"^[A-Za-z0-9_-]+$")
RETRY_STATUSES = (429, 500, 502, 503, 504)
USER_AGENT = "explainer-video-tts/0.1"


class UserError(Exception):
    """A problem the user or the calling agent can fix; printed without a traceback."""


@dataclass(frozen=True)
class Config:
    voice_id: str
    model_id: str = DEFAULT_MODEL
    output_format: str = DEFAULT_OUTPUT_FORMAT
    voice_settings: Optional[dict] = None


@dataclass(frozen=True)
class Segment:
    id: str
    text: str


# ---------- pure rules ----------


def parse_config(raw: Any) -> Config:
    if not isinstance(raw, dict):
        raise UserError("config.json must hold a JSON object")
    voice_id = raw.get("voice_id")
    if not isinstance(voice_id, str) or not voice_id.strip():
        raise UserError('config.json has no "voice_id"')
    settings = raw.get("voice_settings")
    if settings is not None and not isinstance(settings, dict):
        raise UserError('"voice_settings" in config.json must be an object')
    return Config(
        voice_id=voice_id.strip(),
        model_id=raw.get("model_id") or DEFAULT_MODEL,
        output_format=raw.get("output_format") or DEFAULT_OUTPUT_FORMAT,
        voice_settings=settings,
    )


def parse_narration(raw: Any) -> list[Segment]:
    if not isinstance(raw, dict) or not isinstance(raw.get("segments"), list):
        raise UserError('narration.json must be an object with a "segments" list')
    segments: list[Segment] = []
    seen: set[str] = set()
    for i, item in enumerate(raw["segments"]):
        if not isinstance(item, dict):
            raise UserError(f"segment {i} is not an object")
        seg_id, text = item.get("id"), item.get("text")
        if not isinstance(seg_id, str) or not SEGMENT_ID.match(seg_id):
            raise UserError(f"segment {i}: id must match {SEGMENT_ID.pattern}, got {seg_id!r}")
        if seg_id in seen:
            raise UserError(f"segment id {seg_id!r} appears twice")
        if not isinstance(text, str) or not text.strip():
            raise UserError(f"segment {seg_id!r} has no text")
        seen.add(seg_id)
        segments.append(Segment(seg_id, text.strip()))
    if not segments:
        raise UserError("narration.json has no segments")
    return segments


def request_body(text: str, config: Config) -> dict:
    body: dict = {"text": text, "model_id": config.model_id}
    if config.voice_settings is not None:
        body["voice_settings"] = config.voice_settings
    return body


def cache_key(config: Config, body: dict) -> str:
    """Hash of everything that changes the audio: the request body, the voice and the format."""
    material = {"voice_id": config.voice_id, "output_format": config.output_format, "body": body}
    canonical = json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def parse_response(payload: Any) -> tuple[bytes, dict, str]:
    """Return (audio bytes, alignment, which alignment field it came from)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("audio_base64"), str):
        raise UserError("ElevenLabs response has no audio_base64")
    audio = base64.b64decode(payload["audio_base64"])
    for source in ("alignment", "normalized_alignment"):
        alignment = payload.get(source)
        if alignment:
            chars = alignment.get("characters")
            starts = alignment.get("character_start_times_seconds")
            ends = alignment.get("character_end_times_seconds")
            if not (isinstance(chars, list) and isinstance(starts, list) and isinstance(ends, list)):
                raise UserError(f"ElevenLabs {source} is missing a field")
            if not (len(chars) == len(starts) == len(ends)) or not chars:
                raise UserError(f"ElevenLabs {source} has arrays of different lengths")
            return audio, {
                "characters": chars,
                "character_start_times_seconds": starts,
                "character_end_times_seconds": ends,
            }, source
    raise UserError("ElevenLabs response has no character alignment")


def billed_characters(headers: dict, text: str) -> int:
    """Credits charged, from the response header; the docs name it two ways, else count characters."""
    lowered = {k.lower(): v for k, v in headers.items()}
    for name in ("character-cost", "x-character-count"):
        value = lowered.get(name)
        if value is not None and str(value).strip().isdigit():
            return int(value)
    return len(text)


def keychain_status(returncode: int) -> str:
    if returncode == 0:
        return "present"
    if returncode == SECURITY_NOT_FOUND:
        return "missing"
    return "unreadable"


def error_message(status: int, body: bytes) -> str:
    """ElevenLabs error text without echoing anything we sent."""
    detail: Any = None
    try:
        detail = json.loads(body.decode("utf-8")).get("detail")
    except (ValueError, AttributeError):
        pass
    if isinstance(detail, dict):
        text = " ".join(str(detail.get(k)) for k in ("status", "message") if detail.get(k))
    elif isinstance(detail, list):
        text = "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail)
    else:
        text = body.decode("utf-8", "replace")[:300]
    hint = {401: " (key rejected or lacks the text_to_speech permission)",
            402: " (out of credits?)", 429: " (rate limited)"}.get(status, "")
    return f"HTTP {status}{hint}: {text}".rstrip(": ")


# ---------- I/O at the edges ----------

Runner = Callable[..., subprocess.CompletedProcess]


def output_root() -> Path:
    return Path(os.environ.get("EXPLAINER_HOME") or Path.home() / "Movies" / "explainers")


def in_sandbox(environ: Optional[dict] = None) -> bool:
    return bool((os.environ if environ is None else environ).get("SANDBOX_RUNTIME"))


def keychain_lookup(run: Runner = subprocess.run, reveal: bool = False) -> subprocess.CompletedProcess:
    """`security find-generic-password`; without -w it prints attributes only, never the secret."""
    cmd = ["security", "find-generic-password", "-a", os.environ.get("USER", ""), "-s", KEYCHAIN_SERVICE]
    if reveal:
        cmd.append("-w")
    return run(cmd, capture_output=True, text=True)


def read_key(run: Runner = subprocess.run) -> str:
    result = keychain_lookup(run, reveal=True)
    status = keychain_status(result.returncode)
    if status != "present" or not result.stdout.strip():
        raise UserError(f"ElevenLabs key {status} in the Keychain (service {KEYCHAIN_SERVICE})")
    return result.stdout.strip()


def load_config(root: Path) -> Config:
    path = root / "config.json"
    if not path.is_file():
        raise UserError(f"{path} does not exist; it needs at least {{\"voice_id\": \"...\"}}")
    try:
        return parse_config(json.loads(path.read_text(encoding="utf-8")))
    except ValueError as exc:
        raise UserError(f"{path} is not valid JSON: {exc}") from exc


def post_tts(key: str, config: Config, body: dict,
             opener: Callable[..., Any] = urllib.request.urlopen,
             sleep: Callable[[float], None] = time.sleep) -> tuple[Any, dict]:
    url = (f"{API_BASE}/v1/text-to-speech/{config.voice_id}/with-timestamps"
           f"?output_format={config.output_format}")
    data = json.dumps(body).encode("utf-8")
    for attempt in (1, 2):
        request = urllib.request.Request(url, data=data, method="POST", headers={
            "xi-api-key": key, "Content-Type": "application/json",
            "Accept": "application/json", "User-Agent": USER_AGENT})
        try:
            with opener(request, timeout=180) as response:
                return json.loads(response.read().decode("utf-8")), dict(response.headers)
        except urllib.error.HTTPError as exc:
            if attempt == 1 and exc.code in RETRY_STATUSES:
                retry_after = (exc.headers or {}).get("Retry-After", "")
                sleep(float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 5.0)
                continue
            raise UserError(error_message(exc.code, exc.read())) from None
        except urllib.error.URLError as exc:
            raise UserError(f"cannot reach {API_BASE}: {exc.reason}") from None
    raise AssertionError("unreachable")


def probe_duration(path: Path, run: Runner = subprocess.run) -> float:
    result = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                  "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                 capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise UserError(f"ffprobe could not read the duration of {path}: {result.stderr.strip()}") from None


class SafeDir:
    """A directory under the output root, opened one component at a time without following symlinks.

    tts.py runs outside the sandbox, yet it writes where sandboxed commands can plant links.
    Every write therefore goes through this pinned descriptor. A link at any level is refused,
    and temp files get unpredictable names created with O_EXCL. A planted link can never
    redirect a write outside the output root. The root itself is trusted: its parent
    (~/Movies) is not writable inside the sandbox, so the root cannot be swapped for a link.
    """

    def __init__(self, fd: int, path: Path):
        self.fd, self.path = fd, path

    @classmethod
    def open(cls, root: Path, directory: Path, create: bool = False) -> "SafeDir":
        root_real = Path(os.path.realpath(root))
        real = Path(os.path.realpath(directory))
        try:
            parts = real.relative_to(root_real).parts
        except ValueError:
            raise UserError(f"{directory} is outside {root_real}; tts.py writes only under the output root") from None
        fd = os.open(root_real, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in parts:
                if create:
                    try:
                        os.mkdir(part, 0o755, dir_fd=fd)
                    except FileExistsError:
                        pass
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
        except OSError as exc:
            os.close(fd)
            raise UserError(f"refusing to write in {directory}: {exc.strerror} at {part!r} "
                            "(a symlink, or not a directory)") from None
        return cls(fd, real)

    def __enter__(self) -> "SafeDir":
        return self

    def __exit__(self, *exc) -> None:
        os.close(self.fd)

    def is_file(self, name: str) -> bool:
        try:
            return stat.S_ISREG(os.stat(name, dir_fd=self.fd, follow_symlinks=False).st_mode)
        except OSError:
            return False

    def read(self, name: str) -> bytes:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        with os.fdopen(fd, "rb") as f:
            return f.read()

    def write(self, name: str, data: bytes) -> None:
        """Write a temp file, then rename it over `name`. The rename replaces a link there, never follows it."""
        tmp = f".{name}.{secrets.token_hex(8)}.part"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=self.fd)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=self.fd)
            except OSError:
                pass
            raise


def cached_entry(cache: SafeDir, key: str) -> Optional[dict]:
    """The timing record, if both files exist; the JSON is written last and marks the entry complete."""
    if not (cache.is_file(f"{key}.json") and cache.is_file(f"{key}.mp3")):
        return None
    try:
        return json.loads(cache.read(f"{key}.json").decode("utf-8"))
    except (OSError, ValueError):
        return None


def synthesize(narration_path: Path, root: Path, *, run: Runner = subprocess.run,
               opener: Callable[..., Any] = urllib.request.urlopen,
               sleep: Callable[[float], None] = time.sleep,
               sandboxed: Optional[bool] = None, out=None) -> dict:
    out = sys.stdout if out is None else out
    if not narration_path.is_absolute():
        raise UserError("pass narration.json as an absolute path ($TMPDIR differs inside and outside the sandbox)")
    try:
        raw = json.loads(narration_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UserError(f"cannot read {narration_path}: {exc}") from exc
    segments = parse_narration(raw)
    config = load_config(root)
    sandboxed = in_sandbox() if sandboxed is None else sandboxed
    with SafeDir.open(root, narration_path.parent) as workdir, \
            SafeDir.open(root, root / ".cache" / "tts", create=True) as cache:
        summary = _synthesize_into(segments, raw, config, workdir, cache, run=run, opener=opener,
                                   sleep=sleep, sandboxed=sandboxed, out=out)
    print(json.dumps(summary, ensure_ascii=False), file=out)
    return summary


def _synthesize_into(segments: list[Segment], raw: dict, config: Config, workdir: SafeDir, cache: SafeDir,
                     *, run: Runner, opener: Callable[..., Any], sleep: Callable[[float], None],
                     sandboxed: bool, out) -> dict:
    key: Optional[str] = None
    entries, new, billed = [], 0, 0
    for seg in segments:
        body = request_body(seg.text, config)
        digest = cache_key(config, body)
        entry = cached_entry(cache, digest)
        state = "cached"
        if entry is None:
            if key is None:
                if sandboxed:
                    raise UserError("segments need synthesis and the Keychain is unreadable inside the "
                                    "sandbox: run this command with dangerouslyDisableSandbox: true")
                key = read_key(run)
            payload, headers = post_tts(key, config, body, opener=opener, sleep=sleep)
            audio, alignment, source = parse_response(payload)
            cache.write(f"{digest}.mp3", audio)
            cost = billed_characters(headers, seg.text)
            entry = {
                "key": digest, "text": seg.text, "voice_id": config.voice_id,
                "model_id": config.model_id, "output_format": config.output_format,
                "voice_settings": config.voice_settings,
                "duration": probe_duration(cache.path / f"{digest}.mp3", run),
                "alignment_source": source, "alignment": alignment,
                "billed_characters": cost,
                "request_id": {k.lower(): v for k, v in headers.items()}.get("request-id"),
            }
            cache.write(f"{digest}.json", json.dumps(entry, ensure_ascii=False, indent=1).encode("utf-8"))
            new += 1
            billed += cost
            state = "new"
        entries.append({"id": seg.id, "text": seg.text, "duration": entry["duration"],
                        "audio": str(cache.path / f"{digest}.mp3"),
                        "timing": str(cache.path / f"{digest}.json")})
        print(f"{seg.id:<12} {state:<7} {entry['duration']:6.2f}s", file=out)

    manifest = {"language": raw.get("language"), "voice_id": config.voice_id,
                "model_id": config.model_id, "segments": entries}
    workdir.write("voice_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"))
    return {"segments": len(entries), "synthesized": new, "cached": len(entries) - new,
            "audio_seconds": round(sum(e["duration"] for e in entries), 2),
            "billed_characters": billed, "manifest": str(workdir.path / "voice_manifest.json")}


def check(root: Path, *, run: Runner = subprocess.run, sandboxed: Optional[bool] = None,
          out=None) -> int:
    out = sys.stdout if out is None else out
    sandboxed = in_sandbox() if sandboxed is None else sandboxed
    if sandboxed:
        print("key:     unknown - the Keychain is unreadable inside the sandbox; "
              "run this command with dangerouslyDisableSandbox: true", file=out)
        return 2
    ok = True
    status = keychain_status(keychain_lookup(run).returncode)
    print(f"key:     {status} (Keychain service {KEYCHAIN_SERVICE}, account {os.environ.get('USER', '')})",
          file=out)
    if status != "present":
        ok = False
        print('fix:     in your own terminal: security add-generic-password -a "$USER" '
              f"-s {KEYCHAIN_SERVICE} -w   (it prompts for the key without echo)", file=out)
    try:
        config = load_config(root)
        print(f"voice:   {config.voice_id} (model {config.model_id}, {config.output_format})", file=out)
    except UserError as exc:
        ok = False
        print(f"voice:   missing - {exc}", file=out)
    ffprobe = shutil.which("ffprobe")
    print(f"ffprobe: {ffprobe or 'missing - brew install ffmpeg'}", file=out)
    return 0 if ok and ffprobe else 1


def main(argv: list[str]) -> int:
    root = output_root()
    try:
        if argv == ["--check"]:
            return check(root)
        if len(argv) == 1 and not argv[0].startswith("-"):
            synthesize(Path(argv[0]), root)
            return 0
        print(__doc__.strip(), file=sys.stderr)
        return 64
    except UserError as exc:
        print(f"tts.py: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
