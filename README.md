# astridr-tools

Extracted tool modules from the **Astridr** AI agent framework. Each tool implements a unified `BaseTool` interface with async execution, JSON Schema parameters, and tiered approval gates.

Python >= 3.11 required.

## Tool Catalog (53 modules)

### Workspace

| Module | Description |
|--------|-------------|
| `google_workspace.py` | Access Gmail, Google Calendar, and Google Drive |
| `google_meet.py` | Create and manage Google Meet meetings with auto-generated links |
| `slack_tool.py` | Post messages, read channels, search, and manage Slack workspace |
| `obsidian.py` | Search, read, create, and manage notes in your Obsidian vault |
| `airtable.py` | List bases, browse and manage records in Airtable |
| `zoho_crm.py` | Manage Zoho CRM records, modules, and search |
| `pandadoc.py` | List, create, and send PandaDoc documents |
| `lucidchart.py` | Create and manage Lucidchart diagrams -- documents, exports, templates |
| `excalidraw.py` | Build Excalidraw diagram scenes -- create scenes, add elements, export files |
| `canva.py` | Create and manage Canva designs -- templates, exports, asset uploads |
| `google_fonts.py` | Browse and search Google Fonts: list fonts, get font details |

### Media

| Module | Description |
|--------|-------------|
| `youtube.py` | Search, analyze, and manage YouTube videos and channels |
| `cloudinary.py` | Upload, search, transform, and manage media assets in Cloudinary |
| `fal_ai.py` | Generate AI images and videos via fal.ai queue-based API |
| `heygen.py` | Create AI avatar videos using HeyGen -- list avatars, voices, create videos |
| `suno.py` | Generate AI music with Suno -- create songs, extend tracks, get status |
| `remotion.py` | Render programmatic videos using Remotion -- list compositions, trigger renders |
| `pexels.py` | Search free stock photos and videos on Pexels |
| `pixabay.py` | Search free stock images and videos on Pixabay |
| `screenshot.py` | Capture screen/window screenshots and analyze images with AI vision |
| `pdf_editor.py` | Edit PDF files: merge, split, extract text, add watermarks, rotate pages |

### Data

| Module | Description |
|--------|-------------|
| `web_search.py` | Search the web and retrieve page content |
| `summarizer.py` | Summarize URLs, articles, or long text content |
| `document_query.py` | Ask questions about document contents with source citations |
| `newsapi.py` | Search news articles, browse top headlines, and list sources via NewsAPI |
| `hackernews.py` | Search and browse Hacker News: top/new stories, search, items, users |
| `weather.py` | Get current weather conditions and forecasts for any location |
| `priceline.py` | Search Priceline for hotels, flights, and travel deals |
| `browser.py` | Navigate web pages, interact with elements, take screenshots |

### Memory

| Module | Description |
|--------|-------------|
| `memory_save.py` | Save memory entries -- facts, preferences, decisions, solutions, logs |
| `memory_search.py` | Search memories or retrieve recent entries by query |
| `episodic_recall.py` | Recall recent episodic events or clean up expired entries (90-day rolling) |
| `memory_bus_tool.py` | Cross-agent shared knowledge bus -- publish and query facts, decisions, insights |
| `profile_tool.py` | Manage runtime agent profiles -- list, switch, inspect active profile |

### Infrastructure

| Module | Description |
|--------|-------------|
| `base.py` | Abstract BaseTool interface and ToolResult dataclass |
| `registry.py` | Tool registry -- discovery, validation, and lifecycle management |
| `oauth.py` | OAuth 2.0 token manager with automatic refresh and disk persistence |
| `oauth_setup.py` | OAuth setup CLI -- one-time token provisioning for OAuth-based tools |
| `coding_agent.py` | Execute coding tasks: write code, refactor, debug, test |
| `skill_creator.py` | Create new tool skills from a description and Python code |
| `run_pipeline.py` | Run named pipelines (multi-step workflows) with context variables |
| `delegate_task.py` | Delegate a task to a specialist sub-agent |
| `github.py` | Interact with GitHub: repos, issues, PRs, code search |
| `github_pr_workflow.py` | Three-step PR pipeline: review code, prepare changes, merge |

### Social

| Module | Description |
|--------|-------------|
| `linkedin.py` | Manage LinkedIn profiles, connections, posts, and analytics |
| `reddit.py` | Search and browse Reddit posts, subreddits, and comments |
| `blotato.py` | Schedule social media posts across platforms -- create, list, cancel |
| `peer_dm.py` | Send direct messages between agents via the coordinator |
| `dm_admin.py` | Manage DM pairing approvals (list pending, approve, revoke) |

### IoT

| Module | Description |
|--------|-------------|
| `alexa.py` | Manage Alexa smart home -- devices, commands, reminders, routines |
| `home_assistant.py` | Control Home Assistant smart home -- entities, services, automations |
| `garmin.py` | Access Garmin health data -- daily summaries, activities, sleep, heart rate |

## License

Private -- part of the Astridr framework.
