"""Read-only Slack feeder: one source file per channel per day.

The channel-day boundary is the one Google Chat and WhatsApp already write, so
the wiki grades a Slack discussion the same way it grades those. Per-thread would
fragment a conversation that ran across threads; per-channel would be one file
that never stops growing.

WHY A DAY IS ALWAYS FETCHED WHOLE
--------------------------------
An incremental run starts at the beginning of the watermark's UTC day, not at the
watermark itself. A day file is only true if the whole day was read: starting
mid-day would rewrite that file with the afternoon alone and silently drop the
morning. One partially re-read day per sync is the price, and it is cheap.

Read-only: nothing here posts, edits, reacts or joins.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

from feeders import options
from feeders.result import SyncResult, failure
from pipeline.source_files import source_path as resolve_source_path
from server import config, sources as sources_index

API = "https://slack.com/api"

# A user token, not a bot token: the point is to read what this person can
# already read, without adding an app to channels.
SCOPES = ("channels:history channels:read groups:history groups:read "
          "im:history mpim:history users:read")

# conversations.history is the tight method. For an app a workspace installed
# itself it stays on its original tier (50+/min); the 2025 non-Marketplace clamp
# of one request a minute applies to distributed apps, not internal ones —
# docs.slack.dev/changelog/2025/06/03/rate-limits-clarity.
# ponytail: one process-wide gap at the slowest tier we use. Split it per method
# if the one-shot listing calls ever become the bottleneck.
MIN_REQUEST_GAP = 60 / 50
_last_request_at = 0.0

HISTORY_PAGE = 200
# A runaway guard on one channel, not a per-run budget: 50k messages is more than
# any real channel has, and hitting it fails that channel loudly rather than
# writing a day file that is missing its morning.
# ponytail: history comes back newest-first, so a truncated fetch would leave its
# OLDEST day partial. Dropping that one day is the upgrade if a channel ever
# genuinely exceeds this.
MAX_HISTORY_PAGES = 250

# Slack's own error codes, in words that name the fix.
ERROR_HELP = {
    "invalid_auth": "Slack rejected the token. Sign in again at /api/slack/signin/<connection id>.",
    "not_authed": "This Slack connection has not signed in yet. Visit /api/slack/signin/<connection id>.",
    "token_revoked": "The Slack token was revoked. Sign in again at /api/slack/signin/<connection id>.",
    "account_inactive": "That Slack account is deactivated.",
    "missing_scope": f"The Slack app is missing a scope. It needs: {SCOPES}. "
                     "Add them under OAuth & Permissions (User Token Scopes) and sign in again.",
    "ratelimited": "Slack is rate limiting this workspace; the sync backed off and gave up.",
}

log = logging.getLogger("cairn.slack")


class SlackError(RuntimeError):
    """A failure the user can act on. connector_check.py prints it verbatim."""


def _pace() -> None:
    global _last_request_at
    gap = MIN_REQUEST_GAP - (time.monotonic() - _last_request_at)
    if gap > 0:
        time.sleep(gap)
    _last_request_at = time.monotonic()


def _retry_after(headers, attempt: int) -> float:
    try:
        return min(float(headers.get("Retry-After", "0")), 60) or 2 ** attempt
    except (TypeError, ValueError):
        return 2 ** attempt


def _call(token: str, method: str, params: dict | None = None) -> dict:
    """One Web API read. Slack signals failure at HTTP 200 with `ok: false`."""
    url = f"{API}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for attempt in range(4):
        _pace()
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < 3:
                time.sleep(_retry_after(error.headers, attempt))
                continue
            if error.code in (500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise SlackError(f"Slack returned HTTP {error.code} for {method}") from error
        if body.get("ok"):
            return body
        code = body.get("error", "unknown_error")
        if code == "ratelimited" and attempt < 3:
            time.sleep(2 ** attempt)
            continue
        raise SlackError(ERROR_HELP.get(code, f"Slack refused {method}: {code}"))
    raise AssertionError("unreachable")


def _paged(token: str, method: str, key: str, params: dict,
           pages: int = MAX_HISTORY_PAGES) -> list[dict]:
    rows, cursor = [], ""
    for _ in range(pages):
        request = dict(params)
        if cursor:
            request["cursor"] = cursor
        body = _call(token, method, request)
        rows.extend(body.get(key, []))
        cursor = (body.get("response_metadata") or {}).get("next_cursor", "")
        if not cursor:
            return rows
    raise SlackError(f"{method} returned more than {pages * HISTORY_PAGE} rows without "
                     "finishing; narrow the channel list or sync a shorter window")


# --------------------------------------------------------------------------
# mrkdwn
# --------------------------------------------------------------------------
# Slack does not send Markdown. It sends mrkdwn, where a mention is <@U123>, a
# channel is <#C123|general>, a link is <https://x|label>, and &, < and > are
# HTML-escaped. Left alone, every article quoting Slack is full of raw user ids.
_LINK = re.compile(r"<(https?://[^|>]+)(?:\|([^>]*))?>")
_REFERENCE = re.compile(r"<([@#!])([^|>]+)(?:\|([^>]*))?>")


def to_markdown(text: str, names: dict[str, str] | None = None) -> str:
    """Slack mrkdwn as readable text. `names` maps user ids to display names."""
    names = names or {}

    def link(match: re.Match) -> str:
        url, label = match.group(1), match.group(2)
        return f"[{label}]({url})" if label else url

    def reference(match: re.Match) -> str:
        sigil, identifier, label = match.group(1), match.group(2), match.group(3)
        if sigil == "@":
            # A label is Slack's own fallback and beats an unresolved id.
            return "@" + (names.get(identifier) or label or identifier)
        if sigil == "#":
            return "#" + (label or names.get(identifier) or identifier)
        # <!here>, <!channel>, <!subteam^S123|@designers>
        return label or "@" + identifier.split("^")[0]

    out = _REFERENCE.sub(reference, _LINK.sub(link, text or ""))
    # Last, and in this order: unescaping & first would turn "&amp;lt;" into "<".
    return out.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------
def identity(token: str) -> dict:
    """Who this token belongs to. Also the cheapest proof it still works."""
    body = _call(token, "auth.test")
    return {"user": body.get("user", ""), "user_id": body.get("user_id", ""),
            "team": body.get("team", ""), "team_id": body.get("team_id", "")}


def user_names(token: str) -> dict[str, str]:
    """Every user id in the workspace mapped to a display name, once per run.

    Messages carry ids only. `users:read` may be refused by a workspace policy,
    in which case ids stay ids rather than the sync failing.
    """
    try:
        members = _paged(token, "users.list", "members", {"limit": 200})
    except SlackError as error:
        log.info("slack: cannot list users (%s); names stay as ids", error)
        return {}
    names = {}
    for member in members:
        profile = member.get("profile") or {}
        names[member.get("id", "")] = (profile.get("display_name")
                                       or profile.get("real_name")
                                       or member.get("name") or member.get("id", ""))
    return names


def list_channels(token: str, wanted: str = "") -> list[dict]:
    """Channels this user is in, narrowed to `wanted` when it names any."""
    channels = _paged(token, "conversations.list", "channels",
                      {"types": "public_channel,private_channel",
                       "exclude_archived": "true", "limit": 200})
    channels = [c for c in channels if c.get("is_member")]
    selected = {name.strip().lstrip("#").lower() for name in wanted.split(",") if name.strip()}
    if not selected:
        return channels
    kept = [c for c in channels if c.get("name", "").lower() in selected]
    unknown = selected - {c.get("name", "").lower() for c in kept}
    if unknown:
        raise SlackError("Not a channel you are in: " + ", ".join(sorted(unknown))
                         + ". Join it in Slack, or correct the channel list.")
    return kept


def day_floor(watermark: str) -> str:
    """A Slack `oldest`, floored to the start of the watermark's UTC day.

    See the module docstring: a day file is only true if the whole day was read.
    """
    if not watermark:
        return "0"
    at = datetime.fromisoformat(watermark.strip().replace("Z", "+00:00")).astimezone(timezone.utc)
    return f"{at.replace(hour=0, minute=0, second=0, microsecond=0).timestamp():.6f}"


def channel_messages(token: str, channel_id: str, oldest: str = "0") -> list[dict]:
    """Every message in a channel since `oldest`, thread replies included.

    Replies do not appear in conversations.history — a threaded discussion is
    invisible without conversations.replies, which is the single easiest way to
    lose most of what a busy channel actually said.
    """
    messages = _paged(token, "conversations.history", "messages",
                      {"channel": channel_id, "oldest": oldest, "limit": HISTORY_PAGE})
    out = []
    for message in messages:
        out.append(message)
        if message.get("thread_ts") == message.get("ts") and message.get("reply_count"):
            replies = _paged(token, "conversations.replies", "messages",
                             {"channel": channel_id, "ts": message["ts"], "limit": HISTORY_PAGE})
            # The parent comes back with its own replies; keep one copy of it.
            out.extend(r for r in replies if r.get("ts") != message.get("ts"))
    seen_ts, unique = set(), []
    for message in sorted(out, key=lambda m: float(m.get("ts", "0"))):
        if message.get("ts") not in seen_ts:
            seen_ts.add(message.get("ts"))
            unique.append(message)
    return unique


def carries_content(message: dict) -> bool:
    """Joins, leaves and channel-purpose notices are noise, not discussion."""
    if message.get("subtype") in ("channel_join", "channel_leave", "channel_topic",
                                  "channel_purpose", "channel_name", "channel_archive"):
        return False
    return bool((message.get("text") or "").strip() or message.get("files")
                or message.get("attachments"))


def author_of(message: dict, names: dict[str, str]) -> str:
    """Who said it. An app posting an alert has no `user`, only a `username`, and
    a whole channel of deploy notices otherwise reads as "unknown"."""
    user = message.get("user") or ""
    return (names.get(user) or message.get("username") or user
            or message.get("bot_id") or "unknown")


def _instant(ts: str) -> datetime:
    return datetime.fromtimestamp(float(ts), timezone.utc)


def render_day(channel_name: str, day: str, messages: list[dict],
               names: dict[str, str]) -> str:
    lines = [f"# #{channel_name} — {day}", ""]
    for message in messages:
        at = _instant(message.get("ts", "0"))
        author = author_of(message, names)
        text = to_markdown(message.get("text") or "", names).strip()
        # A reply is indented under nothing in particular — the thread's own
        # parent may be on an earlier day — so the marker has to carry that.
        marker = "  ↳ " if message.get("thread_ts") and message["thread_ts"] != message.get("ts") else ""
        lines.append(f"{marker}{at.strftime('%H:%M')} {author}: {text}"
                     if text else f"{marker}{at.strftime('%H:%M')} {author}:")
        for attached in message.get("files") or []:
            lines.append(f"    [file] {attached.get('name') or '(unnamed)'} "
                         f"({attached.get('mimetype') or 'unknown'})")
    return "\n".join(lines)


def message_authors(messages: list[dict], names: dict[str, str]) -> list[str]:
    return list(dict.fromkeys(author_of(m, names) for m in messages))


# --------------------------------------------------------------------------
# sign-in
# --------------------------------------------------------------------------
def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    """Slack's consent page for a user token.

    `user_scope`, not `scope`: a bot token would need the app added to every
    channel, which is a change to the workspace this connector has no business
    making.
    """
    return "https://slack.com/oauth/v2/authorize?" + urllib.parse.urlencode(
        {"client_id": client_id, "user_scope": SCOPES,
         "redirect_uri": redirect_uri, "state": state})


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    """The authorization code traded for a user token. Returns token + identity."""
    payload = urllib.parse.urlencode(
        {"client_id": client_id, "client_secret": client_secret,
         "code": code, "redirect_uri": redirect_uri}).encode()
    request = urllib.request.Request(
        f"{API}/oauth.v2.access", data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=60) as response:
        body = json.loads(response.read())
    if not body.get("ok"):
        raise SlackError(f"Slack refused the sign-in: {body.get('error', 'unknown_error')}")
    token = (body.get("authed_user") or {}).get("access_token", "")
    if not token:
        raise SlackError("Slack completed the sign-in without a user token. Check that the "
                         "app requests User Token Scopes, not Bot Token Scopes.")
    return {"token": token,
            "user_id": (body.get("authed_user") or {}).get("id", ""),
            "team": (body.get("team") or {}).get("name", ""),
            "team_id": (body.get("team") or {}).get("id", "")}


NOT_SIGNED_IN = ("This Slack connection has no token yet. Finish the sign-in once: open "
                 "/api/slack/signin/<connection id> in the browser on this machine, "
                 "approve the app, and accept the one certificate warning.")

NO_CHANNELS = ("Slack accepted the sign-in, but this account is not a member of any "
               "channel the token can read. Join the channels you want read in Slack, "
               "and check the app's User Token Scopes include channels:read and "
               "groups:read for private channels.")


def probe(settings: dict, limit: int = 3) -> dict:
    """Authenticate and list a few channels. Writes nothing."""
    token = str(settings.get("token") or "").strip()
    if not token:
        raise SlackError(NOT_SIGNED_IN)
    who = identity(token)
    channels = list_channels(token, str(settings.get("channels") or ""))
    if not channels:
        raise SlackError(NO_CHANNELS)
    return {"account": f"{who['user']} · {who['team']}" if who["team"] else who["user"],
            "items": [{"id": c["id"], "name": f"#{c.get('name', c['id'])}",
                       "private": bool(c.get("is_private"))} for c in channels[:limit]],
            "more_available": len(channels) > limit}


def run(project_id: str | None = None, on_progress=None, connection_id: str | None = None,
        oldest: str | None = None, max_items: int = 0) -> SyncResult:
    """One item = one channel-day. Returns SyncResult(seen, written, failures).

    `oldest` defaults to the start of this connection's watermark day; pass "0"
    for the whole history. A run that stops at max_items reports a failure so the
    caller leaves the watermark where it was and the backlog is retried.
    """
    from server import connections

    settings = connections.settings_for(connection_id) if connection_id else {}
    token = str(settings.get("token") or "").strip()
    if not token:
        raise SlackError(NOT_SIGNED_IN)
    max_items = options.max_items(max_items or settings.get("max_items") or 0)
    if oldest is None:
        oldest = day_floor(connections.watermark(connection_id) if connection_id else "")
    if project_id is None:
        from server import projects
        project_id = projects.ensure_default()

    who = identity(token)
    # Project and workspace scope the ids and the directory, so the same channel
    # read into two knowledge projects cannot overwrite its own source records.
    scope = hashlib.sha256(f"{project_id}\0{who['team_id'] or who['team']}".encode()).hexdigest()[:12]
    sources = resolve_source_path(config.GDRIVE_TARGET_REPO, f"sources/slack/{scope}")
    inbox = config.GDRIVE_TARGET_REPO / "raw" / "inbox"
    sources.mkdir(parents=True, exist_ok=True)
    inbox.mkdir(parents=True, exist_ok=True)

    channels = list_channels(token, str(settings.get("channels") or ""))
    names = user_names(token)
    seen = written = 0
    failures: list[dict] = []
    truncated = False
    for index, channel in enumerate(channels, 1):
        channel_id, channel_name = channel["id"], channel.get("name", channel["id"])
        if max_items and seen >= max_items:
            truncated = True
            break
        if on_progress:
            on_progress(index, len(channels), f"#{channel_name}")
        try:
            by_day: dict[str, list[dict]] = defaultdict(list)
            for message in channel_messages(token, channel_id, oldest):
                if carries_content(message):
                    by_day[_instant(message["ts"]).strftime("%Y-%m-%d")].append(message)
        except Exception as error:
            # Keyed on the channel: the day is unknown when the listing failed.
            uid = f"slack-{scope}-{channel_id}"
            failures.append(failure(uid, f"#{channel_name}", error))
            sources_index.record_failure(id=uid, project_id=project_id, kind="slack",
                                         name=f"#{channel_name}", reason=error,
                                         connection_id=connection_id)
            continue
        for day, messages in sorted(by_day.items()):
            if max_items and seen >= max_items:
                truncated = True
                break
            seen += 1
            uid = f"slack-{scope}-{channel_id}-{day}"
            try:
                text = render_day(channel_name, day, messages, names)
                slug = f"{re.sub(r'[^a-z0-9]+', '-', channel_name.lower()).strip('-')[:40]}-{day}"
                relative = f"sources/slack/{scope}/{slug}.md"
                path = sources / f"{slug}.md"
                sha = hashlib.sha1(text.encode()).hexdigest()[:8]
                previous = hashlib.sha1(path.read_bytes()).hexdigest()[:8] if path.is_file() else None
                entry = inbox / f"{uid}.md"
                changed = previous != sha or not entry.is_file()
                if previous != sha:
                    path.write_text(text, encoding="utf-8")
                authors = message_authors(messages, names)
                url = f"https://app.slack.com/client/{who['team_id']}/{channel_id}"
                if changed:
                    clock = _instant(messages[-1]["ts"]).strftime("%H:%M:%S")
                    entry.write_text(
                        f"---\nid: {uid}\npath: {relative}\nsha: {sha}\n"
                        f"source_type: chat_thread\nstatus: active\ndate: {day}\n"
                        f"time: {json.dumps(clock)}\nauthors: {json.dumps(authors)}\n"
                        f"url: {json.dumps(url)}\n---\n\n{text}\n", encoding="utf-8")
                sources_index.record(id=uid, project_id=project_id, connection_id=connection_id,
                                     kind="slack", name=f"#{channel_name} — {day}",
                                     path=relative, url=url, folder=f"#{channel_name}",
                                     detail=who["team"] or who["team_id"],
                                     size=len(text.encode()), sha=sha, authors=authors)
                written += int(changed)
            except Exception as error:
                failures.append(failure(uid, f"#{channel_name} — {day}", error))
                sources_index.record_failure(id=uid, project_id=project_id, kind="slack",
                                             name=f"#{channel_name} — {day}", reason=error,
                                             connection_id=connection_id)
    if truncated:
        failures.append(failure(
            "slack-backlog", "More Slack channel-days remain",
            f"stopped at max_items={max_items}; raise the limit to finish the backlog"))
    return SyncResult(seen, written, failures)
