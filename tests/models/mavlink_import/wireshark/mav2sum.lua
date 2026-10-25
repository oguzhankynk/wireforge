-- The MAVLink 2 checksum (mav2sum::Crc) for the Wireshark dissector that `wireforge wireshark`
-- writes for this model: load this file as another Lua plugin, next to the dissector, and the
-- dissector verifies the checksum of every frame. Plain arithmetic only, so it runs with the Lua
-- of every Wireshark version (5.2 to 5.4).
--
-- The CRC_EXTRA bytes come from the model's list, app/include/mav2sum/crc_extra.h, found from this
-- file's place in the project (or a crc_extra.h copied next to it); the table below is used only
-- when neither is there.
local BUILT_IN = { [0] = 50, [1] = 124, [22] = 220, [24] = 24, [31] = 246, [73] = 38, [76] = 152, [77] = 143, [131] = 223, [253] = 83 }
local function read_list(path)
  local file = io.open(path, "r")
  if file == nil then return nil end
  local list = {}
  for id, extra in file:read("*a"):gmatch("X%((%d+)U,%s*(%d+)U%)") do
    list[tonumber(id)] = tonumber(extra)
  end
  file:close()
  return next(list) ~= nil and list or nil
end

local source = debug.getinfo(1, "S").source
local here = source:sub(1, 1) == "@" and (source:sub(2):match("^(.*)[/\\]") or ".") or "."
local CRC_EXTRA = read_list(here .. "/crc_extra.h")
  or read_list(here .. "/../app/include/mav2sum/crc_extra.h")
  or BUILT_IN

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
