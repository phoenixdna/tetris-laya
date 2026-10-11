# -*- coding: utf-8 -*-
"""Laya 玩俄罗斯方块的“翻译官 + 循环”。

设计（按 typesafe-ai 技能的设计表）：
- state 用具名字段的 JSON：board/piece/next/candidates，含义完整、只给决策所需。
- 每步一次调用、并行问三题（独立问题共享同一 state）：
  - placement[choice]：在候选落位集合中选一个； criteria 每个选项自带完整含义；
    另设 defer（no-match 出口）：没有明显好位时交给启发式，模型不硬选。
  - danger[noul]：当前堆叠是否有即刻冲顶危险（高/洞多/不平）。
  - safety[score]：当前局面安全度（critical/risky/stable/clean 四档，每档自带具体情形描述）。
- 代码消费规则（policy 在代码里，不在模型里）：
  danger>=0.7 -> 保守（取最低最实落位）；choice 选 defer/非法 -> 启发式；
  否则听 choice。safety 只做观测记录（复用为数据，不参与分支）。
- token 预算：候选先用确定性启发分剪到 top-K（默认 8），棋盘只裁堆叠顶部；
  用 1024-context 的 typed-decisions/multilingual 更宽裕。
"""
from __future__ import annotations

import json

from tetris import Tetris, W, H, heuristic_score

TOP_ROWS = 12   # state 只带堆叠顶部以上 TOP_ROWS 行
TOP_K = 8       # 候选项剪枝到 8 个（Laya 高选项数是已知短板，见交接说明）


def serialize_board(board, top_rows=TOP_ROWS):
    first = next((y for y in range(H) if any(board[y])), H)
    start = max(0, first - 2)
    rows = board[start:]
    if len(rows) > top_rows:
        rows = rows[-top_rows:]
    return "\n".join("".join("#" if c else "." for c in row) for row in rows)


def build_state_json(game, key, nxt, candidates):
    """具名字段的 JSON state（技能规范：state 给全含义，ID 只给代码用）。"""
    payload = {
        "board": serialize_board(game.board).split("\n"),
        "legend": "#=filled .=empty, top area only",
        "piece": key,
        "next": nxt,
        "candidates": [
            {"id": c["pid"], "col": c["x"], "rot": c["rot"],
             "clears": c["lines"], "holes": c["feat"]["holes"],
             "height": c["feat"]["agg_height"], "max_height": c["feat"]["max_height"]}
            for c in candidates
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def build_state(game, key, nxt, candidates):
    lines = [f"Board {W}x{H} (top area, #=filled):",
             serialize_board(game.board),
             f"Piece: {key}  Next: {nxt}",
             "Candidates (id: piece col rot clears holes height):"]
    for c in candidates:
        f = c["feat"]
        lines.append(
            f"{c['pid']}: {key} col{c['x']} r{c['rot']} "
            f"clears{c['lines']} holes{f['holes']} h{f['agg_height']}")
    return "\n".join(lines)


def prune(placements, k=TOP_K):
    ranked = sorted(placements, key=lambda p: p["hscore"], reverse=True)[:k]
    for i, p in enumerate(ranked):
        p["pid"] = f"p{i}"
    return ranked


class ElTetrisClient:
    """纯代码 El-Tetris（Yiyuan Lee）：枚举全部合法落位，用线性启发式打分取最高。

    score = -0.510066*agg_height + 0.760666*lines - 0.35663*holes - 0.184483*bump
    与 LayaClient 接口一致，可直接互换；也是 Laya 候选剪枝用的同一打分。
    """

    def __init__(self):
        self.last_move = None
        self.last_ms = 0.0

    def choose(self, state_text, candidates):
        return self.decide(None, None, None, candidates, state_text=state_text)

    def decide(self, game, key, nxt, candidates, state_text=None):
        import time
        t0 = time.time()
        best = max(candidates, key=lambda c: c["hscore"])
        self.last_ms = (time.time() - t0) * 1000
        self.last_move = {"choice": best["pid"],
                          "probabilities": {best["pid"]: 1.0},
                          "confidence": 1.0}
        self.last_decision = {"rule": "heuristic", "danger": 0.0,
                              "safety": None, "choice": best["pid"],
                              "probs": {best["pid"]: 1.0}, "conf": 1.0,
                              "ms": self.last_ms,
                              "options": {c["pid"]: f"c{c['x']}/r{c['rot']}"
                                          for c in candidates}}
        return best["pid"]


class StubClient(ElTetrisClient):
    """同接口替身（即 El-Tetris），保留名兼容 demo.py。"""


class LayaClient:
    """真 Laya 客户端：pip install laya 后启用。"""

    def __init__(self, model="convaiinnovations/laya-typed-decisions", subfolder=None):
        import laya  # noqa
        import laya as _laya
        self.agent = (_laya.load(model, subfolder=subfolder) if subfolder
                      else _laya.load(model))
        self.last_move = None   # 最近一次 placement 答案（面板兼容）
        self.last_ms = 0.0
        self.last_decision = None  # 三问组合决策详情：rule/danger/safety/choice

    def choose(self, state_text, candidates):
        raise NotImplementedError("LayaClient 改走三问设计，请用 decide(game, key, nxt, cands)")

    def decide(self, game, key, nxt, candidates):
        """按技能设计表：一次调用并行问 placement[choice] + danger[noul] +
        safety[score]；代码按规则消费（policy 在代码里）。"""
        import time
        criteria = {
            c["pid"]: (f"col {c['x']} rot {c['rot']}: clears {c['lines']} lines, "
                       f"leaves {c['feat']['holes']} holes, "
                       f"stack height {c['feat']['agg_height']}")
            for c in candidates
        }
        criteria["defer"] = ("No candidate is clearly good; "
                             "defer to the built-in heuristic.")
        questions = {
            "placement": {
                "type": "choice",
                "instructions": ("Choose the Tetris placement to play now. Prefer a low "
                                 "flat stack, few holes, and line clears. Choose defer only "
                                 "if no candidate is clearly good."),
                "criteria": criteria,
            },
            "danger": {
                "type": "noul",
                "instructions": ("Is the current stack in immediate danger of topping out? "
                                 "Answer yes if it is tall, holey, or badly uneven."),
            },
            "safety": {
                "type": "score",
                "instructions": "Rate the safety of the current board position.",
                "criteria": [
                    "critical: stack near the top with holes; likely to top out within a few pieces",
                    "risky: tall or uneven stack with several holes",
                    "stable: moderate height with few holes; routine play continues",
                    "clean: low flat stack; easy to continue and plan ahead",
                ],
            },
        }
        state = build_state_json(game, key, nxt, candidates)
        t0 = time.time()
        ans = self.agent.predict(state, questions)
        self.last_ms = (time.time() - t0) * 1000
        # 真实结构：{'model':..., 'answers': {'placement':..., 'danger':..., 'safety':...}}
        A = ans["answers"] if isinstance(ans, dict) and "answers" in ans else ans
        place = A["placement"]
        danger = A["danger"]["noul"] if isinstance(A["danger"], dict) else A["danger"].noul
        safety = A["safety"]
        self.last_move = place if isinstance(place, dict) else place.__dict__
        pick = place["choice"] if isinstance(place, dict) else place.choice
        by_pid = {c["pid"]: c for c in candidates}
        if danger >= 0.7:
            # 保守分支：危险时不听 choice，取最低最实落位
            chosen = min(candidates,
                         key=lambda c: (c["feat"]["agg_height"] + 5 * c["feat"]["holes"]))
            rule = f"conservative(danger={danger:.2f})"
            pid = chosen["pid"]
        elif pick not in by_pid:
            # defer / 非法选项出口：回退启发式
            chosen = max(candidates, key=lambda c: c["hscore"])
            rule = f"defer->heuristic(pick={pick})"
            pid = chosen["pid"]
        else:
            rule = "choice"
            pid = pick
        self.last_decision = {"rule": rule, "danger": danger,
                              "safety": safety, "choice": pid,
                              "probs": (place.get("probabilities", {})
                                        if isinstance(place, dict) else {}),
                              "conf": (place.get("confidence")
                                       if isinstance(place, dict) else None),
                              "ms": self.last_ms,
                              "options": {c["pid"]: f"c{c['x']}/r{c['rot']}"
                                          for c in candidates}}
        return pid


def play(client, max_pieces=200, seed=0, top_k=TOP_K, verbose=False):
    game = Tetris(seed=seed)
    cur = game._draw()
    nxt = game._draw()
    while not game.over and game.pieces < max_pieces:
        placements = game.legal_placements(cur)
        if not placements:
            game.over = True
            break
        cands = prune(placements, k=min(top_k, len(placements)))
        pid = client.decide(game, cur, nxt, cands)
        chosen = next(c for c in cands if c["pid"] == pid)
        cleared = game.step(cur, chosen)
        if verbose:
            print(f"#{game.pieces} {cur} -> {pid} clears={cleared} "
                  f"lines={game.lines} maxh={chosen['feat']['max_height']}")
        cur, nxt = nxt, game._draw()
    return game
