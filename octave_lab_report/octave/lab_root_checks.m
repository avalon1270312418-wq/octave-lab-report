function varargout = lab_root_checks(operation, varargin)
  switch operation
    case 'scale', varargout{1} = sampling_scale(varargin{:});
    case 'interval', varargout{1} = choose_interval(varargin{:});
    case 'roots', [varargout{1:nargout}] = polynomial_roots(varargin{:});
    case 'validate', certify(varargin{:});
    case 'target', varargout{1} = target_mask(varargin{:});
  end
end

function scale = sampling_scale(f, interval, starts)
  if ~isempty(interval)
    t = [.10 .25 .38 .5 .62 .75 .90];
    points = (1-t)*interval(1) + t*interval(2);
  else
    center = 0;
    if ~isempty(starts), center = starts(1); end
    points = center + max(1,abs(center))*[-.5,-.25,-.1,.1,.25,.5];
  end
  values = [];
  for x = points
    try
      y = abs(f(x));
      if isfinite(y) && y > 0, values(end+1) = y; end
    catch
      % Points outside the real domain do not define a scale.
    end
  end
  assert(~isempty(values), 'Unable to establish a nonzero function scale from real samples.');
  scale = median(values);
end

function interval = choose_interval(job)
  assert(~isempty(job.real_count), 'Automatic root isolation requires a rational polynomial. Specify an interval for this function.');
  assert(job.real_count > 0, 'There is no real root matching the requested target.');
  assert(job.real_count == 1, 'Multiple candidate roots detected. Specify a narrower interval or a signed root target.');
  radius = 1;
  for k = 1:numel(job.factors)
    c = job.factors{k}{1};
    radius = max(radius, 1 + max(abs(c(2:end)/c(1))));
  end
  assert(isfinite(radius) && radius < 1e100, 'Polynomial bound is too large. Specify a smaller interval.');
  interval = [-radius, radius];
  if any(strcmp(job.target, {'positive','negative','nonnegative','nonpositive'}))
    value = polynomial_roots(job);
    assert(numel(value) == 1, 'Could not resolve the signed polynomial root.');
    boundary = value/2;
    if strcmp(job.target, 'positive'), interval(1) = boundary; end
    if strcmp(job.target, 'negative'), interval(2) = boundary; end
    if strcmp(job.target, 'nonnegative'), interval(1) = 0; end
    if strcmp(job.target, 'nonpositive'), interval(2) = 0; end
  end
end

function mask = target_mask(values, target)
  switch target
    case 'positive', mask = imag(values) == 0 & real(values) > 0;
    case 'negative', mask = imag(values) == 0 & real(values) < 0;
    case 'nonnegative', mask = imag(values) == 0 & real(values) >= 0;
    case 'nonpositive', mask = imag(values) == 0 & real(values) <= 0;
    case 'all_real', mask = imag(values) == 0;
    otherwise, mask = true(size(values));
  end
end

function [value, multiplicity] = polynomial_roots(job)
  value = [];
  multiplicity = [];
  factors = job.factors;
  if isempty(factors), factors = {{job.coeffs, 1}}; end
  for k = 1:numel(factors)
    candidates = roots(factors{k}{1});
    value = [value; candidates];
    multiplicity = [multiplicity; factors{k}{2}*ones(numel(candidates),1)];
  end
  mask = target_mask(value, job.target);
  if ~isempty(job.interval)
    guard = 64*eps(max(abs(job.interval)));
    mask = mask & imag(value) == 0 & real(value) >= job.interval(1)-guard & real(value) <= job.interval(2)+guard;
    if job.open_interval(1), mask &= real(value) > job.interval(1); end
    if job.open_interval(2), mask &= real(value) < job.interval(2); end
  end
  value = value(mask);
  multiplicity = multiplicity(mask);
  assert(numel(unique(value)) == numel(value), 'Distinct roots were rounded to the same numerical value. Use the original factored form or higher precision.');
  if ~isempty(job.real_count)
    assert(nnz(imag(value)==0) == job.real_count, 'Computed real roots disagree with the exact root count. Refine the interval or improve conditioning.');
  end
  assert(~isempty(value), 'No computed root matches the requested root target.');
end

function certify(f, x, tol, name, width)
  error_limit = max(8*tol, 64*eps);
  residual = abs(f(x));
  assert(residual <= error_limit, '%s failed normalized residual verification. Check for a discontinuity or refine the interval.', name);
  if ~isempty(width) && residual ~= 0, return; end
  h = eps^(1/3)*max(1,abs(x));
  slopes = [];
  for direction = [-1,1]
    try
      slopes(end+1) = abs((f(x+direction*h)-f(x))/h);
    catch
      % A one-sided difference is usable at a domain boundary.
    end
  end
  assert(~isempty(slopes) && max(slopes) > 0 && residual/max(slopes) <= error_limit, ...
    '%s has a small residual but inadequate root accuracy. Use a narrower bracket or different initial points.', name);
end
