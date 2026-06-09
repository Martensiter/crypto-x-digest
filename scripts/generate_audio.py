#!/usr/bin/env python3
"""
Generate TTS audio briefing from a Twitter digest.

Converts a markdown digest into a spoken-word audio briefing suitable for
passive consumption — listen while commuting, exercising, or doing chores.

Pipeline:
  1. Parse markdown digest into sections
  2. Convert to spoken-word script (remove tables, links, formatting)
  3. Split into chunks (OpenAI TTS has 4096 char limit per request)
  4. Generate audio via OpenAI TTS API
  5. Concatenate chunks into a single MP3 file

Supported TTS providers:
  - openai: OpenAI TTS (tts-1, tts-1-hd) — best quality, requires OPENAI_API_KEY
  - voicevox: VOICEVOX (free, Japanese-focused) — for Japanese digests

Usage:
    # Basic usage
    python scripts/generate_audio.py digest.md --output briefing.mp3

    # High-quality mode
    python scripts/generate_audio.py digest.md --output briefing.mp3 --model tts-1-hd

    # Choose voice
    python scripts/generate_audio.py digest.md --output briefing.mp3 --voice onyx

    # Speed adjustment (0.25 to 4.0)
    python scripts/generate_audio.py digest.md --output briefing.mp3 --speed 1.1

    # Japanese spoken script for VOICEVOX
    python scripts/generate_audio.py digest.md --output briefing.mp3 --provider voicevox

Environment:
    OPENAI_API_KEY: Required for OpenAI TTS provider
"""

import argparse
import json
import os
import re
import ssl
import struct
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path


# ============================================================================
# MARKDOWN → SPOKEN SCRIPT CONVERSION
# ============================================================================

def markdown_to_spoken(md_text: str) -> str:
    """
    Convert markdown digest to a natural spoken-word script.

    Removes formatting artifacts and converts structure into speech patterns.
    """
    text = md_text

    # Remove horizontal rules
    text = re.sub(r"^---+\s*$", "", text, flags=re.MULTILINE)

    # Convert headers to spoken transitions
    text = re.sub(r"^#{1}\s+(.+)$", r"\n\n\1.\n", text, flags=re.MULTILINE)
    text = re.sub(r"^#{2}\s+(.+)$", r"\nNext topic: \1.\n", text, flags=re.MULTILINE)
    text = re.sub(r"^#{3}\s+(.+)$", r"\n\1.\n", text, flags=re.MULTILINE)
    text = re.sub(r"^#{4,}\s+(.+)$", r"\1.", text, flags=re.MULTILINE)

    # Remove markdown links but keep text: [text](url) → text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # Remove standalone URLs
    text = re.sub(r"https?://\S+", "", text)

    # Remove markdown bold/italic
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)

    # Remove markdown tables — convert to spoken list
    lines = text.split("\n")
    spoken_lines = []
    in_table = False

    for line in lines:
        stripped = line.strip()

        # Detect table separator
        if re.match(r"^\|[-:\s|]+\|$", stripped):
            in_table = True
            continue

        # Table row
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if in_table and cells:
                # Skip header-only rows
                spoken = ", ".join(c for c in cells if c and not re.match(r"^[-:]+$", c))
                if spoken:
                    spoken_lines.append(f"  {spoken}.")
            continue

        in_table = False

        # Convert bullet points to spoken form
        if re.match(r"^\s*[-*]\s+", stripped):
            content = re.sub(r"^\s*[-*]\s+", "", stripped)
            spoken_lines.append(f"  {content}")
            continue

        # Convert numbered lists
        if re.match(r"^\s*\d+\.\s+", stripped):
            content = re.sub(r"^\s*\d+\.\s+", "", stripped)
            spoken_lines.append(f"  {content}")
            continue

        spoken_lines.append(line)

    text = "\n".join(spoken_lines)

    # Remove code blocks
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)

    # Remove emojis and special characters that TTS handles poorly
    emoji_pattern = re.compile(
        "[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U0001F900-\U0001F9FF"
        "\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF"
        "\U00002600-\U000026FF\U0001F004-\U0001F0CF]+", re.UNICODE
    )
    text = emoji_pattern.sub("", text)

    # Normalize @mentions for speech
    text = re.sub(r"@(\w+)", r"\1", text)

    # Convert common abbreviations for clearer speech
    text = re.sub(r"\bK\b(?=\s*likes)", "thousand", text)
    text = re.sub(r"\b(\d+)K\b", r"\1 thousand", text)
    text = re.sub(r"\b(\d+)M\b", r"\1 million", text)
    text = re.sub(r"\b(\d+)B\b", r"\1 billion", text)
    text = re.sub(r"\bGPT-(\d)", r"GPT \1", text)

    # Clean up excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"  +", " ", text)

    # Add intro and outro
    intro = "Here's your timeline briefing.\n\n"
    outro = "\n\nThat's your briefing for today."

    text = intro + text.strip() + outro

    return text


# ============================================================================
# TEXT CHUNKING
# ============================================================================

def split_into_chunks(text: str, max_chars: int = 4000) -> list:
    """
    Split text into chunks respecting sentence boundaries.

    OpenAI TTS has a 4096 character limit per request.
    We use 4000 to leave margin.
    """
    if len(text) <= max_chars:
        return [text]

    chunks = []
    current_chunk = ""

    # Split by paragraphs first
    paragraphs = text.split("\n\n")

    for para in paragraphs:
        # If a single paragraph is too long, split by sentences
        if len(para) > max_chars:
            sentences = re.split(r"(?<=[.!?])\s+", para)
            for sentence in sentences:
                if len(current_chunk) + len(sentence) + 1 > max_chars:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = sentence
                else:
                    current_chunk += " " + sentence if current_chunk else sentence
        else:
            if len(current_chunk) + len(para) + 2 > max_chars:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = para
            else:
                current_chunk += "\n\n" + para if current_chunk else para

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks


# ============================================================================
# OPENAI TTS
# ============================================================================

def generate_openai_tts(text: str, output_path: str, voice: str = "nova",
                        model: str = "tts-1", speed: float = 1.0) -> bool:
    """
    Generate audio using OpenAI TTS API.

    Voices: alloy, echo, fable, onyx, nova, shimmer
    Models: tts-1 (fast, lower quality), tts-1-hd (higher quality, slower)
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY environment variable not set")
        return False

    chunks = split_into_chunks(text)
    print(f"Split into {len(chunks)} chunks for TTS processing")

    temp_files = []

    try:
        for i, chunk in enumerate(chunks):
            print(f"  Generating audio chunk {i+1}/{len(chunks)} ({len(chunk)} chars)...")

            payload = {
                "model": model,
                "input": chunk,
                "voice": voice,
                "speed": speed,
                "response_format": "mp3",
            }

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            req = urllib.request.Request(
                "https://api.openai.com/v1/audio/speech",
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )

            ctx = ssl.create_default_context()

            try:
                with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
                    audio_data = resp.read()

                temp_path = os.path.join(
                    tempfile.gettempdir(), f"tts_chunk_{i:03d}.mp3"
                )
                with open(temp_path, "wb") as f:
                    f.write(audio_data)
                temp_files.append(temp_path)

            except urllib.error.HTTPError as e:
                body = e.read().decode()[:500]
                print(f"  OpenAI TTS error: {e.code} {body}")
                return False

            # Rate limiting: small delay between chunks
            if i < len(chunks) - 1:
                time.sleep(0.5)

        # Concatenate MP3 chunks
        if len(temp_files) == 1:
            # Single chunk — just copy
            with open(temp_files[0], "rb") as src, open(output_path, "wb") as dst:
                dst.write(src.read())
        else:
            concatenate_mp3(temp_files, output_path)

        file_size = os.path.getsize(output_path)
        print(f"Audio saved: {output_path} ({file_size / 1024:.1f} KB)")
        return True

    finally:
        # Clean up temp files
        for f in temp_files:
            try:
                os.unlink(f)
            except OSError:
                pass


def concatenate_mp3(input_files: list, output_path: str):
    """
    Concatenate MP3 files by raw byte concatenation.

    MP3 is frame-based, so simple concatenation works for same-bitrate files.
    OpenAI TTS outputs consistent bitrate, so this is reliable.
    """
    with open(output_path, "wb") as out:
        for input_file in input_files:
            with open(input_file, "rb") as inp:
                out.write(inp.read())


# ============================================================================
# VOICEVOX TTS (Japanese)
# ============================================================================

def generate_voicevox_tts(text: str, output_path: str,
                          speaker_id: int = 3, host: str = "http://localhost:50021") -> bool:
    """
    Generate audio using VOICEVOX (local, free, Japanese-focused).

    Requires VOICEVOX engine running locally.
    Speaker IDs: 0=四国めたん, 1=ずんだもん, 3=ずんだもん(ノーマル)
    """
    chunks = split_into_chunks(text, max_chars=500)  # VOICEVOX prefers shorter
    print(f"Split into {len(chunks)} chunks for VOICEVOX")

    temp_files = []

    try:
        for i, chunk in enumerate(chunks):
            print(f"  Generating chunk {i+1}/{len(chunks)}...")

            # Step 1: Generate audio query
            query_url = f"{host}/audio_query?text={urllib.parse.quote(chunk)}&speaker={speaker_id}"
            req = urllib.request.Request(query_url, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    query = json.loads(resp.read().decode())
            except Exception as e:
                print(f"  VOICEVOX audio_query error: {e}")
                print("  Is VOICEVOX engine running? Start with: voicevox --host 0.0.0.0 --port 50021")
                return False

            # Step 2: Synthesize
            synth_url = f"{host}/synthesis?speaker={speaker_id}"
            req = urllib.request.Request(
                synth_url,
                data=json.dumps(query).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    wav_data = resp.read()

                temp_path = os.path.join(
                    tempfile.gettempdir(), f"voicevox_chunk_{i:03d}.wav"
                )
                with open(temp_path, "wb") as f:
                    f.write(wav_data)
                temp_files.append(temp_path)

            except Exception as e:
                print(f"  VOICEVOX synthesis error: {e}")
                return False

        # Concatenate WAV files
        concatenate_wav(temp_files, output_path)

        file_size = os.path.getsize(output_path)
        print(f"Audio saved: {output_path} ({file_size / 1024:.1f} KB)")
        return True

    finally:
        for f in temp_files:
            try:
                os.unlink(f)
            except OSError:
                pass


def concatenate_wav(input_files: list, output_path: str):
    """Concatenate WAV files (same format) into a single WAV."""
    if not input_files:
        return

    if len(input_files) == 1:
        with open(input_files[0], "rb") as src, open(output_path, "wb") as dst:
            dst.write(src.read())
        return

    # Read first file to get format
    with open(input_files[0], "rb") as f:
        header = f.read(44)  # Standard WAV header is 44 bytes
        first_data = f.read()

    all_data = bytearray(first_data)

    for input_file in input_files[1:]:
        with open(input_file, "rb") as f:
            f.read(44)  # Skip header
            all_data.extend(f.read())

    # Write combined WAV
    with open(output_path, "wb") as f:
        # Update header with new data size
        total_size = len(all_data)
        new_header = bytearray(header)
        struct.pack_into("<I", new_header, 4, total_size + 36)  # File size - 8
        struct.pack_into("<I", new_header, 40, total_size)  # Data size
        f.write(new_header)
        f.write(all_data)


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Generate TTS audio briefing from Twitter digest"
    )
    parser.add_argument("input", help="Digest markdown file")
    parser.add_argument("--output", "-o", default=None, help="Output audio file")
    parser.add_argument(
        "--provider", choices=["openai", "voicevox"], default="openai",
        help="TTS provider (default: openai)"
    )
    parser.add_argument(
        "--voice", default="nova",
        help="Voice name (openai: alloy/echo/fable/onyx/nova/shimmer)"
    )
    parser.add_argument(
        "--model", default="tts-1",
        help="TTS model (openai: tts-1/tts-1-hd)"
    )
    parser.add_argument(
        "--speed", type=float, default=1.0,
        help="Speech speed (0.25-4.0, default: 1.0)"
    )
    parser.add_argument(
        "--speaker-id", type=int, default=3,
        help="VOICEVOX speaker ID (default: 3 = ずんだもん)"
    )
    parser.add_argument(
        "--script-only", action="store_true",
        help="Only output the spoken script (no audio generation)"
    )
    args = parser.parse_args()

    # Read digest
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: File not found: {input_path}")
        return 1

    with open(input_path, encoding="utf-8") as f:
        md_text = f.read()

    print(f"Read digest: {input_path} ({len(md_text)} chars)")

    # Convert to spoken script
    spoken = markdown_to_spoken(md_text)
    print(f"Spoken script: {len(spoken)} chars")

    # Script-only mode
    if args.script_only:
        script_path = input_path.with_suffix(".spoken.txt")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(spoken)
        print(f"Saved spoken script to: {script_path}")
        return 0

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        ext = ".wav" if args.provider == "voicevox" else ".mp3"
        output_path = str(input_path.with_suffix(ext))

    # Ensure output directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Generate audio
    if args.provider == "openai":
        ok = generate_openai_tts(
            spoken, output_path,
            voice=args.voice, model=args.model, speed=args.speed
        )
    elif args.provider == "voicevox":
        ok = generate_voicevox_tts(
            spoken, output_path,
            speaker_id=args.speaker_id
        )
    else:
        print(f"Unknown provider: {args.provider}")
        return 1

    if not ok:
        print("Audio generation failed")
        return 1

    # Also save the spoken script for reference
    script_path = Path(output_path).with_suffix(".spoken.txt")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(spoken)
    print(f"Spoken script saved to: {script_path}")

    return 0


if __name__ == "__main__":
    exit(main())
