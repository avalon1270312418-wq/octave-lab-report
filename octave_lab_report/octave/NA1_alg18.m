function [x, steps, trace] = NA1_alg18(A, b, initial, tol, limit)
  % Gauss-Seidel uses the newest available lower-triangular components.
  [x, steps, trace] = lab_stationary(A,b,initial,tol,limit,tril(A));
end
