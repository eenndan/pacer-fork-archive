hello_imgui_add_app(timeline timeline.cpp)

target_link_libraries(timeline PRIVATE
    pacer::laps
    pacer::gps-source
    pacer::laps-display
)
