#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include <cmath>
#include <vector>

#include <pacer/datatypes/datatypes.hpp>
#include <pacer/geometry/geometry.hpp>
#include <pacer/interpolation/interpolation.hpp>

using pacer::AdamOptions;
using pacer::CoordinateSystem;
using pacer::GPSSample;
using pacer::InterpolateTimestamps;
using pacer::InterpolationInput;
using pacer::InterpolationLoss;

namespace {
// Uniformly sampled timeline (di == 1): t_true[i] = phase + i / frequency,
// with frame bounds forming a tight band around the truth.
InterpolationInput MakeUniformBands(size_t n, double phase, double frequency,
                                    double half_band) {
  InterpolationInput in;
  in.floor.resize(n);
  in.ceil.resize(n);
  in.di.assign(n, 1.0);
  for (size_t i = 0; i < n; ++i) {
    double t = phase + static_cast<double>(i) / frequency;
    in.floor[i] = t - half_band;
    in.ceil[i] = t + half_band;
  }
  return in;
}
} // namespace

TEST_CASE("InterpolateTimestamps recovers a uniform timeline", "[interp]") {
  const size_t n = 60;
  const double true_phase = 10.0, true_freq = 5.0, half_band = 0.05;
  InterpolationInput in = MakeUniformBands(n, true_phase, true_freq, half_band);

  // Start from a wrong frequency (a real run seeds with rough_frequency, which
  // is close; here we give the optimizer a generous budget to fully converge).
  AdamOptions opts;
  opts.learning_rates = {0.1, 0.03, 0.01, 0.003};
  opts.iterations_per_rate = 200;
  auto res = InterpolateTimestamps(in, /*initial_frequency=*/4.0, opts);

  REQUIRE(res.timestamps.size() == n);

  SECTION("optimization drives the loss down to ~feasible") {
    CHECK(res.loss < 1e-3);
  }

  SECTION("fitted frequency and phase are close to the truth") {
    CHECK(res.frequency == Catch::Approx(true_freq).margin(0.15));
    CHECK(res.phase == Catch::Approx(true_phase).margin(0.1));
  }

  SECTION("recovered timestamps fall within the frame bounds") {
    double max_violation = 0;
    for (size_t i = 0; i < n; ++i) {
      double below = std::max(0.0, in.floor[i] - res.timestamps[i]);
      double above = std::max(0.0, res.timestamps[i] - in.ceil[i]);
      max_violation = std::max(max_violation, below + above);
    }
    CHECK(max_violation < half_band);
  }

  SECTION("timestamps are strictly increasing") {
    for (size_t i = 1; i < n; ++i)
      CHECK(res.timestamps[i] > res.timestamps[i - 1]);
  }
}

TEST_CASE("InterpolationLoss spacing term vanishes for the parametric model",
          "[interp]") {
  // Any constant-frequency timeline has zero di-normalized spacing variance, so
  // the loss equals the constraint term alone (zero when within bounds).
  const size_t n = 20;
  InterpolationInput in = MakeUniformBands(n, 0.0, 4.0, 1.0);
  std::vector<double> t(n);
  for (size_t i = 0; i < n; ++i)
    t[i] = static_cast<double>(i) / 4.0; // exactly within the band
  CHECK(InterpolationLoss(in, t) == Catch::Approx(0.0).margin(1e-12));
}

TEST_CASE("ComputeDi sets di[0]=1 and a positive rough frequency", "[interp]") {
  GPSSample a{.lat = 40.0, .lon = -74.0, .altitude = 0, .full_speed = 10};
  GPSSample b = a;
  b.full_speed = 10;
  CoordinateSystem cs(a);
  // Two samples ~5 m apart in local space.
  auto bb = cs.Global(pacer::Vec3f{5, 0, 0});
  bb.full_speed = 10;

  std::vector<GPSSample> samples{a, bb};
  std::vector<std::pair<double, double>> spans{{0.0, 1.0}, {0.0, 1.0}};
  auto di = pacer::ComputeDi(samples, spans, cs);

  REQUIRE(di.di.size() == 2);
  CHECK(di.di[0] == Catch::Approx(1.0));
  CHECK(di.di[1] >= 1.0);
  CHECK(di.rough_frequency > 0.0);
}
