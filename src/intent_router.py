"""
Intent Router for LiveKit Voice Agent
======================================
Two-tier intent classification to avoid unnecessary LLM calls:

Tier 1 — Regex (0ms, free):
    Catches simple backchannels, greetings, affirmations, negations.
    Returns a known intent string or None.

Tier 2 — LLM (fallback):
    If regex returns None, the utterance goes to the main GPT-4o-mini
    pipeline as usual.

Classified Intents:
    - "backchannel"  → User is just acknowledging ("hmm", "haan", "accha")
    - "affirmation"  → User agrees ("haan kar do", "theek hai karo")
    - "negation"     → User declines ("nahi", "nahi chahiye")
    - "greeting"     → User says hello ("namaste", "hello", "haan bolo")
    - "busy"         → User is busy ("baad mein", "abhi nahi")
    - None           → Complex intent → send to LLM

Usage:
    from intent_router import IntentRouter
    router = IntentRouter()

    intent = router.classify(transcript_text)
    if intent == "backchannel":
        # skip LLM, don't interrupt agent
    elif intent is None:
        # send to LLM as usual
"""

import re
import logging
from typing import Optional

logger = logging.getLogger("voice_agent.intent_router")


class IntentRouter:
    """Fast, regex-based intent classifier for Hindi/Hinglish voice input."""

    # ── Backchannel patterns (should NOT interrupt the agent) ────
    # These are short acknowledgement sounds — the user is just listening.
    _BACKCHANNEL_PATTERNS = [
        # Hindi
        r"^(हाँ|हां|हम्म|अच्छा|ठीक|जी|ओके|ओ|आ|हँ)\.?$",
        # Hinglish / English
        r"^(haan|hmm|ha|ji|accha|theek|okay|ok|oh|aa|hm|mmm)\.?$",
    ]

    # ── Affirmation patterns (user agrees to something) ─────────
    _AFFIRMATION_PATTERNS = [
        r"\b(haan|ha|ji)\s+(kar\s*do|kardo|karo|kar\s*dijiye|kar\s*de|de\s*do|dedo)\b",
        r"\b(हाँ|हां|जी)\s+(कर\s*दो|करो|कर\s*दीजिए|कर\s*दे|दे\s*दो)\b",
        r"\b(theek\s*hai|thik\s*hai|bilkul|zaroor|done|agreed)\b",
        r"\b(ठीक\s*है|बिल्कुल|ज़रूर)\b",
        r"^(yes|haan|ha)\s*$",
    ]

    # ── Negation patterns ───────────────────────────────────────
    _NEGATION_PATTERNS = [
        r"\b(nahi|naa|na|mat|no|nope)\b",
        r"\b(नहीं|ना|मत|नो)\b",
        r"\b(nahi\s*chahiye|nahi\s*karna|interest\s*nahi)\b",
        r"\b(नहीं\s*चाहिए|नहीं\s*करना|इंटरेस्ट\s*नहीं)\b",
    ]

    # ── Greeting patterns ───────────────────────────────────────
    _GREETING_PATTERNS = [
        r"^(hello|hi|hey|namaste|namaskar|haan\s*bolo|haan\s*boliye)\s*\.?$",
        r"^(हैलो|हाय|नमस्ते|नमस्कार|हाँ\s*बोलो|हाँ\s*बोलिए)\s*\.?$",
    ]

    # ── Busy / call-later patterns ──────────────────────────────
    _BUSY_PATTERNS = [
        r"\b(busy|baad\s*mein|kal|abhi\s*nahi|time\s*nahi|bad\s*me)\b",
        r"\b(व्यस्त|बाद\s*में|कल|अभी\s*नहीं|टाइम\s*नहीं)\b",
        r"\b(driving|gaadi\s*chala|meeting)\b",
        r"\b(ड्राइविंग|गाड़ी\s*चला|मीटिंग)\b",
    ]

    # ── Noise / non-speech ──────────────────────────────────────
    _NOISE_PATTERNS = [
        r"^\s*$",                           # Empty
        r"^[\.\,\?\!\s]+$",                 # Only punctuation
        r"^\[.*\]$",                        # Deepgram noise markers [noise], [music]
        r"^(uh|um|ah)\s*\.?$",              # Filler sounds
    ]

    # Pre-compile all patterns for performance
    _COMPILED = {}

    def __init__(self):
        """Compile regex patterns once at init."""
        self._COMPILED = {
            "backchannel": [re.compile(p, re.IGNORECASE) for p in self._BACKCHANNEL_PATTERNS],
            "affirmation": [re.compile(p, re.IGNORECASE) for p in self._AFFIRMATION_PATTERNS],
            "negation": [re.compile(p, re.IGNORECASE) for p in self._NEGATION_PATTERNS],
            "greeting": [re.compile(p, re.IGNORECASE) for p in self._GREETING_PATTERNS],
            "busy": [re.compile(p, re.IGNORECASE) for p in self._BUSY_PATTERNS],
            "noise": [re.compile(p, re.IGNORECASE) for p in self._NOISE_PATTERNS],
        }

    def classify(self, transcript: str) -> Optional[str]:
        """
        Classify a user transcript into a fast intent.

        Returns:
            - "backchannel" — user is just acknowledging, don't interrupt agent
            - "affirmation" — user agrees
            - "negation"    — user declines
            - "greeting"    — user says hello
            - "busy"        — user wants to be called later
            - "noise"       — not real speech, ignore entirely
            - None          — complex intent, needs LLM processing

        Only attempts classification for short utterances (≤5 words).
        Longer utterances are assumed to be complex and always return None.
        """
        text = transcript.strip()

        if not text:
            return "noise"

        word_count = len(text.split())

        # Noise detection — always check regardless of length
        for pattern in self._COMPILED.get("noise", []):
            if pattern.match(text):
                logger.debug(f"Intent: noise — '{text}'")
                return "noise"

        # For short utterances (≤5 words), try all fast intents
        if word_count <= 5:
            # Check in priority order
            for intent in ["backchannel", "greeting", "affirmation", "negation", "busy"]:
                for pattern in self._COMPILED.get(intent, []):
                    if intent == "backchannel":
                        # Backchannel must match the ENTIRE utterance
                        if pattern.match(text):
                            logger.debug(f"Intent: {intent} — '{text}'")
                            return intent
                    else:
                        # Other intents: search anywhere in text
                        if pattern.search(text):
                            logger.debug(f"Intent: {intent} — '{text}'")
                            return intent

        # Complex utterance → needs LLM
        logger.debug(f"Intent: complex (→ LLM) — '{text}'")
        return None

    def is_backchannel(self, transcript: str) -> bool:
        """Convenience method: returns True if the utterance is a backchannel."""
        return self.classify(transcript) == "backchannel"

    def should_skip_llm(self, transcript: str) -> bool:
        """Returns True if this utterance definitely doesn't need LLM processing."""
        intent = self.classify(transcript)
        return intent in ("backchannel", "noise")
