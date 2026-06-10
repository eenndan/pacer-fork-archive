#! /usr/bin/env python3

from pathlib import Path

import litgen

LITGEN_USE_NANOBIND = True


def my_litgen_options() -> litgen.LitgenOptions:
    # configure your options here
    options = litgen.LitgenOptions()
    options.bind_library = litgen.BindLibraryType.nanobind

    # ///////////////////////////////////////////////////////////////////
    #  Root namespace
    # ///////////////////////////////////////////////////////////////////
    # The namespace pacer is the C++ root namespace for the generated bindings
    # (i.e. no submodule will be generated for it in the python bindings)
    options.namespaces_root = ["pacer"]

    # //////////////////////////////////////////////////////////////////
    # Basic functions bindings
    # ////////////////////////////////////////////////////////////////////
    # No specific option is needed for these basic bindings
    # litgen will add the docstrings automatically in the python bindings

    # //////////////////////////////////////////////////////////////////
    # Classes and structs bindings
    # //////////////////////////////////////////////////////////////////
    # No specific option is needed for these bindings.
    # - Litgen will automatically add a default constructor with named parameters
    #   for structs that have no constructor defined in C++.
    #  - A class will publish only its public methods and members

    # ////////////////////////////////////////////////////////////////////
    # Override virtual methods in python
    # ////////////////////////////////////////////////////////////////////
    # RawGPSSource is an abstract base whose virtual methods can be overridden
    # from python (used to feed synthetic / test GPS sources into the engine).
    options.class_override_virtual_methods_in_python__regex = "^RawGPSSource$"

    # ////////////////////////////////////////////////////////////////////
    # Template specializations / ignores for the real pacer types
    # ////////////////////////////////////////////////////////////////////
    # PointInTime<GPSSample> is the only instantiation we expose.
    options.class_template_options.add_specialization("PointInTime", ["GPSSample"])

    # The CRTP operator-mixin bases (ops.hpp) are implementation detail, not API.
    options.class_template_options.add_ignore("VectorOperators")
    options.class_template_options.add_ignore("PointwiseOperators")
    options.class_template_options.add_ignore("LinearOperators")

    # Generic template helpers we don't want auto-specialized into the bindings.
    options.fn_template_options.add_ignore("Interpolate")
    options.fn_template_options.add_ignore("ToPoint")

    # ApproxEqual is an internal geometry helper (the single epsilon-equality used by
    # Segment::operator==), not part of the Python API — keep it off the binding surface.
    options.fn_exclude_by_name__regex = "^ApproxEqual$"

    # ////////////////////////////////////////////////////////////////////
    # Format the python stubs with black
    # ////////////////////////////////////////////////////////////////////
    # Set to True if you want the stub file to be formatted with black
    options.python_run_black_formatter = True

    return options


# ////////////////////////////////////////////////////////////////////////
# Keep child sources alive for the SequentialGPSSource chain (post-write splice)
# ////////////////////////////////////////////////////////////////////////
# SequentialGPSSource(RawGPSSource* left, RawGPSSource* right) borrows both children without
# owning them. litgen has no native keep_alive option (and its postprocess hooks don't see the
# fully-assembled pydef), so after litgen writes the binding we splice the nanobind call policy
# straight into the generated constructor: tie each child's lifetime to the SequentialGPSSource
# (nanobind arg index 1 == `self`/nurse, 2 == left, 3 == right). This is defense-in-depth —
# studio/ingest.chain_sources already holds an `owners` list — so the chain still works if a
# caller drops its references to the children.
#
# The match/replacement use the exact text litgen emits into nanobind_pacer.cpp (6-space `.def`
# indent). A literal (not regex) single replace keeps the edit surgical; if litgen ever changes
# its output so this no longer matches, the splice fails loudly at regen rather than silently
# dropping the policy.
_SEQ_CTOR_BINDING = (
    "      .def(nb::init<pacer::RawGPSSource *, pacer::RawGPSSource *>(),\n"
    '          nb::arg("left"), nb::arg("right"))'
)
_SEQ_CTOR_BINDING_KEEPALIVE = (
    "      .def(nb::init<pacer::RawGPSSource *, pacer::RawGPSSource *>(),\n"
    "          nb::keep_alive<1, 2>(), nb::keep_alive<1, 3>(),\n"
    '          nb::arg("left"), nb::arg("right"))'
)


def _splice_sequential_source_keep_alive(pydef_file: Path) -> None:
    """Inject nb::keep_alive into the generated SequentialGPSSource constructor binding."""
    code = pydef_file.read_text()
    if _SEQ_CTOR_BINDING_KEEPALIVE in code:
        return  # already spliced (idempotent)
    if _SEQ_CTOR_BINDING not in code:
        raise RuntimeError(
            "SequentialGPSSource constructor binding not found in generated "
            f"{pydef_file.name}; litgen output changed — update _SEQ_CTOR_BINDING in "
            "generate-bindings.py (keep_alive was NOT applied)."
        )
    pydef_file.write_text(code.replace(_SEQ_CTOR_BINDING, _SEQ_CTOR_BINDING_KEEPALIVE, 1))


def autogenerate() -> None:
    repository_dir = Path(__file__).parent.parent.parent

    header_files = [
        repository_dir / "pacer/datatypes/datatypes.hpp",
        repository_dir / "pacer/geometry/geometry.hpp",
        repository_dir / "pacer/laps/laps.hpp",
        repository_dir / "pacer/gps-source/gps-source.hpp",
    ]

    output_cpp_pydef_file = repository_dir / "bindings/pacer/nanobind_pacer.cpp"

    litgen.write_generated_code_for_files(
        options=my_litgen_options(),
        input_cpp_header_files=[str(p) for p in header_files],
        output_cpp_pydef_file=output_cpp_pydef_file,
        output_stub_pyi_file=str(repository_dir / "bindings/pacer/pacer/__init__.pyi"),
    )

    # Post-write: add the keep_alive call policy to the SequentialGPSSource constructor binding.
    _splice_sequential_source_keep_alive(output_cpp_pydef_file)


if __name__ == "__main__":
    autogenerate()
