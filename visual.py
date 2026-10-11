# -*- coding: utf-8 -*-
"""Laya 玩俄罗斯方块：实时可视化�?
左边：棋盘（落子下落动画 + 消行动画 + 幽灵投影）�?右边：当前块 / Next / 分数 / Laya 每次决策的选项、概率条、confidence、耗时�?全部英文标注（避�?pygame 中文字体问题）�?
操作：关窗口即退出；跑满 MAX_PIECES 或终局后自动截屏并退出�?"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pygame

from harness import ElTetrisClient, LayaClient, build_state, prune
from tetris import W, H, Tetris, rotations_of

CELL = 30
BOARD_PX = (20, 10)
PANEL_X = BOARD_PX[0] + W * CELL + 20
WIDTH = PANEL_X + 270
HEIGHT = H * CELL + 20
MAX_PIECES = 150
FPS = 60

BG = (12, 12, 24)
GRID = (32, 32, 48)
EMPTY_CELL = (22, 22, 34)
FLASH = (255, 255, 255)
COLORS = {
    "I": (0, 220, 220), "O": (240, 220, 0), "T": (170, 60, 250),
    "S": (60, 230, 60), "Z": (250, 70, 70), "J": (70, 120, 250),
    "L": (250, 160, 40),
}
SCORES = [0, 100, 300, 500, 800]


def draw_board(screen, board, flash_rows=()):
    ox, oy = BOARD_PX
    for y in range(H):
        for x in range(W):
            r = pygame.Rect(ox + x * CELL, oy + y * CELL, CELL, CELL)
            if board[y][x]:
                c = FLASH if y in flash_rows else (200, 200, 215)
                pygame.draw.rect(screen, c, r)
                pygame.draw.rect(screen, BG, r, 2)
            else:
                pygame.draw.rect(screen, EMPTY_CELL, r)
                pygame.draw.rect(screen, GRID, r, 1)


def draw_cells(screen, cells, base_x, base_y, color, ghost=False):
    ox, oy = BOARD_PX
    for dx, dy in cells:
        x, y = base_x + dx, base_y + dy
        if y < 0 or y >= H or x < 0 or x >= W:
            continue
        r = pygame.Rect(ox + x * CELL, oy + y * CELL, CELL, CELL)
        if ghost:
            pygame.draw.rect(screen, color, r, 2)
        else:
            pygame.draw.rect(screen, color, r)
            pygame.draw.rect(screen, BG, r, 2)


def draw_mini(screen, key, px, py, label, font):
    screen.blit(font.render(label, True, (180, 180, 200)), (px, py))
    cells = rotations_of(key)[0]
    minx = min(x for x, _ in cells)
    for dx, dy in cells:
        r = pygame.Rect(px + (dx - minx) * 20, py + 24 + dy * 20, 20, 20)
        pygame.draw.rect(screen, COLORS[key], r)
        pygame.draw.rect(screen, BG, r, 2)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", default="laya", choices=["laya", "eltetris"])
    ap.add_argument("--max-pieces", type=int, default=MAX_PIECES)
    args = ap.parse_args()

    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption(f"{args.client} plays Tetris")
    clock = pygame.time.Clock()
    font = pygame.font.Font(None, 24)
    big = pygame.font.Font(None, 32)

    if args.client == "eltetris":
        client = ElTetrisClient()
    else:
        client = LayaClient(model=r"tetris-laya\models\laya-typed-decisions")
    max_pieces = args.max_pieces
    game = Tetris(seed=7)
    cur, nxt = game._draw(), game._draw()
    score = 0

    phase, frame = "think", 0
    anim = {}
    flash_rows = []
    running = True
    while running:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False

        if phase == "think" and not game.over and game.pieces < max_pieces:
            placements = game.legal_placements(cur)
            if not placements:
                game.over = True
            else:
                cands = prune(placements, k=min(8, len(placements)))
                pid = client.decide(game, cur, nxt, cands)
                chosen = next(c for c in cands if c["pid"] == pid)
                cells = rotations_of(cur)[chosen["rot"]]
                anim = {"cells": cells, "x0": 3, "y0": -4,
                        "x1": chosen["x"], "y1": chosen["y"],
                        "chosen": chosen, "key": cur, "cands": cands}
                phase, frame = "fall", 0
        elif phase == "fall":
            frame += 1
            if frame >= 14:
                phase, frame = "lock", 0
        elif phase == "lock":
            frame += 1
            if frame >= 6:
                ch = anim["chosen"]
                # 手动锁定：先找满行做消行动画，再真正消除
                nb = [row[:] for row in game.board]
                for dx, dy in anim["cells"]:
                    if ch["y"] + dy >= 0:
                        nb[ch["y"] + dy][ch["x"] + dx] = 1
                flash_rows = [y for y in range(H) if all(nb[y])]
                cleared = len(flash_rows)
                game.board = nb
                game.pieces += 1
                game.lines += cleared
                score += SCORES[min(cleared, 4)]
                if any(ch["y"] + dy < 0 for _, dy in anim["cells"]):
                    game.over = True
                else:
                    cur, nxt = nxt, game._draw()
                phase, frame = ("flash", 0) if cleared else ("think", 0)
        elif phase == "flash":
            frame += 1
            if frame >= 12:
                kept = [row for i, row in enumerate(game.board) if i not in flash_rows]
                game.board = [[0] * W for _ in range(len(flash_rows))] + kept
                flash_rows = []
                phase, frame = "think", 0
        else:  # think 但已结束
            if game.over or game.pieces >= max_pieces:
                frame += 1
                if frame > 240:
                    running = False
            else:
                cur, nxt = nxt, game._draw()

        # ---- 绘制 ----
        screen.fill(BG)
        draw_board(screen, game.board, flash_rows)
        if phase in ("fall", "lock") and anim:
            t = min(frame / 14, 1.0) ** 2
            fx = anim["x0"] + (anim["x1"] - anim["x0"]) * min(frame / 6, 1.0)
            fy = anim["y0"] + (anim["y1"] - anim["y0"]) * t
            draw_cells(screen, anim["cells"], anim["x1"], anim["y1"],
                       COLORS[anim["key"]], ghost=True)
            draw_cells(screen, anim["cells"], round(fx), round(fy), COLORS[anim["key"]])

        # 右侧面板
        draw_mini(screen, cur, PANEL_X, 10, "CURRENT", font)
        draw_mini(screen, nxt, PANEL_X, 110, "NEXT", font)
        y = 200
        for label, val in [("PIECES", game.pieces), ("LINES", game.lines), ("SCORE", score)]:
            screen.blit(font.render(f"{label}: {val}", True, (220, 220, 235)), (PANEL_X, y))
            y += 28
        y += 6
        screen.blit(big.render(f"{args.client.upper()} DECISION", True, (120, 230, 170)), (PANEL_X, y))
        y += 32
        mv = client.last_move or {}
        probs = mv.get("probabilities", {}) if isinstance(mv, dict) else {}
        conf = mv.get("confidence", 0) if isinstance(mv, dict) else 0
        dec = getattr(client, "last_decision", None) or {}
        if dec.get("rule"):
            screen.blit(font.render(f"rule: {dec['rule']}", True, (120, 230, 170)), (PANEL_X, y))
            y += 26
        if isinstance(dec.get("danger"), float):
            screen.blit(font.render(f"danger {dec['danger']:.2f}", True, (250, 150, 120)), (PANEL_X, y))
            y += 26
        safety = dec.get("safety")
        if isinstance(safety, dict) and "score" in safety:
            screen.blit(font.render(f"safety {safety['score']:.1f}", True, (150, 200, 250)), (PANEL_X, y))
            y += 26
        if probs and anim.get("cands"):
            desc = {c["pid"]: f"{c['x']}/{c['rot']}" for c in anim["cands"]}
            for pid, p in sorted(probs.items(), key=lambda kv: -kv[1])[:4]:
                screen.blit(font.render(f"{pid}({desc.get(pid, '?')}) {p:.2f}",
                                        True, (200, 200, 215)), (PANEL_X, y))
                pygame.draw.rect(screen, (90, 200, 140),
                                 (PANEL_X, y + 22, int(220 * p), 8))
                y += 36
            screen.blit(font.render(f"conf {conf:.3f}  {client.last_ms:.0f}ms",
                                    True, (150, 150, 170)), (PANEL_X, y))
        else:
            screen.blit(font.render("thinking...", True, (150, 150, 170)), (PANEL_X, y))
        if game.over:
            screen.blit(big.render("GAME OVER", True, (250, 80, 80)), (PANEL_X, HEIGHT - 120))
        elif game.pieces >= max_pieces:
            screen.blit(big.render("DEMO DONE", True, (120, 230, 170)), (PANEL_X, HEIGHT - 120))

        pygame.display.flip()
        clock.tick(FPS)

    pygame.image.save(screen, str(Path(__file__).resolve().parent / "tetris_shot.png"))
    pygame.quit()
    print(f"pieces={game.pieces} lines={game.lines} score={score} over={game.over}")


if __name__ == "__main__":
    main()
