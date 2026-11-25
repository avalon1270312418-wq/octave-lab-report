function [x, steps, trace] = lab_stationary(A, b, initial, tol, limit, split)
  x = initial(:); b = b(:);
  scale_A = norm(A,inf); scale_b = norm(b,inf);
  assert(all(abs(diag(split)) > eps(scale_A)), 'A row has a zero or too small diagonal element.');
  trace = [];
  for steps = 0:limit
    residual = A*x-b;
    assert(isreal(x) && all(isfinite([x;residual])), 'Linear iteration diverged.');
    backward = norm(residual,inf)/max(realmin,scale_A*norm(x,inf)+scale_b);
    trace(end+1,:) = [steps,norm(x,inf),norm(residual,inf),x.',residual.'];
    if backward <= tol, return; end
    x = x - split\residual;
  end
  error('Linear iteration did not meet the backward-error tolerance.');
end
