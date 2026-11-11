"""ToTTI — Tool To Translate Italian: live EN→IT subtitles (decision 0038).

While a call is being recorded, the tail of the growing system-channel wav is transcribed
with Whisper and translated into Italian locally. The lines are ephemeral and never touch
the verbatim transcript, which is still produced in full when the call ends.
"""

from parola.totti.document import DocumentTranslation
from parola.totti.live import SubtitleLoop
from parola.totti.translator import Translator

__all__ = ["DocumentTranslation", "SubtitleLoop", "Translator"]
