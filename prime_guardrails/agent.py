import logging
from google.adk import Agent

from .config import Config
from .prompt import ROUTER_INSTRUCTIONS
from .callbacks import fast_guardrail_callback

# Configure logging
logger = logging.getLogger(__name__)

# Initialize Config
configs = Config()

# Define the root agent
root_agent = Agent(
    model=configs.agent_settings.model,
    instruction=ROUTER_INSTRUCTIONS,
    name=configs.agent_settings.name,
    tools=[], # Layer 1 tools can be added here
    before_model_callback=fast_guardrail_callback,
)
