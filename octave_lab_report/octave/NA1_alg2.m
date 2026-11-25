function [x, k, trace] = NA1_alg2(f, df, x0, tol, max_iter, fixed)
  if nargin < 6, fixed = false; end
  % Newton iteration. Trace columns are [index, x, f(x)].
  x = x0;
  trace = zeros(max_iter + 1, 3);
  for k = 0:max_iter
    fx = f(x);
    if ~isreal(fx) || ~isfinite(fx)
      error('Newton iterate left the real domain.');
    end
    trace(k + 1, :) = [k, x, fx];
    if fx == 0 || (~fixed && abs(fx) <= tol) || (fixed && k == max_iter)
      trace = trace(1:k+1, :);
      return;
    end
    if k == max_iter, break; end
    slope = df(x);
    if slope == 0 || ~isfinite(slope)
      error('Newton derivative is zero or nonfinite.');
    end
    x = x - fx/slope;
  end
  error('Newton iteration did not reach the tolerance.');
end
