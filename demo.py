# -*- coding: utf-8 -*-
"""无头试跑：Stub 验证循环 + 打印一次真实 state 样例（含长度，核对 token 预算）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import StubClient, build_state, play, prune
from tetris import Tetris

if __name__ == "__main__":
    game = play(StubClient(), max_pieces=150, seed=7, verbose=False)
    print(f"pieces={game.pieces} lines={game.lines} over={game.over}")

    # 打印一次中期 state 样例，检查喂给模型的文本形态与长度
    g2 = Tetris(seed=7)
    cur = g2._draw()
    cands = prune(g2.legal_placements(cur))
    sample = build_state(g2, cur, g2._draw(), cands)
    print(f"--- sample state ({len(sample)} chars, {len(cands)} candidates) ---")
    print(sample)
    print("--- final board ---")
    print(game.render())
