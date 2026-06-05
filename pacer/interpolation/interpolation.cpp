#include "interpolation.hpp"

#include <cmath>
#include <set>

namespace pacer {

namespace {

// cumsum(di) - 1, so element 0 is always 0.
std::vector<double> CumStep(const std::vector<double> &di) {
  std::vector<double> c(di.size());
  double acc = 0;
  for (size_t i = 0; i < di.size(); ++i) {
    acc += di[i];
    c[i] = acc - 1.0;
  }
  return c;
}

double Mean(const std::vector<double> &v) {
  if (v.empty())
    return 0;
  double s = 0;
  for (double x : v)
    s += x;
  return s / static_cast<double>(v.size());
}

} // namespace

double InterpolationLoss(const InterpolationInput &in,
                         const std::vector<double> &t) {
  const size_t n = t.size();

  // Spacing: variance of the di-normalized inter-sample time deltas.
  std::vector<double> nd;
  nd.reserve(n > 0 ? n - 1 : 0);
  for (size_t j = 0; j + 1 < n; ++j)
    nd.push_back((t[j + 1] - t[j]) / in.di[j + 1]);
  double m = Mean(nd);
  double spacing = 0;
  for (double x : nd)
    spacing += (x - m) * (x - m);
  if (!nd.empty())
    spacing /= static_cast<double>(nd.size());

  // Constraints: mean squared violation of the [floor, ceil] bounds.
  double constraints = 0;
  for (size_t i = 0; i < n; ++i) {
    double below = std::max(0.0, in.floor[i] - t[i]);
    double above = std::max(0.0, t[i] - in.ceil[i]);
    double p = below + above;
    constraints += p * p;
  }
  if (n)
    constraints /= static_cast<double>(n);

  return spacing + constraints;
}

InterpolationResult InterpolateTimestamps(const InterpolationInput &in,
                                          double initial_frequency,
                                          const AdamOptions &opts) {
  InterpolationResult res;
  const size_t n = in.floor.size();
  if (n == 0)
    return res;

  const std::vector<double> C = CumStep(in.di); // C[0] == 0

  double phase = in.floor[0];
  double freq = initial_frequency > 1e-9 ? initial_frequency : 1.0;

  auto compute_t = [&](double p, double f) {
    std::vector<double> t(n);
    for (size_t i = 0; i < n; ++i)
      t[i] = p + C[i] / f;
    return t;
  };

  const double b1 = opts.beta1, b2 = opts.beta2, eps = opts.epsilon;
  const double inv_n2 = 2.0 / static_cast<double>(n);

  // Adam state for the two parameters {phase, frequency}. Reset per learning
  // rate to mirror the notebook, which creates a fresh torch.optim.Adam (and
  // therefore zeroes momentum and the bias-correction step) for each rate.
  double m_p, v_p, m_f, v_f;
  int step;
  auto adam = [&](double g, double &m, double &v, double &param, double lr) {
    m = b1 * m + (1 - b1) * g;
    v = b2 * v + (1 - b2) * g * g;
    double mh = m / (1 - std::pow(b1, step));
    double vh = v / (1 - std::pow(b2, step));
    param -= lr * mh / (std::sqrt(vh) + eps);
  };

  for (double lr : opts.learning_rates) {
    m_p = v_p = m_f = v_f = 0;
    step = 0;
    for (int it = 0; it < opts.iterations_per_rate; ++it) {
      std::vector<double> t = compute_t(phase, freq);

      // The spacing term is constant (==0) under this model, so only the
      // [floor, ceil] constraint term contributes to the gradient.
      double gp = 0, gf = 0;
      const double inv_f2 = 1.0 / (freq * freq);
      for (size_t i = 0; i < n; ++i) {
        double dconstr_dt = 0; // d(constraints)/dt_i
        if (t[i] < in.floor[i])
          dconstr_dt = -inv_n2 * (in.floor[i] - t[i]);
        else if (t[i] > in.ceil[i])
          dconstr_dt = inv_n2 * (t[i] - in.ceil[i]);
        gp += dconstr_dt;                    // dt_i/dphase == 1
        gf += dconstr_dt * (-C[i] * inv_f2); // dt_i/dfreq == -C[i]/f^2
      }

      ++step;
      adam(gp, m_p, v_p, phase, lr);
      adam(gf, m_f, v_f, freq, lr);
      if (freq < 1e-6)
        freq = 1e-6;
    }
  }

  res.phase = phase;
  res.frequency = freq;
  res.timestamps = compute_t(phase, freq);
  res.loss = InterpolationLoss(in, res.timestamps);
  return res;
}

DiResult ComputeDi(const std::vector<GPSSample> &samples,
                   const std::vector<std::pair<double, double>> &spans,
                   const CoordinateSystem &cs) {
  DiResult out;
  const size_t n = samples.size();
  if (n == 0)
    return out;

  std::set<std::pair<double, double>> distinct(spans.begin(), spans.end());
  out.rough_frequency =
      distinct.empty()
          ? 1.0
          : static_cast<double>(n) / static_cast<double>(distinct.size());

  out.di.resize(n);
  out.di[0] = 1.0;
  for (size_t i = 1; i < n; ++i) {
    double avg_speed =
        0.5 * samples[i - 1].full_speed + 0.5 * samples[i].full_speed;
    double dist = cs.Distance(samples[i - 1], samples[i]);
    double d =
        (avg_speed > 1e-9) ? dist / avg_speed * out.rough_frequency : 1.0;
    out.di[i] = std::round(d);
    if (out.di[i] < 1.0)
      out.di[i] = 1.0;
  }
  return out;
}

InterpolationResult
InterpolateTimestamps(const std::vector<GPSSample> &samples,
                      const std::vector<std::pair<double, double>> &spans,
                      const CoordinateSystem &cs, const AdamOptions &opts) {
  InterpolationInput in;
  const size_t n = samples.size();
  in.floor.resize(n);
  in.ceil.resize(n);
  for (size_t i = 0; i < n; ++i) {
    in.floor[i] = spans[i].first;
    in.ceil[i] = spans[i].second;
  }
  DiResult di = ComputeDi(samples, spans, cs);
  in.di = std::move(di.di);
  return InterpolateTimestamps(in, di.rough_frequency, opts);
}

} // namespace pacer
