function [x, steps, trace] = NA1_alg17(A, b, initial, tol, limit)
  % Jacobi uses only values from the previous iteration.
  [x, steps, trace] = lab_stationary(A,b,initial,tol,limit,diag(diag(A)));
end
