"""End-to-end test: local chunk → OSS → Fun-ASR → transcript text file."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "backend" / "src"))

from video_split.config import set_config_path, get_settings
set_config_path(str(Path(__file__).parent / "config" / "app.yaml"))

from video_split.service.transcriber import transcribe_single_chunk, _is_dashscope_funasr

settings = get_settings()
print(f"Model:     {settings.transcription.model}")
print(f"Base URL:  {settings.transcription.base_url}")
print(f"Fun-ASR:   {_is_dashscope_funasr()}")
print(f"OSS:       {'enabled' if settings.oss.enabled else 'NOT configured'}")
print()

test_file = Path("data/tmp/3/chunks/chunk_000.mp3")
if not test_file.exists():
    print(f"Test file not found: {test_file}")
    sys.exit(1)
print(f"File:      {test_file} ({test_file.stat().st_size / 1024:.0f} KB)")

if _is_dashscope_funasr() and not settings.oss.enabled:
    print("\nERROR: Fun-ASR requires OSS config. Add oss.* to config/app.yaml")
    sys.exit(1)


async def main():
    print("\nTranscribing...")
    entries = await transcribe_single_chunk(test_file)
    print(f"\nGot {len(entries)} sentences:\n")

    output = Path("test_transcription_result.txt")
    lines = []
    for e in entries:
        end = e.start + e.duration
        line = f"[{int(e.start)//60:02d}:{int(e.start)%60:02d}.{int(e.start*10)%10} - {int(end)//60:02d}:{int(end)%60:02d}.{int(end*10)%10}] {e.text}"
        lines.append(line)
        print(line)

    output.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nSaved to {output}")


asyncio.run(main())
