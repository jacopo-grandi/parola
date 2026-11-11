"""The test-set review page (ToTTI, quality plan move A): translate with the audio at hand.

``parola totti review`` serves a local page listing the English sentences of the recorded
calls that are not yet in the frozen test set, each with the audio of its segment embedded
(the format Jacopo prefers: players with visible controls, on localhost — answers written on
his disk, never a data: URL). Saving appends the filled pairs to an additions file; merging
them into the frozen set is an explicit, separate step.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from parola.audio import FFmpegError
from parola.audio.compress import channel_file
from parola.schema import Transcript
from parola.totti.evalset import Pair, pick

# Air around a segment's words, so a clip never cuts the first or last one.
CLIP_MARGIN = 0.2


def pending(folder: Path, frozen: Path) -> list[Pair]:
    """The candidate pairs not already in the frozen test set (by call and segment)."""
    done: set[tuple[str, int]] = set()
    if frozen.exists():
        for line in frozen.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                done.add((d["call"], d["segment"]))
    return [p for p in pick(folder, limit=10_000) if (p.call, p.segment) not in done]


def clip_command(audio: Path, start: float, end: float) -> list[str]:
    """The ffmpeg command that writes one segment's audio as a wav to stdout."""
    return [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(start - CLIP_MARGIN, 0):.2f}", "-to", f"{end + CLIP_MARGIN:.2f}",
        "-i", str(audio), "-ac", "1", "-f", "wav", "pipe:1",
    ]  # fmt: skip


def clip(folder: Path, name: str, segment: int) -> bytes:
    """The audio of one segment of a call, as wav bytes."""
    t = Transcript.load(folder / f"{name}.json")
    seg = t.segments[segment]
    audio = channel_file(folder, name, seg.channel or "mixed")
    if audio is None:
        raise FileNotFoundError(f"{name}: no audio for channel {seg.channel}")
    result = subprocess.run(clip_command(audio, seg.start, seg.end),
                            capture_output=True, check=False)  # fmt: skip
    if result.returncode != 0:
        raise FFmpegError(result.stderr.decode(errors="replace").strip())
    return result.stdout


PAGE = """<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>ToTTI — nuove frasi per il test set</title>
<style>
  body { margin: 0 auto; max-width: 760px; padding: 24px 16px 120px; background: #000;
         color: #f5f5f7; font: 15px/1.5 system-ui, -apple-system, sans-serif; }
  h1 { font-size: 20px; } p.hint { color: #a1a1a6; }
  .pair { border: 1px solid rgba(255,255,255,.08); border-radius: 10px; padding: 12px 14px;
          margin: 10px 0; background: #0a0a0b; }
  .pair.filled { border-color: rgba(50,215,75,.45); }
  .en { color: #a1a1a6; margin: 6px 0; }
  .who { color: #6e6e73; font-size: 12px; }
  audio { width: 100%; height: 34px; margin: 2px 0 6px; }
  textarea { width: 100%; box-sizing: border-box; background: #161618; color: #f5f5f7;
             border: 1px solid rgba(255,255,255,.1); border-radius: 8px; padding: 8px 10px;
             font: inherit; resize: vertical; min-height: 44px; }
  .bar { position: fixed; left: 0; right: 0; bottom: 0; background: rgba(10,10,11,.95);
         border-top: 1px solid rgba(255,255,255,.08); padding: 12px 16px; display: flex;
         gap: 14px; align-items: center; justify-content: center; }
  button { background: #0a84ff; color: #fff; border: 0; border-radius: 8px; padding: 10px 22px;
           font: 600 15px system-ui; cursor: pointer; }
  #state { color: #32d74b; min-width: 200px; }
</style></head><body>
<h1>Nuove frasi per il test set EN→IT</h1>
<p class="hint">Frasi non ancora nel test set. Ascolta, traduci quelle che vuoi aggiungere e
lascia vuote le altre. <b>Invia</b> salva su questo Mac; l'unione al test set congelato è un
passo a parte.</p>
<div id="list"></div>
<div class="bar"><button onclick="send()">Invia</button><span id="state"></span></div>
<script>
const DATA = __DATA__;
const list = document.getElementById("list");
for (const [i, p] of DATA.entries()) {
  const d = document.createElement("div");
  d.className = "pair"; d.dataset.call = p.call; d.dataset.segment = p.segment;
  const head = `${String(i + 1).padStart(3, "0")} · ${p.call} · segmento ${p.segment}`;
  d.innerHTML = `<div class="who">${head}</div>
    <audio controls preload="none" src="/clip/${encodeURIComponent(p.call)}/${p.segment}"></audio>
    <div class="en">${p.en}</div>
    <textarea placeholder="Traduzione italiana (vuota = non entra)"></textarea>`;
  d.querySelector("textarea").addEventListener("input", e =>
    d.classList.toggle("filled", e.target.value.trim() !== ""));
  list.append(d);
}
async function send() {
  const pairs = [...document.querySelectorAll(".pair")].flatMap(d => {
    const it = d.querySelector("textarea").value.trim();
    const seg = Number(d.dataset.segment);
    const src = DATA.find(p => p.call === d.dataset.call && p.segment === seg);
    return it ? [{ call: d.dataset.call, segment: seg, en: src.en, it }] : [];
  });
  const r = await fetch("/save", { method: "POST", headers: { "Content-Type": "application/json" },
                                   body: JSON.stringify({ pairs }) });
  document.getElementById("state").textContent =
    r.ok ? `Salvate ${pairs.length} traduzioni.` : "Errore nel salvataggio.";
}
</script></body></html>"""


def create_review_app(folder: Path, frozen: Path, out: Path):
    """The FastAPI app of the review page; audio stays on this machine."""
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, Response

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    candidates = pending(folder, frozen)

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        data = [{"call": p.call, "segment": p.segment, "en": p.en} for p in candidates]
        return PAGE.replace("__DATA__", json.dumps(data, ensure_ascii=False))

    @app.get("/clip/{name}/{segment}")
    def segment_clip(name: str, segment: int) -> Response:
        try:
            return Response(clip(folder, name, segment), media_type="audio/wav")
        except (FileNotFoundError, FFmpegError, IndexError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.post("/save")
    def save(body: dict) -> dict:
        pairs = body.get("pairs", [])
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as f:
            for p in pairs:
                record = {"call": p["call"], "segment": p["segment"], "en": p["en"],
                          "it": p["it"]}  # fmt: skip
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"saved": len(pairs)}

    return app
