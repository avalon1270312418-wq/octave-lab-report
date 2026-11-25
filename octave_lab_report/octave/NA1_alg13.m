function y = NA1_alg13(f, grid, y0)
  % Classical fourth-order Runge-Kutta method.
  y = zeros(numel(y0),numel(grid));
  y(:,1) = y0(:);
  for k = 1:numel(grid)-1
    x = grid(k);
    h = grid(k+1)-x;
    current = y(:,k);
    s1 = f(x, current);
    s2 = f(x+h/2, current+h*s1/2);
    s3 = f(x+h/2, current+h*s2/2);
    s4 = f(grid(k+1), current+h*s3);
    stages = [s1, s2, s3, s4];
    assert(isreal(stages) && all(isfinite(stages(:))), 'Runge-Kutta stages left the real finite domain.');
    y(:,k+1) = current + h*(s1+2*s2+2*s3+s4)/6;
    assert(all(isfinite(y(:,k+1))), 'Runge-Kutta solution is not finite.');
  end
end
