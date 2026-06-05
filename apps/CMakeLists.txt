hello_imgui_add_app(timeline timeline.cpp)

target_link_libraries(timeline PRIVATE
    pacer::laps
    pacer::gps-source
    pacer::laps-display
    pacer::interpolation
)

# nlohmann/json is vendored inside hello_imgui; use it for the input config.
target_include_directories(timeline PRIVATE
    ${CMAKE_SOURCE_DIR}/3rdparty/hello_imgui/external/nlohmann_json
)
