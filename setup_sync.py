"""
iRacing Setup Sync v2.1
Users log in with their own Discord account (OAuth2).
The bot token is embedded at build time — users never see or enter it.
Setups saved to: Documents\\iRacing\\setups\\<car>\\Discord\\<channel>\\file.sto
"""

import base64
import hashlib
import http.server
import json
import os
import re
import secrets
import sys
import threading
import time
import tkinter as tk
import urllib.parse
import webbrowser
import xml.etree.ElementTree as ET
from tkinter import ttk, scrolledtext, filedialog, messagebox
from pathlib import Path
from datetime import datetime, timedelta, timezone

import discord
import requests

# ── Build-time constants (injected by CI from GitHub Actions secrets) ─────

# The CI build step replaces this placeholder string with the real token.
# In source code it is intentionally blank — the EXE is the only place
# a real value appears.
_EMBEDDED_BOT_TOKEN = 'BOT_TOKEN_PLACEHOLDER'
_DISCORD_CLIENT_ID  = 'CLIENT_ID_PLACEHOLDER'   # public, safe in source

# ── Runtime constants ─────────────────────────────────────────────────────

APP_NAME       = 'iRacing Setup Sync'
VERSION        = '2.1.0'
CONFIG_FILE    = Path(os.getenv('APPDATA', '.')) / 'iRacingSetupSync' / 'config.json'
DEFAULT_OUTPUT = Path.home() / 'Documents' / 'iRacing' / 'setups' / 'Discord'
DISCORD_API    = 'https://discord.com/api/v10'
OAUTH_REDIRECT = 'http://localhost:8765/callback'
OAUTH_SCOPES   = 'identify guilds'
POLL_INTERVAL  = 60   # seconds between live polls

# The server ID of your Discord server (used to verify membership).
# Replace with your server's ID (right-click server → Copy Server ID in Discord).
_REQUIRED_GUILD_ID = 'GUILD_ID_PLACEHOLDER'

DEFAULT_CHANNELS = [
    'hymo-setups',
    'grid-and-go-setups',
    'vrs-setups',
    'coach-dave-academy',
]

DEFAULT_CONFIG = {
    'channel_names': list(DEFAULT_CHANNELS),
    'output_folder': str(DEFAULT_OUTPUT),
    'backfill_days': 30,
    'access_token':  '',
    'refresh_token': '',
    'token_expiry':  0,
    'discord_user':  '',
}

BG     = '#1e1f22'
BG2    = '#2b2d31'
BG3    = '#313338'
ACCENT = '#5865f2'
GREEN  = '#23a55a'
RED    = '#ed4245'
TEXT   = '#dbdee1'
DIM    = '#949ba4'
BORDER = '#3f4248'

# ── Config ────────────────────────────────────────────────────────────────

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

# ── OAuth2 PKCE (user authentication only) ───────────────────────────────

def _pkce_pair() -> tuple[str, str]:
    verifier  = secrets.token_urlsafe(96)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b'=').decode()
    return verifier, challenge

def _auth_url(state: str, challenge: str) -> str:
    return 'https://discord.com/oauth2/authorize?' + urllib.parse.urlencode({
        'client_id':             _DISCORD_CLIENT_ID,
        'redirect_uri':          OAUTH_REDIRECT,
        'response_type':         'code',
        'scope':                 OAUTH_SCOPES,
        'state':                 state,
        'code_challenge':        challenge,
        'code_challenge_method': 'S256',
    })

class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    result: dict | None = None

    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _CallbackHandler.result = {
            'code':  params.get('code',  [None])[0],
            'state': params.get('state', [None])[0],
            'error': params.get('error', [None])[0],
        }
        body = (
            b'<html><body style="font-family:sans-serif;background:#1e1f22;color:#dbdee1;'
            b'display:flex;align-items:center;justify-content:center;height:100vh;margin:0">'
            b'<h2>Logged in! You can close this tab and return to iRacing Setup Sync.</h2>'
            b'</body></html>'
        )
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

def _wait_for_callback(timeout: int = 180) -> dict | None:
    _CallbackHandler.result = None
    srv = http.server.HTTPServer(('localhost', 8765), _CallbackHandler)
    srv.timeout = timeout
    srv.handle_request()
    srv.server_close()
    return _CallbackHandler.result

def _exchange_code(code: str, verifier: str) -> dict:
    r = requests.post(
        f'{DISCORD_API}/oauth2/token',
        data={
            'grant_type':    'authorization_code',
            'code':          code,
            'redirect_uri':  OAUTH_REDIRECT,
            'client_id':     _DISCORD_CLIENT_ID,
            'code_verifier': verifier,
        },
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()

def _refresh_oauth(refresh_token: str) -> dict:
    r = requests.post(
        f'{DISCORD_API}/oauth2/token',
        data={
            'grant_type':    'refresh_token',
            'refresh_token': refresh_token,
            'client_id':     _DISCORD_CLIENT_ID,
        },
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()

def _verify_guild_membership(access_token: str) -> bool:
    """Returns True if the user is a member of the required server."""
    if _REQUIRED_GUILD_ID.endswith('PLACEHOLDER'):
        return True   # dev build — skip check
    try:
        r = requests.get(
            f'{DISCORD_API}/users/@me/guilds',
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10,
        )
        r.raise_for_status()
        guild_ids = {g['id'] for g in r.json()}
        return _REQUIRED_GUILD_ID in guild_ids
    except Exception:
        return False

def _get_discord_user(access_token: str) -> dict:
    r = requests.get(
        f'{DISCORD_API}/users/@me',
        headers={'Authorization': f'Bearer {access_token}'},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()

# ── Discord bot sync engine ───────────────────────────────────────────────

def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '_', name)

def _car_path(data: bytes) -> tuple[str | None, str]:
    """
    Parse CarPath from a .sto XML file.
    Returns (car_path, debug_info) — car_path is None if not found.
    """
    try:
        # Strip UTF-8 BOM if present
        raw = data.lstrip(b'\xef\xbb\xbf')
        text = raw.decode('utf-8', errors='replace')
        root = ET.fromstring(text)

        # Try known attribute names
        for attr in ('CarPath', 'carPath', 'car_path', 'Car'):
            val = root.get(attr, '').strip()
            if val:
                return val, f'tag=<{root.tag}> attr={attr}'

        # Not found — log what attributes ARE present to help diagnosis
        attrs = ', '.join(f'{k}={v!r}' for k, v in list(root.attrib.items())[:6])
        return None, f'tag=<{root.tag}> attrs=[{attrs}]'

    except ET.ParseError as e:
        # Show first 120 chars of raw content to help diagnose
        preview = data[:120].decode('utf-8', errors='replace').replace('\n', ' ')
        return None, f'XML parse error: {e} | content: {preview!r}'
    except Exception as e:
        return None, f'Error: {e}'

class SyncEngine:
    def __init__(self, cfg: dict, log_fn, status_fn):
        self._cfg    = cfg
        self._log    = log_fn
        self._status = status_fn
        self._stop   = threading.Event()
        self._synced  = 0
        self._skipped = 0

    def stop(self):
        self._stop.set()

    def run(self):
        intents = discord.Intents.default()
        intents.message_content = True
        bot = discord.Client(intents=intents)

        @bot.event
        async def on_ready():
            self._log(f'Bot connected — backfilling history…')
            self._status('Syncing history…')
            await self._backfill(bot)
            self._log(
                f'Backfill complete — {self._synced} downloaded, {self._skipped} already present.'
            )
            self._status(f'Live — watching {len(self._channel_names())} channel(s)')

        @bot.event
        async def on_message(message: discord.Message):
            if message.author == bot.user:
                return
            channel_name = self._resolve_name(message.channel)
            if not channel_name:
                return
            for att in message.attachments:
                await self._save_attachment(att, channel_name, message.created_at)

        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        def _check_stop():
            if self._stop.is_set():
                loop.create_task(bot.close())
            else:
                loop.call_later(1, _check_stop)

        loop.call_later(1, _check_stop)
        try:
            loop.run_until_complete(bot.start(_EMBEDDED_BOT_TOKEN))
        except Exception as e:
            if _EMBEDDED_BOT_TOKEN.endswith('PLACEHOLDER'):
                self._log('ERROR: Bot token not injected — use the release EXE, not a dev build.')
            elif 'Improper token' in str(e) or 'LoginFailure' in str(type(e).__name__):
                self._log('ERROR: Bot token is invalid.')
            else:
                self._log(f'Bot error: {e}')
            self._status('Bot error — see log')
        finally:
            loop.close()

    def _channel_names(self) -> set[str]:
        return {c.strip().lower() for c in self._cfg.get('channel_names', [])}

    def _resolve_name(self, channel) -> str | None:
        if isinstance(channel, discord.Thread):
            return channel.parent.name.lower() if channel.parent else None
        if isinstance(channel, discord.TextChannel):
            return channel.name.lower() if channel.name.lower() in self._channel_names() else None
        return None

    def _output_dir(self, channel_name: str, car: str | None) -> Path:
        base = Path(self._cfg.get('output_folder', str(DEFAULT_OUTPUT)))
        if car:
            d = base.parent.parent / car / 'Discord' / channel_name
        else:
            d = base / '_unknown_car' / channel_name
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def _save_attachment(self, att: discord.Attachment, channel_name: str,
                                posted_at: datetime | None) -> bool:
        if not att.filename.lower().endswith('.sto'):
            return False
        try:
            data = await att.read()
            car, dbg = _car_path(data)
            if not car:
                self._log(f'  ! Could not detect car for {att.filename} — {dbg}')
            dest = self._output_dir(channel_name, car) / _safe_name(att.filename)
            if dest.exists():
                self._skipped += 1
                return False
            dest.write_bytes(data)
            ts  = posted_at.strftime('%Y-%m-%d %H:%M') if posted_at else 'now'
            self._log(f'  ✓ {car or "_unknown_car"}/{channel_name}/{att.filename}  [{ts}]')
            self._synced += 1
            return True
        except Exception as e:
            self._log(f'  ✗ {att.filename}: {e}')
            return False

    async def _scan_thread(self, thread: discord.Thread, channel_name: str,
                           cutoff: datetime) -> int:
        count = 0
        try:
            async for msg in thread.history(limit=None, after=cutoff, oldest_first=True):
                for att in msg.attachments:
                    if await self._save_attachment(att, channel_name, msg.created_at):
                        count += 1
        except discord.Forbidden:
            self._log(f'    No permission: {thread.name}')
        except Exception as e:
            self._log(f'    Thread error {thread.name}: {e}')
        return count

    async def _backfill(self, bot: discord.Client):
        days   = int(self._cfg.get('backfill_days', 30))
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
        names  = self._channel_names()

        for guild in bot.guilds:
            for channel in guild.text_channels:
                if channel.name.lower() not in names:
                    continue
                self._log(f'  #{channel.name}')
                try:
                    async for msg in channel.history(limit=None, after=cutoff, oldest_first=True):
                        for att in msg.attachments:
                            await self._save_attachment(att, channel.name, msg.created_at)
                except discord.Forbidden:
                    self._log(f'  No permission: #{channel.name}')

                for thread in channel.threads:
                    self._log(f'    Thread: {thread.name}')
                    await self._scan_thread(thread, channel.name, cutoff)

                try:
                    async for thread in channel.archived_threads(limit=None):
                        if (thread.archive_timestamp and thread.archive_timestamp < cutoff):
                            continue
                        self._log(f'    Archived: {thread.name}')
                        await self._scan_thread(thread, channel.name, cutoff)
                except Exception:
                    pass

# ── Tkinter UI ────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f'{APP_NAME} v{VERSION}')
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(620, 580)
        try:
            _ico = os.path.join(
                getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))),
                'iracing_setup_sync.ico')
            if os.path.exists(_ico):
                self.iconbitmap(_ico)
        except Exception:
            pass
        self._cfg         = load_config()
        self._engine: SyncEngine | None = None
        self._sync_thread: threading.Thread | None = None
        self._running     = False
        self._build_ui()
        self.protocol('WM_DELETE_WINDOW', self._on_close)
        if self._cfg.get('discord_user'):
            self._user_var.set(f'Logged in as  {self._cfg["discord_user"]}')

    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TFrame',        background=BG)
        style.configure('TLabelframe',   background=BG2, foreground=TEXT, relief='flat')
        style.configure('TLabelframe.Label', background=BG2, foreground=TEXT,
                        font=('Segoe UI', 8, 'bold'))
        style.configure('TEntry',        fieldbackground=BG3, foreground=TEXT,
                        insertcolor=TEXT, bordercolor=BORDER, relief='flat')
        style.configure('TButton',       background=BG3, foreground=TEXT,
                        font=('Segoe UI', 9))
        style.configure('Start.TButton', background=ACCENT, foreground='white',
                        font=('Segoe UI', 10, 'bold'))
        style.map('Start.TButton',  background=[('active', '#4752c4')])
        style.configure('Login.TButton', background=ACCENT, foreground='white',
                        font=('Segoe UI', 9, 'bold'))
        style.map('Login.TButton',  background=[('active', '#4752c4')])

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

        # Discord account
        af = ttk.LabelFrame(body, text='DISCORD ACCOUNT', padding=8)
        af.pack(fill='x', pady=(0, 8))
        self._user_var = tk.StringVar(value='Not logged in')
        tk.Label(af, textvariable=self._user_var, bg=BG2, fg=GREEN,
                 font=('Segoe UI', 10, 'bold')).pack(side='left')
        ttk.Button(af, text='Login with Discord', style='Login.TButton',
                   command=self._do_login).pack(side='right')

        # Output folder
        of = ttk.LabelFrame(body, text='OUTPUT FOLDER', padding=8)
        of.pack(fill='x', pady=(0, 8))
        of.columnconfigure(0, weight=1)
        self.v_folder = tk.StringVar(value=self._cfg.get('output_folder', str(DEFAULT_OUTPUT)))
        ttk.Entry(of, textvariable=self.v_folder,
                  font=('Segoe UI', 9)).grid(row=0, column=0, sticky='ew', padx=(0, 6))
        ttk.Button(of, text='Browse…', command=self._browse_folder).grid(row=0, column=1)
        tk.Label(of, text='Setups saved as  <iRacing setups>\\<car>\\Discord\\<channel>\\file.sto',
                 bg=BG2, fg=DIM, font=('Segoe UI', 7)).grid(
                     row=1, column=0, columnspan=2, sticky='w', pady=(4, 0))

        # Channels + backfill
        mid = ttk.Frame(body)
        mid.pack(fill='x', pady=(0, 8))
        mid.columnconfigure(0, weight=1)

        cf = ttk.LabelFrame(mid, text='CHANNELS TO WATCH  (one per line)', padding=8)
        cf.grid(row=0, column=0, sticky='nsew', padx=(0, 8))
        self.channel_box = tk.Text(cf, bg=BG3, fg=TEXT, insertbackground=TEXT,
                                   font=('Consolas', 9), relief='flat', bd=0,
                                   height=5, width=30)
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

    # ── OAuth2 login ─────────────────────────────────────────────────────

    def _do_login(self):
        if _DISCORD_CLIENT_ID.endswith('PLACEHOLDER'):
            messagebox.showerror('Dev build',
                                 'This is a dev build — Client ID not injected yet.')
            return
        self._user_var.set('Waiting for browser…')
        threading.Thread(target=self._login_flow, daemon=True).start()

    def _login_flow(self):
        try:
            verifier, challenge = _pkce_pair()
            state = secrets.token_hex(16)
            webbrowser.open(_auth_url(state, challenge))
            self.log('Browser opened — please authorise the app.')

            result = _wait_for_callback()
            if not result or result.get('error'):
                self.after(0, lambda: self._user_var.set('Login cancelled'))
                return
            if result.get('state') != state:
                self.log('State mismatch — aborting.')
                return

            tokens = _exchange_code(result['code'], verifier)
            access  = tokens['access_token']
            refresh = tokens.get('refresh_token', '')
            expiry  = time.time() + tokens.get('expires_in', 604800)

            if not _verify_guild_membership(access):
                self.log('ERROR: You are not a member of the required server.')
                self.after(0, lambda: self._user_var.set('Not in server — access denied'))
                return

            user = _get_discord_user(access)
            name = user.get('username', '?')
            self._cfg.update({
                'access_token':  access,
                'refresh_token': refresh,
                'token_expiry':  expiry,
                'discord_user':  name,
            })
            save_config(self._cfg)
            self.log(f'Logged in as {name} — server membership verified.')
            self.after(0, lambda: self._user_var.set(f'Logged in as  {name}'))
        except Exception as e:
            self.log(f'Login error: {e}')
            self.after(0, lambda: self._user_var.set('Login error — see log'))

    # ── Helpers ──────────────────────────────────────────────────────────

    def log(self, msg: str):
        def _do():
            self.log_box.config(state='normal')
            self.log_box.insert('end', f'[{datetime.now().strftime("%H:%M:%S")}] {msg}\n')
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

    # ── Start / Stop ─────────────────────────────────────────────────────

    def _toggle(self):
        if self._running:
            self._stop()
        else:
            self._start()

    def _start(self):
        if not self._cfg.get('discord_user'):
            messagebox.showerror('Not logged in', 'Please log in with Discord first.')
            return
        channels = [ln.strip() for ln in
                    self.channel_box.get('1.0', 'end').strip().splitlines() if ln.strip()]
        if not channels:
            messagebox.showerror('No channels', 'Enter at least one channel name.')
            return
        try:
            days = max(1, int(self.v_days.get()))
        except ValueError:
            days = 30

        self._cfg.update({
            'channel_names': channels,
            'output_folder': self.v_folder.get().strip() or str(DEFAULT_OUTPUT),
            'backfill_days': days,
        })
        save_config(self._cfg)

        self._engine      = SyncEngine(dict(self._cfg), self.log, self.set_status)
        self._sync_thread = threading.Thread(target=self._engine.run, daemon=True)
        self._sync_thread.start()
        self._running = True
        self.start_btn.config(text='STOP SYNC')
        self.set_status('Starting…')
        self.log('Sync started.')

    def _stop(self):
        if self._engine:
            self._engine.stop()
            self._engine = None
        self._running = False
        self.start_btn.config(text='START SYNC')
        self.set_status('Stopped')
        self.log('Sync stopped.')

    def _on_close(self):
        self._stop()
        self.destroy()


if __name__ == '__main__':
    app = App()
    app.mainloop()
