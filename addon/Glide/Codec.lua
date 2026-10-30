-- GlideLink codec: the strip's frame as 6-bit symbols, each drawn as two on/off cells (the binary strip).
-- Frame bytes: [version][seq][len hi][len lo][payload ...][fletcher16 hi][fletcher16 lo], zero-padded to a multiple
-- of 3; every 3 bytes become four 6-bit symbols, high bits first. Strip.lua draws each symbol as two cells of three
-- bits (one per colour channel, fully off or fully on), after the magenta and yellow marker cells.
-- The whole wire format (core keys, pages, ring events) is written down in docs/STRIP.md.
-- Arithmetic only (no bit library needed). Keep in sync with companion/protocol.py.
local _, ns = ...

ns.PROTO = 1

local floor = math.floor

local function fletcher16(bytes, n)
    local a, b = 0, 0
    for i = 1, n do
        a = (a + bytes[i]) % 255
        b = (b + a) % 255
    end
    return b * 256 + a
end

-- payload string -> array of 6-bit symbols
function ns.Encode(seq, payload, capacityBytes)
    local maxPayload = capacityBytes - 6
    if #payload > maxPayload then payload = payload:sub(1, maxPayload) end
    local bytes = { ns.PROTO, seq % 256, floor(#payload / 256), #payload % 256 }
    for i = 1, #payload do bytes[#bytes + 1] = payload:byte(i) end
    local sum = fletcher16(bytes, #bytes)
    bytes[#bytes + 1] = floor(sum / 256)
    bytes[#bytes + 1] = sum % 256
    while #bytes % 3 ~= 0 do bytes[#bytes + 1] = 0 end
    local syms = {}
    for i = 1, #bytes, 3 do
        local v = bytes[i] * 65536 + bytes[i + 1] * 256 + bytes[i + 2]
        syms[#syms + 1] = floor(v / 262144) % 64
        syms[#syms + 1] = floor(v / 4096) % 64
        syms[#syms + 1] = floor(v / 64) % 64
        syms[#syms + 1] = v % 64
    end
    return syms, #payload
end

-- 3 bits -> r, g, b each fully off or on (the binary strip: two of these per symbol, high bits first). Off/on
-- survives Windows Auto HDR, which boosts and clips the in-between levels.
function ns.BitsColor(v)
    return floor(v / 4) % 2, floor(v / 2) % 2, v % 2
end
