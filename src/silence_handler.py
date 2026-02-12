"""
Silence Handler for LiveKit Voice Agent
========================================
Implements a multi-tier escalating re-engagement strategy when the user
goes silent after the agent finishes speaking.

Tier 1 (IDLE_REPROMPT_SECONDS):  Re-prompt — "kya aap wahan hain?"
Tier 2 (IDLE_REPROMPT_SECONDS):  Second re-prompt with disconnect warning
Tier 3 (FINAL_DISCONNECT_SECONDS after last re-prompt): Auto-disconnect

On auto-disconnect, calls the provided `disconnect_callback` which handles:
    - Goodbye TTS
    - session.shutdown()
    - Room deletion (SIP BYE signal)

Usage:
    silence = SilenceHandler(
        session=session,
        get_current_phase=lambda: agent.current_phase,
        disconnect_callback=graceful_disconnect,
    )
    # Call silence.on_agent_done_speaking() when agent finishes
    # Call silence.on_user_spoke() whenever user speaks
    # Call silence.shutdown() on call end
"""

import asyncio
import logging
from typing import Callable, Optional, Awaitable

logger = logging.getLogger("voice_agent.silence")


# ── Configuration Constants (easy to tune) ─────────────────────────
IDLE_REPROMPT_SECONDS = 6        # Seconds of silence before each re-prompt
FINAL_DISCONNECT_SECONDS = 20    # Seconds after last re-prompt before auto-disconnect
MAX_REPROMPTS = 2                 # Number of re-prompts before final disconnect


class SilenceHandler:
    """Handles user silence with escalating re-engagement and auto-disconnect."""

    # ── Re-prompt messages by conversation phase ──────────────────
    REPROMPT_MESSAGES = {
        1: {
            # First re-prompt — simple check
            "_default": "हैलो? क्या आप मुझे सुन पा रहे हैं?",
        },
        2: {
            # Second re-prompt — warning about disconnect
            "_default": "हैलो जी? अगर आप सुन रहे हैं तो कृपया कुछ बोलें, नहीं तो कॉल कट जाएगी।",
        },
    }

    # ── Goodbye message for auto-disconnect ───────────────────────
    GOODBYE_MESSAGE = (
        "लगता है आप व्यस्त हैं। मैं अभी फ़ोन रख रही हूँ। धन्यवाद!"
    )

    def __init__(
        self,
        session,
        get_current_phase: Callable[[], str],
        disconnect_callback: Optional[Callable[..., Awaitable]] = None,
    ):
        """
        Args:
            session: The LiveKit AgentSession instance.
            get_current_phase: Callable that returns the current conversation
                               phase ('greeting', 'pitch', 'objections', 'payment').
            disconnect_callback: Async callable to gracefully disconnect the call.
                                 Signature: disconnect_callback(goodbye_msg: str = None)
                                 This should handle session.shutdown() + room deletion.
        """
        self._session = session
        self._get_phase = get_current_phase
        self._disconnect_callback = disconnect_callback
        self._monitor_task: Optional[asyncio.Task] = None
        self._cancelled = False
        self._is_shutdown = False  # Flag to prevent resurrection after disconnect
        self._reprompt_count = 0
        self._is_reprompting = False  # True while the agent is speaking a re-prompt

    # ── Public API ────────────────────────────────────────────────

    def on_agent_done_speaking(self):
        """Call when the agent finishes its turn (state → listening)."""
        if self._is_shutdown:
            logger.debug("Ignoring on_agent_done_speaking because handler is shutdown.")
            return

        if self._is_reprompting:
            # Agent just finished speaking a re-prompt — DON'T reset counters.
            # Just start monitoring for the next tier of silence.
            self._is_reprompting = False
            self._cancel_existing()
            self._cancelled = False
            # Don't reset _reprompt_count — we want escalation to continue
            self._monitor_task = asyncio.create_task(self._run_idle_monitor())
            return

        # Normal case: agent finished a regular response
        self._cancel_existing()
        self._cancelled = False
        self._reprompt_count = 0  # Reset re-prompt counter on new agent turn
        self._monitor_task = asyncio.create_task(self._run_idle_monitor())

    def on_agent_started_speaking(self):
        """Call when agent starts speaking (state → speaking)."""
        if self._is_shutdown:
            return

        if self._is_reprompting:
            # Ignore agent's own re-prompt speech from cancelling the timer
            return
        self._cancel_existing()
        self._reprompt_count = 0 

    def on_user_spoke(self):
        """Call whenever a valid user utterance is detected."""
        if self._is_shutdown:
            return

        # User spoke — always reset everything, even if we were re-prompting
        self._is_reprompting = False
        self._reprompt_count = 0  # Reset re-prompt counter
        self._cancel_existing()

    def cancel_timer(self):
        """Cancel the silence timer without resetting reprompt count.
        
        Use this when the agent starts speaking — we want to pause the
        timer but NOT reset the escalation state (reprompt count).
        """
        self._cancel_existing()

    def shutdown(self):
        """Cleanup on call end."""
        self._is_shutdown = True
        self._cancel_existing()

    # ── Internal ──────────────────────────────────────────────────

    def _cancel_existing(self):
        self._cancelled = True
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
        self._monitor_task = None

    async def _run_idle_monitor(self):
        """Walk through idle tiers: re-prompt → re-prompt → disconnect."""
        logger.info(f"Starting idle monitor (wait={IDLE_REPROMPT_SECONDS}s, count={self._reprompt_count})")
        try:
            # ── Phase 1 & 2: Re-prompts ──────────────────────────
            while self._reprompt_count < MAX_REPROMPTS:
                wait_time = IDLE_REPROMPT_SECONDS
                # logger.debug(f"Sleeping {wait_time}s...")
                await asyncio.sleep(wait_time)

                if self._cancelled:
                    logger.info("Monitor cancelled during sleep")
                    return
                
                self._reprompt_count += 1
                try:
                    phase = self._get_phase()
                except Exception as e:
                    logger.error(f"Failed to get phase: {e}")
                    phase = "greeting"

                # Get the appropriate re-prompt message
                messages = self.REPROMPT_MESSAGES.get(
                    self._reprompt_count,
                    self.REPROMPT_MESSAGES.get(MAX_REPROMPTS, {})
                )
                instruction = messages.get(phase, messages.get("_default", ""))

                if not instruction:
                    logger.warning(f"No instruction for phase {phase}")
                    continue
                
                logger.info(f"Triggering Re-prompt #{self._reprompt_count} (Phase: {phase})")

                # Mark that WE are generating speech — so on_agent_done_speaking
                # and on_user_spoke don't reset our escalation state
                self._is_reprompting = True
                
                # OPTIMIZATION: Use session.say() directly instead of generate_reply().
                # This bypasses the LLM (0 tokens used) and sends text directly to TTS.
                # format: say(text, add_to_chat_ctx=True)
                await self._session.say(instruction, add_to_chat_ctx=True)
            
            # ── Phase 3: Final disconnect after FINAL_DISCONNECT_SECONDS ─
            logger.info(f"Max re-prompts sent. Waiting {FINAL_DISCONNECT_SECONDS}s before disconnect.")
            await asyncio.sleep(FINAL_DISCONNECT_SECONDS)

            if self._cancelled:
                logger.info("Cancelled before disconnect")
                return

            logger.info("Auto-disconnecting now due to silence.")

            # Use disconnect callback if available (handles SIP teardown)
            if self._disconnect_callback:
                # CRITICAL FIX: Run disconnect in a separate task so it survives
                # the cancellation of this monitor task!
                # (Previously, calling shutdown() inside this callback would kill THIS task
                #  before it could finish executing the disconnect logic!)
                asyncio.create_task(self._disconnect_callback(goodbye_msg=self.GOODBYE_MESSAGE))
            else:
                # Fallback: just say goodbye (no SIP teardown)
                await self._session.say(self.GOODBYE_MESSAGE, add_to_chat_ctx=True)
                logger.warning("No disconnect_callback provided — SIP trunk may remain connected.")

        except asyncio.CancelledError:
            logger.debug("Silence monitor task cancelled.")
        except Exception as e:
            logger.error(f"CRITICAL: Silence monitor crashed: {e}", exc_info=True)
