# Packaging notes

For readers who need to audit or change this packaging. For installation and
usage see the [README](../README.md).

## Known issue: CEF's unzip utility aborts during playback

The CEF build bundled upstream (Chrome 137) aborts its `unzip.mojom.Unzipper`
utility process with `*** stack smashing detected ***` every time Chromium's
component updater unpacks a downloaded component. It reproduces with CEF's own
minimal example on plain Ubuntu, so it is not caused by this sandbox — see
[chromiumembedded/cef#3912](https://github.com/chromiumembedded/cef/issues/3912).
The component updater runs in the background, which is why the burst shows up
during playback.

The mechanism is the stack canary, and it takes three facts to line up:

* CEF launches its processes through a Chromium zygote and passes that zygote
  `--change-stack-guard-on-fork=enable` (visible in `ps`);
* Chromium's content layer honours the flag and calls
  `base::ResetStackCanaryIfPossible()` in every forked child (`RunZygote()` in
  `content/app/content_main_runner_impl.cc`), deliberately changing the canary
  behind every stack frame inherited from the zygote;
* CEF's own `CefExecuteProcess()`, which wraps `content::ContentMain()` and is
  therefore still on the stack across that fork, is not built with
  `NO_STACK_PROTECTOR` the way Chromium's own fork path is.

The first time such a child unwinds out of `CefExecuteProcess()` the canary
check fails and `__stack_chk_fail` calls `abort()`. That is why it shows up on
short-lived children — the updater's Unzipper — and not on the renderer, GPU and
network processes, which only reach the same check when they exit.

Functionally it is harmless: the updater retries and the components do end up
installed. The cost is the crash reporting. Every SIGABRT writes a ~110 MB core
dump, and every abort produces a systemd-coredump journal record that KDE's
drkonqi acts on — one observed burst was 24 aborts in 53 seconds, i.e. 2.6 GB of
dumps and 24 crash dialogs on the Plasma version that was current then.

Disabling the component updater would be the obvious way out, and it is not
available from here. CEF knows `--disable-component-update`, but JCEF never
gives CEF the process command line on Linux: `Context::Initialize()` in
java-cef's native code passes `CefMainArgs(0, nullptr)`, and Chromium's
`CommandLine::Init(0, nullptr)` builds an empty `base::CommandLine` from that.
The only switches CEF sees are the ones `CefSettings` and the application itself
append, so a switch on `flatpak run`'s command line never reaches the updater.

What does work is keeping the kernel from reporting the abort at all.
`ani-wrapper` used to set `RLIMIT_CORE` to 0, and that is not enough: the kernel
consults the limit only for file dumps, so with the systemd pipe pattern
(`|/usr/lib/systemd/systemd-coredump %P …`) it still runs systemd-coredump, which
logs the abort and hands it to drkonqi. With the drkonqi shipped on Plasma 6.7.5
a notification is raised from that journal record alone — which is exactly the
symptom: a crash notification whose "Details" button opens an empty report,
because no core was ever written.

A limit of exactly **1 byte** behaves differently. It is the kernel's sentinel
for a recursive dump: `coredump_pipe()` in `fs/coredump.c` checks
`cprm->limit == 1` and gives up before the pipe helper is even started, so the
abort is never reported to anyone. `ulimit` cannot express 1 — its unit is
blocks — so the wrapper calls `prlimit --core=1:1 --pid $$`. Verified with a
crash inside the sandbox:

```
kernel: coredump: 4(probe-sb): RLIMIT_CORE is set to 1, aborting core
```

and, in the same window, no `systemd-coredump` unit, no new entry in
`coredumpctl` and no `drkonqi-coredump-*` unit. Set `ANIMEKO_FLATPAK_KEEP_CORES=1`
to skip all of this and keep cores when debugging:

```sh
flatpak run --env=ANIMEKO_FLATPAK_KEEP_CORES=1 me.him188.ani
```

Dumps collected before this was in place belong to root and have to be removed
with `sudo`:

```sh
sudo rm -f /var/lib/systemd/coredump/core.jcef_helper.*
```

A real fix has to come from upstream: `NO_STACK_PROTECTOR` on CEF's fork path
(what #3912 asks for), or a way to turn the component updater off without the
command line.

## Sleep inhibition needs an explicit D-Bus grant

While a video plays, Animeko inhibits sleep and the screen saver through two
paths: it shells out to `systemd-inhibit` (logind, system bus) and it calls the
`org.freedesktop.ScreenSaver` D-Bus interface. The first came with
[#2773](https://github.com/open-ani/animeko/pull/2773), the second with
[#2925](https://github.com/open-ani/animeko/pull/2925).

Only the second can work here. The GNOME runtime ships no `systemd-inhibit`
binary and the app gates that path on the binary being on `PATH`, so it never
runs. The D-Bus path was failing for a different reason: the session bus proxy
treats `org.freedesktop.ScreenSaver` as an ungranted name, so `Inhibit` failed
silently and the machine suspended mid-playback.

Granting that one name fixes it:

```yaml
- --talk-name=org.freedesktop.ScreenSaver
```

`--system-talk-name=org.freedesktop.login1` is deliberately **not** granted. It
is what the `systemd-inhibit` path would need, and granting it does make
`login1.Manager.Inhibit` work, but since that path cannot run it would only widen
the sandbox: it would be the sole system bus access this package requests,
exposing logind (`PowerOff`, `Reboot`, `Suspend`, ...) for nothing.

Verified on Plasma 6 by watching the call on the session bus and by the power
management panel listing the inhibitor:

```
method call ... interface=org.freedesktop.ScreenSaver; member=Inhibit
   string "Animeko"
   string "Playing video"
```

```
[ScreenSaver] D-Bus inhibit cookie: 951
[ScreenSaver] Inhibited via D-Bus org.freedesktop.ScreenSaver
```

The power management panel ("Prevent automatic locking and sleeping") then shows
`Animeko is currently inhibiting the screen locker. (Playing video)`.

## Why the AppImage does not start as shipped

The AppImage is a jpackage app-image (JetBrains Runtime 21). Its native launcher
reads `usr/lib/app/Ani.cfg`, expands the `$APPDIR` macro in the 274
`app.classpath` lines and hands the result to the JVM.

That classpath is about 37 KB once expanded, which the launcher's
`JvmlLauncherData` buffer handling does not survive. Running the AppImage
unmodified:

```
$ JPACKAGE_DEBUG=true ./ani-6.1.0-linux-x86_64.appimage
[TRACE] JvmLauncher.cpp:318: Need 38457 bytes for JvmlLauncherData buffer
[TRACE] JvmLauncher.cpp:315: Initialized 38457 bytes at 0x56268b98d870 address
[21575]: jli arg[0]: [.../usr/bin/Ani]
[21575]: jli arg[1]: [-classpath]
[21575]: jli arg[2]: [.../core-common-2.2.0-2212d240dfe1e8a7598ee117ccc316d]
[21575]: jli arg[3]: []          <- every remaining argument is empty
...
[21575]: jli arg[19]: []
[21575]:                          <- dies here
Segmentation fault (core dumped)     # exit 139
```

Measured against the real classpath:

| | bytes |
|---|---|
| expanded classpath (274 jars) | 37074 |
| written before the truncation | **5306** |

Only the first 39 of 274 entries make it in, and the environment-variable pointer
array that follows the strings in the same buffer is overwritten with path data.
`jvmLauncherStartJvm()` then calls `setenv()` with a garbage `name` pointer:

```
(gdb) run
Program received signal SIGSEGV, Segmentation fault.
0x00007ffff7db8291 in setenv () from /lib64/libc.so.6
#0  setenv ()
#1 jvmLauncherStartJvm ()
#2 main ()
$rdi = 0xfffffffffffc97b0     <- invalid `name` argument
```

The JVM is never started. The crash reproduces with an empty environment, an
isolated `$HOME`, a short path and from the SquashFS mount, so it is a property
of the AppImage rather than of a particular host.

## How this package works around it: a bootstrap jar

`make-bootstrap.py` rewrites `Ani.cfg` so the launcher only ever sees a single
classpath entry, and moves the jar list into a `Class-Path` manifest attribute:

```
app.classpath=$APPDIR/animeko-bootstrap.jar
```

The bootstrap jar contains nothing but `META-INF/MANIFEST.MF`:

```
Manifest-Version: 1.0
Class-Path: desktop-6.1.0.jar ComposeNativeTray-jvm-5ae5e328b757486c3993
 dc64b8be7a5.jar aboutlibraries-compose-jvm-9cd3d26643d8633a3b84932014c4c7c4.jar
 ...
Main-Class: me.him188.ani.app.desktop.AniDesktop
```

The JVM's own class loader resolves the listed jars, so the launcher's buffer
stays small (`Need 1477 bytes`) and the upstream jar order is preserved. Order
matters here: a few libraries are present in two versions and the first classpath
entry wins.

The script drops `Ani.cfg` entries whose file does not exist. Upstream's packaging
unpacks three runtime jars (`mediamp-mpv-runtime-linux-x64`,
`mediamp-ffmpeg-runtime-linux-x64`, `anitorrent-native-desktop-*-linux-x64`) into
`native/` and deletes the jars themselves, without updating `Ani.cfg` to match.

## The AppImage arrives at install time

The manifest declares the AppImage as an `extra-data` source instead of a build
input:

```yaml
- type: extra-data
  filename: ani.appimage
  url: https://github.com/open-ani/animeko/releases/download/v6.1.0/ani-6.1.0-linux-x86_64.appimage
  sha256: abeeab01daf4a08ab1cd7c4d9c6999b1741a577d93ad7e2ef28b80ea12296aa7
  size: 336849400
```

flatpak-builder records that entry in the app metadata and nothing else. The
AppImage is not downloaded by CI, is not part of the OSTree commit and is not in
a `.flatpak` bundle: what this repository builds is ~100 kB, and the app itself
is fetched from the upstream release on the user's machine when the app is
installed, and again on every update that changes the entry. flatpak verifies
the sha256 against the manifest before it does anything else.

The reason is hosting cost. Packing the AppImage into the commit would put about
300 MB per version into the repository, and every update would be served from
here. The price is on the user's side: each upstream release is a full 337 MB
download (a normal OSTree repo would only send the objects that changed), and
since the download happens on the user's machine, an upstream asset that is
deleted or replaced breaks new installs while CI stays green.

### What apply_extra runs in

`/app/bin/apply_extra` is executed by flatpak while installing, not by a build
sandbox, and the sandbox it gets is much more restricted than what an app gets at
runtime:

| | |
|---|---|
| network, D-Bus, host access | none |
| writable | `/app/extra` only, which is also the working directory |
| `/proc` | **not mounted at all** |
| `/usr` | the app's runtime (`org.gnome.Platform`), read-only |
| uid | the installing user, or root via the system helper |

flatpak builds that sandbox in `apply_extra_data()` (flatpak-dir.c) with
`FLATPAK_RUN_FLAG_NO_PROC`, deliberately: a script that may run as root must not
be able to reach outside files through `/proc/self/exe`.

That last row is what breaks the obvious implementation. The AppImage runtime
finds its own embedded SquashFS through `/proc/self/exe`; without it, it prints

```
Cannot open /proc/self/exe: No such file or directory
Failed to get fs offset for /proc/self/exe
```

and exits 127 without reading a byte. Reproduced verbatim by running the
AppImage in a hand-built sandbox with the same properties. The runtime has an
override for exactly this case: `TARGET_APPIMAGE` names the file to read instead,
and `apply_extra` sets it to `/app/extra/ani.appimage`. `--appimage-extract` then
needs no FUSE and writes `squashfs-root/` in the working directory.

Two smaller details the script handles:

* flatpak does not mark extra data executable, so it `chmod +x`es the file
  first;
* `python3` comes from the runtime at `/usr/bin/python3` (Python 3.13 in
  org.gnome.Platform 49), which is why the bootstrap step could stay a Python
  script instead of being rewritten in shell.

After unpacking, the tree is moved to `/app/extra/ani` and `make-bootstrap.py`
patches it, exactly as it used to do at build time - it just runs on the user's
machine now, once per installed version.

To iterate on `apply_extra` without installing over and over, the same sandbox
can be reproduced by hand:

```sh
bwrap --unshare-all --ro-bind $RUNTIME_FILES /usr --ro-bind $BUILD_DIR/files /app \
      --bind $SOMEDIR /app/extra --symlink usr/bin /bin --symlink usr/lib /lib \
      --symlink usr/lib64 /lib64 --dev /dev --tmpfs /tmp \
      --chdir /app/extra --cap-drop ALL --setenv PATH /app/bin:/usr/bin \
      -- /app/bin/apply_extra
```

## Publishing and the update channel

The `gh-pages` branch carries an OSTree repository, which is what `flatpak
update` pulls from. CI updates it on every tag: the build is signed
(`--gpg-sign`), `flatpak build-update-repo --gpg-sign --generate-static-deltas`
refreshes the summary and the deltas, and the repository plus the two
descriptors are pushed. `@PAGES_URL@` and `@GPGKEY@` in
`me.him188.ani.flatpakref.in` / `me.him188.ani.flatpakrepo.in` are substituted at
that point.

Because the application is an extra-data source, the repository holds metadata
only:

| | |
|---|---|
| first version | 380 KB |
| each version after that | ~100 KB (a new commit and its delta) |
| what a user's update costs | ~100 KB from here, then 337 MB of payload from upstream GitHub |

### Every published commit costs the user 337 MB

Measured while implementing this, with `flatpak update -v`:

```
F: Loading https://github.com/open-ani/animeko/releases/download/v6.1.0/ani-6.1.0-linux-x86_64.appimage using curl
F: extracting extra data
F: Running /app/bin/apply_extra
```

Even when the new commit carries an *identical* extra-data record (same
url/sha256/size), flatpak downloads the file again and re-runs `apply_extra`. A
release that only changes the desktop file therefore still costs every user
337 MB - batch changes instead of publishing often.

### No bundles

The bundle that used to be attached to releases cannot be built for this package
any more: an extra-data record lives in the commit's detached metadata, and
`flatpak build-bundle` does not put it into the bundle, so installing one fails
with

```
错误：安装捆绑包 me.him188.ani 失败：分离的元数据中缺少额外的数据
Error: Failed to install bundle me.him188.ani: Extra data missing in detached metadata
```

Measured against a signed repository built from this manifest. The repository is
the only install path.

### The key

One signing key (`GPG_PRIVATE_KEY`) is enough; CI exports the public half from it
on every publish. Losing the private key means every user has to add the remote
again, so back it up outside the repository. Rotating means: replace the secret,
publish, and have users `flatpak remote-delete animeko` before installing from
the new flatpakref.

## App id

The Flatpak id is `me.him188.ani`, taken from the application id upstream already
uses for this app:

| where | value |
|---|---|
| Android `applicationId` (`app/android/build.gradle.kts`) | `me.him188.ani` |
| macOS `CFBundleURLName` / URL scheme | `me.him188.ani` / `ani` |
| Java package root (main class `me.him188.ani.app.desktop.AniDesktop`) | `me.him188.ani` |
| Data dir (`ProjectDirectories.from("me", "Him188", "Ani")`) | `~/.local/share/ani` |

Flatpak ids are reverse-DNS names, and `open-ani/animeko` is a GitHub org and
repository name that carries no domain. Upstream also owns `animeko.org` and
`openani.org`; to use one of those instead, replace `me.him188.ani` throughout
(app-id, file names, icon names, the metainfo `<id>`), which moves the sandbox
data directory to `~/.var/app/org.openani.Animeko/`.

## Icon

Upstream leaves `iconFile` commented out for Linux in
`app/desktop/build.gradle.kts`:

```kotlin
linux {
    shortcut = true
    packageName = "animeko"
//    iconFile.set(file("icons/a_1024x1024_rounded.ico"))
}
```

jpackage therefore falls back to Compose Multiplatform's default icon for its
icon slot, and `usr/lib/Ani.png` (1024×1024) is the Kotlin logo.

The AppImage carries the real Animeko icon elsewhere. The image files it contains
are:

```
icon.png                                   # .DirIcon, 512x512, the app icon
usr/lib/Ani.png                            # jpackage icon slot, Kotlin default
usr/lib/app/resources/icon.png             # same file as the root icon.png
usr/lib/app/desktop-6.1.0.jar
  └── composeResources/.../drawable/a_round.png   # in-app window/tray icon, 192x192
```

This package installs the root `icon.png`: the 512×512 entry is that file
verbatim (same md5) and 128/256 are Lanczos downscales of it.
`icons/appimage-icon.png` keeps a copy of the source so the derivation can be
reproduced and checked without unpacking 780 MB of AppImage:

```sh
magick icons/appimage-icon.png -filter Lanczos -resize 256x256 icons/me.him188.ani-256.png
```

Upstream's `app/desktop/icons/a_512x512.icns` holds the same logo at 1024×1024,
but with macOS safe-area padding, which makes it render visibly smaller on a
Linux desktop.

## Layout

```
/app/bin/ani              wrapper script (the Flatpak command)
/app/bin/apply_extra      run by flatpak at install and update time
/app/libexec/make-bootstrap.py
/app/share/...            desktop file, metainfo, icons
/app/extra/ani.appimage   exists only while apply_extra runs; it deletes it
/app/extra/ani/           unpacked AppImage tree (usr/bin/Ani, usr/lib/...)
/app/extra/ani/usr/lib/app/            jpackage application dir: Ani.cfg, jars,
                                       native/, resources/
/app/extra/ani/usr/lib/app/animeko-bootstrap.jar
```

Everything under `/app/extra` is what flatpak downloaded and `apply_extra`
produced; everything else is the ~100 kB this repository builds. The app resolves
all its paths relative to `usr/lib/app`, so that tree is kept byte-for-byte
identical to the AppImage apart from `Ani.cfg` and the new bootstrap jar.

## Sandbox details

### X11 is required

JCEF hardcodes `--ozone-platform=x11`: Chromium's Wayland backend crashes the CEF
browser process before it reaches the INITIALIZED state (upstream's comment says
"Force the X11 (XWayland) backend, which is stable"), and Skiko/AWT also render
through XWayland. That is why the manifest asks for both `--socket=x11` and
`--socket=wayland` instead of the usual `--socket=fallback-x11`, which would not
provide X11 on a Wayland session and would leave JCEF unable to start.

### JCEF runs without Chromium's own sandbox

Chromium's sandbox needs either a setuid `chrome-sandbox` (impossible in Flatpak)
or unprivileged user namespaces, which Flatpak's seccomp policy blocks:

```
$ flatpak run --command=sh org.gnome.Platform//49 -c 'unshare -U true'
unshare: unshare failed: Operation not permitted
```

CEF detects this and runs its helper processes with `--no-sandbox` by itself,
which is visible in the process list:

```
/app/extra/ani/usr/lib/runtime/lib/jcef_helper --type=gpu-process --no-sandbox ...
```

JCEF still reaches `INITIALIZED`, so the in-app browser used for Bangumi login
works. The wrapper does not pass `--no-sandbox`; this fallback is CEF's own. The
renderer loses its inner sandbox but stays confined by Flatpak's sandbox.

### Tray icon

Compose's tray helper (`libLinuxTray.so`, sd-bus based) registers
`org.kde.StatusNotifierItem-<pid>-1`. Flatpak cannot express a wildcard for that
hyphenated name — `--own-name` wildcards must be of the form `.*`, and a hyphen is
not a dot — but the sandbox always runs the app as PID 2, so
`--own-name=org.kde.StatusNotifierItem-2-1` covers it. Confirmed from inside the
sandbox:

```
$ flatpak run --command=gdbus me.him188.ani call --session \
    --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
    --method org.freedesktop.DBus.RequestName org.kde.StatusNotifierItem-2-1 0
(uint32 1,)          # PRIMARY_OWNER
```

The tray is only created once the window is hidden to the tray, so nothing
appears at startup.

## Verified

```
Ani started. platform: Linux x86_64, version: 6.1.0, isDebug: false
dataDir: file://~/.var/app/me.him188.ani/data/ani
Anitorrent is loaded.          <- bundled libtorrent, GLIBC_2.38 symbols resolved
FFmpegKit is loaded.           <- bundled FFmpeg
mediampv is loaded.            <- bundled libmpv
JCEF is initialized.           <- in-app browser
Using bundled video enhancement shaders from /app/extra/ani/usr/lib/app/resources/anime4k
```

Every shared library dependency of `Ani`, `libskiko`, `libmediampv`,
`libanitorrent`, `libcef`, `libjvm` and `libmpv` resolves inside the sandbox with
nothing missing, and the app stays up on its own without crashes or fatal log
lines.
