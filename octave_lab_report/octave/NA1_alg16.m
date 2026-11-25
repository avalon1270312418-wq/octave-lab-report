function [root, steps, width, trace] = NA1_alg16(f, a, b, tol, limit)
  % Brent-Dekker iteration: try inverse quadratic or secant interpolation.
  fa = f(a); fb = f(b);
  if abs(fa) < abs(fb), [a,b,fa,fb] = deal(b,a,fb,fa); end
  trace = [0,b,fb,min(a,b),max(a,b),abs(b-a)];
  if fb == 0, [root,steps,width] = deal(b,0,0); return; end
  assert(sign(fa) ~= sign(fb), 'Brent requires opposite signs at the interval endpoints.');
  c = a; fc = fa; d = c; bisected = true;
  for steps = 1:limit
    if fa ~= fc && fb ~= fc
      s = a*fb*fc/((fa-fb)*(fa-fc)) + b*fa*fc/((fb-fa)*(fb-fc)) + c*fa*fb/((fc-fa)*(fc-fb));
    else
      s = b - fb*((b-a)/(fb-fa));
    end
    edge = (3*a+b)/4;
    movement = abs(b-c);
    if ~bisected, movement = abs(c-d); end
    bisected = ~isfinite(s) || s <= min(edge,b) || s >= max(edge,b) ...
      || abs(s-b) >= movement/2 || movement < tol;
    if bisected, s = a + (b-a)/2; end
    fs = f(s);
    d = c; c = b; fc = fb;
    if sign(fa) ~= sign(fs), b = s; fb = fs; else, a = s; fa = fs; end
    if abs(fa) < abs(fb), [a,b,fa,fb] = deal(b,a,fb,fa); end
    root = b;
    width = abs(b-a);
    if fb == 0, width = 0; end
    trace(end+1,:) = [steps,b,fb,min(a,b),max(a,b),width];
    if width <= tol, return; end
  end
  error('Brent did not reach the tolerance within the iteration limit.');
end
