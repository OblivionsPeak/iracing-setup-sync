"""
iRacing Setup Sync
Monitors Discord setup channels and downloads .sto files to
Documents\\iRacing\\setups\\Discord\\<channel-name>\\
"""

import asyncio
import json
import os
import re
import sys
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
from pathlib import Path
from datetime import datetime, timedelta, timezone

import discord

# ── Constants ─────────────────────────────────────────────────────────────

APP_NAME    = 'iRacing Setup Sync'
VERSION     = '1.0.0'
CONFIG_FILE = Path(os.getenv('APPDATA', '.')) / 'iRacingSetupSync' / 'config.json'
DEFAULT_OUTPUT = Path.home() / 'Documents' / 'iRacing' / 'setups' / 'Discord'

DEFAULT_CHANNELS = [
    'hymo-setups',
    'grid-and-go-setups',
    'vrs-setups',
    'coach-dave-academy',
]

DEFAULT_CONFIG = {
    'token':         '',
    'channel_names': list(DEFAULT_CHANNELS),
    'output_folder': str(DEFAULT_OUTPUT),
    'backfill_days': 30,
}

BG     = '#1e1f22'
BG2    = '#2b2d31'
BG3    = '#313338'
ACCENT = '#5865f2'
GREEN  = '#23a55a'
TEXT   = '#dbdee1'
DIM    = '#949ba4'
BORDER = '#3f4248'

# ── Config helpers ────────────────────────────────────────────────────────

def load_config() -> dict:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text('utf-8'))}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg: dict):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding='utf-8')

# ── Discord bot ───────────────────────────────────────────────────────────

class SetupBot(discord.Client):
    def __init__(self, cfg: dict, log_fn, status_fn):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self._cfg       = cfg
        self._log       = log_fn
        self._status    = status_fn
        self._synced    = 0
        self._skipped   = 0

    def _channel_names(self) -> set[str]:
        return {c.strip().lower() for c in self._cfg.get('channel_names', [])}

    def _output_dir(self, channel_name: str) -> Path:
        base = Path(self._cfg.get('output_folder', str(DEFAULT_OUTPUT)))
        d = base / channel_name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _safe_filename(self, name: str) -> str:
        return re.sub(r'[\\/:*?"<>|]', '_', name)

    async def _save_attachment(self, attachment: discord.Attachment, channel_name: str,
                                posted_at: datetime | None = None) -> bool:
        if not attachment.filename.lower().endswith('.sto'):
            return False
        out_dir  = self._output_dir(channel_name)
        filename = self._safe_filename(attachment.filename)
        dest     = out_dir / filename
        if dest.exists():
            self._skipped += 1
            return False
        try:
            await attachment.save(dest)
            ts = posted_at.strftime('%Y-%m-%d %H:%M') if posted_at else 'now'
            self._log(f'  ✓ {channel_name}/{filename}  [{ts}]')
            self._synced += 1
            return True
        except Exception as e:
            self._log(f'  ✗ Failed to save {filename}: {e}')
            return False

    def _resolve_channel_name(self, channel) -> str | None:
        """Return the top-level channel name for both TextChannels and Threads."""
        if isinstance(channel, discord.Thread):
            parent = channel.parent
            return parent.name.lower() if parent else None
        if isinstance(channel, discord.TextChannel):
            return channel.name.lower()
        return None

    async def _scan_thread(self, thread: discord.Thread, channel_name: str,
                           cutoff: datetime) -> int:
        count = 0
        try:
            async for message in thread.history(limit=None, after=cutoff, oldest_first=True):
                for attachment in message.attachments:
                    saved = await self._save_attachment(
                        attachment, channel_name, message.created_at)
                    if saved:
                        count += 1
        except discord.Forbidden:
            self._log(f'    No permission to read thread: {thread.name}')
        except Exception as e:
            self._log(f'    Error scanning thread {thread.name}: {e}')
        return count

    async def on_ready(self):
        self._log(f'Logged in as {self.user} — backfilling history…')
        self._status('Syncing history…')

        backfill_days = int(self._cfg.get('backfill_days', 30))
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=backfill_days)
        channel_names = self._channel_names()

        for guild in self.guilds:
            self._log(f'Server: {guild.name}')
            for channel in guild.text_channels:
                if channel.name.lower() not in channel_names:
                    continue
                self._log(f'  Scanning #{channel.name}…')
                count = 0

                # Scan direct channel messages
                try:
                    async for message in channel.history(limit=None, after=cutoff, oldest_first=True):
                        for attachment in message.attachments:
                            if await self._save_attachment(attachment, channel.name, message.created_at):
                                count += 1
                except discord.Forbidden:
                    self._log(f'  No permission to read #{channel.name}')
                except Exception as e:
                    self._log(f'  Error scanning #{channel.name}: {e}')

                # Scan active threads
                try:
                    for thread in channel.threads:
                        self._log(f'    Thread: {thread.name}')
                        count += await self._scan_thread(thread, channel.name, cutoff)
                except Exception as e:
                    self._log(f'  Error listing threads in #{channel.name}: {e}')

                # Scan archived threads
                try:
                    async for thread in channel.archived_threads(limit=None):
                        if thread.archive_timestamp and thread.archive_timestamp < cutoff:
                            continue
                        self._log(f'    Archived thread: {thread.name}')
                        count += await self._scan_thread(thread, channel.name, cutoff)
                except discord.Forbidden:
                    pass
                except Exception as e:
                    self._log(f'  Error listing archived threads in #{channel.name}: {e}')

                self._log(f'  #{channel.name}: {count} new file(s) downloaded')

        self._log(
            f'Backfill complete — {self._synced} downloaded, {self._skipped} already present.'
        )
        self._status(f'Live — watching {len(channel_names)} channel(s) + threads')

    async def on_message(self, message: discord.Message):
        if message.author == self.user:
            return
        channel_name = self._resolve_channel_name(message.channel)
        if not channel_name or channel_name not in self._channel_names():
            return
        for attachment in message.attachments:
            await self._save_attachment(attachment, channel_name, message.created_at)

# ── Bot thread ────────────────────────────────────────────────────────────

class BotThread(threading.Thread):
    def __init__(self, cfg: dict, log_fn, status_fn):
        super().__init__(daemon=True)
        self._cfg    = cfg
        self._log    = log_fn
        self._status = status_fn
        self._bot: SetupBot | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._bot = SetupBot(self._cfg, self._log, self._status)
        try:
            self._loop.run_until_complete(self._bot.start(self._cfg['token']))
        except asyncio.CancelledError:
            pass
        except discord.LoginFailure:
            self._log('ERROR: Invalid bot token — check your token and try again.')
            self._status('Login failed')
        except Exception as e:
            self._log(f'Bot error: {e}')
            self._status('Error — see log')
        finally:
            self._loop.close()

    def stop(self):
        if self._bot and self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._bot.close(), self._loop)

# ── Tkinter UI ────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f'{APP_NAME} v{VERSION}')
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(600, 560)
        try:
            _ico = os.path.join(
                getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))),
                'iracing_setup_sync.ico')
            if os.path.exists(_ico):
                self.iconbitmap(_ico)
        except Exception:
            pass
        self._cfg        = load_config()
        self._bot_thread: BotThread | None = None
        self._running    = False
        self._build_ui()
        self.protocol('WM_DELETE_WINDOW', self._on_close)

    # ── UI ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TFrame',      background=BG)
        style.configure('TLabelframe', background=BG2, foreground=TEXT, relief='flat')
        style.configure('TLabelframe.Label', background=BG2, foreground=TEXT,
                        font=('Segoe UI', 8, 'bold'))
        style.configure('TEntry',      fieldbackground=BG3, foreground=TEXT,
                        insertcolor=TEXT, bordercolor=BORDER, relief='flat')
        style.configure('TButton',     background=BG3, foreground=TEXT,
                        font=('Segoe UI', 9))
        style.configure('Start.TButton', background=ACCENT, foreground='white',
                        font=('Segoe UI', 10, 'bold'))
        style.map('Start.TButton', background=[('active', '#4752c4')])

        # Header
        hdr = tk.Frame(self, bg=BG2, pady=10)
        hdr.pack(fill='x')
        tk.Label(hdr, text='iRacing Setup Sync', bg=BG2, fg=TEXT,
                 font=('Segoe UI', 16, 'bold')).pack(side='left', padx=16)
        tk.Label(hdr, text=f'v{VERSION}', bg=BG2, fg=DIM,
                 font=('Segoe UI', 9)).pack(side='left')
        self._status_var = tk.StringVar(value='Stopped')
        tk.Label(hdr, textvariable=self._status_var, bg=BG2, fg=DIM,
                 font=('Segoe UI', 9, 'italic')).pack(side='right', padx=16)

        body = ttk.Frame(self)
        body.pack(fill='both', expand=True, padx=14, pady=10)

        # Token
        tf = ttk.LabelFrame(body, text='BOT TOKEN', padding=8)
        tf.pack(fill='x', pady=(0, 8))
        tf.columnconfigure(0, weight=1)
        self.v_token = tk.StringVar(value=self._cfg.get('token', ''))
        tok_entry = ttk.Entry(tf, textvariable=self.v_token, show='•', font=('Segoe UI', 9))
        tok_entry.grid(row=0, column=0, sticky='ew', padx=(0, 6))
        ttk.Button(tf, text='Show', command=lambda: tok_entry.config(
            show='' if tok_entry.cget('show') else '•')).grid(row=0, column=1)
        tk.Label(tf, text='Create a bot at discord.com/developers → Applications → Bot → Token',
                 bg=BG2, fg=DIM, font=('Segoe UI', 7)).grid(
                     row=1, column=0, columnspan=2, sticky='w', pady=(4, 0))

        # Output folder
        of = ttk.LabelFrame(body, text='OUTPUT FOLDER', padding=8)
        of.pack(fill='x', pady=(0, 8))
        of.columnconfigure(0, weight=1)
        self.v_folder = tk.StringVar(value=self._cfg.get('output_folder', str(DEFAULT_OUTPUT)))
        ttk.Entry(of, textvariable=self.v_folder, font=('Segoe UI', 9)).grid(
            row=0, column=0, sticky='ew', padx=(0, 6))
        ttk.Button(of, text='Browse…', command=self._browse_folder).grid(row=0, column=1)
        tk.Label(of, text='Setups saved as  <folder>\\<channel-name>\\filename.sto',
                 bg=BG2, fg=DIM, font=('Segoe UI', 7)).grid(
                     row=1, column=0, columnspan=2, sticky='w', pady=(4, 0))

        # Channels + backfill
        mid = ttk.Frame(body)
        mid.pack(fill='x', pady=(0, 8))
        mid.columnconfigure(0, weight=1)
        mid.columnconfigure(1, weight=0)

        cf = ttk.LabelFrame(mid, text='CHANNELS TO WATCH  (one per line)', padding=8)
        cf.grid(row=0, column=0, sticky='nsew', padx=(0, 8))
        self.channel_box = tk.Text(cf, bg=BG3, fg=TEXT, insertbackground=TEXT,
                                   font=('Consolas', 9), relief='flat', bd=0,
                                   height=6, width=30)
        self.channel_box.pack(fill='both', expand=True)
        self.channel_box.insert('1.0', '\n'.join(self._cfg.get('channel_names', DEFAULT_CHANNELS)))

        bf = ttk.LabelFrame(mid, text='BACKFILL', padding=8)
        bf.grid(row=0, column=1, sticky='ns')
        tk.Label(bf, text='Days of history\nto sync on start',
                 bg=BG2, fg=DIM, font=('Segoe UI', 8), justify='center').pack()
        self.v_days = tk.StringVar(value=str(self._cfg.get('backfill_days', 30)))
        ttk.Entry(bf, textvariable=self.v_days, width=6,
                  font=('Segoe UI', 12), justify='center').pack(pady=(6, 0))

        # Buttons
        btn_row = ttk.Frame(body)
        btn_row.pack(fill='x', pady=(0, 8))
        self.start_btn = ttk.Button(btn_row, text='START SYNC', style='Start.TButton',
                                    command=self._toggle, width=16)
        self.start_btn.pack(side='left')
        ttk.Button(btn_row, text='Open Folder', command=self._open_folder).pack(
            side='left', padx=(10, 0))
        ttk.Button(btn_row, text='Clear Log', command=self._clear_log).pack(side='right')

        # Log
        lf = ttk.LabelFrame(body, text='LOG', padding=6)
        lf.pack(fill='both', expand=True)
        self.log_box = scrolledtext.ScrolledText(
            lf, bg=BG3, fg=TEXT, insertbackground=TEXT,
            font=('Consolas', 8), relief='flat', bd=0,
            state='disabled', wrap='word',
        )
        self.log_box.pack(fill='both', expand=True)

        self.log(f'{APP_NAME} v{VERSION} ready.')
        self.log(f'Output: {self.v_folder.get()}')

    # ── Helpers ──────────────────────────────────────────────────────────

    def log(self, msg: str):
        def _do():
            self.log_box.config(state='normal')
            ts = datetime.now().strftime('%H:%M:%S')
            self.log_box.insert('end', f'[{ts}] {msg}\n')
            self.log_box.see('end')
            self.log_box.config(state='disabled')
        self.after(0, _do)

    def set_status(self, msg: str):
        self.after(0, lambda: self._status_var.set(msg))

    def _browse_folder(self):
        d = filedialog.askdirectory(initialdir=self.v_folder.get())
        if d:
            self.v_folder.set(d)

    def _open_folder(self):
        folder = Path(self.v_folder.get())
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def _clear_log(self):
        self.log_box.config(state='normal')
        self.log_box.delete('1.0', 'end')
        self.log_box.config(state='disabled')

    def _read_channels(self) -> list[str]:
        raw = self.channel_box.get('1.0', 'end').strip()
        return [ln.strip() for ln in raw.splitlines() if ln.strip()]

    # ── Start / Stop ─────────────────────────────────────────────────────

    def _toggle(self):
        if self._running:
            self._stop()
        else:
            self._start()

    def _start(self):
        token = self.v_token.get().strip()
        if not token:
            messagebox.showerror('Missing token', 'Enter your bot token before starting.')
            return
        channels = self._read_channels()
        if not channels:
            messagebox.showerror('No channels', 'Enter at least one channel name.')
            return
        try:
            days = max(1, int(self.v_days.get()))
        except ValueError:
            days = 30

        self._cfg.update({
            'token':         token,
            'channel_names': channels,
            'output_folder': self.v_folder.get().strip() or str(DEFAULT_OUTPUT),
            'backfill_days': days,
        })
        save_config(self._cfg)

        self._bot_thread = BotThread(dict(self._cfg), self.log, self.set_status)
        self._bot_thread.start()
        self._running = True
        self.start_btn.config(text='STOP SYNC')
        self.set_status('Starting…')
        self.log('Bot started.')

    def _stop(self):
        if self._bot_thread:
            self._bot_thread.stop()
            self._bot_thread = None
        self._running = False
        self.start_btn.config(text='START SYNC')
        self.set_status('Stopped')
        self.log('Bot stopped.')

    def _on_close(self):
        self._stop()
        self.destroy()


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == '__main__':
    app = App()
    app.mainloop()
