# EchoShift · 音频转 MP3（含 QQ 音乐加密格式解密）

Windows 桌面音频转换工具：把 ffmpeg 能读的**任何音频/视频**，以及 **QQ 音乐加密容器**
（MFLAC / MGG / QMC）转成 **MP3**，并且能精细控制 **比特率** 与 **采样率**。

界面是深色主题的 Tkinter，转码后端是内置的 ffmpeg + libmp3lame，**用户端零依赖**：解压即可用，
不需要另外装 Python、ffmpeg 或任何运行库。

> **用途与合规**：本项目用于把**你自己合法持有**的音频文件在本地做格式转换
> （例如把无损文件压成车载能放的 MP3）。它不提供、不获取、不绕过任何内容授权，
> 不读取 QQ 音乐客户端的登录凭据，也不会自动向任何服务器索取密钥；
> 仓库**不附带任何密钥**。请自行确认你的使用符合所在地区的法律与相关服务条款。
> 详见[许可与合规](#许可与合规)。

![运行中](docs/busy.png)

<p align="center">
  <img src="docs/idle.png" width="49%" alt="空队列" />
  <img src="docs/done.png" width="49%" alt="转换完成" />
</p>

左侧是常用设置，高级设置默认收起；右侧队列带**行内进度条**、状态胶囊与状态色。
选中条目后，队列下方固定显示两行摘要，点击可展开完整路径、参数、警告、错误和输出路径。
这里不再使用文件信息悬浮窗。失败条目会直接显示原因，并自动展开日志。

---

## 使用方式

EchoShift 只有**一个入口：`EchoShift.exe`**，不需要 Python、不需要 ffmpeg、不需要任何运行库。

1. 打开 [Releases](https://github.com/k1anyang/EchoShift/releases/latest)，下载
   `EchoShift-<版本>-win64.zip`。
2. 解压到**任意可写目录**（整个文件夹一起，不要只把 `EchoShift.exe` 拖出来）。
3. 双击 `EchoShift.exe`。免安装、免管理员权限；整份文件夹拷到别的 Windows 机器也能直接跑。

首次使用建议先拖一两个文件试一次，确认输出目录和参数符合预期，再整目录批量转换。

<details>
<summary>想从源码运行 / 自己打包（开发用）</summary>

仓库已包含 `vendor/ffmpeg`，克隆下来就能跑：

```powershell
git clone https://github.com/k1anyang/EchoShift.git
cd EchoShift
.\launch_gui.pyw                   # 图形界面（无控制台窗口）
```

或者不借助任何脚本（需要 Python 3.10+）：

```powershell
$env:PYTHONPATH = "src"
pythonw -m echoshift.gui.app       # 或者 python -m echoshift.gui.app
```

自己打包成便携 EXE：

```powershell
pip install pyinstaller
.\build.ps1                 # 先跑测试，再生成 dist\EchoShift\EchoShift.exe
.\build.ps1 -OneFile        # 改成自解压单文件（启动慢几秒）
.\build.ps1 -SkipTests      # 跳过测试
```

默认产出**文件夹**布局：内置 ffmpeg 的 DLL 有约 50 MB，单文件版每次启动都要把它们解压一遍。
产物内含 ffmpeg 与 Python 运行时，整份 `EchoShift` 文件夹拷到别的 Windows 机器即可运行。

> 换成自备的 ffmpeg 时跑 `tools\vendor_ffmpeg.ps1`：默认从 BtbN 的 `gpl-shared` 构建下载并校验
> `libmp3lame`，也可以 `-SourceDir "C:\path\to\ffmpeg\bin"` 从本机已有安装目录复制。
> 没有 ffmpeg 时界面仍会启动，但会提示「ffmpeg 不可用」。
>
> 想要桌面/开始菜单快捷方式：`.\tools\make_shortcut.ps1 -Destination "$env:USERPROFILE\Desktop"`。
> 它记录的是**生成时**的绝对路径，挪动文件夹后重新生成即可。

</details>

---

## 能做什么

**输入**

| 类型 | 扩展名 | 是否需要密钥 |
| --- | --- | --- |
| 普通音频/视频 | 见下方说明 | 否 |
| QMC1 加密容器 | `.qmcflac` `.qmc0` `.qmc3` `.qmcogg` | **否**，算法自带 |
| QMC2 加密容器（QTag / V1 尾部） | `.mflac` `.mflac0` `.mflach` `.mgg` `.mgg0` `.mgg1` `.mggl` | 否，自动提取 |
| QMC2 加密容器（musicex 尾部） | 同上 | **是**，需外部 ekey |

关于「普通音频/视频」的范围：

> **ffprobe 才是权威，扩展名只是提示。**
> 判据不是文件名，而是 ffmpeg 能不能读懂内容。
>
> - **你显式添加的文件**（拖拽、文件对话框、命令行指名）**一律会尝试**。
>   `song.xyz`、没有扩展名、扩展名写错的文件，只要内容是 ffmpeg 认识的音频，就能转。
> - **扫描文件夹时**才做过滤，否则音乐目录里的 `.jpg` / `.cue` / `.log` 会被一起丢进队列。
>   过滤条件是「扩展名在白名单里」**或**「文件头字节是已知媒体签名」，
>   所以改了名的音频照样会被捡起来。
>
> 内置白名单覆盖常见音频（flac / wav / aiff / ape / wv / tta / dsf / mp3 / m4a / m4b /
> aac / ogg / oga / opus / wma / ac3 / dts / amr / caf / midi …）与常见视频容器
> （mp4 / mkv / webm / mov / avi / flv / ts …，会抽取第一条音轨）。
> 完整列表见 `src/echoshift/qmc/decoder.py` 里的 `PLAIN_AUDIO_EXTENSIONS`
> 与 `VIDEO_CONTAINER_EXTENSIONS`。

**功能**

- 批量队列，支持拖拽文件/文件夹（递归），逐项与总体进度
- 比特率三档控制：**VBR**（LAME `-q 0–9`）、**ABR**、**CBR**（8–320 kbps）
- 采样率 8 k–48 k 九档可选，或「保持原样」；源采样率非法时自动降到最近的合法值并提示
- 声道处理：保持原样 / 下混单声道 / 强制立体声；多声道源自动下混
- 标签与封面完整迁移（ID3v2.4 或 2.3，内嵌 APIC 封面）
- 输出命名模板（`{artist}/{album}/{track:02d} {title}.mp3` 之类）
- 同名文件策略：自动改名 / 覆盖 / 跳过
- 失败自动重试、转换后校验（可解码性、时长比对、采样率/声道/码率核对）
- ffmpeg 连续 120 秒没有进度会自动终止并重试；停止与关闭窗口不会阻塞界面线程
- 先写隐藏的 `.part.mp3`，校验成功后原子替换，失败或取消不会留下残缺 MP3
- 候选 ekey 来源：界面手填、同名 `.ekey` 文件、JSON 密钥库

---

## 编码参数详解

### 三种码率模式

| 模式 | ffmpeg 参数 | 说明 | 适合 |
| --- | --- | --- | --- |
| **VBR** | `-q:a N`（N=0–9） | 质量恒定、码率浮动。N 越小越好 | 音乐库长期保存（默认 `q2`） |
| **ABR** | `-b:a Nk -abr 1` | 平均码率，体积可控 | 想要体积稳定又想省空间 |
| **CBR** | `-b:a Nk` | 恒定码率，兼容性最好 | 老播放器、车载音响 |

LAME 的 VBR 质量档位参考（软件里也会显示）：

| q | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 约 kbps | 245 | 225 | 190 | 175 | 165 | 130 | 115 | 100 | 85 | 65 |

### 采样率与码率的合法组合

MP3 不是随便配的：**可用码率区间取决于采样率所属的 MPEG 版本**。

| MPEG 版本 | 采样率 | 合法码率 |
| --- | --- | --- |
| MPEG-1 | 32000 / 44100 / 48000 Hz | 32 – 320 kbps |
| MPEG-2 | 16000 / 22050 / 24000 Hz | 8 – 160 kbps |
| MPEG-2.5 | 8000 / 11025 / 12000 Hz | 8 – 160 kbps |

超出范围的组合（例如「22050 Hz + 320 kbps」）会被**直接拒绝并给出可选项**，而不是悄悄改参数。
界面里的码率下拉框也会跟着采样率自动收窄。

### 采样率与声道是「按文件」决定的

- 选「保持原样」时，96 kHz / 192 kHz 这类 MP3 不支持的高采样率源会自动降到 48 kHz，并在日志里提示。
- 源文件是 5.1 等多声道时，「保持原样」会自动下混成立体声并提示；想强制单声道请选「单声道」。

---

## MFLAC / QMC 支持情况

QQ 音乐用两代加密方案，本项目在 `src/echoshift/qmc/` 下完整实现了它们：

- **QMC1**（`.qmcflac` / `.qmc0` / `.qmc3` / `.qmcogg`）：基于固定种子的 XOR 掩码，
  **不需要任何密钥**，开箱即用。
- **QMC2**（`.mflac` / `.mgg` 系列）：需要 ekey，并且按解码后密钥长度自动选择两种算法之一：
  - 密钥 ≤ 300 字节 → **Map** 密码
  - 密钥 > 300 字节 → **改进版 RC4**，每 5120 字节重新派生一次

  ekey 本身还可能是 TC-TEA（腾讯魔改 TEA）加密封装的，实现里也一并处理了。

### ekey 从哪来

解码器**只使用**以下三类来源，按优先级排列：

1. **文件内嵌**（QTag 或 V1 尾部）——优先级最高，因为它是可验证正确的那个。
   传入的手填 ekey 不会覆盖它，避免把本来能解的文件弄坏。
2. **界面/命令行手动指定**的 ekey。
3. **同名密钥文件**：`song.mflac.ekey`、`song.ekey`（内容可以是裸 ekey、`ekey,songid` 或 `{"ekey": "..."}`）。
4. **JSON 密钥库**：按 `mid` / `song_id` / 文件名匹配。

只有当容器是**现代 musicex 尾部**（QQ 音乐 ≥ 19.57）时，文件里才完全没有密钥，
这时才必须靠 2–4 中的一种提供。

> **本项目不会、也没有实现「自动从 QQ 音乐服务器拉取 ekey」**：
> 那需要读取本机 QQ 音乐客户端的登录凭据（Windows 上还得读运行中进程的内存），
> 这件事超出「本地格式转换」的范围。所以 musicex 文件需要你自己提供 ekey。
>
> 仓库里也**不附带任何密钥**：`vendor/keys/qmc_keys.json` 是一个空壳，格式见文件内注释。

### V1 尾部有两种写法，真实文件用的是不常见的那一种

`.mflac` 的 V1 尾部结构是 `[密钥区][u32 LE 密钥区长度]`。问题出在「密钥区装什么」——
公开实现之间并不一致：

| 写法 | 密钥区内容 | 谁在用 |
| --- | --- | --- |
| **文本式** | **ASCII base64 的 ekey 字符串**，NUL 结尾 | **QQ 音乐真实下载的 `.mflac`** |
| 原始式 | 原始密钥字节 | 部分第三方工具产出的文件 |

早期版本只实现了「原始式」（把密钥区字节再 base64 编码一次，再交给解析器），
于是**真实文件一律解不开**：双重编码，密钥全错，解出来就是噪声。

现在两种解读都会被列出，解码器**拿文件自己的头几十字节去试**，留下真正能解出
`fLaC` 签名的那一个。因此两种写法都能开，也不依赖把猜测写对。

### 装了 19.51 还是解不开？

先跑 `python -X utf8 -m echoshift 文件.mflac --diagnose`，看「结论」那一行：

1. **尾部类型 = `musicex`，或尾部标记里什么都没有**
   → 这个文件里根本没有 ekey。
   **换回 19.51 并不会给已经下载好的文件补上密钥**——旧版本只会加密它自己新下载的文件。
   必须用 19.51 把这首歌**重新下载一遍**，尾部才会带密钥。
2. **尾部类型 = `QTag` / `QMC2 v1`，但「解密后头部」不是 `fLaC`**
   → 拿到了密钥却解不开。两种 V1 写法都试过还不行的，说明是第三种布局——
   把 `--diagnose` 的完整输出发出来，尾部十六进制那几行就能定位。
3. **尾部类型 = `QTag` / `QMC2 v1`，且「解密后头部」是 `fLaC`**
   → 解密没问题，问题在后面的转码环节，看日志里的具体报错。

### 解密正确性怎么保证的

- 解密后会**嗅探前 16 字节**：如果不是 `fLaC` / `OggS` / `ID3` 等已知音频签名，
  就直接报「ekey 很可能不正确」并提示跑 `--diagnose`，而不是输出一个听着像噪声的 MP3。
- ekey 的多种解读会**按文件实际试解**来挑选，而不是按启发式猜。

---

## 输出命名模板

模板里的 `/` 是目录分隔符，可用变量：

```
{filename} {title} {artist} {album} {albumartist} {track} {disc}
{year} {genre} {composer} {index} {samplerate} {bitrate} {mode}
```

支持格式说明（如 `{track:02d}`）。几个要点：

- 缺失的标签替换为空串，`{artist} - {title}.mp3` 在无艺术家标签时会退化成 `标题.mp3`，
  不会留下 `" - 标题.mp3"` 这种残缺名字。
- **标签值里的 `/` 会被替换成 `_`**——`AC/DC` 不会变成两级目录。
- 文件名里的 Windows 非法字符、保留设备名（`CON`、`LPT1`…）都会被处理。
- 模板无法逃出输出目录：`..` 和绝对路径都会被中和。

内置模板：

| 说明 | 模板 |
| --- | --- |
| 艺术家 - 标题（默认） | `{artist} - {title}.mp3` |
| 保持原文件名 | `{filename}.mp3` |
| 音乐库结构 | `{artist}/{album}/{track:02d} {title}.mp3` |
| 专辑艺术家 / 专辑 | `{albumartist}/{album}/{title}.mp3` |
| 含碟号 | `{artist}/{album}/{disc:02d}{track:02d} {title}.mp3` |

---

## 命令行用法

命令行是给开发者的（从源码 `pip install -e .` 或设置 `PYTHONPATH` 后使用），
发布的 `EchoShift.exe` 只有图形界面。

```powershell
python -X utf8 -m echoshift --help
python -X utf8 -m echoshift --list-presets
```

> `-X utf8` 建议保留：中文版 Windows 控制台默认 GBK，不加也能用（程序会兜底切换编码），
> 但显式打开更稳妥。

常用示例：

```powershell
# 单个文件，V0 最高质量
python -X utf8 -m echoshift song.flac -o out --mode vbr -q 0

# 加密文件 + 手填 ekey，固定 320 kbps
python -X utf8 -m echoshift locked.mflac --ekey "<EKEY>" -o out --mode cbr -b 320

# 整个目录递归，重采样到 44.1 kHz 并强制立体声，4 线程并行
python -X utf8 -m echoshift D:\Music -o D:\MP3 -r --sample-rate 44100 --channels stereo -j 4

# 只看会得到什么，不实际转换
python -X utf8 -m echoshift song.mflac --dry-run

# mflac 打不开时：打印文件头尾、尾部布局与密钥判定（不转换）
python -X utf8 -m echoshift broken.mflac --diagnose

# 机器可读的结果（含每项校验明细）
python -X utf8 -m echoshift D:\Music -o D:\MP3 --json
```

退出码：`0` 全部成功，`1` 有失败项，`2` 参数错误，`3` ffmpeg 不可用，`4` 没找到输入文件。

### `--diagnose`：mflac 打不开时先跑这个

它会打印文件头/尾的十六进制与 ASCII、尾部标记（`QTag` / `musicex`）出现在哪里、
识别出的尾部类型、ekey 来源与解码后长度、选定哪个密码，以及**决定性的那一步**：
用拿到的密钥解密首块，看结果是不是 `fLaC` 这样的真实签名。

- 首块签名正常 → 密钥和布局都对，问题在下游。
- 首块不是签名 → 密钥错，或者真实布局和预期不同。
- 完全没有 ekey → 会直接告诉你是 musicex（19.57+）还是连尾部都没有。

---

## 已知限制

- **界面不支持中途调整队列**：转换开始后添加/删除会被拒绝（按钮会变灰并说明原因）。
- **只输出 MP3**，不输出其他格式。
- **只支持 Windows 64 位**。
- **musicex 容器无法自动取密钥**，这是有意为之，见 [MFLAC / QMC 支持情况](#mflac--qmc-支持情况)。
- 转换速度取决于 CPU 核数与源文件大小；`.mflac` 解密是纯 Python 实现的，
  40 MB 左右的曲目在单进程下需要十几到二十秒，多个大文件同时转换时会更慢。
- 转换期间界面保持可响应；但如果机器本身负载已经很重，界面仍会变慢。

---

## 许可与合规

本项目采用 **GNU General Public License v3.0**，全文见 [LICENSE](LICENSE)
（内置的 ffmpeg 是 GPL 组件，因此本项目同样以 GPLv3 发布）。

- 本项目用于**你自己合法持有**的音频文件的本地格式转换（例如把无损文件压成车载能放的 MP3）。
  请自行确认符合你所在地区的法律与相关服务条款。
- 项目**不提供、不获取、不绕过任何内容授权**：不会向任何服务器索取密钥，
  不读取 QQ 音乐客户端的登录凭据，也不附带任何密钥。
- 解密算法参考了以下公开实现，特此致谢：
  [ownlight6/qmc-decoder](https://github.com/ownlight6/qmc-decoder)、
  [jixunmoe/tc_tea_rust](https://github.com/jixunmoe/tc_tea_rust)
  与 [unlock-music](https://git.unlock-music.dev/um)。
