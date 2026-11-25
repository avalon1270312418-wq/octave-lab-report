function [x, k, trace] = NA1_alg4(f, a, b, tol, max_iter, fixed)
  if nargin < 6, fixed = false; end
  if fixed, tol = 0; end
  % Regula falsi. Trace columns are [update number, x, f(x)].
  fa = f(a);
  fb = f(b);
  trace = zeros(max_iter, 6);
  if abs(fa) <= tol || abs(fb) <= tol
    x = a;
    if abs(fa) > tol, x = b; end
    k = 0;
    trace = [0, x, f(x), a, b, NaN];
    return;
  end
  assert(a < b && sign(fa) ~= sign(fb), 'Regula falsi needs a sign-changing interval.');
  for k = 1:max_iter
    scale = max(abs([fa, fb]));
    left = abs(fa)/scale;
    right = abs(fb)/scale;
    x = a + (b-a)*(left/(left+right));
    fx = f(x);
    trace(k, :) = [k, x, fx, a, b, NaN];
    if abs(fx) <= tol || (fixed && k == max_iter)
      trace = trace(1:k, :);
      return;
    end
    assert(x > a && x < b, 'Regula falsi stagnated.');
    if sign(fx) == sign(fa)
      a = x;
      fa = fx;
    else
      b = x;
      fb = fx;
    end
  end
  error('Regula falsi did not reach the tolerance.');
end
