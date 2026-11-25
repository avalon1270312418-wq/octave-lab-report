function x = NA1_alg7(A, b)
  % Gaussian elimination with partial pivoting.
  n = rows(A);
  assert(n == columns(A) && numel(b) == n, 'A must be square and compatible with b.');
  augmented = [A, b(:)];
  cutoff = n * eps(norm(A, inf));
  for k = 1:n
    [pivot, offset] = max(abs(augmented(k:n, k)));
    assert(pivot > cutoff, 'A is singular or numerically rank deficient.');
    pivot_row = k + offset - 1;
    augmented([k, pivot_row], :) = augmented([pivot_row, k], :);
    for row = k+1:n
      factor = augmented(row, k) / augmented(k, k);
      augmented(row, k:end) -= factor * augmented(k, k:end);
    end
  end
  x = zeros(n, 1);
  for k = n:-1:1
    x(k) = (augmented(k, end) - augmented(k, k+1:n)*x(k+1:n)) / augmented(k, k);
  end
end
