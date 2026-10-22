# 内容保护补丁:改动与验证记录

## 改动一览

| 文件 | 位置 | 改动 |
|---|---|---|
| `Telegram/SourceFiles/data/data_channel.cpp` | `ChannelData::allowsForwarding()` | `return !(flags() & Flag::NoForwards);` → `return true;` |
| `Telegram/SourceFiles/data/data_chat.cpp` | `ChatData::allowsForwarding()` | `return !(flags() & Flag::NoForwards);` → `return true;` |
| `Telegram/SourceFiles/history/history_item.cpp` | `HistoryItem::forbidsForward()` | `return (_flags & MessageFlag::NoForwards);` → `return false;` |

三处都是"唯一读取点",改动一个函数即覆盖该层级的全部闸口:

- `PeerData::allowsForwarding()` 是 peer 级唯一分发点,只路由到 `ChannelData` / `ChatData`
  (`data_peer.cpp:1739`),因此频道、超级群、基础群全部覆盖;
  用户级隐私设置(`UserData::allowsForwarding()`)不受影响。
- 消息级 `noforwards` 标志只在 `HistoryItem::forbidsForward()` 被读取
  (`history_item.cpp:3433`;标志在 `history_item.cpp:2788` 由 `data.is_noforwards()` 写入),
  因此改这一个函数即放开"机器人发的单条保护消息"。

可直接应用的补丁:`verify/allows-forwarding.patch`
(基线 = `8f49dcadfb^`,即第一个补丁提交的父提交)。

## 验证 1:逐字抽取 + 编译 + A/B 真值表

`verify/run_verify.py` 从真实源文件中按花括号配对**逐字**抽取这两个函数
(工作区版本 = 新,`git show 8f49dcadfb^:` = 旧),标志位常量也从真实头文件解析
(`data_channel.h`: `1ULL << 20`,`data_chat.h`: `1 << 8`),
用 C++ 编译器分别编译成两个可执行文件后对拍。

```
ChannelData  (Flag::NoForwards = 0x100000)
                                         old  new
no flags at all                          1    1
unrelated bits only                      1    1
NoForwards only                          0 -> 1
NoForwards + unrelated bits              0 -> 1
every bit except NoForwards              1    1
all bits set                             0 -> 1
```

结果:改动**只在** peer 级 `NoForwards` 置位时改变行为,其它标志位组合行为不变。

## 验证 2:真实 UI 闸口整链对拍

`verify/run_gates.py` 进一步把**判定下载入口的函数本身**逐字抽出编译,并把
`PeerData::allowsForwarding()` 的分发结构照 `data_peer.cpp` 还原,形成
`闸口 → peer → ChannelData/ChatData` 的完整链路(不是把 peer 结果写死)。

抽取位置:`history_item.cpp:3432/3436/3447`、`history_inner_widget.cpp:3925/3929`、
`media_view_overlay_widget.cpp:1466`、`window_session_controller.cpp:146`、
`history_view_list_widget.cpp:6760/6772`。

`1 = 拦截`,单元格为 `改前 → 改后`:

| 闸口 | 受限频道(无单条标志) | 受限频道 + 单条标志 | 受限超级群 | 未受限(对照) | 受限频道 + TTL 媒体 |
|---|---|---|---|---|---|
| `allowsMediaDownloadControls` | 1 → 0 | 1 → 0 | 1 → 0 | 0 → 0 | 1 → 1 |
| `hasCopyRestriction` | 1 → 0 | 1 → 0 | 1 → 0 | 0 → 0 | 1 → 0 |
| `hasCopyMediaRestriction` | 1 → 0 | 1 → 0 | 1 → 0 | 0 → 0 | 1 → 1 |
| `OverlayWidget::hasCopyMediaRestriction` | 1 → 0 | 1 → 0 | 1 → 0 | 0 → 0 | 1 → 1 |
| `HasSavingRestriction` | 1 → 0 | 1 → 0 | 1 → 0 | 0 → 0 | 1 → 1 |
| `CopyRestrictionTypeFor` | Channel → None | Channel → None | Group → None | None → None | Channel → None |
| `CopyMediaRestrictionTypeFor` | Channel → None | Channel → None | Group → None | None → None | Channel → None |

结论:

- 真实场景(频道整体保护、单条消息带保护标志):**全部闸口由拦截翻转为放行**。
- **TTL 媒体仍然被拦**(`forbidsSaving()` 的 `_media->ttlSeconds()` 分支保留),
  阅后即焚语义没有被破坏。
- 付费媒体(paid media)同样保留 —— `HasExtendedMedia(*invoice)` 分支未动。
- 对照组未受限频道行为不变,没有回归。

## 验证 3:调用链覆盖审计

所有保存/下载闸口都读 `peer->allowsForwarding()` 或 `item->forbidsForward()`:

- `HistoryItem::allowsMediaDownloadControls()` — `history_item.cpp:3447`
- `CopyRestrictionTypeFor()` / `CopyMediaRestrictionTypeFor()` — `history_view_list_widget.cpp:6760/6772`
- `HistoryInner::hasCopyRestriction()` — `history_inner_widget.cpp:3925`
- `OverlayWidget::hasCopyMediaRestriction()` / `computeSaveButtonVisible()` / `saveAs()` — `media_view_overlay_widget.cpp:1466/1679/3304`
- `HasSavingRestriction()` — `window_session_controller.cpp:146`
- `AddSaveDocumentAction()`、`Menu::AddDownloadFilesAction()`

## 验证 4:CI 中自动执行

`verify/run_verify.py` 与 `verify/run_gates.py` 现在在 CI 的
**Content-protection gate verification** 步骤里自动跑(runner 上用 MSVC 编译),
日志末尾会打印 `=== GATE VERIFICATION: run_verify=0 run_gates=0 (0 = PASS) ===`。

两个脚本都支持:

- 基线固定为 `8f49dcadfb^`(可用环境变量 `VERIFY_BASELINE` 覆盖),
  不随 HEAD 移动而失效;
- 编译器自动探测:`g++` → `clang++` → MSVC `cl.exe`;
- 找不到编译器或基线 commit 时**跳过**而不是失败,不会拖垮构建。

## 已知副作用

Telegram 把"禁止保存"和"禁止转发"实现为同一个 `noforwards` 标志,所以补丁同时放开:

- 转发、复制文本/图片
- 媒体查看器保存按钮、右键另存为、多选下载
- 截图保护(`contentNeedsScreenshotProtection()` 同样读该函数)

`Story::forbidsForward()`(stories)未改动。

## 运行时注意:自动更新会顶掉补丁版

CI 产物必须关掉自带更新器,否则官方发行版会在第一次启动时把 exe 原地替换掉 ——
详见 `verify/AUTOUPDATE-SWAP.md`。workflow 现在用
`-D DESKTOP_APP_DISABLE_AUTOUPDATE=ON` 构建,并且不再产出 `Updater.exe`。
