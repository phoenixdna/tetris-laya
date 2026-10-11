# tetris-laya

用 [Laya](https://huggingface.co/convaiinnovations/laya)（非自回归 System 1 决策模型，421M 参数、1024 上下文）实时下俄罗斯方块，pygame 左右双盘对战：左边 Laya，右边你。

- **人机对战**：同一发牌序列、同一重力，公平开局；左栏实时显示 Laya 的 choice / danger / safety / 概率条 / 耗时。
- **LAYA 一键托管**：PLAYING 中点中栏 `LAYA HOST`，右边也交给第二个 Laya 实例（棋盘标签与窗口标题变为 **LAYA-1**）；再点 `TAKE OVER` 随时把控制权交还键盘。
- **消行对轰**：任一方消 n 行，对手棋盘底部被推上 n 行「只留 1 个随机空洞」的垃圾行；下落块随之整体上移一格，顶部方块被挤出即判负。两侧面板显示 `GARBAGE` 计数与 `garbage +n incoming` 预警。
- **失控块（ROGUE）**：约每 15–20 块随机刷出一块红色失控块，3 倍速直落、期间不接受任何控制，NEXT 会提前红色预警。

## 安装

需要 Python 3.10+（Windows / macOS / Linux 均可），有 GPU 更快但不是必需。

```bash
git clone https://github.com/phoenixdna/tetris-laya.git
cd tetris-laya
pip install laya pygame
```

模型权重约 842MB，不随仓库分发（GitHub 单文件上限 100MB），二选一下载到 `models/laya-typed-decisions/`：

```bash
python fetch_model.py      # 直连 HuggingFace 下载，支持断点续传
# 或
git lfs install
git clone https://huggingface.co/convaiinnovations/laya-typed-decisions models/laya-typed-decisions
```

## 运行

在**仓库根目录**下执行（默认加载本地权重）：

```bash
python tetris-laya/versus.py                  # 左 Laya vs 右边的你（默认）
python tetris-laya/versus.py --ai eltetris    # 左边换成纯代码 El-Tetris，不加载模型、秒开
python tetris-laya/versus.py --human bot      # 右边开局即托管（自动测试用）
python tetris-laya/versus.py --seed 11 --rogue-p 0 --max-pieces 50 --auto-start   # 冒烟测试
```

首次启动要加载 842MB 权重，窗口会显示 `LOADING MODEL...`（本机实测约 46s）；之后每步决策约 0.5–1s，思考在后台线程跑，画面不卡。

| 参数 | 说明 |
| --- | --- |
| `--ai {laya,eltetris}` | 左边引擎，默认 `laya` |
| `--human {you,bot}` | 右边是人还是托管 |
| `--seed N` | 发牌种子，双方同序列 |
| `--rogue-p P` | 失控块概率，默认 `1/17`（约 15–20 块一次），`0` 关闭 |
| `--max-pieces N` | 任一方达到即收尾（冒烟测试用，`0` = 打到死） |
| `--auto-start` | 跳过开始菜单直接开打 |

## 操作

开始菜单点 `START GAME` 或回车；随时可点按钮。

| 键 | 动作 |
| --- | --- |
| ← / → | 左右移动（支持长按连发） |
| ↓ | 加速下落（soft drop） |
| 空格 / X | 顺时针旋转 |
| Z | 逆时针旋转 |
| ↑ | hard drop |
| P | 暂停 / 继续 |

按键可在 `keys.json` 里改（键名用 pygame 键名，如 `"space"` `"up"` `"return"`，每个动作可绑多个键）。

## 文件结构

| 文件 | 作用 |
| --- | --- |
| `tetris.py` | 无头引擎：10×20 棋盘、7-bag 发牌、落位枚举、棋盘特征与 El-Tetris 启发分 |
| `harness.py` | Laya 翻译官：一次并行问三题（`placement[choice]` / `danger[noul]` / `safety[score]`），代码里消费规则；`ElTetrisClient` 是同接口的纯代码替身 |
| `versus.py` | pygame 双人对战主程序：托管按钮、垃圾对轰、失控块、决策面板 |
| `visual.py` | 单人 Laya 实时可视化（左棋盘 + 右侧概率条） |
| `demo.py` / `play_real.py` | 无头试跑：stub 验证循环 / 真 Laya 计时 |
| `fetch_model.py` | 权重直连下载（断点续传） |
| `keys.json` | 按键配置 |

## 规则细节

- 得分：消 1/2/3/4 行 = 100/300/500/800；重力随双方总消行数加速。
- 垃圾行：整行填满、随机留 1 个空洞；对方消行后的闪烁动画结束才推入，避免行号错乱。
- 托管切换：模型在点击后于后台线程加载，加载期间仍可手动玩；加载失败只在中栏红字提示，不影响对局。

## 常见问题

- **`No module named 'laya'`**：`pip install laya`（依赖 torch / transformers，已装则不会重装）。
- **`laya.load()` 卡住不动**：装了 TensorFlow 时其 abseil 运行时会死锁模型构建，加环境变量再跑：`USE_TF=0 python tetris-laya/versus.py`。
- **按键没反应**：先点一下游戏窗口抢焦点（程序启动时会尝试把窗口置前）。
- **换机器要重下模型**：`fetch_model.py` 会跳过已完整的文件、断点续传残缺文件。
