cmake_minimum_required(VERSION 3.28)

project(pacer)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

set_property(GLOBAL PROPERTY USE_FOLDERS ON)

# Don't give an active VIRTUAL_ENV (e.g. ~/.venvs) priority when resolving Python: use the
# standard PATH-based lookup so the build picks the interpreter on PATH — under `pixi run` that
# is the pixi env (.pixi/envs/default). Without this, a fresh configure with VIRTUAL_ENV exported
# hijacks find_package(Python) to the venv. (STANDARD, not NEVER — NEVER is not a valid value for
# this variable; the accepted values are FIRST/ONLY/STANDARD.)
set(Python_FIND_VIRTUALENV STANDARD)

find_package(Python 3.13
  REQUIRED COMPONENTS Interpreter Development.Module
  OPTIONAL_COMPONENTS Development.SABIModule)

find_package(Catch2 REQUIRED)

enable_testing()  # required for add_test() in tests/ to register with CTest

add_subdirectory(3rdparty)
add_subdirectory(pacer)
add_subdirectory(tests)
add_subdirectory(bindings)
