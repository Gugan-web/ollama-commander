# Ollama Commander

Ollama Commander is a colorful terminal dashboard for managing and chatting with your local Ollama models.

## Features

- Bright ANSI dashboard with installed model and runtime status panels
- Arrow-key command menu
- Streaming chat mode with conversation memory
- Pull, inspect, stop, delete, and copy model actions
- No external Python dependencies

## Run

```powershell
python .\ollama_cli.py
```

Make sure Ollama is installed and running locally.

## Controls

- `Up` and `Down`: Move through menus
- `Enter`: Select
- `Q`: Go back or quit
- `/clear`: Reset chat history inside chat mode
- `/exit`: Leave chat mode

## Notes

- The app talks to Ollama over the local API at `http://127.0.0.1:11434`
- Set `OLLAMA_HOST` if your Ollama server is running elsewhere
