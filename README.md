# ImageTools

图片处理小工具集（Python + Pillow），目前包含两个：

| 工具 | 用途 |
|---|---|
| `gif_to_webp.py` | 把 GIF 动图批量转成**透明背景动画 WebP**（原图带透明则保留，白底则自动抠除），体积通常比 GIF 小 10%~45% |
| `make_rotating_webp.py` | 把静态图片抠成透明背景，并生成**围绕主体圆心 360° 匀速旋转**的动画 WebP（适合段位徽章、奖牌等展示） |

两者的产出都是动画 WebP：体积比 GIF 小、支持完整 alpha 透明通道，Android（API 18+，用 `ImageDecoder`/`AnimatedImageDrawable`）及主流浏览器均原生支持。

## 环境

需要 Python 3 和 Pillow：

```bash
pip install Pillow
```

两个脚本只依赖 Pillow，不需要 ffmpeg、numpy 或其他库。

---

## 1. gif_to_webp.py — GIF 转透明动画 WebP

**做了什么**

- 逐个读出 GIF 所有帧，带透明的原样保留 alpha；纯色白底的自动抠成透明（边缘按白度做 alpha 渐变软化，不留锯齿白边）。
- 全透明像素的 RGB 归零，避免个别解码器边缘混色。
- 无损 WebP 编码，保留全部帧、每帧时长、无限循环。
- 处理完自动校验帧数、时长、alpha 范围并打印压缩率。

**用法**

```bash
python gif_to_webp.py                       # 转换当前 pic/ 下所有 gif → pic/webp/
python gif_to_webp.py D:/other/pics         # 转换指定目录下所有 gif → <目录>/webp/
python gif_to_webp.py X.gif -o D:/out       # 转换单个文件到指定目录
python gif_to_webp.py -t 245                # 抠白阈值调低（主体偏白被误抠时用）
python gif_to_webp.py -f 15                 # 边缘软化宽度收窄
```

**参数**

| 参数 | 默认 | 说明 |
|---|---|---|
| `input` | `pic` | 输入 GIF 文件或目录 |
| `-o, --output` | 自动 | 输出目录（单文件默认同级 `webp/`，目录默认 `<目录>/webp/`） |
| `-t, --threshold` | `250` | 抠白阈值（RGB 三通道最小值）：主体偏白被误抠时调低，如 245 |
| `-f, --fade` | `30` | 边缘软化宽度：白边明显调大，主体发虚调小 |

**小提示**：Pillow 读动画 WebP 的 `info['duration']` 恒为 0（它的已知限制，不是文件坏了），所以脚本直接解析 RIFF 的 ANMF 块来核对时长。Pillow 合并内容相同的连续帧属于正常优化（总时长不变）。

---

## 2. make_rotating_webp.py — 透明背景 + 中心旋转动画

**做了什么**

- **抠背景**：从画布四角/四边做连通泛洪，只把「与边缘连通的浅色背景」转成透明。徽章内部的浅色高光、被轮廓包围的区域不会被误抠。
- **检测圆心**：圆形主体与水平直径相交的那几行不透明像素横向跨度最大，取这些行的中心作为旋转轴。对带皇冠/顶部装饰的图尤其重要——它们的主体圆心比图片几何中心低，若按几何中心转会明显偏心晃动。
- **旋转**：把圆心对齐到正方形画布正中心，画布边长取「圆心到最远主体像素距离 ×2」，保证皇冠等突出部分转到任何角度都不被裁切；按 360/N 度逐帧旋转，合成无限循环动画。
- **有损压缩**：默认质量 75（`method=4`），对徽章类图肉眼几乎无损，体积比无损小一个数量级；可切无损。

**用法**

```bash
python make_rotating_webp.py                          # 处理 pic/ 下全部 jpg/png/webp
python make_rotating_webp.py X.jpg                    # 只处理一张
python make_rotating_webp.py pic -o D:/out            # 指定输出目录
python make_rotating_webp.py -d 4 -n 48 -q 75         # 4 秒一圈、48 帧、质量 75（默认）
python make_rotating_webp.py --bright 205 --spread 45 # 抠背景容差微调
python make_rotating_webp.py --lossless               # 无损（体积会大很多）
python make_rotating_webp.py --no-feather             # 关闭边缘羽化
```

**参数**

| 参数 | 默认 | 说明 |
|---|---|---|
| `input` | `pic` | 输入图片文件或目录 |
| `-o, --output` | 自动 | 输出目录（默认 `<输入>/rotating_webp/`） |
| `-d, --duration` | `4.0` | 转一圈的秒数 |
| `-n, --frames` | `48` | 每圈帧数（48 帧即约 12fps，旋转流畅） |
| `-q, --quality` | `75` | 有损质量 1-100；实测徽章图 75 与 90 肉眼无差，65 起出现色块 |
| `--bright` | `205` | 背景亮度下限（RGB 三通道都 ≥ 此值才可能是背景）；主体浅色被误抠时调低 |
| `--spread` | `45` | 背景色通道最大差值（低饱和才算背景） |
| `--lossless` | 关 | 无损编码，体积约为有损的 10 倍 |
| `--no-feather` | 关 | 关闭 alpha 边缘轻微羽化 |

**体积参考**：5 张 300px 左右的段位徽章、4 秒/圈 × 48 帧，默认质量 75 合计约 4 MB；质量 90 约 6 MB；无损约 23 MB。

---

## 许可

MIT License，见 [LICENSE](LICENSE)。可自由使用、修改、商用。
