###############################################################################
#                                    gpmf                                     #
###############################################################################

add_library(gpmf
    ${CMAKE_CURRENT_SOURCE_DIR}/gpmf-parser/GPMF_parser.c
    ${CMAKE_CURRENT_SOURCE_DIR}/gpmf-parser/GPMF_utils.c
    ${CMAKE_CURRENT_SOURCE_DIR}/gpmf-parser/demo/GPMF_mp4reader.c
    ${CMAKE_CURRENT_SOURCE_DIR}/gpmf-parser/demo/GPMF_print.c
)
target_include_directories(gpmf PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/gpmf-parser)
set_property(TARGET gpmf PROPERTY FOLDER "3rdparty")
add_library(gpmf::gpmf ALIAS gpmf)

###############################################################################
#                                  nanobind                                   #
###############################################################################

# nanobind drives the Python bindings.
add_subdirectory(nanobind)
