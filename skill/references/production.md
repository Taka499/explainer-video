# Producing the video

You are the background subagent the `explainer-video` skill launched. Your prompt gives
you a brief, a work directory `W`, and the skill directory `S`; every path below uses
them as absolute paths. Nobody answers questions while you work. Decide, note the
decision in `W/storyboard.md`, and keep going.

**Ground rules**

- Put everything in `W`. Final files: `W/<slug>.mp4` and `W/<slug>.srt`, where `<slug>` is
  the last part of `W`.
- Exactly one command runs outside the sandbox: `S/scripts/tts.py`, with
  `dangerouslyDisableSandbox: true`. Run everything else sandboxed. Never read, print or
  look for the ElevenLabs key.
- Synthesized audio costs the user credits and the cache makes repeats free, so settle the
  narration before you synthesize it. Rendering is free and can be repeated.
- If the same error comes back three times, stop and report it rather than looping.
- Shorthand used below: `T` means `S/scripts/toolchain.sh run`. It puts the Manim venv and TeX on
  `PATH` and sends every cache to a writable place.

## 1. Teaching arc

A 3Blue1Brown-style explainer makes the viewer feel they could have discovered the idea
themselves. Shape it like this:

1. **Hook.** Open on a concrete question or a small puzzle. Never open on a definition.
2. **Concrete before general.** Work one specific example on screen before stating the rule.
3. **Show, then name.** The viewer sees why something is true before the term or formula
   appears. The formula is a label for what they have just watched.
4. **The narration points, the picture explains.** Say what to watch ("look at the slope as
   the point slides"), and don't read the formula aloud symbol by symbol.
5. **Payoff.** Answer the opening question, then close on one sentence the viewer can keep.

Fit the scope to the length and cut ruthlessly: a 60-second video carries one idea.

- **Speaking rate:** about 2.5 words per second in English, 7 characters per second in
  Japanese, 4.5 characters per second in Chinese.
- **Visual pauses:** leave about 15% of the time to the picture alone.
- **Scenes:** 30–60 seconds → 2–4 scenes of 10–25 seconds each.

## 2. Storyboard: `W/storyboard.md`

One section per scene, in order. For each scene give:

- its class name, in CamelCase (it becomes the Manim scene class);
- its purpose, in one line;
- the visuals as numbered beats;
- the narration segment ids it plays.

End with the word or character count and the estimated length against the target. Adjust
before going on.

## 3. Narration: `W/narration.json`

```json
{"language": "en", "segments": [
  {"id": "s01", "text": "Why does a ball thrown upward slow down, stop, and fall back?"},
  {"id": "s02", "text": "Let's watch its height, moment by moment."}
]}
```

- One segment is one visual beat of 1–2 sentences. Ids are `s01`, `s02`, … in playing order.
- Write for the ear. Keep sentences short and leave out parentheses. Write formulas the way
  they are spoken: "x squared", not `x^2`; "f of x", not `f(x)`.
- Technical terms on screen stay in English in every language. That is the user's rule.
  Where the viewer has to match a spoken term to the screen, use the same English term in
  the narration too, e.g. "この gradient は…".
- Read the whole script through once against the storyboard before synthesizing. A change
  after this point re-bills that segment.

## 4. Voice

```bash
S/scripts/tts.py W/narration.json          # dangerouslyDisableSandbox: true
cp S/scripts/voice.py W/voice.py
T python W/voice.py show                   # every segment with its duration
T python W/voice.py show s03               # one segment, word by word with start times
```

`tts.py` prints each segment's duration and ends with a JSON summary that includes
`billed_characters`. Keep that number for the report, adding it up across runs if you
synthesize more than once. Durations are now fixed, so compare their total with the
target length. If it misses by more than 25%, rewrite the narration and synthesize again
(only the changed segments are billed).

## 5. Scenes: `W/scenes.py`

```python
from manim import *
from voice import Narration

narration = Narration.load()
config.background_color = "#0b0b0f"


class Hook(Scene):
    def construct(self):
        question = Text("Why does it fall back?", font="Helvetica Neue", font_size=44)
        with narration.say(self, "s01") as seg:
            self.play(Write(question), run_time=seg.until("slow down"))
            ball = Dot(radius=0.15, color=YELLOW).shift(DOWN * 2)
            self.play(FadeIn(ball), question.animate.to_edge(UP, buff=0.6),
                      run_time=seg.until("fall back"))
            self.play(ball.animate.shift(UP * 3), run_time=seg.until_end(), rate_func=there_and_back)
        # Leaving the block waits out the rest of the audio plus a short pause.
```

**Timing**

- Every segment plays inside `with narration.say(self, "<id>") as seg:`. It adds the audio
  and records the start time that the subtitles use. Never call `add_sound` yourself, and
  never nest `say` blocks.
- Make a visual land on its word: `run_time=seg.until("phrase")` ends the animation as the
  phrase begins. `seg.wait_until("phrase")` holds still until then, and `seg.until_end()`
  fills the rest of the segment.
- The phrase must appear verbatim in the segment's text (`voice.py show <id>` lists them).
  A missing phrase raises an error that quotes the text, so the mistake is caught at render
  time.
- Every scene contains at least one `say` block. The scenes are joined in storyboard order.

**Look**

- Near-black background; white text; `BLUE_C` for the main object, `YELLOW` for whatever
  matters right now; `TEAL`, `GREEN_C` and `RED_C` for secondary roles; `GREY_B` for context.
  Colour codes meaning: once a quantity has a colour, it keeps it through the whole video.
- Maths in `MathTex`. Words in `Text`, with `font="Helvetica Neue"` for Latin script,
  `font="Hiragino Sans"` for Japanese and `font="PingFang SC"` for Chinese. Keep CJK out of
  `MathTex`. If it is unavoidable, pass `tex_template=TexTemplateLibrary.ctex`.
- Sizes at 1080p: titles 44–52, body 32–36, labels 26–30, never below 24.
- Stay inside the frame (14.2 × 8 units) with a margin of 0.5. Lay out with
  `to_edge(..., buff=0.5)`, `next_to`, `arrange` and `VGroup`, not with raw coordinates.
  Shrink long formulas with `scale_to_fit_width(config.frame_width - 1)`.
- At most one short sentence of prose on screen at a time. Labels beat captions, since the
  subtitles carry the words.
- Clear the stage between ideas: `FadeOut` the old group, or transform it into the next one.
  Never leave stale objects behind.
- Motion vocabulary: `Write` and `Create` introduce; `TransformMatchingTex` steps through
  algebra; `ReplacementTransform` morphs one shape into another; `Indicate` and
  `Circumscribe` emphasize; `ValueTracker` with `always_redraw` shows continuous change.
- Borrow the style only. No Pi creatures, no 3Blue1Brown logo or branding, no music, and no
  imitation of any real person's voice.

## 6. Render and check, at low quality

```bash
T manim -ql --media_dir W/build/media W/scenes.py Hook          # one scene
T manim -ql --media_dir W/build/media W/scenes.py Hook Slope Payoff
```

Output: `W/build/media/videos/scenes/480p15/<Scene>.mp4`. Read the render log for errors
and warnings, such as a missing phrase, LaTeX errors or `run_time` too short.

Then look at the result. Contact sheets of stills, four per image, two seconds apart:

```bash
mkdir -p W/build/frames
ffmpeg -v error -y -i W/build/media/videos/scenes/480p15/Hook.mp4 \
  -vf "fps=1/2,scale=854:-1,tile=2x2" W/build/frames/Hook_%02d.png
```

Read every sheet with the Read tool and check for:

- text or objects overlapping, cut off by the frame edge, or too small to read;
- objects left over from an earlier idea;
- CJK characters drawn as empty boxes (wrong font);
- wrong maths, or a label on the wrong object;
- **sync**: the thing the narration names should be on screen when it is said. To check
  one moment, take a single frame at the time the phrase is spoken: the segment's `start`
  in `W/build/timeline/<Scene>.json` plus the phrase time from `voice.py show <id>`.
  Command: `ffmpeg -v error -y -ss <t> -i <mp4> -frames:v 1 W/build/frames/check.png`.

Fix and re-render. Allow up to three rounds per scene. After that, move on and list what
is still wrong in the report.

## 7. Final render and assembly

```bash
T manim -qh --media_dir W/build/media W/scenes.py Hook Slope Payoff
T python W/voice.py assemble W/<slug>.mp4 \
  W/build/media/videos/scenes/1080p60/Hook.mp4 \
  W/build/media/videos/scenes/1080p60/Slope.mp4 \
  W/build/media/videos/scenes/1080p60/Payoff.mp4
```

List the scene files in storyboard order. `assemble` joins them and writes `W/<slug>.srt`
next to the video. It prints a JSON summary: length, cue count, and any `unused_segments`
(narration nobody plays, usually a bug). The final render also rewrites the timeline, so
always assemble from the 1080p60 files of that same render.

Last check on the finished file: sample nine frames across the whole video and read the
sheet.

```bash
ffmpeg -v error -y -i W/<slug>.mp4 -vf "fps=9/<length in seconds>,scale=640:-1,tile=3x3" -frames:v 1 W/build/frames/final.png
```

Also read the first cues of the SRT.

## 8. Report

Your final message is the report the main session relays to the user, so keep it under
15 lines:

```text
video:      <W>/<slug>.mp4
subtitles:  <W>/<slug>.srt
length:     <seconds> s, <N> scenes, narration in <language>
credits:    <billed_characters, summed over this run's tts.py calls>
issues:     <anything still wrong, or "none seen in the sampled frames">
```

If you could not finish, report the step, the command and its error output instead.
