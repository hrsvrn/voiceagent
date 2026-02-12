from dotenv import load_dotenv
import os
import asyncio
import time
from pathlib import Path

import logging
from livekit import agents
from livekit.agents import AgentSession, Agent, RoomInputOptions, MetricsCollectedEvent, RunContext
from livekit.agents.llm import function_tool
from livekit.agents.metrics import LLMMetrics, EOUMetrics, TTSMetrics
from livekit.plugins import silero, deepgram, cartesia, openai
from pricing import PricingManager, validate_payload, PLAN_KEYS
from silence_handler import SilenceHandler
from intent_router import IntentRouter
import prompts

# LiveKit Server API for room management (delete room → SIP BYE)
import livekit.api as lkapi
from livekit.api import DeleteRoomRequest

logger = logging.getLogger("voice_agent")
logger.setLevel(logging.INFO)

# Load environment variables
load_dotenv()
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv(".env")


# ─── Sample Payload (replace with actual API source) ──────────────────────────

SAMPLE_PAYLOAD = {
    "phone_number": 7891389837,
    "operator_code": "WE2541039",
    "operator_name": "Umashankar sharma",
    "segment": "SFO",
    "operator_age_in_system": 1540,
    "op_decile": 6,
    "app_type": "Android",
    "app_version": "20.1.1",
    "payment_trend": "late_after_10days",
    "open_tickets_60days": None,
    "total_due_veh": 1,
    "vehicle_id": 1659532,
    "vehicle_number": "RJ29GB7437",
    "renewal_date": "2026-01-26",
    "veh_decile": 6,
    "autopay_active_flag": 0,
    "distance_covered_before_renewal": 0.01,
    "veh_catg": None,
    "no_info_date": "2026-01-13 11:48:34",
    "active_fastag": None,
    "prev_plan_validity": 12,
    "base_type": "Yearly",
    "prev_plan_start_date": "2025-01-25",
    "app_usage_before_renewal": 100,
    "bucket_price": 1600,
    "gps_1yr": 1800,
    "gps_1yr_tracking_only": 1450,
    "gps_2yr": 2950,
    "gps_4yr": 5400,
    "plan_6m": 1150,
    "plan_3m": 700,
    "plan_3m_24d_limited": 500,
    "plan_1m": None,
    "plan_1yr_gps_unlimited_login_dcm_ds": 2200,
    "recommendation": "plan_1yr_gps_unlimited_login_dcm_ds",
}


class WheelsEyeSupportAgent(Agent):
    def __init__(self, payload: dict = None) -> None:
        # Use provided payload or sample
        raw_payload = payload or SAMPLE_PAYLOAD

        # Validate and extract fields
        self.validated = validate_payload(raw_payload)

        # Initialize pricing from payload (dynamic prices)
        self.pricing = PricingManager(raw_payload)

        # Resolve recommended plan
        rec_key = self.validated["recommendation"]
        rec_details = self.pricing.get_plan_details(rec_key)

        # Build plan descriptions using pricing module
        plan_descriptions = {}
        for key in PLAN_KEYS:
            plan_descriptions[key] = self.pricing.build_plan_description(key)

        # Build prompt context
        self.prompt_context = {
            "customer_name": self.validated["first_name"],
            "vehicle_last4_hindi": self.validated["vehicle_last4_hindi"],
            "app_usage": self.validated["app_usage"],
            "recommended_plan": rec_details["plan_name"] if rec_details and rec_details["available"] else "रिकमेंडेड प्लान उपलब्ध नहीं",
            "plan_price": self.pricing.format_price_hindi(rec_details["base_price"]) if rec_details and rec_details["available"] else "मूल्य उपलब्ध नहीं",
            "plan_price_final": self.pricing.format_price_hindi(rec_details["final_price"]) if rec_details and rec_details["available"] else "मूल्य उपलब्ध नहीं",
            "plan_descriptions": plan_descriptions,
        }

        # Store rec_key for tool use
        self._rec_plan_key = rec_key

        # ── Phase tracking ────────────────────────────────────────
        self._current_phase = "greeting"

        # Start with GREETING phase
        self._instructions = prompts.get_phase_prompt("greeting", self.prompt_context)

        super().__init__(
            instructions=self._instructions,
            min_endpointing_delay=0.25,  # ↑ from 0.15 — Hindi speakers pause mid-sentence
        )

    @property
    def instructions(self):
        return self._instructions

    @instructions.setter
    def instructions(self, value):
        self._instructions = value

    @property
    def current_phase(self) -> str:
        """Returns the current conversation phase."""
        return self._current_phase

    @function_tool
    async def transition_to_phase(self, context: RunContext, phase: str):
        """
        Transitions the conversation to the specified phase by updating the agent's instructions.
        Valid phases: 'greeting', 'pitch', 'objections', 'downsell', 'payment', 'confirm'.
        Sales funnel: greeting → pitch → objections/downsell → payment → confirm.
        """
        logger.info(f"Transitioning to phase: {phase}")

        valid_phases = ["greeting", "pitch", "objections", "downsell", "payment", "confirm"]
        if phase not in valid_phases:
            logger.warning(f"Invalid phase requested: {phase}")
            return f"Invalid phase. Valid phases: {', '.join(valid_phases)}"

        self._current_phase = phase
        new_instructions = prompts.get_phase_prompt(phase, self.prompt_context)
        self.instructions = new_instructions

        return f"Transitioned to {phase} phase."

    @function_tool
    async def recommend_plan(self, context: RunContext):
        """
        Recommends the specific plan to the user with full details including price, GST, and features.
        Use this when the user agrees to recharge or wants to know about the recommended plan.
        """
        logger.info("Recommending plan to user")

        rec_details = self.pricing.get_plan_details(self._rec_plan_key)

        if rec_details and rec_details["available"]:
            plan_desc = self.prompt_context["plan_descriptions"].get(self._rec_plan_key, "")
            return f"मैं आपको यह प्लान रिकमेंड करती हूँ: {plan_desc}"
        else:
            return "क्षमा करें, रिकमेंडेड प्लान अभी उपलब्ध नहीं है."

    @function_tool
    async def list_all_plans(self, context: RunContext):
        """
        Lists all available GPS plans with their details.
        Use this when the user asks about available plans or wants to know all options.
        Only lists plans that are currently available (base_price is not None).
        """
        logger.info("Listing all available plans")

        plan_descriptions = self.prompt_context["plan_descriptions"]

        all_plans_text = "जी हाँ, हमारे पास ये प्लान्स उपलब्ध हैं:\n\n"

        plan_order = [
            "plan_1m", "plan_3m", "plan_3m_24d_limited", "plan_6m",
            "gps_1yr", "gps_1yr_tracking_only",
            "plan_1yr_gps_unlimited_login_dcm_ds",
            "gps_2yr", "gps_4yr",
        ]

        available_count = 0
        for plan_key in plan_order:
            plan_details = self.pricing.get_plan_details(plan_key)
            if plan_details and plan_details.get("available", False):
                if plan_key in plan_descriptions:
                    all_plans_text += f"{plan_descriptions[plan_key]}\n\n"
                    available_count += 1

        if available_count == 0:
            all_plans_text = "अभी कोई plan available नहीं है."
        else:
            all_plans_text += "\nआपके लिए मैं " + self.prompt_context.get('recommended_plan', '') + " सबसे अच्छा मानती हूँ. लगा दूँ?"

        return all_plans_text


# ─── Graceful Disconnect Configuration ────────────────────────────────────────
GOODBYE_WAIT_SECONDS = 4  # Wait for TTS to finish saying goodbye before teardown


async def entrypoint(ctx: agents.JobContext):
    # ── VAD: Tuned for noisy truck environments ──────────────────
    vad = silero.VAD.load(
        min_speech_duration=0.3,     # ↑ from 0.25 — requires 300ms of speech to trigger
        min_silence_duration=0.4,    # 400ms silence = end of utterance
    )

    session = AgentSession(
        stt=deepgram.STT(
            model="nova-3",
            language="hi",
            smart_format=True,       # Better formatting of numbers/entities
        ),
        llm=openai.LLM(
            model="gpt-4o-mini",
            temperature=0.2,
        ),
        tts=cartesia.TTS(
            model="sonic-3",
            voice="95d51f79-c397-46f9-b49a-23763d3eaa2d",
            language="hi",
        ),
        vad=vad,
        # ── Retuned for Hindi conversational speech ───────────────
        min_interruption_duration=0.6,    # ↑ from 0.5 — prevents "hmm" from interrupting
        min_endpointing_delay=0.25,       # ↑ from 0.15 — Hindi speakers pause mid-sentence
        min_interruption_words=3,         # ↑ from 2 — requires 3 words to trigger interruption
        preemptive_generation=True,       # Start LLM while TTS still playing
    )

    # ── Initialize agent with payload ─────────────────────────────
    agent = WheelsEyeSupportAgent(payload=SAMPLE_PAYLOAD)
    call_state = {"user_spoken": False}

    # ── Intent Router (regex-based, zero-cost classification) ─────
    intent_router = IntentRouter()

    # ── Session-ended flag (prevents double-disconnect) ───────────
    session_ended = False

    # ── Graceful Disconnect Function ──────────────────────────────
    #
    # This is the SINGLE function for ALL disconnect scenarios:
    #   - User idle timeout (auto-disconnect)
    #   - User hangs up (detected via room events)
    #   - Conversation ends naturally
    #
    # Steps:
    #   1. Say goodbye (if message provided and session still active)
    #   2. Shutdown the agent session (drains pending speech)
    #   3. Delete the LiveKit room → sends SIP BYE to trunk provider
    #
    # WHY delete_room is critical:
    #   - session.shutdown() only disconnects the AGENT
    #   - The SIP caller stays connected (hearing silence) unless the room is deleted
    #   - Deleting the room triggers LiveKit to send SIP BYE to the trunk
    #

    async def graceful_disconnect(goodbye_msg: str = None):
        """
        Gracefully disconnect the call with proper SIP trunk teardown.

        Args:
            goodbye_msg: Optional farewell message to speak before disconnecting.
                        Pass None to skip the goodbye (e.g., when user already hung up).
        """
        nonlocal session_ended

        if session_ended:
            logger.info("Session already ended. Ignoring duplicate disconnect request.")
            return

        session_ended = True

        # Stop silence monitoring immediately
        silence_handler.shutdown()

        try:
            # Step 1: Say goodbye (optional — skip if user already hung up)
            if goodbye_msg:
                try:
                    # OPTIMIZATION: Use session.say() to bypass LLM token usage for goodbye
                    await session.say(goodbye_msg, add_to_chat_ctx=True)
                    # Wait for TTS to finish playing the goodbye
                    await asyncio.sleep(GOODBYE_WAIT_SECONDS)
                except Exception as e:
                    logger.warning(f"Failed to say goodbye: {e}")

            # Step 2: Shutdown agent session (graceful drain of pending speech)
            try:
                # Note: If logging shows 'NoneType can't be used in await', session.shutdown() 
                # might be synchronous or already completed. We'll try to await it, 
                # but handle TypeError just in case.
                val = session.shutdown()
                if val is not None and hasattr(val, '__await__'):
                    await val
                logger.info("Agent session shut down successfully.")
            except Exception as e:
                logger.warning(f"Error shutting down session: {e}")

            # Step 3: Delete the room → sends SIP BYE signal to trunk provider
            #
            # This is the most important step. Without this, the phone call
            # stays connected even after the agent disconnects.
            try:
                lk_api = lkapi.LiveKitAPI()  # Uses LIVEKIT_URL, API_KEY, API_SECRET from env
                try:
                    await lk_api.room.delete_room(
                        DeleteRoomRequest(room=ctx.room.name)
                    )
                    logger.info(f"Room '{ctx.room.name}' deleted. SIP trunk disconnected via BYE.")
                except Exception as e:
                    # If 404/Not Found, the room is already gone. That's a success.
                    if "not found" in str(e).lower() or "404" in str(e):
                        logger.info(f"Room '{ctx.room.name}' already deleted.")
                    else:
                        raise e
                finally:
                    await lk_api.aclose()
                    
            except Exception as e:
                logger.error(f"Failed to delete room for SIP teardown: {e}")

        except Exception as e:
            logger.error(f"Error during graceful disconnect: {e}")

    # ── Silence Handler (re-prompt + auto-disconnect) ─────────────
    silence_handler = SilenceHandler(
        session=session,
        get_current_phase=lambda: agent.current_phase,
        disconnect_callback=graceful_disconnect,
    )

    # ── Event Handlers ────────────────────────────────────────────

    @session.on("agent_state_changed")
    def on_state_changed(state):
        logger.info(f"Agent State: {state}")
        # Silence detection: start monitoring when agent finishes speaking
        # Note: state is an AgentStateChanged object, not a string
        if state.new_state == "listening":
            silence_handler.on_agent_done_speaking()
        elif state.new_state == "speaking":
            silence_handler.on_agent_started_speaking()

    @session.on("error")
    def on_session_error(err):
        logger.error(f"Session Error: {err}")

    latency_tracker = {}

    @session.on("user_transcription")
    def on_user_transcription(transcription):
        logger.info(f"on_user_transcription: {transcription.text}")

    @session.on("transcription_received")
    def on_transcription(transcription):
        logger.info(f"on_transcription: {transcription.text}")

    @session.on("user_input_transcribed")
    def on_user_input(event):
        transcript = event.transcript
        logger.info(f"on_user_input: {transcript}")
        # Mark that user has spoken (cancels initial timeout)
        call_state["user_spoken"] = True

        # Intent routing: skip LLM for noise/backchannels
        intent = intent_router.classify(transcript)
        if intent == "noise":
            logger.info(f"Filtered noise transcript: '{transcript}'")
            return

        # Valid speech (or backchannel) detected — reset silence timer
        silence_handler.on_user_spoke()

        if intent == "backchannel":
            logger.info(f"Backchannel detected: '{transcript}' — not interrupting agent")
            return
        if intent:
            logger.info(f"Fast intent detected: {intent} — '{transcript}'")

    @session.on("metrics_collected")
    def on_metrics_collected(event: MetricsCollectedEvent):
        metrics = event.metrics
        if isinstance(metrics, LLMMetrics):
            logger.info(
                f"LLM Metrics - Duration: {metrics.duration:.2f}s, "
                f"TTFT: {metrics.ttft:.2f}s, Tokens: {metrics.total_tokens} "
                f"(In: {metrics.prompt_tokens}, Out: {metrics.completion_tokens})"
            )
            if metrics.speech_id:
                latency_tracker.setdefault(metrics.speech_id, {})["llm"] = metrics

        elif isinstance(metrics, EOUMetrics):
            if metrics.speech_id:
                latency_tracker.setdefault(metrics.speech_id, {})["eou"] = metrics

        elif isinstance(metrics, TTSMetrics):
            if metrics.speech_id:
                state = latency_tracker.setdefault(metrics.speech_id, {})
                state["tts"] = metrics

                eou = state.get("eou")
                llm = state.get("llm")
                if eou and llm:
                    user_stop_time = eou.timestamp - eou.end_of_utterance_delay
                    agent_start_time = metrics.timestamp
                    total_latency = agent_start_time - user_stop_time
                    logger.info(
                        f"E2E Latency (ID: {metrics.speech_id}): {total_latency:.2f}s "
                        f"(STT: {eou.transcription_delay:.2f}s, LLM: {llm.ttft:.2f}s, TTS: {metrics.ttfb:.2f}s)"
                    )
                    del latency_tracker[metrics.speech_id]

    # ── Room-Level Event Handlers (SIP disconnect detection) ──────
    #
    # These handlers detect when the USER hangs up the phone call.
    # Without these, the agent session stays alive indefinitely
    # after the user disconnects.

    @ctx.room.on("participant_disconnected")
    def on_participant_left(participant):
        """Detect when any participant (including SIP caller) leaves the room."""
        logger.info(
            f"Participant disconnected: identity={participant.identity}, "
            f"kind={participant.kind}"
        )

        # Only trigger cleanup for non-agent participants
        # (the agent itself disconnecting shouldn't trigger this)
        if participant.kind != participant.kind.AGENT:
            logger.info("Non-agent participant left. Initiating agent-side cleanup.")
            asyncio.create_task(
                graceful_disconnect(goodbye_msg=None)
            )

    @ctx.room.on("reconnecting")
    def on_reconnecting():
        logger.warning("Room reconnecting — preserving agent state")
        silence_handler.on_user_spoke()  # Pause silence timer during reconnect

    @ctx.room.on("reconnected")
    def on_reconnected():
        logger.info("Room reconnected — resuming conversation")
        asyncio.create_task(session.generate_reply(
            instructions="कनेक्शन वापस आ गया—हम कहाँ रुके थे? चलिए आगे बढ़ते हैं."
        ))

    @ctx.room.on("disconnected")
    def on_disconnected():
        logger.error("Room disconnected — call ended")
        silence_handler.shutdown()

    # ── Start Session ─────────────────────────────────────────────
    await session.start(
        room=ctx.room,
        agent=agent,
    )

    # ── Initial Interaction Logic ─────────────────────────────────
    # Wait for the user to say "Hello" first (standard phone etiquette).
    # If they are silent for 4s, we initiate the conversation.
    
    first_name = agent.validated["first_name"]
    vehicle_hindi = agent.validated["vehicle_last4_hindi"]
    opening_text = f"<warm> नमस्ते {first_name} जी! मैं वंशिका, Wheelseye GPS टीम से बोल रही हूँ. आपकी गाड़ी {vehicle_hindi} के GPS ट्रैकिंग को लेकर एक ज़रूरी जानकारी देनी थी."
    
    INITIAL_SILENCE_SECONDS = 4.0

    async def initial_silence_check():
        await asyncio.sleep(INITIAL_SILENCE_SECONDS)
        # If user hasn't spoken yet, we break the silence
        if not call_state["user_spoken"]:
            logger.info(f"User silent for {INITIAL_SILENCE_SECONDS}s. Initiating call.")
            # OPTIMIZATION: Use session.say() instead of generate_reply()
            # This saves tokens and ensures the exact text/tags are spoken.
            await session.say(opening_text, add_to_chat_ctx=True)
            # Mark as spoken to align state
            call_state["user_spoken"] = True

    # Start the check in background without blocking handling of user input
    asyncio.create_task(initial_silence_check())

if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))