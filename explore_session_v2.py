
import sys
try:
    print("START")
    import livekit.agents
    print("Imported livekit.agents")
    # Try different ways to access AgentSession
    try:
        from livekit.agents import AgentSession
        print("Found AgentSession")
        print(dir(AgentSession))
    except ImportError:
        print("Could not import AgentSession directly")
        print(dir(livekit.agents))
    print("END")
except Exception as e:
    print(f"Error: {e}")
