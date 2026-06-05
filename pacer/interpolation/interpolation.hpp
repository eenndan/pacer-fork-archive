#pragma once

#include <utility>
#include <vector>

#include <pacer/datatypes/datatypes.hpp>
#include <pacer/geometry/geometry.hpp>

namespace pacer {

// Gradient-descent (Adam) options for timestamp interpolation.
struct AdamOptions {
  std::vector<double> learning_rates = {1e-1, 1e-2, 1e-3};
  int iterations_per_rate = 100;
  double beta1 = 0.9;
  double beta2 = 0.999;
  double epsilon = 1e-8;
};

// Per-sample inputs to the optimizer. All three vectors have the same length N.
//   floor[i], ceil[i] : the time interval the i-th sample must fall within
//                       (e.g. a video frame's [start, end] span).
//   di[i]             : expected number of sampling steps between sample i-1
//                       and i (di[0] == 1). Drives the spacing model.
struct InterpolationInput {
  std::vector<double> floor;
  std::vector<double> ceil;
  std::vector<double> di;
};

struct InterpolationResult {
  std::vector<double> timestamps; // recovered per-sample timestamps (length N)
  double phase = 0;               // fitted t[0]
  double frequency = 0;           // fitted sampling frequency
  double loss = 0;                // final loss value
};

// The loss from the notebook: variance of di-normalized spacing plus the mean
// squared violation of the [floor, ceil] bounds. Exposed for tests / parity.
double InterpolationLoss(const InterpolationInput &input,
                         const std::vector<double> &t);

// Recover per-sample timestamps with the parametric model
//   t[i] = phase + (cumsum(di)[i] - 1) / frequency
// by fitting {phase, frequency} with Adam. Because this model forces a constant
// di-normalized spacing, the spacing-variance term is identically zero and the
// fit is driven entirely by keeping t within [floor, ceil]. No autodiff: the
// gradient w.r.t. {phase, frequency} is computed analytically.
InterpolationResult InterpolateTimestamps(const InterpolationInput &input,
                                          double initial_frequency,
                                          const AdamOptions &opts = {});

struct DiResult {
  std::vector<double> di;
  double rough_frequency = 1.0;
};

// Build the di vector and a rough sampling frequency from GPS samples and their
// frame spans, matching the notebook:
//   rough_frequency = #samples / #distinct spans
//   di[i] = round(distance(s[i-1], s[i]) / avg_speed * rough_frequency)
// di[0] is 1 and every di is clamped to >= 1 (a 0 would divide by zero in the
// spacing term and collapse the parametric timeline).
DiResult ComputeDi(const std::vector<GPSSample> &samples,
                   const std::vector<std::pair<double, double>> &spans,
                   const CoordinateSystem &cs);

// Convenience: derive floor/ceil/di from samples + spans + coordinate system
// and run the optimizer. `spans` must be the same length as `samples`.
InterpolationResult
InterpolateTimestamps(const std::vector<GPSSample> &samples,
                      const std::vector<std::pair<double, double>> &spans,
                      const CoordinateSystem &cs, const AdamOptions &opts = {});

} // namespace pacer
