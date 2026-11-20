#!/usr/bin/env python3
"""Transcribe audio/video files to text with faster-whisper (local Whisper models).

Usage:
    python transcribe.py INPUT [INPUT ...] [--model base] [--language en]
                         [--format txt|srt|vtt|json|all] [--out-dir DIR]
                         [--translate] [--timestamps]

Writes one output file per input (next to the input, or in --out-dir) and
prints the plain transcript to stdout. Installs faster-whisper on first use.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Plain HTTPS downloads work through the session proxy more reliably than the
# Xet protocol, which needs extra hosts.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")


def ensure_faster_whisper():
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        print("Installing faster-whisper (one-time)...", file=sys.stderr)
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", "faster-whisper"]
        )


def load_audio(path: Path):
    """Decode any audio/video file to 16 kHz mono float32 with ffmpeg.

    faster-whisper's built-in decoder (PyAV) breaks on newer PyAV releases,
    and ffmpeg handles more formats anyway. Falls back to the path if ffmpeg
    is missing.
    """
    import shutil

    if not shutil.which("ffmpeg"):
        return str(path)
    import numpy as np

    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path),
         "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode(errors="replace").strip() or "ffmpeg failed")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def ts(seconds: float, sep: str) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def render(fmt: str, segments, info, timestamps: bool) -> str:
    if fmt == "txt":
        if timestamps:
            return "\n".join(
                f"[{ts(s['start'], '.')[:-4]} -> {ts(s['end'], '.')[:-4]}] {s['text']}"
                for s in segments
            ) + "\n"
        return " ".join(s["text"] for s in segments).strip() + "\n"
    if fmt == "srt":
        return "".join(
            f"{i}\n{ts(s['start'], ',')} --> {ts(s['end'], ',')}\n{s['text']}\n\n"
            for i, s in enumerate(segments, 1)
        )
    if fmt == "vtt":
        return "WEBVTT\n\n" + "".join(
            f"{ts(s['start'], '.')} --> {ts(s['end'], '.')}\n{s['text']}\n\n"
            for s in segments
        )
    if fmt == "json":
        return json.dumps(
            {
                "language": info.language,
                "language_probability": round(info.language_probability, 3),
                "duration": round(info.duration, 2),
                "segments": segments,
            },
            indent=2,
            ensure_ascii=False,
        )
    raise ValueError(fmt)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("inputs", nargs="+", help="audio or video files")
    p.add_argument(
        "--model",
        default="base",
        help="tiny | base | small | medium | large-v3 | distil-large-v3 (default: base)",
    )
    p.add_argument("--language", default=None, help="e.g. en, es, ur; auto-detect if omitted")
    p.add_argument("--format", default="txt", choices=["txt", "srt", "vtt", "json", "all"])
    p.add_argument("--out-dir", default=None, help="where to write outputs (default: next to input)")
    p.add_argument("--translate", action="store_true", help="translate speech to English")
    p.add_argument("--timestamps", action="store_true", help="add timestamps to txt output")
    p.add_argument("--no-vad", action="store_true", help="disable silence filtering")
    args = p.parse_args()

    ensure_faster_whisper()
    from faster_whisper import WhisperModel

    print(f"Loading model '{args.model}'...", file=sys.stderr)
    model = WhisperModel(args.model, device="cpu", compute_type="int8")

    formats = ["txt", "srt", "vtt", "json"] if args.format == "all" else [args.format]
    failed = 0
    for inp in args.inputs:
        path = Path(inp)
        if not path.exists():
            print(f"ERROR: {inp} not found", file=sys.stderr)
            failed += 1
            continue
        t0 = time.time()
        try:
            seg_iter, info = model.transcribe(
                load_audio(path),
                language=args.language,
                task="translate" if args.translate else "transcribe",
                vad_filter=not args.no_vad,
                beam_size=5,
            )
            segments = [
                {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
                for s in seg_iter
            ]
        except Exception as e:  # unreadable/corrupt file, etc.
            print(f"ERROR transcribing {inp}: {e}", file=sys.stderr)
            failed += 1
            continue

        out_dir = Path(args.out_dir) if args.out_dir else path.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for fmt in formats:
            out = out_dir / f"{path.stem}.{fmt}"
            out.write_text(render(fmt, segments, info, args.timestamps), encoding="utf-8")
            written.append(str(out))

        print(
            f"[{path.name}] language={info.language} ({info.language_probability:.0%}), "
            f"audio={info.duration:.1f}s, took={time.time() - t0:.1f}s -> {', '.join(written)}",
            file=sys.stderr,
        )
        if not segments:
            print(f"[{path.name}] (no speech detected)", file=sys.stderr)
        print(f"=== {path.name} ===")
        print(render("txt", segments, info, args.timestamps))

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
