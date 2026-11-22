"""Bot feature modules (discord.py cogs). Each one checks its per-server toggle on every event."""

from .automations import Automations
from .automod import AutoMod
from .claude_chat import ClaudeChat
from .autorole import AutoRole
from .custom_commands import CustomCommands
from .economy import Economy
from .levels import Levels
from .logs import ServerLogs
from .moderation import Moderation
from .reaction_roles import ReactionRoles
from .restricted import RestrictedChannels
from .warn_system import Warnings
from .welcome import Welcome

COGS = [Welcome, AutoRole, ReactionRoles, CustomCommands, ServerLogs, Warnings, AutoMod, RestrictedChannels, Moderation, Levels, Economy, Automations, ClaudeChat]
