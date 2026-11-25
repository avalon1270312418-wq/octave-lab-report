function out = lab_display(value, digits, decimals)
  if isempty(decimals)
    out = sprintf('%.*g',digits,real(value));
    if imag(value) ~= 0, out = [out,sprintf('%+.*gi',digits,imag(value))]; end
  else
    out = sprintf('%.*f',decimals,real(value));
    if imag(value) ~= 0, out = [out,sprintf('%+.*fi',decimals,imag(value))]; end
  end
end
