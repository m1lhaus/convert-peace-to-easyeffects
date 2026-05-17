#!/usr/bin/env python3
"""
Convert PEACE Equalizer (.peace) profiles to EasyEffects JSON presets.

PEACE stores its equalizer settings in a Windows INI-style format. When
activated on Windows, PEACE converts this to an EqualizerAPO config (text)
which is then loaded by EqualizerAPO. EasyEffects (Linux) can import that
APO text format, but since we're on Linux we skip the Windows step entirely
and go straight from .peace → EasyEffects JSON preset.

Filter types (PK, LS, HS, LP, HP, BP, NO, AP, LSC, HSC …) are decoded
from the [Filters] section of the .peace file and mapped to the
corresponding EasyEffects "APO (DR)" band types.

Usage:
    python3 convert_peace.py *.peace
    python3 convert_peace.py *.peace --output-dir ~/.config/easyeffects/output
    python3 convert_peace.py profile.peace --skip-zero-gain

The generated .json files can be placed directly in:
  ~/.config/easyeffects/output/     (native package install)
  ~/.var/app/com.github.wwmm.easyeffects/config/easyeffects/output/  (Flatpak)
"""

import argparse
import configparser
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# PEACE [Filters] section — filter type codes
#
# PEACE stores a 0-based integer per band in the [Filters] section that
# encodes the filter type.  The authoritative source is the $FilterTypes
# array in Peace.au3 (line 384), which is the PEACE AutoIt source code.
#
# Codes and the APO filter string PEACE generates for each:
#
#  0  PK    Peak (Bell)         "ON PK  Fc X Hz Gain Y dB Q Z"
#  1  LPQ   Low-pass + Q        "ON LPQ Fc X Hz Q Z"
#  2  HPQ   High-pass + Q       "ON HPQ Fc X Hz Q Z"
#  3  BP    Band-pass           "ON BP  Fc X Hz Q Z"
#  4  LS    Low Shelf           "ON LS  Fc X Hz Gain Y dB" (no Q; APO default S=0.9)
#  5  HS    High Shelf          "ON HS  Fc X Hz Gain Y dB" (no Q; APO default S=0.9)
#  6  NO    Notch               "ON NO  Fc X Hz Q Z"
#  7  AP    All-pass            "ON AP  Fc X Hz Q Z"
#  8  LSC   Lo-shelf slope      "ON LSC S dB Fc X Hz Gain Y dB"
#                                 [Qualities] stores slope S (dB/oct);
#                                 APO: effective slope = S/12, isCornerFreq=false
#  9  HSC   Hi-shelf slope      "ON HSC S dB Fc X Hz Gain Y dB"  (same)
# 10  BWLP  Butterworth LP      cascaded LPQ lines — NOT a single biquad
# 11  BWHP  Butterworth HP      cascaded HPQ lines — NOT a single biquad
# 12  LRLP  Linkwitz-Riley LP   cascaded LPQ lines — NOT a single biquad
# 13  LRHP  Linkwitz-Riley HP   cascaded HPQ lines — NOT a single biquad
# 14  LSCQ  Lo-shelf centre+Q  "ON LSC Fc X Hz Gain Y dB Q Z"
#                                 [Qualities] stores biquad Q directly;
#                                 APO: Q-mode, isCornerFreq=false (centre freq)
# 15  HSCQ  Hi-shelf centre+Q  "ON HSC Fc X Hz Gain Y dB Q Z"  (same)
# 16  LSQ   Lo-shelf corner+Q  "ON LS  Fc X Hz Gain Y dB Q Z"
#                                 [Qualities] stores biquad Q; isCornerFreq=true
#                                 (APO adjusts corner → centre internally)
# 17  HSQ   Hi-shelf corner+Q  "ON HS  Fc X Hz Gain Y dB Q Z"  (same)
#
# Note: codes 10-13 (Butterworth/Linkwitz-Riley) generate multiple APO filter
# lines per band.  There is no single-band EasyEffects equivalent; they are
# mapped to the nearest plain Lo-pass / Hi-pass type as a best effort.
# ---------------------------------------------------------------------------

PEACE_CODE_TO_EE: dict[int, str] = {
    0:  "Bell",        # PK   – Peak
    1:  "Lo-pass",     # LPQ  – Low-pass with Q
    2:  "Hi-pass",     # HPQ  – High-pass with Q
    3:  "Band-pass",   # BP   – Band-pass
    4:  "Lo-shelf",    # LS   – Low Shelf (no Q in APO, default S=0.9)
    5:  "Hi-shelf",    # HS   – High Shelf (no Q in APO, default S=0.9)
    6:  "Notch",       # NO   – Notch
    7:  "All-pass",    # AP   – All-pass
    8:  "Lo-shelf",    # LSC  – Low Shelf, slope-mode, isCornerFreq=false
    9:  "Hi-shelf",    # HSC  – High Shelf, slope-mode, isCornerFreq=false
    10: "Lo-pass",     # BWLP – Butterworth LP (cascaded; single-band approx)
    11: "Hi-pass",     # BWHP – Butterworth HP (cascaded; single-band approx)
    12: "Lo-pass",     # LRLP – Linkwitz-Riley LP (cascaded; single-band approx)
    13: "Hi-pass",     # LRHP – Linkwitz-Riley HP (cascaded; single-band approx)
    14: "Lo-shelf",    # LSCQ – Low Shelf, centre-freq, biquad Q (Q used directly)
    15: "Hi-shelf",    # HSCQ – High Shelf, centre-freq, biquad Q (Q used directly)
    16: "Lo-shelf",    # LSQ  – Low Shelf, corner-freq, biquad Q (isCornerFreq=true)
    17: "Hi-shelf",    # HSQ  – High Shelf, corner-freq, biquad Q (isCornerFreq=true)
}

# Filter types that do not use gain in EqualizerAPO; gain will be zeroed
# in the EasyEffects output to avoid confusion.
_PASS_TYPES = {"Lo-pass", "Hi-pass", "Band-pass", "Notch", "All-pass"}

# Codes where [Qualities] stores the slope in dB/oct (not biquad Q).
# PEACE writes "ON LSC/HSC Slope dB Fc X Hz Gain Y dB" to the APO config.
# EqualizerAPO: S = slope/12, isCornerFreq=false (stored freq is centre freq).
# EasyEffects Lo/Hi-shelf APO(DR) interprets the Q field as S, so:
#   JSON Q = peace_quality / 12
# No frequency adjustment is needed (isCornerFreq=false).
_SLOPE_LSC_CODES = {8, 9}
_SLOPE_LSC_NAMES = {8: "LSC", 9: "HSC"}

# Codes for Butterworth / Linkwitz-Riley filters that PEACE expands into
# multiple cascaded biquad lines in the APO config.  They cannot be
# represented accurately as a single EasyEffects band.
_CASCADED_FILTER_CODES = {10, 11, 12, 13}
_CASCADED_FILTER_NAMES = {
    10: "BWLP (Butterworth LP)",
    11: "BWHP (Butterworth HP)",
    12: "LRLP (Linkwitz-Riley LP)",
    13: "LRHP (Linkwitz-Riley HP)",
}


def parse_peace_file(path: Path) -> tuple[float, list[dict]]:
    """
    Parse a .peace file and return (preamp_dB, list_of_bands).

    Each band dict has keys: frequency, gain, q, ee_type (EasyEffects type
    string), code (raw PEACE filter code).

    Only the global "All speakers" EQ is extracted, i.e. the sections
    [Frequencies], [Gains], [Qualities], and [Filters] (without a numeric
    suffix). Per-speaker sections like [Frequencies1], [Gains3] etc. are
    intentionally ignored because headphone profiles target all channels.
    """
    config = configparser.RawConfigParser()
    # .peace files can be UTF-8 with or without a BOM; handle both.
    try:
        config.read(path, encoding="utf-8-sig")
    except Exception:
        config.read(path, encoding="windows-1252")

    # --- PreAmp ----------------------------------------------------------
    preamp = 0.0
    if config.has_section("General") and config.has_option("General", "PreAmp"):
        preamp = float(config.get("General", "PreAmp"))

    # --- Main EQ bands (global "All" channel) ----------------------------
    frequencies: dict[int, float] = {}
    gains: dict[int, float] = {}
    qualities: dict[int, float] = {}
    filter_codes: dict[int, int] = {}

    if config.has_section("Frequencies"):
        for key, val in config.items("Frequencies"):
            if key.lower().startswith("frequency"):
                idx = int(key[len("frequency"):])
                frequencies[idx] = float(val)

    if config.has_section("Gains"):
        for key, val in config.items("Gains"):
            if key.lower().startswith("gain"):
                idx = int(key[len("gain"):])
                gains[idx] = float(val)

    if config.has_section("Qualities"):
        for key, val in config.items("Qualities"):
            if key.lower().startswith("quality"):
                idx = int(key[len("quality"):])
                qualities[idx] = float(val)

    if config.has_section("Filters"):
        for key, val in config.items("Filters"):
            if key.lower().startswith("filter"):
                idx = int(key[len("filter"):])
                filter_codes[idx] = int(val)

    # --- Assemble bands in sorted order ----------------------------------
    bands: list[dict] = []
    slope_warnings: list[str] = []

    for idx in sorted(frequencies.keys()):
        freq = frequencies[idx]
        gain = gains.get(idx, 0.0)
        q = qualities.get(idx, 1.0)
        code = filter_codes.get(idx, 0)  # 0 = PK (Peak) when absent
        ee_type = PEACE_CODE_TO_EE.get(code)
        if ee_type is None:
            ee_type = "Bell"  # fallback for unknown codes

        # Pass/stop filters do not use gain in APO; zero it out to keep
        # the preset accurate (avoids misleading non-zero gain values).
        if ee_type in _PASS_TYPES:
            gain = 0.0

        # For LSC/HSC codes (8, 9): [Qualities] stores slope in dB/oct.
        # PEACE generates "ON LSC/HSC slope dB Fc X Hz Gain Y dB" for APO.
        # EqualizerAPO: S = slope/12, isCornerFreq=false → stored freq is
        # already the centre frequency, no adjustment needed.
        # EasyEffects Lo/Hi-shelf APO(DR) uses Q as the S parameter, so:
        #   JSON Q = slope / 12
        if code in _SLOPE_LSC_CODES:
            orig_q = q
            q = orig_q / 12.0
            slope_warnings.append(
                f"  band at {freq:.1f} Hz: {_SLOPE_LSC_NAMES[code]} (code {code}), "
                f"slope={orig_q:.4g} dB/oct → Q set to {q:.4f} (= slope/12). "
                f"Frequency unchanged (isCornerFreq=false)."
            )

        # Warn about codes that PEACE expands into multiple cascaded biquad
        # lines — they cannot be represented as a single EasyEffects band.
        if code in _CASCADED_FILTER_CODES:
            slope_warnings.append(
                f"  band at {freq:.1f} Hz: {_CASCADED_FILTER_NAMES[code]} (code {code}) — "
                f"PEACE cascades multiple biquad filters for this type; "
                f"mapped to Lo/Hi-pass as a best-effort single-band approximation."
            )

        bands.append(
            {
                "frequency": freq,
                "gain": gain,
                "q": q,
                "ee_type": ee_type,
                "code": code,
            }
        )

    if slope_warnings:
        print(f"WARNING: {path.name}: approximated filter(s):", file=sys.stderr)
        for msg in slope_warnings:
            print(msg, file=sys.stderr)

    return preamp, bands


def build_preset_json(preamp: float, bands: list[dict]) -> dict:
    """
    Build the EasyEffects 7.x JSON preset structure for an equalizer.

    Band mode "APO (DR)" matches the EqualizerAPO Digital Recursive
    algorithm — the same filter math that PEACE/APO uses on Windows.
    Equalizer mode "IIR" is the correct mode for per-band APO (DR) filters.
    """
    left: dict[str, dict] = {}
    right: dict[str, dict] = {}

    for i, band in enumerate(bands):
        band_key = f"band{i}"
        band_json = {
            "frequency": band["frequency"],
            "gain": band["gain"],
            "mode": "APO (DR)",
            "mute": False,
            "q": band["q"],
            "slope": "x1",
            "solo": False,
            "type": band.get("ee_type", "Bell"),
            "width": 4.0,
        }
        left[band_key] = band_json
        right[band_key] = dict(band_json)  # same settings for both channels

    preset = {
        "output": {
            "blocklist": [],
            "equalizer#0": {
                "balance": 0.0,
                "bypass": False,
                "input-gain": preamp,
                "left": left,
                "mode": "IIR",
                "num-bands": len(bands),
                "output-gain": 0.0,
                "pitch-left": 0.0,
                "pitch-right": 0.0,
                "right": right,
                "split-channels": False,
            },
            "plugins_order": ["equalizer#0"],
        }
    }
    return preset


def convert_file(
    peace_path: Path,
    output_dir: Path | None,
    skip_zero_gain: bool,
    verbose: bool,
) -> Path:
    """Convert one .peace file; return path to the written .json file."""
    preamp, bands = parse_peace_file(peace_path)

    # Optionally skip flat/zero-gain bands
    if skip_zero_gain:
        skipped = [b for b in bands if b["gain"] == 0.0]
        bands = [b for b in bands if b["gain"] != 0.0]
        if verbose and skipped:
            freqs = ", ".join(f"{b['frequency']} Hz" for b in skipped)
            print(f"  Skipped {len(skipped)} zero-gain band(s): {freqs}")

    if not bands:
        raise ValueError("No EQ bands found (file may be empty or all gains are 0)")

    unknown_codes = {b["code"] for b in bands if b["code"] not in PEACE_CODE_TO_EE}
    if unknown_codes:
        print(f"  WARNING: {peace_path.name}: unknown filter code(s) {sorted(unknown_codes)}; defaulted to Bell", file=sys.stderr)

    preset = build_preset_json(preamp, bands)

    stem = peace_path.stem
    dest_dir = output_dir if output_dir else peace_path.parent
    out_path = dest_dir / f"{stem}.json"

    dest_dir.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(preset, fh, indent=4)

    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert PEACE Equalizer .peace profiles to EasyEffects JSON presets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s *.peace
  %(prog)s *.peace --output-dir ~/.config/easyeffects/output
  %(prog)s profile.peace --skip-zero-gain --verbose

After conversion, copy the .json files to one of:
  ~/.config/easyeffects/output/                                  (native)
  ~/.var/app/com.github.wwmm.easyeffects/config/easyeffects/output/  (Flatpak)
""",
    )
    parser.add_argument("files", nargs="+", metavar="FILE.peace", help=".peace file(s) to convert")
    parser.add_argument(
        "--output-dir", "-o",
        metavar="DIR",
        help="Directory for output .json files (default: same directory as input)",
    )
    parser.add_argument(
        "--skip-zero-gain",
        action="store_true",
        default=False,
        help="Omit EQ bands whose gain is exactly 0 dB (they have no effect)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Print extra information during conversion",
    )

    args = parser.parse_args()
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else None

    success = 0
    errors = 0

    for file_arg in args.files:
        peace_path = Path(file_arg).expanduser()
        if not peace_path.exists():
            print(f"ERROR: File not found: {peace_path}", file=sys.stderr)
            errors += 1
            continue

        if args.verbose:
            print(f"Converting {peace_path.name} ...")
        try:
            out = convert_file(peace_path, output_dir, args.skip_zero_gain, args.verbose)
            print(f"OK  {peace_path.name}  →  {out}")
            success += 1
        except Exception as exc:
            print(f"ERROR: {peace_path.name}: {exc}", file=sys.stderr)
            errors += 1

    print(f"\n{success} converted, {errors} failed.")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
