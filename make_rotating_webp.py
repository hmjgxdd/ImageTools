#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把图片抠成透明背景，并生成「围绕中心 360° 匀速旋转」的动画 WebP。

抠背景：从画布四角/四边出发做连通泛洪，只把「与边缘连通的浅色背景」转透明。
        徽章内部的浅色高光、被轮廓包围的区域不会被误抠（连通性保护）。
旋转：  自动检测主体圆心（如带皇冠的徽章，圆心比图片几何中心低），把圆心对齐
        到正方形画布正中心再逐帧旋转，因此徽章本体不偏心晃动、皇冠也不会被裁切。

用法：
  python make_rotating_webp.py                          # 处理 pic/ 下全部 jpg/png
  python make_rotating_webp.py X.jpg                    # 只处理一张
  python make_rotating_webp.py pic -o D:/out            # 指定输出目录
  python make_rotating_webp.py -d 4 -n 48 -q 75         # 4秒一圈、48帧、质量75（默认）
  python make_rotating_webp.py --bright 205 --spread 45 # 抠背景容差微调
  python make_rotating_webp.py --lossless               # 改用无损（体积会大很多）

依赖：Python 3 + Pillow
"""
import argparse
import math
import os
import sys
from collections import deque

from PIL import Image, ImageFilter

# ---------- 默认参数 ----------
DEFAULT_DURATION = 4.0     # 转一圈秒数
DEFAULT_FRAMES = 48        # 每圈帧数
DEFAULT_QUALITY = 75       # 有损质量（默认）；75 对徽章图肉眼几乎无损，比 90 省约 1/3 体积
DEFAULT_BRIGHT = 205       # 背景"亮度"下限（RGB 三通道都 >= 此值才可能是背景）
DEFAULT_SPREAD = 45        # 背景通道最大差值（低饱和才算背景；渐变浅灰紫也在这个范围）


def is_background(r, g, b, bright, spread):
    """判断是否背景色：足够亮 + 低饱和（浅灰/浅灰紫）。"""
    return min(r, g, b) >= bright and (max(r, g, b) - min(r, g, b)) <= spread


def cut_background(img, bright, spread, feather=True):

    """连通泛洪抠背景：只把与画布边缘连通的背景色区域设为透明。"""
    rgba = img.convert("RGBA")
    w, h = rgba.size
    src = rgba.load()
    visited = bytearray(w * h)
    queue = deque()

    # 从四条边的所有像素起步，符合条件的入队
    def try_seed(x, y):
        idx = y * w + x
        if visited[idx]:
            return
        r, g, b, _ = src[x, y]
        if is_background(r, g, b, bright, spread):
            visited[idx] = 1
            queue.append((x, y))

    for x in range(w):
        try_seed(x, 0)
        try_seed(x, h - 1)
    for y in range(h):
        try_seed(0, y)
        try_seed(w - 1, y)

    # BFS 泛洪
    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h:
                idx = ny * w + nx
                if not visited[idx]:
                    r, g, b, _ = src[nx, ny]
                    if is_background(r, g, b, bright, spread):
                        visited[idx] = 1
                        queue.append((nx, ny))

    # 生成 alpha：背景=0，其余=255
    alpha = Image.new("L", (w, h), 255)
    ap = alpha.load()
    for i, flag in enumerate(visited):
        if flag:
            ap[i % w, i // w] = 0

    # 边缘轻微羽化，消除锯齿（可选）
    if feather:
        alpha = alpha.filter(ImageFilter.GaussianBlur(0.6))

    out = rgba.copy()
    out.putalpha(alpha)
    # 透明像素 RGB 归零，避免旋转插值时混出背景色边
    px = out.load()
    for y in range(h):
        for x in range(w):
            if px[x, y][3] == 0:
                px[x, y] = (0, 0, 0, 0)
    return out


def detect_center(cut):
    """检测主体圆心。

    原理：圆形徽章在与它水平直径相交的那几行，不透明像素的横向跨度最大。
    取跨度≥95%最大跨度的行（排除皇冠/装饰的窄行），这些行的中心即圆心。
    对纯圆徽章，结果与几何中心一致；对带皇冠的王者图，圆心会比几何中心低，
    因为顶部皇冠占据了额外空间。
    """
    w, h = cut.size
    alpha = cut.getchannel("A")
    ap = alpha.load()
    spans = []  # 每行的 (左, 右, 宽度)
    for y in range(h):
        xs = [x for x in range(w) if ap[x, y] > 128]
        if xs:
            spans.append((y, min(xs), max(xs), max(xs) - min(xs) + 1))
    if not spans:
        return w / 2.0, h / 2.0
    max_width = max(s[3] for s in spans)
    core = [s for s in spans if s[3] >= 0.95 * max_width]
    cy = sum(s[0] for s in core) / len(core)
    cx = sum((s[1] + s[2]) / 2.0 for s in core) / len(core)
    return cx, cy


def make_rotation(cut, duration, frames, center=None):
    """生成 N 帧匀速旋转图像 + 每帧时长。

    center=(cx, cy) 为旋转轴（主体圆心）；None 时自动检测。
    把圆心对齐到正方形画布正中心，旋转时徽章本体不晃动。
    画布边长取「圆心到任意主体的最远距离 ×2」，保证皇冠等突出部分
    转任何角度都不被裁切。
    """
    w, h = cut.size
    cx, cy = center if center is not None else detect_center(cut)
    alpha = cut.getchannel("A")
    ap = alpha.load()
    # 圆心到最远不透明像素的距离
    max_r = 0.0
    for y in range(h):
        for x in range(w):
            if ap[x, y] > 128:
                d = math.hypot(x - cx, y - cy)
                if d > max_r:
                    max_r = d
    side = int(math.ceil(max_r * 2)) + 4
    if side % 2:
        side += 1
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    # 让圆心落在画布中心
    canvas.alpha_composite(cut, (side // 2 - int(round(cx)),
                                 side // 2 - int(round(cy))))

    imgs = []
    for i in range(frames):
        angle = 360.0 * i / frames
        imgs.append(canvas.rotate(angle, resample=Image.BICUBIC, expand=False))
    per_frame_ms = int(round(duration * 1000 / frames))
    return imgs, per_frame_ms, (cx, cy)


def process_one(src, dst, duration, frames, bright, spread, feather=True,
                quality=DEFAULT_QUALITY, lossless=False):
    img = Image.open(src)
    cut = cut_background(img, bright, spread, feather)
    imgs, per_ms, center = make_rotation(cut, duration, frames)
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    save_kwargs = dict(format="WEBP", save_all=True, append_images=imgs[1:],
                       duration=per_ms, loop=0, method=4)
    if lossless:
        save_kwargs["lossless"] = True
    else:
        save_kwargs["quality"] = quality
    imgs[0].save(dst, **save_kwargs)
    # 校验
    chk = Image.open(dst)
    n = getattr(chk, "n_frames", 1)
    data = open(dst, "rb").read()
    idx, durs = 0, []
    while True:
        idx = data.find(b"ANMF", idx)
        if idx < 0:
            break
        durs.append(int.from_bytes(data[idx + 20:idx + 23], "little"))
        idx += 4
    chk.seek(0)
    amin, amax = chk.convert("RGBA").getchannel("A").getextrema()
    return {
        "size": cut.size, "canvas": imgs[0].size, "center": center,
        "frames": n, "total_ms": sum(durs), "alpha": (amin, amax),
        "src_size": os.path.getsize(src), "dst_size": os.path.getsize(dst),
    }


def collect_inputs(input_arg):
    if os.path.isfile(input_arg):
        return [(input_arg, os.path.splitext(os.path.basename(input_arg))[0] + ".webp")]
    if os.path.isdir(input_arg):
        found = []
        for f in sorted(os.listdir(input_arg)):
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                found.append((os.path.join(input_arg, f),
                              os.path.splitext(f)[0] + ".webp"))
        return found
    return None


def main():
    p = argparse.ArgumentParser(description="抠透明背景 + 中心旋转动画 WebP")
    p.add_argument("input", nargs="?", default="pic", help="输入图片或目录（默认 pic/）")
    p.add_argument("-o", "--output", help="输出目录（默认 <输入>/rotating_webp/）")
    p.add_argument("-d", "--duration", type=float, default=DEFAULT_DURATION,
                   help=f"转一圈秒数，默认 {DEFAULT_DURATION}")
    p.add_argument("-n", "--frames", type=int, default=DEFAULT_FRAMES,
                   help=f"每圈帧数，默认 {DEFAULT_FRAMES}")
    p.add_argument("--bright", type=int, default=DEFAULT_BRIGHT,
                   help=f"背景亮度下限，默认 {DEFAULT_BRIGHT}；主体浅色被误抠时调低")
    p.add_argument("--spread", type=int, default=DEFAULT_SPREAD,
                   help=f"背景色通道最大差，默认 {DEFAULT_SPREAD}")
    p.add_argument("-q", "--quality", type=int, default=DEFAULT_QUALITY,
                   help=f"有损质量 1-100，默认 {DEFAULT_QUALITY}")
    p.add_argument("--lossless", action="store_true",
                   help="无损编码（体积约为有损的 10 倍，不推荐用于旋转动画）")
    p.add_argument("--no-feather", action="store_true", help="关闭边缘羽化")
    args = p.parse_args()

    pairs = collect_inputs(args.input)
    if pairs is None:
        print(f"错误：找不到 {args.input}")
        sys.exit(1)
    if not pairs:
        print(f"错误：{args.input} 下没有 jpg/png/webp 图片")
        sys.exit(1)

    out_dir = args.output or os.path.join(
        args.input if os.path.isdir(args.input) else os.path.dirname(args.input),
        "rotating_webp")
    os.makedirs(out_dir, exist_ok=True)

    print(f"共 {len(pairs)} 张；{args.duration}s/圈 × {args.frames}帧；输出 {out_dir}\n")
    ok = 0
    for src, name in pairs:
        dst = os.path.join(out_dir, name)
        try:
            r = process_one(src, dst, args.duration, args.frames,
                            args.bright, args.spread, not args.no_feather,
                            args.quality, args.lossless)
            print(f"  {os.path.basename(src):<12} 圆心({r['center'][0]:.0f},{r['center'][1]:.0f}) "
                  f"抠图{r['size'][0]}x{r['size'][1]}→画布{r['canvas'][0]}x{r['canvas'][1]}  "
                  f"校验:{r['frames']}帧/{r['total_ms']}ms alpha={r['alpha'][0]}-{r['alpha'][1]}  "
                  f"{r['src_size']//1024}KB→{r['dst_size']//1024}KB")
            ok += 1
        except Exception as e:
            print(f"  {os.path.basename(src):<12} 失败：{e}")
    print(f"\n完成 {ok}/{len(pairs)} 个")


if __name__ == "__main__":
    main()
