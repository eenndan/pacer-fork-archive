// This file based on ImGui's demo app with ImPlot's demo sprinkled on top.
// I'm gonna leave all boilerplate for now.

// Dear ImGui: standalone example application for GLFW + OpenGL 3, using
// programmable pipeline (GLFW is a cross-platform general purpose library for
// handling windows, inputs, OpenGL/Vulkan/Metal graphics context creation,
// etc.)

// Learn about Dear ImGui:
// - FAQ                  https://dearimgui.com/faq
// - Getting Started      https://dearimgui.com/getting-started
// - Documentation        https://dearimgui.com/docs (same as your local docs/
// folder).
// - Introduction, links and more at the top of imgui.cpp

#include <iostream>
#include <sstream>

#include "imgui.h"

#include "implot.h"
#include "implot_internal.h"
#include <hello_imgui/hello_imgui.h>

#include <pacer/datatypes/datatypes.hpp>
#include <pacer/geometry/geometry.hpp>
#include <pacer/gps-source/gps-source.hpp>
#include <pacer/laps-display/laps-display.hpp>
#include <pacer/laps/laps.hpp>

#include <unordered_map>
#include <vector>

// [Win32] Our example includes a copy of glfw3.lib pre-compiled with VS2010 to
// maximize ease of testing and compatibility with old VS compilers. To link
// with VS2010-era libraries, VS2015+ requires linking with
// legacy_stdio_definitions.lib, which we do using this pragma. Your own project
// should not be affected, as you are likely to link with a newer binary of GLFW
// that is adequate for your version of Visual Studio.
#if defined(_MSC_VER) && (_MSC_VER >= 1900) &&                                 \
    !defined(IMGUI_DISABLE_WIN32_FUNCTIONS)
#pragma comment(lib, "legacy_stdio_definitions")
#endif

// This example can also compile and run with Emscripten! See
// 'Makefile.emscripten' for details.
#ifdef __EMSCRIPTEN__
#include "../libs/emscripten/emscripten_mainloop_stub.h"
#endif

using pacer::GPSSample;

void ReadInput(pacer::Laps *plaps) {
  const char *filenames[] = {
      // SP
      "/Users/denys/Documents/gokarting-ui/GH010219.MP4",
      "/Users/denys/Documents/gokarting-ui/GH020219.MP4",
      "/Users/denys/Documents/gokarting-ui/GH030219.MP4",
      "/Users/denys/Documents/gokarting-ui/GH040219.MP4",
      "/Users/denys/Documents/gokarting-ui/GH050219.MP4",
      //// MK
      // "/Users/denys/Downloads/GX010079.MP4",
      // "/Users/denys/Downloads/GX020079.MP4",
      // "/Users/denys/Downloads/GX030079.MP4",
      //// MK
      // "/Users/denys/Pictures/GH010251.MP4",
      // "/Users/denys/Pictures/GH020251.MP4",
      // "/Users/denys/Pictures/GH030251.MP4",
  };

  pacer::GPMFSource mm[] = {
      pacer::GPMFSource(filenames[0]),
      pacer::GPMFSource(filenames[1]),
      pacer::GPMFSource(filenames[2]),
      // pacer::GPMFSource(filenames[3]),
      // pacer::GPMFSource(filenames[4]),
  };
  pacer::SequentialGPSSource m12(&mm[0], &mm[1]), m(&m12, &mm[2]);
  // m14(&m13, &mm[3]), m(&m14, &mm[4]);

  // const char *filename = "/mnt/c/work/gokart-videos/GH010243.MP4";
  // pacer::GPMFSource m(filename);

  m.Seek(0);

  auto &laps = *plaps;

  pacer::CoordinateSystem cs;
  std::unordered_map<int, int> counts(20);

  std::vector<pacer::PointInTime<GPSSample>> samples;

  for (m.Seek(0); !m.IsEnd(); m.Next()) {
    auto [start, end] = m.CurrentTimeSpan();
    m.pacer::RawGPSSource::Samples(
        [&](GPSSample s, size_t current, size_t total) {
          if (s.full_speed > 1e-6) {
            laps.AddPoint(s, start + (end - start) / total * current);
          }
        });
  }
}

void ReadInputDat(pacer::Laps *plaps) {
  pacer::ReadDatFile(
      "/Users/denys/Downloads/1749283873879948155.dat",
      [&](pacer::GPSSample sample, double time) {
        plaps->AddPoint(sample, time);
        std::cerr << "Added sample: " << sample << " at time: " << time
                  << std::endl;
      },
      pacer::DatVersion::WITH_TIMESTAMP);
}

// Main code
int main(int, char **) {
  pacer::Laps full_laps;
  ReadInput(&full_laps);

  full_laps.sectors.start_line = full_laps.PickRandomStart();
  auto laps = full_laps;

  auto laps_display = pacer::LapsDisplay{&laps};
  pacer::DeltaLapsComparison delta;

  float duration =
            laps.GetPoint(laps.PointCount() - 1).time - laps.GetPoint(0).time,
        current = 0;

  auto implotContext = ImPlot::CreateContext();

  HelloImGui::Run(
      [&] {
        laps.Update();

        std::vector<GPSSample> gps;
        static float start = 0, end = full_laps.PointCount();

        if (ImGui::Begin("Data Subset")) {
          ImGui::Text("Select data subset to display on the map");
          ImGui::SetNextItemWidth(ImGui::GetWindowWidth() / 2);
          if (ImGui::SliderFloat("Start", &start, 0, end) ||
              (ImGui::SameLine(), ImGui::SliderFloat("End", &end, start,
                                                     full_laps.PointCount()))) {
            laps.ClearPoints();
            for (size_t i = start; i < end; ++i) {
              auto [gps, time] = full_laps.GetPoint(i);
              laps.AddPoint(gps, time);
            }
          }
        }
        ImGui::End();

        delta.cs = laps_display.cs;
        static int old_selected_lap = laps_display.selected_lap;
        if (old_selected_lap != laps_display.selected_lap) {
          float width = delta.reference_lap.width;
          delta.reference_lap = laps.GetLap(laps_display.selected_lap);
          delta.reference_lap.width = width;
        }

        if (ImGui::Begin("Map")) {
          if (ImPlot::BeginPlot("GPS", ImVec2(-1, -1), ImPlotFlags_Equal)) {
            laps_display.DisplayMap();
            auto getter = [](int index, void *data) {
              auto &[gps, ld] = *reinterpret_cast<
                  std::pair<std::vector<GPSSample> &, pacer::LapsDisplay &> *>(
                  data);
              return ld.ToImPlotPoint(gps[index]);
            };

            std::pair<std::vector<GPSSample> &, pacer::LapsDisplay &> data = {
                gps, laps_display};

            ImPlot::PlotScatterG("data", getter, &data, (int)gps.size());

            if (!gps.empty()) {
              std::stringstream ss;
              ss << "Speed: " << gps.back().full_speed * 3.6 << "km/h";
              auto point = laps_display.ToImPlotPoint(gps.back());
              ImPlot::PlotText(ss.str().data(), point[0], point[1]);
            }
            delta.PlotSticks();
            ImPlot::EndPlot();
          }
        }
        ImGui::End();

        if (ImGui::Begin("Laps")) {
          delta.DrawSlider();
          ImGui::SameLine();
          laps_display.DisplayTable();
        }
        ImGui::End();

        if (ImGui::Begin("Delta")) {
          delta.Display(laps);
        }
        ImGui::End();

        if (ImGui::Begin("Lap Telemetry")) {
          laps_display.DisplayLapTelemetry();
        }
        ImGui::End();
      },
      "Pacer Timeline", true);

  ImPlot::DestroyContext(implotContext);

  return 0;
}
