function integral = NA1_alg11(samples, h)
  % Composite Simpson rule with an even number of subintervals.
  assert(mod(numel(samples)-1, 2) == 0, 'Simpson rule requires an even number of subintervals.');
  integral = (h/3) * (samples(1) + 4*sum(samples(2:2:end-1)) + 2*sum(samples(3:2:end-1)) + samples(end));
end
