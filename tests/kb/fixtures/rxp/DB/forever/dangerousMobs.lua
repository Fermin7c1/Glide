local _, addon = ...

if addon.game ~= "FOREVER" then return end
-- synthetic fixture data (tests/kb/fixture.py)
local dangerousMobs = {
  ["Alliance"] = {
    ["Dun Morogh"] = {
      ["Fixture Wolf"] = {
        {
        MinLevel = 7,
        MaxLevel = 8,
        Classification = "Normal",
        Movement = "Fast Patrol",
        Notes = "Calls the pack",
        Location = ".pin Dun Morogh,28.0,40.0;.mob Fixture Wolf"
        },
      },
    },
  },
}
addon.dangerousMobs = dangerousMobs
