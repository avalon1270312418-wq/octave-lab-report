function [x, k, trace] = NA1_alg6(f, g, x0, tol, max_iter, fixed)
  if nargin < 6, fixed = false; end
  % Steffensen acceleration of x = g(x).
  x = x0;
  trace = zeros(max_iter + 1, 3);
  for k = 0:max_iter
    fx = f(x);
    y = g(x);
    step = y - x;
    trace(k + 1, :) = [k, x, fx];
    if (fx == 0 && step == 0) || (~fixed && abs(fx) <= tol && abs(step) <= tol) || (fixed && k == max_iter)
      trace = trace(1:k+1, :);
      return;
    end
    if k == max_iter, break; end
    denom = (g(y) - y) - step;
    assert(denom ~= 0 && isfinite(denom), 'Steffensen denominator is zero or nonfinite.');
    x = x - (step/denom)*step;
  end
  error('Steffensen iteration did not reach the tolerance.');
end
