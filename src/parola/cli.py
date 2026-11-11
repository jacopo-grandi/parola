"""Command-line interface.

parola transcribe <file>              audio/video -> <name>.json + <name>.txt
parola record                         record a call (mic + system audio) and transcribe it
parola serve                          local web UI for the recorded calls
parola titles                         suggested titles next to the ones you chose
parola compress [--dry-run]           lossless FLAC for calls still stored as wav
parola glossary                       terms learned from your corrections
parola speakers <name>                tell the voices apart on a call already transcribed
parola validate <transcript.json>     check a reference against docs/conventions.md
parola annotate draft <asr.json>      ASR transcript -> editable <name>.ann.txt
parola annotate import <name.ann.txt> edited annotation -> validated <name>.ref.json
parola manifest check <file.jsonl>    validate a dataset manifest
parola eval [--systems a,b]           run ASR systems on the test set and write a report
parola audit build --system <name>    sample a system's insertions for a listening check
parola audit summary <answers.json>   share of insertions that were really spoken
parola adjudicate build <id>          listen only where two transcripts disagree
parola adjudicate apply <id> <answers.json>  write the adjudicated reference
"""

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from parola import __version__, adjudicate
from parola.annotate import AnnotationError, from_annotation, merge_segments, to_annotation
from parola.asr import DEFAULT_MODEL, MLXWhisperBackend
from parola.audio import FFmpegError
from parola.eval import audit
from parola.eval.report import run_system, score_system, write_report
from parola.eval.systems import SYSTEMS
from parola.manifest import check_manifest
from parola.pipeline import transcribe_file
from parola.schema import Transcript
from parola.schema.lint import has_errors, lint

LANGUAGES = ("auto", "it", "en")
ENGINES = ("turbo", "crisper")
CRISPER_NOTICE = (
    "CrisperWhisper 2.0 weights and outputs are licensed for non-commercial use only "
    "(nyra health Non-Commercial Research License): do not use it for commercial purposes. "
    "See METHODOLOGY.md (Models)."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="parola", description="Local verbatim transcription.")
    parser.add_argument("--version", action="version", version=f"parola {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    t = sub.add_parser("transcribe", help="transcribe an audio or video file")
    t.add_argument("file", type=Path, help="input audio/video file (m4a, mp4, wav, ...)")
    t.add_argument(
        "--lang",
        choices=LANGUAGES,
        default="auto",
        help="force the language or detect it automatically (default: auto)",
    )
    t.add_argument(
        "--out", type=Path, default=None, help="output folder (default: next to the input)"
    )
    t.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"mlx-whisper model (default: {DEFAULT_MODEL})"
    )

    rc = sub.add_parser(
        "record", help="record a call (microphone + system audio) and transcribe it"
    )
    rc.add_argument("--out", type=Path, default=Path("data/private/calls"),
                    help="output folder (default: data/private/calls, ignored by git)")  # fmt: skip
    rc.add_argument("--name", default=None, help="file name (default: call-<date>-<time>)")
    rc.add_argument("--lang", choices=LANGUAGES, default="auto")
    rc.add_argument("--duration", type=float, default=None, help="stop after N seconds")
    rc.add_argument("--no-mic", action="store_true", help="system audio only")
    rc.add_argument("--no-system", action="store_true", help="microphone only")
    rc.add_argument(
        "--no-doubt",
        action="store_true",
        help="skip the second opinion (Parakeet v3) that marks doubtful words",
    )
    rc.add_argument(
        "--engine",
        choices=ENGINES,
        default="turbo",
        help="turbo (default, MIT) or crisper (CrisperWhisper 2.0: non-commercial use only, "
        "needs `uv sync --group baselines`)",
    )

    sub.add_parser("doctor", help="this Mac and the install, to paste into a bug report")

    sv = sub.add_parser("serve", help="local web UI for the recorded calls")
    sv.add_argument("--dir", type=Path, default=Path("data/private/calls"),
                    help="folder with the recordings (default: data/private/calls)")  # fmt: skip
    sv.add_argument("--port", type=int, default=8765)

    sp = sub.add_parser("speakers", help="tell the voices apart on a call already transcribed")
    sp.add_argument("name")
    sp.add_argument("--dir", type=Path, default=Path("data/private/calls"))

    gl = sub.add_parser("glossary", help="terms learned from your corrections")
    gl.add_argument("--dir", type=Path, default=Path("data/private/calls"))

    co = sub.add_parser("compress", help="lossless FLAC for calls still stored as wav")
    co.add_argument("--dir", type=Path, default=Path("data/private/calls"))
    co.add_argument("--dry-run", action="store_true", help="list what would be compressed")

    ti = sub.add_parser("titles", help="suggested titles next to the ones you chose")
    ti.add_argument("--dir", type=Path, default=Path("data/private/calls"))

    to = sub.add_parser("totti", help="EN→IT translation: test set draft and import")
    tosub = to.add_subparsers(dest="action", required=True)
    tod = tosub.add_parser("draft", help="English sentences from the calls, to translate by hand")
    tod.add_argument("--dir", type=Path, default=Path("data/private/calls"))
    tod.add_argument("-o", "--out", type=Path, default=Path("data/private/totti/test-en-it.txt"))
    tod.add_argument("--limit", type=int, default=300)
    tod.add_argument("--force", action="store_true", help="overwrite an existing file")
    toi = tosub.add_parser("import", help="convert the filled draft into the frozen test set")
    toi.add_argument("file", type=Path, help="the draft with every IT: line filled")
    toi.add_argument("-o", "--out", type=Path, default=None, help="default: <name>.jsonl")
    tor = tosub.add_parser("review", help="translate new test-set sentences with the audio at hand")
    tor.add_argument("--dir", type=Path, default=Path("data/private/calls"))
    tor.add_argument("--frozen", type=Path, default=Path("data/private/totti/test-en-it.jsonl"))
    tor.add_argument("--out", type=Path, default=Path("data/private/totti/review-additions.jsonl"))
    tor.add_argument("--port", type=int, default=8798)
    tot = tosub.add_parser("distill", help="teacher pass over the source sentences (resumable)")
    tot.add_argument("--sources", type=Path, default=Path("data/totti-train/source-en.jsonl"))
    tot.add_argument("--out", type=Path, default=Path("data/totti-train/distilled-madlad.jsonl"))
    tot.add_argument("--model", type=Path, default=Path("data/models/madlad400-3b-mt-ct2"))
    tot.add_argument("--threads", type=int, default=8)

    v = sub.add_parser("validate", help="check a reference transcript against the conventions")
    v.add_argument("file", type=Path)

    a = sub.add_parser("annotate", help="write references by hand")
    asub = a.add_subparsers(dest="action", required=True)
    d = asub.add_parser("draft", help="turn an ASR transcript into an editable annotation file")
    d.add_argument("file", type=Path, help="transcript JSON produced by `parola transcribe`")
    d.add_argument("-o", "--out", type=Path, default=None, help="default: <name>.ann.txt")
    d.add_argument("--force", action="store_true", help="overwrite an existing file")
    d.add_argument(
        "--merge-gap",
        type=float,
        default=1.0,
        help="join segments separated by at most this many seconds, up to 30 s (default: 1.0)",
    )
    d.add_argument("--no-merge", action="store_true", help="keep the ASR segments as they are")
    i = asub.add_parser("import", help="convert an edited annotation file to a reference JSON")
    i.add_argument("file", type=Path, help="annotation file (.ann.txt)")
    i.add_argument("-o", "--out", type=Path, default=None, help="default: <name>.ref.json")

    m = sub.add_parser("manifest", help="dataset manifests")
    msub = m.add_subparsers(dest="action", required=True)
    c = msub.add_parser("check", help="validate a manifest")
    c.add_argument("file", type=Path)
    c.add_argument("--root", type=Path, default=Path("."), help="repository root (default: .)")
    c.add_argument("--files", action="store_true", help="also open audio and reference files")

    e = sub.add_parser("eval", help="run ASR systems on the test set and write a report")
    e.add_argument(
        "--systems",
        default=",".join(SYSTEMS),
        help=f"comma-separated systems (default: all of {', '.join(SYSTEMS)})",
    )
    e.add_argument("--manifest", type=Path, default=Path("data/manifests/test.jsonl"))
    e.add_argument("--root", type=Path, default=Path("."), help="repository root (default: .)")
    e.add_argument("--hyp-dir", type=Path, default=Path("data/private/hyp"))
    e.add_argument("--out", type=Path, default=None, help="default: results/<date>-baseline")
    e.add_argument("--force", action="store_true", help="re-run cached transcriptions")
    e.add_argument(
        "--seed", type=int, default=0, help="random seed for decoding (use another --hyp-dir)"
    )

    au = sub.add_parser("audit", help="listening check of a system's insertions")
    ausub = au.add_subparsers(dest="action", required=True)
    ab = ausub.add_parser("build", help="sample insertions, cut clips, write a review page")
    ab.add_argument("--system", required=True, choices=list(SYSTEMS))
    ab.add_argument("--manifest", type=Path, default=Path("data/manifests/test.jsonl"))
    ab.add_argument("--root", type=Path, default=Path("."))
    ab.add_argument("--hyp-dir", type=Path, default=Path("data/private/hyp"))
    ab.add_argument("--out", type=Path, default=None, help="default: data/private/audit/<system>")
    ab.add_argument("--seed", type=int, default=0, help="sampling seed")
    asm = ausub.add_parser("summary", help="summarize exported answers")
    asm.add_argument("file", type=Path)

    ad = sub.add_parser("adjudicate", help="decide by listening where two transcripts disagree")
    adsub = ad.add_subparsers(dest="action", required=True)
    adb = adsub.add_parser("build", help="clips and review page for one manifest record")
    adb.add_argument("id")
    adb.add_argument("--manifest", type=Path, default=Path("data/manifests/test.jsonl"))
    adb.add_argument("--root", type=Path, default=Path("."))
    adb.add_argument("--hyp-dir", type=Path, default=Path("data/private/hyp"))
    adb.add_argument("--out", type=Path, default=None, help="default: data/private/adjudicate/<id>")
    adb.add_argument(
        "--listen",
        default="",
        help="comma-separated kinds to always listen to instead of accepting them "
        f"automatically ({', '.join(sorted(adjudicate.AUTO_KINDS | adjudicate.SAMPLED_KINDS))})",
    )
    ada = adsub.add_parser("apply", help="apply exported answers and update the manifest")
    ada.add_argument("id")
    ada.add_argument("answers", type=Path)
    ada.add_argument("--manifest", type=Path, default=Path("data/manifests/test.jsonl"))
    ada.add_argument("--root", type=Path, default=Path("."))
    ada.add_argument("--dir", type=Path, default=None, help="default: data/private/adjudicate/<id>")
    ada.add_argument(
        "--accept-anyway", action="store_true", help="apply even if a rule check fails"
    )
    return parser


def _strip_suffixes(path: Path, *suffixes: str) -> str:
    name = path.name
    for s in suffixes:
        if name.endswith(s):
            return name[: -len(s)]
    return path.stem


def _print_issues(issues) -> None:
    for issue in issues:
        print(issue, file=sys.stderr)


def cmd_transcribe(args) -> int:
    language = None if args.lang == "auto" else args.lang
    try:
        outputs = transcribe_file(
            args.file, MLXWhisperBackend(args.model), out_dir=args.out, language=language
        )
    except (FileNotFoundError, FFmpegError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"wrote {outputs.json_path}")
    print(f"wrote {outputs.txt_path}")
    return 0


def make_engine(engine: str):
    if engine == "crisper":
        print(f"notice: {CRISPER_NOTICE}", file=sys.stderr)
        from parola.asr.crisper_backend import CrisperBackend

        return CrisperBackend()
    # Call channels are often silent while the other side talks: VAD skips silent channels,
    # drops text written over silence and picks the language on speech
    # (results/2026-10-15-vad-analisi.md).
    return MLXWhisperBackend(vad=True)


def cmd_record(args) -> int:
    from parola.capture.recorder import main_record

    language = None if args.lang == "auto" else args.lang
    if args.engine == "crisper" and language is None:
        print("error: --engine crisper needs --lang it or --lang en", file=sys.stderr)
        return 1
    second = None
    if not args.no_doubt:
        from parola.asr.parakeet_backend import ParakeetBackend

        second = ParakeetBackend()
    return main_record(args.out, args.name, make_engine(args.engine), language,
                       args.duration, not args.no_mic, not args.no_system,
                       second=second)  # fmt: skip


def cmd_speakers(args) -> int:
    """Voices on the channels that can hold several (decision 0026), for an existing call."""
    from parola import diarize
    from parola.annotate import merge_segments
    from parola.audio import channel_file
    from parola.capture.recorder import VOICED_CHANNELS, to_dialogue
    from parola.schema import Transcript
    from parola.speakers import assign

    path = args.dir / f"{args.name}.json"
    t = Transcript.load(path)
    if t.edits:
        print("error: the call has edits; their segment numbers would no longer match",
              file=sys.stderr)  # fmt: skip
        return 1
    if not diarize.installed():
        print("error: sherpa-onnx is not installed", file=sys.stderr)
        return 1
    diarize.ensure_models()
    for channel in VOICED_CHANNELS:
        audio = channel_file(args.dir, args.name, channel)
        if audio is None or not any(s.channel == channel for s in t.segments):
            continue
        turns = diarize.diarize(audio)
        t = assign(t, channel, turns)
        voices = sorted({s.speaker for s in t.segments if s.speaker and s.channel == channel})
        print(f"{channel}: {len(voices)} voices")
    t = merge_segments(t)
    t.save(path)
    (args.dir / f"{args.name}.txt").write_text(to_dialogue(t), encoding="utf-8")
    return 0


def cmd_glossary(args) -> int:
    """What the app has learned from the corrections in the calls (decision 0025)."""
    from parola.learned import MIN_CORRECTIONS, load_terms

    terms = sorted(load_terms(args.dir).values(), key=lambda t: (-t.count, t.text.lower()))
    if not terms:
        print("nothing learned yet: corrections of names, acronyms and rare words are kept")
        return 0
    for t in terms:
        heard = ", ".join(f"{h} ({n})" for h, n in t.heard.most_common())
        state = "suggested" if t.count >= MIN_CORRECTIONS else f"after {MIN_CORRECTIONS}"
        print(f"{t.text:24} {t.count:>3}x  {state:10}  heard as: {heard}")
    return 0


def cmd_compress(args) -> int:
    """Replace the wav of each transcribed call with a FLAC of the same samples the models hear
    (decision 0024). Calls not transcribed yet are left alone."""
    from parola.app.server import recordings
    from parola.audio import FFmpegError, compress
    from parola.capture.recorder import CHANNELS

    mb = 1024 * 1024
    before = after = 0
    failed = 0
    for name in recordings(args.dir):
        for c in CHANNELS:
            wav = args.dir / f"{name}.{c}.wav"
            if not wav.exists():
                continue
            size = wav.stat().st_size
            if args.dry_run:
                print(f"would compress {wav.name} ({size / mb:.0f} MB)")
                before += size
                continue
            try:
                flac = compress(wav)
            except FFmpegError as e:
                print(f"kept {wav.name}: {e}", file=sys.stderr)
                failed += 1
                continue
            before += size
            after += flac.stat().st_size
            print(
                f"{wav.name}: {size / mb:.0f} MB -> {flac.name} {flac.stat().st_size / mb:.1f} MB"
            )
    if args.dry_run:
        print(f"{before / mb:.0f} MB of wav; about a twentieth of it once compressed")
    elif before:
        print(f"{before / mb:.0f} MB -> {after / mb:.0f} MB")
    else:
        print("nothing to compress")
    return 1 if failed else 0


def cmd_titles(args) -> int:
    """How suggested titles fare (decision 0023): a call you renamed is a reference title."""
    from parola.app.server import recordings
    from parola.schema import Transcript

    kept = renamed = 0
    for name in recordings(args.dir):
        meta = Transcript.load(args.dir / f"{name}.json").metadata
        if not meta.title_suggested:
            continue
        if meta.title and meta.title != meta.title_suggested:
            renamed += 1
            print(f"{name}\n  suggested: {meta.title_suggested}\n  yours:     {meta.title}")
        else:
            kept += 1
            print(f"{name}\n  kept:      {meta.title_suggested}")
    total = kept + renamed
    if not total:
        print("no calls with a suggested title yet: record or import a few", file=sys.stderr)
        return 0
    print(f"\n{kept} of {total} suggested titles kept, {renamed} renamed")
    return 0


def cmd_doctor(args) -> int:
    from parola import doctor

    return doctor.main()


def cmd_serve(args) -> int:
    import uvicorn

    from parola.app.server import create_app

    if not args.dir.is_dir():
        # A fresh clone has no data/private/calls yet: the first call goes there.
        args.dir.mkdir(parents=True)
        print(f"parola: created {args.dir} for the calls", file=sys.stderr)
    import socket

    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            print(f"parola: something is already running on http://127.0.0.1:{args.port} "
                  "(probably parola serve): open it, or use --port", file=sys.stderr)  # fmt: skip
            return 1
    print(f"parola: http://127.0.0.1:{args.port} (Ctrl-C to stop)", file=sys.stderr)
    # Bound to localhost only: the recordings never leave this machine.
    uvicorn.run(create_app(args.dir), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def cmd_validate(args) -> int:
    transcript = Transcript.load(args.file)
    issues = lint(transcript)
    _print_issues(issues)
    n_err = sum(i.severity == "error" for i in issues)
    print(f"{args.file}: {n_err} errors, {len(issues) - n_err} warnings")
    return 1 if n_err else 0


def cmd_annotate_draft(args) -> int:
    out = args.out or args.file.with_name(_strip_suffixes(args.file, ".json") + ".ann.txt")
    if out.exists() and not args.force:
        print(f"error: {out} exists (use --force to overwrite)", file=sys.stderr)
        return 1
    transcript = Transcript.load(args.file)
    n_asr = len(transcript.segments)
    if not args.no_merge:
        transcript = merge_segments(transcript, max_gap=args.merge_gap)
    out.write_text(to_annotation(transcript, draft=True), encoding="utf-8")
    print(f"wrote {out} ({len(transcript.segments)} segments from {n_asr})")
    return 0


def cmd_annotate_import(args) -> int:
    try:
        transcript = from_annotation(args.file.read_text(encoding="utf-8"))
    except AnnotationError as e:
        print(f"error: {args.file}: {e}", file=sys.stderr)
        return 1
    issues = lint(transcript)
    _print_issues(issues)
    if has_errors(issues):
        print("not written: fix the errors above and import again", file=sys.stderr)
        return 1
    out = args.out or args.file.with_name(_strip_suffixes(args.file, ".ann.txt") + ".ref.json")
    transcript.save(out)
    print(f"wrote {out}")
    return 0


def cmd_manifest_check(args) -> int:
    report = check_manifest(args.file, args.root, check_files=args.files)
    for w in report.warnings:
        print(f"warning: {w}", file=sys.stderr)
    for e in report.errors:
        print(f"error: {e}", file=sys.stderr)
    print(report.summary())
    return 1 if report.errors else 0


def cmd_eval(args) -> int:
    names = [n.strip() for n in args.systems.split(",") if n.strip()]
    unknown = [n for n in names if n not in SYSTEMS]
    if unknown:
        print(f"error: unknown systems {unknown}; known: {list(SYSTEMS)}", file=sys.stderr)
        return 1
    report = check_manifest(args.manifest, args.root, check_files=True)
    if report.errors:
        for e in report.errors:
            print(f"error: {e}", file=sys.stderr)
        return 1
    records = [r for r in report.records if r.reference is not None]
    if not records:
        print("error: no references in the manifest", file=sys.stderr)
        return 1
    systems = [SYSTEMS[n] for n in names]
    rows = []
    for system in systems:
        print(f"[{system.name}] transcribing {len(records)} files (cached ones are skipped)")
        timing = run_system(
            system, records, args.root, args.hyp_dir, force=args.force, seed=args.seed
        )
        rows += score_system(system, records, args.root, args.hyp_dir, timing)
    out = args.out or Path("results") / f"{date.today().isoformat()}-baseline"
    md, csv_path = write_report(rows, systems, records, out)
    print(f"wrote {md}")
    print(f"wrote {csv_path}")
    return 0


def cmd_audit_build(args) -> int:
    report = check_manifest(args.manifest, args.root, check_files=True)
    if report.errors:
        for e in report.errors:
            print(f"error: {e}", file=sys.stderr)
        return 1
    records = [r for r in report.records if r.reference is not None]
    insertions = []
    for r in records:
        hyp_path = args.hyp_dir / args.system / f"{Path(r.audio).stem}.json"
        if not hyp_path.exists():
            print(f"error: {hyp_path} missing: run `parola eval --systems {args.system}` first",
                  file=sys.stderr)  # fmt: skip
            return 1
        ref = Transcript.load(args.root / r.reference)
        insertions += audit.find_insertions(r.id, ref, Transcript.load(hyp_path))
    available = Counter(i.category for i in insertions)
    print("insertions found:", dict(available))
    items = audit.sample(insertions, seed=args.seed)
    out = args.out or Path("data/private/audit") / args.system
    (out / "clips").mkdir(parents=True, exist_ok=True)
    audio_of = {r.id: args.root / r.audio for r in records}
    for ins in items:
        audit.cut_clip(
            audio_of[ins.file_id], ins.start, ins.end, out / "clips" / f"{ins.item_id}.m4a"
        )
    page = audit.write_page(items, args.system, out)
    print(f"wrote {page} ({len(items)} items)")
    return 0


def cmd_audit_summary(args) -> int:
    print(audit.summarize(json.loads(args.file.read_text(encoding="utf-8"))))
    return 0


CRISPER, TURBO = "crisper-turbo-lang", "turbo"


def _record(manifest: Path, root: Path, record_id: str):
    for r in check_manifest(manifest, root).records:
        if r.id == record_id:
            return r
    raise FileNotFoundError(f"no record {record_id!r} in {manifest}")


def cmd_adjudicate_build(args) -> int:
    record = _record(args.manifest, args.root, args.id)
    stem = Path(record.audio).stem
    crisper = Transcript.load(args.hyp_dir / CRISPER / f"{stem}.json")
    if record.reference:
        base, other = Transcript.load(args.root / record.reference), crisper
        labels = ("riferimento attuale", "CrisperWhisper")
    else:
        # No reference yet: start from CrisperWhisper (closer to verbatim, see
        # results/2026-10-15-audit-crisper.md) and check it against turbo.
        base = merge_segments(crisper)
        other = Transcript.load(args.hyp_dir / TURBO / f"{stem}.json")
        labels = ("CrisperWhisper", "Whisper turbo")
    listen = {k.strip() for k in args.listen.split(",") if k.strip()}
    regions = adjudicate.apply_policy(
        adjudicate.find_regions(base, other),
        listen=listen,
        verbatim_side="B" if record.reference else "A",
    )
    out = args.out or Path("data/private/adjudicate") / args.id
    out.mkdir(parents=True, exist_ok=True)
    base.save(out / "base.json")
    page = adjudicate.build(regions, args.root / record.audio, args.id, labels, out)
    words = sum(len(s.words) for s in base.segments)
    kinds = Counter(r.kind for r in regions)
    shown = sum(1 for r in regions if r.auto is None or r.check)
    print(f"{len(regions)} disagreements over {words} words: {dict(kinds.most_common())}")
    print(f"to listen: {shown} ({sum(r.check for r in regions)} rule checks); "
          f"accepted automatically: {len(regions) - shown}")  # fmt: skip
    print(f"wrote {page}")
    return 0


def cmd_adjudicate_apply(args) -> int:
    record = _record(args.manifest, args.root, args.id)
    work = args.dir or Path("data/private/adjudicate") / args.id
    base = Transcript.load(work / "base.json")
    regions = adjudicate.load_regions(work)
    given = json.loads(args.answers.read_text(encoding="utf-8"))["answers"]
    agreement = adjudicate.rule_agreement(regions, given)
    failed = []
    for kind, (agree, total) in sorted(agreement.items()):
        print(f"rule check {kind}: {agree}/{total} agree with the rule")
        if total >= 3 and agree / total < adjudicate.MIN_RULE_AGREEMENT:
            failed.append(kind)
    if failed and not args.accept_anyway:
        print(f"rule checks failed for {failed}: rebuild with --listen {','.join(failed)} "
              "(answers are kept) or pass --accept-anyway", file=sys.stderr)  # fmt: skip
        return 1
    answers = adjudicate.merged_answers(regions, given)
    result = adjudicate.apply(base, regions, answers)
    if base.metadata.kind == "reference":
        text = to_annotation(result)
    else:
        text = to_annotation(result, draft=True).replace("# status: draft", "# status: reviewed", 1)
    testset = (args.root / record.audio).parent
    ann_path = testset / f"{args.id}.adjudicated.ann.txt"
    ann_path.write_text(text, encoding="utf-8")
    reference = from_annotation(text)
    issues = lint(reference)
    _print_issues(issues)
    if has_errors(issues):
        print(f"not written: fix {ann_path}, then `parola annotate import` it", file=sys.stderr)
        return 1
    ref_path = testset / f"{args.id}.ref.json"
    backup = testset / f"{args.id}.ref.{record.reference_quality}.json"
    if ref_path.exists() and record.reference_quality and not backup.exists():
        ref_path.rename(backup)
        print(f"previous reference kept as {backup}")
    reference.save(ref_path)
    choices = Counter((answers.get(r.item_id) or {}).get("choice") or "A" for r in regions)
    by_kind: dict[str, Counter] = {}
    for r in regions:
        if r.item_id in given and (given[r.item_id] or {}).get("choice"):
            by_kind.setdefault(r.kind, Counter())[given[r.item_id]["choice"]] += 1
    for kind, c in sorted(by_kind.items()):
        print(f"listened {kind}: A {c['A']}, B {c['B']}, text {c['text']}")
    n_auto = sum(1 for r in regions if r.auto and r.item_id not in given)
    checks = ", ".join(f"{k} {a}/{t}" for k, (a, t) in sorted(agreement.items())) or "none"
    note = (f"Adjudicated {date.today().isoformat()}: {len(regions)} disagreements "
            f"(A {choices['A']}, B {choices['B']}, text {choices['text']}; "
            f"{n_auto} accepted by rule, checks: {checks}).")  # fmt: skip
    lines = args.manifest.read_text(encoding="utf-8").splitlines()
    out_lines = []
    for line in lines:
        if line.strip() and json.loads(line)["id"] == args.id:
            rec = json.loads(line)
            rec["reference"] = ref_path.relative_to(args.root).as_posix()
            rec["reference_quality"] = "adjudicated"
            rec["derived_from"] = sorted(set(rec.get("derived_from", [])) | {"crisperwhisper"})
            rec["notes"] = ((rec.get("notes") or "") + " " + note).strip()
            line = json.dumps(rec, ensure_ascii=False)
        out_lines.append(line)
    args.manifest.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"wrote {ref_path}; manifest updated ({note})")
    return 0


def _models_for(args) -> list[str]:
    """Model repos a command needs, to decide whether it can run offline."""
    if args.command == "transcribe":
        return [args.model]
    if args.command == "record":
        repos = [DEFAULT_MODEL if args.engine == "turbo" else "nyralabs/CrisperWhisper2.0_turbo"]
        if not args.no_doubt:
            repos.append("mlx-community/parakeet-tdt-0.6b-v3")
        return repos
    if args.command == "eval":
        return sorted({SYSTEMS[n.strip()].model for n in args.systems.split(",") if n.strip()
                       in SYSTEMS})  # fmt: skip
    return []


def cmd_totti_draft(args) -> int:
    from parola.totti.evalset import pick, to_draft

    if args.out.exists() and not args.force:
        print(f"error: {args.out} exists (use --force to overwrite)", file=sys.stderr)
        return 1
    pairs = pick(args.dir, limit=args.limit)
    if not pairs:
        print(f"error: no English sentences found in {args.dir}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(to_draft(pairs), encoding="utf-8")
    print(f"wrote {args.out} ({len(pairs)} sentences from {len({p.call for p in pairs})} calls)")
    return 0


def cmd_totti_import(args) -> int:
    from parola.totti.evalset import EvalSetError, from_draft, save

    try:
        pairs = from_draft(args.file.read_text(encoding="utf-8"))
    except EvalSetError as e:
        print(f"error: {args.file}: {e}", file=sys.stderr)
        return 1
    out = save(pairs, args.out or args.file.with_suffix(".jsonl"))
    print(f"wrote {out} ({len(pairs)} pairs)")
    return 0


def cmd_totti_review(args) -> int:
    import uvicorn

    from parola.totti.review import create_review_app, pending

    n = len(pending(args.dir, args.frozen))
    print(f"{n} candidate sentences · http://127.0.0.1:{args.port} · Ctrl-C per chiudere",
          file=sys.stderr)  # fmt: skip
    app = create_review_app(args.dir, args.frozen, args.out)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def cmd_totti_distill(args) -> int:
    import ctranslate2
    import sentencepiece

    from parola.totti.distill import translate_sources

    sp = sentencepiece.SentencePieceProcessor(str(args.model / "spiece.model"))
    translator = ctranslate2.Translator(str(args.model), device="cpu", inter_threads=1,
                                        intra_threads=args.threads)  # fmt: skip

    def translate(texts: list[str]) -> list[str]:
        tokens = [[*sp.encode(f"<2it> {t}", out_type=str), "</s>"] for t in texts]
        res = translator.translate_batch(tokens, beam_size=2, max_batch_size=32,
                                         max_decoding_length=256)  # fmt: skip
        return [sp.decode(r.hypotheses[0]) for r in res]

    def progress(done: int, total: int) -> None:
        print(f"\rtranslated {done}/{total}", end="", file=sys.stderr)

    n = translate_sources(args.sources, args.out, translate, progress=progress)
    print(f"\n{n} new pairs in {args.out}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if repos := _models_for(args):
        from parola.models import offline_if_cached

        offline_if_cached(repos)
    handlers = {
        ("transcribe", None): cmd_transcribe,
        ("record", None): cmd_record,
        ("serve", None): cmd_serve,
        ("doctor", None): cmd_doctor,
        ("titles", None): cmd_titles,
        ("compress", None): cmd_compress,
        ("glossary", None): cmd_glossary,
        ("speakers", None): cmd_speakers,
        ("validate", None): cmd_validate,
        ("annotate", "draft"): cmd_annotate_draft,
        ("annotate", "import"): cmd_annotate_import,
        ("totti", "draft"): cmd_totti_draft,
        ("totti", "import"): cmd_totti_import,
        ("totti", "distill"): cmd_totti_distill,
        ("totti", "review"): cmd_totti_review,
        ("manifest", "check"): cmd_manifest_check,
        ("eval", None): cmd_eval,
        ("audit", "build"): cmd_audit_build,
        ("audit", "summary"): cmd_audit_summary,
        ("adjudicate", "build"): cmd_adjudicate_build,
        ("adjudicate", "apply"): cmd_adjudicate_apply,
    }
    try:
        return handlers[(args.command, getattr(args, "action", None))](args)
    except (FileNotFoundError, ValidationError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def main_ilat(argv: list[str] | None = None) -> int:
    """The old command name: same as parola, with a notice (decision 0019)."""
    print("ilat is now called parola: use `parola` (ilat will be removed later)", file=sys.stderr)
    return main(argv)


if __name__ == "__main__":
    sys.exit(main())
