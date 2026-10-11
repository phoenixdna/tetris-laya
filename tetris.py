# -*- coding: utf-8 -*-
"""极简无头俄罗斯方块引擎：专为 AI 落子规划设计。

约定：棋盘 H(20)行 x W(10)列，row 0 为顶部。方块可短暂位于 y<0（顶部上方），
锁定后若有格子 y<0 则判 block out。AI 采用“ placements 规划”模式：
每块直接选定最终落位（旋转+列），相当于 guideline 的 hard drop 规划。

模块级纯函数（cells_collide / lock_on / compute_placements）不碰实例状态，
可在后台线程调用；Tetris 类方法只是对它们的委托。
"""
from __future__ import annotations

import random

W, H = 10, 20

BASE_SHAPES = {
    "I": ((0, 1), (1, 1), (2, 1), (3, 1)),
    "O": ((1, 0), (2, 0), (1, 1), (2, 1)),
    "T": ((1, 0), (0, 1), (1, 1), (2, 1)),
    "S": ((1, 0), (2, 0), (0, 1), (1, 1)),
    "Z": ((0, 0), (1, 0), (1, 1), (2, 1)),
    "J": ((0, 0), (0, 1), (1, 1), (2, 1)),
    "L": ((2, 0), (0, 1), (1, 1), (2, 1)),
}


def _normalize(cells):
    xs = [x for x, _ in cells]
    ys = [y for _, y in cells]
    return frozenset((x - min(xs), y - min(ys)) for x, y in cells)


def rotations_of(key):
    """返回去重后的旋转集合（O 块只有 1 种，S/Z/I 2 种，其余 4 种）。"""
    cells = frozenset(BASE_SHAPES[key])
    out, seen = [], set()
    for _ in range(4):
        norm = _normalize(cells)
        if norm not in seen:
            seen.add(norm)
            out.append(norm)
        cells = frozenset((y, -x) for x, y in cells)
    return out


def cells_collide(board, cells, ox, oy):
    for dx, dy in cells:
        x, y = ox + dx, oy + dy
        if x < 0 or x >= W or y >= H:
            return True
        if y >= 0 and board[y][x]:
            return True
    return False


def lock_on(board, cells, ox, oy):
    """把方块写入 board 副本；返回 (新棋盘, 消行数, 是否冲顶)。"""
    nb = [row[:] for row in board]
    topped = False
    for dx, dy in cells:
        x, y = ox + dx, oy + dy
        if y < 0:
            topped = True
            continue
        nb[y][x] = 1
    kept = [row for row in nb if not all(row)]
    cleared = H - len(kept)
    return [[0] * W for _ in range(cleared)] + kept, cleared, topped


def board_features(board):
    """确定性棋盘特征：给 stub 评分和 Laya 候选项描述共用。"""
    heights, holes = [], 0
    for x in range(W):
        h = 0
        for y in range(H):
            if board[y][x]:
                h = H - y
                break
        heights.append(h)
        seen_block = False
        for y in range(H):
            if board[y][x]:
                seen_block = True
            elif seen_block:
                holes += 1
    bump = sum(abs(heights[i] - heights[i + 1]) for i in range(W - 1))
    return {
        "heights": heights,
        "agg_height": sum(heights),
        "max_height": max(heights),
        "holes": holes,
        "bumpiness": bump,
    }


# El-Tetris 经典权重（stub 用，分数越高越好）
W_AGG, W_HOLE, W_BUMP, W_LINE = -0.510066, -0.35663, -0.184483, 0.760666


def heuristic_score(feat, lines):
    return W_AGG * feat["agg_height"] + W_HOLE * feat["holes"] + W_BUMP * feat["bumpiness"] + W_LINE * lines


def compute_placements(board, key):
    """纯函数版合法落位枚举（不碰实例状态，可在后台线程调用）。
    每个元素含 rot/x/y/board/lines/feat/hscore。"""
    out, seen = [], set()
    for ri, cells in enumerate(rotations_of(key)):
        for x in range(-3, W):
            y = -4
            if cells_collide(board, cells, x, y):
                continue
            while not cells_collide(board, cells, x, y + 1):
                y += 1
            nb, cleared, topped = lock_on(board, cells, x, y)
            if topped:
                continue
            sig = tuple(tuple(r) for r in nb)
            if sig in seen:
                continue
            seen.add(sig)
            feat = board_features(nb)
            out.append({
                "rot": ri, "x": x, "y": y,
                "board": nb, "lines": cleared, "feat": feat,
                "hscore": heuristic_score(feat, cleared),
            })
    return out


class Tetris:
    def __init__(self, seed=0):
        self.board = [[0] * W for _ in range(H)]
        self.rng = random.Random(seed)
        self.bag = []
        self.pieces = 0
        self.lines = 0
        self.over = False

    def _draw(self):
        if not self.bag:
            self.bag = list(BASE_SHAPES)
            self.rng.shuffle(self.bag)
        return self.bag.pop()

    def _collides(self, cells, ox, oy):
        return cells_collide(self.board, cells, ox, oy)

    @staticmethod
    def _lock_on(board, cells, ox, oy):
        return lock_on(board, cells, ox, oy)

    def legal_placements(self, key):
        """枚举当前块所有合法落位；按结果棋盘去重。"""
        return compute_placements(self.board, key)

    def step(self, key, placement):
        """执行一次落子；返回消行数。无合法落位则结束游戏。"""
        if self.over:
            return 0
        nb, cleared, topped = self._lock_on(
            self.board, rotations_of(key)[placement["rot"]], placement["x"], placement["y"])
        self.board = nb
        self.pieces += 1
        self.lines += cleared
        if topped:
            self.over = True
        return cleared

    def render(self):
        return "\n".join("".join("#" if c else "." for c in row) for row in self.board)
