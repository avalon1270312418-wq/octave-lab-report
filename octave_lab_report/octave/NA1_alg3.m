function [x, k, trace] = NA1_alg3(f, a, b, tol, max_iter, fixed)
  if nargin < 6, fixed = false; end
  if fixed, tol = 0; end
  % Secant iteration. k counts updates after the two initial values.
  fa = f(a);
  fb = f(b);
  trace = zeros(max_iter + 2, 3);
  trace(1:2, :) = [0, a, fa; 1, b, fb];
  if abs(fa) <= tol || abs(fb) <= tol
    k = 0;
    x = b;
    trace = trace(1:2, :);
    if abs(fa) <= tol, x = a; trace = trace(1, :); end
    return;
  end
  for k = 1:max_iter
    denominator = fb - fa;
    if denominator == 0 || ~isfinite(denominator)
      error('Secant denominator is zero or nonfinite.');
    end
    x = b - fb*(b - a)/denominator;
    fx = f(x);
    if ~isreal(fx) || ~isfinite(fx)
      error('Secant iterate left the real domain.');
    end
    trace(k + 2, :) = [k + 1, x, fx];
    if abs(fx) <= tol || (fixed && k == max_iter)
      trace = trace(1:k+2, :);
      return;
    end
    a = b; fa = fb;
    b = x; fb = fx;
  end
  error('Secant iteration did not reach the tolerance.');
end
