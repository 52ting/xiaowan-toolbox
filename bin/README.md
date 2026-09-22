# bin/ —— 放这里的内置 ffmpeg

这个目录用来存放**随程序一起分发**的 ffmpeg 可执行文件。
放好之后重新打包，产物就会自带 ffmpeg，用户无需自行安装。

| 平台 | 需要放的文件 | 说明 |
| --- | --- | --- |
| Windows | `ffmpeg.exe`、`ffprobe.exe` | 见下方获取方式 |
| macOS | `ffmpeg`、`ffprobe` | `bash packaging/build_macos.sh` 会自动从 Homebrew 复制过来 |

放好后用 `python packaging/build_windows.py`（Windows）或
`bash packaging/build_macos.sh`（macOS）打包即可。

## Windows 获取 ffmpeg

任选一个来源，把解压出来的 `ffmpeg.exe` 与 `ffprobe.exe` 放进本目录：

- **BtbN 构建**（推荐，含最新编码器）
  <https://github.com/BtbN/FFmpeg-Builds/releases> → 下载 `ffmpeg-master-latest-win64-gpl.zip`

- **gyan.dev 构建**（体积较小）
  <https://www.gyan.dev/ffmpeg/builds/> → 下载 `ffmpeg-release-essentials.zip`

- **已有 ffmpeg 的话**，直接复制过来即可：

  ```powershell
  Copy-Item (Get-Command ffmpeg).Source .\bin\ffmpeg.exe
  Copy-Item (Get-Command ffprobe).Source .\bin\ffprobe.exe
  ```

## 不放会怎样

不影响功能，只是**打包出来的程序不带 ffmpeg**，运行时需要用户自己装了 ffmpeg
并加进 PATH（程序会自动去 PATH 与系统常见位置找）。

## 授权提醒

FFmpeg 遵循 LGPL / GPL。上面推荐的构建多为 **GPL** 版本，
如果你要把内置了 ffmpeg 的程序对外分发，请一并遵守对应许可（提供源码获取方式等）。
