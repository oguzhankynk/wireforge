-- The MAVLink 2 checksum (mav2sum::Crc) for the Wireshark dissector that `wireforge wireshark`
-- writes for this model: load this file as another Lua plugin, next to the dissector, and the
-- dissector verifies the checksum of every frame. Plain arithmetic only, so it runs with the Lua
-- of every Wireshark version (5.2 to 5.4).
local CRC_EXTRA = { [0] = 50, [1] = 124, [22] = 220, [24] = 24, [31] = 246, [73] = 38, [76] = 152, [77] = 143, [131] = 223, [253] = 83 }
local MESSAGE_ID_AT = 8 -- the three-byte message id, little endian, from the eighth byte (1-based)

local function bxor(a, b)
  local result, bit = 0, 1
  while a > 0 or b > 0 do
    if a % 2 ~= b % 2 then result = result + bit end
    a, b, bit = math.floor(a / 2), math.floor(b / 2), bit * 2
  end
  return result
end

-- Adds one byte to a CRC-16/MCRF4XX (the MAVLink "X.25" accumulate).
local function accumulate(crc, byte)
  local tmp = bxor(byte, crc % 256)
  tmp = bxor(tmp, (tmp * 16) % 256)
  local value = bxor(math.floor(crc / 256), (tmp * 256) % 65536)
  value = bxor(value, (tmp * 8) % 65536)
  return bxor(value, math.floor(tmp / 16))
end

_G.wireforge_checksums = _G.wireforge_checksums or {}
-- `data` is the frame from its start byte to the end of the payload.
_G.wireforge_checksums["mav2sum::Crc"] = function(data)
  local crc = 0xFFFF
  for index = 2, #data do crc = accumulate(crc, data:byte(index)) end
  local id = 0
  if #data >= MESSAGE_ID_AT + 2 then
    id = data:byte(MESSAGE_ID_AT) + data:byte(MESSAGE_ID_AT + 1) * 256 +
      data:byte(MESSAGE_ID_AT + 2) * 65536
  end
  return accumulate(crc, CRC_EXTRA[id] or 0)
end
