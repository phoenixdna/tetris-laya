# -*- coding: utf-8 -*-
"""Laya/ElTetris vs YOU：左右对战。

左边 AI，右边人类（键盘）。一方死亡即结束，判胜负 + 截屏。
公平性：双方同一发牌序列、同一重力；人类独享 soft/hard drop 主动权，
AI 独享完美落位规划。

AI 行为（按用户要求）：平时与人类同速下落、边落边转向；
只在“拿定主意”（choice 首选项概率够高）时偶尔 hard drop。
失控块：任一边约每 15–20 块随机刷出一块红色失控块，直线 3 倍速下落，
期间不接受任何控制（AI 不转向、人类按键无效），NEXT 会提前红色预警。
垃圾对轰：任一方消 n 行，对手棋盘底部被推上 n 行带随机空洞的垃圾块
（对手若有下落块则整体上移一格保持相对位置；顶部方块被挤出则判负）。
托管按钮：PLAYING 中点 "LAYA HOST"，人这边交给第二个 Laya 实例托管，
棋盘标签与窗口标题变为 LAYA-1（模型在点击后后台加载）；再点 "TAKE OVER" 交还键盘。
  python tetris-laya/versus.py --ai laya        # 左边真 Laya
  python tetris-laya/versus.py --ai eltetris    # 左边纯代码
  python tetris-laya/versus.py --human bot      # 右边开局即托管（冒烟测试用）

人类操作（默认，可改 tetris-laya/keys.json）：←→ 移动，↓ 加速，空格/X 旋转，
Z 逆时针，上键 hard drop，P 暂停。开始菜单点 START GAME 或回车。
"""
import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pygame

from harness import ElTetrisClient, LayaClient, prune
from tetris import W, H, Tetris, compute_placements, rotations_of, BASE_SHAPES

CELL = 26
BW, BH = W * CELL, H * CELL
COL_AI = 10                  # 左栏：LAYA 决策细节 + 战绩
AI_X = COL_AI + 250          # AI 棋盘
MID_X = AI_X + BW + 15       # 中栏：共用信息 + 按钮 + 胜负
HUMAN_X = MID_X + 200 + 15   # 人类棋盘
COL_YOU = HUMAN_X + BW + 15  # 右栏：你的战绩 + 操作
TITLE_H = 44
TOP = 10 + TITLE_H
WIDTH = COL_YOU + 240
HEIGHT = TOP + BH + 30
FPS = 60
GRAVITY0 = 24          # 双方基础重力（帧/格），随总消行加速
AI_STEER_EVERY = 4     # AI 每 4 帧调整一次（类人反应）
HARD_PROB_MIN = 0.70   # Laya 首选项概率超过此值 -> hard drop（偶尔加速）
ROGUE_COLOR = (255, 95, 95)  # 失控块配色
ROGUE_P = 1 / 17       # 每边每次发块掷出失控块的概率（约 15–20 块一次，可用 --rogue-p 调）
KICKS = [(0, 0), (-1, 0), (1, 0), (0, -1), (-2, 0), (2, 0)]
MODEL_DIR = str(Path(__file__).resolve().parent / "models" / "laya-typed-decisions")
GARBAGE_RNG = random.Random(0xA11CE)  # 垃圾行专用：不动发牌/失控块的随机序列
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

# AI 思考线程池：decide 在后台跑，主循环（人类操作/渲染）永远不卡
# 3 个 worker：左思考 + 右思考/托管模型加载
_EXEC = ThreadPoolExecutor(max_workers=3)


# ---------------- 按键配置（可设置） ----------------
# 改法：直接编辑 tetris-laya/keys.json（首次运行自动生成），键名用 pygame 键名
# 如 "space" "up" "left" "z" "x" "p" "return"。每个动作可绑多个键。
KEYS_FILE = Path(__file__).resolve().parent / "keys.json"
DEFAULT_KEYS = {
    "left": ["left"],
    "right": ["right"],
    "soft_drop": ["down"],
    "rotate": ["space", "x"],
    "rotate_ccw": ["z"],
    "hard_drop": ["up"],
    "pause": ["p"],
    "start": ["return", "space"],
}
KEY_NAMES = {}  # 动作 -> [键名]（界面显示用）


def ensure_keys_file():
    if not KEYS_FILE.exists():
        KEYS_FILE.write_text(json.dumps(DEFAULT_KEYS, ensure_ascii=False, indent=2),
                             encoding="utf-8")


def load_keymap():
    merged = dict(DEFAULT_KEYS)
    try:
        user = json.loads(KEYS_FILE.read_text(encoding="utf-8"))
        for k, v in user.items():
            if k in merged and isinstance(v, list) and v:
                merged[k] = v
    except Exception:
        pass
    KEY_NAMES.update({k: list(v) for k, v in merged.items()})
    out = {}
    for k, names in merged.items():
        codes = set()
        for n in names:
            try:
                codes.add(pygame.key.key_code(n))
            except Exception:
                pass
        out[k] = codes
    return out


class Button:
    def __init__(self, x, y, w, h, label):
        self.rect = pygame.Rect(x, y, w, h)
        self.label = label

    def draw(self, screen, font, mouse):
        hover = self.rect.collidepoint(mouse)
        c = (70, 140, 105) if hover else (42, 84, 64)
        pygame.draw.rect(screen, c, self.rect, border_radius=8)
        pygame.draw.rect(screen, (120, 230, 170), self.rect, 2, border_radius=8)
        t = font.render(self.label, True, (235, 235, 240))
        screen.blit(t, (self.rect.centerx - t.get_width() // 2,
                        self.rect.centery - t.get_height() // 2))


class Dealer:
    """双方同一发牌序列，保证公平。"""

    def __init__(self, seed=7):
        self.rng = random.Random(seed)
        self.bag = []

    def next(self):
        if not self.bag:
            self.bag = list(BASE_SHAPES)
            self.rng.shuffle(self.bag)
        return self.bag.pop()


class Side:
    def __init__(self, name):
        self.name = name
        self.game = Tetris(seed=0)
        self.key = self.nxt = None
        self.rots = []
        self.rot = self.x = self.y = 0
        self.has_piece = False
        self.fall_t = 0
        self.steer_t = 0
        self.score = 0
        self.flash = []
        self.flash_t = 0
        self.plan = None      # AI/托管：{"tx","trot","ty","hard","hard_at"}
        self.over = False
        self._future = None   # 后台思考任务 (gen, Future)
        self._gen = 0         # 落子代际：过期计划直接丢弃
        self.rogue = False    # 失控块：直线下落，不接受任何控制
        self.nxt_rogue = False  # 下一块是否为失控块（预览用）
        self.rogue_count = 0
        self.pending_garbage = 0  # 待推入的垃圾行数（等本方 flash 结束再推）
        self.garbage = 0          # 已吃垃圾行总数

    @property
    def cells(self):
        return self.rots[self.rot] if self.rots else frozenset()


def try_move(side, dx):
    if not side.game._collides(side.cells, side.x + dx, side.y):
        side.x += dx
        return True
    return False


def try_rotate(side, direction=1):
    n = len(side.rots)
    new_rot = (side.rot + direction) % n
    cells = side.rots[new_rot]
    for kx, ky in KICKS:
        if not side.game._collides(cells, side.x + kx, side.y + ky):
            side.rot, side.x, side.y = new_rot, side.x + kx, side.y + ky
            return True
    return False


def drop_y(side):
    y = side.y
    while not side.game._collides(side.cells, side.x, y + 1):
        y += 1
    return y


def spawn(side, dealer):
    side.key = side.nxt or dealer.next()
    side.nxt = dealer.next()
    side.rots = rotations_of(side.key)
    side.rot, side.x, side.y = 0, 3, -2
    side.fall_t = 0
    side.plan = None
    side.rogue = bool(side.nxt_rogue)  # 上一块出生时已掷好的骰子
    if side.rogue:
        side.rogue_count += 1
    side.nxt_rogue = side.game.rng.random() < ROGUE_P  # 给下一块掷骰子
    if side.game._collides(side.cells, side.x, side.y):
        side.over = True
        return False
    side.has_piece = True
    return True


def lock(side, foe=None):
    nb = [row[:] for row in side.game.board]
    topped = False
    for dx, dy in side.cells:
        x, y = side.x + dx, side.y + dy
        if y < 0:
            topped = True
            continue
        nb[y][x] = 1
    full = [y for y in range(H) if all(nb[y])]
    side.game.board = nb
    side.game.pieces += 1
    side.game.lines += len(full)
    side.score += SCORES[min(len(full), 4)]
    side.flash, side.flash_t = full, 6 if full else 0
    side.has_piece = False
    if topped:
        side.over = True
    if full and foe is not None and not topped:  # 对轰：消 n 行 -> 对手吃 n 行垃圾
        foe.pending_garbage += len(full)
    return len(full)


def make_garbage_row():
    """整行填满、只留 1 个随机空洞的垃圾行。"""
    hole = GARBAGE_RNG.randrange(W)
    return [0 if x == hole else 1 for x in range(W)]


def apply_garbage(side, n):
    """底部推上 n 行垃圾（整盘上移一格）；下落块跟着上移保持相对位置；
    若顶部方块被挤出棋盘则判负。"""
    applied = 0
    for _ in range(n):
        top = side.game.board[0]
        side.game.board = side.game.board[1:] + [make_garbage_row()]
        applied += 1
        if side.has_piece:
            side.y -= 1
        if any(top):
            side.over = True
            break
    side.garbage += applied
    side.pending_garbage = 0


def clear_flash(side):
    kept = [row for i, row in enumerate(side.game.board) if i not in side.flash]
    side.game.board = [[0] * W for _ in range(len(side.flash))] + kept
    side.flash = []


def _think_worker(client, board, key, nxt, top_k=8):
    """后台线程：纯函数枚举 + decide。返回 (pid, top_p, slim)，
    slim=[{pid,x,rot,y,hscore}]；无合法落位返回 None；异常由调用方吞掉。"""
    placements = compute_placements(board, key)
    if not placements:
        return None
    cands = prune(placements, k=min(top_k, len(placements)))
    pid = client.decide(SimpleNamespace(board=board), key, nxt, cands)
    mv = client.last_move or {}
    probs = mv.get("probabilities", {}) if isinstance(mv, dict) else {}
    top_p = max(probs.values()) if probs else 0.0
    slim = [{"pid": c["pid"], "x": c["x"], "rot": c["rot"],
             "y": c["y"], "hscore": c["hscore"]} for c in cands]
    return (pid, top_p, slim)


def request_plan(side, client):
    """出生即提交后台思考，主循环不等结果。"""
    side._gen += 1
    board_copy = [row[:] for row in side.game.board]
    fut = _EXEC.submit(_think_worker, client, board_copy, side.key, side.nxt)
    side._future = (side._gen, fut)


def poll_plan(side):
    """每帧非阻塞收结果；过期/落子已结束则丢弃；无合法落位判负。"""
    if side._future is None:
        return
    gen, fut = side._future
    if not fut.done():
        return
    side._future = None
    try:
        res = fut.result()
    except Exception:
        return  # 思考失败：本块无计划，直落即可，下一块重试
    if gen != side._gen or not side.has_piece:
        return  # 过期计划
    if res is None:
        side.over = True
        return
    pid, top_p, slim = res
    by = {s["pid"]: s for s in slim}
    chosen = by.get(pid)
    if chosen is None:  # defer / 非法选项出口：回退启发式
        chosen = max(slim, key=lambda s: s["hscore"])
    # hard drop 判定：Laya 看首选项概率；启发式客户端概率恒 1，加随机门限保证“偶尔”
    if top_p >= 1.0:
        hard = random.random() < 0.25
    else:
        hard = top_p >= HARD_PROB_MIN
    side.plan = {"tx": chosen["x"], "trot": chosen["rot"], "ty": chosen["y"],
                 "hard": hard, "hard_at": min(chosen["y"], side.y + 6)}


def ai_steer(side):
    """每 AI_STEER_EVERY 帧调用：向目标列/旋转各走一步（类人速度）。"""
    p = side.plan
    if not p:
        return
    if side.x < p["tx"]:
        try_move(side, 1)
    elif side.x > p["tx"]:
        try_move(side, -1)
    if side.rot != p["trot"]:
        try_rotate(side, 1)


def ai_hard_drop(side, foe=None):
    side.y = drop_y(side)
    lock(side, foe)
    side.plan = None


# ---------------- 绘制 ----------------

def draw_board(screen, board, ox, flash_rows=()):
    for y in range(H):
        for x in range(W):
            r = pygame.Rect(ox + x * CELL, TOP + y * CELL, CELL, CELL)
            if board[y][x]:
                c = FLASH if y in flash_rows else (200, 200, 215)
                pygame.draw.rect(screen, c, r)
                pygame.draw.rect(screen, BG, r, 2)
            else:
                pygame.draw.rect(screen, EMPTY_CELL, r)
                pygame.draw.rect(screen, GRID, r, 1)


def draw_cells(screen, ox, cells, bx, by, color, ghost=False):
    for dx, dy in cells:
        x, y = bx + dx, by + dy
        if y < 0 or y >= H or x < 0 or x >= W:
            continue
        r = pygame.Rect(ox + x * CELL, TOP + y * CELL, CELL, CELL)
        if ghost:
            pygame.draw.rect(screen, color, r, 2)
        else:
            pygame.draw.rect(screen, color, r)
            pygame.draw.rect(screen, BG, r, 2)


def draw_mini(screen, key, px, py, label, font, rogue=False):
    if rogue:
        label += " (ROGUE!)"
    screen.blit(font.render(label, True, (180, 180, 200)), (px, py))
    cells = rotations_of(key)[0]
    minx = min(x for x, _ in cells)
    color = ROGUE_COLOR if rogue else COLORS[key]
    for dx, dy in cells:
        r = pygame.Rect(px + (dx - minx) * 18, py + 24 + dy * 18, 18, 18)
        pygame.draw.rect(screen, color, r)
        pygame.draw.rect(screen, BG, r, 2)


def _focus_window():
    """尽力把游戏窗口抢到前台（按键进了终端是“失灵”最常见原因）。失败就跳过。"""
    try:
        import ctypes
        hwnd = pygame.display.get_wm_info().get("window")
        if hwnd:
            ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def draw_side_panel(screen, side, ox, font, extra=()):
    y = TOP + BH + 8
    for label, val in [("PIECES", side.game.pieces), ("LINES", side.game.lines),
                       ("SCORE", side.score)]:
        screen.blit(font.render(f"{label} {val}", True, (220, 220, 235)), (ox, y))
        y += 24
    for text, color in extra:
        screen.blit(font.render(text, True, color), (ox, y))
        y += 24


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--ai", default="laya", choices=["laya", "eltetris"])
    ap.add_argument("--human", default="you", choices=["you", "bot"])
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--max-pieces", type=int, default=0,
                    help="任一方达到即结束（冒烟测试用；0=不限，打到死）")
    ap.add_argument("--auto-start", action="store_true",
                    help="跳过开始菜单直接开打（冒烟测试用）")
    ap.add_argument("--rogue-p", type=float, default=ROGUE_P,
                    help="失控块概率（默认约 1/17，即 15–20 块一次）")
    args = ap.parse_args()

    globals()["ROGUE_P"] = args.rogue_p

    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption(f"AI({args.ai}) vs YOU")
    _focus_window()
    clock = pygame.time.Clock()
    font = pygame.font.Font(None, 22)
    big = pygame.font.Font(None, 34)
    screen.fill(BG)
    screen.blit(big.render("LOADING MODEL...", True, (220, 220, 235)), (MID_X, 200))
    pygame.display.flip()

    ai_client = (LayaClient(model=MODEL_DIR) if args.ai == "laya" else ElTetrisClient())
    # 人方托管：--human bot 开局即托管；否则点 "LAYA HOST" 后台加载第二个实例
    if args.human == "bot":
        host_client, human_hosted, host_kind = ElTetrisClient(), True, "bot"
    else:
        host_client, human_hosted, host_kind = None, False, args.ai
    host_loading, host_future, host_error = False, None, None
    ensure_keys_file()
    keymap = load_keymap()
    GARBAGE_RNG.seed(args.seed ^ 0xA11CE)
    dealer = Dealer(seed=args.seed)
    ai, human = Side("AI"), Side("YOU")
    paused = False
    winner = None
    state = "playing" if args.auto_start else "menu"  # menu -> playing -> over
    end_t = 0
    held = {}
    frames = 0
    exit_reason = "unknown"
    last_key = "-"      # 按键回显：证明输入链路活着
    key_count = 0
    shot_saved = False
    btn_start = Button(MID_X, HEIGHT - 64, 200, 50, "START GAME")
    btn_pause = Button(MID_X, HEIGHT - 64, 200, 44, "PAUSE")
    btn_host = Button(MID_X, HEIGHT - 118, 200, 44, "LAYA HOST")
    btn_restart = Button(MID_X, HEIGHT - 64, 200, 50, "RESTART")
    running = True

    def right_name():
        if human_hosted or args.human == "bot":
            return "LAYA-1" if host_kind == "laya" else "BOT"
        return "YOU"

    def load_host_client():
        return LayaClient(model=MODEL_DIR) if host_kind == "laya" else ElTetrisClient()

    def start_match():
        nonlocal dealer, ai, human, paused, winner, state, end_t, held, shot_saved
        dealer = Dealer(seed=args.seed)
        ai, human = Side("AI"), Side("YOU")
        paused = False
        winner = None
        state = "playing"
        end_t = 0
        held = {}
        shot_saved = False
        btn_pause.label = "PAUSE"

    while running:
        frames += 1
        mouse = pygame.mouse.get_pos()
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
                exit_reason = f"quit-event@{frames}"
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                last_key = "click"
                key_count += 1
                if state == "menu" and btn_start.rect.collidepoint(e.pos):
                    start_match()
                elif state == "playing" and btn_pause.rect.collidepoint(e.pos):
                    paused = not paused
                    btn_pause.label = "RESUME" if paused else "PAUSE"
                elif (state == "playing" and args.human == "you"
                      and btn_host.rect.collidepoint(e.pos)):
                    if human_hosted:
                        human_hosted = False
                        pygame.display.set_caption(f"AI({args.ai}) vs YOU")
                    elif not host_loading:
                        host_loading = True
                        host_error = None
                        host_future = _EXEC.submit(load_host_client)
                elif state == "over" and btn_restart.rect.collidepoint(e.pos):
                    start_match()
            elif e.type == pygame.KEYDOWN:
                last_key = pygame.key.name(e.key)
                key_count += 1
                if state == "menu" and e.key in keymap["start"]:
                    start_match()
                elif state == "over" and e.key in keymap["start"]:
                    start_match()
                elif state == "playing":
                    if e.key in keymap["pause"]:
                        paused = not paused
                        btn_pause.label = "RESUME" if paused else "PAUSE"
                    elif (args.human == "you" and not human_hosted and not paused
                          and human.has_piece and not human.over and not human.rogue):
                        if e.key in keymap["rotate"]:
                            try_rotate(human, 1)
                        elif e.key in keymap["rotate_ccw"]:
                            try_rotate(human, -1)
                        elif e.key in keymap["hard_drop"]:
                            human.y = drop_y(human)
                            lock(human, ai)

        # 托管模型后台加载完成 -> 立即接管人这边
        if host_loading and host_future is not None and host_future.done():
            host_loading = False
            try:
                host_client = host_future.result()
            except Exception as ex:
                host_error = f"host load failed: {ex}"[:48]
            else:
                human_hosted = True
                pygame.display.set_caption(f"AI({args.ai}) vs {right_name()}")
            host_future = None

        gravity = max(8, GRAVITY0 - max(ai.game.lines, human.game.lines) // 6)
        rn = right_name()

        if state == "playing" and not paused:
            for side, foe, is_ai in ((ai, human, True), (human, ai, False)):
                hosted = is_ai or human_hosted
                if side.over:
                    continue
                if side.flash_t > 0:
                    side.flash_t -= 1
                    if side.flash_t == 0:
                        clear_flash(side)
                    continue
                if side.pending_garbage:  # 对方消行的对轰垃圾，等本方 flash 结束推入
                    apply_garbage(side, side.pending_garbage)
                    if side.over:
                        continue
                if not side.has_piece:
                    if spawn(side, dealer):
                        if not side.rogue:  # 失控块：不思考、不转向，直落
                            if is_ai:
                                request_plan(side, ai_client)
                            elif human_hosted and host_client is not None:
                                request_plan(side, host_client)
                    continue
                # 重力（双方同速；失控块 3 倍速直落；人类按住↓则 2 倍速）
                side.fall_t += 1
                step_every = max(2, gravity // 3) if side.rogue else gravity
                if not hosted and not side.rogue:
                    keys = pygame.key.get_pressed()
                    if any(keys[k] for k in keymap["soft_drop"]):
                        step_every = 2
                    # 人类左右移动（带重复）
                    for action, dx in (("left", -1), ("right", 1)):
                        codes = keymap[action]
                        if any(keys[k] for k in codes):
                            held[action] = held.get(action, 0) + 1
                            if held[action] == 1 or held[action] >= 12 and (held[action] % 4 == 0):
                                try_move(side, dx)
                        else:
                            held[action] = 0
                if side.fall_t >= step_every:
                    side.fall_t = 0
                    if not side.game._collides(side.cells, side.x, side.y + 1):
                        side.y += 1
                    else:
                        lock(side, foe)
                        continue
                # AI/托管：先收后台思考结果，再转向（含 hard drop 时机）
                if hosted:
                    poll_plan(side)
                    side.steer_t += 1
                    p = side.plan
                    if p and p["hard"] and side.y >= p["hard_at"]:
                        ai_hard_drop(side, foe)
                    elif side.steer_t >= AI_STEER_EVERY:
                        side.steer_t = 0
                        ai_steer(side)

            if ai.over or human.over:
                if ai.over and human.over:
                    winner = "DRAW"
                else:
                    winner = f"{rn} WINS" if ai.over else "AI WINS"
                state = "over"
                end_t = 0
            elif args.max_pieces and (ai.game.pieces >= args.max_pieces
                                      or human.game.pieces >= args.max_pieces):
                winner = "CAP REACHED"
                state = "over"
                end_t = 0

        if state == "over":
            if not shot_saved:
                shot_saved = True
                pygame.image.save(screen, str(Path(__file__).resolve().parent / "versus_shot.png"))
            end_t += 1
            if args.max_pieces and end_t > 200:
                running = False
                exit_reason = f"winner-timeout({winner})@{frames}"

        # ---- 绘制 ----
        screen.fill(BG)
        draw_board(screen, ai.game.board, AI_X, ai.flash)
        draw_board(screen, human.game.board, HUMAN_X, human.flash)
        if ai.has_piece and not ai.over:
            draw_cells(screen, AI_X, ai.cells, ai.x, ai.y,
                       ROGUE_COLOR if ai.rogue else COLORS[ai.key])
        if human.has_piece and not human.over:
            draw_cells(screen, HUMAN_X, human.cells, human.x, human.y,
                       ROGUE_COLOR if human.rogue else COLORS[human.key])
        # ---- 左栏：LAYA 决策细节 + 战绩 ----
        lx = COL_AI
        ly = TOP
        screen.blit(font.render("THINKING", True, (120, 230, 170)), (lx, ly))
        ly += 28
        dec = getattr(ai_client, "last_decision", None) or {}
        if dec.get("rule"):
            screen.blit(font.render((f"rule: {dec['rule']}")[:32], True, (200, 200, 215)), (lx, ly))
            ly += 26
            if isinstance(dec.get("danger"), float):
                screen.blit(font.render(f"danger {dec['danger']:.2f}", True, (250, 150, 120)), (lx, ly))
                ly += 26
            safety = dec.get("safety")
            if isinstance(safety, dict) and "score" in safety:
                screen.blit(font.render(f"safety {safety['score']:.1f}", True, (150, 200, 250)), (lx, ly))
                ly += 26
            think_line = f"think {dec['ms']:.0f}ms"
            if isinstance(dec.get("conf"), (int, float)):
                think_line += f" conf {dec['conf']:.3f}"
            screen.blit(font.render(think_line, True, (150, 150, 170)), (lx, ly))
            ly += 26
            screen.blit(font.render(f"choice {dec.get('choice')}", True, (200, 200, 215)), (lx, ly))
            ly += 26
            probs = dec.get("probs") or {}
            desc = dec.get("options") or {}
            if probs:
                for pid, p in sorted(probs.items(), key=lambda kv: -kv[1])[:3]:
                    screen.blit(font.render(f"{pid}({desc.get(pid, '?')}) {p:.2f}",
                                            True, (200, 200, 215)), (lx, ly))
                    pygame.draw.rect(screen, (90, 200, 140),
                                     (lx, ly + 22, int(170 * max(0.0, min(1.0, p))), 8))
                    ly += 36
            if ai.plan and ai.plan["hard"]:
                screen.blit(font.render("HARD DROP ARMED", True, (250, 220, 90)), (lx, ly))
                ly += 26
        else:
            screen.blit(font.render("waiting...", True, (150, 150, 170)), (lx, ly))
            ly += 26
        ly += 8
        for label, val in [("PIECES", ai.game.pieces), ("LINES", ai.game.lines),
                           ("SCORE", ai.score), ("GARBAGE", ai.garbage)]:
            screen.blit(font.render(f"{label} {val}", True, (220, 220, 235)), (lx, ly))
            ly += 24
        if ai.rogue:
            screen.blit(font.render("!! ROGUE-NO CONTROL !!", True, ROGUE_COLOR), (lx, ly))
            ly += 26
        elif ai.rogue_count:
            screen.blit(font.render(f"rogue x{ai.rogue_count}", True, (150, 150, 170)), (lx, ly))
            ly += 26
        if ai.pending_garbage:
            screen.blit(font.render(f"garbage +{ai.pending_garbage} incoming", True,
                                    (250, 150, 120)), (lx, ly))
            ly += 26
        ly += 6
        if ai.nxt:
            draw_mini(screen, ai.nxt, lx, ly, "AI NEXT", font, rogue=ai.nxt_rogue)

        # ---- 右栏：你的战绩 + 操作 ----
        def _keys(action):
            return "/".join(KEY_NAMES.get(action, [action])).upper()
        rx = COL_YOU
        ry = TOP
        for label, val in [("PIECES", human.game.pieces), ("LINES", human.game.lines),
                           ("SCORE", human.score), ("GARBAGE", human.garbage)]:
            screen.blit(font.render(f"{label} {val}", True, (220, 220, 235)), (rx, ry))
            ry += 24
        if human.rogue:
            screen.blit(font.render("!! ROGUE-NO CONTROL !!", True, ROGUE_COLOR), (rx, ry))
            ry += 26
        elif human.rogue_count:
            screen.blit(font.render(f"rogue x{human.rogue_count}", True, (150, 150, 170)), (rx, ry))
            ry += 26
        if human.pending_garbage:
            screen.blit(font.render(f"garbage +{human.pending_garbage} incoming", True,
                                    (250, 150, 120)), (rx, ry))
            ry += 26
        ry += 6
        if human.nxt:
            draw_mini(screen, human.nxt, rx, ry, f"{rn} NEXT", font, rogue=human.nxt_rogue)
            ry += 112
        if human_hosted or args.human == "bot":
            screen.blit(font.render(f"HOSTED BY {rn}", True, (120, 230, 170)), (rx, ry))
            ry += 28
            screen.blit(font.render("click LAYA HOST / TAKE OVER", True, (150, 150, 170)), (rx, ry))
        else:
            screen.blit(font.render("CONTROLS", True, (150, 200, 250)), (rx, ry))
            ry += 28
            for action, label in (("left", "move"), ("soft_drop", "fast"), ("rotate", "rotate"),
                                  ("rotate_ccw", "rot-ccw"), ("hard_drop", "drop"),
                                  ("pause", "pause")):
                if action == "left":
                    t = f"{_keys('left')}/{_keys('right')}: move"
                else:
                    t = f"{_keys(action)}: {label}"
                screen.blit(font.render(t, True, (150, 150, 170)), (rx, ry))
                ry += 26

        mx = MID_X
        # 棋盘大标题：左 LAYA / 右 YOU（或托管后的 LAYA-1）
        t_laya = big.render("LAYA", True, (120, 230, 170))
        screen.blit(t_laya, (AI_X + BW // 2 - t_laya.get_width() // 2, 10))
        t_you = big.render(rn, True, (120, 230, 170) if human_hosted or args.human == "bot"
                           else (150, 200, 250))
        screen.blit(t_you, (HUMAN_X + BW // 2 - t_you.get_width() // 2, 10))
        screen.blit(font.render(f"engine: {args.ai}", True, (120, 230, 170)), (mx, 30))
        screen.blit(font.render(f"seed {args.seed}", True, (150, 150, 170)), (mx, 56))
        screen.blit(font.render("same pieces · gravity", True, (150, 150, 170)), (mx, 82))
        my = 118
        screen.blit(font.render(f"KEY: {last_key} x{key_count}", True, (250, 220, 90)), (mx, my))
        my += 34
        # （LAYA 决策细节已移到左栏）
        if state == "over":
            col = (250, 80, 80) if winner == "AI WINS" else (120, 230, 170)
            screen.blit(big.render(winner or "", True, col), (mx, my))
            my += 44
            screen.blit(font.render("click RESTART or press", True, (200, 200, 215)), (mx, my))
            my += 26
            screen.blit(font.render(f"{_keys('start')} to retry", True, (200, 200, 215)), (mx, my))
            my += 34
            btn_restart.draw(screen, font, mouse)
        else:
            if state == "menu":
                screen.blit(font.render("click START or press", True, (200, 200, 215)), (mx, my))
                my += 26
                screen.blit(font.render(f"{_keys('start')} to play", True, (200, 200, 215)), (mx, my))
                my += 34
                btn_start.draw(screen, font, mouse)
            elif state == "playing":
                btn_host.label = ("LOADING..." if host_loading
                                  else ("TAKE OVER" if human_hosted
                                        else f"{host_kind.upper()} HOST"))
                btn_host.draw(screen, font, mouse)
                btn_pause.draw(screen, font, mouse)
                if args.human == "you" and not human_hosted and not host_loading:
                    screen.blit(font.render("clear lines -> push garbage", True,
                                            (150, 150, 170)), (mx, HEIGHT - 132))
                if host_error:
                    screen.blit(font.render(host_error, True, ROGUE_COLOR), (mx, HEIGHT - 150))
        if paused and state == "playing":
            screen.blit(big.render("PAUSED", True, (250, 220, 90)), (mx, 240))

        pygame.display.flip()
        clock.tick(FPS)

    pygame.image.save(screen, str(Path(__file__).resolve().parent / "versus_shot.png"))
    _EXEC.shutdown(wait=False)
    pygame.quit()
    print(f"AI lines={ai.game.lines} pieces={ai.game.pieces} rogue={ai.rogue_count} "
          f"garbage={ai.garbage} | {rn} lines={human.game.lines} pieces={human.game.pieces} "
          f"rogue={human.rogue_count} garbage={human.garbage} | "
          f"{winner} | exit={exit_reason} frames={frames} keys={key_count}")


if __name__ == "__main__":
    main()
