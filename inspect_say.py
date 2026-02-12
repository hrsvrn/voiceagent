
from livekit.agents import AgentSession
import inspect

try:
    print(help(AgentSession.say))
except Exception as e:
    print(f"Error inspecting say: {e}")

try:
    print(help(AgentSession.generate_reply))
except Exception as e:
    print(f"Error inspecting generate_reply: {e}")
