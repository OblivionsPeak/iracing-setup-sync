"""
iRacing Setup Sync v2.0
OAuth2-based: each user logs in with their own Discord account.
No bot token required. Uses Discord REST API with polling.
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

import requests

# ── Constants ─────────────────────────────────────────────────────────────

APP_NAME      = 'iRacing Setup Sync'
VERSION       = '2.0.0'
CONFIG_FILE   = Path(os.getenv('APPDATA', '.')) / 'iRacingSetupSync' / 'config.json'
DEFAULT_OUTPUT = Path.home() / 'Documents' / 'iRacing' / 'setups' / 'Discord'
DISCORD_API   = 'https://discord.com/api/v10'
OAUTH_REDIRECT = 'http://localhost:8765/callback'
OAUTH_SCOPES  = 'identify guilds'
POLL_INTERVAL = 60   # seconds between live polls

DEFAULT_CHANNELS = [
    'hymo-setups',
    'grid-and-go-setups',
    'vrs-setups',
    'coach-dave-academy',
]

DEFAULT_CONFIG = {
    'client_id':     '',
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

# ── OAuth2 PKCE helpers ───────────────────────────────────────────────────

def _pkce_pair() -> tuple[str, str]:
    verifier  = secrets.token_urlsafe(96)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b'=').decode()
    return verifier, challenge

def _auth_url(client_id: str, state: str, challenge: str) -> str:
    return 'https://discord.com/oauth2/authorize?' + urllib.parse.urlencode({
        'client_id':             client_id,
        'redirect_uri':          OAUTH_REDIRECT,
        'response_type':         'code',
        'scope':                 OAUTH_SCOPES,
        'state':                 state,
        'code_challenge':        challenge,
        'code_challenge_method': 'S256',
    })

class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """Single-use HTTP handler that captures the OAuth2 callback."""
    result: dict | None = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        _OAuthCallbackHandler.result = {
            'code':  (params.get('code',  [None])[0]),
            'state': (params.get('state', [None])[0]),
            'error': (params.get('error', [None])[0]),
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

def _wait_for_callback(timeout: int = 120) -> dict | None:
    _OAuthCallbackHandler.result = None
    srv = http.server.HTTPServer(('localhost', 8765), _OAuthCallbackHandler)
    srv.timeout = timeout
    srv.handle_request()
    srv.server_close()
    return _OAuthCallbackHandler.result

def _exchange_code(client_id: str, code: str, verifier: str) -> dict:
    r = requests.post(
        f'{DISCORD_API}/oauth2/token',
        data={
            'grant_type':    'authorization_code',
            'code':          code,
            'redirect_uri':  OAUTH_REDIRECT,
            'client_id':     client_id,
            'code_verifier': verifier,
        },
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()

def _do_refresh(client_id: str, refresh_token: str) -> dict:
    r = requests.post(
        f'{DISCORD_API}/oauth2/token',
        data={
            'grant_type':    'refresh_token',
            'refresh_token': refresh_token,
            'client_id':     client_id,
        },
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()

# ── Discord REST client ───────────────────────────────────────────────────

class DiscordClient:
    def __init__(self, access_token: str):
        self._sess = requests.Session()
        self._sess.headers['Authorization'] = f'Bearer {access_token}'

    def _get(self, path: str, **params) -> dict | list:
        r = self._sess.get(f'{DISCORD_API}{path}', params=params or None, timeout=20)
        r.raise_for_status()
        return r.json()

    def get_user(self) -> dict:
        return self._get('/users/@me')

    def get_guilds(self) -> list:
        return self._get('/users/@me/guilds')

    def get_channels(self, guild_id: str) -> list:
        return self._get(f'/guilds/{guild_id}/channels')

    def get_messages(self, channel_id: str, after: str | None = None) -> list:
        kw = {'limit': 100}
        if after:
            kw['after'] = after
        return self._get(f'/channels/{channel_id}/messages', **kw)

    def get_active_threads(self, guild_id: str) -> dict:
        return self._get(f'/guilds/{guild_id}/threads/active')

    def get_archived_threads(self, channel_id: str, before: str | None = None) -> dict:
        kw = {'limit': 100}
        if before:
            kw['before'] = before
        try:
            return self._get(f'/channels/{channel_id}/threads/archived/public', **kw)
        except requests.HTTPError:
            return {'threads': []}

    def get_thread_messages(self, thread_id: str, after: str | None = None) -> list:
        kw = {'limit': 100}
        if after:
            kw['after'] = after
        return self._get(f'/channels/{thread_id}/messages', **kw)

# ── Sync engine ───────────────────────────────────────────────────────────

def _date_to_snowflake(dt: datetime) -> str:
    ms = int(dt.timestamp() * 1000)
    return str((ms - 1420070400000) << 22)

def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '_', name)

def _car_path(data: bytes) -> str | None:
    try:
        root = ET.fromstring(data.decode('utf-8', errors='replace'))
        return root.get('CarPath') or root.get('carPath') or None
    except ET.ParseError:
        return None

class SyncEngine:
    def __init__(self, cfg: dict, client: DiscordClient, log_fn, status_fn):
        self._cfg     = cfg
        self._client  = client
        self._log     = log_fn
        self._status  = status_fn
        self._stop    = threading.Event()
        self._last_id: dict[str, str] = {}  # channel_id → last seen snowflake
        self._synced  = 0
        self._skipped = 0

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            self._run()
        except Exception as e:
            self._log(f'Sync error: {e}')
            self._status('Error — see log')

    def _channel_names(self) -> set[str]:
        return {c.strip().lower() for c in self._cfg.get('channel_names', [])}

    def _output_dir(self, channel_name: str, car: str | None) -> Path:
        base = Path(self._cfg.get('output_folder', str(DEFAULT_OUTPUT)))
        if car:
            # setups/<car>/Discord/<channel>/
            d = base.parent.parent / car / 'Discord' / channel_name
        else:
            d = base / '_unknown_car' / channel_name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save(self, data: bytes, filename: str, channel_name: str,
              posted_at: datetime | None) -> bool:
        filename = _safe_name(filename)
        car      = _car_path(data)
        dest     = self._output_dir(channel_name, car) / filename
        if dest.exists():
            self._skipped += 1
            return False
        dest.write_bytes(data)
        ts        = posted_at.strftime('%Y-%m-%d %H:%M') if posted_at else 'now'
        car_label = car or '(unknown car)'
        self._log(f'  ✓ {car_label}/{channel_name}/{filename}  [{ts}]')
        self._synced += 1
        return True

    def _process_messages(self, messages: list, channel_name: str) -> int:
        count = 0
        for msg in messages:
            ts = None
            try:
                ts = datetime.fromisoformat(msg['timestamp'].replace('Z', '+00:00'))
            except Exception:
                pass
            for att in msg.get('attachments', []):
                if not att['filename'].lower().endswith('.sto'):
                    continue
                try:
                    r = requests.get(att['url'], timeout=30)
                    r.raise_for_status()
                    if self._save(r.content, att['filename'], channel_name, ts):
                        count += 1
                except Exception as e:
                    self._log(f'  ✗ {att["filename"]}: {e}')
        return count

    def _scan_channel(self, channel_id: str, channel_name: str,
                      after: str | None = None) -> str | None:
        """Fetch all messages in a channel after `after` snowflake. Returns last ID seen."""
        last = after
        while True:
            try:
                msgs = self._client.get_messages(channel_id, after=last)
            except requests.HTTPError as e:
                self._log(f'  Cannot read #{channel_name}: {e}')
                break
            if not msgs:
                break
            msgs.sort(key=lambda m: m['id'])
            self._process_messages(msgs, channel_name)
            last = msgs[-1]['id']
            if len(msgs) < 100:
                break
        return last

    def _scan_thread(self, thread_id: str, channel_name: str,
                     after: str | None = None) -> str | None:
        last = after
        while True:
            try:
                msgs = self._client.get_thread_messages(thread_id, after=last)
            except Exception:
                break
            if not msgs:
                break
            msgs.sort(key=lambda m: m['id'])
            self._process_messages(msgs, channel_name)
            last = msgs[-1]['id']
            if len(msgs) < 100:
                break
        return last

    def _run(self):
        channel_names = self._channel_names()
        self._log('Finding your servers and channels…')

        guilds = self._client.get_guilds()
        # channel_id → (channel_name, guild_id)
        targets: dict[str, tuple[str, str]] = {}
        # thread_id → (parent_channel_name, guild_id)
        thread_targets: dict[str, tuple[str, str]] = {}

        for guild in guilds:
            gid = guild['id']
            try:
                channels = self._client.get_channels(gid)
            except Exception:
                continue
            for ch in channels:
                if ch.get('type') == 0 and ch['name'].lower() in channel_names:
                    targets[ch['id']] = (ch['name'], gid)

        if not targets:
            self._log('No matching channels found in your servers.')
            self._status('No channels found — check channel names in settings')
            return

        # ── Backfill ──────────────────────────────────────────────────────
        days   = int(self._cfg.get('backfill_days', 30))
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
        after  = _date_to_snowflake(cutoff)
        self._log(f'Backfilling last {days} days from {len(targets)} channel(s)…')

        for cid, (cname, gid) in targets.items():
            self._log(f'  #{cname}')
            last = self._scan_channel(cid, cname, after=after)
            if last:
                self._last_id[cid] = last

            # Active threads belonging to this channel
            try:
                active = self._client.get_active_threads(gid)
                for t in active.get('threads', []):
                    if t.get('parent_id') == cid:
                        self._log(f'    Thread: {t["name"]}')
                        tlast = self._scan_thread(t['id'], cname, after=after)
                        if tlast:
                            self._last_id[t['id']] = tlast
                            thread_targets[t['id']] = (cname, gid)
            except Exception as e:
                self._log(f'    Could not list active threads: {e}')

            # Archived threads
            try:
                archived = self._client.get_archived_threads(cid)
                for t in archived.get('threads', []):
                    arc_ts_str = t.get('thread_metadata', {}).get('archive_timestamp', '')
                    if arc_ts_str:
                        arc_ts = datetime.fromisoformat(arc_ts_str.replace('Z', '+00:00'))
                        if arc_ts < cutoff:
                            continue
                    self._log(f'    Archived thread: {t["name"]}')
                    tlast = self._scan_thread(t['id'], cname, after=after)
                    if tlast:
                        self._last_id[t['id']] = tlast
                        thread_targets[t['id']] = (cname, gid)
            except Exception as e:
                self._log(f'    Could not list archived threads: {e}')

        self._log(
            f'Backfill complete — {self._synced} downloaded, {self._skipped} already present.'
        )
        self._status(f'Live — polling every {POLL_INTERVAL}s')

        # ── Poll loop ─────────────────────────────────────────────────────
        while not self._stop.is_set():
            self._stop.wait(POLL_INTERVAL)
            if self._stop.is_set():
                break
            new = 0
            for cid, (cname, gid) in targets.items():
                last = self._scan_channel(cid, cname, after=self._last_id.get(cid))
                if last:
                    self._last_id[cid] = last
                # Check for newly created threads
                try:
                    active = self._client.get_active_threads(gid)
                    for t in active.get('threads', []):
                        if t.get('parent_id') == cid:
                            tlast = self._scan_thread(
                                t['id'], cname, after=self._last_id.get(t['id']))
                            if tlast:
                                self._last_id[t['id']] = tlast
                                thread_targets[t['id']] = (cname, gid)
                except Exception:
                    pass
            for tid, (cname, gid) in thread_targets.items():
                if tid not in {t['id'] for g in [self._client.get_active_threads(gid)]
                               for t in g.get('threads', [])}:
                    continue
            if new:
                self._log(f'[POLL] {new} new setup(s) downloaded')

# ── App ───────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f'{APP_NAME} v{VERSION}')
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(620, 600)
        try:
            _ico = os.path.join(
                getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))),
                'iracing_setup_sync.ico')
            if os.path.exists(_ico):
                self.iconbitmap(_ico)
        except Exception:
            pass
        self._cfg         = load_config()
        self._sync_thread: threading.Thread | None = None
        self._engine: SyncEngine | None = None
        self._running     = False
        self._build_ui()
        self.protocol('WM_DELETE_WINDOW', self._on_close)
        # Auto-refresh user label if already logged in
        if self._cfg.get('discord_user'):
            self._user_var.set(f'Logged in as  {self._cfg["discord_user"]}')

    # ── UI ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TFrame',       background=BG)
        style.configure('TLabelframe',  background=BG2, foreground=TEXT, relief='flat')
        style.configure('TLabelframe.Label', background=BG2, foreground=TEXT,
                        font=('Segoe UI', 8, 'bold'))
        style.configure('TEntry',       fieldbackground=BG3, foreground=TEXT,
                        insertcolor=TEXT, bordercolor=BORDER, relief='flat')
        style.configure('TButton',      background=BG3, foreground=TEXT,
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

        # Discord login
        af = ttk.LabelFrame(body, text='DISCORD ACCOUNT', padding=8)
        af.pack(fill='x', pady=(0, 8))
        af.columnconfigure(1, weight=1)

        tk.Label(af, text='Client ID:', bg=BG2, fg=DIM,
                 font=('Segoe UI', 8)).grid(row=0, column=0, sticky='w', padx=(0, 8))
        self.v_client_id = tk.StringVar(value=self._cfg.get('client_id', ''))
        ttk.Entry(af, textvariable=self.v_client_id,
                  font=('Segoe UI', 9)).grid(row=0, column=1, sticky='ew', padx=(0, 8))
        ttk.Button(af, text='Login with Discord', style='Login.TButton',
                   command=self._do_login).grid(row=0, column=2)

        self._user_var = tk.StringVar(value='Not logged in')
        tk.Label(af, textvariable=self._user_var, bg=BG2, fg=GREEN,
                 font=('Segoe UI', 8)).grid(row=1, column=0, columnspan=3,
                                             sticky='w', pady=(4, 0))
        tk.Label(af,
                 text='Get your Client ID at discord.com/developers → your app → General Information.\n'
                      'Add  http://localhost:8765/callback  as a Redirect URI under OAuth2.',
                 bg=BG2, fg=DIM, font=('Segoe UI', 7), justify='left').grid(
                     row=2, column=0, columnspan=3, sticky='w', pady=(4, 0))

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
        client_id = self.v_client_id.get().strip()
        if not client_id:
            messagebox.showerror('Missing Client ID',
                                 'Enter your Discord application Client ID first.')
            return
        self._user_var.set('Waiting for browser login…')
        threading.Thread(target=self._login_flow, args=(client_id,), daemon=True).start()

    def _login_flow(self, client_id: str):
        try:
            verifier, challenge = _pkce_pair()
            state = secrets.token_hex(16)
            url   = _auth_url(client_id, state, challenge)
            webbrowser.open(url)
            self.log('Browser opened — please authorise the app in Discord.')

            result = _wait_for_callback(timeout=180)
            if not result or result.get('error'):
                self.log(f'Login cancelled or failed: {result}')
                self.after(0, lambda: self._user_var.set('Login failed — try again'))
                return
            if result.get('state') != state:
                self.log('State mismatch — possible CSRF, aborting.')
                return

            tokens = _exchange_code(client_id, result['code'], verifier)
            access  = tokens['access_token']
            refresh = tokens.get('refresh_token', '')
            expiry  = time.time() + tokens.get('expires_in', 604800)

            dc = DiscordClient(access)
            user = dc.get_user()
            name = user.get('username', '?')

            self._cfg.update({
                'client_id':    client_id,
                'access_token': access,
                'refresh_token': refresh,
                'token_expiry':  expiry,
                'discord_user':  name,
            })
            save_config(self._cfg)
            self.log(f'Logged in as {name}')
            self.after(0, lambda: self._user_var.set(f'Logged in as  {name}'))
        except Exception as e:
            self.log(f'Login error: {e}')
            self.after(0, lambda: self._user_var.set('Login error — see log'))

    def _ensure_token(self) -> str | None:
        """Return a valid access token, refreshing if needed."""
        if time.time() < self._cfg.get('token_expiry', 0) - 60:
            return self._cfg['access_token']
        refresh = self._cfg.get('refresh_token', '')
        if not refresh:
            return None
        try:
            tokens  = _do_refresh(self._cfg['client_id'], refresh)
            access  = tokens['access_token']
            self._cfg.update({
                'access_token':  access,
                'refresh_token': tokens.get('refresh_token', refresh),
                'token_expiry':  time.time() + tokens.get('expires_in', 604800),
            })
            save_config(self._cfg)
            self.log('Token refreshed.')
            return access
        except Exception as e:
            self.log(f'Token refresh failed: {e} — please log in again.')
            return None

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
        token = self._ensure_token()
        if not token:
            messagebox.showerror('Not logged in',
                                 'Please log in with Discord before starting.')
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
            'channel_names': channels,
            'output_folder': self.v_folder.get().strip() or str(DEFAULT_OUTPUT),
            'backfill_days': days,
        })
        save_config(self._cfg)

        client       = DiscordClient(token)
        self._engine = SyncEngine(dict(self._cfg), client, self.log, self.set_status)
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


# ── Entry ─────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app = App()
    app.mainloop()
