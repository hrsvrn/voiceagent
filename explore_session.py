
import asyncio
from livekit.agents import AgentSession
import inspect

print("Exploring AgentSession...")
try:
    print(dir(AgentSession))
except Exception as e:
    print(f"Error: {e}")
