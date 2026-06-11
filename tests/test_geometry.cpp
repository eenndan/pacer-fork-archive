#include <cmath>

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include <pacer/datatypes/datatypes.hpp>
#include <pacer/geometry/geometry.hpp>

using namespace pacer;

// ---------------------------------------------------------------------------
// Segment::Intersects
// ---------------------------------------------------------------------------

TEST_CASE("Segment::Intersects — vertical timing line at x=0",
          "[geometry][segment]") {
  // Segment along x=0, from (0,-1) to (0,1).
  Segment s{.first = {0, -1}, .second = {0, 1}};

  SECTION("crossing pair writes ratio ~0.5") {
    double ratio = -1.0;
    bool hit = s.Intersects(Point{-1, 0}, Point{1, 0}, &ratio);
    REQUIRE(hit);
    // norm = (second-first).Rot() = {0,2}.Rot() = {-2,0}
    // d1 = norm . (snd - first) = {-2,0}.{1,1}  = -2
    // d2 = norm . (fst - first) = {-2,0}.{-1,1} =  2
    // ratio = |d2| / (|d1| + |d2|) = 2 / 4 = 0.5
    CHECK(ratio == Catch::Approx(0.5));
  }

  SECTION("non-crossing pair (both on same side, x > 0) returns false") {
    double ratio = -1.0;
    bool hit = s.Intersects(Point{1, 0}, Point{2, 0}, &ratio);
    REQUIRE_FALSE(hit);
  }

  SECTION("crossing closer to fst end writes ratio ~0.75") {
    double ratio = -1.0;
    bool hit = s.Intersects(Point{-3, 0}, Point{1, 0}, &ratio);
    REQUIRE(hit);
    // d1 = {-2,0}.{1,1}  = -2
    // d2 = {-2,0}.{-3,1} =  6
    // ratio = |d2| / (|d1| + |d2|) = 6 / 8 = 0.75
    CHECK(ratio == Catch::Approx(0.75));

    // Verify crossing point lies on the segment:
    // crossing = fst*(1-ratio) + snd*ratio
    //          = {-3,0}*0.25 + {1,0}*0.75 = {-0.75+0.75, 0} = {0, 0}
    // x=0 is indeed on s.
    Point fst{-3, 0}, snd{1, 0};
    Point crossing = fst * (1 - ratio) + snd * ratio;
    CHECK(crossing.x == Catch::Approx(0.0));
  }

  SECTION("null ratio pointer is safe (no crash)") {
    bool hit = s.Intersects(Point{-1, 0}, Point{1, 0}, nullptr);
    REQUIRE(hit);
  }
}

// ---------------------------------------------------------------------------
// Segment::Intersects / Split — exact-on-line edge conventions
//
// These pin the boundary conventions the lap/sector timing path relies on
// (laps.cpp Update() calls Split per consecutive trace-point pair). Both
// straddle tests in Intersects use `>= 0 -> false`, i.e. STRICT proper
// crossings only. Consequences pinned below:
//   * a trace VERTEX lying EXACTLY on the timing line is owned by NEITHER
//     adjacent trace segment — both report no crossing, so the crossing is
//     dropped entirely (zero, not one). With real GPS doubles an exact zero
//     essentially never occurs; nudge the vertex any epsilon off the line and
//     exactly ONE of the two adjacent segments crosses (the one whose other
//     endpoint is on the far side).
//   * a tangential touch (the timing line's ENDPOINT grazing the trace
//     segment) is no crossing either.
// ---------------------------------------------------------------------------

TEST_CASE("Segment::Intersects — trace vertex exactly on the timing line",
          "[geometry][segment][edge]") {
  // Timing line along x=0, from (0,-1) to (0,1).
  Segment s{.first = {0, -1}, .second = {0, 1}};
  // Trace A -> B -> C where B sits EXACTLY on the line (x == 0), strictly
  // inside the line's span.
  const Point a{-1, 0.2}, c{1, 0.0};

  SECTION("exact vertex: NEITHER adjacent segment crosses (zero total)") {
    const Point b{0, 0.1};
    double ratio = -7.0; // sentinel: must stay untouched on false returns
    CHECK_FALSE(s.Intersects(a, b, &ratio));
    CHECK_FALSE(s.Intersects(b, c, &ratio));
    // Contract: ratio is written only on a true return.
    CHECK(ratio == -7.0);
  }

  SECTION("vertex nudged BEFORE the line: only the outgoing segment crosses") {
    const Point b{-1e-9, 0.1};
    double ratio = -7.0;
    CHECK_FALSE(s.Intersects(a, b, &ratio)); // both endpoints still left of x=0
    CHECK(ratio == -7.0);
    REQUIRE(s.Intersects(b, c, &ratio)); // crosses essentially AT b
    CHECK(ratio == Catch::Approx(0.0).margin(1e-6));
  }

  SECTION("vertex nudged PAST the line: only the incoming segment crosses") {
    const Point b{1e-9, 0.1};
    double ratio = -7.0;
    REQUIRE(s.Intersects(a, b, &ratio)); // crosses essentially AT b
    CHECK(ratio == Catch::Approx(1.0).margin(1e-6));
    ratio = -7.0;
    CHECK_FALSE(s.Intersects(b, c, &ratio)); // both endpoints right of x=0
    CHECK(ratio == -7.0);
  }
}

TEST_CASE("Segment::Intersects — tangential touch (line endpoint grazes the "
          "trace segment) is no crossing",
          "[geometry][segment][edge]") {
  // Timing line from (0,0) to (0,1): its endpoint `first` == (0,0) lies
  // EXACTLY on the trace segment (-1,0) -> (1,0). The first straddle test
  // (line endpoints vs the trace's supporting line) hits exactly 0 -> false.
  Segment s{.first = {0, 0}, .second = {0, 1}};
  double ratio = -7.0;
  CHECK_FALSE(s.Intersects(Point{-1, 0}, Point{1, 0}, &ratio));
  CHECK(ratio == -7.0);

  // Same convention from the other side: trace grazing the OTHER endpoint.
  Segment s2{.first = {0, -1}, .second = {0, 0}};
  CHECK_FALSE(s2.Intersects(Point{-1, 0}, Point{1, 0}, &ratio));
  CHECK(ratio == -7.0);
}

namespace {
PointInTime<GPSSample> TracePoint(double lon, double lat, double t) {
  return PointInTime<GPSSample>{
      .point = GPSSample{.lat = lat,
                         .lon = lon,
                         .altitude = 0,
                         .full_speed = 0,
                         .ground_speed = 0,
                         .timestamp_ms = 0},
      .time = t,
  };
}
} // namespace

TEST_CASE("Split — vertex exactly on the timing line is dropped by both "
          "adjacent trace segments",
          "[geometry][split][edge]") {
  // Split is the timing-path entry (laps.cpp Update()): segments are in
  // lon/lat degrees via ToLonLat (Point{lon, lat}). Same geometry as above.
  Segment line{.first = {0, -1}, .second = {0, 1}};
  auto a = TracePoint(-1.0, 0.2, /*t=*/10.0);
  auto c = TracePoint(1.0, 0.0, /*t=*/12.0);

  SECTION("exact vertex: both Splits return nullopt (crossing lost)") {
    auto b = TracePoint(0.0, 0.1, /*t=*/11.0);
    CHECK_FALSE(Split(line, a, b).has_value());
    CHECK_FALSE(Split(line, b, c).has_value());
  }

  SECTION("epsilon-off vertex: exactly one Split fires, at the vertex's time") {
    auto b = TracePoint(-1e-9, 0.1, /*t=*/11.0);
    CHECK_FALSE(Split(line, a, b).has_value());
    auto hit = Split(line, b, c);
    REQUIRE(hit.has_value());
    CHECK(hit->time == Catch::Approx(11.0).margin(1e-6));
  }
}

// ---------------------------------------------------------------------------
// Interpolate(Point, Point, ratio)
// ---------------------------------------------------------------------------

TEST_CASE("Interpolate Point — midpoint and endpoints",
          "[geometry][interpolate]") {
  Point from{0, 0};
  Point to{10, 20};

  SECTION("ratio 0.5 gives midpoint") {
    auto mid = Interpolate(from, to, 0.5);
    CHECK(mid.x == Catch::Approx(5.0));
    CHECK(mid.y == Catch::Approx(10.0));
  }

  SECTION("ratio 0 gives from") {
    auto p = Interpolate(from, to, 0.0);
    CHECK(p.x == Catch::Approx(from.x));
    CHECK(p.y == Catch::Approx(from.y));
  }

  SECTION("ratio 1 gives to") {
    auto p = Interpolate(from, to, 1.0);
    CHECK(p.x == Catch::Approx(to.x));
    CHECK(p.y == Catch::Approx(to.y));
  }
}

// ---------------------------------------------------------------------------
// Interpolate(GPSSample, GPSSample, ratio)  — timestamp_ms bug regression
// ---------------------------------------------------------------------------

TEST_CASE("Interpolate GPSSample — all fields including timestamp_ms",
          "[geometry][interpolate][regression]") {
  GPSSample from{
      .lat = 0,
      .lon = 0,
      .altitude = 0,
      .full_speed = 0,
      .ground_speed = 0,
      .timestamp_ms = 0,
  };
  GPSSample to{
      .lat = 10,
      .lon = 20,
      .altitude = 100,
      .full_speed = 30,
      .ground_speed = 25,
      .timestamp_ms = 1000,
  };

  SECTION("ratio 0.5 interpolates all fields, timestamp_ms must NOT be 0") {
    auto mid = Interpolate(from, to, 0.5);
    CHECK(mid.lat == Catch::Approx(5.0));
    CHECK(mid.lon == Catch::Approx(10.0));
    CHECK(mid.altitude == Catch::Approx(50.0));
    CHECK(mid.full_speed == Catch::Approx(15.0));
    CHECK(mid.ground_speed == Catch::Approx(12.5));
    // Regression: timestamp_ms was previously always 0 (dropped).
    CHECK(mid.timestamp_ms == 500);
  }

  SECTION("ratio 0 returns from (including timestamp_ms == 0)") {
    auto p = Interpolate(from, to, 0.0);
    CHECK(p.lat == Catch::Approx(from.lat));
    CHECK(p.lon == Catch::Approx(from.lon));
    CHECK(p.altitude == Catch::Approx(from.altitude));
    CHECK(p.full_speed == Catch::Approx(from.full_speed));
    CHECK(p.ground_speed == Catch::Approx(from.ground_speed));
    CHECK(p.timestamp_ms == from.timestamp_ms);
  }

  SECTION("ratio 1 returns to (including timestamp_ms == 1000)") {
    auto p = Interpolate(from, to, 1.0);
    CHECK(p.lat == Catch::Approx(to.lat));
    CHECK(p.lon == Catch::Approx(to.lon));
    CHECK(p.altitude == Catch::Approx(to.altitude));
    CHECK(p.full_speed == Catch::Approx(to.full_speed));
    CHECK(p.ground_speed == Catch::Approx(to.ground_speed));
    CHECK(p.timestamp_ms == to.timestamp_ms);
  }
}

// ---------------------------------------------------------------------------
// ToPoint overloads
// ---------------------------------------------------------------------------

TEST_CASE("ToPoint overloads", "[geometry][topoint]") {
  SECTION("ToLonLat(GPSSample) returns Point{lon, lat}") {
    // geometry.cpp: return {s.lon, s.lat}  =>  x=lon, y=lat
    GPSSample s{.lat = 5,
                .lon = 7,
                .altitude = 0,
                .full_speed = 0,
                .ground_speed = 0,
                .timestamp_ms = 0};
    auto p = ToLonLat(s);
    CHECK(p.x == Catch::Approx(7.0));
    CHECK(p.y == Catch::Approx(5.0));
  }

  SECTION("ToPoint(Point) is identity") {
    Point orig{3, 4};
    auto p = ToPoint(orig);
    CHECK(p.x == Catch::Approx(3.0));
    CHECK(p.y == Catch::Approx(4.0));
  }

  SECTION("ToPoint(Vec3f) takes x and y, drops z") {
    Vec3f v{1, 2, 3};
    auto p = ToPoint(v);
    CHECK(p.x == Catch::Approx(1.0));
    CHECK(p.y == Catch::Approx(2.0));
  }
}

// ---------------------------------------------------------------------------
// CoordinateSystem — round-trip and Distance
// ---------------------------------------------------------------------------

TEST_CASE("CoordinateSystem round-trip Global(Local(p)) == p",
          "[geometry][coordinate_system]") {
  GPSSample origin{
      .lat = 40.0,
      .lon = -74.0,
      .altitude = 10.0,
      .full_speed = 0,
      .ground_speed = 0,
      .timestamp_ms = 0,
  };
  CoordinateSystem cs(origin);

  SECTION("origin maps to local zero then back") {
    auto local = cs.Local(origin);
    CHECK(local.x == Catch::Approx(0.0).margin(1e-4));
    CHECK(local.y == Catch::Approx(0.0).margin(1e-4));
    CHECK(local.z == Catch::Approx(0.0).margin(1e-4));
  }

  SECTION("round-trip Global(Local(p)) recovers p within 1e-6 relative") {
    auto local = cs.Local(origin);
    auto recovered = cs.Global(local);
    CHECK_THAT(recovered.lat,
               Catch::Matchers::WithinRelMatcher(origin.lat, 1e-6));
    CHECK_THAT(recovered.lon,
               Catch::Matchers::WithinRelMatcher(origin.lon, 1e-6));
    CHECK_THAT(recovered.altitude,
               Catch::Matchers::WithinRelMatcher(origin.altitude, 1e-6));
  }

  SECTION("round-trip holds for a nearby point") {
    GPSSample nearby{
        .lat = 40.001,
        .lon = -73.999,
        .altitude = 20.0,
        .full_speed = 0,
        .ground_speed = 0,
        .timestamp_ms = 0,
    };
    auto local = cs.Local(nearby);
    auto recovered = cs.Global(local);
    CHECK_THAT(recovered.lat,
               Catch::Matchers::WithinRelMatcher(nearby.lat, 1e-6));
    CHECK_THAT(recovered.lon,
               Catch::Matchers::WithinRelMatcher(nearby.lon, 1e-6));
    CHECK_THAT(recovered.altitude,
               Catch::Matchers::WithinRelMatcher(nearby.altitude, 1e-6));
  }
}

TEST_CASE(
    "CoordinateSystem::Distance is symmetric and zero for identical points",
    "[geometry][coordinate_system]") {
  GPSSample a{.lat = 40.0,
              .lon = -74.0,
              .altitude = 10.0,
              .full_speed = 0,
              .ground_speed = 0,
              .timestamp_ms = 0};
  GPSSample b{.lat = 40.01,
              .lon = -73.99,
              .altitude = 50.0,
              .full_speed = 0,
              .ground_speed = 0,
              .timestamp_ms = 0};

  CoordinateSystem cs(a);

  SECTION("distance from a point to itself is zero") {
    CHECK(cs.Distance(a, a) == Catch::Approx(0.0).margin(1e-6));
    CHECK(cs.Distance(b, b) == Catch::Approx(0.0).margin(1e-6));
  }

  SECTION("distance is symmetric") {
    double d_ab = cs.Distance(a, b);
    double d_ba = cs.Distance(b, a);
    CHECK(d_ab == Catch::Approx(d_ba));
  }

  SECTION("distance between distinct points is positive") {
    CHECK(cs.Distance(a, b) > 0.0);
  }
}

TEST_CASE("Default-constructed CoordinateSystem is a usable identity (ECEF) "
          "frame, not silent zeros",
          "[geometry][coordinate_system]") {
  // The members used to default to an all-zero basis, which made every
  // Local()/Distance() silently return 0 for code that forgot to install a
  // track-centred frame. The default is now the identity ECEF basis.
  CoordinateSystem cs;
  GPSSample a{.lat = 40.0,
              .lon = -74.0,
              .altitude = 10.0,
              .full_speed = 0,
              .ground_speed = 0,
              .timestamp_ms = 0};
  GPSSample b{.lat = 40.01,
              .lon = -73.99,
              .altitude = 10.0,
              .full_speed = 0,
              .ground_speed = 0,
              .timestamp_ms = 0};

  SECTION("Local() yields the raw ECEF position in metres (earth-scale)") {
    auto local = cs.Local(a);
    double norm =
        std::sqrt(local.x * local.x + local.y * local.y + local.z * local.z);
    // |ECEF position| is between the polar and (height-compensated)
    // equatorial radius — emphatically not zero.
    CHECK(norm > 6'300'000.0);
    CHECK(norm < 6'400'000.0);
  }

  SECTION("Distance() is the true 3D chord, not zero") {
    // ~0.01 deg lat + 0.01 deg lon near 40N is roughly 1.4 km.
    double d = cs.Distance(a, b);
    CHECK(d > 1'000.0);
    CHECK(d < 2'000.0);
  }

  SECTION("Global(Local(p)) round-trips through the identity frame") {
    auto recovered = cs.Global(cs.Local(a));
    CHECK_THAT(recovered.lat, Catch::Matchers::WithinRelMatcher(a.lat, 1e-9));
    CHECK_THAT(recovered.lon, Catch::Matchers::WithinRelMatcher(a.lon, 1e-9));
    CHECK_THAT(recovered.altitude,
               Catch::Matchers::WithinRelMatcher(a.altitude, 1e-6));
  }
}
