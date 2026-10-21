"""What you see in the terminal during a pull.

Two versions with the same methods, picked by make_ui():
  LiveUI   a live dashboard (progress bars, request stats, countdowns, recent events), drawn with `rich`.
           Used when the output is a real terminal.
  PlainUI  one line at a time, like a log. Used when output goes to a file or a test captures it.

The pull code only calls these methods. The HTTP client reports what it's doing through
NBAClient.on_event, so neither of them knows anything about drawing.
"""

import logging
import time
from collections import deque
from datetime import datetime

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TaskProgressColumn, TextColumn, TimeRemainingColumn
from rich.table import Table
from rich.text import Text

PHASES = {
    "reference": "Player lists",
    "gamelogs": "Game lists",
    "team": "Team data",
    "games": "Games",
}


def make_ui(client, console=None):
    console = console or Console()
    return LiveUI(client, console) if console.is_terminal else PlainUI(client)


class PlainUI:
    """Prints progress as ordinary lines (the pre-dashboard behaviour)."""

    def __init__(self, client):
        self.client = client
        self._phase = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def season(self, season, is_current):
        print(f"\n=== {season}{' (current season)' if is_current else ''} ===")

    def phase(self, key, total, detail=""):
        print(f"  {PHASES[key]}: {detail}" if detail else f"  {PHASES[key]}")
        self._phase[key] = [0, total, time.monotonic(), self.client.live_requests]

    def advance(self, key):
        p = self._phase[key]
        p[0] += 1
        done, total, t0, live0 = p
        if key == "games" and (done % 25 == 0 or done == total):
            eta = (time.monotonic() - t0) / done * (total - done) / 60
            print(f"    {done:>5}/{total} games  ({self.client.live_requests - live0} live requests so far, "
                  f"~{eta:.0f} min left)", flush=True)

    def note(self, message):
        print(f"  {message}")


class LiveUI:
    """A dashboard that redraws itself a few times a second, so pauses and retry waits count down."""

    EVENTS_SHOWN = 6
    RATE_WINDOW = 300  # requests/min is measured over the last 5 minutes

    def __init__(self, client, console):
        self.client = client
        self.console = console
        self.started = time.monotonic()
        self.title = "NBA pull"
        self.current_phase = ""
        self.status = "Starting"
        self.wait_label, self.wait_until = "", 0.0
        self.events = deque(maxlen=self.EVENTS_SHOWN)
        self.live_times = deque()
        self.failures = 0
        self.progress = Progress(
            TextColumn("{task.description:<12}"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TextColumn("ETA"),
            TimeRemainingColumn(),
            console=console,
            expand=True,
        )
        self.tasks = {}
        self.live = Live(self, console=console, refresh_per_second=4, transient=False)
        self._log_handler = _EventLogHandler(self)
        self._muted = []

    # ---------- lifecycle ----------

    def __enter__(self):
        self.client.on_event = self._on_event
        # Warnings would otherwise be printed straight over the dashboard. Show them in the
        # "recent events" list instead; the log file still gets everything.
        root = logging.getLogger()
        self._muted = [h for h in root.handlers
                       if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)]
        for h in self._muted:
            root.removeHandler(h)
        root.addHandler(self._log_handler)
        self.live.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.status, self.wait_until = ("Finished" if exc_type is None else "Stopped"), 0.0
        self.live.stop()
        root = logging.getLogger()
        root.removeHandler(self._log_handler)
        for h in self._muted:
            root.addHandler(h)
        self.client.on_event = None
        return False

    # ---------- called by the pull ----------

    def season(self, season, is_current):
        self.title = f"NBA pull · {season}{' (current season)' if is_current else ''}"
        for task_id in self.tasks.values():
            self.progress.remove_task(task_id)
        self.tasks.clear()

    def phase(self, key, total, detail=""):
        self.current_phase = PHASES[key]
        self.tasks[key] = self.progress.add_task(PHASES[key], total=total)
        if detail:
            self.note(f"{PHASES[key]}: {detail}")

    def advance(self, key):
        self.progress.advance(self.tasks[key])

    def note(self, message):
        self.events.append((datetime.now().strftime("%H:%M:%S"), message, "dim"))

    # ---------- called by the HTTP client ----------

    def _on_event(self, kind, **info):
        now = time.monotonic()
        if kind == "live":
            self.live_times.append(now)
            self.status = f"Downloaded {info['endpoint']}"
        elif kind == "cached":
            self.status = f"Already have {info['endpoint']} (cached)"
        elif kind == "retry":
            self.wait_label = f"Retry {info['attempt']}/{info['of']} for {info['endpoint']} ({info['problem']})"
            self.wait_until = now + info["seconds"]
        elif kind == "pause":
            self.wait_label = f"Scheduled break after {info['after']:,} requests, then a fresh connection"
            self.wait_until = now + info["seconds"]

    def _event(self, message, style):
        if message.startswith("FAILED"):
            self.failures += 1
        self.events.append((datetime.now().strftime("%H:%M:%S"), message, style))

    # ---------- drawing ----------

    def _req_per_min(self):
        now = time.monotonic()
        while self.live_times and now - self.live_times[0] > self.RATE_WINDOW:
            self.live_times.popleft()
        span = min(self.RATE_WINDOW, now - self.started)
        return len(self.live_times) / (span / 60) if span > 5 else 0.0

    def __rich__(self):
        c = self.client
        stats = Table.grid(expand=True, padding=(0, 2))
        stats.add_column(style="bold", no_wrap=True)
        stats.add_column(ratio=3, no_wrap=True)
        stats.add_column(style="bold", no_wrap=True)
        stats.add_column(ratio=2, no_wrap=True)
        conn = f"{c.requests_on_connection}/{c.pause_every} until break" if c.pause_every else "-"
        stats.add_row("Requests", f"{c.live_requests:,} new · {c.cache_hits:,} cached · "
                                  f"{self._req_per_min():.0f}/min",
                      "Connection", conn)
        stats.add_row("Failures", Text(str(self.failures), style="red bold" if self.failures else "green"),
                      "Retries", Text(str(c.retries), style="yellow" if c.retries else ""))

        left = self.wait_until - time.monotonic()
        if left > 0:
            status = Text(f"{self.wait_label} — waiting {left:.0f}s", style="yellow")
        else:
            status = Text(self.status, style="cyan")
        status_row = Table.grid(padding=(0, 2))
        status_row.add_column(style="bold")
        status_row.add_row("Status", status)

        events = Table.grid(padding=(0, 2))
        events.add_column(style="dim", no_wrap=True)
        events.add_column()
        for when, message, style in self.events:
            events.add_row(when, Text(message, style=style, overflow="ellipsis", no_wrap=True))
        if not self.events:
            events.add_row("", Text("Nothing yet", style="dim"))

        elapsed = time.monotonic() - self.started
        return Panel(
            Group(self.progress, Text(""), stats, status_row, Text(""), Text("Recent events", style="bold"), events),
            title=f"[bold]{self.title}[/] · {self.current_phase}",
            subtitle=f"elapsed {_hms(elapsed)} · Ctrl+C stops safely (re-run to continue)",
            border_style="blue",
        )


class _EventLogHandler(logging.Handler):
    """Routes nbadata's log messages (retries, pauses, failures) into the dashboard's event list."""

    def __init__(self, ui):
        super().__init__(level=logging.INFO)
        self.ui = ui

    def emit(self, record):
        if not record.name.startswith("nbadata"):
            return
        style = "red" if record.levelno >= logging.WARNING and "FAILED" in record.getMessage() else (
            "yellow" if record.levelno >= logging.WARNING else "")
        self.ui._event(record.getMessage(), style)


def _hms(seconds):
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def summary_table(rows, title):
    """A small two-column table for end-of-run summaries; prints fine in or out of a terminal."""
    t = Table(title=title, show_header=False, title_justify="left", title_style="bold")
    t.add_column(style="bold")
    t.add_column()
    for k, v in rows:
        t.add_row(k, v)
    return t
