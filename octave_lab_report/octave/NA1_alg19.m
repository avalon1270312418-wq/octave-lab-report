function [value, estimate] = NA1_alg19(f, interval, tolerance)
  % Adaptive Gauss-Kronrod quadrature with an absolute error estimate.
  [value, estimate] = quadgk(@(x) arrayfun(f,x), interval(1), interval(2), 'AbsTol', tolerance, 'RelTol', 0);
  assert(isreal(value) && isfinite(value) && isfinite(estimate), 'Adaptive integration left the real finite domain.');
end
