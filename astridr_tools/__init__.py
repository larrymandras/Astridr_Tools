"""Built-in tools and tool registry."""

from astridr.tools.airtable import AirtableTool
from astridr.tools.base import BaseTool, ToolResult
from astridr.tools.coding_agent import CodingAgentTool
from astridr.tools.dm_admin import DMAdminTool
from astridr.tools.episodic_recall import EpisodicRecallTool
from astridr.tools.document_query import DocumentQueryTool
from astridr.tools.github import GitHubTool
from astridr.tools.github_pr_workflow import PRWorkflowTool
from astridr.tools.google_fonts import GoogleFontsTool
from astridr.tools.memory_bus_tool import MemoryBusTool
from astridr.tools.google_workspace import GoogleWorkspaceTool
from astridr.tools.hackernews import HackerNewsTool
from astridr.tools.newsapi import NewsAPITool
from astridr.tools.obsidian import ObsidianTool
from astridr.tools.pdf_editor import PDFEditorTool
from astridr.tools.pexels import PexelsTool
from astridr.tools.pixabay import PixabayTool
from astridr.tools.registry import ToolRegistry
from astridr.tools.skill_creator import SkillCreatorTool
from astridr.tools.summarizer import SummarizerTool
from astridr.tools.weather import WeatherTool
from astridr.tools.web_search import WebSearchTool

# Wave 2 — API-key tools
from astridr.tools.cloudinary import CloudinaryTool
from astridr.tools.fal_ai import FalAITool
from astridr.tools.heygen import HeyGenTool
from astridr.tools.pandadoc import PandaDocTool
from astridr.tools.reddit import RedditTool
from astridr.tools.suno import SunoTool

# Wave 2 — OAuth tools
from astridr.tools.linkedin import LinkedInTool
from astridr.tools.youtube import YouTubeTool
from astridr.tools.zoho_crm import ZohoCRMTool

# Wave 3 — Freya tool wrappers
from astridr.tools.google_meet import GoogleMeetTool
from astridr.tools.slack_tool import SlackTool

# Wave 3+ — Skuld creative tool wrappers
from astridr.tools.blotato import BlotatoTool
from astridr.tools.canva import CanvaTool
from astridr.tools.excalidraw import ExcalidrawTool
from astridr.tools.lucidchart import LucidchartTool
from astridr.tools.remotion import RemotionTool

# Wave 3+ — Iðunn personal life tool wrappers
from astridr.tools.alexa import AlexaTool
from astridr.tools.garmin import GarminTool
from astridr.tools.home_assistant import HomeAssistantTool
from astridr.tools.priceline import PricelineTool

# Runtime agent profile tool
from astridr.tools.profile_tool import ProfileTool

__all__ = [
    "AirtableTool",
    "AlexaTool",
    "BaseTool",
    "BlotatoTool",
    "CanvaTool",
    "CloudinaryTool",
    "CodingAgentTool",
    "DMAdminTool",
    "DocumentQueryTool",
    "EpisodicRecallTool",
    "ExcalidrawTool",
    "FalAITool",
    "GarminTool",
    "GitHubTool",
    "GoogleFontsTool",
    "GoogleMeetTool",
    "GoogleWorkspaceTool",
    "HackerNewsTool",
    "HeyGenTool",
    "HomeAssistantTool",
    "LinkedInTool",
    "LucidchartTool",
    "MemoryBusTool",
    "NewsAPITool",
    "ObsidianTool",
    "PandaDocTool",
    "PDFEditorTool",
    "PexelsTool",
    "PixabayTool",
    "PRWorkflowTool",
    "PricelineTool",
    "ProfileTool",
    "RedditTool",
    "RemotionTool",
    "SkillCreatorTool",
    "SlackTool",
    "SunoTool",
    "SummarizerTool",
    "ToolRegistry",
    "ToolResult",
    "WeatherTool",
    "WebSearchTool",
    "YouTubeTool",
    "ZohoCRMTool",
]
