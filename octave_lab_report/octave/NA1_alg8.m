function [values, coeffs] = NA1_alg8(nodes, samples, query)
  % Lagrange polynomial in descending powers, with barycentric evaluation.
  nodes = nodes(:).';
  samples = samples(:).';
  n = numel(nodes);
  assert(n == numel(samples) && n == numel(unique(nodes)), 'Nodes must be distinct.');
  coeffs = zeros(1, n);
  weights = zeros(1, n);
  for k = 1:n
    other = nodes([1:k-1, k+1:n]);
    weights(k) = 1 / prod(nodes(k) - other);
    coeffs += samples(k)*weights(k)*poly(other);
  end
  values = zeros(size(query));
  for k = 1:numel(query)
    distance = query(k) - nodes;
    hit = find(distance == 0, 1);
    if isempty(hit)
      terms = weights ./ distance;
      values(k) = sum(terms .* samples) / sum(terms);
    else
      values(k) = samples(hit);
    end
  end
end
