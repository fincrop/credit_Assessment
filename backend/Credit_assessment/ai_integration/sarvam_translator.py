"""
SarvamAI Translator
====================
Translates English credit reports into Indian regional languages using
the SarvamAI API (saaras model). Hindi is prioritized by default.

Configuration:
    Set SARVAM_API_KEY environment variable before running.

Usage:
    translator = SarvamTranslator(target_language="hi")
    hindi_report = translator.translate(english_report)

Supported language codes (ISO 639):
    hi - Hindi (default)
    te - Telugu
    mr - Marathi
    ta - Tamil
    kn - Kannada
    gu - Gujarati
    pa - Punjabi
    bn - Bengali
    or - Odia
    ml - Malayalam
"""

import os
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_SARVAM_TRANSLATE_URL = "https://api.sarvam.ai/translate"
_REQUEST_TIMEOUT      = 30

# Map of ISO codes → Sarvam language identifiers
_SARVAM_LANG_MAP = {
    "hi": "hi-IN",
    "te": "te-IN",
    "mr": "mr-IN",
    "ta": "ta-IN",
    "kn": "kn-IN",
    "gu": "gu-IN",
    "pa": "pa-IN",
    "bn": "bn-IN",
    "or": "od-IN",
    "ml": "ml-IN",
}


class SarvamTranslator:
    """
    Translates text to Indian regional languages via SarvamAI.
    Falls back to English with a header note when API key is absent.
    """

    def __init__(self, target_language: str = "hi"):
        self.api_key         = os.environ.get("SARVAM_API_KEY")
        self.target_language = target_language
        self.sarvam_lang     = _SARVAM_LANG_MAP.get(target_language, "hi-IN")

        if self.api_key:
            logger.info(
                f"✔ SarvamTranslator initialized "
                f"(language={target_language} → {self.sarvam_lang})"
            )
        else:
            logger.warning(
                "SarvamTranslator: SARVAM_API_KEY not set — "
                "translation will be skipped."
            )

    def translate(self, text: str) -> str:
        """
        Translate English text to the configured target language.

        Args:
            text: English text to translate (credit report narrative)

        Returns:
            Translated text string, or original text with a note if API unavailable.
        """
        if not self.api_key:
            return (
                f"[Translation unavailable — SARVAM_API_KEY not set]\n\n{text}"
            )

        if not text or not text.strip():
            return text

        # SarvamAI has a ~1000 char limit per call — chunk if needed
        chunks   = self._chunk_text(text, max_chars=900)
        translated_chunks = []
        failed = False

        for chunk in chunks:
            result = self._call_sarvam(chunk)
            if result is None:
                failed = True
                break
            translated_chunks.append(result)

        if failed or not translated_chunks:
            logger.warning(
                "SarvamAI translation failed; returning English original"
            )
            return text

        translated = "\n".join(translated_chunks)
        logger.info(
            f"✔ SarvamAI translated report to {self.target_language} "
            f"({len(translated)} chars)"
        )
        return translated

    # ── Private helpers ────────────────────────────────────────────────────────

    def _call_sarvam(self, text: str) -> Optional[str]:
        """Call SarvamAI translate endpoint for a single chunk."""
        try:
            import urllib.request, urllib.error

            payload = json.dumps({
                "input": text,
                "source_language_code": "en-IN",
                "target_language_code": self.sarvam_lang,
                "speaker_gender": "Male",
                "mode": "formal",
                "model": "mayura:v1",
            }).encode("utf-8")

            req = urllib.request.Request(
                _SARVAM_TRANSLATE_URL,
                data=payload,
                headers={
                    "api-subscription-key": self.api_key,
                    "Content-Type":         "application/json",
                },
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result.get("translated_text") or text

        except Exception as exc:
            logger.error(f"SarvamAI translation failed: {exc}")
            return None  # Caller keeps the English original

    @staticmethod
    def _chunk_text(text: str, max_chars: int = 900) -> list:
        """Split text into paragraphs not exceeding max_chars each."""
        paragraphs = text.split("\n")
        chunks, current = [], ""
        for para in paragraphs:
            if len(current) + len(para) + 1 <= max_chars:
                current = (current + "\n" + para).lstrip("\n")
            else:
                if current:
                    chunks.append(current)
                current = para
        if current:
            chunks.append(current)
        return chunks if chunks else [text[:max_chars]]
