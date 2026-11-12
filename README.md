# Proton Wineland FFXIV

Based on [Proton Salkim](https://github.com/nanomatters/proton-salkim).
A custom build to add (DL)-DSR capabilities. Currently only (tested) for the [FFXIV DLSS Forge](https://github.com/amatiramisu/ffxiv-dlss-forge)

It might be usable for other games, if someone knows what they're doing, they probably could do some interesting things with it.
Just don't cheat on your wife with this.

## Install

Release builds target x86-64-v3 CPUs (AVX2). Extract the archive into `~/.local/share/Steam/compatibilitytools.d/`, then select the build in XIVLauncher-RB.

## Build

Source builds do not include DL-DSR assets. Provide your own kernels and weights from NVIDIA's Windows driver 621.14.

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

`make redist` includes the complete `dldsr-assets/621.14` directory when it exists. Use `make redist DLDSR_ASSETS=/absolute/path/to/assets` to package another directory, or `make redist DLDSR_ASSETS=` to omit the assets.
