# 打包技术说明

面向需要审计或改动这份打包配置的读者。安装与使用请看 [README](../README.zh-CN.md)。

## 已知问题：播放时 CEF 的解包进程反复崩溃

上游打包进来的 CEF（Chrome 137）在 Chromium 组件更新器解包组件时，会以
`*** stack smashing detected ***` 中止它的 `unzip.mojom.Unzipper` 工具进程。这个问题在普通
Ubuntu 上用 CEF 官方 minimal 示例就能复现，与本沙箱无关，见
[chromiumembedded/cef#3912](https://github.com/chromiumembedded/cef/issues/3912)。
组件更新器在后台运行，所以崩溃集中出现在播放期间。

机制是栈保护金丝雀，需要三件事同时成立：

* CEF 通过 Chromium 的 zygote 拉起子进程，并给它传了
  `--change-stack-guard-on-fork=enable`（在 `ps` 里能看到）；
* Chromium 的 content 层确实实现了这个开关：每次 fork 出的子进程都会调用
  `base::ResetStackCanaryIfPossible()`（`content/app/content_main_runner_impl.cc` 里的
  `RunZygote()`），把从 zygote 继承下来的每个栈帧背后的金丝雀换掉；
* CEF 自己的 `CefExecuteProcess()` 包着 `content::ContentMain()`，因而跨过了那次 fork，
  而它并没有像 Chromium 自己的 fork 路径那样用 `NO_STACK_PROTECTOR` 编译。

于是这种子进程第一次从 `CefExecuteProcess()` 里退出时，金丝雀校验失败，
`__stack_chk_fail` 直接调 `abort()`。这也解释了为什么只有短命的子进程（更新器的 Unzipper）
出问题，而 renderer、GPU、网络这些长命进程要等到退出时才走到同一个校验。

功能上无害：更新器会重试，组件最终确实装上了。代价是崩溃报告——每次 SIGABRT 都会写一个
约 110 MB 的 core dump，并且每条 systemd-coredump 的 journal 记录都会被 KDE 的 drkonqi
接手。实测一次组件更新风暴是 53 秒内 24 次 abort，即 2.6 GB 的 dump，以及在当时的 Plasma
上是 24 个崩溃窗口。

关掉组件更新器本来是显而易见的出路，但在这里走不通。CEF 认识
`--disable-component-update`，可 Linux 上 JCEF 从不把进程命令行交给 CEF：java-cef 原生代码
里的 `Context::Initialize()` 传的是 `CefMainArgs(0, nullptr)`，而 Chromium 的
`CommandLine::Init(0, nullptr)` 由此构造出一个空的 `base::CommandLine`。CEF 能看到的开关只有
`CefSettings` 和应用自己追加的那些，写在 `flatpak run` 命令行上的开关永远到不了更新器。

真正有效的是让内核干脆不要上报这次 abort。`ani-wrapper` 以前把 `RLIMIT_CORE` 设为 0，这不够：
内核只在写文件型 core 时才看这个限制，而这里是 systemd 的管道模式
（`|/usr/lib/systemd/systemd-coredump %P …`），内核照样会启动 systemd-coredump，让它记下这次
abort 并转交给 drkonqi。在 Plasma 6.7.5 自带的 drkonqi 上，仅凭这条 journal 记录就会发出崩溃
通知——这正是用户看到的现象：通知还在，点「详细信息」却什么都没有，因为根本没有 core。

把限制设成**恰好 1 字节**就不一样了。这是内核给递归 core 留的哨兵值：`fs/coredump.c` 里的
`coredump_pipe()` 会检查 `cprm->limit == 1`，在启动管道程序之前就放弃，于是这次 abort 不会被
上报给任何人。`ulimit` 表达不了 1（它的单位是块），所以包装脚本用
`prlimit --core=1:1 --pid $$`。在沙箱里用一次真实崩溃验证过：

```
kernel: coredump: 4(probe-sb): RLIMIT_CORE is set to 1, aborting core
```

同一时间窗内没有 `systemd-coredump` 单元、`coredumpctl` 里没有新条目、也没有任何
`drkonqi-coredump-*` 单元。需要调试时设 `ANIMEKO_FLATPAK_KEEP_CORES=1` 可以跳过这一切、
保留 core：

```sh
flatpak run --env=ANIMEKO_FLATPAK_KEEP_CORES=1 me.him188.ani
```

在此修复生效之前产生的 dump 属主是 root，需要 sudo 删除：

```sh
sudo rm -f /var/lib/systemd/coredump/core.jcef_helper.*
```

真正的修复只能来自上游：给 CEF 的 fork 路径加上 `NO_STACK_PROTECTOR`（#3912 想要的正是这个），
或者提供一个不依赖命令行的组件更新开关。

## 休眠抑制需要显式授予一个 D-Bus 名

播放视频时，Animeko 通过两条路抑制休眠与熄屏：调用 `systemd-inhibit` 命令（logind，系统总线），
以及调用 `org.freedesktop.ScreenSaver` 这个 D-Bus 接口。前者来自
[#2773](https://github.com/open-ani/animeko/pull/2773)，后者来自
[#2925](https://github.com/open-ani/animeko/pull/2925)。

这里只有第二条路能走通。GNOME runtime 里没有 `systemd-inhibit` 命令，而应用会先判断该命令
是否在 `PATH` 上，不在就直接跳过，所以那条路永远不会执行。D-Bus 那条路失败的原因不同：
会话总线代理把 `org.freedesktop.ScreenSaver` 当作未授权的名字拒绝掉，于是 `Inhibit` 静默失败，
播放时机器照样休眠。

把这一个名字授出去即可修好：

```yaml
- --talk-name=org.freedesktop.ScreenSaver
```

`--system-talk-name=org.freedesktop.login1` **故意不授**。它确实是 `systemd-inhibit` 那条路
需要的，授了之后 `login1.Manager.Inhibit` 也的确能用，但既然那条路根本跑不起来，授它只会扩大
沙箱：它会是本包唯一申请的系统总线访问，把 logind（`PowerOff`、`Reboot`、`Suspend` 等接口）
白白暴露出去。

在 Plasma 6 上已通过监视会话总线上的调用、以及电源管理面板列出的抑制者确认：

```
method call ... interface=org.freedesktop.ScreenSaver; member=Inhibit
   string "Animeko"
   string "Playing video"
```

```
[ScreenSaver] D-Bus inhibit cookie: 951
[ScreenSaver] Inhibited via D-Bus org.freedesktop.ScreenSaver
```

电源管理面板（「阻止自动锁屏和睡眠」）随后会显示
「Animeko 正在阻止锁屏。(Playing video)」。

## 为什么 AppImage 原样无法启动

这个 AppImage 是 jpackage 生成的 app-image（JetBrains Runtime 21）。它的原生启动器会读取
`usr/lib/app/Ani.cfg`，把 274 行 `app.classpath` 里的 `$APPDIR` 宏展开，再把结果交给 JVM。

展开后的 classpath 约 37 KB，超出了启动器 `JvmlLauncherData` 缓冲区的处理能力。
原样运行官方 AppImage：

```
$ JPACKAGE_DEBUG=true ./ani-6.1.0-linux-x86_64.appimage
[TRACE] JvmLauncher.cpp:318: Need 38457 bytes for JvmlLauncherData buffer
[TRACE] JvmLauncher.cpp:315: Initialized 38457 bytes at 0x56268b98d870 address
[21575]: jli arg[0]: [.../usr/bin/Ani]
[21575]: jli arg[1]: [-classpath]
[21575]: jli arg[2]: [.../core-common-2.2.0-2212d240dfe1e8a7598ee117ccc316d]
[21575]: jli arg[3]: []          <- 从这一条开始，所有参数都是空的
...
[21575]: jli arg[19]: []
[21575]:                          <- 死在这里
Segmentation fault (core dumped)     # 退出码 139
```

与真实 classpath 对比实测：

| | 字节数 |
|---|---|
| 展开后的 classpath（274 个 jar） | 37074 |
| 实际写入后被截断 | **5306** |

274 条里只有前 39 条写了进去；而同一个缓冲区中紧跟在字符串之后的环境变量指针数组，
被路径数据覆盖了。于是 `jvmLauncherStartJvm()` 拿着一个野指针去调 `setenv()`：

```
(gdb) run
Program received signal SIGSEGV, Segmentation fault.
0x00007ffff7db8291 in setenv () from /lib64/libc.so.6
#0  setenv ()
#1 jvmLauncherStartJvm ()
#2 main ()
$rdi = 0xfffffffffffc97b0     <- 非法的 name 参数
```

JVM 完全没有机会启动。该崩溃在空环境、隔离 `$HOME`、很短路径、直接从 SquashFS 挂载的情况下
均可复现，因此是 AppImage 自身的问题，与具体机器无关。

## 绕过方式：引导 jar

`make-bootstrap.py` 改写 `Ani.cfg`，让启动器永远只看到一个 classpath 条目，
把真正的 jar 清单搬到 manifest 的 `Class-Path` 属性里：

```
app.classpath=$APPDIR/animeko-bootstrap.jar
```

这个引导 jar 里只有 `META-INF/MANIFEST.MF`：

```
Manifest-Version: 1.0
Class-Path: desktop-6.1.0.jar ComposeNativeTray-jvm-5ae5e328b757486c3993
 dc64b8be7a5.jar aboutlibraries-compose-jvm-9cd3d26643d8633a3b84932014c4c7c4.jar
 ...
Main-Class: me.him188.ani.app.desktop.AniDesktop
```

改由 JVM 自己的类加载器去解析这些 jar，启动器那边的缓冲区就只剩很小一段
（日志变成 `Need 1477 bytes`），同时保留了上游的 jar 顺序。顺序在这里是有意义的：
有几个库同时存在两个版本，classpath 上靠前的那个生效。

脚本会跳过 `Ani.cfg` 里指向不存在文件的条目——上游打包时把三个 runtime jar
（`mediamp-mpv-runtime-linux-x64`、`mediamp-ffmpeg-runtime-linux-x64`、
`anitorrent-native-desktop-*-linux-x64`）解包进 `native/` 后删掉了原 jar，
但没有同步更新 `Ani.cfg`。

## 安装时才下载 AppImage（extra-data）

manifest 里 AppImage 是 extra-data 源，而不是构建输入：

```yaml
- type: extra-data
  filename: ani.appimage
  url: https://github.com/open-ani/animeko/releases/download/v6.1.0/ani-6.1.0-linux-x86_64.appimage
  sha256: abeeab01daf4a08ab1cd7c4d9c6999b1741a577d93ad7e2ef28b80ea12296aa7
  size: 336849400
```

flatpak-builder 只把这条记录写进应用 metadata，不会下载文件。AppImage 不在 OSTree commit 里，
也不在 `.flatpak` bundle 里：本仓库构建出来的东西约 100 kB，应用本体在用户安装时从上游 release
拉取，之后每次更新只要这条记录变了就再拉一次。flatpak 会先按 manifest 里的 sha256 校验，
通过才继续。

这么做的原因是托管成本：把 AppImage 打进 commit，等于每个版本往仓库里塞约 300 MB，
并且每次更新的流量都由你出。代价转移到用户侧：每个上游版本都是一次完整的 337 MB 下载
（普通 OSTree 仓库只传变化的对象）；而且下载发生在用户机器上，上游如果把 release 资产删了
或换了，新用户会装不上，而你的 CI 仍然是绿的。

### apply_extra 跑在什么样的沙箱里

`/app/bin/apply_extra` 是 flatpak 在安装过程中执行的，不是构建沙箱，它拿到的沙箱比应用运行时
受限得多：

| | |
|---|---|
| 网络、D-Bus、宿主 | 全都没有 |
| 可写 | 只有 `/app/extra`，同时也是工作目录 |
| `/proc` | **完全不挂载** |
| `/usr` | 应用的 runtime（`org.gnome.Platform`），只读 |
| uid | 用户安装时是当前用户；系统安装时经 system helper 为 root |

这个沙箱由 flatpak 的 `apply_extra_data()`（flatpak-dir.c）用 `FLATPAK_RUN_FLAG_NO_PROC`
构建，是刻意为之：脚本在系统安装场景下可能以 root 运行，不能让 `/proc/self/exe`
成为访问外部文件的通道。

最后那一条正好会打死最直觉的写法。AppImage runtime 靠 `/proc/self/exe` 找到自己内嵌的
SquashFS，没有它就会打印：

```
Cannot open /proc/self/exe: No such file or directory
Failed to get fs offset for /proc/self/exe
```

然后一个字节都没读就退出 127。用手工搭的同性质沙箱复现完全一致。runtime 正好为这种情况留了
开关：`TARGET_APPIMAGE` 指定要读的文件，`apply_extra` 把它设成 `/app/extra/ani.appimage`。
随后 `--appimage-extract` 不需要 FUSE，会在工作目录写出 `squashfs-root/`。

脚本另外处理两个细节：

* flatpak 交给应用的是 644 权限的文件，所以先 `chmod +x`；
* `python3` 来自 runtime 的 `/usr/bin/python3`（org.gnome.Platform 49 里是 Python 3.13），
  所以引导 jar 那一步可以继续用 Python，不必改写成 shell。

解包后的目录被移到 `/app/extra/ani`，再由 `make-bootstrap.py` 打补丁——和以前在构建期做的事
完全一样，只是现在跑在用户机器上，每装一个版本跑一次。

想反复调试 `apply_extra` 又不想一直重装，可以手工复刻同一个沙箱：

```sh
bwrap --unshare-all --ro-bind $RUNTIME_FILES /usr --ro-bind $BUILD_DIR/files /app \
      --bind $SOMEDIR /app/extra --symlink usr/bin /bin --symlink usr/lib /lib \
      --symlink usr/lib64 /lib64 --dev /dev --tmpfs /tmp \
      --chdir /app/extra --cap-drop ALL --setenv PATH /app/bin:/usr/bin \
      -- /app/bin/apply_extra
```

## 发布与更新通道

`gh-pages` 分支上放着一个 OSTree 仓库，也就是 `flatpak update` 拉取的地方。它由 CI 在打标签时
更新：构建时加上 `--gpg-sign`，然后 `flatpak build-update-repo --gpg-sign --generate-static-deltas`
刷新 summary 和 delta，最后把仓库和两个描述文件推上去（`me.him188.ani.flatpakref.in` /
`me.him188.ani.flatpakrepo.in` 里的 `@PAGES_URL@`、`@GPGKEY@` 在此时替换）。

因为应用本体是 extra-data，仓库里只有元数据：

| | |
|---|---|
| 第一个版本 | 380 KB |
| 之后的每个版本 | ~100 KB（主要是新 commit 和 delta） |
| 用户更新时 | 从你这里拉 ~100 KB 元数据，再从上游 GitHub 下 337 MB payload |

### 每次发布都会让用户重下 337 MB

这一点在实现时实测确认过（`flatpak update -v`）：

```
F: Loading https://github.com/open-ani/animeko/releases/download/v6.1.0/ani-6.1.0-linux-x86_64.appimage using curl
F: extracting extra data
F: Running /app/bin/apply_extra
```

即使新 commit 里的 extra-data 记录与上一版**完全一样**（同样的 url/sha256/size），flatpak 依然
会重新下载并重新执行 `apply_extra`。所以"只改了桌面文件"的版本也会让每个用户重下 337 MB——
发版要攒着一起发。

### 不能再用 bundle

以前挂在 Release 上的 bundle 现在已经做不出来了：extra-data 的记录在 commit 的 detached
metadata 里，而 `flatpak build-bundle` 不会把它写进 bundle，所以安装时报

```
错误：安装捆绑包 me.him188.ani 失败：分离的元数据中缺少额外的数据
```

这是拿本 manifest 构建出的签名仓库实测的结果。仓库是唯一的安装入口。

### 密钥

签名密钥只需要一把（`GPG_PRIVATE_KEY` secret），公钥由 CI 从私钥当场导出。密钥丢失等于所有用户都要重新
添加 remote，所以私钥要在仓库外备份。轮换密钥的流程是：换 secret → 重新发布 → 用户在
`flatpak remote-delete animeko` 后用新的 flatpakref 重新添加。

## 应用 ID

Flatpak 的 app-id 用的是 `me.him188.ani`，取自上游为这个应用已经在使用的 application id：

| 位置 | 值 |
|---|---|
| Android `applicationId`（`app/android/build.gradle.kts`） | `me.him188.ani` |
| macOS `CFBundleURLName` / URL scheme | `me.him188.ani` / `ani` |
| Java 包名根（主类 `me.him188.ani.app.desktop.AniDesktop`） | `me.him188.ani` |
| 数据目录（`ProjectDirectories.from("me", "Him188", "Ani")`） | `~/.local/share/ani` |

Flatpak 的 app-id 采用反向域名，而 `open-ani/animeko` 是 GitHub 组织名与仓库名，
本身不携带域名。上游另外持有 `animeko.org` 与 `openani.org`；若要改用其中之一，
把 `me.him188.ani` 全局替换掉即可（app-id、各文件名、图标名、metainfo 的 `<id>`），
沙箱数据目录会随之变为 `~/.var/app/org.openani.Animeko/`。

## 图标

上游在 `app/desktop/build.gradle.kts` 里把 Linux 的图标设置注释掉了：

```kotlin
linux {
    shortcut = true
    packageName = "animeko"
//    iconFile.set(file("icons/a_1024x1024_rounded.ico"))
}
```

于是 jpackage 回退到 Compose Multiplatform 的默认图标，`usr/lib/Ani.png`（1024×1024）
是 Kotlin 的 logo。

真正的 Animeko 图标在 AppImage 中的另一个位置。包里包含的图片文件是：

```
icon.png                                   # .DirIcon，512x512，应用图标
usr/lib/Ani.png                            # jpackage 图标槽，Kotlin 默认图
usr/lib/app/resources/icon.png             # 与根目录 icon.png 同一文件
usr/lib/app/desktop-6.1.0.jar
  └── composeResources/.../drawable/a_round.png   # 应用窗口/托盘自画的图标，192x192
```

本包安装的是根目录的 `icon.png`：512×512 那一档是它的原文件（md5 相同），
128/256 是它的 Lanczos 缩放。`icons/appimage-icon.png` 保留了一份原始文件，
这样不必解包 780 MB 的 AppImage 也能复现和核对：

```sh
magick icons/appimage-icon.png -filter Lanczos -resize 256x256 icons/me.him188.ani-256.png
```

上游 `app/desktop/icons/a_512x512.icns` 里是同一个 logo 的 1024×1024 版本，
但带 macOS 的安全区留白，放到 Linux 桌面上会明显偏小。

## 目录布局

```
/app/bin/ani              包装脚本（Flatpak 的 command）
/app/bin/apply_extra      flatpak 在安装/更新时执行
/app/libexec/make-bootstrap.py
/app/share/...            desktop 文件、metainfo、图标
/app/extra/ani.appimage   只在 apply_extra 运行期间存在，脚本会删掉它
/app/extra/ani/           解包后的 AppImage 目录树（usr/bin/Ani、usr/lib/...）
/app/extra/ani/usr/lib/app/            jpackage 应用目录：Ani.cfg、各种 jar、
                                       native/、resources/
/app/extra/ani/usr/lib/app/animeko-bootstrap.jar
```

`/app/extra` 下的全部内容都是 flatpak 下载、apply_extra 生成的；其余部分才是本仓库构建的
那约 100 kB。应用所有路径都相对于 `usr/lib/app` 解析，所以这棵树与 AppImage 保持逐字节一致，
只改了 `Ani.cfg` 并新增了引导 jar。

## 沙箱细节

### 必须给 X11

JCEF 硬编码了 `--ozone-platform=x11`：Chromium 的 Wayland 后端会让 CEF 浏览器进程在到达
INITIALIZED 之前就崩溃（上游注释里写明「Force the X11 (XWayland) backend, which is
stable」），Skiko/AWT 也走 XWayland 渲染。所以 manifest 同时要了 `--socket=x11` 和
`--socket=wayland`，而不是常见的 `--socket=fallback-x11`——后者在 Wayland 会话里不会
提供 X11，JCEF 就没法工作了。

### JCEF 没有 Chromium 自带沙箱

Chromium 的沙箱要么依赖 setuid 的 `chrome-sandbox`（在 Flatpak 里不可能），要么依赖
非特权 user namespace，而后者被 Flatpak 的 seccomp 策略禁掉了：

```
$ flatpak run --command=sh org.gnome.Platform//49 -c 'unshare -U true'
unshare: unshare failed: Operation not permitted
```

CEF 会自行检测到这一点，并给它的 helper 进程加上 `--no-sandbox` 降级运行，
在进程列表里可以看到：

```
/app/extra/ani/usr/lib/runtime/lib/jcef_helper --type=gpu-process --no-sandbox ...
```

因此 JCEF 仍能到达 `INITIALIZED`，用于 Bangumi 登录的内置浏览器可正常工作。
包装脚本并不传 `--no-sandbox`，这是 CEF 自身的降级行为。渲染进程失去了它自己的
内层沙箱，但仍然受 Flatpak 沙箱约束。

### 托盘图标

Compose 的托盘组件（`libLinuxTray.so`，基于 sd-bus）注册的 bus 名是
`org.kde.StatusNotifierItem-<pid>-1`。Flatpak 无法用通配符表达这种带连字符的名字
（`--own-name` 的通配符必须是 `.*` 形式，而这里短横线不是点），但沙箱里应用的 PID
固定是 2，所以 `--own-name=org.kde.StatusNotifierItem-2-1` 正好覆盖。在沙箱内确认：

```
$ flatpak run --command=gdbus me.him188.ani call --session \
    --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
    --method org.freedesktop.DBus.RequestName org.kde.StatusNotifierItem-2-1 0
(uint32 1,)          # PRIMARY_OWNER
```

托盘是懒加载的：只有窗口被隐藏到托盘后才会创建，所以启动时看不到图标。

## 实测结果

```
Ani started. platform: Linux x86_64, version: 6.1.0, isDebug: false
dataDir: file://~/.var/app/me.him188.ani/data/ani
Anitorrent is loaded.          <- 自带 libtorrent，GLIBC_2.38 符号可解析
FFmpegKit is loaded.           <- 自带 FFmpeg
mediampv is loaded.            <- 自带 libmpv
JCEF is initialized.           <- 内置浏览器
Using bundled video enhancement shaders from /app/extra/ani/usr/lib/app/resources/anime4k
```

`Ani`、`libskiko`、`libmediampv`、`libanitorrent`、`libcef`、`libjvm`、`libmpv`
在沙箱内的所有动态库依赖都能解析，零缺失；应用能自行长时间驻留，启动后无崩溃、无 fatal 日志。
