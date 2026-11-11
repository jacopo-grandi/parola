<h1 align="center"><img src="docs/assets/parola.png" alt="P.A.R.O.L.A." width="720"></h1>
<p align="center"><b>P</b>arlato <b>A</b>scoltato, <b>R</b>iconosciuto e <b>O</b>rganizzato in <b>L</b>ocale, <b>A</b>lla lettera</p>

Word-for-word transcription of audio and calls, **100% local** on Apple Silicon Macs, for
Italian and English (including both mixed in the same sentence).

Word for word: what was said, as it was said. No summaries, no AI meeting notes, no rephrasing —
grammar mistakes stay as spoken, and every word traces back to its moment in the audio. Words
the model may have got wrong are marked for review, so the transcript can be made exact
([the numbers](#for-engineers)). A clean layer will be derived from the faithful one, never the
other way around.

How it is measured, which models it uses and why: [`METHODOLOGY.md`](METHODOLOGY.md).

Status: in development; the native Mac app works.

![The demo call in P.A.R.O.L.A.: the transcript with the voice named, the calls in the sidebar, and the player with every doubt reviewed](docs/assets/screenshots/call.png)

## What it looks like

![Forty seconds in P.A.R.O.L.A.: a call is recorded with the floating recorder, transcribed, a doubtful word is corrected with a learned suggestion, exported, found with search and renamed](docs/assets/demo.gif)

[The same demo as a video](https://github.com/jacopo-grandi/parola/releases/download/v0.1.0/parola-demo.mp4).

The demo is a real recording of an invented line, transcribed by the app as it is: Whisper
hears "Parola" where "P.A.R.O.L.A." was said, and the review fixes it with a suggestion learned
from earlier corrections. The pictures come from the same recording, except the call still
being transcribed below, an invented dialogue voiced by two macOS text-to-speech voices.

**Review only the doubtful words.** Words that a second model does not confirm are underlined.
N opens the next one, plays just that passage and makes it editable; Enter saves or confirms.
Terms you corrected before are offered as one-click suggestions.

![Correcting the doubtful word "Parola": it is in an edit field while its audio plays, with the suggestion P.A.R.O.L.A. learned from past corrections](docs/assets/screenshots/review.png)

**Search every call.** The search box finds a word in all the transcripts (accents and elided
articles ignored) and lists where each call says it; Cmd-F finds it inside the call.

![Searching for "demo": the sidebar lists where each call says it, with a snippet](docs/assets/screenshots/search.png)

**Read while it transcribes.** The text grows as Whisper writes it; the doubtful words and the
voices are added when they are ready.

![A call still being transcribed: the provisional text, and its progress in the sidebar](docs/assets/screenshots/growing.png)

**A recorder above every window.** While a call is recorded, a small panel stays on top of Meet
or any other app, with the time, the levels of both sides, pause and stop. It shrinks to a dot
and can be dragged anywhere.

<p><img src="docs/assets/screenshots/recorder.png" alt="The floating recorder: a red dot, 00:06, the levels of both sides, pause, stop and shrink" width="252">
&nbsp;<img src="docs/assets/screenshots/recorder-dot.png" alt="The recorder shrunk to a dot" width="40"></p>

**Asked, never assumed.** On the first start the app says which models it needs and downloads
them only when you agree. A file you import is asked its language.

![The first-start screen listing the three models, their licences and sizes](docs/assets/screenshots/models.png)

![The language question for an imported file: Italiano, English or Rileva](docs/assets/screenshots/language.png)

## Install the app

On an Apple Silicon Mac with macOS 14.2 or later:

1. **Install.** In Terminal:

   ```bash
   git clone https://github.com/jacopo-grandi/parola.git
   cd parola
   ./install.sh
   ```

   The script checks the tools (Swift, Homebrew, `uv`, `ffmpeg`) and asks before installing any, builds
   `PAROLA.app`, and offers to copy it to /Applications and to open it.
2. **Open P.A.R.O.L.A.** from /Applications (or Launchpad, Spotlight). It lives in the menu bar
   too: closing the window keeps it ready to record.
3. **Download the models.** On the first start a "Prima di iniziare" screen shows up by itself:
   it lists the speech models, their licences and sizes (about 4.2 GB, once), and downloads them
   only when you press **Scarica i modelli**, with a progress bar. After that, nothing goes over
   the network.
4. **Record.** Press **Registra** in the window or in the menu bar. The first time, macOS asks
   for the microphone and for system audio: allow both. A small floating recorder stays on top
   of your call; stop it and the transcript appears in the list. When another app starts using
   the microphone (Meet in the browser, Zoom, FaceTime…), P.A.R.O.L.A. asks whether to record
   that call, and when the app lets the microphone go it asks whether the call is over. It never
   starts recording without a click; switch it off from the menu bar with **Proponi di
   registrare le call**.

Or let a coding agent do step 1: paste this into Claude Code, Codex or a similar agent on your
Mac:

> Install P.A.R.O.L.A. on this Mac: clone https://github.com/jacopo-grandi/parola into my
> home folder and follow the "Instructions for coding agents" in its INSTALL.md.

Details, updating, removing and troubleshooting: [`INSTALL.md`](INSTALL.md).

Something not working? [Open an issue](https://github.com/jacopo-grandi/parola/issues/new/choose)
with the output of `uv run parola doctor`.

## From the terminal

Everything the app does is also a `parola` command. For development, after `./install.sh` (or
`brew install uv ffmpeg` and `uv sync`):

```bash
uv run pre-commit install
```

The first transcription downloads `mlx-community/whisper-large-v3-turbo` (~1.6 GB) and
`mlx-community/parakeet-tdt-0.6b-v3` (~2.5 GB) from Hugging Face into the local cache, unless
the app already did. After that, nothing goes over the network.

## Usage

```bash
uv run parola transcribe path/to/call.m4a            # language auto-detected
uv run parola transcribe path/to/call.mp4 --lang it  # force Italian
uv run parola transcribe call.m4a --out transcripts/
```

Writes `<name>.json` (segments and words with timestamps) and `<name>.txt` (readable, one line
per segment) next to the input, or into `--out`.

### Recording a call

```bash
uv run parola record                       # Ctrl-C to stop; files in data/private/calls/
```

Records your microphone ("Io") and the system audio ("Altri") with Core Audio taps (macOS 14.2+,
no virtual driver), transcribes each side with Whisper turbo and marks the words a second
transcription (Parakeet v3) does not confirm as `(doubtful?)` in the text. The first run asks for
the microphone and system-audio recording permissions. Once the models are downloaded, nothing
goes over the network.

### Reading the transcripts

```bash
uv run parola serve                        # http://127.0.0.1:8765, local only
uv run parola compress --dry-run           # calls still stored as wav, and their size
uv run parola compress                     # lossless FLAC for them: ~100 MB per hour of call
uv run parola glossary                     # terms learned from your corrections (suggested, never applied)
uv run parola speakers NAME                # tell the voices apart on a call already transcribed
```

**● Registra** in the sidebar records a call without the terminal: timer, live levels for both
sides (an empty "Altri" meter means system audio is not reaching the app: check the permission),
pause, and a small floating window that stays on top of Meet (Chrome and Edge). **■** stops the
recording and transcribes it in the background. Run `parola serve` from the same app you granted
the audio permissions to (e.g. Terminal).

To transcribe an audio or video file recorded elsewhere, use the upload button next to
**Registra** or drop the file on the window. It asks the file's language first (Italiano,
English, or Rileva to detect it; the last choice is offered first). It goes through the same
transcription and doubtful words, on a single track: every voice is on it, so there is no "Io / Altri" split.
While a file or a recording is being transcribed it shows at the top of the list, with its
progress; click it to watch the text grow as Whisper writes it (read-only until the doubtful
words and the voices are in).

The sidebar lists the calls by date. Its search box looks inside every transcript (case, accents
and elided articles ignored: "perche" finds "perché", "impatto" finds "dell'impatto") and shows
where each call says it; a result opens the call at that point, with every occurrence marked
and Enter / Shift-Enter to move between them; the field at the top of a call (Cmd-F) searches that call. The ••• menu renames, suggests a title from the
content, or deletes (with undo). The others in a call (and everyone in an imported file) are told apart as "Altri 1", "Altri 2"...:
click a label to name the voice, use the dot next to a turn's time to move it to another voice
(Cmd-Z undoes it), or hide the voices with the people button. In a call, doubtful words are
highlighted and a click on a word plays the audio from there; while it plays, the text follows the word being said (it waits a few seconds after you scroll), and a click on the timeline shows that point in the text; the menu next to the timeline sets the speed (0.75× to 2×). N opens the next doubtful word (it plays and becomes editable):
Enter saves the correction (unchanged text confirms it) and stays put — N moves on, Esc cancels, a double click
edits any word, Cmd-Z undoes. Every change is logged with the model's original text, which is also
kept as `<name>.asr.json`. Export as txt, md, docx, json, or PDF through the browser's print.

**English calls in Italian (experimental).** With an English→Italian model on disk, the **IT**
toggle in a call shows the Italian translation under each English line, and the **CC** button
on the floating recorder writes live Italian subtitles under it, during a recording or without
recording anything. The translation is derived from the verbatim and never changes it. The app
does not download this model: convert Helsinki-NLP/opus-mt-en-it (Apache-2.0 on its model
card, CC-BY 4.0 in the OPUS-MT repository) to CTranslate2 int8 into
`data/models/opus-mt-en-it-ct2` with `training/totti/convert.sh`, in a separate environment
with `ctranslate2` and `transformers`. Until it is there, the toggles stay hidden. How the
engine was chosen and measured is in [METHODOLOGY.md](METHODOLOGY.md#translation-into-italian-totti).

### Writing reference transcripts

```bash
uv run parola annotate draft call.json         # model output -> editable call.ann.txt
uv run parola annotate import call.ann.txt     # reviewed annotation -> validated call.ref.json
uv run parola validate call.ref.json           # check a reference against the conventions
uv run parola manifest check data/manifests/test.jsonl --files
```

### Evaluating ASR systems

```bash
uv run parola eval                                  # all systems, references in the manifest
uv sync --group baselines                         # optional: Parakeet v3 and CrisperWhisper 2.0
uv run --group baselines parola eval --systems parakeet-v3,crisper-turbo-lang
```

Reports go to `results/` (markdown + csv); hypotheses are cached in `data/private/hyp/`.
CrisperWhisper weights are under a non-commercial research license: evaluation only.

To check whether a system's extra words were really spoken (references may have missed them):

```bash
uv run parola audit build --system crisper-turbo-lang   # clips + local page in data/private/audit/
uv run parola audit summary audit-answers.json          # after exporting the answers from the page
```

Transcription rules: [`docs/conventions.md`](docs/conventions.md). Annotation guide:
[`docs/annotation.md`](docs/annotation.md).

## For engineers

Transcribers usually clean speech up. This project keeps it — Italian, English, and both mixed
in the same sentence — and tells you where it is not sure. The interesting part is not the app
but the measurement around it: a fidelity-first metric suite with hand-built unit tests,
adjudicated references (every model disagreement decided by ear), bootstrap confidence
intervals, and a dated report in [`results/`](results/) behind every number. The method, the
models and why they were chosen: [`METHODOLOGY.md`](METHODOLOGY.md).

On the two adjudicated reference calls (fidelity WER, lower is better; details and confidence
intervals in [`METHODOLOGY.md`](METHODOLOGY.md)):

| System | Italian | English | Fillers kept (of 466) |
|---|---|---|---|
| **Whisper large-v3-turbo** (default) | **4.8** | 5.3 | 2 |
| CrisperWhisper 2.0 turbo | 8.5 | **2.7** | 448 |
| Parakeet TDT 0.6B v3 | 16.7 | 10.1 | 315 |

The honest gap is in that last column: the engine that is most faithful in Italian also drops
nearly every filler, so today "word for word" means no rewording — not full verbatim, which
comes from the doubtful-word review where it matters. The open next step is a LoRA for Italian
verbatim, trained on calls recorded with consent and annotated verbatim by hand
([`docs/annotation.md`](docs/annotation.md)) — a corpus that does not exist publicly.

## Development

```bash
uv run pytest            # fast tests
uv run pytest -m slow    # also runs the real model on synthetic speech
uv run ruff check . && uv run ruff format .
```

Audio files and anything under `data/` (except `data/manifests/`) are ignored by git and
blocked by a pre-commit hook. Never commit recordings or personal data.

## License

Code: [MIT](LICENSE). The license of fine-tuned model weights will depend on the training data.

The P.A.R.O.L.A. name and logo identify this project and are not covered by the MIT
license: forks and derived works may not present themselves under them.
