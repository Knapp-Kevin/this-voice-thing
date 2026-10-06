"""Command-line driver for the isolated RVC feasibility harness."""

import argparse
import json

from this_voice_thing.engines import rvc_feasibility as rvc


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show the pinned RVC environment state.")

    install = sub.add_parser("install", help="Install the pinned isolated RVC environment.")
    install.add_argument("--reset", action="store_true", help="Replace a mismatched upstream checkout.")

    sub.add_parser("assets", help="Download shared HuBERT and RMVPE inference assets.")

    convert = sub.add_parser("convert", help="Run one offline RVC conversion smoke test.")
    convert.add_argument("--model", required=True)
    convert.add_argument("--input", required=True)
    convert.add_argument("--output", required=True)
    convert.add_argument("--index")
    convert.add_argument("--pitch", type=float, default=0.0)
    convert.add_argument("--f0-method", choices=["rmvpe", "pm"], default="rmvpe")
    convert.add_argument("--index-rate", type=float, default=0.0)
    convert.add_argument("--speaker-id", type=int)

    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.command == "status":
        result = rvc.status()
    elif args.command == "install":
        result = rvc.install(reset=args.reset)
    elif args.command == "assets":
        result = rvc.download_shared_assets()
    else:
        result = rvc.offline_convert(
            model=args.model,
            input_path=args.input,
            output_path=args.output,
            index_path=args.index,
            pitch=args.pitch,
            f0_method=args.f0_method,
            index_rate=args.index_rate,
            speaker_id=args.speaker_id,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
