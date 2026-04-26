# Ollama Commander

Ollama Commander is a terminal-first interface for working with local [Ollama](https://ollama.com/) models. It combines model management, interactive chat, and a lightweight file-backed knowledge base in a single keyboard-driven workflow.

![Ollama Commander dashboard](assets/screenshots/01-dashboard-pull.png)

## Overview

Running Ollama day to day often means switching between commands to inspect models, pull new tags, stop active sessions, and start chats. Ollama Commander brings those tasks into one focused CLI so you can manage local models and talk to them without leaving the terminal.

## Highlights

- Interactive dashboard for installed and running models
- Model operations for pull, inspect, stop, delete, duplicate, and refresh
- Chat interface with persistent conversation history during the session
- File-backed knowledge base for attaching local documents to chat
- Smart excerpt selection for large files to avoid overloading prompts
- Built-in `.docx` and `.pptx` text extraction
- No third-party Python dependencies

## Requirements

- Python 3.10 or newer
- Ollama installed and available on your system path
- A running Ollama server
- A terminal with ANSI color support

## Quick Start

Clone the repository and launch the CLI:

```powershell
git clone https://github.com/Gugan-web/ollama-commander.git
cd ollama-commander
python .\ollama_cli.py
```

On Windows, you can also start it with:

```powershell
.\run-commander.bat
```

## Configuration

By default, Ollama Commander connects to:

```text
http://127.0.0.1:11434
```

To target a different Ollama host, set `OLLAMA_HOST` before launching:

```powershell
$env:OLLAMA_HOST = "http://192.168.1.10:11434"
python .\ollama_cli.py
```

## Main Workflows

### Dashboard

The dashboard shows installed models, active models in memory, and the available actions in one view. Navigation is keyboard-first and designed for quick operational tasks.

Available actions:

- `Pull model`
- `Chat with model`
- `Inspect a model`
- `Stop a running model`
- `Delete a model`
- `Duplicate a model`
- `Refresh dashboard`
- `Add files`
- `Quit`

### Chat

Chat mode provides a clean terminal conversation view for local models. It supports multi-turn sessions and optional knowledge-base context from attached local files.

Chat commands:

- `Up` / `Down` to move through menus
- `Enter` to confirm a selection
- `Q` to go back or quit
- `/clear` to reset chat history
- `/files` to open the knowledge-base manager
- `/exit` to leave chat mode

### Knowledge Base

The knowledge base lets you attach local files and use them as supporting context in chat.

- Attached files are stored in `.ollama_commander_kb.json`
- Files remain available across restarts
- Large files are chunked and narrowed to relevant excerpts
- Text and code files are read directly
- `.docx` and `.pptx` content is extracted automatically

This keeps prompts smaller and makes document-assisted local chat much more practical.

## Screenshots

### Dashboard And Model Management

Dashboard with model actions:

![Dashboard with pull selected](assets/screenshots/01-dashboard-pull.png)

Pulling a model:

![Pull prompt](assets/screenshots/02-pull-prompt.png)

Inspecting an installed model:

![Dashboard with inspect selected](assets/screenshots/03-dashboard-inspect.png)

![Inspect select](assets/screenshots/04-inspect-select.png)

![Inspect details](assets/screenshots/05-inspect-details.png)

Stopping a running model:

![Dashboard with stop selected](assets/screenshots/06-dashboard-stop.png)

![Stop empty state](assets/screenshots/07-stop-empty.png)

Deleting and duplicating models:

![Delete select](assets/screenshots/08-delete-select.png)

![Dashboard with duplicate selected](assets/screenshots/09-dashboard-duplicate.png)

![Duplicate select](assets/screenshots/10-duplicate-select.png)

Refreshing dashboard state:

![Dashboard with refresh selected](assets/screenshots/11-dashboard-refresh.png)

### Knowledge Base Workflow

Knowledge-base entry point from the dashboard:

![Dashboard with add files selected](assets/screenshots/12-dashboard-add-files.png)

Knowledge-base manager:

![Knowledge base home](assets/screenshots/13-knowledge-base-home.png)

Adding files by drag and drop or pasted path:

![Knowledge base drag and drop](assets/screenshots/14-knowledge-base-drag-drop.png)

![Knowledge base path entry](assets/screenshots/15-knowledge-base-path-entry.png)

Attached file confirmation:

![Knowledge base added](assets/screenshots/16-knowledge-base-added.png)

Removing files and clearing the list:

![Knowledge base remove prompt](assets/screenshots/17-knowledge-base-remove.png)

![Knowledge base removed](assets/screenshots/18-knowledge-base-removed.png)

![Knowledge base remove confirmation](assets/screenshots/19-knowledge-base-removed-confirm.png)

![Knowledge base cleared](assets/screenshots/20-knowledge-base-cleared.png)

## Project Structure

```text
.
|-- ollama_cli.py
|-- run-commander.bat
|-- README.md
`-- assets/
    `-- screenshots/
```

## Notes

- Ollama Commander is designed for local, terminal-based Ollama workflows
- Knowledge-base support is strongest for text-heavy files and Office document extraction
- Very large or binary-heavy files are still limited by how much useful text can be extracted

## Roadmap

- PDF extraction support
- Richer transcript rendering
- Improved retrieval across multiple large files
- Exportable chat sessions
