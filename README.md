# pacer

This is my project for analysing telemetry data from races.
It's still work-in-progress, but the worst of the early-days jank has been cleaned
up: input paths are no longer baked into the binary, timestamp interpolation now
runs in C++, there's a small test suite, and the build is green again.

## getting started

### env

I use [pixi](https://pixi.sh) for external dependency management.
It is much better than `conda`/`mamba`/`micromamba` while leveraging the same infrastructure.
I use `cmake` for building binary stuff, `litgen` for code-generation of bindings and `scikit-build` to glue two together via `pyproject.toml`.
Is it the best setup? No. Does it work? Somewhat.

```bash
git submodule update --init --recursive  # 3rdparty/ deps (imgui, implot, gpmf, hello_imgui, nanobind)
pixi install                              # env + editable python bindings
```

After that, there are pixi tasks for the common stuff (no need to memorize commands):

```bash
pixi run build      # configure + build everything (cmake + Ninja)
pixi run test       # run the C++ (Catch2) tests via ctest
pixi run test-py    # C++ vs PyTorch interpolation parity check
pixi run timeline   # build + launch the GUI app
pixi run fmt        # clang-format the C/C++ sources
pixi run web        # build the WASM/web app (needs an Emscripten SDK)
```

### what to do?

There're two good places to get started:

- `timeline` app — the GUI (map, lap table, delta plots). Point it at your files
  via CLI args or a config: `pixi run timeline -- a.MP4 b.MP4` or copy
  `pacer.example.json` to `pacer.json`. It uses the C++ gradient-descent timestamp
  interpolation automatically;
- `notebooks/interpolation.ipynb` --- a tidied notebook walking through loading
  GoPro GPS, recovering timestamps with `pacer.interpolate_timestamps`, lap
  segmentation and lap-delta plots (set `PACER_DATA` to your video folder).

## components

Some components:

- `pacer/`: bread and butter --- main library code, mixed C++ and Python stuff;
- `apps/timeline.cpp`: main app: consists of bunch of different views on top of parsed data;
- `examples/`: bunch of examples of usage of 3rd party dependencies (e.g. implot, imgui, gpmf-parser);
- `notebooks/`: me hacking stuff and never tyding it up;
- `libs/`: parser for telemetry data, laps mangling, some geometry utilities;
- `bindings/`: python bindings that semi-automatically generated from C++ source code, not too stable.

## future ideas

Still in progress:

- actual video-feed inside `timeline` app;
- more on-line approach, e.g. to use live in session for comments like:
  - too much wheelspin on exit;
  - too little braking on entry;
  - shorter line is better;
  - keep minimum speed higher;
  - etc.
- finish the emscripten/web app (the build target is scaffolded; in-browser file
  loading still needs preloading into the emscripten FS);
- keep chipping away at the code (it's much better, but still WIP).

Wow, something already done:

- lap segmentation, comparison between laps with delta;
- nanobind-based python bindings to rapidly experiment in python;
- integration with 3rd party gps data, e.g. from sampled file, consider building ios app for capturing;
- timestamp interpolation in C++ (gradient descent), exposed to Python and used
  by the `timeline` app — verified to match the original PyTorch implementation;
- config/CLI-driven inputs (no more hard-coded paths), formatting/lint config,
  pixi tasks, and a Catch2 test suite (ops, geometry, laps, interpolation).

## credits

It's ain't much, but it's honest work.

pacer © 2025 by Denys Smirnov is licensed under CC BY-NC-SA 4.0.
To view a copy of this license, visit <https://creativecommons.org/licenses/by-nc-sa/4.0/>
