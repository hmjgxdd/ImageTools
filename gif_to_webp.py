#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GIF 动图 → 透明背景动画 WebP 批量转换工具

特点：
  1. 保留透明：原图带透明通道则直接保留；纯白底则自动抠成透明（可调阈值）。
  2. 抗锯齿软化：白底边缘按"白度"渐变成半透明，不留锯齿白边。
  3. 全透明像素 RGB 归零，避免个别解码器边缘混色。
  4. 无损编码、保留全部帧 / 每帧时长 / 无限循环。
  5. 纯 PIL 的 C 级图像运算（不用 numpy），800x600 / 200 帧的大图也能秒级完成。

用法（在 PowerShell 或 Git Bash 里执行）：
  python gif_to_webp.py                       # 转换 pic/ 下所有 gif → pic/webp/
  python gif_to_webp.py D:/other/pics         # 转换指定目录下所有 gif → <目录>/webp/
  python gif_to_webp.py X.gif -o D:/out       # 转换单个文件到指定目录
  python gif_to_webp.py -t 245                # 抠白阈值调低（主体偏白导致被误抠时用）
  python gif_to_webp.py -f 15                 # 边缘软化宽度收窄

依赖：Python 3 + Pillow（pip install Pillow）
"""
import argparse
import os
import sys
from PIL import Image, ImageChops

# ---------------- 可调参数默认值 ----------------
DEFAULT_WHITE_THRESHOLD = 250   # >= 该值视为背景白（RGB 三通道最小值）
DEFAULT_FADE = 30               # 边缘软化区间宽度；越大过渡越柔


def read_frames(path):
    """读取 GIF 全部帧的 RGBA 与每帧时长。"""
    im = Image.open(path)
    n = getattr(im, "n_frames", 1)
    frames, durations = [], []
    for i in range(n):
        im.seek(i)
        frames.append(im.convert("RGBA").copy())
        durations.append(im.info.get("duration") or 80)
    return frames, durations


def zero_transparent_rgb(rgba):
    """把 alpha=0 像素的 RGB 归零（不改 alpha），避免解码器边缘混色。"""
    a = rgba.getchannel("A")
    zero_mask = a.point(lambda v: 255 if v == 0 else 0)
    rgb = Image.merge("RGB", rgba.split()[:3])
    rgb = Image.composite(Image.new("RGB", rgba.size, (0, 0, 0)), rgb, zero_mask)
    out = rgb.convert("RGBA")
    out.putalpha(a)
    return out


def cut_white(rgba, threshold, fade):
    """抠白底：近白像素转透明，边缘按白度做 alpha 渐变软化。"""
    r, g, b, a = rgba.split()
    # 每像素 RGB 最小值 = 离"纯白"的距离（越大越接近白）
    mn = ImageChops.darker(ImageChops.darker(r, g), b)
    lo = max(0, threshold - fade)
    # 白度 → alpha 保留系数：mn>=threshold 全透明；mn<=lo 完全保留；中间线性过渡
    scale = mn.point(
        lambda m: 0 if m >= threshold
        else (255 if m <= lo else int(255 * (threshold - m) / float(threshold - lo)))
    )
    new_a = ImageChops.multiply(a, scale)
    # 全透明像素 RGB 归零
    zero_mask = new_a.point(lambda v: 255 if v == 0 else 0)
    rgb = Image.merge("RGB", (r, g, b))
    rgb = Image.composite(Image.new("RGB", rgba.size, (0, 0, 0)), rgb, zero_mask)
    out = rgb.convert("RGBA")
    out.putalpha(new_a)
    return out


def convert_one(src, dst, threshold, fade):
    """转换单个 GIF，返回统计信息 dict。"""
    frames, durations = read_frames(src)
    # 任一帧有半透明/透明像素 → 视为原本就带透明，只做 RGB 归零，不抠白
    has_alpha = any(f.getchannel("A").getextrema()[0] < 255 for f in frames)
    mode = "保留原透明" if has_alpha else "抠白底"
    cleaned = [
        zero_transparent_rgb(f) if has_alpha else cut_white(f, threshold, fade)
        for f in frames
    ]
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    cleaned[0].save(
        dst, format="WEBP", save_all=True, append_images=cleaned[1:],
        duration=durations, loop=0, lossless=True, method=6,
    )
    return {
        "mode": mode,
        "frames": len(frames),
        "duration_ms": sum(durations),
        "src_size": os.path.getsize(src),
        "dst_size": os.path.getsize(dst),
    }


def webp_frame_durations(path):
    """从 WebP 的 ANMF 块直接读每帧时长（毫秒）。

    注意：Pillow 读动画 WebP 的 info['duration'] 恒为 0，是它的已知限制，
    不是文件真的丢了时长。直接解析 RIFF 块才准确。
    ANMF 块结构: 'ANMF'(4) + size(4) + x(3)+y(3)+w(3)+h(3) + duration(3) + flags(1)
    """
    data = open(path, "rb").read()
    idx, durs = 0, []
    while True:
        idx = data.find(b"ANMF", idx)
        if idx < 0:
            break
        durs.append(int.from_bytes(data[idx + 20:idx + 23], "little"))
        idx += 4
    return durs


def verify_webp(path):
    """校验输出：帧数、总时长、alpha 范围。"""
    im = Image.open(path)
    n = getattr(im, "n_frames", 1)
    im.seek(0)
    amin, amax = im.convert("RGBA").getchannel("A").getextrema()
    durs = webp_frame_durations(path)
    return n, sum(durs), amin, amax


def collect_inputs(input_arg):
    """返回 [(src, dst), ...]。"""
    if os.path.isfile(input_arg):
        name = os.path.splitext(os.path.basename(input_arg))[0] + ".webp"
        out_dir = os.path.dirname(os.path.abspath(input_arg))
        return [(input_arg, os.path.join(out_dir, name))]
    if os.path.isdir(input_arg):
        gifs = sorted(
            os.path.join(input_arg, f) for f in os.listdir(input_arg)
            if f.lower().endswith(".gif")
        )
        if not gifs:
            return []
        out_dir = os.path.join(input_arg, "webp")
        return [(g, os.path.join(out_dir, os.path.splitext(os.path.basename(g))[0] + ".webp"))
                for g in gifs]
    return None


def main():
    p = argparse.ArgumentParser(description="GIF 动图批量转透明背景动画 WebP")
    p.add_argument("input", nargs="?", default="pic",
                   help="输入 GIF 文件或目录（默认 pic/）")
    p.add_argument("-o", "--output",
                   help="输出目录（默认：单文件→同级 webp/；目录→<目录>/webp/ 内）")
    p.add_argument("-t", "--threshold", type=int, default=DEFAULT_WHITE_THRESHOLD,
                   help=f"抠白阈值 0-255，默认 {DEFAULT_WHITE_THRESHOLD}；主体偏白被误抠时调低")
    p.add_argument("-f", "--fade", type=int, default=DEFAULT_FADE,
                   help=f"边缘软化宽度，默认 {DEFAULT_FADE}；白边明显时调大，主体发虚时调小")
    args = p.parse_args()

    pairs = collect_inputs(args.input)
    if pairs is None:
        print(f"错误：找不到输入路径 {args.input}")
        sys.exit(1)
    if not pairs:
        print(f"错误：{args.input} 目录下没有 GIF 文件")
        sys.exit(1)

    # 显式指定输出目录时，覆盖默认 dst 的目录部分
    if args.output:
        os.makedirs(args.output, exist_ok=True)
        pairs = [(s, os.path.join(args.output, os.path.basename(d))) for s, d in pairs]

    print(f"共 {len(pairs)} 个 GIF → WebP（阈值 {args.threshold}，软化 {args.fade}）\n")
    total_src = total_dst = 0
    ok = 0
    for src, dst in pairs:
        try:
            info = convert_one(src, dst, args.threshold, args.fade)
            n, total, amin, amax = verify_webp(dst)
            total_src += info["src_size"]
            total_dst += info["dst_size"]
            ok += 1
            saved = 100 * (1 - info["dst_size"] / info["src_size"]) if info["src_size"] else 0
            frame_note = "" if n == info["frames"] else f"(合并重复帧{info['frames']}→{n})"
            dur_note = "" if total == info["duration_ms"] else f" 警告:原{info['duration_ms']}ms"
            print(f"  {os.path.basename(src):<12} [{info['mode']}] "
                  f"{info['frames']}帧/{info['duration_ms']}ms  "
                  f"{info['src_size']//1024}KB → {info['dst_size']//1024}KB (省{saved:.0f}%)  "
                  f"校验:{n}帧{frame_note}/{total}ms{dur_note} alpha={amin}-{amax}")
        except Exception as e:
            print(f"  {os.path.basename(src):<12} 失败：{e}")
    print(f"\n完成 {ok}/{len(pairs)} 个；总计 {total_src//1024}KB → {total_dst//1024}KB "
          f"(省 {100 * (1 - total_dst / total_src):.0f}%)" if total_src else "\n完成")


if __name__ == "__main__":
    main()
