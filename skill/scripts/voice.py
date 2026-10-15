"""Narration timing for Manim scenes, read offline from the manifest tts.py writes.

Copy this file into the video's work directory, beside voice_manifest.json and scenes.py.

In scenes.py:

    from voice import Narration
    narration = Narration.load()

    class Intro(Scene):
        def construct(self):
            with narration.say(self, "s01") as seg:
                self.play(Write(title), run_time=seg.until("derivative"))
                self.play(Create(graph), run_time=2)
            # leaving the block waits out the rest of the audio

Each `say` adds the segment's audio at the current scene time and records that time
in build/timeline/<Scene>.json, which `assemble` uses to place the subtitles:

    python voice.py show [SEGMENT_ID]           durations and word times, to write until() calls
    python voice.py assemble OUT.mp4 A.mp4 B.mp4 ...
        join the rendered scenes (file stem = scene class name) and write OUT.srt

There is no Manim import here: a scene only needs .time, .add_sound() and .wait(),
so the pure parts are tested without Manim.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SENTENCE_END = set(".!?。！？")
LATIN_CUE_CHARS = 42  # one subtitle line
CJK_CUE_CHARS = 20
MIN_CUE_SECONDS = 0.8
CUE_GAP_FILL = 0.6  # gaps shorter than this keep the previous cue on screen
MIN_RUN_TIME = 0.1
DEFAULT_TAIL = 0.3  # silence after each segment, so sentences do not run together


class NarrationError(Exception):
    pass


@dataclass(frozen=True)
class Alignment:
    chars: tuple
    starts: tuple
    ends: tuple

    @classmethod
    def from_json(cls, raw: dict) -> "Alignment":
        return cls(tuple(raw["characters"]), tuple(raw["character_start_times_seconds"]),
                   tuple(raw["character_end_times_seconds"]))

    @classmethod
    def linear(cls, text: str, duration: float) -> "Alignment":
        """Even spacing, for audio that came back without character timing."""
        step = duration / max(len(text), 1)
        return cls(tuple(text), tuple(i * step for i in range(len(text))),
                   tuple((i + 1) * step for i in range(len(text))))

    @property
    def text(self) -> str:
        return "".join(self.chars)


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


# ---------- pure rules ----------


def is_cjk(ch: str) -> bool:
    code = ord(ch[0]) if ch else 0
    return (0x3000 <= code <= 0x30FF or 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF
            or 0xF900 <= code <= 0xFAFF or 0xFF00 <= code <= 0xFFEF)


def default_max_chars(text: str) -> int:
    visible = [c for c in text if not c.isspace()]
    cjk = sum(1 for c in visible if is_cjk(c))
    return CJK_CUE_CHARS if visible and cjk / len(visible) >= 0.3 else LATIN_CUE_CHARS


def phrase_time(alignment: Alignment, phrase: str, nth: int = 1) -> float:
    """Seconds from the segment start to where the nth occurrence of phrase begins."""
    text, index = alignment.text, -1
    for _ in range(nth):
        index = text.find(phrase, index + 1)
        if index < 0:
            raise NarrationError(f"{phrase!r} (occurrence {nth}) is not in: {text!r}")
    return float(alignment.starts[_char_entry(alignment, index)])


def _char_entry(alignment: Alignment, string_index: int) -> int:
    """The alignment entry holding a string index (entries are usually, not always, one character)."""
    position = 0
    for entry, chars in enumerate(alignment.chars):
        if position + len(chars) > string_index:
            return entry
        position += len(chars)
    return len(alignment.chars) - 1


def tokens(chars: tuple) -> list[tuple[int, int]]:
    """Spans (start, end) of alignment entries: a CJK character, or a run of other
    non-space characters. An opening bracket or quote joins the token after it; other
    punctuation-only tokens join the token before them."""
    spans: list[tuple[int, int]] = []
    i, n = 0, len(chars)
    open_from: Optional[int] = None
    while i < n:
        if chars[i].isspace():
            i += 1
            continue
        j = i + 1
        if not is_cjk(chars[i]):
            while j < n and not chars[j].isspace() and not is_cjk(chars[j]):
                j += 1
        categories = {unicodedata.category(c) for c in "".join(chars[i:j])}
        if categories <= {"Ps", "Pi"}:
            open_from = i if open_from is None else open_from
        elif (open_from is None and spans and spans[-1][1] == i
              and all(c.startswith("P") for c in categories)):
            spans[-1] = (spans[-1][0], j)
        else:
            spans.append((i if open_from is None else open_from, j))
            open_from = None
        i = j
    if open_from is not None:
        spans.append((open_from, n))
    return spans


def _join(chars: tuple, spans: list[tuple[int, int]]) -> str:
    """Tokens joined with a space wherever the original had whitespace between them."""
    parts = []
    for k, (a, b) in enumerate(spans):
        if k and a > spans[k - 1][1]:
            parts.append(" ")
        parts.append("".join(chars[a:b]))
    return "".join(parts)


def split_cues(alignment: Alignment, max_chars: Optional[int] = None) -> list[Cue]:
    """Subtitle cues for one segment, relative to its start: break after a sentence ends,
    or before a token that would push the cue past max_chars."""
    if max_chars is None:
        max_chars = default_max_chars(alignment.text)
    chars, cues, current = alignment.chars, [], []

    def flush() -> None:
        if current:
            cues.append(Cue(float(alignment.starts[current[0][0]]),
                            float(alignment.ends[current[-1][1] - 1]), _join(chars, current)))
            current.clear()

    for span in tokens(chars):
        if current and len(_join(chars, current + [span])) > max_chars:
            flush()
        current.append(span)
        if "".join(chars[span[0]:span[1]]).rstrip("\"'”」』)）")[-1:] in SENTENCE_END:
            flush()
    flush()
    return cues


def scene_offsets(durations: list[float]) -> list[float]:
    offsets, total = [], 0.0
    for d in durations:
        offsets.append(total)
        total += d
    return offsets


def place_cues(scenes: list[tuple[float, list[dict]]], segment_cues: dict[str, list[Cue]]) -> list[Cue]:
    """Absolute cues from (scene offset, timeline entries) pairs; a timeline entry is
    {"id": segment id, "start": seconds into its scene}."""
    placed = []
    for offset, timeline in scenes:
        for entry in timeline:
            base = offset + float(entry["start"])
            placed += [Cue(base + c.start, base + c.end, c.text) for c in segment_cues[entry["id"]]]
    return smooth(sorted(placed, key=lambda c: c.start))


def smooth(cues: list[Cue]) -> list[Cue]:
    """Give each cue a readable minimum length and bridge short gaps, never overlapping the next."""
    out = []
    for i, cue in enumerate(cues):
        limit = cues[i + 1].start if i + 1 < len(cues) else float("inf")
        end = max(cue.end, cue.start + MIN_CUE_SECONDS)
        if limit - end < CUE_GAP_FILL:
            end = limit
        out.append(Cue(cue.start, min(end, limit), cue.text))
    return out


def srt_timestamp(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_srt(cues: list[Cue]) -> str:
    blocks = [f"{i}\n{srt_timestamp(c.start)} --> {srt_timestamp(c.end)}\n{c.text}\n"
              for i, c in enumerate(cues, 1)]
    return "\n".join(blocks)


# ---------- scene side ----------


@dataclass(frozen=True)
class SegmentAudio:
    id: str
    text: str
    audio: str
    duration: float
    alignment: Alignment


class Timeline:
    """Segment start times per scene, rewritten on every say() so a render always leaves a full file.
    The list lives on the scene object, so each render of a scene starts a fresh one."""

    def __init__(self, directory: Path):
        self.directory = directory

    def record(self, scene: object, segment_id: str, start: float) -> None:
        name = type(scene).__name__
        entries = vars(scene).setdefault("_narration_timeline", [])
        entries.append({"id": segment_id, "start": round(start, 4)})
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / f"{name}.json").write_text(
            json.dumps({"scene": name, "segments": entries}, indent=1), encoding="utf-8")


class Say:
    def __init__(self, scene, segment: SegmentAudio, narration: "Narration", tail: float):
        self.scene, self.segment, self.narration, self.tail = scene, segment, narration, tail
        self.start = 0.0

    def __enter__(self) -> "Say":
        if self.narration.speaking:
            raise NarrationError("say() blocks cannot nest: the two audio tracks would overlap")
        self.narration.speaking = True
        self.start = float(self.scene.time)
        self.scene.add_sound(self.segment.audio)
        self.narration.timeline.record(self.scene, self.segment.id, self.start)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.narration.speaking = False
        if exc_type is None:
            rest = self.remaining() + self.tail
            if rest > 1e-3:
                self.scene.wait(rest)
        return False

    @property
    def duration(self) -> float:
        return self.segment.duration

    def elapsed(self) -> float:
        return float(self.scene.time) - self.start

    def remaining(self) -> float:
        return self.segment.duration - self.elapsed()

    def time_of(self, phrase: str, nth: int = 1) -> float:
        return phrase_time(self.segment.alignment, phrase, nth)

    def until(self, phrase: str, nth: int = 1, minimum: float = MIN_RUN_TIME) -> float:
        """A run_time that ends the animation as the phrase is spoken (never below `minimum`)."""
        return max(self.time_of(phrase, nth) - self.elapsed(), minimum)

    def until_end(self, minimum: float = MIN_RUN_TIME) -> float:
        return max(self.remaining(), minimum)

    def wait_until(self, phrase: str, nth: int = 1) -> None:
        gap = self.time_of(phrase, nth) - self.elapsed()
        if gap > 0.02:
            self.scene.wait(gap)


class Narration:
    def __init__(self, segments: dict[str, SegmentAudio], timeline: Timeline):
        self.segments, self.timeline = segments, timeline
        self.speaking = False

    @classmethod
    def load(cls, manifest: Optional[Path] = None) -> "Narration":
        manifest = manifest or Path(__file__).resolve().with_name("voice_manifest.json")
        if not manifest.is_file():
            raise NarrationError(f"{manifest} is missing: run tts.py on narration.json first")
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        segments = {}
        for item in raw["segments"]:
            timing = json.loads(Path(item["timing"]).read_text(encoding="utf-8"))
            alignment = (Alignment.from_json(timing["alignment"]) if timing.get("alignment")
                         else Alignment.linear(item["text"], float(item["duration"])))
            segments[item["id"]] = SegmentAudio(item["id"], item["text"], item["audio"],
                                                float(item["duration"]), alignment)
        return cls(segments, Timeline(manifest.parent / "build" / "timeline"))

    def __getitem__(self, segment_id: str) -> SegmentAudio:
        if segment_id not in self.segments:
            raise NarrationError(f"no segment {segment_id!r}; known: {', '.join(self.segments)}")
        return self.segments[segment_id]

    def say(self, scene, segment_id: str, tail: float = DEFAULT_TAIL) -> Say:
        return Say(scene, self[segment_id], self, tail)


# ---------- command line ----------


def _ffprobe(args: list[str], path: Path) -> str:
    result = subprocess.run(["ffprobe", "-v", "error", *args, "-of",
                             "default=noprint_wrappers=1:nokey=1", str(path)],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise NarrationError(f"ffprobe failed on {path}: {result.stderr.strip()}")
    return result.stdout.strip()


def show(narration: Narration, segment_id: Optional[str]) -> None:
    for seg in ([narration[segment_id]] if segment_id else narration.segments.values()):
        print(f"{seg.id}  {seg.duration:.2f}s  {seg.text}")
        if segment_id:
            for a, b in tokens(seg.alignment.chars):
                print(f"  {seg.alignment.starts[a]:6.2f}  {''.join(seg.alignment.chars[a:b])}")


def assemble(out: Path, scene_files: list[Path], narration: Narration) -> dict:
    timeline_dir = narration.timeline.directory
    durations, scenes = [], []
    for path in scene_files:
        if not path.is_file():
            raise NarrationError(f"{path} does not exist")
        if not _ffprobe(["-select_streams", "a", "-show_entries", "stream=index"], path):
            raise NarrationError(f"{path} has no audio: every scene needs at least one say() block")
        timeline_file = timeline_dir / f"{path.stem}.json"
        if not timeline_file.is_file():
            raise NarrationError(f"{timeline_file} is missing: render {path.stem} again")
        durations.append(float(_ffprobe(["-show_entries", "format=duration"], path)))
        scenes.append(json.loads(timeline_file.read_text(encoding="utf-8"))["segments"])

    out.parent.mkdir(parents=True, exist_ok=True)
    concat_list = timeline_dir.parent / "concat.txt"
    concat_list.write_text("".join(f"file '{p.resolve()}'\n" for p in scene_files), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat_list),
                    "-c", "copy", "-movflags", "+faststart", str(out)], check=True)

    used = {entry["id"] for timeline in scenes for entry in timeline}
    cues = place_cues(list(zip(scene_offsets(durations), scenes)),
                      {sid: split_cues(narration[sid].alignment) for sid in used})
    srt = out.with_suffix(".srt")
    srt.write_text(format_srt(cues), encoding="utf-8")
    unused = sorted(set(narration.segments) - used)
    return {"video": str(out), "subtitles": str(srt), "seconds": round(sum(durations), 2),
            "scenes": len(scene_files), "cues": len(cues), "unused_segments": unused}


def main(argv: list[str]) -> int:
    try:
        if argv[:1] == ["show"] and len(argv) <= 2:
            show(Narration.load(), argv[1] if len(argv) == 2 else None)
            return 0
        if argv[:1] == ["assemble"] and len(argv) >= 3:
            print(json.dumps(assemble(Path(argv[1]), [Path(p) for p in argv[2:]], Narration.load()),
                             ensure_ascii=False))
            return 0
    except NarrationError as exc:
        print(f"voice.py: {exc}", file=sys.stderr)
        return 1
    print(__doc__.strip(), file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
