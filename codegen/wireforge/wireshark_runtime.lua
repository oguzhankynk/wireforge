-- Interpreter of MODEL: registers one protocol per interface and dissects its frames the way the
-- WireForge reference codec decodes them. Problems are reported as expert info whose text starts
-- with the runtime status name, e.g. "[ChecksumMismatch] ...".

local bit_and, bit_or, bit_xor
if bit32 then
  bit_and, bit_or, bit_xor = bit32.band, bit32.bor, bit32.bxor
else
  bit_and, bit_or, bit_xor = load([[
    return function(a, b) return a & b end,
      function(a, b) return a | b end,
      function(a, b) return a ~ b end]])()
end

local STATUSES = {
  "Truncated", "OutOfRange", "ConstMismatch", "CountExceedsMax", "ChecksumMismatch",
  "InvalidLength", "TrailingBytes", "UnknownMessageId", "InvalidState",
}
local GROUPS = {
  Truncated = expert.group.MALFORMED, InvalidLength = expert.group.MALFORMED,
  TrailingBytes = expert.group.MALFORMED, ChecksumMismatch = expert.group.CHECKSUM,
}

-- Values -----------------------------------------------------------------------------------------

local function big(value)
  if value.signed then return Int64.new(value.low, value.high) end
  return UInt64.new(value.low, value.high)
end

local function number_of(value)
  if type(value) == "userdata" then return value:tonumber() end
  return value
end

-- Wire text is Latin-1; Wireshark shows UTF-8. NUL bytes are shown as \0.
local function latin1_text(data)
  return (data:gsub("[\0\128-\255]", function(char)
    local byte = char:byte()
    if byte == 0 then return "\\0" end
    return string.char(0xC0 + math.floor(byte / 64), 0x80 + byte % 64)
  end))
end

local function hex(value)
  if type(value) == "userdata" then return "0x" .. value:tohex() end
  return string.format("0x%X", value)
end

local function to_text(value)
  if type(value) == "number" and value == math.floor(value) and math.abs(value) < 2 ^ 53 then
    return string.format("%d", value)
  end
  return tostring(value)
end

-- Count expressions: integer arithmetic with C division ----------------------------------------

local function c_div(a, b)
  if b == 0 then error("division by zero") end
  local quotient = math.floor(math.abs(a) / math.abs(b))
  if (a < 0) ~= (b < 0) then quotient = -quotient end
  return quotient
end

local function checked_shift(a, b)
  if a < 0 or b < 0 or b > 63 then error("invalid shift " .. a .. ", " .. b) end
  return 2 ^ b
end

local BINARY = {
  ["+"] = function(a, b) return a + b end,
  ["-"] = function(a, b) return a - b end,
  ["*"] = function(a, b) return a * b end,
  ["/"] = c_div,
  ["%"] = function(a, b) return a - b * c_div(a, b) end,
  ["<<"] = function(a, b) return a * checked_shift(a, b) end,
  [">>"] = function(a, b) return math.floor(a / checked_shift(a, b)) end,
  ["&"] = function(a, b) return bit_and(a, b) end,
  ["|"] = function(a, b) return bit_or(a, b) end,
  ["^"] = function(a, b) return bit_xor(a, b) end,
  ["=="] = function(a, b) return a == b and 1 or 0 end,
  ["!="] = function(a, b) return a ~= b and 1 or 0 end,
  ["<"] = function(a, b) return a < b and 1 or 0 end,
  ["<="] = function(a, b) return a <= b and 1 or 0 end,
  [">"] = function(a, b) return a > b and 1 or 0 end,
  [">="] = function(a, b) return a >= b and 1 or 0 end,
}

local function evaluate(node, values)
  if type(node) == "number" then return node end
  local kind = node[1]
  if kind == "field" then
    local value = values[node[2]]
    if value == nil then error("no value for " .. node[2]) end
    return value
  elseif kind == "unary" then
    local operand = evaluate(node[3], values)
    if node[2] == "-" then return -operand end
    if node[2] == "~" then return -operand - 1 end
    return operand == 0 and 1 or 0
  elseif kind == "binary" then
    local op, left = node[2], evaluate(node[3], values)
    if op == "&&" then
      return (left ~= 0 and evaluate(node[4], values) ~= 0) and 1 or 0
    elseif op == "||" then
      return (left ~= 0 or evaluate(node[4], values) ~= 0) and 1 or 0
    end
    return BINARY[op](left, evaluate(node[4], values))
  elseif kind == "if" then
    if evaluate(node[2], values) ~= 0 then return evaluate(node[3], values) end
    return evaluate(node[4], values)
  end
  local a, b = evaluate(node[3], values), evaluate(node[4], values)
  if node[2] == "min" then return math.min(a, b) end
  if node[2] == "max" then return math.max(a, b) end
  local quotient = c_div(a, b)
  if quotient * b ~= a and (a < 0) == (b < 0) then quotient = quotient + 1 end
  return quotient
end

-- Checksums --------------------------------------------------------------------------------------

local function reflect(value, width)
  local result = 0
  for _ = 1, width do
    result = result * 2 + value % 2
    value = math.floor(value / 2)
  end
  return result
end

-- Rocksoft-model CRC of a byte string, for widths up to 32 bits.
local function crc(algo, data)
  local top = 2 ^ (algo.width - 1)
  local register = algo.init
  for index = 1, #data do
    local byte = data:byte(index)
    if algo.refin then byte = reflect(byte, 8) end
    for bit = 7, 0, -1 do
      local feedback = (register >= top) ~= (math.floor(byte / 2 ^ bit) % 2 == 1)
      register = (register % top) * 2
      if feedback then register = bit_xor(register, algo.poly) end
    end
  end
  if algo.refout then register = reflect(register, algo.width) end
  return bit_xor(register, algo.xorout)
end

-- The same CRC for widths of 33 to 64 bits, on UInt64 values.
local function crc64(algo, data)
  local width = algo.width
  local mask = UInt64.max():rshift(64 - width)
  local top = UInt64.new(1, 0):lshift(width - 1)
  local zero = UInt64.new(0, 0)
  local poly, register = big(algo.poly), big(algo.init)
  for index = 1, #data do
    local byte = data:byte(index)
    if algo.refin then byte = reflect(byte, 8) end
    for bit = 7, 0, -1 do
      local feedback = (register:band(top) ~= zero) ~= (math.floor(byte / 2 ^ bit) % 2 == 1)
      register = register:lshift(1):band(mask)
      if feedback then register = register:bxor(poly) end
    end
  end
  if algo.refout then
    local reflected = zero
    for _ = 1, width do
      reflected = reflected:lshift(1):bor(register:band(1))
      register = register:rshift(1)
    end
    register = reflected
  end
  return register:bxor(big(algo.xorout))
end

-- Returns the checksum of `data`, or nil and the reason why it cannot be computed.
local function checksum_of(name, data)
  local algo = MODEL.checksums[name]
  if algo.algo == "custom" then
    local hooks = rawget(_G, "wireforge_checksums")
    local hook = type(hooks) == "table" and hooks[algo.impl] or nil
    if hook == nil then return nil, "no wireforge_checksums entry for " .. algo.impl end
    local ok, result = pcall(hook, data)
    if not ok then return nil, "wireforge_checksums entry for " .. algo.impl .. " failed: " .. tostring(result) end
    return result
  end
  if algo.width > 32 then return crc64(algo, data) end
  return crc(algo, data)
end

-- Fields -----------------------------------------------------------------------------------------

-- A decode context covers one part of a frame (header, payload or trailer): `tvb` is the frame,
-- `limit` the end of the part (offsets are frame offsets), `scopes` the byte ranges of the frame
-- checksums cover, `little` and `check` the byte order and decode range checks of the part.

local function report(ctx, item, status, text)
  item:add_proto_expert_info(ctx.experts[status], "[" .. status .. "] " .. text)
end

local function report_rest(ctx, tree, offset, status, text)
  tree:add_tvb_expert_info(ctx.experts[status], ctx.tvb(offset, ctx.limit - offset),
    "[" .. status .. "] " .. text)
end

local function take(ctx, tree, offset, length, name)
  if ctx.limit - offset < length then
    report_rest(ctx, tree, offset, "Truncated", name .. " needs " .. length .. " byte(s)")
    return nil
  end
  return ctx.tvb(offset, length)
end

local function add(ctx, tree, abbr, range, value)
  local field = ctx.fields[abbr]
  if value ~= nil then return tree:add(field, range, value) end
  if ctx.little then return tree:add_le(field, range) end
  return tree:add(field, range)
end

local function out_of_range(ctx, item, text)
  if ctx.check then
    report(ctx, item, "OutOfRange", text)
  else
    item:add_proto_expert_info(ctx.experts.unchecked, "[OutOfRange] " .. text .. " (not checked on decode)")
  end
end

local function check_value(ctx, desc, value, item)
  local number = number_of(value)
  if desc.type == "bool" and number > 1 then
    report(ctx, item, "OutOfRange", "bool value " .. to_text(value))
  end
  if desc.min ~= nil and not (value >= desc.min) then
    out_of_range(ctx, item, to_text(value) .. " is below " .. to_text(desc.min))
  end
  if desc.max ~= nil and not (value <= desc.max) then
    out_of_range(ctx, item, to_text(value) .. " is above " .. to_text(desc.max))
  end
  if desc.enum ~= nil and MODEL.enums[desc.enum][number] == nil then
    out_of_range(ctx, item, to_text(value) .. " is not a member of " .. desc.enum)
  end
  if desc.const ~= nil and value ~= desc.const then
    report(ctx, item, "ConstMismatch", to_text(value) .. " != " .. to_text(desc.const))
  end
end

local function verify_checksum(ctx, state, desc, value, item, offset)
  local use = desc.checksum
  local first, last
  if use.scope == "payload" or use.scope == "header_and_payload" then
    local scope = ctx.scopes[use.scope]
    first, last = scope[1], scope[2]
  elseif use.scope == "preceding" then
    first, last = state.start, offset
  else
    first, last = state.ranges[use.first][1], state.ranges[use.last][2]
  end
  local data = last > first and ctx.tvb:raw(first, last - first) or ""
  local expected, reason = checksum_of(use.use, data)
  if expected == nil then
    item:add_proto_expert_info(ctx.experts.unverified, "Checksum not verified: " .. reason)
  elseif (type(expected) == "userdata" and value ~= expected)
      or (type(expected) ~= "userdata" and number_of(value) ~= expected) then
    report(ctx, item, "ChecksumMismatch", "received " .. hex(value) .. ", computed " .. hex(expected))
  else
    item:append_text(" [correct]")
  end
end

local function read_int(ctx, range, width, signed)
  if width == 8 then
    if signed then return ctx.little and range:le_int64() or range:int64() end
    return ctx.little and range:le_uint64() or range:uint64()
  end
  if signed then return ctx.little and range:le_int() or range:int() end
  return ctx.little and range:le_uint() or range:uint()
end

local decode_fields

local function decode_scalar(ctx, state, desc, tree, offset)
  local range = take(ctx, tree, offset, desc.width, desc.name)
  if range == nil then return nil end
  local value, item
  if desc.type == "f32" or desc.type == "f64" then
    value = ctx.little and range:le_float() or range:float()
    item = add(ctx, tree, desc.abbr, range)
  elseif desc.type == "char" then
    value = range:uint()
    item = add(ctx, tree, desc.abbr, range, latin1_text(string.char(value)))
  else
    value = read_int(ctx, range, desc.width, desc.signed)
    item = add(ctx, tree, desc.abbr, range)
  end
  check_value(ctx, desc, value, item)
  if desc.checksum ~= nil then verify_checksum(ctx, state, desc, value, item, offset) end
  return offset + desc.width, number_of(value), item
end

-- Opaque user type: raw hex only, no value interpretation (the C++ encode/decode is the user's).
local function decode_bytes(ctx, desc, tree, offset)
  local range = take(ctx, tree, offset, desc.width, desc.name)
  if range == nil then return nil end
  local item = tree:add(ctx.fields[desc.abbr], range)
  return offset + desc.width, nil, item
end

-- Decodes one element (scalar, struct or opaque bytes); returns the next offset, value, item.
local function decode_element(ctx, state, desc, tree, offset)
  if desc.kind == "struct" then
    local item = tree:add(ctx.fields[desc.abbr], ctx.tvb(offset, 0))
    local finish = decode_fields(ctx, desc.fields, item, offset)
    if finish ~= nil then item:set_len(finish - offset) end
    return finish, nil, item
  end
  if desc.kind == "bytes" then return decode_bytes(ctx, desc, tree, offset) end
  return decode_scalar(ctx, state, desc, tree, offset)
end

local function decode_occurs(ctx, state, desc, tree, offset)
  local occurs = desc.occurs
  local count
  if occurs.fill then
    local left = ctx.limit - offset
    if left % occurs.element ~= 0 then
      report_rest(ctx, tree, offset, "InvalidLength",
        left .. " byte(s) left for " .. desc.name .. ", not a multiple of " .. occurs.element)
      return nil
    end
    count = left / occurs.element
  elseif occurs.count ~= nil then
    local ok, result = pcall(evaluate, occurs.count, state.values)
    if not ok then
      report_rest(ctx, tree, offset, "InvalidState", "count of " .. desc.name .. ": " .. tostring(result))
      return nil
    end
    count = result
  else
    count = occurs.max
  end
  if count < occurs.min or count > occurs.max then
    report_rest(ctx, tree, offset, "CountExceedsMax", "count " .. to_text(count) .. " of " ..
      desc.name .. " outside [" .. occurs.min .. ", " .. occurs.max .. "]")
    return nil
  end
  if desc.type == "char" then
    local range = take(ctx, tree, offset, count, desc.name)
    if range == nil then return nil end
    local text = count > 0 and range:raw() or ""
    if not occurs.dynamic then text = text:gsub("\0+$", "") end
    add(ctx, tree, desc.abbr, range, latin1_text(text))
    return offset + count
  end
  local holder = tree:add(ctx.tvb(offset, 0), desc.name .. " (" .. to_text(count) ..
    (count == 1 and " element)" or " elements)"))
  local start = offset
  for index = 0, count - 1 do
    local item
    offset, _, item = decode_element(ctx, state, desc, holder, offset)
    if item ~= nil then item:prepend_text("[" .. index .. "] ") end
    if offset == nil then return nil end
  end
  holder:set_len(offset - start)
  return offset
end

-- Decodes one bit-field of a group container and returns its value as a number.
local function decode_leaf(ctx, desc, tree, range, container, wide)
  local value, item
  if wide then
    local bits = container:rshift(desc.shift)
    if desc.bits < 64 then bits = bits:band(UInt64.max():rshift(64 - desc.bits)) end
    if desc.signed then
      value = Int64.new(bits:lower(), bits:higher())
      if desc.bits < 64 then value = value:lshift(64 - desc.bits):arshift(64 - desc.bits) end
    else
      value = bits
    end
    local shown = value
    if desc.type == "bool" then shown = number_of(value) ~= 0 end
    item = add(ctx, tree, desc.abbr, range, shown)
  else
    value = math.floor(container / 2 ^ desc.shift) % 2 ^ desc.bits
    if desc.signed and value >= 2 ^ (desc.bits - 1) then value = value - 2 ^ desc.bits end
    item = add(ctx, tree, desc.abbr, range)
  end
  check_value(ctx, desc, value, item)
  return number_of(value)
end

local function decode_group(ctx, state, desc, tree, offset)
  local range = take(ctx, tree, offset, desc.width, desc.parts[1].name)
  if range == nil then return nil end
  local container
  if desc.wide then
    container = ctx.little and range:le_uint64() or range:uint64()
  else
    container = ctx.little and range:le_uint() or range:uint()
  end
  local span = { offset, offset + desc.width }
  for _, part in ipairs(desc.parts) do
    if part.leaves ~= nil then
      local sub = tree:add(ctx.fields[part.abbr], range)
      for _, leaf in ipairs(part.leaves) do decode_leaf(ctx, leaf, sub, range, container, desc.wide) end
    else
      state.values[part.name] = decode_leaf(ctx, part, tree, range, container, desc.wide)
    end
    state.ranges[part.name] = span
  end
  return offset + desc.width
end

-- Decodes the fields of a struct starting at `offset`; returns the next offset (nil when the
-- rest cannot be decoded) and the decoded values of plain fields by name.
decode_fields = function(ctx, fields, tree, offset)
  local state = { start = offset, values = {}, ranges = {} }
  for _, desc in ipairs(fields) do
    local start, value = offset, nil
    if desc.kind == "group" then
      offset = decode_group(ctx, state, desc, tree, offset)
    elseif desc.occurs ~= nil then
      offset = decode_occurs(ctx, state, desc, tree, offset)
    else
      offset, value = decode_element(ctx, state, desc, tree, offset)
      state.values[desc.name] = value
    end
    if offset == nil then return nil, state.values end
    if desc.kind ~= "group" then state.ranges[desc.name] = { start, offset } end
  end
  return offset, state.values
end

-- Frames -----------------------------------------------------------------------------------------

local function length_value(layout, payload_size)
  if layout.length_counts == "payload" then return payload_size end
  if layout.length_counts == "payload_and_trailer" then return payload_size + layout.trailer_size end
  return layout.header_size + payload_size + layout.trailer_size
end

local function frame_error(state, item, status, text)
  item:add_proto_expert_info(state.experts[status], "[" .. status .. "] " .. text)
end

-- Dissects one complete frame; returns the message name (nil when unknown).
local function dissect_frame(state, layout, tvb, tree)
  local length = tvb:len()
  local root = tree:add(state.proto, tvb())
  local header_size, trailer_size = layout.header_size, layout.trailer_size
  if length < header_size + trailer_size then
    frame_error(state, root, "Truncated", "frame of " .. length ..
      " byte(s), header and trailer need " .. (header_size + trailer_size))
    return nil
  end
  local payload_size = length - header_size - trailer_size
  local payload_end = header_size + payload_size
  local scopes = { payload = { header_size, payload_end }, header_and_payload = { 0, payload_end } }
  local function context(limit, little, check)
    return {
      tvb = tvb, limit = limit, little = little, check = check, fields = state.fields,
      experts = state.experts, scopes = scopes,
    }
  end
  if layout.max_frame_size ~= nil and length > layout.max_frame_size then
    frame_error(state, root, "InvalidLength",
      length .. " > max_frame_size " .. layout.max_frame_size)
  end
  if layout.frame_size ~= nil and length ~= layout.frame_size then
    frame_error(state, root, "InvalidLength",
      "frame has " .. length .. " bytes, fixed framing needs " .. layout.frame_size)
  end
  local values = {}
  if header_size > 0 then
    local ctx = context(header_size, layout.little, layout.check)
    local item = root:add(state.fields[state.name .. ".header"], tvb(0, header_size))
    local _
    _, values = decode_fields(ctx, layout.header, item, 0)
    local length_field = layout.length_field
    if length_field ~= nil and values[length_field.name] ~= nil then
      local expected = length_value(layout, payload_size)
      if values[length_field.name] ~= expected then
        report(ctx, item, "InvalidLength", "length field " .. to_text(values[length_field.name]) ..
          " does not match the frame (" .. expected .. ")")
      end
    end
  end
  local message
  if layout.id_field ~= nil then
    local identifier = values[layout.id_field]
    message = identifier ~= nil and layout.by_id[identifier] or nil
    if message == nil then
      frame_error(state, root, "UnknownMessageId", "id " .. to_text(identifier or "?"))
    end
  else
    message = layout.messages[1]
  end
  if message ~= nil and layout.trim and payload_size < message.size then
    -- The trailing zero bytes were not sent: dissect the payload extended with zeros.
    local bytes = tvb(header_size, payload_size):bytes()
    bytes:set_size(message.size)
    local padded = bytes:tvb("Zero-extended payload")
    local ctx = context(message.size, message.little, message.check)
    ctx.tvb = padded
    local item = root:add(state.fields[message.abbr], tvb(header_size, payload_size))
    item:append_text(" (" .. (message.size - payload_size) .. " trailing zero byte(s) not sent)")
    decode_fields(ctx, message.fields, item, 0)
    root:append_text(", " .. message.name)
  elseif message ~= nil then
    local ctx = context(payload_end, message.little, message.check)
    local item = root:add(state.fields[message.abbr], tvb(header_size, payload_size))
    local finish = decode_fields(ctx, message.fields, item, header_size)
    if finish ~= nil and finish < payload_end and not layout.allow_trailing then
      report_rest(ctx, item, finish, "TrailingBytes", (payload_end - finish) .. " byte(s) left")
    end
    root:append_text(", " .. message.name)
  elseif payload_size > 0 then
    root:add(state.fields[state.name .. ".payload"], tvb(header_size, payload_size))
  end
  if trailer_size > 0 then
    local ctx = context(length, layout.little, layout.check)
    local item = root:add(state.fields[state.name .. ".trailer"], tvb(payload_end, trailer_size))
    decode_fields(ctx, layout.trailer, item, payload_end)
  end
  return message and message.name or nil
end

-- Returns whether the sync bytes are at `offset` (nil when the buffer ends before them).
local function sync_at(layout, tvb, offset)
  for _, sync in ipairs(layout.sync) do
    local start = offset + sync.offset
    if tvb:len() < start + #sync.bytes then return nil end
    if tvb:raw(start, #sync.bytes) ~= sync.bytes then return false end
  end
  return true
end

-- Returns the size of the frame at `offset`, "more" when bytes are missing to tell, or nil, a
-- status and a reason when no frame starts there.
local function frame_size_at(layout, tvb, offset)
  local available = tvb:len() - offset
  if layout.framing == "none" then return available end
  if layout.sync ~= nil then
    local found = sync_at(layout, tvb, offset)
    if found == nil then return "more" end
    if not found then return nil, "ConstMismatch", "sync bytes not found" end
  end
  if layout.framing == "fixed" then return layout.frame_size end
  local field = layout.length_field
  if available < field.offset + field.width then return "more" end
  local range = tvb(offset + field.offset, field.width)
  local value = number_of(read_int({ little = layout.little }, range, field.width, false))
  local size
  if layout.length_counts == "payload" then
    size = layout.header_size + value + layout.trailer_size
  elseif layout.length_counts == "payload_and_trailer" then
    size = layout.header_size + value
  else
    size = value
  end
  if size < layout.header_size + layout.trailer_size or size == 0 then
    return nil, "InvalidLength", "length field gives a " .. to_text(size) .. " byte frame"
  end
  if layout.max_frame_size ~= nil and size > layout.max_frame_size then
    return nil, "InvalidLength", "length field gives " .. to_text(size) .. " > max_frame_size " ..
      layout.max_frame_size
  end
  return size
end

local function dissect(state, tvb, pinfo, tree)
  local layout = state.layouts[state.selected()]
  pinfo.cols.protocol:set(state.label)
  local offset, total, names = 0, tvb:len(), {}
  local skipped_from
  local function flush_skipped(upto)
    if skipped_from ~= nil and upto > skipped_from then
      local range = tvb(skipped_from, upto - skipped_from)
      local root = tree:add(state.proto, range)
      root:append_text(", skipped bytes")
      local item = root:add(state.fields[state.name .. ".skipped"], range)
      item:add_proto_expert_info(state.experts.resync,
        "[Resync] " .. (upto - skipped_from) .. " byte(s) before the next frame skipped")
    end
    skipped_from = nil
  end
  while offset < total do
    local size, status, reason = frame_size_at(layout, tvb, offset)
    if size == "more" or (size ~= nil and offset + size > total) then
      flush_skipped(offset)
      if pinfo.can_desegment > 0 then
        pinfo.desegment_offset = offset
        pinfo.desegment_len = size == "more" and DESEGMENT_ONE_MORE_SEGMENT or offset + size - total
        return
      end
      local item = tree:add(state.proto, tvb(offset, total - offset))
      item:add_proto_expert_info(state.experts.Truncated,
        "[Truncated] " .. (total - offset) .. " byte(s) do not hold a complete frame")
      break
    elseif size == nil and layout.resync then
      skipped_from = skipped_from or offset
      offset = offset + 1
      while layout.sync ~= nil and offset < total and sync_at(layout, tvb, offset) == false do
        offset = offset + 1
      end
    elseif size == nil then
      flush_skipped(offset)
      local item = tree:add(state.proto, tvb(offset, total - offset))
      item:add_proto_expert_info(state.experts[status], "[" .. status .. "] " .. reason)
      break
    else
      flush_skipped(offset)
      names[#names + 1] = dissect_frame(state, layout, tvb(offset, size):tvb(), tree) or "?"
      offset = offset + size
    end
  end
  flush_skipped(offset)
  if #names > 0 then pinfo.cols.info:set(state.label .. ": " .. table.concat(names, ", ")) end
  return total
end

-- Registration -----------------------------------------------------------------------------------

local INTEGER_FIELDS = {
  uint8 = ProtoField.uint8, uint16 = ProtoField.uint16, uint24 = ProtoField.uint24,
  uint32 = ProtoField.uint32, uint64 = ProtoField.uint64, int8 = ProtoField.int8,
  int16 = ProtoField.int16, int24 = ProtoField.int24, int32 = ProtoField.int32,
  int64 = ProtoField.int64,
}

local function proto_field(spec)
  local abbr, label, description = spec.abbr, spec.label, spec.description
  if spec.type == "none" then return ProtoField.none(abbr, label, description) end
  if spec.type == "bytes" then return ProtoField.bytes(abbr, label, base.NONE, description) end
  if spec.type == "string" then return ProtoField.string(abbr, label, base.ASCII, description) end
  if spec.type == "float" then return ProtoField.float(abbr, label, description) end
  if spec.type == "double" then return ProtoField.double(abbr, label, description) end
  if spec.type == "bool" then
    local display = spec.base ~= nil and tonumber(spec.base) or base.NONE
    return ProtoField.bool(abbr, label, display, nil, spec.mask, description)
  end
  local values = spec.enum ~= nil and MODEL.enums[spec.enum] or nil
  return INTEGER_FIELDS[spec.type](abbr, label, base[spec.base or "DEC"], values, spec.mask, description)
end

-- Turns the 64-bit bounds of a field description (and its children) into Int64/UInt64 values.
local function prepare(desc)
  for _, key in ipairs({ "min", "max", "const" }) do
    if type(desc[key]) == "table" then desc[key] = big(desc[key]) end
  end
  for _, list in ipairs({ desc.fields or {}, desc.parts or {}, desc.leaves or {} }) do
    for _, child in ipairs(list) do prepare(child) end
  end
end

local USER_DLTS = { { 1, "Off", -1 } }
for index = 0, 15 do USER_DLTS[#USER_DLTS + 1] = { index + 2, "USER" .. index, index } end

local EXTRA_EXPERTS = {
  { "resync", "Bytes skipped to find the next frame", expert.group.MALFORMED, expert.severity.WARN },
  { "unchecked", "Value outside its range (not checked on decode)", expert.group.PROTOCOL,
    expert.severity.NOTE },
  { "unverified", "Checksum not verified", expert.group.CHECKSUM, expert.severity.NOTE },
}

local function register(spec)
  local proto = Proto(spec.name, spec.description)
  local state = {
    proto = proto, name = spec.name, label = spec.interface, layouts = spec.layouts,
    fields = {}, experts = {},
  }
  local fields = {}
  for _, field_spec in ipairs(spec.fields) do
    local field = proto_field(field_spec)
    state.fields[field_spec.abbr] = field
    fields[#fields + 1] = field
  end
  proto.fields = fields
  local experts = {}
  for _, status in ipairs(STATUSES) do
    local item = ProtoExpert.new(spec.name .. ".error." .. status, status,
      GROUPS[status] or expert.group.PROTOCOL, expert.severity.ERROR)
    state.experts[status] = item
    experts[#experts + 1] = item
  end
  for _, info in ipairs(EXTRA_EXPERTS) do
    local item = ProtoExpert.new(spec.name .. "." .. info[1], info[2], info[3], info[4])
    state.experts[info[1]] = item
    experts[#experts + 1] = item
  end
  proto.experts = experts
  for _, layout in ipairs(spec.layouts) do
    layout.by_id = {}
    for _, message in ipairs(layout.messages) do
      if message.id ~= nil then layout.by_id[message.id] = message end
      prepare(message)
    end
    for _, part in ipairs({ layout.header, layout.trailer }) do
      for _, desc in ipairs(part) do prepare(desc) end
    end
  end

  local profiles = {}
  for index, name in ipairs(spec.profiles) do profiles[#profiles + 1] = { index, name, index } end
  if #profiles > 1 then
    proto.prefs.profile = Pref.enum("Profile", 1, "Profile (defines) of the captured traffic",
      profiles, false)
  end
  proto.prefs.udp_port = Pref.uint("UDP port", 0, "Dissect this UDP port (0: off)")
  proto.prefs.tcp_port = Pref.uint("TCP port", 0, "Dissect this TCP port (0: off)")
  proto.prefs.user_dlt = Pref.enum("User DLT", -1, "Dissect captures with this link-layer type",
    USER_DLTS, false)
  state.selected = function()
    if #profiles > 1 then return proto.prefs.profile end
    return 1
  end

  local bound = { udp = 0, tcp = 0, dlt = -1 }
  proto.prefs_changed = function()
    local udp, tcp = DissectorTable.get("udp.port"), DissectorTable.get("tcp.port")
    local encap = DissectorTable.get("wtap_encap")
    if bound.udp ~= 0 then udp:remove(bound.udp, proto) end
    if bound.tcp ~= 0 then tcp:remove(bound.tcp, proto) end
    if bound.dlt >= 0 then encap:remove(wtap_encaps.USER0 + bound.dlt, proto) end
    bound.udp, bound.tcp, bound.dlt = proto.prefs.udp_port, proto.prefs.tcp_port, proto.prefs.user_dlt
    if bound.udp ~= 0 then udp:add(bound.udp, proto) end
    if bound.tcp ~= 0 then tcp:add(bound.tcp, proto) end
    if bound.dlt >= 0 then encap:add(wtap_encaps.USER0 + bound.dlt, proto) end
  end
  DissectorTable.get("udp.port"):add_for_decode_as(proto)
  DissectorTable.get("tcp.port"):add_for_decode_as(proto)
  proto.dissector = function(tvb, pinfo, tree) return dissect(state, tvb, pinfo, tree) end
end

for _, spec in ipairs(MODEL.protocols) do register(spec) end
