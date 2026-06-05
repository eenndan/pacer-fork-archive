add_pacer_library(interpolation SOURCES interpolation.cpp HEADERS interpolation.hpp)
target_link_libraries(pacer_interpolation PUBLIC pacer::geometry pacer::datatypes)
