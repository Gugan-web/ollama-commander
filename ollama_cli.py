#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

if os.name == "nt":
    import msvcrt
else:
    import select
    import termios
    import tty


API_BASE = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
KB_STATE_FILE = Path(".ollama_commander_kb.json")


class Style:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    FG = {
        "white": "\033[97m",
        "silver": "\033[37m",
        "soft": "\033[38;5;250m",
        "muted": "\033[38;5;244m",
        "red": "\033[91m",
        "orange": "\033[38;5;214m",
        "yellow": "\033[93m",
        "lime": "\033[92m",
        "green": "\033[32m",
        "cyan": "\033[96m",
        "sky": "\033[38;5;117m",
        "blue": "\033[94m",
        "pink": "\033[95m",
        "magenta": "\033[38;5;205m",
    }

    BG = {
        "slate": "\033[48;5;238m",
        "indigo": "\033[48;5;54m",
        "blue": "\033[48;5;25m",
        "cyan": "\033[48;5;31m",
        "green": "\033[48;5;28m",
        "orange": "\033[48;5;166m",
        "pink": "\033[48;5;161m",
    }


def color(text: str, *codes: str) -> str:
    return "".join(codes) + text + Style.RESET


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def visible_len(text: str) -> int:
    return len(ANSI_RE.sub("", text))


def pad_visible(text: str, width: int) -> str:
    padding = max(width - visible_len(text), 0)
    return text + (" " * padding)


def wrap_panel_line(text: str, width: int) -> list[str]:
    if visible_len(text) <= width:
        return [pad_visible(text, width)]

    plain = ANSI_RE.sub("", text)
    wrapped = textwrap.wrap(plain, width=width) or [""]
    return [segment.ljust(width) for segment in wrapped]


def enable_ansi() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass

    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def box_chars() -> dict[str, str]:
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    if "utf" in encoding:
        return {
            "top_left": "┌",
            "top_right": "┐",
            "bottom_left": "└",
            "bottom_right": "┘",
            "horizontal": "─",
            "vertical": "│",
        }
    return {
        "top_left": "+",
        "top_right": "+",
        "bottom_left": "+",
        "bottom_right": "+",
        "horizontal": "-",
        "vertical": "|",
    }


def clear() -> None:
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def terminal_width(default: int = 100) -> int:
    return shutil.get_terminal_size((default, 30)).columns


def panel(title: str, lines: Iterable[str], accent: str = Style.FG["cyan"]) -> str:
    box = box_chars()
    width = min(max(terminal_width() - 4, 60), 110)
    inner = width - 4
    top = color(box["top_left"] + box["horizontal"] * (width - 2) + box["top_right"], accent)
    bottom = color(
        box["bottom_left"] + box["horizontal"] * (width - 2) + box["bottom_right"],
        accent,
    )
    title_text = pad_visible(color(f" {title} ", Style.BOLD, Style.FG["white"]), inner)
    title_row = color(box["vertical"] + " ", accent) + title_text + color(" " + box["vertical"], accent)

    rows = [top]
    rows.append(title_row)
    for line in lines:
        for wrapped in wrap_panel_line(line, inner):
            rows.append(
                color(box["vertical"] + " ", accent)
                + wrapped
                + color(" " + box["vertical"], accent)
            )
    rows.append(bottom)
    return "\n".join(rows)


def format_bytes(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{num_bytes} B"


def read_key() -> str:
    if os.name == "nt":
        first = msvcrt.getwch()
        if first in ("\x00", "\xe0"):
            second = msvcrt.getwch()
            return {
                "H": "UP",
                "P": "DOWN",
                "K": "LEFT",
                "M": "RIGHT",
            }.get(second, second)
        if first == "\r":
            return "ENTER"
        return first

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        first = sys.stdin.read(1)
        if first == "\x1b":
            ready, _, _ = select.select([sys.stdin], [], [], 0.05)
            if ready:
                second = sys.stdin.read(1)
                third = sys.stdin.read(1)
                return {
                    "[A": "UP",
                    "[B": "DOWN",
                    "[C": "RIGHT",
                    "[D": "LEFT",
                }.get(second + third, "ESC")
            return "ESC"
        if first in ("\r", "\n"):
            return "ENTER"
        return first
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


@dataclass
class MenuChoice:
    key: str
    label: str
    description: str


@dataclass
class KnowledgeFile:
    path: Path
    size_bytes: int


class OllamaClient:
    def __init__(self, base_url: str = API_BASE) -> None:
        self.base_url = base_url

    def _request(self, method: str, path: str, payload: dict | None = None, stream: bool = False):
        url = f"{self.base_url}{path}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            response = urllib.request.urlopen(request)
            return response if stream else json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama API error {exc.code}: {detail}") from exc
        except TimeoutError as exc:
            raise RuntimeError("Request to Ollama timed out. The model might be loading or processing a large input.") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError) or (hasattr(exc.reason, "args") and "timed out" in str(exc.reason).lower()):
                raise RuntimeError("Request to Ollama timed out. The model might be loading or processing a large input.") from exc
            raise RuntimeError(
                f"Could not connect to Ollama at {self.base_url}. Start Ollama first."
            ) from exc

    def version(self) -> str:
        output = subprocess.run(
            ["ollama", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        return output.stdout.strip() or output.stderr.strip() or "unknown"

    def list_models(self) -> list[dict]:
        data = self._request("GET", "/api/tags")
        return data.get("models", [])

    def running_models(self) -> list[dict]:
        data = self._request("GET", "/api/ps")
        return data.get("models", [])

    def show_model(self, name: str) -> dict:
        return self._request("POST", "/api/show", {"model": name})

    def pull_model(self, name: str):
        return self._request("POST", "/api/pull", {"name": name, "stream": True}, stream=True)

    def copy_model(self, source: str, destination: str) -> dict:
        return self._request("POST", "/api/copy", {"source": source, "destination": destination})

    def delete_model(self, name: str) -> dict:
        return self._request("DELETE", "/api/delete", {"name": name})

    def stop_model(self, name: str) -> dict:
        completed = subprocess.run(
            ["ollama", "stop", name],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "Stop failed.")
        return {"status": completed.stdout.strip() or "stopped"}

    def stream_chat(self, model: str, messages: list[dict]):
        return self._request(
            "POST",
            "/api/chat",
            {"model": model, "messages": messages, "stream": True},
            stream=True,
        )


class OllamaCommander:
    def __init__(self) -> None:
        enable_ansi()
        self.client = OllamaClient()
        self.menu = [
            MenuChoice("1", "Chat with a model", "Interactive streaming chat with history."),
            MenuChoice("2", "Pull a model", "Download any model directly from Ollama."),
            MenuChoice("3", "Inspect a model", "See parameters, template, and metadata."),
            MenuChoice("4", "Stop a running model", "Unload active models from memory."),
            MenuChoice("5", "Delete a model", "Remove an installed model cleanly."),
            MenuChoice("6", "Duplicate a model", "Copy one local model to a new tag."),
            MenuChoice("7", "Refresh dashboard", "Reload status and model lists."),
            MenuChoice("8", "Add files", "Attach local files as a session knowledge base."),
            MenuChoice("Q", "Quit", "Exit the commander."),
        ]
        self.selected_index = 0
        self.knowledge_files: list[KnowledgeFile] = []
        self.kb_status_lines: list[str] = []
        self.load_knowledge_files()

    def render_banner(self) -> str:
        width = terminal_width()
        stripes = [
            Style.BG["pink"],
            Style.BG["orange"],
            Style.BG["green"],
            Style.BG["cyan"],
            Style.BG["blue"],
            Style.BG["indigo"],
        ]
        title = " OLLAMA COMMANDER "
        padding = max(width - len(title), 0)
        left = padding // 2
        right = padding - left
        bar = []
        for i in range(left):
            bar.append(stripes[i % len(stripes)] + " ")
        bar.append(color(title, Style.BOLD, Style.FG["white"], Style.BG["pink"]))
        for i in range(right):
            bar.append(stripes[(i + left) % len(stripes)] + " ")
        bar.append(Style.RESET)
        subtitle = color(
            "Bold terminal control for your local Ollama models",
            Style.BOLD,
            Style.FG["sky"],
        )
        return "".join(bar) + "\n" + subtitle

    def pause(self, prompt: str = "Press Enter to continue...") -> None:
        input(color(prompt, Style.FG["silver"]))

    def prompt(self, label: str, allow_empty: bool = False) -> str:
        while True:
            value = input(color(label + " ", Style.BOLD, Style.FG["white"])).strip()
            if value or allow_empty:
                return value
            print(color("A value is required.", Style.FG["red"]))

    def confirm(self, label: str) -> bool:
        answer = input(color(f"{label} [y/N] ", Style.BOLD, Style.FG["yellow"])).strip().lower()
        return answer in {"y", "yes"}

    def select_from_list(self, title: str, items: list[str], empty_message: str) -> str | None:
        if not items:
            clear()
            print(self.render_banner())
            print()
            print(panel(title, [empty_message], Style.FG["orange"]))
            self.pause()
            return None

        selected = 0
        while True:
            clear()
            print(self.render_banner())
            print()
            lines = []
            for index, item in enumerate(items):
                if index == selected:
                    lines.append(color(f"> {item}", Style.BOLD, Style.FG["lime"]))
                else:
                    lines.append(color(f"  {item}", Style.FG["silver"]))
            lines.append("")
            lines.append("Use Up/Down, Enter to select, or Q to cancel.")
            print(panel(title, lines, Style.FG["cyan"]))
            key = read_key()
            if key == "UP":
                selected = (selected - 1) % len(items)
            elif key == "DOWN":
                selected = (selected + 1) % len(items)
            elif key in {"ENTER", "\r"}:
                return items[selected]
            elif key.lower() == "q":
                return None

    def print_dashboard(self) -> tuple[list[dict], list[dict]]:
        models = self.client.list_models()
        running = self.client.running_models()

        total_bytes = sum(model.get("size", 0) for model in models)
        stats_lines = [
            color(f"Installed models : {len(models)}", Style.FG["lime"], Style.BOLD),
            color(f"Running models   : {len(running)}", Style.FG["yellow"], Style.BOLD),
            color(f"Disk footprint   : {format_bytes(total_bytes)}", Style.FG["pink"], Style.BOLD),
            color(f"CLI version      : {self.client.version()}", Style.FG["sky"], Style.BOLD),
        ]
        model_lines = []
        if models:
            for model in models[:8]:
                size = format_bytes(model.get("size", 0))
                modified = model.get("modified_at", "unknown").replace("T", " ").replace("Z", "")
                model_lines.append(
                    color(model.get("name", "unknown"), Style.FG["white"], Style.BOLD)
                    + color(f"  {size}  {modified[:19]}", Style.FG["silver"])
                )
            if len(models) > 8:
                model_lines.append(color(f"... and {len(models) - 8} more", Style.FG["orange"]))
        else:
            model_lines.append(color("No installed models yet.", Style.FG["orange"]))

        live_lines = []
        if running:
            for model in running:
                live_lines.append(
                    color(model.get("name", "unknown"), Style.FG["lime"], Style.BOLD)
                    + color(
                        f"  {model.get('size_vram', 0) // (1024 * 1024)} MB VRAM  ctx {model.get('details', {}).get('parameter_size', 'n/a')}",
                        Style.FG["silver"],
                    )
                )
        else:
            live_lines.append(color("No models currently loaded.", Style.FG["silver"]))

        print(panel("Status", stats_lines, Style.FG["pink"]))
        print()
        print(panel("Installed Models", model_lines, Style.FG["cyan"]))
        print()
        print(panel("Live Memory", live_lines, Style.FG["green"]))
        return models, running

    def print_menu(self) -> None:
        lines = []
        for index, choice in enumerate(self.menu):
            marker = ">" if index == self.selected_index else " "
            style = (Style.BOLD, Style.FG["yellow"]) if index == self.selected_index else (Style.FG["silver"],)
            label = f"{marker} [{choice.key}] {choice.label}"
            lines.append(color(label, *style) + color(f"  {choice.description}", Style.FG["white"]))
        lines.append("")
        lines.append("Use Up/Down or press a shortcut key.")
        print(panel("Commands", lines, Style.FG["orange"]))

    def spinner(self, label: str, seconds: float = 0.8) -> None:
        frames = ["|", "/", "-", "\\"]
        end = time.time() + seconds
        index = 0
        while time.time() < end:
            sys.stdout.write(
                "\r"
                + color(frames[index % len(frames)], Style.FG["pink"], Style.BOLD)
                + " "
                + color(label, Style.FG["silver"])
            )
            sys.stdout.flush()
            time.sleep(0.08)
            index += 1
        sys.stdout.write("\r" + " " * (len(label) + 4) + "\r")
        sys.stdout.flush()

    def read_knowledge_file(self, raw_path: str) -> KnowledgeFile:
        path = Path(raw_path.strip().strip('"')).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()
        if not path.exists():
            raise RuntimeError(f"File not found: {path}")
        if not path.is_file():
            raise RuntimeError(f"Not a file: {path}")
        size_bytes = path.stat().st_size
        if size_bytes == 0:
            raise RuntimeError(f"File is empty: {path}")
        return KnowledgeFile(path=path, size_bytes=size_bytes)

    def extract_paths(self, raw_text: str) -> list[str]:
        text = raw_text.strip()
        if not text:
            return []

        paths = []
        current = []
        quote_char = None

        for char in text:
            if quote_char:
                if char == quote_char:
                    quote_char = None
                else:
                    current.append(char)
                continue

            if char in {'"', "'"}:
                quote_char = char
                continue

            if char in {" ", "\t", "\r", "\n", ";"}:
                token = "".join(current).strip()
                if token and token not in {"&", "|"}:
                    paths.append(token)
                current = []
                continue

            current.append(char)

        token = "".join(current).strip()
        if token and token not in {"&", "|"}:
            paths.append(token)

        return paths

    def save_knowledge_files(self) -> None:
        payload = {"files": [str(item.path) for item in self.knowledge_files]}
        KB_STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def load_knowledge_files(self) -> None:
        self.knowledge_files = []
        if not KB_STATE_FILE.exists():
            return
        try:
            payload = json.loads(KB_STATE_FILE.read_text(encoding="utf-8"))
            for raw_path in payload.get("files", []):
                try:
                    self.knowledge_files.append(self.read_knowledge_file(raw_path))
                except Exception:
                    continue
        except Exception:
            self.knowledge_files = []

    def chunk_text(self, text: str, chunk_chars: int = 3200) -> Iterable[str]:
        text = text.strip()
        if not text:
            return

        parts: list[str] = []
        current_size = 0
        for line in text.splitlines(True):
            parts.append(line)
            current_size += len(line)
            if current_size >= chunk_chars:
                chunk = "".join(parts).strip()
                if chunk:
                    yield chunk
                parts = []
                current_size = 0
        if parts:
            chunk = "".join(parts).strip()
            if chunk:
                yield chunk

    def extract_zip_xml_text(self, path: Path, member_names: list[str]) -> str:
        snippets: list[str] = []
        with zipfile.ZipFile(path) as archive:
            for name in member_names:
                try:
                    raw = archive.read(name)
                except KeyError:
                    continue
                try:
                    root = ET.fromstring(raw)
                except ET.ParseError:
                    continue

                texts = []
                for element in root.iter():
                    if element.text and element.text.strip():
                        texts.append(element.text.strip())
                if texts:
                    snippets.append("\n".join(texts))
        return "\n\n".join(snippets)

    def extract_pptx_text(self, path: Path) -> str:
        with zipfile.ZipFile(path) as archive:
            slide_names = sorted(
                name
                for name in archive.namelist()
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            )
        text = self.extract_zip_xml_text(path, slide_names)
        if text:
            return text
        return f"PowerPoint file: {path.name}"

    def extract_docx_text(self, path: Path) -> str:
        text = self.extract_zip_xml_text(path, ["word/document.xml"])
        return text or f"Word document: {path.name}"

    def extract_file_text(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".pptx":
            return self.extract_pptx_text(path)
        if suffix == ".docx":
            return self.extract_docx_text(path)
        if suffix in {".txt", ".md", ".py", ".json", ".yaml", ".yml", ".toml", ".ini", ".csv", ".log", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".xml", ".sql", ".java", ".c", ".cpp", ".h", ".hpp", ".rs", ".go", ".sh", ".ps1"}:
            return path.read_text(encoding="utf-8", errors="replace")

        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return f"Attached file: {path.name} ({format_bytes(path.stat().st_size)}). Binary or unsupported format."

    def iter_text_chunks(self, path: Path, chunk_chars: int = 3200) -> Iterable[str]:
        text = self.extract_file_text(path)
        yield from self.chunk_text(text, chunk_chars)

    def file_context_limit(self, item: KnowledgeFile) -> int:
        suffix = item.path.suffix.lower()
        if suffix in {".pptx", ".docx"}:
            return 7000
        return 5000

    def file_kind_label(self, item: KnowledgeFile) -> str:
        suffix = item.path.suffix.lower()
        if suffix == ".pptx":
            return "PowerPoint"
        if suffix == ".docx":
            return "Word"
        if suffix:
            return suffix[1:].upper()
        return "FILE"

    def score_chunk(self, query_terms: set[str], chunk: str) -> int:
        if not query_terms:
            return 1
        lowered = chunk.lower()
        return sum(lowered.count(term) for term in query_terms)

    def select_file_context(self, item: KnowledgeFile, user_text: str) -> str:
        max_chars = self.file_context_limit(item)
        query_terms = {
            term
            for term in re.findall(r"[a-zA-Z0-9_./:-]{3,}", user_text.lower())
            if term not in {"the", "and", "for", "with", "from", "that", "this", "into", "about"}
        }
        if not query_terms:
            for chunk in self.iter_text_chunks(item.path):
                return chunk[:max_chars].strip()
            return ""

        best_chunk = ""
        best_score = -1
        first_chunk = ""

        for index, chunk in enumerate(self.iter_text_chunks(item.path)):
            if not chunk:
                continue
            if index == 0:
                first_chunk = chunk
            score = self.score_chunk(query_terms, chunk)
            if score > best_score:
                best_score = score
                best_chunk = chunk
            if best_score > 8:
                break
            if index >= 79:
                break

        selected = best_chunk or first_chunk
        return selected[:max_chars].strip()

    def build_knowledge_message(self, user_text: str) -> str | None:
        if not self.knowledge_files:
            return None

        sections = [
            "Use the following local knowledge files when they are relevant to the user's request.",
            "Treat them as supplemental context, not as higher priority than the user's direct instructions.",
            "The excerpts below were selected from attached files based on the latest user prompt.",
        ]
        for item in self.knowledge_files:
            excerpt = self.select_file_context(item, user_text)
            if not excerpt:
                continue
            sections.append(f"[{self.file_kind_label(item)}] {item.path.name} ({format_bytes(item.size_bytes)})")
            sections.append(excerpt)
        return "\n\n".join(sections)

    def chat_icon(self) -> str:
        encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
        return "◦" if "utf" in encoding else "*"

    def render_chat_bubble(self, text: str, width: int) -> list[str]:
        bubble_limit = max(min(width // 2, 40), 18)
        paragraphs = text.splitlines() or [text]
        wrapped_lines: list[str] = []
        for paragraph in paragraphs:
            wrapped = textwrap.wrap(
                paragraph,
                width=bubble_limit,
                replace_whitespace=False,
                drop_whitespace=True,
            ) or [""]
            wrapped_lines.extend(wrapped)

        inner_width = max((max((len(line) for line in wrapped_lines), default=0) + 2), 6)
        rows = []
        for line in wrapped_lines:
            bubble = color(
                f" {line.ljust(inner_width - 2)} ",
                Style.BOLD,
                Style.FG["white"],
                Style.BG["slate"],
            )
            left_padding = max(width - visible_len(bubble), 0)
            rows.append((" " * left_padding) + bubble)
        return rows

    def render_assistant_message(self, text: str, width: int) -> list[str]:
        content_width = max(min(width - 6, 90), 36)
        rows = []
        paragraphs = text.splitlines() or [text]
        for index, paragraph in enumerate(paragraphs):
            wrapped = textwrap.wrap(
                paragraph,
                width=content_width,
                replace_whitespace=False,
                drop_whitespace=True,
            ) or [""]
            for line in wrapped:
                rows.append(color(line, Style.FG["white"]))
            if index != len(paragraphs) - 1:
                rows.append("")
        return rows

    def render_chat_screen_lines(self, model: str, history: list[dict]) -> list[str]:
        width = min(max(terminal_width() - 6, 50), 110)
        rows = [
            color(f"{model}", Style.BOLD, Style.FG["soft"]),
            color(
                f"{len(self.knowledge_files)} file(s) attached  •  /files  /clear  /exit",
                Style.FG["muted"],
            ),
            "",
        ]

        if not history:
            rows.extend(
                [
                    color("Start the conversation.", Style.FG["muted"]),
                    "",
                ]
            )
            return rows

        for message in history[-8:]:
            role = message.get("role", "")
            content = message.get("content", "").strip() or "(empty)"
            if role == "user":
                rows.extend(self.render_chat_bubble(content, width))
                rows.append("")
                continue

            if role == "assistant":
                thought_seconds = message.get("thought_seconds")
                if thought_seconds is not None:
                    icon = self.chat_icon()
                    rows.append(
                        color(
                            f"{icon}  Thought for {thought_seconds:.1f} seconds",
                            Style.FG["muted"],
                        )
                    )
                    rows.append("")
                rows.extend(self.render_assistant_message(content, width))
                rows.append("")
                continue

            rows.append(color(content, Style.FG["orange"]))
            rows.append("")

        return rows

    def print_chat_screen(self, model: str, history: list[dict]) -> None:
        clear()
        print()
        for line in self.render_chat_screen_lines(model, history):
            print("  " + line if line else "")

    def manage_knowledge_files(self) -> None:
        while True:
            clear()
            print(self.render_banner())
            print()
            lines = [
                color(f"Attached files: {len(self.knowledge_files)}", Style.FG["lime"], Style.BOLD),
                "[A] Add file path(s)",
                "[R] Remove a file",
                "[C] Clear all files",
                "[Q] Back",
                "",
                "Large files are allowed. The chat will pull relevant excerpts when needed.",
                "Tip: you can drag and drop one or many files into the terminal prompt.",
                'Use quotes around paths with spaces, like "C:\\My Notes\\doc.txt".',
                "",
            ]
            if self.kb_status_lines:
                lines.extend(self.kb_status_lines)
                lines.append("")
            if self.knowledge_files:
                lines.extend(
                    f"{idx + 1}. {item.path.name}  [{format_bytes(item.size_bytes)}]  ({item.path})"
                    for idx, item in enumerate(self.knowledge_files)
                )
            else:
                lines.append("No knowledge files attached yet.")

            print(panel("Knowledge Base", lines, Style.FG["cyan"]))
            choice = input(color("\nChoose an action ", Style.BOLD, Style.FG["yellow"])).strip().lower()
            if choice == "q":
                return
            if choice == "a":
                raw_paths = self.prompt("Enter file path(s)")
                added = 0
                notices = []
                self.kb_status_lines = []
                for raw_path in self.extract_paths(raw_paths):
                    try:
                        item = self.read_knowledge_file(raw_path)
                        if any(existing.path == item.path for existing in self.knowledge_files):
                            notices.append(color(f"Skipped duplicate: {item.path.name}", Style.FG["orange"], Style.BOLD))
                            continue
                        self.knowledge_files.append(item)
                        added += 1
                    except Exception as exc:
                        notices.append(color(str(exc), Style.FG["red"], Style.BOLD))
                self.save_knowledge_files()
                if added:
                    self.kb_status_lines.append(color(f"Attached {added} file(s).", Style.FG["lime"], Style.BOLD))
                elif not notices:
                    self.kb_status_lines.append(color("No valid file paths were provided.", Style.FG["orange"], Style.BOLD))
                self.kb_status_lines.extend(notices)
                continue
            if choice == "r":
                if not self.knowledge_files:
                    self.kb_status_lines = [color("No files to remove.", Style.FG["orange"], Style.BOLD)]
                    self.pause()
                    continue
                index_text = self.prompt("Remove which file number?")
                if not index_text.isdigit():
                    self.kb_status_lines = [color("Enter a valid number.", Style.FG["red"], Style.BOLD)]
                    self.pause()
                    continue
                index = int(index_text) - 1
                if not 0 <= index < len(self.knowledge_files):
                    self.kb_status_lines = [color("That file number is out of range.", Style.FG["red"], Style.BOLD)]
                    self.pause()
                    continue
                removed = self.knowledge_files.pop(index)
                self.save_knowledge_files()
                self.kb_status_lines = [color(f"Removed {removed.path.name}.", Style.FG["lime"], Style.BOLD)]
                continue
            if choice == "c":
                self.knowledge_files.clear()
                self.save_knowledge_files()
                self.kb_status_lines = [color("Knowledge base cleared.", Style.FG["lime"], Style.BOLD)]
                continue

    def run(self) -> None:
        while True:
            try:
                clear()
                print(self.render_banner())
                print()
                self.print_dashboard()
                print()
                self.print_menu()
                key = read_key()
                if key == "UP":
                    self.selected_index = (self.selected_index - 1) % len(self.menu)
                    continue
                if key == "DOWN":
                    self.selected_index = (self.selected_index + 1) % len(self.menu)
                    continue

                action = None
                if key == "ENTER":
                    action = self.menu[self.selected_index].key
                else:
                    action = key.upper()
                    for idx, choice in enumerate(self.menu):
                        if choice.key == action:
                            self.selected_index = idx
                            break

                if action == "1":
                    self.chat_flow()
                elif action == "2":
                    self.pull_flow()
                elif action == "3":
                    self.inspect_flow()
                elif action == "4":
                    self.stop_flow()
                elif action == "5":
                    self.delete_flow()
                elif action == "6":
                    self.copy_flow()
                elif action in {"7", "R"}:
                    self.spinner("Refreshing dashboard...")
                elif action == "8":
                    self.manage_knowledge_files()
                elif action == "Q":
                    clear()
                    print(self.render_banner())
                    print()
                    print(color("Session closed. Keep building.", Style.FG["lime"], Style.BOLD))
                    return
            except KeyboardInterrupt:
                clear()
                print(color("Interrupted. Exiting cleanly.", Style.FG["yellow"], Style.BOLD))
                return
            except Exception as exc:
                print()
                print(panel("Error", [str(exc)], Style.FG["red"]))
                self.pause()

    def chat_flow(self) -> None:
        models = [model.get("name", "") for model in self.client.list_models()]
        selected = self.select_from_list("Choose a model", models, "Install a model first with Pull.")
        if not selected:
            return

        history = []
        while True:
            self.print_chat_screen(selected, history)
            try:
                user_text = input(color("\n  › ", Style.BOLD, Style.FG["soft"]))
            except EOFError:
                return
            user_text = user_text.strip()
            if not user_text:
                continue
            if user_text.lower() == "/exit":
                return
            if user_text.lower() == "/clear":
                history.clear()
                continue
            if user_text.lower() == "/files":
                self.manage_knowledge_files()
                continue

            request_messages = []
            knowledge_message = self.build_knowledge_message(user_text)
            if knowledge_message:
                request_messages.append({"role": "system", "content": knowledge_message})

            history.append({"role": "user", "content": user_text})
            request_messages.extend(history)
            parts = []
            try:
                started_at = time.time()
                response = self.client.stream_chat(selected, request_messages)
                for raw in response:
                    if not raw:
                        continue
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    payload = json.loads(line)
                    content = payload.get("message", {}).get("content", "")
                    if content:
                        parts.append(content)
                    if payload.get("done"):
                        break
                history.append(
                    {
                        "role": "assistant",
                        "content": "".join(parts),
                        "thought_seconds": time.time() - started_at,
                    }
                )
            except Exception as exc:
                print(color(f"Chat failed: {exc}", Style.FG["red"], Style.BOLD))
                self.pause()
                return

    def pull_flow(self) -> None:
        clear()
        print(self.render_banner())
        print()
        model_name = self.prompt("Enter model to pull (example: llama3.2:latest)")
        print()
        print(color(f"Pulling {model_name}", Style.FG["pink"], Style.BOLD))
        try:
            response = self.client.pull_model(model_name)
            last_status = ""
            for raw in response:
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                payload = json.loads(line)
                status = payload.get("status", "").strip()
                completed = payload.get("completed")
                total = payload.get("total")
                if completed and total:
                    percent = completed / total * 100
                    message = f"{status}  {percent:5.1f}%"
                else:
                    message = status or last_status or "working..."
                last_status = status or last_status
                sys.stdout.write("\r" + color(message.ljust(60), Style.FG["white"]))
                sys.stdout.flush()
            sys.stdout.write("\n")
            print(color("Pull complete.", Style.FG["lime"], Style.BOLD))
        except Exception as exc:
            print(color(f"Pull failed: {exc}", Style.FG["red"], Style.BOLD))
        self.pause()

    def inspect_flow(self) -> None:
        models = [model.get("name", "") for model in self.client.list_models()]
        selected = self.select_from_list("Inspect which model?", models, "No installed models found.")
        if not selected:
            return
        clear()
        print(self.render_banner())
        print()
        details = self.client.show_model(selected)
        info_lines = [
            color(f"Model       : {selected}", Style.FG["lime"], Style.BOLD),
            f"Family      : {details.get('details', {}).get('family', 'unknown')}",
            f"Parameters  : {details.get('details', {}).get('parameter_size', 'unknown')}",
            f"Quantization: {details.get('details', {}).get('quantization_level', 'unknown')}",
        ]
        parameters = details.get("parameters", "") or "No parameters block."
        template = details.get("template", "") or "No template block."
        print(panel("Model Overview", info_lines, Style.FG["cyan"]))
        print()
        print(panel("Parameters", parameters.splitlines()[:16], Style.FG["orange"]))
        print()
        print(panel("Prompt Template", template.splitlines()[:16], Style.FG["pink"]))
        self.pause()

    def stop_flow(self) -> None:
        running = [model.get("name", "") for model in self.client.running_models()]
        selected = self.select_from_list("Stop which model?", running, "No models are currently running.")
        if not selected:
            return
        try:
            self.client.stop_model(selected)
            print(color(f"Stopped {selected}.", Style.FG["lime"], Style.BOLD))
        except Exception as exc:
            print(color(f"Stop failed: {exc}", Style.FG["red"], Style.BOLD))
        self.pause()

    def delete_flow(self) -> None:
        models = [model.get("name", "") for model in self.client.list_models()]
        selected = self.select_from_list("Delete which model?", models, "No installed models found.")
        if not selected:
            return
        if not self.confirm(f"Delete {selected}?"):
            return
        try:
            self.client.delete_model(selected)
            print(color(f"Deleted {selected}.", Style.FG["lime"], Style.BOLD))
        except Exception as exc:
            print(color(f"Delete failed: {exc}", Style.FG["red"], Style.BOLD))
        self.pause()

    def copy_flow(self) -> None:
        models = [model.get("name", "") for model in self.client.list_models()]
        selected = self.select_from_list("Copy which model?", models, "No installed models found.")
        if not selected:
            return
        destination = self.prompt("New model tag (example: my-llama:latest)")
        try:
            self.client.copy_model(selected, destination)
            print(color(f"Copied {selected} to {destination}.", Style.FG["lime"], Style.BOLD))
        except Exception as exc:
            print(color(f"Copy failed: {exc}", Style.FG["red"], Style.BOLD))
        self.pause()


def main() -> int:
    commander = OllamaCommander()
    commander.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
