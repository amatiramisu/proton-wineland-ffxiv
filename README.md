# Proton Wineland FFXIV

A Proton build for FFXIV through XIVLauncher, based on [Proton Salkim](https://github.com/nanomatters/proton-salkim). It adds DSR-style supersampling under Wayland and Dalamud hook compatibility, and is the companion to [FFXIV DLSS Forge](https://github.com/amatiramisu/ffxiv-dlss-forge), which selects the DSR desktop resolution in-game.

## Install

Releases are built for x86-64-v3 CPUs (AVX2). Extract the archive into `~/.local/share/Steam/compatibilitytools.d/`, then select the build in XIVLauncher.

## Use

Set FFXIV to Borderless Windowed and choose the desktop resolution in [FFXIV DLSS Forge](https://github.com/amatiramisu/ffxiv-dlss-forge). Wine reduces the larger desktop to the monitor's native resolution.

The modes are NVIDIA's DSR factors from 1.20× to 4.00×. `WINE_DSR_FACTORS` in the launch environment selects other factors up to 4, or `0` for none.

## Changes

- Desktop modes above the native resolution, reduced to the monitor by the Vulkan swapchain. Whole factors average pixel blocks.
- Borderless windows stay fullscreen during a DSR mode change.
- Executables load at a dynamic base, leaving room for Dalamud hooks. From GE-Proton.
- The primary monitor follows KDE Plasma and GNOME, also without Steam running.

The Wine patches are in `patches/wine/`.

## Build

```sh
mkdir build && cd build
CFLAGS="-O3 -march=x86-64-v3 -mtune=core-avx2" ../configure.sh --enable-ccache --build-name=<name>
make redist
```

See the Proton Salkim README for its own features and options.
