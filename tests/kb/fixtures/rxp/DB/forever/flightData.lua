local _,addon = ...

if addon.game ~= "FOREVER" then return end

-- synthetic fixture data (tests/kb/fixture.py): two flight masters, both ways
addon.FPDB = {
    ["Alliance"] = {
        [9601] = {
            [9602] = 95,
            ["name"] = "Fixture Hub, Dun Morogh",
        },
        [9602] = {
            [9601] = 97,
            ["name"] = "Fixture Town, Loch Modan",
        },
    },
}
