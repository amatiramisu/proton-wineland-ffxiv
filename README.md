# Proton Wineland FFXIV

A Proton build for FFXIV through XIVLauncher, based on [Proton Salkim](https://github.com/nanomatters/proton-salkim). It adds DSR-style supersampling under Wayland and Dalamud hook compatibility, and is the companion to [FFXIV DLSS Forge](https://github.com/amatiramisu/ffxiv-dlss-forge), which selects the DSR desktop resolution in-game.

## Install

Releases are built for x86-64-v3 CPUs (AVX2). Extract the archive into `~/.local/share/Steam/compatibilitytools.d/`, then select the build in XIVLauncher.

## Use

Set FFXIV to Borderless Windowed. In [FFXIV DLSS Forge](https://github.com/amatiramisu/ffxiv-dlss-forge), select Off, DSR or DL-DSR and the game output resolution. The method and smoothness can change during play. Wine rebuilds its presentation swapchain and reports the active filter to Forge. The controls use Wine display data across supported desktops.

The modes are NVIDIA's DSR factors from 1.20× to 4.00×. `WINE_DSR_FACTORS` in the launch environment selects other factors up to 4, or `0` for none. `WINE_DSR_SMOOTHNESS` sets the startup smoothness from 0 to 100, with a default of 0. Explicit Forge settings take priority during play.

Native NVIDIA DL-DSR supports 1.78× and 2.25× SDR modes with the kernels and weights of NVIDIA driver 621.14. Release archives include these assets in `files/share/dldsr/621.14`. Proton selects them before Wine starts, while preserving an existing `WINE_DLDSR_PATH` setting. Extracting the release into Steam's compatibility-tools directory also installs the assets.

Wine prepares the optional Vulkan capabilities at device creation. Forge can then switch between DSR and DL-DSR during play. The panel shows capability and setup failures, including Gaussian fallback.

## Changes

- Desktop modes above the native resolution, reduced through a linear-light Gaussian filter or native NVIDIA DL-DSR.
- Per-window filter and smoothness controls, with physical display queries and active-filter status for Forge.
- Borderless windows stay fullscreen during a DSR mode change.
- Executables load at a dynamic base, leaving room for Dalamud hooks. From GE-Proton.
- The primary monitor follows KDE Plasma and GNOME, also without Steam running.

The Wine patches are in `patches/wine/`.

## Build

Source builds work without DL-DSR assets. To include DL-DSR, provide your own kernels and weights from NVIDIA's Windows driver 621.14. Generated NVIDIA binaries stay outside Git.

Extract `Display.Driver/nvlddmkm.sys` from the [621.14 driver installer](https://us.download.nvidia.com/Windows/621.14/621.14-desktop-win10-win11-64bit-international-dch-whql.exe), then run the extractor from the repository root:

```sh
mkdir -p dldsr-assets
python3 tools/extract-dldsr.py /path/to/nvlddmkm.sys dldsr-assets/621.14
```

The extractor needs Python 3 and the matching driver file. The output directory must be new, with an existing parent directory.

```sh
mkdir build && cd build
CFLAGS="-O3 -march=x86-64-v3 -mtune=core-avx2" ../configure.sh --enable-ccache --build-name=<name>
make redist
```

`make redist` includes the complete `dldsr-assets/621.14` directory when it exists. Use `make redist DLDSR_ASSETS=/absolute/path/to/assets` to package another directory, or `make redist DLDSR_ASSETS=` to omit the assets. Packaging copies the entire directory without a fixed file list or checksum checks.

For a build without bundled assets, set `WINE_DLDSR_PATH` to your prepared asset directory in the game's launch environment before starting the game.

See the Proton Salkim README for its own features and options.
