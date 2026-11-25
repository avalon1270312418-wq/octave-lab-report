function lab_execute(jobs, help_commands, capture_images, resume)
  if nargin < 4, resume = false; end
  more off;
  printf('GNU Octave %s\n', OCTAVE_VERSION);
  if ~isempty(help_commands)
    fid = fopen('octave_help.txt', 'wt');
    assert(fid >= 0, 'Could not open octave_help.txt');
    unwind_protect
      for k = 1:numel(help_commands)
        name = help_commands{k};
        fprintf(fid, '>> help %s\n%s\n', name, help(name));
        printf('Ran help %s (see octave_help.txt)\n', name);
      end
    unwind_protect_cleanup
      fclose(fid);
    end_unwind_protect
  end

  cases = cell(1, numel(jobs));
  for index = 1:numel(jobs)
    printf('LAB_TASK %d/%d\n', index, numel(jobs)); fflush(stdout);
    checkpoint = sprintf('task-%02d.json',index);
    capture_task = capture_images(min(index,numel(capture_images)));
    if resume && exist(checkpoint,'file')
      raw_checkpoint = strtrim(fileread(checkpoint));
      previous = jsondecode(raw_checkpoint);
      if ~isfield(previous,'error') && ~isfield(previous,'issue') && ~isfield(previous,'screenshot_error')
        printf('LAB_REUSED %d\n',index);
        printf('Task %d: recorded summary from the verified previous run\n',index);
        if iscell(previous.pages) && ~isempty(previous.pages) && iscell(previous.pages{1})
          printf('%s\n',previous.pages{1}{:});
        end
        if capture_task
          for page = 1:numel(previous.pages)
            if ~exist(sprintf('case-%02d-%02d.png',index,page),'file')
              try
                capture_page(previous.pages{page},index,page);
              catch err
                previous.screenshot_error = err.message;
              end
            end
          end
        end
        if isfield(previous,'screenshot_error')
          raw_checkpoint = regexprep(raw_checkpoint, '}\s*$', [',"screenshot_error":', jsonencode(previous.screenshot_error), '}']);
        end
        cases{index} = raw_checkpoint;
        printf('LAB_DONE %d\n',index); fflush(stdout);
        continue;
      end
    end
    try
      if strcmp(jobs{index}.kind, 'scalar')
        [item, lines] = solve_case(jobs{index}, index);
      else
        [item, lines] = lab_extra(jobs{index}, index);
      end
      pages = paginate(lines, index);
    catch err
      fprintf(2, 'Task %d (%s): %s\n', index, jobs{index}.label, err.message);
      item = struct('kind',jobs{index}.kind, 'error',err.message,'interval',[], 'automatic',false,'results',{{}});
      pages = {};
    end
    item.pages = pages;
    if capture_task
      try
        for page = 1:numel(pages)
          capture_page(pages{page}, index, page);
        end
      catch err
        warning('Figure capture unavailable: %s', err.message);
        item.screenshot_error = err.message;
      end
    end
    cases{index} = jsonencode(item);
    save_json(checkpoint, cases{index});
    printf('LAB_DONE %d\n',index); fflush(stdout);
  end
  save_json('results.json', ['{"version":',jsonencode(OCTAVE_VERSION),',"cases":[',strjoin(cases,','),']}']);
end

function save_json(name, payload)
  temp = [name, '.tmp'];
  fid = fopen(temp, 'wt');
  assert(fid >= 0, 'Could not open JSON checkpoint.');
  unwind_protect
    fprintf(fid, '%s\n', payload);
  unwind_protect_cleanup
    fclose(fid);
  end_unwind_protect
  assert(movefile(temp,name,'f'), 'Could not commit JSON checkpoint.');
end

function [item, lines] = solve_case(job, index)
  assert(isempty(job.domain_problem), '%s', job.domain_problem);
  raw = @(x) real_value(job.f(x));
  interval = job.interval;
  initial = job.initial;
  need_bracket = any(ismember(job.methods, {'bisection', 'false_position', 'fzero','brent'}));
  for method = job.methods
    chosen = method_start(job, method{1});
    need_bracket |= (any(ismember(method{1}, {'newton', 'fixed_point', 'steffensen'})) && isempty(chosen)) ...
      || (strcmp(method{1},'secant') && numel(chosen) < 2);
  end
  automatic = isempty(interval) && need_bracket;
  if automatic
    interval = lab_root_checks('interval', job);
  end
  if ~isempty(job.interval) && ~isempty(job.real_count) && need_bracket
    assert(job.real_count == 1, 'Multiple candidate roots detected or the chosen interval contains no matching real root.');
    if any(strcmp(job.target, {'positive','negative'}))
      [roots_in_scope, ~] = lab_root_checks('roots', job);
      if strcmp(job.target,'positive') && interval(1) <= 0
        interval(1) = roots_in_scope(1)/2;
      elseif strcmp(job.target,'negative') && interval(2) >= 0
        interval(2) = roots_in_scope(1)/2;
      end
    end
    if strcmp(job.target,'nonnegative'), interval(1) = max(0,interval(1)); end
    if strcmp(job.target,'nonpositive'), interval(2) = min(0,interval(2)); end
    for side = 1:2
      if job.open_interval(side) && interval(side) == job.interval(side) && raw(interval(side)) == 0
        interior = lab_root_checks('roots',job);
        interval(side) = interval(side) + (interior(1)-interval(side))/2;
      end
    end
  end
  if numel(job.methods) == 1 && strcmp(job.methods{1}, 'roots')
    scale = 1;
  else
    scale = lab_root_checks('scale', raw, interval, initial);
  end
  f = @(x) real_value(raw(x)/scale);
  fixed = job.fixed_steps > 0;
  limit = job.limit;
  if fixed, limit = job.fixed_steps; end
  if ~isempty(job.reference)
    lab_root_checks('validate', f, job.reference, max(job.tol,1e-12), 'Reference root', []);
  end

  item = struct('kind', 'scalar', 'interval', interval, 'automatic', automatic, 'results', {{}}, 'plot_file', '');
  if ~isempty(job.domain_warning), item = append_issue(item, job.domain_warning); end
  item.unique_interval = ~isempty(interval) && ~isempty(job.real_count) && job.real_count == 1;
  summary = {sprintf('GNU Octave %s   |   Equation %d', OCTAVE_VERSION, index), ...
     job.label, sprintf('tol = %.3g   max iterations = %d', job.tol, job.limit), ''};
  trace_lines = {};
  chart = {};
  found_single = [];
  failures = {};
  for k = 1:numel(job.methods)
    name = job.methods{k};
    initial = method_start(job, name);
    trace = [];
    steps = [];
    width = [];
    starts = [];
    multiplicity = [];
    try
      switch name
        case 'bisection'
          check_bracket(f, interval);
          [root, steps, width, trace] = NA1_alg1(f, interval(1), interval(2), job.tol, limit, fixed);
        case 'brent'
          check_bracket(f, interval);
          [root, steps, width, trace] = NA1_alg16(f, interval(1), interval(2), job.tol, limit);
        case 'newton'
          if isempty(initial)
            starts = interval(1) + (interval(2) - interval(1))/2;
          else
            starts = initial(1);
          end
          df = @(x) real_value(job.df(x)/scale);
          [root, steps, trace] = NA1_alg2(f, df, starts, job.tol, limit, fixed);
        case 'secant'
          starts = interval;
          if numel(initial) == 2, starts = initial; end
          assert(starts(1) ~= starts(2), 'Secant needs two distinct initial points.');
          [root, steps, trace] = NA1_alg3(f, starts(1), starts(2), job.tol, limit, fixed);
        case 'false_position'
          check_bracket(f, interval);
          [root, steps, trace] = NA1_alg4(f, interval(1), interval(2), job.tol, limit, fixed);
        case {'fixed_point', 'steffensen'}
          if isempty(initial)
            starts = interval(1) + (interval(2) - interval(1))/2;
          else
            starts = initial(1);
          end
          g = @(x) real_value(job.g(x));
          if strcmp(name, 'fixed_point')
            [root, steps, trace] = NA1_alg5(f, g, starts, job.tol, limit, fixed);
          else
            [root, steps, trace] = NA1_alg6(f, g, starts, job.tol, limit, fixed);
          end
        case 'fzero'
          check_bracket(f, interval);
          options = optimset('TolX', job.tol, 'MaxIter', job.limit, 'FunValCheck', 'on');
          [root, ~, status, info, trace] = lab_traced_solver(@fzero, f, interval, options);
          assert(status == 1, 'fzero did not converge.');
          steps = info.iterations;
        case 'roots'
          [root, multiplicity] = lab_root_checks('roots', job);
          residual = abs(polyval(job.coeffs, root));
          poly_scale = max(realmin, polyval(abs(job.coeffs), abs(root)));
          assert(all(isfinite(root)) && all(isfinite(residual)) && all(isfinite(poly_scale)) ...
            && all(residual ./ poly_scale <= max(8*job.tol,64*eps)), 'roots residual check failed.');
      end
      if ~strcmp(name, 'roots')
        residual = abs(raw(root));
        assert(all(abs(root-job.excluded_points) > max(job.tol,abs(root)*eps)), 'The approximation lies at a known point outside the domain.');
        if ~fixed
          lab_root_checks('validate', f, root, job.tol, name, width);
          found_single(end+1) = root;
        end
        assert(all(lab_root_checks('target', root, job.target)), 'The computed root does not match the requested root target. Choose a suitable interval or initial points.');
        if ~isempty(job.interval)
          assert(root >= interval(1) && root <= interval(2), 'Iteration converged outside the specified interval. Check the initial points.');
          assert(all(~job.open_interval | root ~= job.interval), 'The endpoint root is excluded by the open interval.');
        end
      end
      error_estimate = [];
      if ~isempty(job.error_tol)
        if ~isempty(job.reference)
          error_estimate = abs(root-job.reference);
          reference_size = abs(job.reference);
        else
          error_estimate = width;
          reference_size = abs(root)-width;
        end
        if strcmp(job.error_mode,'relative')
          if isempty(reference_size) || reference_size <= 0
            error_estimate = Inf;
          else
            error_estimate = error_estimate/reference_size;
          end
        end
        if isempty(error_estimate) || any(error_estimate > job.error_tol)
          item = append_issue(item, [name, ' 尚未达到指定的误差限。']);
        end
      end
      if ~isempty(job.residual_tol)
        assert(all(residual <= job.residual_tol), 'The requested absolute residual was not met.');
      end
      assert(all(isfinite(root)) && all(isfinite(residual)), 'Nonfinite result.');
      if ~isempty(trace), trace(:,3) *= scale; end
      result = struct('method', name, 're', real(root(:)).', 'im', imag(root(:)).', ...
        'residual', residual(:).', 'steps', steps, 'half_width', width, 'starts', starts, 'trace', trace, ...
        'multiplicity', multiplicity(:).', 'fixed', fixed, 'requested_steps', job.fixed_steps, ...
        'error_estimate', error_estimate);
      if fixed && steps ~= job.fixed_steps
        item = append_issue(item, sprintf('%s 提前停止。要求 %d 次，实际 %d 次。',name,job.fixed_steps,steps));
      end
      item.results{end+1} = result;
      if ~isempty(starts)
        printf('Equation %d %s initial: %s\n', index, name, strtrim(sprintf('%.10g ', starts)));
      end
      step_note = '';
      if ~isempty(steps), step_note = sprintf('   steps: %d', steps); end
      for j = 1:numel(root)
        value = lab_display(root(j),job.digits,job.decimals);
        summary{end+1} = sprintf('%-14s x = %-13s |f(x)| = %.3g%s', name, value, residual(j), step_note);
      end
      if ~isempty(width), summary{end+1} = sprintf('interval half-width = %.3g', width); end
      if ~isempty(trace)
        chart{end+1} = struct('name', name, 'data', trace);
        rows = arrayfun(@(r) sprintf('%3d   %+16.12g   %+.4g', trace(r, 1:3)), 1:size(trace, 1), 'UniformOutput', false);
        printf('\nEquation %d, %s iteration trace (k, x, f(x))\n', index, name);
        printf('%s\n', rows{:});
        if numel(rows) > 12
          rows = [rows(1:5), {'... complete trace in octave.log ...'}, rows(end-5:end)];
        end
        trace_lines = [trace_lines, {sprintf('%s trace:  k, x(k), f(x(k))', name)}, rows, {''}];
      end
    catch err
      failures{end+1} = [name, ': ', err.message];
      fprintf(2, 'Equation %d %s failed: %s\n', index, name, err.message);
    end
  end
  assert(~isempty(item.results), '%s', strjoin(failures,' | '));
  if ~isempty(failures)
    item = append_issue(item, ['部分方法未完成。', strjoin(failures,' | ')]);
  end
  if ~isempty(found_single) && max(found_single)-min(found_single) > max(32*job.tol, 128*eps(max(1,max(abs(found_single)))))
    item = append_issue(item,'各方法得到不同的根。请分别指定区间或初始值。');
  end
  if ~isempty(job.reference) && ~isempty(found_single) && any(abs(found_single-job.reference) > max(32*job.tol,128*eps(max(1,abs(job.reference)))))
    item = append_issue(item,'参考根与迭代收敛到的根不同。请确认参考根和初始条件。');
  end
  if ~isempty(interval)
    summary = [summary(1:3), {sprintf('interval = [%.12g, %.12g]', interval)}, summary(4:end)];
  end
  printf('\n%s\n', summary{:});
  lines = [summary, {''}, trace_lines];
  item.plot_files = {};
  for k = 1:numel(job.plots)
    try
      item.plot_files{k} = lab_plot(index, job.plots{k}, chart, job.reference, [], [], k);
    catch err
      item.plot_files{k} = '';
      item = append_issue(item,['无法生成所需图形。', err.message]);
    end
  end
end

function initial = method_start(job, name)
  initial = job.initial;
  if isfield(job.method_initials,name)
    initial = job.method_initials.(name);
  end
end

function item = append_issue(item, message)
  if isfield(item,'issue')
    item.issue = [item.issue, ' ', message];
  else
    item.issue = message;
  end
end

function pages = paginate(lines, index)
  while isempty(lines{end}), lines(end) = []; end
  per_page = 28;
  pages = cell(1, ceil(numel(lines)/per_page));
  for n = 1:numel(pages)
    pages{n} = lines(1+(n-1)*per_page:min(n*per_page,numel(lines)));
    if n > 1
      pages{n} = [{sprintf('GNU Octave %s   |   Task %d (%d/%d)', OCTAVE_VERSION, index, n, numel(pages))}, pages{n}];
    end
  end
end

function y = real_value(y)
  if ~isscalar(y) || ~isreal(y) || ~isfinite(y)
    error('A function or derivative evaluation left the finite real domain.');
  end
end

function check_bracket(f, interval)
  a = f(interval(1));
  b = f(interval(2));
  assert(~(a == 0 && b == 0), 'Both endpoints are roots. Specify an interval for one target root.');
  assert(a == 0 || b == 0 || sign(a) ~= sign(b), 'Interval endpoints must bracket a sign change.');
end

function capture_page(lines, index, page)
  toolkits = available_graphics_toolkits();
  assert(any(strcmp(toolkits, 'qt')), 'Octave was started without Qt graphics.');
  graphics_toolkit('qt');
  height = max(130, 40 + 20*numel(lines));
  columns = max(cellfun(@numel, lines));
  width = max(680, 32 + 8*columns);
  h = figure('visible', 'off', 'color', [.96 .97 .98], 'position', [10 10 width height], 'menubar', 'none', 'toolbar', 'none');
  unwind_protect
    ax = axes('parent', h, 'position', [0 0 1 1], 'visible', 'off');
    text(ax, 0.022, 1-14/height, lines, 'units', 'normalized', 'verticalalignment', 'top', ...
      'fontname', 'monospace', 'fontsize', 11, 'interpreter', 'none', 'color', [0.08 0.13 0.23]);
    drawnow();
    frame = getframe(h).cdata;
    [r, c] = find(all(frame < 220, 3));
    assert(~isempty(r), 'Octave figure contained no visible output.');
    margin = 20;
    rows = max(1, min(r)-margin):min(size(frame, 1), max(r)+margin);
    cols = max(1, min(c)-margin):min(size(frame, 2), max(c)+margin);
    imwrite(frame(rows, cols, :), sprintf('case-%02d-%02d.png', index, page));
  unwind_protect_cleanup
    close(h);
  end_unwind_protect
end
