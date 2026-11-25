function [x, k, trace] = NA1_alg5(f, g, x0, tol, max_iter, fixed)
  if nargin < 6, fixed = false; end
  % Fixed-point iteration x(k+1) = g(x(k)).
  x = x0;
  trace = zeros(max_iter + 1, 3);
  for k = 0:max_iter
    fx = f(x);
    y = g(x);
    trace(k + 1, :) = [k, x, fx];
    if (fx == 0 && y == x) || (~fixed && abs(fx) <= tol && abs(y-x) <= tol) || (fixed && k == max_iter)
      trace = trace(1:k+1, :);
      return;
    end
    x = y;
  end
  error('Fixed-point iteration did not reach the tolerance.');
end
