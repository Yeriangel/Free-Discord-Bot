"""Bot feature modules (discord.py cogs). Each one checks its per-server toggle on every event."""

from .afk import AFK
from .automations import Automations
from .automod import AutoMod
from .autorole import AutoRole
from .birthdays import Birthdays
from .channels_auto import StatsChannels, TempChannels
from .claude_chat import ClaudeChat
from .custom_commands import CustomCommands
from .economy import Economy
from .giveaways import Giveaways
from .invite_tracker import InviteTracker
from .levels import Levels
from .logs import ServerLogs
from .moderation import Moderation
from .polls import Polls
from .reaction_roles import ReactionRoles
from .reminders import Reminders
from .restricted import RestrictedChannels
from .social_alerts import SocialAlerts
from .starboard import Activity, Starboard
from .suggestions import Suggestions
from .ticketing import Ticketing
from .warn_system import Warnings
from .welcome import Welcome

COGS = [Activity, Welcome, AutoRole, ReactionRoles, CustomCommands, ServerLogs, Warnings, AutoMod, RestrictedChannels,
        Moderation, Levels, Economy, Automations, Starboard, InviteTracker, Ticketing, Suggestions, Polls, Reminders,
        StatsChannels, TempChannels, AFK, SocialAlerts, Giveaways, Birthdays, ClaudeChat]
