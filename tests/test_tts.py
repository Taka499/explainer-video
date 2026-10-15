import base64
import email.message
import io
import json
import subprocess
import urllib.error

import pytest

import tts

KEY = "sk_test_secret_value"


def completed(cmd, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)


class FakeRunner:
    """Stands in for subprocess.run: the Keychain and ffprobe."""

    def __init__(self, key_status=0, duration="1.25"):
        self.key_status, self.duration, self.calls = key_status, duration, []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if cmd[0] == "security":
            stdout = KEY + "\n" if "-w" in cmd and self.key_status == 0 else ""
            return completed(cmd, self.key_status, stdout)
        if cmd[0] == "ffprobe":
            return completed(cmd, 0, self.duration + "\n")
        raise AssertionError(f"unexpected command {cmd}")

    @property
    def key_reads(self):
        return sum(1 for c in self.calls if c[0] == "security" and "-w" in c)


class FakeResponse:
    def __init__(self, payload, headers=None):
        self._body, self.headers = json.dumps(payload).encode(), headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def payload_for(text, source="alignment"):
    chars = list(text)
    return {"audio_base64": base64.b64encode(b"MP3:" + text.encode()).decode(),
            source: {"characters": chars,
                     "character_start_times_seconds": [i * 0.1 for i in range(len(chars))],
                     "character_end_times_seconds": [(i + 1) * 0.1 for i in range(len(chars))]}}


class FakeOpener:
    def __init__(self, errors=()):
        self.requests, self.errors = [], list(errors)

    def __call__(self, request, timeout):
        self.requests.append(request)
        if self.errors:
            raise self.errors.pop(0)
        body = json.loads(request.data)
        return FakeResponse(payload_for(body["text"]), {"Character-Cost": str(len(body["text"]))})


def refuse(*args, **kwargs):
    raise AssertionError("must not be called")


def http_error(code, body=b"{}", headers=None):
    msg = email.message.Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError("https://api.elevenlabs.io/x", code, "err", msg, io.BytesIO(body))


@pytest.fixture
def root(tmp_path):
    home = tmp_path / "explainers"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"voice_id": "voice123"}))
    return home


def write_narration(directory, segments):
    path = directory / "narration.json"
    path.write_text(json.dumps({"language": "en", "segments": segments}))
    return path


SEGMENTS = [{"id": "s01", "text": "A derivative measures change."},
            {"id": "s02", "text": "Slopes, everywhere."}]


# ---------- parse_config ----------


def test_config_defaults():
    config = tts.parse_config({"voice_id": " v1 "})
    assert config == tts.Config("v1", tts.DEFAULT_MODEL, tts.DEFAULT_OUTPUT_FORMAT, None)


def test_config_keeps_overrides():
    config = tts.parse_config({"voice_id": "v", "model_id": "m", "output_format": "mp3_44100_64",
                               "voice_settings": {"speed": 0.9}})
    assert (config.model_id, config.output_format, config.voice_settings) == ("m", "mp3_44100_64", {"speed": 0.9})


@pytest.mark.parametrize("raw", [{}, {"voice_id": ""}, {"voice_id": "  "}, {"voice_id": 3}, [], None,
                                 {"voice_id": "v", "voice_settings": "fast"}])
def test_config_rejects(raw):
    with pytest.raises(tts.UserError):
        tts.parse_config(raw)


# ---------- parse_narration ----------


def test_narration_happy_path_strips_text():
    assert tts.parse_narration({"segments": [{"id": "a-1_b", "text": " hi "}]}) == [tts.Segment("a-1_b", "hi")]


@pytest.mark.parametrize("raw", [
    None, {}, {"segments": []}, {"segments": "x"}, {"segments": ["x"]},
    {"segments": [{"id": "a b", "text": "x"}]},
    {"segments": [{"id": "", "text": "x"}]},
    {"segments": [{"id": "a", "text": ""}]},
    {"segments": [{"id": "a", "text": "   "}]},
    {"segments": [{"id": "a", "text": 5}]},
    {"segments": [{"id": "a", "text": "x"}, {"id": "a", "text": "y"}]},
])
def test_narration_rejects(raw):
    with pytest.raises(tts.UserError):
        tts.parse_narration(raw)


# ---------- request body and cache key ----------


def test_body_omits_voice_settings_unless_configured():
    assert tts.request_body("hi", tts.Config("v")) == {"text": "hi", "model_id": tts.DEFAULT_MODEL}
    assert tts.request_body("hi", tts.Config("v", voice_settings={"speed": 1.1}))["voice_settings"] == {"speed": 1.1}


def key_for(text="hello", **config):
    cfg = tts.Config(**{"voice_id": "v", **config})
    return tts.cache_key(cfg, tts.request_body(text, cfg))


def test_cache_key_is_stable_and_ignores_key_order():
    a = key_for(voice_settings={"stability": 0.5, "speed": 1.0})
    b = key_for(voice_settings={"speed": 1.0, "stability": 0.5})
    assert a == b and len(a) == 32


@pytest.mark.parametrize("change", [
    {"text": "hello!"}, {"voice_id": "w"}, {"model_id": "eleven_v3"},
    {"output_format": "mp3_44100_64"}, {"voice_settings": {"speed": 1.0}},
])
def test_cache_key_changes_with_every_audio_input(change):
    text = change.pop("text", "hello")
    assert key_for(text, **change) != key_for()


# ---------- response parsing ----------


def test_response_prefers_alignment_and_decodes_audio():
    audio, alignment, source = tts.parse_response(payload_for("ab"))
    assert audio == b"MP3:ab" and source == "alignment" and alignment["characters"] == ["a", "b"]


def test_response_falls_back_to_normalized_alignment():
    payload = payload_for("ab", source="normalized_alignment")
    payload["alignment"] = None
    assert tts.parse_response(payload)[2] == "normalized_alignment"


@pytest.mark.parametrize("mutate", [
    lambda p: p.pop("audio_base64"),
    lambda p: p.pop("alignment"),
    lambda p: p["alignment"]["character_end_times_seconds"].pop(),
    lambda p: p["alignment"].pop("characters"),
    lambda p: p["alignment"].update(characters=[], character_start_times_seconds=[],
                                    character_end_times_seconds=[]),
])
def test_response_rejects_broken_payloads(mutate):
    payload = payload_for("ab")
    mutate(payload)
    with pytest.raises(tts.UserError):
        tts.parse_response(payload)


@pytest.mark.parametrize("headers,expected", [
    ({"character-cost": "12"}, 12), ({"X-Character-Count": "7"}, 7),
    ({"character-cost": "n/a"}, 5), ({}, 5),
])
def test_billed_characters(headers, expected):
    assert tts.billed_characters(headers, "hello") == expected


def test_keychain_status_codes():
    assert [tts.keychain_status(c) for c in (0, 44, 1, 51)] == ["present", "missing", "unreadable", "unreadable"]


def test_error_message_reads_detail_and_adds_hint():
    body = json.dumps({"detail": {"status": "invalid_api_key", "message": "Invalid key"}}).encode()
    assert tts.error_message(401, body) == ("HTTP 401 (key rejected or lacks the text_to_speech permission): "
                                            "invalid_api_key Invalid key")
    assert "field required" in tts.error_message(422, json.dumps({"detail": [{"msg": "field required"}]}).encode())
    assert tts.error_message(500, b"<html>oops") == "HTTP 500: <html>oops"


# ---------- check ----------


def test_check_refuses_inside_sandbox_without_touching_the_keychain(root, capsys):
    assert tts.check(root, run=refuse, sandboxed=True) == 2
    assert "dangerouslyDisableSandbox" in capsys.readouterr().out


def test_check_reports_present_key_without_reading_it(root, capsys, monkeypatch):
    monkeypatch.setattr(tts.shutil, "which", lambda name: "/usr/bin/" + name)
    runner = FakeRunner(key_status=0)
    assert tts.check(root, run=runner, sandboxed=False) == 0
    out = capsys.readouterr().out
    assert "key:     present" in out and "voice123" in out
    assert runner.key_reads == 0 and KEY not in out


def test_check_reports_missing_key_and_voice(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(tts.shutil, "which", lambda name: "/usr/bin/" + name)
    assert tts.check(tmp_path, run=FakeRunner(key_status=44), sandboxed=False) == 1
    out = capsys.readouterr().out
    assert "key:     missing" in out and "security add-generic-password" in out and "voice:   missing" in out


# ---------- synthesize ----------


def test_first_run_synthesizes_and_writes_manifest(root, tmp_path, capsys):
    narration = write_narration(tmp_path, SEGMENTS)
    runner, opener = FakeRunner(), FakeOpener()
    summary = tts.synthesize(narration, root, run=runner, opener=opener, sleep=refuse, sandboxed=False)

    assert summary["synthesized"] == 2 and summary["cached"] == 0
    assert summary["billed_characters"] == sum(len(s["text"]) for s in SEGMENTS)
    assert runner.key_reads == 1
    request = opener.requests[0]
    assert request.full_url == ("https://api.elevenlabs.io/v1/text-to-speech/voice123/with-timestamps"
                                "?output_format=mp3_44100_128")
    assert request.get_header("Xi-api-key") == KEY
    manifest = json.loads((tmp_path / "voice_manifest.json").read_text())
    first = manifest["segments"][0]
    assert first["id"] == "s01" and first["duration"] == 1.25
    assert open(first["audio"], "rb").read() == b"MP3:" + SEGMENTS[0]["text"].encode()
    assert json.loads(open(first["timing"]).read())["alignment"]["characters"][0] == "A"
    assert KEY not in capsys.readouterr().out


def test_second_run_is_free(root, tmp_path):
    narration = write_narration(tmp_path, SEGMENTS)
    tts.synthesize(narration, root, run=FakeRunner(), opener=FakeOpener(), sandboxed=False)
    summary = tts.synthesize(narration, root, run=refuse, opener=refuse, sandboxed=True)
    assert summary["cached"] == 2 and summary["billed_characters"] == 0


def test_only_the_changed_segment_is_synthesized(root, tmp_path):
    narration = write_narration(tmp_path, SEGMENTS)
    tts.synthesize(narration, root, run=FakeRunner(), opener=FakeOpener(), sandboxed=False)
    write_narration(tmp_path, [SEGMENTS[0], {"id": "s02", "text": "Slopes, nearly everywhere."}])
    opener = FakeOpener()
    assert tts.synthesize(narration, root, run=FakeRunner(), opener=opener, sandboxed=False)["synthesized"] == 1
    assert len(opener.requests) == 1


def test_incomplete_cache_entry_is_redone(root, tmp_path):
    narration = write_narration(tmp_path, SEGMENTS[:1])
    tts.synthesize(narration, root, run=FakeRunner(), opener=FakeOpener(), sandboxed=False)
    for timing in (root / ".cache" / "tts").glob("*.json"):
        timing.unlink()
    assert tts.synthesize(narration, root, run=FakeRunner(), opener=FakeOpener(), sandboxed=False)["synthesized"] == 1


def test_sandboxed_run_with_uncached_segments_explains_itself(root, tmp_path):
    narration = write_narration(tmp_path, SEGMENTS)
    with pytest.raises(tts.UserError, match="dangerouslyDisableSandbox"):
        tts.synthesize(narration, root, run=refuse, opener=refuse, sandboxed=True)


def test_relative_narration_path_is_rejected(root):
    with pytest.raises(tts.UserError, match="absolute"):
        tts.synthesize(tts.Path("narration.json"), root, run=refuse, opener=refuse, sandboxed=False)


def test_missing_key_stops_before_any_request(root, tmp_path):
    narration = write_narration(tmp_path, SEGMENTS)
    with pytest.raises(tts.UserError, match="missing"):
        tts.synthesize(narration, root, run=FakeRunner(key_status=44), opener=refuse, sandboxed=False)


def test_rate_limit_is_retried_once_after_retry_after(root, tmp_path):
    narration = write_narration(tmp_path, SEGMENTS[:1])
    slept, opener = [], FakeOpener(errors=[http_error(429, headers={"Retry-After": "2"})])
    tts.synthesize(narration, root, run=FakeRunner(), opener=opener, sleep=slept.append, sandboxed=False)
    assert slept == [2.0] and len(opener.requests) == 2


def test_second_failure_is_reported(root, tmp_path):
    narration = write_narration(tmp_path, SEGMENTS[:1])
    opener = FakeOpener(errors=[http_error(503), http_error(503)])
    with pytest.raises(tts.UserError, match="HTTP 503"):
        tts.synthesize(narration, root, run=FakeRunner(), opener=opener, sleep=lambda s: None, sandboxed=False)


def test_auth_error_is_not_retried_and_hides_the_key(root, tmp_path):
    narration = write_narration(tmp_path, SEGMENTS[:1])
    opener = FakeOpener(errors=[http_error(401, b'{"detail": {"status": "invalid_api_key"}}')])
    with pytest.raises(tts.UserError) as info:
        tts.synthesize(narration, root, run=FakeRunner(), opener=opener, sleep=refuse, sandboxed=False)
    assert "401" in str(info.value) and KEY not in str(info.value) and len(opener.requests) == 1
