# 事故记录:补丁版被官方自动更新"吃掉"

日期:2026-10-22

## 现象

CI 成功产出补丁版 `Telegram.exe` 并下载到本机后,在受保护频道右键文件,**仍然没有"下载/另存为"**。

## 结论

**补丁没有失效 —— 测试时运行的根本不是补丁版二进制。**
官方客户端的自动更新器在第一次启动时把我们的 exe 原地换成了官方发行版。

## 证据链

### 1. 三个产物,只有两个是不同的东西

| 文件 | 大小 | 版本 | 签名 | 是什么 |
|---|---|---|---|---|
| `Downloads\48fccd15….zip` 内的 `Telegram.exe` | 573,374,976 | 7.3.2 | NotSigned | **我们的补丁版**(CI Debug 构建) |
| `Downloads\48fccd15…\Telegram.exe`(解压后的目录) | 250,053,160 | 7.3.3 | **Telegram FZ-LLC (GlobalSign EV)** | **官方发行版** |
| `%APPDATA%\Telegram Desktop\Telegram.exe` | 250,053,160 | 7.3.3 | 同上 | 官方版(SHA256 与上一行**完全相同**) |

SHA256:

```
补丁版   c27aa3b1c753084a70bc3da1f59efe12e9dd61e230aee2522cdfcab1c48fc6f3
官方版   CEE6F5F0E1F9556E827FB65C9EB422DBEE3ECA7B7723A29D6F1502756E6CA666
```

官方版的 PDB 路径是 `C:\Telegram\tx64\out\Release\Telegram.pdb` —— Telegram 官方发行版的构建路径;
我们的 CI 产出必然是 runner 上的 `…\TBuild\tdesktop\out\Debug\Telegram.pdb`。

### 2. 日志里的版本号切换

`DebugLogs\log_21_15.txt`(第一次启动,补丁版):

```
[21:24:03.180] Launched version: 7003002, install beta: [FALSE], debug mode: [TRUE]
[21:24:03.181] Command line: …\Telegram.exe -noupdate
```

`log.txt`(更新器重启后的那次启动):

```
[2026.10.22 21:26:17] Launched version: 7003003, install beta: [FALSE], debug mode: [TRUE]
[2026.10.22 21:26:17] Command line: …\Telegram.exe -noupdate -debug
```

`7003002` = 7.3.2(我们的),`7003003` = 7.3.3(官方)。

### 3. 更新器日志自证

`DebugLogs\20261022_212616_upd.txt`:

```
Need to update!
Updater name is: …\Updater.exe
Copying file 'updates\temp\Telegram.exe' to '…\Telegram.exe'
Update succeed!  Clearing folder.
Result arguments: -debug
Executed 'Telegram.exe', closing log and quitting.
```

`Telegram.exe` 的 mtime 正好是 **21:24:46**,与更新器覆盖的时刻吻合。

### 4. 根因

`win.yml` 里传的是 `-D DESKTOP_APP_DISABLE_AUTOUPDATE=OFF` —— 按 `cmake/variables.cmake:37` 的语义,
`OFF` 表示**不关闭**自动更新,于是 `Telegram/CMakeLists.txt:2824` 的 `Updater` 目标被构建出来。
我们的版本号 7.3.2 低于官方 7.3.3,客户端一启动就发现"有新版",于是拉官方包把 exe 换掉了。

## 修复

### 运行时(立刻可用,不重建)

`run-patched\start-patched.cmd`:

- 复制原 `tdata`,直接复用登录状态;
- 删掉 `Updater.exe`(让更新器**没有能力**执行);
- `attrib +R` 把 `Telegram.exe` 设为只读(即使更新器还在也覆盖不了);
- 以 `-noupdate` 启动。

### 下次构建(已改好,待提交)

`tdesktop\.github\workflows\win.yml` 与 `verify\win-fork.yml`:

```diff
-          -D DESKTOP_APP_DISABLE_AUTOUPDATE=OFF ^
+          -D DESKTOP_APP_DISABLE_AUTOUPDATE=ON ^
…
           move %OUT%\Telegram.exe artifact/
-          move %OUT%\Updater.exe artifact/
+          if exist %OUT%\Updater.exe move %OUT%\Updater.exe artifact/
```

`ON` 会同时带来两个效果:

- 定义 `TDESKTOP_DISABLE_AUTOUPDATE`(`Telegram/cmake/telegram_options.cmake:40`)→ `UpdaterIsDisabled = true`,客户端永远不查更新;
- 不再构建 `Updater` 目标 → artifact 目录里没有 `Updater.exe`。

第二条改动是**必须的**:否则 `move %OUT%\Updater.exe` 会因为文件不存在而让 "Move artifact" 步骤返回非零,
job 直接失败,artifact 根本不会被上传。

## 附:一次失败的 CI 运行

`0_Windows (x64).txt` 记录的那次运行其实**失败了**,没有产出任何 exe:

```
CMake Error at cmake/external/wallet_engine/CMakeLists.txt:29 (target_sources):
  Cannot find source file:
    C:/Users/runneradmin/TBuild/Libraries/win64/tdesktop_rust/out/src/wallet_engine/wallet_engine.cpp
CMake Generate step failed.  Build files cannot be regenerated correctly.
##[error]Process completed with exit code -1.
```

原因是 workflow 里 "Free up some disk space" 的清理步骤把 UniFFI 生成的 `wallet_engine.cpp` 删掉了
(保留白名单当时不包含 `*/src/wallet_engine/*`)。
fork 的 HEAD 提交 `aea1fdde5e "Keep the wallet-engine binding when pruning the libraries"` 已经修好,
`win.yml` 里也已包含 `-path '*/src/wallet_engine/*'`。后来那次成功运行用的就是修复后的 workflow。
