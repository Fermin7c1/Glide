local _,addon = ...

if addon.game ~= "FOREVER" then return end

-- synthetic fixture data (tests/kb/fixture.py)
addon.mapId = {
    ["Dun Morogh"] = 1426,
    ["Loch Modan"] = 1432,
}

addon.taxiPos = {
  [0] = {
    [9601] = {wx = -5584.417, wy = -463.417, flag = 1},
    [9602] = {wx = -5315.0, wy = -2958.0, flag = 1},
  },
}
