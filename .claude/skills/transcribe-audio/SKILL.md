---
name: transcribe-audio
description: Transcribe speech to text from audio or video files (mp3, wav, m4a, ogg, opus, flac, webm, mp4, mov, voice notes, recordings, podcasts, meetings, lectures) using local Whisper models. Use this whenever the user wants to know what is said in an audio/video file, asks to "listen to", transcribe, caption, subtitle (SRT/VTT), summarize, or translate a recording, even if they don't say "transcribe" — Claude can't hear audio directly, so this skill is how it understands speech.
---

# Transcribe audio

Claude can't hear audio, but it can run Whisper (via `faster-whisper`, on CPU)
to turn speech into text, and then work with that text.

## Quick start

```bash
python .claude/skills/transcribe-audio/scripts/transcribe.py path/to/file.mp3
```

The script installs `faster-whisper` on first use, downloads the model
(cached afterwards), prints the transcript, and writes `file.txt` next to the
input. Video files work too; audio is extracted automatically.

## Options

| Flag | Use |
|---|---|
| `--model` | `tiny`, `base` (default), `small`, `medium`, `large-v3`, `distil-large-v3` |
| `--language en` | Skip auto-detection; helps with short clips or accents |
| `--format srt\|vtt\|json\|all` | Subtitles, or JSON with per-segment timestamps |
| `--timestamps` | Timestamps in the `.txt` output |
| `--translate` | Translate non-English speech to English |
| `--out-dir DIR` | Write outputs somewhere other than beside the input |
| `--no-vad` | Turn off silence filtering if quiet speech is being dropped |

Several files can be passed at once; the model loads once for all of them.

## Choosing a model

Everything runs on CPU, so bigger models are slower. Rough guide:

- `base`: good default, about real-time or faster on CPU.
- `small`: noticeably more accurate; use for accented speech, noisy audio, or
  non-English languages.
- `medium` / `large-v3`: best accuracy but slow (can take several times the
  audio length). Use when accuracy matters and the clip is short, or ask the
  user first for long recordings.
- `tiny`: only for quick checks.

For long recordings (over ~30 minutes), run the command in the background so
the session isn't blocked, and tell the user roughly how long it may take.

## After transcribing

Do what the user actually wanted with the text: answer their question,
summarize, extract action items, etc. Point them to the saved transcript file.
Whisper can mishear names, jargon, and numbers, so mention when a word looks
uncertain instead of presenting it as fact. If nothing was transcribed, the
file may be silent or music-only; say so.

## Troubleshooting

- **Transcript is gibberish or in the wrong language**: auto-detection can
  misfire, especially with accents or background noise (e.g. English detected
  as Malayalam). Rerun with `--language en` (or the right code) and `--model small`.
- **Need exact word timings** (e.g. to sync on-screen text): use
  `word_timestamps=True` in faster-whisper. If words bunch up at one time,
  re-transcribe short windows around that spot to pin them down.
- **`403 Forbidden ... no rule allows this host`** while downloading a model:
  the environment's network policy blocks Hugging Face. The model files need
  `huggingface.co` plus its CDN (`us.aws.cdn.hf.co`; other regions use a
  different prefix, so check the redirect with
  `curl -sI https://huggingface.co/Systran/faster-whisper-base/resolve/main/model.bin`).
  Ask to have those hosts added to the environment.
- **Can't decode the file**: convert it first with
  `ffmpeg -i input.xyz -ar 16000 -ac 1 out.wav` and transcribe `out.wav`.
