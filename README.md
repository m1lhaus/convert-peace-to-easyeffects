# convert_peace

Converts [PEACE Equalizer](https://sourceforge.net/projects/peace-equalizer-apo-extension/) `.peace` profiles to [EasyEffects](https://github.com/wwmm/easyeffects) JSON presets.

PEACE stores equalizer settings in a Windows INI-style format. On Windows, PEACE feeds these to EqualizerAPO at runtime. This script reads the `.peace` file directly and generates a ready-to-use EasyEffects preset — no Windows or EqualizerAPO required.

Supported profile content: **all standard EqualizerAPO filter types** (Bell/Peak, Low/High Shelf, Low/High Pass, Band-pass, Notch, All-pass) and input gain (PreAmp). Per-speaker band assignments are ignored; all bands are applied to both left and right channels, which is the correct behaviour for headphone EQ.

## Requirements

- Python 3.10+
- No third-party packages — standard library only

## Usage

```bash
python3 convert_peace.py FILE.peace [FILE.peace ...]
```

### Options

| Flag | Description |
|------|-------------|
| `-o DIR`, `--output-dir DIR` | Write `.json` files to `DIR` instead of the same directory as the input |
| `--skip-zero-gain` | Omit bands whose gain is exactly 0 dB (they have no audible effect) |
| `-v`, `--verbose` | Print per-file details during conversion |

### Examples

```bash
# Convert a single profile, output next to the source file
python3 convert_peace.py _HD660S.peace

# Convert all profiles and deploy directly to EasyEffects (native install)
python3 convert_peace.py *.peace --output-dir ~/.config/easyeffects/output

# Flatpak install
python3 convert_peace.py *.peace \
    --output-dir ~/.var/app/com.github.wwmm.easyeffects/config/easyeffects/output

# Skip flat bands and show details
python3 convert_peace.py *.peace --skip-zero-gain --verbose
```

After conversion, EasyEffects will list the new presets in its preset browser immediately — no restart needed.

## Output format

Each `.peace` file produces one `.json` file with the same stem. The preset uses:

- Plugin: `equalizer#0`
- Band mode: `APO (DR)` — matches the EqualizerAPO Digital Recursive biquad algorithm
- Equalizer mode: `IIR` — correct for APO (DR) per-band filters
- Filter types decoded from the `[Filters]` section: `Bell`, `Lo-shelf`, `Hi-shelf`, `Lo-pass`, `Hi-pass`, `Band-pass`, `Notch`, `All-pass`

### PEACE `[Filters]` code mapping

| Code | APO type | EasyEffects type |
|------|----------|------------------|
| 0 (default) | PK | Bell |
| 1 | LP | Lo-pass |
| 2 | HP | Hi-pass |
| 3 | BP | Band-pass |
| 4 | LS | Lo-shelf |
| 5 | HS | Hi-shelf |
| 6 | NO | Notch |
| 7 | AP | All-pass |
| 8 | LS 6dB | Lo-shelf |
| 9 | HS 6dB | Hi-shelf |
| 10 | LS 12dB | Lo-shelf |
| 11 | HS 12dB | Hi-shelf |
| 12 | LPQ | Lo-pass |
| 13 | HPQ | Hi-pass |
| 14 | LSC | Lo-shelf |
| 15 | HSC | Hi-shelf |
