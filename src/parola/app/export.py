"""Exports of a transcript (Word, Markdown) and of a whole project as a zip (decision 0059)."""

from __future__ import annotations

import re
import zipfile
from datetime import datetime
from io import BytesIO

from parola.capture.recorder import LABELS, marked_text, to_dialogue
from parola.schema import Transcript, format_timestamp
from parola.speakers import label as speaker_label

# Characters no file system takes in a name.
UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
FORMATS = ("txt", "md", "docx")


def safe_stem(title: str, fallback: str) -> str:
    return UNSAFE.sub("", title).strip() or fallback


def to_markdown(t: Transcript, name: str) -> str:
    lines = [f"# {name}", ""]
    for s in t.segments:
        label = speaker_label(t, s, LABELS)
        who = f"**{label}** · " if label else ""
        lines.append(f"{who}{format_timestamp(s.start)[:8]} — {marked_text(s)}")
        lines.append("")
    return "\n".join(lines)


def render(t: Transcript, title: str, fmt: str) -> bytes:
    if fmt == "docx":
        return to_docx(t, title)
    return (to_dialogue(t) if fmt == "txt" else to_markdown(t, title)).encode("utf-8")


def project_zip(calls: list[tuple[str, str, Transcript]], fmt: str) -> bytes:
    """One file per call, oldest first, named "2026-11-23 09.41 Titolo.docx": ``calls`` are
    (started_at in ISO 8601, title, transcript), in any order."""
    if fmt not in FORMATS:
        raise ValueError(f"no such format: {fmt}")
    buf = BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for started_at, title, t in sorted(calls, key=lambda c: datetime.fromisoformat(c[0])):
            when = datetime.fromisoformat(started_at).astimezone()  # the Mac's local time
            stem = f"{when:%Y-%m-%d %H.%M} {safe_stem(title, 'call')}"
            name, k = f"{stem}.{fmt}", 2
            while name.casefold() in used:  # two calls with the same title in the same minute
                name, k = f"{stem} ({k}).{fmt}", k + 1
            used.add(name.casefold())
            z.writestr(name, render(t, title, fmt))
    return buf.getvalue()


def to_docx(t: Transcript, name: str) -> bytes:
    from docx import Document
    from docx.enum.text import WD_COLOR_INDEX

    doc = Document()
    doc.add_heading(name, level=1)
    doubtful = sum(w.doubtful for s in t.segments for w in s.words)
    if doubtful:
        doc.add_paragraph(f"Parole evidenziate: {doubtful} ancora da verificare (una seconda "
                          "trascrizione dice altro).")  # fmt: skip
    for seg in t.segments:
        p = doc.add_paragraph()
        label = speaker_label(t, seg, LABELS)
        stamp = format_timestamp(seg.start)[:8]
        head = p.add_run(f"{label} · {stamp}  " if label else f"{stamp}  ")
        head.bold = True
        words = seg.words or []
        if not words:
            p.add_run(seg.text)
            continue
        for k, w in enumerate(words):
            run = p.add_run(w.text + (" " if k < len(words) - 1 else ""))
            if w.doubtful:
                run.font.highlight_color = WD_COLOR_INDEX.YELLOW
    out = BytesIO()
    doc.save(out)
    return out.getvalue()
