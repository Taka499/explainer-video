import json
import shutil
import subprocess

import pytest

import voice
from voice import Alignment, Cue


def align(text, step=0.1):
    """Alignment with one character every `step` seconds."""
    return Alignment(tuple(text), tuple(i * step for i in range(len(text))),
                     tuple((i + 1) * step for i in range(len(text))))


def words(alignment):
    return ["".join(alignment.chars[a:b]) for a, b in voice.tokens(alignment.chars)]


# ---------- phrase timing ----------


def test_phrase_time_is_the_start_of_its_first_character():
    assert voice.phrase_time(align("the slope of x"), "slope") == pytest.approx(0.4)


def test_phrase_time_nth_occurrence():
    assert voice.phrase_time(align("x then x again"), "x", nth=2) == pytest.approx(0.7)


def test_phrase_time_in_cjk_text():
    assert voice.phrase_time(align("微分とは傾きです"), "傾き") == pytest.approx(0.4)


def test_phrase_time_with_multi_character_entries():
    alignment = Alignment(("ab", "c", "de"), (0.0, 1.0, 2.0), (1.0, 2.0, 3.0))
    assert voice.phrase_time(alignment, "e") == 2.0 and voice.phrase_time(alignment, "c") == 1.0


@pytest.mark.parametrize("phrase,nth", [("integral", 1), ("slope", 2)])
def test_phrase_time_missing_phrase_names_the_text(phrase, nth):
    with pytest.raises(voice.NarrationError, match="the slope"):
        voice.phrase_time(align("the slope"), phrase, nth)


def test_linear_alignment_spreads_evenly():
    alignment = Alignment.linear("abcd", 2.0)
    assert alignment.starts == (0.0, 0.5, 1.0, 1.5) and alignment.ends[-1] == 2.0


# ---------- tokens ----------


def test_tokens_latin_words_keep_their_punctuation():
    assert words(align("Hello, world. (Yes)")) == ["Hello,", "world.", "(Yes)"]


def test_tokens_cjk_characters_split_and_punctuation_attaches():
    assert words(align("微分、です。")) == ["微", "分、", "で", "す。"]


def test_tokens_opening_bracket_joins_the_next_token():
    assert words(align("「傾き」とは")) == ["「傾", "き」", "と", "は"]


def test_tokens_mixed_script_keeps_english_terms_whole():
    assert words(align("Fourier変換 は")) == ["Fourier", "変", "換", "は"]


def test_tokens_empty_and_whitespace():
    assert voice.tokens(()) == [] and voice.tokens(tuple("   ")) == []


def test_tokens_leading_punctuation_stays_a_token():
    assert words(align("... ok")) == ["...", "ok"]


# ---------- cue splitting ----------


def test_cues_break_after_each_sentence():
    cues = voice.split_cues(align("One two. Three four!"))
    assert [c.text for c in cues] == ["One two.", "Three four!"]
    assert cues[0].start == pytest.approx(0.0) and cues[0].end == pytest.approx(0.8)
    assert cues[1].start == pytest.approx(0.9)


def test_cues_break_before_exceeding_max_chars():
    cues = voice.split_cues(align("aaa bbb ccc ddd"), max_chars=7)
    assert [c.text for c in cues] == ["aaa bbb", "ccc ddd"]


def test_cue_exactly_at_max_chars_is_not_split():
    assert len(voice.split_cues(align("aaa bbb"), max_chars=7)) == 1


def test_overlong_single_word_gets_its_own_cue():
    assert [c.text for c in voice.split_cues(align("a supercalifragilistic b"), max_chars=5)] == [
        "a", "supercalifragilistic", "b"]


def test_cjk_cues_join_without_spaces_and_use_the_cjk_limit():
    text = "微分は変化の割合です。" + "あ" * 25
    cues = voice.split_cues(align(text))
    assert cues[0].text == "微分は変化の割合です。"
    assert [len(c.text) for c in cues[1:]] == [voice.CJK_CUE_CHARS, 5]


def test_sentence_end_inside_closing_quote_still_breaks():
    assert [c.text for c in voice.split_cues(align('He said "stop." Then left.'))] == [
        'He said "stop."', "Then left."]


def test_decimal_number_does_not_break_a_sentence():
    assert len(voice.split_cues(align("Pi is 3.14 roughly."))) == 1


@pytest.mark.parametrize("text,expected", [
    ("plain english", voice.LATIN_CUE_CHARS), ("日本語のテキスト", voice.CJK_CUE_CHARS),
    ("", voice.LATIN_CUE_CHARS), ("Fourier transform は便利", voice.LATIN_CUE_CHARS),
    ("Fourier 変換は便利です", voice.CJK_CUE_CHARS),
])
def test_default_max_chars(text, expected):
    assert voice.default_max_chars(text) == expected


# ---------- placement and SRT ----------


def test_scene_offsets_are_cumulative():
    assert voice.scene_offsets([2.0, 3.5, 1.0]) == [0.0, 2.0, 5.5] and voice.scene_offsets([]) == []


def test_place_cues_adds_scene_offset_and_segment_start():
    cues = {"a": [Cue(0.0, 1.0, "A")], "b": [Cue(0.5, 2.0, "B")]}
    placed = voice.place_cues([(0.0, [{"id": "a", "start": 0.0}]), (10.0, [{"id": "b", "start": 1.0}])], cues)
    assert [(c.start, c.end, c.text) for c in placed] == [(0.0, 1.0, "A"), (11.5, 13.0, "B")]


def test_smooth_enforces_minimum_and_bridges_short_gaps_without_overlap():
    out = voice.smooth([Cue(0.0, 0.2, "a"), Cue(0.5, 1.0, "b"), Cue(1.3, 2.0, "c"), Cue(5.0, 6.0, "d")])
    assert [c.end for c in out] == [0.5, 1.3, pytest.approx(2.1), 6.0]


def test_smooth_bridges_a_gap_shorter_than_the_fill_threshold():
    assert voice.smooth([Cue(0.0, 1.0, "a"), Cue(1.4, 2.4, "b")])[0].end == 1.4


def test_smooth_leaves_long_gaps_open():
    out = voice.smooth([Cue(0.0, 1.0, "a"), Cue(3.0, 4.0, "b")])
    assert out[0].end == 1.0


@pytest.mark.parametrize("seconds,expected", [
    (0, "00:00:00,000"), (1.25, "00:00:01,250"), (59.9996, "00:01:00,000"),
    (3661.5, "01:01:01,500"), (-0.3, "00:00:00,000"),
])
def test_srt_timestamp(seconds, expected):
    assert voice.srt_timestamp(seconds) == expected


def test_format_srt_numbers_blocks_from_one():
    assert voice.format_srt([Cue(0, 1, "a"), Cue(1, 2.5, "b")]) == (
        "1\n00:00:00,000 --> 00:00:01,000\na\n\n2\n00:00:01,000 --> 00:00:02,500\nb\n")


def test_format_srt_empty():
    assert voice.format_srt([]) == ""


# ---------- scene helper ----------


class FakeScene:
    def __init__(self):
        self.time, self.sounds, self.waits = 0.0, [], []

    def add_sound(self, path):
        self.sounds.append((path, self.time))

    def wait(self, duration):
        self.waits.append(round(duration, 6))
        self.time += duration

    def play(self, run_time):
        self.time += run_time


class Intro(FakeScene):
    pass


class Outro(FakeScene):
    pass


@pytest.fixture
def narration(tmp_path):
    segments = {
        "s01": voice.SegmentAudio("s01", "the slope of x", "/a/s01.mp3", 2.0, align("the slope of x")),
        "s02": voice.SegmentAudio("s02", "next", "/a/s02.mp3", 1.0, align("next")),
    }
    return voice.Narration(segments, voice.Timeline(tmp_path / "build" / "timeline"))


def test_say_adds_audio_and_waits_out_the_rest_plus_tail(narration):
    scene = Intro()
    scene.time = 3.0
    with narration.say(scene, "s01", tail=0.25) as seg:
        scene.play(0.5)
        assert seg.elapsed() == pytest.approx(0.5) and seg.remaining() == pytest.approx(1.5)
    assert scene.sounds == [("/a/s01.mp3", 3.0)]
    assert scene.waits == [1.75] and scene.time == pytest.approx(5.25)


def test_say_does_not_wait_when_animations_overran(narration):
    scene = Intro()
    with narration.say(scene, "s02", tail=0) as seg:
        scene.play(1.5)
        assert seg.until_end() == voice.MIN_RUN_TIME
    assert scene.waits == []


def test_until_targets_the_phrase_from_now(narration):
    scene = Intro()
    with narration.say(scene, "s01") as seg:
        assert seg.until("slope") == pytest.approx(0.4)
        scene.play(0.3)
        assert seg.until("slope") == pytest.approx(0.1)
        scene.play(1.0)
        assert seg.until("slope") == voice.MIN_RUN_TIME


def test_wait_until_skips_a_phrase_already_spoken(narration):
    scene = Intro()
    with narration.say(scene, "s01", tail=0) as seg:
        seg.wait_until("of")
        assert scene.time == pytest.approx(1.0)
        seg.wait_until("slope")
        assert scene.waits == [1.0]


def test_nested_say_is_refused(narration):
    scene = Intro()
    with narration.say(scene, "s01"):
        with pytest.raises(voice.NarrationError, match="nest"):
            with narration.say(scene, "s02"):
                pass


def test_exception_inside_say_does_not_wait_and_releases_the_lock(narration):
    scene = Intro()
    with pytest.raises(RuntimeError):
        with narration.say(scene, "s01"):
            raise RuntimeError("bad scene code")
    assert scene.waits == []
    with narration.say(scene, "s02"):
        pass


def test_unknown_segment_lists_the_known_ones(narration):
    with pytest.raises(voice.NarrationError, match="s01, s02"):
        narration.say(Intro(), "s99")


def test_timeline_is_written_per_scene_class(narration, tmp_path):
    intro, outro = Intro(), Outro()
    with narration.say(intro, "s01", tail=0):
        pass
    with narration.say(intro, "s02", tail=0):
        pass
    with narration.say(outro, "s01", tail=0):
        pass
    directory = tmp_path / "build" / "timeline"
    assert json.loads((directory / "Intro.json").read_text())["segments"] == [
        {"id": "s01", "start": 0.0}, {"id": "s02", "start": 2.0}]
    assert json.loads((directory / "Outro.json").read_text())["segments"] == [{"id": "s01", "start": 0.0}]


def test_a_new_render_of_a_scene_replaces_its_timeline(narration, tmp_path):
    for _ in range(2):
        with narration.say(Intro(), "s02", tail=0):
            pass
    assert len(json.loads((tmp_path / "build" / "timeline" / "Intro.json").read_text())["segments"]) == 1


# ---------- loading the manifest ----------


def write_manifest(tmp_path, alignment=True):
    timing = tmp_path / "abc.json"
    timing.write_text(json.dumps({"alignment": {
        "characters": list("hi"), "character_start_times_seconds": [0.0, 0.2],
        "character_end_times_seconds": [0.2, 0.4]} if alignment else None}))
    manifest = tmp_path / "voice_manifest.json"
    manifest.write_text(json.dumps({"segments": [
        {"id": "s01", "text": "hi", "duration": 0.6, "audio": str(tmp_path / "abc.mp3"), "timing": str(timing)}]}))
    return manifest


def test_load_reads_timing_and_puts_the_timeline_beside_the_manifest(tmp_path):
    loaded = voice.Narration.load(write_manifest(tmp_path))
    assert loaded["s01"].alignment.starts == (0.0, 0.2) and loaded["s01"].duration == 0.6
    assert loaded.timeline.directory == tmp_path / "build" / "timeline"


def test_load_falls_back_to_linear_timing(tmp_path):
    assert voice.Narration.load(write_manifest(tmp_path, alignment=False))["s01"].alignment.starts == (0.0, 0.3)


def test_load_without_manifest_says_what_to_run(tmp_path):
    with pytest.raises(voice.NarrationError, match="tts.py"):
        voice.Narration.load(tmp_path / "voice_manifest.json")


# ---------- assembly, with real ffmpeg ----------


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")
def test_assemble_joins_scenes_and_offsets_subtitles(tmp_path):
    manifest = tmp_path / "voice_manifest.json"
    timing = tmp_path / "t.json"
    timing.write_text(json.dumps({"alignment": {
        "characters": list("Hi."), "character_start_times_seconds": [0.0, 0.1, 0.2],
        "character_end_times_seconds": [0.1, 0.2, 0.3]}}))
    manifest.write_text(json.dumps({"segments": [
        {"id": "s01", "text": "Hi.", "duration": 0.3, "audio": "x.mp3", "timing": str(timing)},
        {"id": "s02", "text": "Hi.", "duration": 0.3, "audio": "x.mp3", "timing": str(timing)}]}))
    narration = voice.Narration.load(manifest)
    scenes = []
    for name, seg in (("Intro", "s01"), ("Outro", "s02")):
        path = tmp_path / f"{name}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=15:d=1",
                        "-f", "lavfi", "-i", "sine=d=1", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", str(path)], check=True)
        narration.timeline.directory.mkdir(parents=True, exist_ok=True)
        (narration.timeline.directory / f"{name}.json").write_text(
            json.dumps({"segments": [{"id": seg, "start": 0.5}]}))
        scenes.append(path)

    result = voice.assemble(tmp_path / "out" / "video.mp4", scenes, narration)

    assert result["scenes"] == 2 and result["cues"] == 2 and result["seconds"] == pytest.approx(2.0, abs=0.1)
    starts = [line.split(" --> ")[0] for line in (tmp_path / "out" / "video.srt").read_text().splitlines()
              if " --> " in line]
    assert starts[0] == "00:00:00,500" and starts[1].startswith("00:00:01,5")
    assert (tmp_path / "out" / "video.mp4").stat().st_size > 0


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")
def test_assemble_refuses_a_scene_without_audio(tmp_path):
    silent = tmp_path / "Silent.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=15:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(silent)], check=True)
    with pytest.raises(voice.NarrationError, match="no audio"):
        voice.assemble(tmp_path / "v.mp4", [silent], voice.Narration.load(write_manifest(tmp_path)))


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="needs ffprobe")
def test_assemble_refuses_a_missing_scene_file(tmp_path):
    with pytest.raises(voice.NarrationError, match="does not exist"):
        voice.assemble(tmp_path / "v.mp4", [tmp_path / "Nope.mp4"], voice.Narration.load(write_manifest(tmp_path)))
