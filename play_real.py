# -*- coding: utf-8 -*-
"""真 Laya 试跑：加载 typed-decisions（1024 ctx）并实下 12 步，计时。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import LayaClient, play

t0 = time.time()
client = LayaClient(model=r"tetris-laya\models\laya-typed-decisions")
print(f"model loaded in {time.time() - t0:.1f}s")

t1 = time.time()
game = play(client, max_pieces=12, seed=7, verbose=True)
print(f"12 moves in {time.time() - t1:.1f}s "
      f"({(time.time() - t1) / 12:.2f}s/move)")
print(f"pieces={game.pieces} lines={game.lines} over={game.over}")
print(game.render())
