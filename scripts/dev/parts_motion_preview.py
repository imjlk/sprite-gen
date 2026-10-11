# SPDX-License-Identifier: Apache-2.0
"""Compare actual layered evidence at its own cycle length, on one 24fps clock.

The videos are review artifacts, never appearance-specific runtime assets.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

from PIL import Image, ImageDraw
from sprite_gen.video.repair import measure_jolt


def preview(old, new, out):
    if out.exists():
        raise ValueError('preserve the previous review artifact')
    out.mkdir(parents=True)
    metrics = {}
    ffmpeg = shutil.which('ffmpeg') or '/opt/homebrew/bin/ffmpeg'
    for state in ('walk', 'run'):
        groups = [[Image.open(p).convert('RGBA') for p in sorted((folder/'frames').glob(state+'.*.png'))]
                  for folder in (old, new)]
        assert all(groups)
        metrics[state] = {label: {'frames': len(frames), 'jolt': measure_jolt(frames)}
                          for label, frames in zip(('before', 'after'), groups)}
        command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'rawvideo',
                   '-pix_fmt', 'rgb24', '-s', '960x520', '-r', '24', '-i', '-',
                   '-an', '-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p',
                   '-movflags', '+faststart', str(out/(state+'-comparison.mp4'))]
        with subprocess.Popen(command, stdin=subprocess.PIPE) as process:
            for tick in range(24*6):
                board = Image.new('RGB', (960, 520), (235, 235, 235))
                draw = ImageDraw.Draw(board)
                for column, (label, frames) in enumerate(zip(('BEFORE', 'AFTER'), groups)):
                    n = tick % len(frames)
                    frame = frames[n].resize((480, 480), Image.Resampling.LANCZOS)
                    board.paste(frame, (column*480, 40), frame)
                    draw.text((column*480+12, 12), f'{state} {label} | {n:02}/{len(frames)} | 24fps', fill='black')
                process.stdin.write(board.tobytes())
            process.stdin.close()
            assert process.wait() == 0
    result = {'fps': 24, 'seconds': 6, 'metrics': metrics,
              'runtime_assets_generated': 0, 'art_approved': False,
              'scope': 'actual composition preview and alpha continuity, not gait or art approval'}
    (out/'report.json').write_text(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('old', 'new', 'out'):
        parser.add_argument('--'+name, type=Path, required=True)
    print(json.dumps(preview(**vars(parser.parse_args()))))
