function [values, knots, coefficients, residual] = NA1_alg14(x, y, query)
  % Octave's not-a-knot cubic spline. Each row uses powers of x-knots(i).
  [x, order] = sort(x);
  y = y(order);
  pp = spline(x, y);
  [knots, coefficients] = unmkpp(pp);
  values = ppval(pp, query);
  residual = max(abs(ppval(pp, x)-y));
end
