function [values, coeffs, residual] = NA1_alg15(x, y, query, degree)
  % Fit in scaled coordinates by QR, retaining the scaling in the result.
  [polynomial, stats, scale] = polyfit(x, y, degree);
  assert(all(isfinite(polynomial)) && rcond(stats.R) > 10*eps, 'Least-squares design matrix is ill-conditioned.');
  coeffs = [scale, polynomial];
  values = polyval(polynomial, query, [], scale);
  residual = polyval(polynomial, x, [], scale) - y;
end
