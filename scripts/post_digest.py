#!/usr/bin/env python3
"""
Post digest to configurable destinations (Slack, Discord, custom webhook).

Supported destinations:
  - slack: Slack Incoming Webhook
  - discord: Discord Webhook (text + optional audio attachment)
  - webhook: Generic HTTP POST (JSON)
  - file: Save to file (default behavior)

Usage:
    python post_digest.py digest.md --config config/post_destinations.yaml
    python post_digest.py digest.md --slack-url https://hooks.slack.com/...
    python post_digest.py digest.md --discord-url https://discord.com/api/webhooks/...

    # Post with audio briefing attached
    python post_digest.py digest.md --discord-url https://... --audio briefing.mp3

    # Generate audio on-the-fly and post
    python post_digest.py digest.md --discord-url https://... --generate-audio
"""

import argparse
import json
import os
import re
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


def expand_env(val: str) -> str:
    """Expand ${VAR} to os.environ.get('VAR', '')."""
    if not isinstance(val, str):
        return val
    m = re.match(r"^\$\{([^}]+)\}$", val.strip())
    if m:
        return os.environ.get(m.group(1), "")
    return val


def load_config(config_path: str) -> dict:
    """Load YAML config. Returns empty dict if not found or YAML unavailable."""
    if not yaml:
        return {}
    path = Path(config_path)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    # Expand ${VAR} in nested values
    if "destinations" in data:
        for dest_name, dest_cfg in data["destinations"].items():
            if isinstance(dest_cfg, dict):
                for k, v in list(dest_cfg.items()):
                    if isinstance(v, str):
                        dest_cfg[k] = expand_env(v)
    return data


def truncate_for_slack(text: str, max_len: int = 3500) -> str:
    """Truncate text for Slack (blocks have limits)."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 50] + "\n\n... (truncated)"


def post_to_slack(webhook_url: str, content: str, title: str = "Twitter Digest") -> bool:
    """Post digest to Slack via Incoming Webhook."""
    # Convert markdown links [text](url) to Slack mrkdwn <url|text>
    content_slack = re.sub(r"\[([^\]]+)\]\((https://[^)]+)\)", r"<\2|\1>", content)

    truncated = truncate_for_slack(content_slack)

    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": title, "emoji": True}
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": truncated}
            }
        ]
    }

    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        print(f"Slack error: {e}")
        return False
    except Exception as e:
        print(f"Slack error: {e}")
        return False


def truncate_for_discord(text: str, max_len: int = 1900) -> str:
    """Truncate for Discord (embed description limit 4096, but 2000 is safer)."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 50] + "\n\n... (truncated)"


def sanitize_for_discord(text: str) -> str:
    """Sanitize text for Discord embed (avoid 403 from invalid chars)."""
    # Discord can reject certain characters; replace problematic ones
    text = text.replace("\x00", "")  # Null bytes
    # Limit consecutive newlines
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text


def post_to_discord(webhook_url: str, content: str, title: str = "Twitter Digest") -> bool:
    """Post digest to Discord via Webhook."""
    url = webhook_url.strip()  # Remove accidental whitespace from secrets
    if not url.startswith("https://discord.com/api/webhooks/"):
        print("Discord error: Invalid webhook URL format")
        return False

    truncated = truncate_for_discord(content)
    truncated = sanitize_for_discord(truncated)

    payload = {
        "embeds": [{
            "title": title[:256],  # Discord limit
            "description": truncated,
            "color": 0x1DA1F2  # Twitter blue
        }]
    }

    # Cloudflare (error 1010) blocks datacenter IPs; mimic browser headers
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status in (200, 204)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:300]
        except Exception:
            pass
        print(f"Discord error: {e}")
        if body:
            print(f"Response: {body}")
        return False
    except Exception as e:
        print(f"Discord error: {e}")
        return False


def post_audio_to_discord(webhook_url: str, audio_path: str,
                          content: str = "", title: str = "Twitter Digest") -> bool:
    """
    Post audio file as attachment to Discord via Webhook (multipart/form-data).

    Discord webhooks accept multipart uploads with up to 25MB files.
    We send the audio as a file attachment with an optional text embed.
    """
    url = webhook_url.strip()
    if not url.startswith("https://discord.com/api/webhooks/"):
        print("Discord error: Invalid webhook URL format")
        return False

    if not os.path.exists(audio_path):
        print(f"Discord error: Audio file not found: {audio_path}")
        return False

    file_size = os.path.getsize(audio_path)
    if file_size > 25 * 1024 * 1024:  # 25MB Discord limit
        print(f"Discord error: Audio file too large ({file_size / 1024 / 1024:.1f} MB, max 25 MB)")
        return False

    filename = os.path.basename(audio_path)
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"

    # Build multipart body
    body_parts = []

    # JSON payload part (embed with text summary)
    summary = content[:300] + "..." if len(content) > 300 else content
    payload_json = json.dumps({
        "embeds": [{
            "title": title[:256],
            "description": sanitize_for_discord(summary),
            "color": 0x1DA1F2,
            "footer": {"text": "Audio briefing attached below"}
        }]
    })

    body_parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="payload_json"\r\n'
        f"Content-Type: application/json\r\n\r\n"
        f"{payload_json}\r\n"
    )

    # File part
    with open(audio_path, "rb") as f:
        audio_data = f.read()

    content_type = "audio/mpeg" if audio_path.endswith(".mp3") else "audio/wav"

    body_parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="files[0]"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    )

    # Assemble body
    body = b""
    body += body_parts[0].encode("utf-8")
    body += body_parts[1].encode("utf-8")
    body += audio_data
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")

    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
    }

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status in (200, 204)
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode()[:500]
        except Exception:
            pass
        print(f"Discord audio upload error: {e.code}")
        if body_text:
            print(f"Response: {body_text}")
        return False
    except Exception as e:
        print(f"Discord audio upload error: {e}")
        return False


def _log_http_error(prefix: str, e: Exception, url: str) -> None:
    """Log HTTP error with full details for debugging."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    safe_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"
    print(f"\n--- {prefix} DEBUG ---")
    print(f"URL: {safe_url}")
    print(f"Error: {e}")
    if isinstance(e, urllib.error.HTTPError):
        print(f"Status: {e.code} {e.reason}")
        try:
            body = e.read().decode("utf-8", errors="replace")
            print(f"Response body: {body[:500]}")
        except Exception:
            pass
        if e.headers:
            for h in ["cf-ray", "x-request-id", "cf-cache-status", "content-type", "cf-mitigated-by"]:
                for k, v in e.headers.items():
                    if k.lower() == h.lower():
                        print(f"Header {k}: {v}")
                        break
    print("--- END DEBUG ---\n")


def post_to_webhook(webhook_url: str, content: str, title: str = "Twitter Digest") -> bool:
    """Post to generic webhook (JSON body)."""
    url = webhook_url.strip()
    if not url:
        print("Webhook error: URL is empty")
        return False

    # Truncate for bridge: test が通る = 小さいペイロードが鍵。Discord は 1900 文字で切り詰めるので事前に制限
    max_content = 2000
    if len(content) > max_content:
        content = content[:max_content - 50] + "\n\n... (truncated)"

    payload = {
        "title": title,
        "content": content
    }

    # Python-urllib が Cloudflare にブロックされることがあるため、curl と同様のヘッダーを使用
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "curl/8.0.0",  # test の curl と同じく curl と偽装
        "Accept": "*/*",
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status in (200, 201, 204)
    except urllib.error.HTTPError as e:
        _log_http_error("Webhook", e, url)
        return False
    except urllib.error.URLError as e:
        print(f"\n--- Webhook DEBUG ---")
        print(f"URL: {url[:50]}... (masked)")
        print(f"URLError: {e.reason if hasattr(e, 'reason') else e}")
        print("--- END DEBUG ---\n")
        return False
    except Exception as e:
        print(f"Webhook error: {type(e).__name__}: {e}")
        return False


def save_to_file(path: str, content: str) -> bool:
    """Save content to file."""
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except Exception as e:
        print(f"File error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Post digest to configurable destinations")
    parser.add_argument("input", nargs="?", help="Digest markdown file (default: digest.md)")
    parser.add_argument("--config", "-c", help="Config file (YAML)")
    parser.add_argument("--slack-url", help="Slack Incoming Webhook URL (overrides config)")
    parser.add_argument("--discord-url", help="Discord Webhook URL (overrides config)")
    parser.add_argument("--discord-bridge-url", help="Bridge URL to avoid 1010 (POST to bridge -> Discord)")
    parser.add_argument("--webhook-url", help="Custom webhook URL (overrides config)")
    parser.add_argument("--output", "-o", help="Also save to this file path")
    parser.add_argument("--title", default="Twitter Digest (@ichita.base.eth)", help="Post title")
    parser.add_argument("--audio", help="Audio file to attach to Discord post")
    parser.add_argument("--generate-audio", action="store_true",
                        help="Generate TTS audio from digest and attach to Discord")
    parser.add_argument("--audio-voice", default="nova",
                        help="TTS voice for --generate-audio (default: nova)")
    args = parser.parse_args()

    scripts_dir = Path(__file__).resolve().parent
    project_dir = scripts_dir.parent

    # Resolve input file
    if args.input:
        input_path = Path(args.input)
    else:
        input_path = project_dir / "digest.md"

    if not input_path.exists():
        # Try common locations
        for name in ["twitter-digest-v2.md", "twitter-digest.md", "digest.md"]:
            p = project_dir / name
            if p.exists():
                input_path = p
                break

    if not input_path.exists():
        print(f"Error: Digest file not found. Specify path or create {project_dir / 'digest.md'}")
        return 1

    with open(input_path, encoding="utf-8") as f:
        content = f.read()

    # Load config
    config_path = args.config or (project_dir / "config" / "post_destinations.yaml")
    config = load_config(str(config_path))
    destinations = config.get("destinations", {})

    # Collect enabled destinations (CLI overrides config)
    def get_dest(dest_key: str):
        d = destinations.get(dest_key)
        return d if isinstance(d, dict) else {}

    def get_url(dest_key: str, url_key: str, env_var: str) -> str:
        dest = get_dest(dest_key)
        if dest.get("enabled", True) is False:
            return ""
        url = dest.get(url_key) or os.environ.get(env_var)
        return expand_env(str(url)) if url else ""

    to_slack = args.slack_url or get_url("slack", "webhook_url", "SLACK_WEBHOOK_URL")
    to_discord_bridge = args.discord_bridge_url or os.environ.get("DISCORD_BRIDGE_URL", "").strip()
    to_discord = args.discord_url or get_url("discord", "webhook_url", "DISCORD_WEBHOOK_URL")
    to_webhook = args.webhook_url or get_url("webhook", "url", "CUSTOM_WEBHOOK_URL")
    to_file = args.output
    if not to_file and get_dest("file").get("enabled", True) != False:
        to_file = get_dest("file").get("path")

    # Handle audio generation if requested
    audio_path = args.audio
    if args.generate_audio and not audio_path:
        print("Generating TTS audio from digest...")
        scripts_dir = Path(__file__).resolve().parent
        audio_path = str(input_path.with_suffix(".mp3"))
        gen_cmd = [
            "python", str(scripts_dir / "generate_audio.py"),
            str(input_path),
            "--output", audio_path,
            "--voice", args.audio_voice,
        ]
        result = subprocess.run(gen_cmd, capture_output=False)
        if result.returncode != 0:
            print("Audio generation failed, posting text only")
            audio_path = None

    results = []

    if to_slack:
        ok = post_to_slack(to_slack, content, args.title)
        results.append(("Slack", ok))
    if to_discord_bridge:
        ok = post_to_webhook(to_discord_bridge, content, args.title)
        results.append(("Discord (via bridge)", ok))
    elif to_discord:
        # Post text embed
        ok = post_to_discord(to_discord, content, args.title)
        results.append(("Discord", ok))
        # Post audio attachment if available
        if audio_path and os.path.exists(audio_path):
            ok_audio = post_audio_to_discord(to_discord, audio_path, content, args.title)
            results.append(("Discord Audio", ok_audio))
    if to_webhook:
        ok = post_to_webhook(to_webhook, content, args.title)
        results.append(("Webhook", ok))
    if to_file:
        ok = save_to_file(to_file, content)
        results.append(("File", ok))

    if not results:
        print("No destination configured. Use --config, --slack-url, --discord-url, or --output")
        print("See config/post_destinations.example.yaml for config format.")
        return 1

    # Summary
    print("\nPost results:")
    for name, ok in results:
        status = "OK" if ok else "FAILED"
        print(f"  {name}: {status}")

    return 0 if all(r[1] for r in results) else 1


if __name__ == "__main__":
    exit(main())
