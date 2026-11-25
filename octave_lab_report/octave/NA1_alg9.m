function [values, coeffs] = NA1_alg9(nodes, samples, query)
  % Newton divided differences, evaluated in nested form.
  nodes = nodes(:).';
  terms = samples(:).';
  n = numel(nodes);
  assert(n == numel(terms) && n == numel(unique(nodes)), 'Nodes must be distinct.');
  for level = 1:n-1
    for k = n:-1:level+1
      terms(k) = (terms(k) - terms(k-1)) / (nodes(k) - nodes(k-level));
    end
  end
  values = terms(n)*ones(size(query));
  coeffs = terms(n);
  for k = n-1:-1:1
    values = terms(k) + (query - nodes(k)) .* values;
    coeffs = conv(coeffs, [1, -nodes(k)]);
    coeffs(end) += terms(k);
  end
end
