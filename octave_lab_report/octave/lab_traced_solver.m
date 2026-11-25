function [root, residual, status, info, trace] = lab_traced_solver(solver, f, initial, options)
  scalar_solver = strcmp(func2str(solver), 'fzero');
  width = 3;
  if ~scalar_solver, width += 2*numel(initial); end
  trace = zeros(0, width);
  last = [];
  [root, residual, status, info] = solver(f, initial, optimset(options, 'OutputFcn', @record));
  record(root, struct(), 'done');

  function stop = record(point, optimvalues, state)
    stop = false;
    if scalar_solver && ~isscalar(point)
      return;
    end
    if strcmp(state, 'done') && isequal(point(:), last)
      return;
    end
    value = f(point);
    if scalar_solver
      trace(end+1, :) = [rows(trace), point, value];
    else
      trace(end+1, :) = [rows(trace), norm(point, inf), norm(value, inf), point(:).', value(:).'];
    end
    last = point(:);
  end
end
