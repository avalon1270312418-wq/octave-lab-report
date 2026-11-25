function [x, k, half_width, trace] = NA1_alg1(f, a, b, tol, max_iter, fixed)
  if nargin < 6, fixed = false; end
  % Bisection of a sign-changing interval.
  fa = f(a);
  fb = f(b);
  if fa == 0 || fb == 0
    x = a;
    if fb == 0, x = b; end
    k = 0;
    half_width = 0;
    trace = [0, x, 0, a, b, 0];
    return;
  end
  if a >= b || sign(fa) == sign(fb)
    error('Provide an ordered interval with opposite endpoint signs.');
  end
  trace = zeros(max_iter, 6);
  for k = 1:max_iter
    half_width = (b - a)/2;
    x = a + half_width;
    fx = f(x);
    if fx == 0, half_width = 0; end
    trace(k, :) = [k, x, fx, a, b, half_width];
    if fx == 0 || (~fixed && half_width <= tol) || (fixed && k == max_iter)
      trace = trace(1:k, :);
      return;
    end
    if sign(fa) ~= sign(fx)
      b = x;
    else
      a = x;
      fa = fx;
    end
  end
  error('Bisection did not reach the tolerance.');
end
