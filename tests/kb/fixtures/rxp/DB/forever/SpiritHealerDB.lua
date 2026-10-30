local addonName, addon = ...

if addon.game ~= "FOREVER" then return end

-- synthetic fixture data (tests/kb/fixture.py)
addon.SpiritHealerWorld = {
  [0] = {
    { id = 9701, wx = -5600.000, wy = -440.000, name = "Dun Morogh, Fixture Hub" },
    { id = 9702, wx = -5320.000, wy = -2950.000, name = "Loch Modan, Fixture Town" },
  },
}
