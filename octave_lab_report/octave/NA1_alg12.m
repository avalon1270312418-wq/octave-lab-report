function y = NA1_alg12(f, grid, y0)
  % Explicit Euler method for scalar or vector initial values.
  y = zeros(numel(y0),numel(grid));
  y(:,1) = y0(:);
  for k = 1:numel(grid)-1
    h = grid(k+1)-grid(k);
    y(:,k+1) = y(:,k) + h*f(grid(k), y(:,k));
    assert(isreal(y(:,k+1)) && all(isfinite(y(:,k+1))), 'Euler method left the real finite domain.');
  end
end
