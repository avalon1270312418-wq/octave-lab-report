function [item, summary] = lab_extra(job, index)
  item = struct('kind', job.kind, 'interval', [], 'automatic', false, 'results', {{}}, 'plot_file', '');
  summary = {sprintf('GNU Octave %s   |   Task %d', OCTAVE_VERSION, index), job.label, ''};
  if any(strcmp(job.kind, {'integral','ode'}))
    assert(isempty(job.domain_problem), '%s', job.domain_problem);
    if ~isempty(job.domain_warning), item = extra_issue(item, job.domain_warning); end
    item.interval = job.interval;
    count = job.segments;
    if count == 0, count = 16; end
    grid = linspace(job.interval(1),job.interval(2),count+1);
    assert(all(diff(grid) ~= 0), 'Grid spacing is smaller than floating-point resolution.');
    chart = {};
    for k = 1:numel(job.methods)
      name = job.methods{k};
      try
        if strcmp(name,'adaptive_integral')
          target = job.accuracy_tol;
          if isempty(target), target = job.tol; end
          [value, estimate] = NA1_alg19(job.f,job.interval,target);
          item.results{end+1} = struct('method',name,'steps',[],'integral',value,'error_estimate',estimate);
          summary{end+1} = sprintf('%s   integral = %s; estimated error %.3e',name,lab_display(value,job.digits,job.decimals),estimate);
          if estimate > target
            item = extra_issue(item,'quadgk 的误差估计超过给定的绝对误差限。');
          end
          if ~isempty(job.plot)
            values = arrayfun(job.f,grid);
            assert(isreal(values) && all(isfinite(values)), 'An integrand plot sample is outside the finite real domain.');
            chart{end+1} = struct('name',name,'data',[grid(:),values(:)]);
          end
          continue;
        end
        if strcmp(job.kind,'ode')
          if strcmp(name,'euler'), algorithm = @NA1_alg12; order = 1; else, algorithm = @NA1_alg13; order = 4; end
          values = algorithm(job.f, grid, job.initial);
          terminal_values = arrayfun(@(v) lab_display(v,job.digits,job.decimals),values(:,end),'UniformOutput',false);
          summary{end+1} = sprintf('%s   terminal y: %s', name, strjoin(terminal_values,', '));
          approximate = @(n) ode_terminal(algorithm, job, n);
          coarse = values(:,end);
        else
          values = arrayfun(job.f, grid);
          assert(isreal(values) && all(isfinite(values)), 'Nonfinite or nonreal integrand at a grid point.');
          if strcmp(name,'trapezoid'), algorithm = @NA1_alg10; order = 2; else, algorithm = @NA1_alg11; order = 4; end
          integral = algorithm(values, (grid(end)-grid(1))/job.segments);
          assert(isfinite(integral), 'Quadrature result is not finite.');
          summary{end+1} = sprintf('%s   integral = %s', name, lab_display(integral,job.digits,job.decimals));
          approximate = @(n) quadrature(algorithm, job, n);
          coarse = integral;
        end
        result = struct('method', name, 'values', values, 'grid', grid, 'steps', job.segments);
        if strcmp(job.kind,'integral'), result.integral = integral; end
        if strcmp(job.kind,'ode'), result.terminal = values(:,end).'; end
        result.error_estimate = [];
        try
          fine = approximate(2*job.segments);
          finer = approximate(4*job.segments);
          first_change = norm(fine-coarse,inf);
          second_change = norm(finer-fine,inf);
          assert(isfinite(first_change+second_change), 'Nonfinite refinement difference.');
          if second_change > max(.8*first_change,64*eps(max(abs([coarse(:);fine(:);finer(:)]))))
            item = extra_issue(item, [name, ' 网格加密未显示稳定收敛，误差待核对。']);
          else
            result.error_estimate = max(first_change, second_change*2^order)*2^order/(2^order-1);
            if first_change > 0 && second_change > 0
              result.order_estimate = log2(first_change/second_change);
            end
            result.refinement = [job.segments, result.error_estimate; ...
                                 2*job.segments, second_change*2^order/(2^order-1)];
            if strcmp(job.plot,'grid_error')
              chart{end+1} = struct('name',name,'data',[result.refinement, result.refinement(:,2)]);
            end
            summary{end+1} = sprintf('  estimated absolute error = %.3e', result.error_estimate);
            if ~isempty(job.accuracy_tol) && result.error_estimate > job.accuracy_tol
              item = extra_issue(item, [name, ' 网格加密估计超过指定绝对误差限。']);
            end
          end
        catch err
          item = extra_issue(item, [name, ' 无法验证网格误差。', err.message]);
        end
        item.results{end+1} = result;
        if ~isempty(job.plot) && ~strcmp(job.plot,'grid_error')
          for variable = 1:rows(values)
            series_name = name;
            if rows(values)>1, series_name = [name, ' ',job.variables{variable}]; end
            chart{end+1} = struct('name', series_name, 'data', [grid(:), values(variable,:).']);
          end
        end
      catch err
        item = extra_issue(item, [name, ': ', err.message]);
      end
    end
    if isempty(item.results), error('%s',item.issue); end
    if strcmp(job.plot,'integral') && ~isempty(chart), chart = chart(1); chart{1}.name = 'integrand'; end
    item = try_chart(item, job, index, chart, [], []);
  elseif any(strcmp(job.kind, {'interpolation','regression'}))
    chart = {};
    for k = 1:numel(job.methods)
      name = job.methods{k};
      try
        grid_x = [];
        if ~isempty(job.plot)
          plot_range = [job.nodes, job.query];
          grid_x = unique([linspace(min(plot_range), max(plot_range), 301), job.query]);
        end
        query = [job.query, grid_x];
        knots = [];
        if strcmp(name,'least_squares')
          [values, coeffs, residuals] = NA1_alg15(job.nodes, job.samples, query, job.degree);
          node_residual = max(abs(residuals));
        elseif strcmp(name,'spline')
          [values, knots, coeffs, node_residual] = NA1_alg14(job.nodes, job.samples, query);
        else
          if strcmp(name, 'lagrange'), algorithm = @NA1_alg8; else, algorithm = @NA1_alg9; end
          [values, coeffs] = algorithm(job.nodes, job.samples, query);
          node_residual = max(abs(polyval(coeffs, job.nodes) - job.samples));
        end
        assert(all(isfinite([values(:); coeffs(:); node_residual])), 'The fitted or interpolated values are not finite.');
        if strcmp(job.kind,'interpolation')
          assert(node_residual <= job.tol, 'Interpolation failed the node residual check.');
        end
        item.results{end+1} = struct('method', name, 'query', job.query, 'values', values(1:numel(job.query)), ...
          'coefficients', coeffs, 'knots', knots, 'residual', node_residual);
        if strcmp(name,'least_squares')
          item.results{end}.rmse = norm(residuals/sqrt(numel(residuals)));
        end
        summary{end+1} = sprintf('%s   max node residual = %.3e', name, node_residual);
        if isvector(coeffs) && isempty(knots)
          summary{end+1} = ['  coefficients: ', strtrim(sprintf('%.*g ', [job.digits*ones(size(coeffs)); coeffs]))];
        end
        for j = 1:numel(job.query)
          summary{end+1} = sprintf('  P(%.*g) = %s', job.digits, job.query(j), lab_display(values(j),job.digits,job.decimals));
        end
        if ~isempty(grid_x)
          chart{end+1} = struct('name', name, 'data', [grid_x(:), values(numel(job.query)+1:end).']);
        end
      catch err
        item = extra_issue(item, [name, ': ', err.message]);
      end
    end
    if isempty(item.results), error('%s',item.issue); end
    item = try_chart(item, job, index, chart, job.nodes, job.samples);
  else
    chart = {};
    for name_cell = job.methods
      name = name_cell{1};
      try
        steps = [];
        trace = [];
        if strcmp(job.kind, 'linear')
          matrix_norm = norm(job.A,inf);
          assert(isfinite(matrix_norm), 'The matrix norm exceeds floating-point range.');
          switch name
            case 'gaussian'
              value = NA1_alg7(job.A, job.b);
            case 'jacobi'
              [value,steps,trace] = NA1_alg17(job.A,job.b,job.initial,job.tol,job.limit);
            case 'gauss_seidel'
              [value,steps,trace] = NA1_alg18(job.A,job.b,job.initial,job.tol,job.limit);
          end
          residual = abs(job.A*value - job.b(:));
          scaled = max(residual)/max(realmin,matrix_norm*norm(value,inf)+norm(job.b,inf));
        else
          options = optimset('TolFun', job.tol, 'TolX', job.tol, 'MaxIter', job.limit, 'FunValCheck', 'on');
          [value, residual, status, stats, trace] = lab_traced_solver(@fsolve, job.f, job.initial(:), options);
          assert(status > 0, 'fsolve did not converge. Specify a different initial vector.');
          residual = abs(residual);
          steps = stats.iterations;
          scaled = max(residual);
        end
        assert(isreal(value) && all(isfinite([value(:);residual(:)])) && scaled <= job.tol, 'System solution failed the residual check.');
        item.results{end+1} = struct('method', name, 'values', value(:).', 'residual', residual(:).', 'steps', steps, 'trace', trace);
        if ~isempty(trace)
          printf('\nTask %d: iteration, iterate infinity norm, residual infinity norm, iterate, residual vector\n',index);
          for j = 1:rows(trace)
            printf('%d%s\n', trace(j,1), sprintf(' %.15g',trace(j,2:end)));
          end
        end
        summary{end+1} = sprintf('%s   maximum residual = %.3e', name, max(residual));
        if ~isempty(steps), summary{end+1} = sprintf('Iterations: %d', steps); end
        for k = 1:numel(value)
          summary{end+1} = sprintf('  %s = %s   |F(%d)| = %.3e', job.variables{k}, lab_display(value(k),job.digits,job.decimals), k, residual(k));
        end
        if ~isempty(trace), chart{end+1} = struct('name',name,'data',trace); end
      catch err
        item = extra_issue(item,[name, ': ',err.message]);
      end
    end
    if isempty(item.results), error('%s',item.issue); end
    item = try_chart(item,job,index,chart,[],[]);
  end
  printf('\n%s\n', summary{:});
end

function terminal = ode_terminal(algorithm, job, n)
  points = linspace(job.interval(1), job.interval(2), n+1);
  assert(all(diff(points) ~= 0), 'Refinement grid exceeds floating-point resolution.');
  values = algorithm(job.f, points, job.initial);
  terminal = values(:,end);
end

function value = quadrature(algorithm, job, n)
  points = linspace(job.interval(1), job.interval(2), n+1);
  samples = arrayfun(job.f, points);
  assert(isreal(samples) && all(isfinite(samples)), 'An integrand evaluation failed on the refinement grid.');
  value = algorithm(samples, (points(end)-points(1))/n);
end

function item = extra_issue(item, message)
  if isfield(item,'issue'), message = [item.issue, ' ', message]; end
  item.issue = message;
end

function item = try_chart(item, job, index, series, nodes, samples)
  if ~isempty(job.plot)
    try
      item.plot_file = lab_plot(index, job.plot, series, [], nodes, samples);
    catch err
      item = extra_issue(item,['无法生成所需图形。', err.message]);
    end
  end
end
