from dotenv import load_dotenv
import os
from pathlib import Path

import logging
from livekit import agents
from livekit.agents import AgentSession, Agent, RoomInputOptions, MetricsCollectedEvent, RunContext
from livekit.agents.llm import function_tool
from livekit.agents.metrics import LLMMetrics, EOUMetrics, TTSMetrics
from livekit.plugins import silero, deepgram, cartesia, openai
from pricing import PricingManager, validate_payload, PLAN_KEYS
import prompts

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

        # Start with GREETING phase
        self._instructions = prompts.get_phase_prompt("greeting", self.prompt_context)

        super().__init__(
            instructions=self._instructions,
            min_endpointing_delay=0.150,
        )

    @property
    def instructions(self):
        return self._instructions

    @instructions.setter
    def instructions(self, value):
        self._instructions = value

    @function_tool
    async def transition_to_phase(self, context: RunContext, phase: str):
        """
        Transitions the conversation to the specified phase by updating the agent's instructions.
        Valid phases: 'pitch', 'objections', 'payment', 'greeting'.
        """
        logger.info(f"Transitioning to phase: {phase}")

        if phase not in ["greeting", "pitch", "objections", "payment"]:
            logger.warning(f"Invalid phase requested: {phase}")
            return "Invalid phase."

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
            all_plans_text = "क्षमा करें, अभी कोई प्लान उपलब्ध नहीं है."
        else:
            all_plans_text += "आप कौन सा प्लान लेना चाहेंगे?"

        return all_plans_text


async def entrypoint(ctx: agents.JobContext):
    vad = silero.VAD.load(min_speech_duration=0.25)
    session = AgentSession(
        stt=deepgram.STT(
            model="nova-3",
            language="hi",
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
        min_interruption_duration=0.5,
        min_endpointing_delay=0.150,
        min_interruption_words=2,
        preemptive_generation=True,
    )

    @session.on("agent_state_changed")
    def on_state_changed(state):
        logger.info(f"Agent State: {state}")

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
        logger.info(f"on_user_input: {event.transcript}")

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

    # Initialize agent with payload
    agent = WheelsEyeSupportAgent(payload=SAMPLE_PAYLOAD)

    await session.start(
        room=ctx.room,
        agent=agent,
    )

    # First message: short greeting that waits for user's hello
    first_name = agent.validated["first_name"]
    await session.generate_reply(
        instructions=f"नमस्ते {first_name} जी, मैं वंशिका वीलसाई जीपीएस से बोल रही हूँ. क्या अभी बात हो सकती है?"
    )

if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))