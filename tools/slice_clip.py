import argparse
import wave


def slice_wav(src_path: str, dst_path: str, start_s: float, end_s: float) -> None:
    with wave.open(src_path, "rb") as src:
        params = src.getparams()
        rate = src.getframerate()
        src.setpos(int(start_s * rate))
        frames = src.readframes(int((end_s - start_s) * rate))
    with wave.open(dst_path, "wb") as dst:
        dst.setparams(params)
        dst.writeframes(frames)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Slice a WAV file by time range.")
    parser.add_argument("src")
    parser.add_argument("dst")
    parser.add_argument("start", type=float)
    parser.add_argument("end", type=float)
    args = parser.parse_args()
    slice_wav(args.src, args.dst, args.start, args.end)
    print(f"Wrote {args.dst} ({args.end - args.start:.1f}s slice)")
