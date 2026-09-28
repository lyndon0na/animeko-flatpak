# Animeko Flatpak 打包

把官方 Linux AppImage 重新打包成 Flatpak 的构建配置。

[English](README.md) ｜ **简体中文** ｜ [打包技术说明](docs/packaging-notes.zh-CN.md)

[![build](https://github.com/lyndon0na/animeko-flatpak/actions/workflows/build.yml/badge.svg)](https://github.com/lyndon0na/animeko-flatpak/actions/workflows/build.yml)

## 应用信息

| 项目 | 值 |
|---|---|
| 名称 | Animeko（简称 Ani） |
| 版本 | 6.2.0 |
| App ID | `me.him188.ani` |
| Runtime | `org.gnome.Platform` 51 |
| 架构 | x86_64 |
| 上游源码 | https://github.com/open-ani/animeko |
| 官网 | https://animeko.org/ |
| 应用本体 | 上游的 `ani-6.2.0-linux-x86_64.appimage`，声名为 [extra-data](https://docs.flatpak.org/en/latest/module-sources.html#extra-data) 源：flatpak 在用户机器上安装时下载并校验 sha256，再由 `apply_extra` 解包和打补丁 |

## 安装

### 方式一：从仓库安装

```sh
flatpak install --user https://lyndon0na.github.io/animeko-flatpak/me.him188.ani.flatpakref
```

这一条命令会同时把 remote 配好，之后的版本交给 `flatpak update`（或桌面软件中心）。安装时会从
GitHub 下载约 337 MB 的 AppImage——应用本体就来自那里。

### 方式二：本地构建

```sh
# 安装依赖
sudo dnf install flatpak flatpak-builder      # Fedora
sudo apt install flatpak flatpak-builder      # Debian / Ubuntu

# 添加 Flathub 并安装 GNOME runtime 与 SDK
flatpak remote-add --if-not-exists --user flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install --user flathub org.gnome.Platform//51 org.gnome.Sdk//51

# 克隆并构建
git clone https://github.com/lyndon0na/animeko-flatpak.git
cd animeko-flatpak
flatpak-builder --user --install --force-clean --repo=repo build me.him188.ani.yaml
```

构建阶段不需要下载任何东西：AppImage 是 extra-data 源，构建只用 runtime 和 SDK。安装时才从
上游 release 的 URL 拉取，并按 manifest 里记录的 sha256 校验。

### 为什么没有 bundle

bundle 装不下 extra-data 的记录：那条记录在 commit 的 detached metadata 里，而
`flatpak build-bundle` 不会把它打进 bundle，于是安装时报
`Extra data missing in detached metadata`。所以发布在 GitHub Pages 上的仓库是唯一的安装入口。

## 运行

```sh
flatpak run me.him188.ani
```

## 卸载

```sh
flatpak uninstall --user me.him188.ani
```

卸载时 flatpak 会询问是否一并删除应用数据（`~/.var/app/me.him188.ani/`）。

## 权限说明

| 权限 | 用途 |
|---|---|
| `--share=network` | 在线视频源、BitTorrent、弹幕与 Bangumi API |
| `--share=ipc` | X11 共享内存 |
| `--socket=x11` | JCEF 强制使用 X11 后端，Skiko / AWT 也走 XWayland |
| `--socket=wayland` | Wayland 支持；同时提供 X11，原因见技术说明 |
| `--socket=pulseaudio` | 音频播放 |
| `--device=dri` | GPU 渲染与 VA-API 硬件解码 |
| `--talk-name=org.kde.StatusNotifierWatcher` | 系统托盘 |
| `--own-name=org.kde.StatusNotifierItem-2-1` | 托盘图标注册自己的 D-Bus 名 |
| `--talk-name=org.freedesktop.Notifications` | 通知 |
| `--talk-name=org.freedesktop.ScreenSaver` | 播放时抑制熄屏与休眠 |

这份打包不申请任何宿主文件系统权限。应用默认写入的一切——媒体下载目录、媒体缓存、截图、
数据库、日志——都在 `~/.var/app/me.him188.ani/` 里，沙箱自带这块空间。因此想把缓存或下载目录
指到真实路径时，需要另外授权：文件夹选择器会照样让你选中 `~/Videos`，但在授权之前应用读写不了它。

```sh
flatpak override --user --filesystem=xdg-videos me.him188.ani
```

要整个家目录就把 `xdg-videos` 换成 `home`，或者在 Flatseal 里按应用勾选。manifest 的注释里按
由窄到宽列出了这些选项。

## 文件说明

| 文件 | 说明 |
|---|---|
| `me.him188.ani.yaml` | Flatpak manifest（GNOME runtime 51） |
| `apply_extra` | 安装/更新时执行：解包 AppImage 并打补丁 |
| `make-bootstrap.py` | 改写 `Ani.cfg` 并生成引导 jar，由 `apply_extra` 调用，详见[技术说明](docs/packaging-notes.zh-CN.md) |
| `ani-wrapper` | `/app/bin/ani` 入口脚本 |
| `me.him188.ani.desktop` | 桌面入口 |
| `me.him188.ani.metainfo.xml` | AppStream 元数据 |
| `icons/me.him188.ani-*.png` | 安装的图标（128 / 256 / 512） |
| `icons/appimage-icon.png` | 上述图标的原始文件，取自 AppImage 自带的 `icon.png` |
| `me.him188.ani.flatpakref.in` | 一行命令安装用的模板，由 CI 填充后发布 |
| `me.him188.ani.flatpakrepo.in` | 同上，给手动添加 remote 的人用 |
| `.github/workflows/build.yml` | CI：构建、校验、发布仓库与 Release |
| `.github/workflows/upstream.yml` | 每天检查上游，需要时开升级 PR |
| `docs/` | [打包技术说明](docs/packaging-notes.zh-CN.md) |
| `LICENSE.txt` | AGPL-3.0 许可证 |

## CI

`.github/workflows/build.yml`：

* 推送到 `main` 或手动触发：构建、安装并校验部署后的目录树；
* 推送 `v*` 标签：额外对构建签名、更新已发布的仓库，并创建 GitHub Release；
* `.github/workflows/upstream.yml` 每天跑一次：上游 open-ani/animeko 发了新版本时，
  它会开一个 PR 把 manifest 指向新版本。合并这个 PR **不会**发布——发布仍然靠打标签。

Runner 没有显示器，所以 GUI 冒烟测试在 Xvfb 下尽力而为地运行（只报告，不阻断构建）；
对产物目录的结构校验是阻断性的。

## 发布

打标签即发布，需要一次性准备两件事：

1. 仓库 Secret **`GPG_PRIVATE_KEY`**：签名密钥的 ASCII-armoured 私钥。CI 从中导出 key id 和
   公钥，用它签 commit 和仓库 summary，并把公钥写进两个描述文件。
2. **GitHub Pages** 指向 `gh-pages` 分支（Settings → Pages → Source）。每次打标签，CI 会把
   OSTree 仓库、`me.him188.ani.flatpakref`、`me.him188.ani.flatpakrepo` 和一个图标推到该分支。
3. **允许 Actions 创建 PR**（Settings → Actions → General → Workflow permissions），
   否则每天的 upstream 检查开不了升级 PR。

发新版时改 manifest 的 `url`、`sha256`、`size`（顺手更新 metainfo 里的 `<release>`），提交、打标签。

```sh
git tag v6.2.0
git push origin v6.2.0
```

打标签前值得知道的两件事：

* **每一个发布的版本，用户都要重下约 337 MB。** 只要 commit 变了，flatpak 就会重新下载并重新
  应用 extra data，即使 manifest 里那条记录没变——`flatpak update -v` 每次都会打印
  `Loading …ani-6.2.0-linux-x86_64.appimage using curl`。所以改动要攒一起发，别为纯元数据打版本。
* 发布出去的仓库每版只有几百 KB（第一版是 380 KB），因为应用本体不在里面，GitHub Pages 绰绰有余。
  签名密钥丢了的话，所有用户都得用新密钥重新添加 remote，注意备份。

仓库里的两个描述文件是模板：`@PAGES_URL@` 和 `@GPGKEY@` 在发布时替换，仓库里不含任何密钥材料。

## 注意事项

* 这是**非官方**打包，上游不提供支持；有问题请提到本仓库。
* 应用本体在安装或更新时从 GitHub 下载，不由本仓库分发，所以每个发布的版本都是一次完整的
  约 337 MB 下载；上游若删除了某个 release 资产，新用户会装不上，直到 manifest 里的 sha256
  指向新的文件。
* 应用数据存放在 `~/.var/app/me.him188.ani/`，与系统里安装的 Animeko 完全隔离。
* 播放界面的截图按钮把 PNG 存到 `~/.var/app/me.him188.ani/data/ani/screenshots/`；
  flatpak 沙箱里的 `$HOME` 退出即销毁，所以 wrapper 在启动前把进程工作目录指到了那里，详见
  [技术说明](docs/packaging-notes.zh-CN.md#截图会落在工作目录里)。
* App ID 沿用上游自己的 `me.him188.ani`，依据见[技术说明](docs/packaging-notes.zh-CN.md)。
* CEF 在组件更新器运行时会让 unzip 工具进程崩溃；崩溃本身无害，但 KDE 的崩溃报告器会为此
  发通知，wrapper 现在让内核不再上报这些 abort，详见
  [已知问题](docs/packaging-notes.zh-CN.md)。

## 许可证

应用程序与图标素材来自 [open-ani/animeko](https://github.com/open-ani/animeko)，
采用 [AGPL-3.0](LICENSE.txt) 许可；本仓库仅提供打包配置。

## 相关链接

* [Animeko 官网](https://animeko.org/)
* [Animeko 源码](https://github.com/open-ani/animeko)
* [打包技术说明](docs/packaging-notes.zh-CN.md)
* [Releases](../../releases)
