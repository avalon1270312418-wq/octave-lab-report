function integral = NA1_alg10(samples, h)
  % Composite trapezoidal rule on a uniformly spaced grid.
  integral = h * ((samples(1) + samples(end))/2 + sum(samples(2:end-1)));
end
