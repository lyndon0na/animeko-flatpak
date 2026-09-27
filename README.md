# Animeko Flatpak Packaging

Build configuration that repackages the official Linux AppImage as a Flatpak.

**English** | [简体中文](README.zh-CN.md) | [Packaging notes](docs/packaging-notes.md)

[![build](https://github.com/lyndon0na/animeko-flatpak/actions/workflows/build.yml/badge.svg)](https://github.com/lyndon0na/animeko-flatpak/actions/workflows/build.yml)

## App info

| | |
|---|---|
| Name | Animeko (Ani) |
| Version | 6.2.0 |
| App ID | `me.him188.ani` |
| Runtime | `org.gnome.Platform` 49 |
| Architecture | x86_64 |
| Upstream source | https://github.com/open-ani/animeko |
| Homepage | https://animeko.org/ |
| App payload | the upstream `ani-6.2.0-linux-x86_64.appimage`, declared as an [extra-data](https://docs.flatpak.org/en/latest/module-sources.html#extra-data) source: flatpak downloads it on the machine where the app is installed, verifies its sha256, and `apply_extra` unpacks and patches it there |

## Installation

### From the repository

```sh
flatpak install --user https://lyndon0na.github.io/animeko-flatpak/me.him188.ani.flatpakref
```

This adds the remote as well, so later versions arrive through `flatpak update`
(or through the desktop's software centre). Installing downloads the ~337 MB
AppImage from GitHub, which is where the application itself comes from.

### Build locally

```sh
# dependencies
sudo dnf install flatpak flatpak-builder      # Fedora
sudo apt install flatpak flatpak-builder      # Debian / Ubuntu

# Flathub, plus the GNOME runtime and SDK
flatpak remote-add --if-not-exists --user flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install --user flathub org.gnome.Platform//49 org.gnome.Sdk//49

# clone and build
git clone https://github.com/lyndon0na/animeko-flatpak.git
cd animeko-flatpak
flatpak-builder --user --install --force-clean --repo=repo build me.him188.ani.yaml
```

Building needs no AppImage: it is an extra-data source, so the build only uses
the runtime and the SDK. The AppImage is pulled on the machine where the app is
installed, from the upstream release URL, checked against the sha256 recorded in
the manifest.

### Why there is no bundle

A bundle cannot carry the extra-data record: it lives in the commit's detached
metadata, which `flatpak build-bundle` does not include, so installing one fails
with `Extra data missing in detached metadata`. The repository published over
GitHub Pages is therefore the only install path.

## Run

```sh
flatpak run me.him188.ani
```

## Uninstall

```sh
flatpak uninstall --user me.him188.ani
```

Flatpak will ask whether to delete the app data in `~/.var/app/me.him188.ani/` as
well.

## Permissions

| Permission | Purpose |
|---|---|
| `--share=network` | Online video sources, BitTorrent, danmaku and Bangumi APIs |
| `--share=ipc` | X11 shared memory |
| `--socket=x11` | JCEF forces the X11 backend; Skiko / AWT render through XWayland too |
| `--socket=wayland` | Wayland support, alongside X11 for the reason above |
| `--socket=pulseaudio` | Audio playback |
| `--device=dri` | GPU rendering and VA-API hardware decoding |
| `--talk-name=org.kde.StatusNotifierWatcher` | System tray |
| `--own-name=org.kde.StatusNotifierItem-2-1` | The tray icon's own D-Bus name |
| `--talk-name=org.freedesktop.Notifications` | Notifications |
| `--talk-name=org.freedesktop.ScreenSaver` | Inhibit the screen saver and sleep while a video plays |

No host filesystem access is requested. Everything the app stores by default -
the media download folder, the media cache, the database and the logs - lives in
`~/.var/app/me.him188.ani/`, which the sandbox always provides. Pointing the app
at a real path therefore needs a grant too: the folder picker will happily show
you `~/Videos`, but the app cannot read or write it until you allow it.

```sh
flatpak override --user --filesystem=xdg-videos me.him188.ani
```

Use `--filesystem=home` for the whole home directory, or do the same per app in
Flatseal. The manifest lists the alternatives in a comment, in increasing order
of width.

## Files

| File | Purpose |
|---|---|
| `me.him188.ani.yaml` | Flatpak manifest (GNOME runtime 49) |
| `apply_extra` | runs at install and update time: unpacks the AppImage and patches it |
| `make-bootstrap.py` | rewrites `Ani.cfg` and generates the bootstrap jar; executed by `apply_extra`, see the [packaging notes](docs/packaging-notes.md) |
| `ani-wrapper` | `/app/bin/ani` entry point |
| `me.him188.ani.desktop` | launcher entry |
| `me.him188.ani.metainfo.xml` | AppStream metadata |
| `icons/me.him188.ani-*.png` | installed icons (128 / 256 / 512) |
| `icons/appimage-icon.png` | source of those icons, taken from the AppImage's own `icon.png` |
| `me.him188.ani.flatpakref.in` | template for the one-line install, filled in and published by CI |
| `me.him188.ani.flatpakrepo.in` | same, for adding the remote by hand |
| `.github/workflows/build.yml` | CI: builds, verifies, publishes the repository and the release |
| `.github/workflows/upstream.yml` | daily upstream check, opens a bump pull request |
| `docs/` | [Packaging notes](docs/packaging-notes.md) |
| `LICENSE.txt` | AGPL-3.0 license |

## CI

`.github/workflows/build.yml`:

* a push to `main` or a manual dispatch builds, installs and verifies the
  deployed tree;
* a `v*` tag additionally signs the build, updates the published repository and
  creates a GitHub release;
* `.github/workflows/upstream.yml` runs daily: when open-ani/animeko publishes a
  newer release it opens a pull request that points the manifest at it. Merging
  that pull request does not publish anything - the tag does.

The runner is headless, so the GUI smoke test runs under Xvfb as a best-effort
step that reports without failing the build. The structural checks are gating.

```sh
git tag v6.2.0
git push origin v6.2.0
```

## Publishing

A tag publishes the repository, and that needs two things set up once:

1. **`GPG_PRIVATE_KEY`** as a repository secret: the ASCII-armoured private key
   of the signing key. CI derives the key id and the public key from it, signs
   the commit and the repository summary, and writes the public key into the
   descriptors.
2. **GitHub Pages** serving the `gh-pages` branch (Settings → Pages → Source).
   Every tag pushes the OSTree repository, `me.him188.ani.flatpakref`,
   `me.him188.ani.flatpakrepo` and an icon to that branch.
3. **Pull requests from Actions** allowed (Settings → Actions → General →
   Workflow permissions), otherwise the daily upstream check cannot open its
   bump pull request.

To release a new version, edit `url`, `sha256` and `size` in the manifest (and
the `<release>` entry in the metainfo), commit, and tag.

Two things worth knowing before you tag:

* **Every published commit costs each user a full ~337 MB download.** flatpak
  re-downloads and re-applies extra data whenever the commit changes, even when
  the extra-data record did not - `flatpak update -v` prints
  `Loading …ani-6.2.0-linux-x86_64.appimage using curl` every time. Batch your
  changes; do not cut metadata-only releases.
* The published repository is a few hundred kilobytes per version (the first one
  was 380 KB), because the application is not in it - GitHub Pages is plenty.
  Losing the signing key means every user has to add the remote again with the
  new key, so keep a backup.

The descriptors committed here are templates: `@PAGES_URL@` and `@GPGKEY@` are
substituted at publish time, so no key material is in the repository.

## Notes

* This is **unofficial** packaging. Upstream does not support it; please file
  issues against this repository.
* The app is downloaded from GitHub when you install or update, not shipped by
  this repository, so every published version is a fresh ~337 MB download. If
  upstream ever deletes a release asset, new installs break until the sha256 in
  the manifest is pointed at a new one.
* App state lives in `~/.var/app/me.him188.ani/`, fully separate from a
  system-installed Animeko.
* The App ID is upstream's own `me.him188.ani`; the reasoning is in the
  [packaging notes](docs/packaging-notes.md).
* CEF aborts its unzip utility process each time Chromium's component updater
  runs. The abort is harmless, but KDE's crash reporter notified about it, so
  the wrapper keeps the kernel from reporting those aborts — see the
  [known issue](docs/packaging-notes.md#known-issue-cefs-unzip-utility-aborts-during-playback).

## License

The application and the icon artwork come from
[open-ani/animeko](https://github.com/open-ani/animeko) and are licensed
[AGPL-3.0](LICENSE.txt). This repository only provides the packaging
configuration.

## Links

* [Animeko homepage](https://animeko.org/)
* [Animeko source](https://github.com/open-ani/animeko)
* [Packaging notes](docs/packaging-notes.md)
* [Releases](../../releases)
