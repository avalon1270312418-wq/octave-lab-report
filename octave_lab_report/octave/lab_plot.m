function name = lab_plot(index, kind, series, reference, nodes, samples, plot_number)
  if nargin < 7, plot_number = 1; end
  toolkits = available_graphics_toolkits();
  if any(strcmp(toolkits, 'qt'))
    graphics_toolkit('qt');
  elseif any(strcmp(toolkits, 'gnuplot'))
    graphics_toolkit('gnuplot');
  else
    error('No Qt or gnuplot toolkit is available for the requested chart.');
  end
  name = sprintf('plot-%02d.png', index);
  if plot_number > 1, name = sprintf('plot-%02d-%02d.png', index, plot_number); end
  h = figure('visible', 'off', 'color', 'white', 'position', [30, 30, 940, 500], 'paperpositionmode', 'auto');
  unwind_protect
    ax = axes('parent', h, 'position', [.15 .17 .54 .68], 'fontsize', 10, 'linewidth', 1);
    hold(ax, 'on');
    handles = [];
    labels = {};
    dash = {'-', '--', '-.', ':'};
    all_y = [];
    curve_plot = any(strcmp(kind, {'interpolation','integral','ode','regression'}));
    if ~curve_plot
      for j = 1:numel(series)
        [~, y] = data_for_plot(series{j}.data, kind, reference);
        all_y = [all_y; y(:)];
      end
    end
    positive = all_y(all_y > 0);
    floor_value = eps;
    if ~isempty(positive), floor_value = max(realmin, min(positive)/10); end
    for k = 1:numel(series)
      item = series{k};
      line_style = dash{1+mod(k-1, numel(dash))};
      if curve_plot
        curve = plot(ax, item.data(:, 1), item.data(:, 2), line_style, 'linewidth', 1.8);
      else
        [x, y] = data_for_plot(item.data, kind, reference);
        if isempty(y), continue; end
        % Keep exact zeros visible just below the smallest positive value.
        y(y == 0) = floor_value;
        curve = semilogy(ax, x, y, [line_style, 'o'], 'linewidth', 1.4, 'markersize', 3);
      end
      handles(end+1) = curve;
      labels{end+1} = strrep(item.name, '_', ' ');
    end
    assert(~isempty(get(ax, 'children')), 'No iteration data is available for the requested chart.');
    if curve_plot
      if ~isempty(nodes)
        handles(end+1) = plot(ax, nodes, samples, 'ko', 'markersize', 6, 'markerfacecolor', 'white');
        labels{end+1} = 'Data points';
      end
      xlabel(ax, 'x');
      switch kind
        case 'ode', ylabel(ax, 'y(x)');
        case 'integral', ylabel(ax, 'f(x)');
        otherwise, ylabel(ax, 'P(x)');
      end
      title(ax, sprintf('Task %d - %s', index,kind));
    else
      switch kind
        case 'step', ylabel(ax, '|x(k) - x(k-1)|');
        case 'absolute', ylabel(ax, '|x(k) - x_{ref}|');
        case 'relative', ylabel(ax, '|x(k) - x_{ref}| / |x_{ref}|');
        case 'grid_error', ylabel(ax, 'Estimated absolute error');
        otherwise, ylabel(ax, 'Residual norm');
      end
      set(ax, 'yscale', 'log');
      bounds = log10(ylim(ax));
      span = ceil(bounds(2)) - floor(bounds(1));
      if span > 8
        stride = ceil(span / 8);
        low = ceil(bounds(1) / stride) * stride;
        high = floor(bounds(2) / stride) * stride;
        set(ax, 'ytick', 10 .^ (low:stride:high));
      end
      if strcmp(kind,'grid_error')
        xlabel(ax,'Subintervals');
        set(ax,'xscale','log');
      else
        xlabel(ax,'Iteration');
      end
      title(ax, sprintf('Task %d - convergence', index));
    end
    grid(ax, 'on');
    set(ax, 'xminorgrid', 'off', 'yminorgrid', 'off');
    key = axes('parent', h, 'position', [.72 .17 .25 .68], 'xlim', [0 1], 'ylim', [0 1], 'visible', 'off');
    for k = 1:numel(handles)
      y = 1 - .9*k/(numel(handles)+1);
      color = get(handles(k), 'color');
      line(key, [.02 .22], [y y], 'color', color, 'linestyle', get(handles(k), 'linestyle'), 'linewidth', 1.4);
      line(key, .12, y, 'color', color, 'marker', get(handles(k), 'marker'), 'markersize', 4);
      text(key, .3, y, labels{k}, 'fontsize', min(9,160/(numel(handles)+1)), 'interpreter', 'none');
    end
    if any(all_y == 0)
      text(key, .02, .05, sprintf('Exact zeros shown at %.2g',floor_value), 'fontsize', 8, 'interpreter', 'none');
    end
    print(h, name, '-dpng', '-r150');
  unwind_protect_cleanup
    close(h);
  end_unwind_protect
end

function [x,y] = data_for_plot(trace, kind, reference)
  x = trace(:,1);
  switch kind
    case 'step', x = x(2:end); y = abs(diff(trace(:,2)));
    case 'absolute', y = abs(trace(:,2)-reference);
    case 'relative', y = abs((trace(:,2)-reference)/reference);
    otherwise, y = abs(trace(:,3));
  end
end
