import vapoursynth as vs
from awsmfunc import ScreenGen, DynamicTonemap, FrameInfo, zresize
import random
import asyncio
from pathlib import Path
from functools import partial

# Modified version of https://git.concertos.live/AHD/ahd_utils/src/branch/master/screengn.py
core = vs.core

async def vs_screengn(
    source: Path,
    encode: Path = None,
    filter_b_frames: bool = False,
    num: int = 5,
    dir: Path = Path("."),
    seed: int = None,
):
    dir = Path(dir)
    source = Path(source)
    if encode:
        encode = Path(encode)

    if seed is not None:
        random.seed(seed)

    # Prefer ffms2, fallback to lsmash for m2ts
    if source.suffix.lower() == ".m2ts":
        src = core.lsmas.LWLibavSource(str(source))
    else:
        cachefile = dir / "ffms2.ffms2"
        src = core.ffms2.Source(str(source), cachefile=str(cachefile))

    enc = None
    if encode:
        enc = core.ffms2.Source(str(encode))

    num_frames = len(src)
    start, end = 1000, num_frames - 10000

    frames = []

    def filter_ftype(n, f, clip, frame, frames_list, ftype="B"):
        if f.props["_PictType"].decode() == ftype:
            frames_list.append(frame)
        return clip

    if filter_b_frames and enc:
        i = 0
        while len(frames) < num:
            frame = random.randint(start, end)
            enc_f = enc[frame]
            enc_f = enc_f.std.FrameEval(
                partial(filter_ftype, clip=enc_f, frame=frame, frames_list=frames),
                enc_f
            )
            # Offload blocking frame output to avoid blocking event loop
            await asyncio.to_thread(enc_f.output, open(Path("/dev/null"), "wb"))
            i += 1
            if i > num * 10:
                raise ValueError("screengn: Encode doesn't seem to contain desired picture type frames.")
    else:
        frames = sorted(random.randint(start, end) for _ in range(num))

    # Write frame numbers to file
    screens_txt = dir / "screens.txt"
    screens_txt.write_text("".join(f"{f}\n" for f in frames))

    # Crop and resize src if encode dimensions mismatch
    if encode and (src.width != enc.width or src.height != enc.height):
        ref = zresize(enc, preset=src.height)
        crop_l = int(round((src.width - ref.width) / 2))
        crop_t = int(round((src.height - ref.height) / 2))
        src = src.std.Crop(left=crop_l, right=crop_l, top=crop_t, bottom=crop_t)

        if enc.width / enc.height > 16 / 9:
            width, height = enc.width, None
        else:
            width, height = None, enc.height

        src = zresize(src, width=width, height=height)

    # HDR Tonemap if needed
    tonemapped = False
    primaries = src.get_frame(0).props.get("_Primaries", None)
    if primaries == 9:
        tonemapped = True
        src = DynamicTonemap(src, src_fmt=False, libplacebo=False, adjust_gamma=True)
        if encode:
            enc = DynamicTonemap(enc, src_fmt=False, libplacebo=False, adjust_gamma=True)

    if tonemapped:
        src = FrameInfo(src, "Tonemapped")
    ScreenGen(src, str(dir), "a")

    if encode:
        if tonemapped:
            enc = FrameInfo(enc, "Encode (Tonemapped)")
        ScreenGen(enc, str(dir), "b")
